#!/usr/bin/env python3
"""补审 f5b73b1 的回放诊断串写：保留原批次，生成带来源记录的派生批次。

不运行 MPI，不放宽正式分析器，不改变任何网格、算法工作量或计时。
只在已知源码版本、精确变换预热、完整计数证据同时成立时，将旧距离
诊断误记的 verified/reference_seconds 在派生记录中归零。
"""
import argparse
import gzip
import hashlib
import json
import math
import shutil
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

from analyze_results import inspect, profile_path, run_guard, write_csv
from analyze_worklet_routes import analyze

SOURCE = 'f5b73b1c0394537bd8159048c1db7d88558bb967'
ROUTES = ('split_active', 'front_transform')
CHANGED = ('front_distance_verified', 'front_distance_reference_seconds')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_rows(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def corrected_rows(rows):
    """严格识别已知串写；返回新记录和每个进程修改前的两个字段。"""
    if not rows:
        raise ValueError('missing original warmup profiles')
    n = rows[0]['ranks']
    metadata = rows[0]['metadata']
    if (metadata.get('MESH_SOURCE_REVISION') != SOURCE or
            metadata.get('volume_cost_profile') != 'phase_full_cost_v3' or
            metadata.get('volume_front_distance') != 'distance_original_v1' or
            metadata.get('volume_front_transform') != 'transform_exact_replay_v1' or
            metadata.get('timing_mode') != 'natural' or
            metadata.get('mesh_quality') != 'volume_audit_v1'):
        raise ValueError('profile does not match the known replay diagnostic bug')
    if len(rows) != n or sorted(r['rank'] for r in rows) != list(range(n)):
        raise ValueError('missing or duplicate warmup ranks')
    derived = json.loads(json.dumps(rows, allow_nan=False))
    changed = []
    for row in derived:
        if row['metadata'] != metadata or row['ranks'] != n or row['repeat'] != 0:
            raise ValueError('inconsistent warmup identity')
        m = row['metrics']
        calls = m['front_distance_calls']
        if (m['front_distance_verified'] != calls or
                m['front_transform_verified'] != calls or
                m['front_transform_calls'] != calls or
                m['front_distance_mismatches'] or m['front_transform_mismatches'] or
                any(m['front_distance_' + f] for f in ('hits', 'fallback', 'allocated_cells')) or
                m['front_distance_queries'] != m['front_distance_computed']):
            raise ValueError('warmup evidence differs from the known diagnostic contamination')
        distance_time = m['front_distance_reference_seconds']
        transform_time = m['front_transform_reference_seconds']
        if (not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0
                    for v in (calls, distance_time, transform_time)) or
                distance_time > transform_time or
                transform_time - distance_time > calls * .001 + 1e-6 or
                (calls == 0 and (distance_time or transform_time))):
            raise ValueError('reference timing does not match shared replay diagnostics')
        changed.append(dict(rank=row['rank'], before={k: m[k] for k in CHANGED},
                            after={k: 0 for k in CHANGED}))
        for key in CHANGED:
            m[key] = 0
    if not any(r['before']['front_distance_verified'] for r in changed):
        raise ValueError('no diagnostic contamination to recover')
    return derived, changed


def write_profiles(path, rows):
    with gzip.open(path, 'wt') as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False, separators=(',', ':')) + '\n')


