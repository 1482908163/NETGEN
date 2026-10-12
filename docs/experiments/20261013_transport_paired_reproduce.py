"""Recheck retained certificates; this does not replace raw-profile validation."""
import argparse
import csv
import hashlib
import io
import json
import math
import statistics as st
from pathlib import Path

SOURCE = '634aa99068ff2b4cdca385f0d1128c91f625cad5'
BINARY = '94030d0b537745ee5b0fdcc4cb788ff3acd34f0940716ea05ef085f2bf4d91cb'
PREFIX = 'ghost_transport_'
PHASES = ('prepare', 'metadata', 'exchange', 'decode', 'release')
WORK = ('send_items', 'receive_items', 'raw_send_bytes', 'raw_receive_bytes')


def reproduce(root, manifest):
    files = {e['path']: e for e in manifest['files']}
    blobs = {}
    for path, entry in files.items():
        data = (root / path).read_bytes()
        git = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        assert git == entry['sha'] and len(data) == entry['size'], path
        blobs[path] = data
    def read(path):
        return blobs[path]
    def csvrows(path):
        return list(csv.DictReader(io.StringIO(read(path).decode())))

    runtime = json.loads(read('runtime_manifest.json'))
    assert runtime['source_revision'] == SOURCE
    for n in (128, 256, 512):
        receipt = json.loads(read(f'p{n}/mpi_startup/receipt.json'))
        assert receipt['success'] and receipt['exit_code'] == 0
        assert receipt['identity']['ranks'] == n and receipt['identity']['binary_sha256'] == BINARY
    # Each local report declares the exact SHA-256 of its source certificates.
    for n in (128, 512):
        for entry in json.loads(read(f'p{n}/ghost_transport_sources.json')):
            assert hashlib.sha256(read(entry['path'])).hexdigest() == entry['sha256']

    quality = []
    runs = {}
    for n in (128, 256, 512):
        for seed in (-1, 17, 41):
            stem = 'sparse' + ('' if seed == -1 else f'_seed{seed}') + '_natural'
            qpaths = [f'route_ghost_{route}/p{n}/{stem}/repeat_0/quality_summary.json'
                      for route in ('literal', 'dictionary')]
            assert read(qpaths[0]) == read(qpaths[1])
            q = json.loads(read(qpaths[0]))
            assert q['schema'] == 'volume_audit_v1' and q['structural_pass'] and not q['issues']
            assert q['identity']['MESH_SOURCE_REVISION'] == SOURCE
            assert q['identity']['MESH_BINARY_SHA256'] == BINARY
            assert q['counts']['checked'] == q['counts']['elements'] > 0
            assert not q['counts']['hard_errors']
            quality.append(dict(ranks=n, seed=seed, elements=q['counts']['elements'], identical=True))
            for route in ('literal', 'dictionary'):
                for repeat in range(4):
                    path = f'route_ghost_{route}/p{n}/{stem}/repeat_{repeat}/ghost_transport_certificate.csv'
                    if path not in files:
                        assert (n, seed, route, repeat) == (256, 17, 'dictionary', 1)
                        continue
                    rows = csvrows(path)
                    rows.sort(key=lambda r: int(r['rank']))
                    assert [int(r['rank']) for r in rows] == list(range(n))
                    assert len({r['core_seconds'] for r in rows}) == 1
                    mode = ('literal_transport_v1' if route == 'literal' else
                            'vertex_dictionary_reference_v1' if repeat == 0 else 'vertex_dictionary_transport_v1')
                    reference = route == 'dictionary' and repeat == 0
                    for row in rows:
                        assert (row['source_revision'], row['binary_sha256'], row['algorithm'],
                                row['timing'], row['ghost_transport']) == (SOURCE, BINARY, 'sparse', 'natural', mode)
                        assert (int(row['ranks']), int(row['partition_seed']), int(row['repeat'])) == (n, seed, repeat)
                        v = {k[len(PREFIX):]: float(x) for k, x in row.items()
                             if k.startswith(PREFIX) and k != 'ghost_transport'}
                        assert all(math.isfinite(x) and x >= 0 for x in v.values())
                        assert all(x.is_integer() and x <= 2**53 for k, x in v.items() if 'seconds' not in k)
                        assert v['calls'] == 1 and v['mismatches'] == 0
                        for direction, key in (('send', 'volume_num_s'), ('receive', 'volume_num_r')):
                            peers = float(row[key])
                            assert v['raw_' + direction + '_bytes'] == 140 * v[direction + '_items']
                            assert v['dictionary_' + direction + '_peers'] + v['literal_' + direction + '_peers'] == peers
                            assert v['payload_' + direction + '_messages'] >= peers
                            assert peers <= v[direction + '_items']
                            payload = v['payload_' + direction + '_bytes']
                            assert payload <= v['raw_' + direction + '_bytes']
                            assert (payload == 0) == (v[direction + '_items'] == 0)
                            if route == 'literal':
                                assert payload == v['raw_' + direction + '_bytes']
                                assert not v['dictionary_' + direction + '_peers']
                                assert not v['metadata_' + direction + '_bytes']
                                assert not v['metadata_' + direction + '_messages']
                                assert v['payload_' + direction + '_messages'] == peers
                            else:
                                assert v['metadata_' + direction + '_bytes'] == 24 * peers
                                assert v['metadata_' + direction + '_messages'] == peers
                        assert v['verify_calls'] == int(reference)
                        assert v['verified_items'] == (v['receive_items'] if reference else 0)
                        assert v['verified_peers'] == (float(row['volume_num_r']) if reference else 0)
                        if not reference:
                            assert v['reference_seconds'] == 0
                        if route == 'literal':
                            assert not any(v[k + '_seconds'] for k in PHASES if k != 'exchange')
                            assert not v['retained_wire_bytes_peak']
                        assert sum(v[k + '_seconds'] for k in PHASES) <= v['total_seconds'] + 1e-6
                        assert v['total_seconds'] <= v['full_seconds'] + 1e-6
                        assert v['full_seconds'] <= float(row['core_seconds']) + 1e-3
                    fields = WORK + ('payload_send_bytes', 'payload_receive_bytes', 'metadata_send_bytes',
                                     'metadata_receive_bytes', 'verified_items', 'mismatches')
                    totals = {k: sum(float(r[PREFIX + k]) for r in rows) for k in fields}
                    for prefix in ('raw', 'payload', 'metadata'):
                        assert totals[prefix + '_send_bytes'] == totals[prefix + '_receive_bytes']
                    assert totals['send_items'] == totals['receive_items']
                    runs[n, seed, repeat, route] = dict(rows=rows, totals=totals)
    assert len(runs) == 71
    assert not read('p128/route_issues.txt') and not read('p512/route_issues.txt')
    issues = read('p256/route_issues.txt').decode()
    assert 'analysis_validation_failure' in issues and 'ghost transport phase scope missing' in issues
    assert 'successful!!!' in issues
    # All available partners must conserve the original per-rank ghost work.
    compared_rank_rows = 0
    for (n, seed, repeat, route), run in runs.items():
        if route != 'literal' or (n, seed, repeat, 'dictionary') not in runs:
            continue
        partner = runs[n, seed, repeat, 'dictionary']
        for x, y in zip(run['rows'], partner['rows']):
            assert all(x[PREFIX + k] == y[PREFIX + k] for k in WORK)
            assert all(x[k] == y[k] for k in ('volume_num_s', 'volume_num_r'))
        compared_rank_rows += n

    pairs = []
    for n in (128, 512):  # Never turn the incomplete p256 matrix into a performance result.
        published = csvrows(f'p{n}/ghost_transport_pairs.csv')
        assert len(published) == 9
        for record in published:
            seed, repeat = int(record['seed']), int(record['repeat'])
            control, candidate = runs[n, seed, repeat, 'literal'], runs[n, seed, repeat, 'dictionary']
            x, y = control['rows'], candidate['rows']
            rank = max(range(n), key=lambda i: float(x[i][PREFIX + 'full_seconds']))
            a, b = x[rank], y[rank]
            assert int(record['rank']) == rank
            assert (record['source_revision'], record['binary_sha256']) == (SOURCE, BINARY)
            for side, row in (('control', a), ('candidate', b)):
                assert float(record[side + '_core_seconds']) == float(row['core_seconds'])
                assert float(record[side + '_full_seconds']) == float(row[PREFIX + 'full_seconds'])
                for field in row:
                    if field.startswith(PREFIX) and field != 'ghost_transport':
                        assert float(record[side + '_' + field]) == float(row[field])
            raw = control['totals']['payload_send_bytes']
            wire = candidate['totals']['payload_send_bytes'] + candidate['totals']['metadata_send_bytes']
            assert float(record['control_payload_bytes']) == raw
            assert float(record['candidate_payload_and_metadata_bytes']) == wire
            core = 100 * (1 - float(b['core_seconds']) / float(a['core_seconds']))
            full = 100 * (1 - float(b[PREFIX + 'full_seconds']) / float(a[PREFIX + 'full_seconds']))
            reduction = 100 * (1 - wire / raw)
            for key, value in (('core_reduction_percent', core), ('full_reduction_percent', full),
                               ('wire_reduction_percent', reduction)):
                assert math.isclose(float(record[key]), value, rel_tol=1e-12, abs_tol=1e-12)
            pairs.append(dict(ranks=n, seed=seed, repeat=repeat, control_slowest_rank=rank,
                core_reduction_percent=core, full_reduction_percent=full, wire_reduction_percent=reduction,
                control_core_seconds=float(a['core_seconds']), candidate_core_seconds=float(b['core_seconds']),
                control_full_seconds=float(a[PREFIX + 'full_seconds']), candidate_full_seconds=float(b[PREFIX + 'full_seconds']),
                **{side + '_' + phase + '_seconds': float(row[PREFIX + phase + '_seconds'])
                   for side, row in (('control', a), ('candidate', b)) for phase in PHASES + ('total',)}))

    def summary(group):
        return dict(pairs=len(group), core_wins=sum(r['core_reduction_percent'] > 0 for r in group),
            full_wins=sum(r['full_reduction_percent'] > 0 for r in group),
            **{key + '_median': st.median(r[key] for r in group)
               for key in ('core_reduction_percent', 'full_reduction_percent', 'wire_reduction_percent')},
            **{side + '_helper_share_percent_median': st.median(100 * r[side + '_total_seconds'] / r[side + '_full_seconds'] for r in group)
               for side in ('control', 'candidate')})
    summaries = [dict(ranks=n, **summary([r for r in pairs if r['ranks'] == n])) for n in (128, 512)]
    seeds = [dict(ranks=n, seed=seed, **summary([r for r in pairs if r['ranks'] == n and r['seed'] == seed]))
             for n in (128, 512) for seed in (-1, 17, 41)]
    critical = []
    for n in (128, 512):
        breakdown = csvrows(f'route_ghost_literal/p{n}/analysis/critical_rank_breakdown.csv')
        assert len(breakdown) == 45
        for seed in (-1, 17, 41):
            for repeat in (1, 2, 3):
                g = [r for r in breakdown if (int(r['partition_seed']), int(r['repeat'])) == (seed, repeat)]
                assert sorted(int(r['compute_order']) for r in g) == list(range(1, 6))
                assert len({int(r['rank']) for r in g}) == 5
                assert all((int(r['ranks']), r['algorithm'], r['timing']) == (n, 'sparse', 'natural') for r in g)
        slow = [r for r in breakdown if int(r['compute_order']) == 1]
        critical.append(dict(ranks=n, sampled_compute_slowest_runs=9,
            compute_seconds_median=st.median(float(r['compute_seconds']) for r in slow),
            local_volume_mesh_seconds_median=st.median(float(r['local_volume_mesh_seconds']) for r in slow),
            volume_refine_seconds_median=st.median(float(r['volume_refine_seconds']) for r in slow),
            local_volume_mesh_share_of_recorded_compute_percent_median=st.median(100*float(r['local_volume_mesh_seconds'])/float(r['compute_seconds']) for r in slow),
            full_exchange_slowest_rank_present_in_compute_top5=sum(any(int(r['rank']) == pair['control_slowest_rank'] and
                (int(r['partition_seed']), int(r['repeat'])) == (pair['seed'], pair['repeat']) for r in breakdown)
                for pair in pairs if pair['ranks'] == n),
            kernel_generation_seconds_median=st.median(float(r['kernel_generation_seconds']) for r in slow),
            kernel_front_seconds_median=st.median(float(r['kernel_front_seconds']) for r in slow),
            kernel_delaunay_seconds_median=st.median(float(r['kernel_delaunay_seconds']) for r in slow),
            note='Recorded compute stages only; this is not the global core critical rank or a sum of nested kernel timers.'))
    return dict(result_commit=manifest['commit'], experiment_source=SOURCE, binary_sha256=BINARY,
        certificates=71, warmup_runs=18, formal_runs_with_certificates=53,
        certificate_rank_rows=sum(len(r['rows']) for r in runs.values()), compared_work_rank_rows=compared_rank_rows,
        verified_receive_records=int(sum(r['totals']['verified_items'] for r in runs.values())), mismatches=0,
        quality=quality, excluded_scale=dict(ranks=256, missing_candidate=dict(seed=17, repeat=1),
            reason='analysis_validation_failure; original raw profile not uploaded; not retrospectively cleared'),
        summary=summaries, seeds=seeds, complete_scales_combined=summary(pairs), pairs=pairs, compute_evidence=critical,
        limitation='Retained certificates and quality only; raw profiles are not in this GitHub upload. Phase scope coverage cannot be revalidated from certificates.',
        source_files={path: dict(git_blob=files[path]['sha'], sha256=hashlib.sha256(data).hexdigest())
                      for path, data in sorted(blobs.items())})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('manifest', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    text = json.dumps(reproduce(a.root, json.loads(a.manifest.read_text())), ensure_ascii=False, indent=2) + '\n'
    if a.output:
        a.output.write_text(text)
    else:
        print(text)
