#!/usr/bin/env python3
"""Check missing pairs, quality gates, provenance and job failure retention."""
import csv
import importlib.util
import json
from pathlib import Path
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
                modes=['natural', 'split'], repeats=2, input_sha256='input')
    (root / 'node_scaling_plan.json').write_text(json.dumps(plan))
    build = dict(source_revision='source', binary_sha256='binary', kernel_sha256='kernel')
    (root / 'node_scaling_build.json').write_text(json.dumps(build))
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
    pairs = module.rows(root / 'node_scaling_pairs.csv')
    assert len(pairs) == 16 and all(r['first_loan_fraction'] == '' for r in pairs)
    # Removing just one pair invalidates the scale; no partial success remains.
    path = root / 'kernel_node_elastic_t4/p64/analysis/runs.csv'
    original = path.read_text()
    write_csv(path, module.rows(path)[:-1])
    states, errors = module.summarize(root)
    assert states[0]['state'] == 'invalid' and errors and not (root / 'node_scaling_summary.csv').read_text()
    path.write_text(original)
    for field, value in [('scaling_work_sha256', 'changed'), ('scaling_kernel_sha256', 'wrong-library')]:
        data = module.rows(path)
        data[0][field] = value
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
print('node scaling summary: pairing, provenance, quality, pending and failure checks passed')