def recover(root, output):
    root, output = root.resolve(), output.resolve()
    if not root.is_dir() or output.exists() or output == root or root in output.parents:
        raise ValueError('source must exist; output must be a new sibling directory')
    manifest = json.loads((root / 'runtime_manifest.json').read_text())
    if manifest.get('source_revision') != SOURCE:
        raise ValueError('runtime snapshot is not the known source revision')
    scales = [int(s) for s in (root / 'requested_process_counts.txt').read_text().split()]
    if not scales or len(set(scales)) != len(scales):
        raise ValueError('invalid requested process counts')
    runs, plans = [], []
    for route in ROUTES:
        for n in scales:
            folder = root / ('route_' + route) / f'p{n}'
            plan = json.loads((folder / 'plan.json').read_text())
            if (plan.get('ranks') != n or plan.get('algorithms') != ['sparse'] or
                    plan.get('timings') != ['natural'] or plan.get('repeats') != 3 or
                    plan.get('partition_seeds') != [-1, 17, 41] or not plan.get('quality_warmup')):
                raise ValueError('unexpected experiment plan: ' + str(folder))
            plans.append(folder)
            for seed in plan['partition_seeds']:
                stem = 'sparse_natural' if seed == -1 else f'sparse_seed{seed}_natural'
                for repeat in range(4):
                    directory = folder / stem / f'repeat_{repeat}'
                    if directory.is_symlink() or any(p.is_symlink() for p in directory.parents):
                        raise ValueError('source path contains a symlink')
                    raw = profile_path(directory)
                    if raw.is_symlink():
                        raise ValueError('profile path contains a symlink')
                    repair = route == 'front_transform' and repeat == 0
                    if repair:
                        reason = (directory / 'failure_reason.txt').read_text()
                        if ((directory / 'SUCCESS').exists() or
                                not reason.startswith('analysis_validation_failure\n') or
                                'original front entered distance cache' not in reason):
                            raise ValueError('warmup was not rejected solely by the known diagnostic gate')
                    elif not (directory / 'SUCCESS').is_file():
                        raise ValueError('another run is incomplete: ' + str(directory))
                    runs.append((directory, raw, repair))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    with ExitStack() as locks:
        for directory, _, _ in runs:
            if not locks.enter_context(run_guard(directory)) or (directory / 'RUNNING').exists():
                raise ValueError('run is still active: ' + str(directory))
        try:
            temporary = Path(tempfile.mkdtemp(prefix='.front-replay-', dir=output.parent))
            evidence = dict(schema='front_transform_replay_recovery_v1', source_revision=SOURCE,
                            original_batch=str(root), derived_batch=str(output),
                            changed_fields=list(CHANGED), runs=[], complete=False)
            for name in ('runtime_manifest.json', 'requested_process_counts.txt'):
                shutil.copyfile(root / name, temporary / name)
            for folder in plans:
                target = temporary / folder.relative_to(root)
                target.mkdir(parents=True)
                shutil.copyfile(folder / 'plan.json', target / 'plan.json')
                # Original runner exit codes remain evidence, not new launch successes.
                status = folder / 'run_status.tsv'
                if status.exists():
                    shutil.copyfile(status, target / 'original_run_status.tsv')
            analyses = {}
            for directory, raw, repair in runs:
                relative = directory.relative_to(root)
                target = temporary / relative
                target.mkdir(parents=True)
                record = dict(directory=str(relative), original_profile=raw.name,
                              original_sha256=sha256(raw), recovered=repair)
                if repair:
                    rows, changes = corrected_rows(load_rows(raw))
                    if rows[0]['ranks'] != int(directory.parent.parent.name[1:]):
                        raise ValueError('profile rank count differs from plan')
                    seed = -1 if directory.parent.name == 'sparse_natural' else int(directory.parent.name.split('_seed')[1].split('_')[0])
                    if int(rows[0]['metadata']['partition_seed']) != seed:
                        raise ValueError('profile partition seed differs from plan')
                    shutil.copyfile(raw, target / ('original_' + raw.name))
                    shutil.copyfile(directory / 'failure_reason.txt', target / 'original_failure_reason.txt')
                    write_profiles(target / 'rank_profiles.jsonl.gz', rows)
                    record['rank_changes'] = changes
                else:
                    shutil.copyfile(raw, target / raw.name)
                cert, costs = [], []
                phase = analyses.setdefault(directory.parent.parent, dict(runs=[], critical=[], costs=[]))
                result, _ = inspect(profile_path(target), certificate_sink=cert, cost_sink=costs,
                                    critical_sink=phase['critical'])
                phase['costs'].extend(costs)
                if result['repeat'] > 0:
                    phase['runs'].append(result)
                if result['source_revision'] != SOURCE:
                    raise ValueError('a run used another source revision')
                if result['repeat'] == 0 and not result['mesh_quality']['structural_pass']:
                    raise ValueError('volume quality audit failed')
                if cert:
                    write_csv(target / 'algorithm_certificate.csv', cert)
                if costs:
                    write_csv(target / 'operation_cost_profile.csv', costs)
                if 'mesh_quality' in result:
                    (target / 'quality_summary.json').write_text(json.dumps(result['mesh_quality'], indent=2, allow_nan=False) + '\n')
                (target / 'SUCCESS').touch()
                record['derived_sha256'] = sha256(profile_path(target))
                evidence['runs'].append(record)
            for folder, phase in analyses.items():
                target = temporary / folder.relative_to(root) / 'analysis'
                target.mkdir()
                for name, rows in (('runs.csv', phase['runs']),
                                   ('critical_rank_breakdown.csv', phase['critical']),
                                   ('volume_operation_costs.csv', phase['costs'])):
                    write_csv(target / name, rows)
            # The unchanged strict analyzer checks full quality equality, per-rank
            # work, operation coverage and matching certificates before publication.
            for n in scales:
                if not analyze(temporary, n, ROUTES):
                    raise ValueError('strict paired audit failed at p' + str(n))
                # Analyzer source paths must remain valid after the atomic rename.
                for path in (temporary / f'p{n}').glob('*.csv'):
                    path.write_text(path.read_text().replace(str(temporary), str(output)))
            evidence['complete'] = True
            (temporary / 'replay_recovery_manifest.json').write_text(json.dumps(evidence, indent=2, allow_nan=False) + '\n')
            temporary.rename(output)
            temporary = None
        finally:
            if temporary is not None:
                shutil.rmtree(temporary)
    print('补审完成；原始批次未修改，未运行 MPI：' + str(output))
    print('请提交派生批次的 p*/ 报告、预热质量/证书及 replay_recovery_manifest.json。')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, help='new derived batch; defaults to ROOT_replay_recovered')
    args = parser.parse_args()
    output = args.output or args.root.with_name(args.root.name + '_replay_recovered')
    try:
        recover(args.root, output)
    except (OSError, EOFError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, '补审失败，原始数据未更改：' + str(error) + '\n')


if __name__ == '__main__':
    main()
