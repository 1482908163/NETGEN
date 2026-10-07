"""Independently check the uploaded ordered-combine batch, including failures."""
import argparse
import csv
import json
import math
import re
import statistics as st
from collections import Counter
from pathlib import Path

ROUTES = ("batch_parallel", "combine_profile", "combine_waves")
SEEDS = (-1, 17, 41)
MODES = ("natural", "split")
SOURCE = "22b98e2f0d4975aed1cb3b598fef3abe86905c09"
PAIRS = ((ROUTES[0], ROUTES[1]), (ROUTES[1], ROUTES[2]), (ROUTES[0], ROUTES[2]))
FIELDS = ("calls", "scanned_edges", "candidates", "attempts", "applied", "waves",
          "parallel_waves", "parallel_attempts", "planning_seconds", "evaluation_seconds",
          "commit_seconds", "seconds", "verified_waves", "verified_attempts",
          "mismatches", "max_wave_size")

def rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f, delimiter="\t" if path.suffix == ".tsv" else ","))

def key(row):
    return int(row["partition_seed"]), row["timing"], int(row["repeat"])

def num(row, name):
    v = float(row[name])
    assert math.isfinite(v) and v >= 0, (name, v)
    return v

def signature(value, p):
    v = json.loads(value)
    assert len(v) == p and [x[0] for x in v] == list(range(p))
    return v

def canonical_quality(q):
    # Scheduler is deliberately different; all audit/identity values must match.
    return {k: v for k, v in q.items() if k != "scheduler"}

def combine_counts(row, wave, verify=False, per_rank=False):
    m = {k: num(row, "combine_commit_" + k) for k in FIELDS}
    assert all(v == int(v) for k, v in m.items() if "seconds" not in k)
    assert m["applied"] <= m["attempts"] == m["candidates"] <= m["scanned_edges"]
    assert m["mismatches"] == 0
    assert m["verified_waves"] == (m["waves"] if verify else 0)
    assert m["verified_attempts"] == (m["attempts"] if verify else 0)
    if wave:
        assert m["parallel_waves"] <= m["waves"] <= m["attempts"]
        assert 2*m["parallel_waves"] <= m["parallel_attempts"] <= 4*m["parallel_waves"]
        assert m["attempts"] == m["parallel_attempts"] + m["waves"] - m["parallel_waves"]
        assert m["max_wave_size"] <= 4
        assert (m["parallel_waves"] > 0) == (m["max_wave_size"] > 1)
    else:
        assert not any(m[k] for k in ("waves", "parallel_waves", "parallel_attempts",
                                     "planning_seconds", "max_wave_size"))
    assert m["planning_seconds"] <= m["commit_seconds"] + 1e-6
    # Inclusive timers can only be added on the same rank, never their maxima.
    if per_rank:
        assert m["evaluation_seconds"] + m["commit_seconds"] <= m["seconds"] + 1e-6
        assert m["seconds"] <= 1.05*num(row, "kernel_optimization_seconds") + 1e-3
    return m

