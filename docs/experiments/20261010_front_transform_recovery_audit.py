#!/usr/bin/env python3
"""复核已上传补审证据与关键进程成本；不重跑网格、不替代原始记录补审。"""
import argparse
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = 'f5b73b1c0394537bd8159048c1db7d88558bb967'
FIELDS = {'front_distance_verified', 'front_distance_reference_seconds'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_csv(path):
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream))


def check_objects(root, manifest):
    for item in manifest['files']:
        data = (root / item['path']).read_bytes()
        sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        require(len(data) == item['bytes'] and sha == item['git_blob_sha'],
                'uploaded evidence differs: ' + str(root / item['path']))


def audit(original, derived, output):
    csv.field_size_limit(32 * 1024 * 1024)
    sources = json.loads((HERE / '20261010_front_transform_recovery_source_manifest.json').read_text())
    old_sources = json.loads((HERE / '20261010_front_transform_source_manifest.json').read_text())
    check_objects(original, old_sources)
    check_objects(derived, sources)
    manifest = json.loads((derived / 'replay_recovery_manifest.json').read_bytes())
    require(manifest['schema'] == 'front_transform_replay_recovery_v1'
            and manifest['complete'] is True and manifest['source_revision'] == SOURCE,
            'unsupported or incomplete recovery')
    require(set(manifest['changed_fields']) == FIELDS, 'unexpected changed fields')
    records = manifest['runs']
    require(len(records) == 72 and len({r['directory'] for r in records}) == 72,
            'recovery must cover 72 distinct runs')
    recovered = {r['directory']: r for r in records if r['recovered']}
    ordinary = [r for r in records if not r['recovered']]
    require(len(recovered) == 9 and len(ordinary) == 63, 'unexpected recovery coverage')
    require(all(r['original_sha256'] == r['derived_sha256'] for r in ordinary),
            'ordinary profile changed in provenance record')
    pairs, warmups, comparisons, costs, fixed_reductions = [], [], [], [], []
    formal_count = tables_count = rank_changes = verified = 0
    for n in (128, 256, 512):
        require((derived / f'p{n}/route_issues.txt').read_bytes() == b'', 'route issues remain')
        group = read_csv(derived / f'p{n}/route_comparisons.csv')
        require(len(group) == 3 and {int(r['seed']) for r in group} == {-1, 17, 41},
                'comparison coverage differs')
        for row in group:
            require(row['control'] == 'split_active' and row['candidate'] == 'front_transform'
                    and row['validated'] == row['quality_pass'] == row['same_volume_fingerprint'] == 'True'
                    and int(row['paired_count']) == 3 and not row['quality_issues'],
                    'comparison lacks strict qualification')
            comparisons.append(dict(ranks=n, seed=int(row['seed']),
                                    paired_reduction_pct=float(row['paired_reduction_pct']),
                                    wins=int(row['paired_wins']), count=3, validated=True,
                                    quality_pass=True, volume_equal=True))
        for route in ('split_active', 'front_transform'):
            relative = Path(f'route_{route}/p{n}/analysis/runs.csv')
            require((original / relative).read_bytes() == (derived / relative).read_bytes(),
                    'formal run table changed: ' + str(relative))
            tables_count += 1
        for seed in (-1, 17, 41):
            stem = 'sparse_natural' if seed == -1 else f'sparse_seed{seed}_natural'
            candidate = Path(f'route_front_transform/p{n}/{stem}/repeat_0')
            control = Path(f'route_split_active/p{n}/{stem}/repeat_0')
            quality_bytes = (derived / candidate / 'quality_summary.json').read_bytes()
            require(quality_bytes == (derived / control / 'quality_summary.json').read_bytes()
                    == (original / control / 'quality_summary.json').read_bytes(),
                    'complete quality report differs: ' + str(candidate))
            quality = json.loads(quality_bytes)
            require(quality['structural_pass'] is True and quality['counts']['hard_errors'] == 0
                    and quality['ranks'] == n and quality['seed'] == seed
                    and quality['identity']['MESH_SOURCE_REVISION'] == SOURCE,
                    'quality audit identity or structure differs')
            quality_sha = hashlib.sha1(b'blob ' + str(len(quality_bytes)).encode() + b'\0' + quality_bytes).hexdigest()
            pairs.append(dict(path=str(candidate / 'quality_summary.json'), blob=quality_sha,
                              exact_bytes_equal_to_derived_and_original_control=True,
                              structural_pass=True, elements=quality['counts']['elements'], hard_errors=0))
            rows = read_csv(derived / candidate / 'algorithm_certificate.csv')
            record = recovered[str(candidate)]
            require(record['original_sha256'] != record['derived_sha256'], 'recovery profile hash unchanged')
            changes = record['rank_changes']
            require(len(rows) == len(changes) == n
                    and [int(r['rank']) for r in rows] == [r['rank'] for r in changes] == list(range(n)),
                    'warmup rank coverage differs')
            for row, change in zip(rows, changes):
                require(row['source_revision'] == SOURCE and row['repeat'] == '0'
                        and int(row['ranks']) == n
                        and row['binary_sha256'] == quality['identity']['MESH_BINARY_SHA256']
                        and row['volume_front_distance'] == 'distance_original_v1'
                        and row['volume_front_transform'] == 'transform_exact_replay_v1',
                        'warmup mode or identity differs')
                calls = int(row['front_transform_calls'])
                require(calls == int(row['front_transform_verified']) == int(row['front_distance_calls'])
                        == change['before']['front_distance_verified']
                        and int(row['front_transform_mismatches']) == 0, 'exact replay count differs')
                require(all(float(row['front_distance_' + f]) == 0 for f in
                            ('verified', 'reference_seconds', 'mismatches', 'hits', 'fallback', 'allocated_cells')),
                        'distance replay/cache activity remains')
                require(set(change['before']) == set(change['after']) == FIELDS
                        and all(v == 0 for v in change['after'].values()), 'unexpected rank field correction')
                # 旧包装器分别读时钟；沿用 f093fc2 补审协议，不能要求两次读数完全相等。
                distance_time = change['before']['front_distance_reference_seconds']
                transform_time = float(row['front_transform_reference_seconds'])
                require(all(math.isfinite(v) and v >= 0 for v in (distance_time, transform_time))
                        and distance_time <= transform_time
                        and transform_time - distance_time <= calls * .001 + 1e-6
                        and (calls != 0 or distance_time == transform_time == 0),
                        'reference time violates the recorded recovery protocol')
                verified += calls
            rank_changes += n
            warmups.append(dict(path=str(candidate / 'algorithm_certificate.csv'), ranks=n,
                                calls=sum(int(r['front_transform_calls']) for r in rows),
                                verified=sum(int(r['front_transform_verified']) for r in rows), mismatch=0,
                                reference_seconds=sum(float(r['front_transform_reference_seconds']) for r in rows)))
            for repeat in (1, 2, 3):
                for route in ('split_active', 'front_transform'):
                    relative = Path(f'route_{route}/p{n}/{stem}/repeat_{repeat}/algorithm_certificate.csv')
                    require((original / relative).read_bytes() == (derived / relative).read_bytes(),
                            'formal certificate changed: ' + str(relative))
                    formal_count += 1
        runs = read_csv(original / f'route_split_active/p{n}/analysis/runs.csv')
        critical = read_csv(original / f'route_split_active/p{n}/analysis/critical_rank_breakdown.csv')
        lookup = {(int(r['partition_seed']), int(r['repeat']), int(r['rank'])): r for r in critical}
        candidate_critical = read_csv(original / f'route_front_transform/p{n}/analysis/critical_rank_breakdown.csv')
        candidate_lookup = {(int(r['partition_seed']), int(r['repeat']), int(r['rank'])): r for r in candidate_critical}
        for run in runs:
            seed, repeat, rank = (int(run[f]) for f in ('partition_seed', 'repeat', 'slowest_compute_rank'))
            row = lookup[seed, repeat, rank]
            candidate_row = candidate_lookup[seed, repeat, rank]
            control_apply, candidate_apply = float(row['front_transform_apply_seconds']), float(candidate_row['front_transform_apply_seconds'])
            require(control_apply > 0 and math.isfinite(control_apply) and math.isfinite(candidate_apply)
                    and candidate_apply >= 0, 'invalid full-matching cost')
            fixed_reductions.append(100 * (1 - candidate_apply / control_apply))
            compute, front = float(row['compute_seconds']), float(row['kernel_front_seconds'])
            components = {}
            for phase in ('prepare', 'cleanup'):
                components[phase] = sum(float(row[f'cost_{scope}_{op}_{phase}_seconds'])
                                        for scope in ('generation', 'optimization', 'repair')
                                        for op in ('combine', 'split', 'swap', 'swap2'))
            require(compute > 0 and all(math.isfinite(v) and v >= 0
                                       for v in (compute, front, *components.values())), 'invalid cost values')
            costs.append(dict(ranks=n, seed=seed, repeat=repeat, rank=rank,
                              compute_seconds=compute, front_seconds=front, front_compute_pct=100*front/compute,
                              operation_prepare_seconds=components['prepare'],
                              operation_cleanup_seconds=components['cleanup'],
                              prepare_cleanup_compute_pct=100*sum(components.values())/compute))
    require(formal_count == 54 and tables_count == 6 and len(costs) == 27 and rank_changes == 2688,
            'formal evidence coverage differs')
    require(len(fixed_reductions) == 27, 'fixed-rank comparison coverage differs')
    result = dict(schema='front_transform_recovery_confirmation_v1', result_commit=sources['result_commit'],
                  algorithm_source_revision=SOURCE, recovery_tool_revision='f093fc2ea11e5b44a577bd29c018027fa3087e38',
                  original_batch=old_sources['batch'].split('/')[-1], derived_batch=sources['batch'], complete=True, quality_validated=True,
                  uploaded_source_objects_verified=len(sources['files']), original_source_objects_verified=len(old_sources['files']),
                  comparison_groups=comparisons, ordinary_profiles_unchanged_by_recorded_sha256=len(ordinary),
                  corrected_warmup_profiles=len(recovered), changed_rank_rows=rank_changes,
                  unchanged_formal_certificate_blobs=formal_count, unchanged_formal_runs_csv_blobs=tables_count,
                  exact_replay_verified=verified, exact_replay_mismatches=0, quality_pairs=pairs, warmup_stats=warmups,
                  raw_profile_independent_recheck=False,
                  qualification='Raw profiles remain local; this audit verifies uploaded provenance, certificates and complete quality-report bytes.',
                  decision='stop_operator_reuse', fixed_rank_apply_pairs=len(fixed_reductions),
                  fixed_rank_apply_wins=sum(r > 0 for r in fixed_reductions),
                  fixed_rank_apply_median_reduction_pct=statistics.median(fixed_reductions))
    output.mkdir(parents=True, exist_ok=True)
    (output / '20261010_front_transform_recovery_confirmation.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    with (output / '20261010_front_transform_cost_screen.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(costs[0]))
        writer.writeheader()
        writer.writerows(costs)
    print(json.dumps({k: result[k] for k in ('quality_validated', 'exact_replay_verified',
                                           'exact_replay_mismatches', 'decision')}, ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original', type=Path)
    parser.add_argument('derived', type=Path)
    parser.add_argument('--output', type=Path, default=HERE)
    args = parser.parse_args()
    audit(args.original, args.derived, args.output)
