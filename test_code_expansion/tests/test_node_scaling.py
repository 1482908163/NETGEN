#!/usr/bin/env python3
"""Check missing pairs, quality gates, workload consistency and job failure retention."""
import importlib.util
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('node_scaling', ROOT / 'strong_scaling/analyze_node_scaling.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(module.csv_text(rows))


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    plan = dict(counts=[64, 128], schedulers=list(module.LABELS), seeds=[-1, 17],
                modes=['natural', 'split'], repeats=2, levels=2, refines=1, measure_kernel_workload=True)
    (root / 'node_scaling_plan.json').write_text(json.dumps(plan))
    pdir = root / 'p64'
    pdir.mkdir()
    (pdir / 'node_scaling_state.json').write_text('{"exit_code":0}')
    (pdir / 'kernel_issues.txt').write_text('')
    summary, audit = [], []
    for scheduler in plan['schedulers']:
        folder = root / f'kernel_{scheduler}_t4/p64/analysis'
        folder.mkdir(parents=True)
        for name in ('issues.txt', 'critical_issues.txt'):
            (folder / name).write_text('')
        raw, critical = [], []
        for mode in plan['modes']:
            for seed in plan['seeds']:
                summary.append(dict(scheduler=scheduler, timing=mode, seed=seed, repeats=2,
                                    compute_imbalance=2, volume_elements=100))
                if scheduler != 'node_fixed':
                    audit.append(dict(contribution='borrowing' if scheduler == 'node_elastic' else 'phaseaware',
                                      timing=mode, seed=seed, quality_pass=True))
                for repeat in (1, 2):
                    r = dict(algorithm='sparse', ranks=64, timing=mode, partition_seed=seed, repeat=repeat,
                             core_seconds=10 if scheduler == 'node_fixed' else 8,
                             compute_max_seconds=5, compute_imbalance=2, volume_elements_sum=100,
                             scaling_work_sha256=f'{seed}', scaling_source_revision='source',
                             scaling_input_sha256='input', scaling_binary_sha256='binary', scaling_kernel_sha256='kernel')
                    r.update({field: 1 for field in module.WORKLOAD_FIELDS})
                    r.update(volume_kernel_elements_sum=80, volume_kernel_elements_mean=1.25,
                             volume_kernel_elements_max=2, volume_kernel_elements_imbalance=1.6,
                             scaling_kernel_work_sha256=f'kernel-{seed}',
                             local_volume_mesh_max_seconds=5 if scheduler == 'node_fixed' else 4)
                    raw.append(r)
                    c = dict(r, rank=7, selection='compute;generation', first_loan_fraction='',
                             coop_borrowed_core_seconds=0, compute_seconds=5,
                             kernel_generation_seconds=3, kernel_repair_seconds=1, kernel_optimization_seconds=1,
                             kernel_team_start_seconds=.1, kernel_team_stop_seconds=.1, coop_management_seconds=.1)
                    critical.append(c)
        write_csv(folder / 'runs.csv', raw)
        write_csv(folder / 'kernel_critical_ranks.csv', critical)
    write_csv(pdir / 'kernel_summary.csv', summary)
    write_csv(pdir / 'ablation_summary.csv', audit)
    states, errors = module.summarize(root)
    assert states[0]['state'] == 'complete' and states[1]['state'] == 'pending' and not errors
    result = module.rows(root / 'node_scaling_summary.csv')
    assert len(result) == 8 and all(abs(float(r['paired_reduction_pct']) - 20) < 1e-10 for r in result)
    assert all(abs(float(r['kernel_reduction_pct']) - 20) < 1e-10 and
               float(r['control_volume_kernel_elements_sum']) == 80 and float(r['volume_elements']) == 100 for r in result)
    pairs = module.rows(root / 'node_scaling_pairs.csv')
    assert len(pairs) == 16 and all(r['first_loan_fraction'] == '' for r in pairs)
    # Removing just one pair invalidates the scale; no partial success remains.
    path = root / 'kernel_node_elastic_t4/p64/analysis/runs.csv'
    original = path.read_text()
    write_csv(path, module.rows(path)[:-1])
    states, errors = module.summarize(root)
    assert states[0]['state'] == 'invalid' and errors and not (root / 'node_scaling_summary.csv').read_text()
    path.write_text(original)
    for bad_value in ('', 'different-kernel-work'):
        data = module.rows(path)
        data[0]['scaling_kernel_work_sha256'] = bad_value
        write_csv(path, data)
        assert module.summarize(root)[0][0]['state'] == 'invalid'
        path.write_text(original)
    data = module.rows(path)
    data[0]['scaling_work_sha256'] = 'changed'
    write_csv(path, data)
    states, errors = module.summarize(root)
    assert states[0]['state'] == 'invalid' and errors
    path.write_text(original)
    original_audit = (pdir / 'ablation_summary.csv').read_text()
    data = module.rows(pdir / 'ablation_summary.csv')
    data[0]['quality_pass'] = False
    write_csv(pdir / 'ablation_summary.csv', data)
    assert module.summarize(root)[0][0]['state'] == 'invalid'
    (pdir / 'ablation_summary.csv').write_text(original_audit)
    (pdir / 'node_scaling_state.json').write_text('{"exit_code":124}')
    assert module.summarize(root)[0][0]['state'] == 'failed'
    assert not (root / 'node_scaling_summary.csv').read_text()
    # 原程序使用 baseline 通信，借核使用 sparse：总收益与纯借核收益分开。
    plan.update(schedulers=['node_original','node_fixed','node_elastic'], overall_comparison=True)
    (root/'node_scaling_plan.json').write_text(json.dumps(plan))
    (pdir/'node_scaling_state.json').write_text('{"exit_code":0}')
    source=root/'kernel_node_fixed_t4/p64/analysis'
    original=root/'kernel_node_original_t4/p64/analysis'
    shutil.copytree(source,original)
    for name in ('runs.csv','kernel_critical_ranks.csv'):
        data=module.rows(original/name)
        for r in data:
            r['algorithm']='baseline'
            r['core_seconds']=20
        write_csv(original/name,data)
    overview=[dict(r,algorithm='sparse') for r in summary if r['scheduler']!='node_phaseaware']
    overview += [dict(r,scheduler='node_original',algorithm='baseline') for r in summary if r['scheduler']=='node_fixed']
    write_csv(pdir/'kernel_summary.csv',overview)
    write_csv(pdir/'ablation_summary.csv', [dict(r,contribution='total' if r['contribution']=='phaseaware' else 'borrowing') for r in audit])
    states,errors=module.summarize(root)
    assert states[0]['state']=='complete' and not errors
    report=module.rows(root/'node_scaling_summary.csv')
    assert {r['comparison'] for r in report}=={'total','borrowing'}
    for r in report:
        expected=60 if r['comparison']=='total' else 20
        assert abs(float(r['paired_reduction_pct'])-expected)<1e-10
        assert r['control_algorithm']==('baseline' if r['comparison']=='total' else 'sparse')

print('node scaling summary: pairing, workload, quality, pending and failure checks passed without build stamps')

# 直接检验新计数的汇总，不用体细化后的单元数代替内核单元数。
sys.path.insert(0, str(ROOT / 'strong_scaling'))
from analyze_results import volume_workload_metrics
samples = [dict(rank=i, metrics=dict(volume_kernel_input_points=4, volume_kernel_input_surface_elements=4,
    volume_kernel_points=4, volume_kernel_surface_elements=4, volume_kernel_elements=n,
    local_volume_elements_before_adjacency=n*8), stages={}) for i, n in enumerate((2, 6))]
metrics = volume_workload_metrics(samples)
assert metrics['volume_kernel_elements_sum'] == 8
assert metrics['volume_kernel_elements_mean'] == 4
assert metrics['volume_kernel_elements_max'] == 6
assert metrics['volume_kernel_elements_imbalance'] == 1.5
original_env = os.environ.get('WORKLOAD_ROOT')
os.environ['WORKLOAD_ROOT'] = 'fixture'
try:
    try:
        volume_workload_metrics([dict(rank=0, metrics={}, stages={})])
    except ValueError:
        pass
    else:
        raise AssertionError('缺失内核计数必须报告为缺失，不能反推')
finally:
    if original_env is None:
        os.environ.pop('WORKLOAD_ROOT', None)
    else:
        os.environ['WORKLOAD_ROOT'] = original_env
print('pre-refine workload: actual counts and missing-data checks passed')