def analyze(root):
    runmaps, critical, quality = {}, {}, {}
    identities, failures, warmups, result_pairs, fixed = set(), [], [], [], []
    status_count = good_formal = good_warmup = coverage_pairs = 0
    for p in (128, 256, 512):
        for route in ROUTES:
            d = root / f"route_{route}" / f"p{p}"
            plan = json.loads((d/"plan.json").read_text())
            assert plan == dict(ranks=p, repeats=8, algorithms=["sparse"],
                                timings=list(MODES), partition_seeds=list(SEEDS), quality_warmup=True)
            config = (d/"configuration.txt").read_text()
            assert f"SOURCE_REVISION={SOURCE}" in config
            assert "--kernel-threads\n4\n" in config
            assert all(f"{k}=4" in config for k in ("RANKS_PER_NODE", "CPUS_PER_TASK"))
            kernel_sha = re.search(r"^MESH_KERNEL_SHA256=([0-9a-f]{64})$", config, re.M).group(1)
            binary_sha = re.search(r"^([0-9a-f]{64})  .*/mesh_occ_mpi$", config, re.M).group(1)
            input_sha = re.search(r"^([0-9a-f]{64})  .*/wholewall3solid\.STEP$", config, re.M).group(1)
            identities.add((SOURCE, input_sha, binary_sha, kernel_sha))
            status = rows(d/"run_status.tsv")
            expected = {(seed, mode, i) for seed in SEEDS for mode in MODES for i in range(9)}
            assert len(status) == 54 and {key(x) for x in status} == expected
            status_count += len(status)
            good = {key(x) for x in status if x["exit_code"] == "0"}
            good_formal += sum(k[2] > 0 for k in good)
            good_warmup += sum(k[2] == 0 for k in good)
            data = rows(d/"analysis/runs.csv")
            runmaps[p, route] = {key(x): x for x in data}
            assert len(data) == len(runmaps[p, route])
            assert set(runmaps[p, route]) == {k for k in good if k[2] > 0}
            for x in status:
                if x["exit_code"] == "0":
                    continue
                stem = "sparse" + (f"_seed{x['partition_seed']}" if x["partition_seed"] != "-1" else "") + "_" + x["timing"]
                file = d/stem/f"repeat_{x['repeat']}"/"failure_reason.txt"
                log = file.read_text()
                first = re.findall(r"yhrun: error: (\S+): task (\d+): (Segmentation fault|Aborted)", log)[0]
                failures.append(dict(p=p, route=route, seed=int(x["partition_seed"]),
                                     mode=x["timing"], repeat=int(x["repeat"]), exit_code=int(x["exit_code"]),
                                     node=first[0], rank=int(first[1]), signal=first[2],
                                     file=str(file.relative_to(root))))
            cr = rows(d/"analysis/critical_rank_breakdown.csv")
            critical[p, route] = {(key(x), int(x["rank"])): x for x in cr}
            assert len(cr) == len(critical[p, route])
            for x in data:
                assert x["kernel_scheduler"] == route
                assert x["ghost_exchange_policy"] == "legacy_v1"
                assert x["global_numbering"] == "owner_local_v2"
                assert x["node_cpu_bind"] == "cores" and x["node_affinity_layout"] == "disjoint"
                if route != ROUTES[0]:
                    assert x["combine_commit_verification"] == "off"
                    combine_counts(x, route == "combine_waves")
                    sig = signature(x["combine_commit_coverage_signature"], p)
                    for i, metric in enumerate(FIELDS[:5], 1):
                        assert sum(v[i] for v in sig) == num(x, "combine_commit_"+metric)
            if route != ROUTES[0]:
                for x in cr:
                    combine_counts(x, route == "combine_waves", per_rank=True)
            for seed in SEEDS:
                stem = "sparse_natural" if seed == -1 else f"sparse_seed{seed}_natural"
                qfile = d/stem/"repeat_0/quality_summary.json"
                assert qfile.exists() == ((seed, "natural", 0) in good)
                if not qfile.exists():
                    continue
                q = json.loads(qfile.read_text())
                assert q["structural_pass"] and not q["issues"] and len(q["per_rank"]) == p
                ident = q["identity"]
                assert ident["MESH_SOURCE_REVISION"] == SOURCE
                assert all(ident[k] == "4" for k in ("RANKS_PER_NODE", "CPUS_PER_TASK", "kernel_threads"))
                identities.add(tuple(ident[k] for k in ("MESH_SOURCE_REVISION", "MESH_INPUT_SHA256",
                                                        "MESH_BINARY_SHA256", "MESH_KERNEL_SHA256")))
                assert q["counts"]["hard_errors"] == 0 and q["counts"]["checked"] == q["counts"]["elements"]
                assert sum(q["shape_hist"]) == q["counts"]["elements"]
                assert sum(x["elements"] for x in q["per_rank"]) == q["counts"]["elements"]
                assert all(x["hard_errors"] == 0 and sum(x["shape_hist"]) == x["elements"] for x in q["per_rank"])
                quality[p, route, seed] = q
                if route == ROUTES[0]:
                    continue
                v = json.loads((d/stem/"repeat_0/combine_verification_summary.json").read_text())
                m = combine_counts(v, route == "combine_waves", route == "combine_waves")
                sig = signature(v["combine_commit_coverage_signature"], p)
                replay = signature(v["combine_commit_verification_signature"], p)
                for a, b in zip(sig, replay):
                    assert a[4] == b[2]
                    assert b[3] == (b[1] if route == "combine_waves" else 0)
                    assert b[4] == (b[2] if route == "combine_waves" else 0) and b[5] == 0
                assert sum(x[1] for x in replay) == m["waves"]
                assert sum(x[2] for x in replay) == m["attempts"]
                warmups.append(dict(p=p, route=route, seed=seed, **m))
        # Every available pair of quality reports agrees except its scheduler name.
        for seed in SEEDS:
            available = [quality[p, route, seed] for route in ROUTES if (p, route, seed) in quality]
            assert all(canonical_quality(q) == canonical_quality(available[0]) for q in available)
        for x in rows(root/f"p{p}/partition_diversity.csv"):
            expected_reports = 2 if p == 256 else 3
            assert int(x["distinct_surface_assignments"]) == expected_reports
        uploaded = {(x["control"], x["candidate"], int(x["seed"]), x["timing"]): x
                    for x in rows(root/f"p{p}/route_comparisons.csv")}
        for control, candidate in PAIRS:
            for seed in SEEDS:
                for mode in MODES:
                    a, b = runmaps[p, control], runmaps[p, candidate]
                    indices = [i for i in range(1, 9) if (seed, mode, i) in a and (seed, mode, i) in b]
                    pairs = [(a[seed, mode, i], b[seed, mode, i]) for i in indices]
                    assert pairs
                    if control == "combine_profile":
                        for x, y in pairs:
                            assert x["combine_commit_coverage_signature"] == y["combine_commit_coverage_signature"]
                            coverage_pairs += 1
                    qa, qb = quality.get((p, control, seed)), quality.get((p, candidate, seed))
                    valid = len(pairs) == 8 and qa is not None and qb is not None
                    pct = [100*(1-num(y, "core_seconds")/num(x, "core_seconds")) for x, y in pairs]
                    u = uploaded[control, candidate, seed, mode]
                    assert (u["validated"] == "True") == valid and int(u["paired_count"]) == len(pairs)
                    assert math.isclose(float(u["paired_reduction_pct"]), st.median(pct), abs_tol=1e-9)
                    assert int(u["paired_wins"]) == sum(x > 0 for x in pct)
                    result_pairs.append(dict(p=p, seed=seed, mode=mode, control=control, candidate=candidate,
                                             valid=valid, repeats=indices, pct=st.median(pct), wins=sum(x > 0 for x in pct),
                                             minimum=min(pct), maximum=max(pct),
                                             control_s=st.median(num(x, "core_seconds") for x, y in pairs),
                                             candidate_s=st.median(num(y, "core_seconds") for x, y in pairs)))
        for seed in SEEDS:
            # Use all eight repeats of a complete natural comparison on one fixed rank.
            if not any(x["p"] == p and x["seed"] == seed and x["mode"] == "natural"
                       and x["control"] == ROUTES[0] and x["candidate"] == ROUTES[2] and x["valid"] for x in result_pairs):
                continue
            count = Counter(int(runmaps[p, ROUTES[0]][seed, "natural", i]["slowest_compute_rank"]) for i in range(1, 9))
            candidates = [rank for rank in range(p) if all(((seed, "natural", i), rank) in critical[p, route]
                          for route in ROUTES for i in range(1, 9))]
            assert candidates
            rank = min(candidates, key=lambda n: (-count[n], n))
            values = {route: [critical[p, route][(seed, "natural", i), rank] for i in range(1, 9)] for route in ROUTES}
            metric_names = ("compute_seconds", "local_volume_mesh_seconds", "kernel_generation_seconds",
                            "kernel_repair_seconds", "kernel_optimization_seconds")
            medians = {rt: {k: st.median(num(x, k) for x in xs) for k in metric_names} for rt, xs in values.items()}
            for rt in ROUTES[1:]:
                medians[rt].update({k: st.median(num(x, "combine_commit_"+k) for x in values[rt]) for k in FIELDS})
                medians[rt]["commit_of_optimization_pct"] = st.median(
                    100*num(x, "combine_commit_commit_seconds")/num(x, "kernel_optimization_seconds") for x in values[rt])
            medians["combine_waves"]["planning_of_commit_pct"] = st.median(
                100*num(x, "combine_commit_planning_seconds")/num(x, "combine_commit_commit_seconds") for x in values["combine_waves"])
            medians["combine_waves"]["parallel_attempts_pct"] = st.median(
                100*num(x, "combine_commit_parallel_attempts")/num(x, "combine_commit_attempts") for x in values["combine_waves"])
            changes = [100*(num(y, "combine_commit_commit_seconds")/
                       num(x, "combine_commit_commit_seconds")-1)
                       for x,y in zip(values[ROUTES[1]],values[ROUTES[2]])]
            fixed.append(dict(p=p, seed=seed, rank=rank, baseline_compute_critical_occurrences=count[rank],
                              medians=medians,
                              commit_change_pct=st.median(changes),commit_increases=sum(x>0 for x in changes),
                              commit_change_min=min(changes),commit_change_max=max(changes)))
    assert status_count == 486 and len(failures) == 22 and good_formal == 413 and good_warmup == 51
    assert len(identities) == 1 and len(quality) == 24 and len(result_pairs) == 54
    assert sum(x["valid"] for x in result_pairs) == 34
    candidate_warmups = [x for x in warmups if x["route"] == "combine_waves"]
    return dict(batch="mesh_algorithms_20261007-195529",source=SOURCE,statuses=status_count,
                successful_formal=good_formal, successful_warmups=good_warmup, failed=len(failures),
                quality_reports=len(quality), identities=list(identities), equal_available_quality=True,
                coverage_matched_formal_pairs=coverage_pairs,
                candidate_warmups=len(candidate_warmups),
                verified_waves=sum(x["verified_waves"] for x in candidate_warmups),
                verified_attempts=sum(x["verified_attempts"] for x in candidate_warmups),
                replay_mismatches=sum(x["mismatches"] for x in candidate_warmups),
                valid_comparisons=sum(x["valid"] for x in result_pairs),
                pairs=result_pairs, fixed_ranks=fixed, warmups=warmups, failures=failures,
                workload={f"{p}/{seed}":next(q["counts"]["elements"] for (pp,rt,ss),q in quality.items()
                         if pp==p and ss==seed) for p in (128,256,512) for seed in SEEDS})

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(args.root)
    output = args.output or args.root/"independent_analysis.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k not in ("pairs","fixed_ranks","warmups","failures")}, ensure_ascii=False))
    for x in result["pairs"]:
        if x["mode"] == "natural":
            print("PAIR", json.dumps(x, ensure_ascii=False))
    for x in result["fixed_ranks"]:
        print("FIXED", json.dumps(x, ensure_ascii=False))
