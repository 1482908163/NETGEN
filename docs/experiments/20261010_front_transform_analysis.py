#!/usr/bin/env python3
"""复算已上传的正式算子成本；缺失候选质量预热时明确禁止加速结论。

python3 docs/experiments/20261010_front_transform_analysis.py \
  test_code_expansion/strong_scaling_results/mesh_algorithms_20261010-012256
"""
import argparse
import csv
import hashlib
import json
import statistics as st
from pathlib import Path

SOURCE = 'f5b73b1c0394537bd8159048c1db7d88558bb967'
FIELDS = ('calls', 'transforms', 'dense_terms', 'evaluated_terms', 'skipped_terms',
          'fallback', 'compiled_rules', 'compile_seconds', 'verified', 'mismatches',
          'reference_seconds', 'apply_seconds')
ROUTES = ('split_active', 'front_transform')
csv.field_size_limit(32 * 1024 * 1024)


def read_csv(path, delimiter=','):
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream, delimiter=delimiter))


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def reduction(control, candidate):
    return 100 * (1 - float(candidate) / float(control))


def analyze(root, output):
    manifest = json.loads(Path(__file__).with_name('20261010_front_transform_source_manifest.json').read_text())
    for entry in manifest['files']:
        data = (root / entry['path']).read_bytes()
        actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if len(data) != entry['bytes'] or actual != entry['git_blob_sha']:
            raise ValueError('uploaded source evidence differs: ' + entry['path'])
    runtime = json.loads((root / 'runtime_manifest.json').read_text())
    assert runtime['source_revision'] == SOURCE
    runs, critical, certificates, statuses = {}, {}, {}, []
    for route in ROUTES:
        for n in (128, 256, 512):
            folder = root / ('route_' + route) / f'p{n}'
            statuses.extend(read_csv(folder / 'run_status.tsv', '\t'))
            for run in read_csv(folder / 'analysis/runs.csv'):
                key = (n, int(run['partition_seed']), int(run['repeat']), route)
                assert key not in runs and run['source_revision'] == SOURCE
                assert run['volume_front_distance'] == 'distance_original_v1'
                assert run['volume_front_transform'] == ('dense_rule_operator_v1' if route == 'split_active' else 'compiled_rule_operator_v1')
                stem = 'sparse_natural' if key[1] == -1 else f'sparse_seed{key[1]}_natural'
                cert = read_csv(folder / stem / f'repeat_{key[2]}/algorithm_certificate.csv')
                assert len(cert) == n and [int(r['rank']) for r in cert] == list(range(n))
                assert all(r['source_revision'] == SOURCE and r['binary_sha256'] == run['binary_sha256'] for r in cert)
                signature = [[int(r['rank'])] + [float(r['front_transform_' + f]) for f in FIELDS] for r in cert]
                assert signature == json.loads(run['front_transform_certificate_signature'])
                for r in cert:
                    m = lambda f: float(r['front_transform_' + f])
                    assert m('dense_terms') == m('evaluated_terms') + m('skipped_terms')
                    assert m('calls') == float(r['front_distance_calls'])
                    assert m('transforms') == float(r['front_distance_candidates'])
                    assert all(m(f) == 0 for f in ('verified', 'mismatches', 'reference_seconds', 'fallback'))
                    assert all(float(r['front_distance_' + f]) == 0 for f in ('verified', 'mismatches', 'reference_seconds', 'hits', 'fallback', 'allocated_cells'))
                for f in FIELDS:
                    assert abs(sum(float(r['front_transform_' + f]) for r in cert) - float(run['front_transform_' + f])) < 1e-7
                runs[key], certificates[key] = run, cert
            for r in read_csv(folder / 'analysis/critical_rank_breakdown.csv'):
                key = (n, int(r['partition_seed']), int(r['repeat']), route, int(r['rank']))
                assert key not in critical
                critical[key] = r
    assert len(runs) == 54 and len(statuses) == 72
    failed = [r for r in statuses if r['exit_code'] != '0']
    assert len(failed) == 9 and all(r['repeat'] == '0' and 'route_front_transform' in r['directory'] for r in failed)
    pairs, fixed, summaries = [], [], []
    for n in (128, 256, 512):
        comparisons = read_csv(root / f'p{n}/route_comparisons.csv')
        assert len(comparisons) == 3
        assert all(r['validated'] == 'False' and r['quality_issues'] == 'missing_audit' for r in comparisons)
        for seed in (-1, 17, 41):
            group, same = [], []
            for repeat in (1, 2, 3):
                key = (n, seed, repeat)
                c, d = (runs[key + (route,)] for route in ROUTES)
                assert c['binary_sha256'] == d['binary_sha256']
                for field in ('front_distance_work_signature', 'front_transform_work_signature'):
                    assert json.loads(c[field]) == json.loads(d[field])
                for left, right in zip(certificates[key + ('split_active',)], certificates[key + ('front_transform',)]):
                    for prefix, fields in (('front_distance_', ('calls', 'queries', 'rules', 'candidates')),
                                           ('front_transform_', ('calls', 'transforms', 'dense_terms'))):
                        assert all(float(left[prefix + f]) == float(right[prefix + f]) for f in fields)
                rank = int(c['slowest_compute_rank'])
                a, b = (critical[key + (route, rank)] for route in ROUTES)
                for r, route in ((a, 'split_active'), (b, 'front_transform')):
                    cert = certificates[key + (route,)][rank]
                    assert all(float(r['front_transform_' + f]) == float(cert['front_transform_' + f]) for f in FIELDS)
                p = dict(ranks=n, seed=seed, repeat=repeat, quality_validated=False,
                         control_core_seconds=float(c['core_seconds']), candidate_core_seconds=float(d['core_seconds']),
                         core_reduction_pct=reduction(c['core_seconds'], d['core_seconds']),
                         dense_terms=float(d['front_transform_dense_terms']), skipped_terms=float(d['front_transform_skipped_terms']),
                         skipped_pct=100 * float(d['front_transform_skipped_terms']) / float(d['front_transform_dense_terms']),
                         control_all_rank_apply_seconds=float(c['front_transform_apply_seconds']),
                         candidate_all_rank_apply_seconds=float(d['front_transform_apply_seconds']),
                         all_rank_apply_reduction_pct=reduction(c['front_transform_apply_seconds'], d['front_transform_apply_seconds']))
                f = dict(ranks=n, seed=seed, repeat=repeat, rank=rank, selection='control_slowest_compute',
                         control_apply_seconds=float(a['front_transform_apply_seconds']),
                         candidate_apply_seconds=float(b['front_transform_apply_seconds']),
                         apply_reduction_pct=reduction(a['front_transform_apply_seconds'], b['front_transform_apply_seconds']),
                         control_front_seconds=float(a['kernel_front_seconds']), candidate_front_seconds=float(b['kernel_front_seconds']),
                         control_compute_seconds=float(a['compute_seconds']), candidate_compute_seconds=float(b['compute_seconds']),
                         skipped_pct=100 * float(b['front_transform_skipped_terms']) / float(b['front_transform_dense_terms']))
                pairs.append(p); fixed.append(f); group.append(p); same.append(f)
            published = next(r for r in comparisons if int(r['seed']) == seed)
            assert abs(st.median(r['core_reduction_pct'] for r in group) - float(published['paired_reduction_pct'])) < 1e-9
            summaries.append(dict(ranks=n, seed=seed, quality_validated=False,
                                  skipped_pct=group[0]['skipped_pct'], core_reduction_pct=st.median(r['core_reduction_pct'] for r in group),
                                  core_wins=sum(r['core_reduction_pct'] > 0 for r in group),
                                  fixed_apply_reduction_pct=st.median(r['apply_reduction_pct'] for r in same),
                                  fixed_apply_wins=sum(r['apply_reduction_pct'] > 0 for r in same),
                                  all_rank_apply_reduction_pct=st.median(r['all_rank_apply_reduction_pct'] for r in group)))
    evidence = dict(schema='front_transform_result_evidence_v1', result_commit=manifest['result_commit'],
                    source_revision=SOURCE, verified_source_files=len(manifest['files']), formal_certificate_runs=54,
                    per_rank_work_equal_pairs=27, formal_reference_counter_runs=0, failed_warmups=9,
                    retained_tree_checks=manifest['retained_tree_checks'], quality_validated=False,
                    fixed_apply_wins=sum(r['apply_reduction_pct'] > 0 for r in fixed), fixed_apply_pairs=27,
                    fixed_apply_median_reduction_pct=st.median(r['apply_reduction_pct'] for r in fixed),
                    decision='stop_operator_reuse; recover_missing_audits_from_original_profiles')
    output.mkdir(parents=True, exist_ok=True)
    prefix = '20261010_front_transform_'
    write_csv(output / (prefix + 'pairs.csv'), pairs)
    write_csv(output / (prefix + 'same_rank.csv'), fixed)
    write_csv(output / (prefix + 'summary.csv'), summaries)
    (output / (prefix + 'evidence.json')).write_text(json.dumps(evidence, indent=2, allow_nan=False) + '\n')
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    analyze(args.root, args.output)
