#!/usr/bin/env python3
"""修复原程序被稀疏时间戳检查误拒的批次；只分析已有数据，不提交或启动网格。"""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys

import analyze_results as analysis
import analyze_node_scaling as scaling

KNOWN_ERROR = 'missing/invalid natural sync timestamp: face_sparse_match_arrival_elapsed'
HERE = Path(__file__).resolve().parent
STATUS_FIELDS = ('algorithm', 'timing', 'repeat', 'exit_code', 'directory', 'partition_seed')


def recover_folder(folder, plan, scheduler):
    """只恢复已有成功记录，或明确记录为本次后处理错误的运行。"""
    with (folder/'run_status.tsv').open() as stream:
        history = list(csv.DictReader(stream, delimiter='\t'))
    latest = {}
    for row in history:
        latest[row['algorithm'], row['timing'], int(row['partition_seed']), int(row['repeat'])] = row
    errors = (folder/'failures.log').read_text() if (folder/'failures.log').exists() else ''
    changes = []
    for algorithm in json.loads((folder/'plan.json').read_text())['algorithms']:
        for mode in plan['modes']:
            for seed in plan['seeds']:
                name = algorithm + (f'_seed{seed}' if seed != -1 else '') + '_' + mode
                for rep in range(1-plan['warmups'], plan['repeats']+1):
                    key = algorithm, mode, seed, rep
                    row = latest[key]
                    directory = folder/name/f'repeat_{rep}'
                    if row['exit_code'] != '0':
                        prefix = f'{algorithm}/{mode} seed {seed} repeat {rep}: Invalid completed run ('
                        known = (scheduler == 'node_original' and algorithm == 'baseline' and row['exit_code'] == '1'
                                 and any(line.startswith(prefix) and line.endswith(KNOWN_ERROR) for line in errors.splitlines()))
                        if not known:
                            raise ValueError(f'{name}/repeat_{rep}: 非本次后处理错误，保留失败状态')
                    with analysis.run_guard(directory) as acquired:
                        if not acquired or (directory/'RUNNING').exists():
                            raise ValueError(f'{directory}: 仍在运行')
                        critical = []
                        result, stages = analysis.inspect(analysis.profile_path(directory), critical_sink=critical)
                        identity = (result['algorithm'], result['timing'], int(result['partition_seed']), int(result['repeat']))
                        if identity != key or result['ranks'] != int(folder.name[1:]):
                            raise ValueError(f'{directory}: 记录与计划不符')
                        if rep == 0 and mode == 'natural' and plan.get('quality_warmup'):
                            if not result.get('mesh_quality', {}).get('structural_pass'):
                                raise ValueError(f'{directory}: 质量检查未通过')
                        analysis.save_compact_run(directory, result, stages, critical)
                        (directory/'SUCCESS').touch()
                        analysis.try_cleanup(directory)
                    if row['exit_code'] != '0':
                        changes.append(dict(row, exit_code='0'))
    # 原状态保留在历史中，仅在整条路线验证通过后追加纠正结果。
    if changes:
        with (folder/'run_status.tsv').open('a', newline='') as stream:
            csv.DictWriter(stream, fieldnames=STATUS_FIELDS, delimiter='\t').writerows(changes)
    return len(changes)


def run_analyzer(script, args, env):
    proc = subprocess.run([sys.executable, str(HERE/script), *map(str, args)], env=env,
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode:
        raise ValueError(proc.stdout[-4000:] or f'{script}: 退出码 {proc.returncode}')


def recover(root):
    suite = json.loads((root/'node_workload_plan.json').read_text())
    os.environ.update(NODE_SCALING='1', WORKLOAD_ROOT=str(root))
    statuses = []
    for case in suite['cases']:
        child = root/case['directory']
        plan = json.loads((child/'node_scaling_plan.json').read_text())
        env = dict(os.environ, WORKLOAD_ROOT=str(root), COMPACT_RESULTS='1',
                   KERNEL_SCHEDULERS=' '.join(plan['schedulers']), KERNEL_THREAD_COUNTS=str(plan['threads']))
        for p in plan['counts']:
            repaired, problems = 0, []
            state = child/f'p{p}/node_scaling_state.json'
            if not state.exists():
                problems.append('作业尚无终止状态，未恢复')
            else:
                for scheduler in plan['schedulers']:
                    folder = child/f'kernel_{scheduler}_t{plan["threads"]}'/f'p{p}'
                    try:
                        repaired += recover_folder(folder, plan, scheduler)
                        run_analyzer('analyze_results.py', [folder], env)
                    except (OSError, ValueError, KeyError, TypeError, EOFError) as error:
                        problems.append(f'{scheduler}: {error}')
                if not problems:
                    try:
                        run_analyzer('analyze_results.py', [child, '--kernel-overview', '--kernel-ranks', p], env)
                    except ValueError as error:
                        problems.append(str(error))
            if state.exists():
                try:
                    run_analyzer('analyze_node_scaling.py', [child, '--mark-ranks', p, '--exit-code', int(bool(problems))], env)
                except ValueError as error:
                    problems.append(str(error))
            status = dict(levels=case['levels'], ranks=p, recovered_runs=repaired,
                          state='failed' if problems else 'complete', issues='; '.join(problems))
            statuses.append(status)
            scaling.atomic(root/'recovery_status.csv', scaling.csv_text(statuses))
            print(f"面细化={case['levels']} 节点={p//plan['ranks_per_node']}：{status['state']}；恢复 {repaired} 次误拒记录", flush=True)
    run_analyzer('analyze_node_scaling.py', [root, '--workload-summary'], dict(os.environ))
    print(f'查看 {root / "RESULT_SUMMARY.txt"}；未运行网格程序。')
    if any(r['state'] != 'complete' for r in statuses):
        raise SystemExit('部分记录不能恢复，请查看 recovery_status.csv；不要直接重跑整批实验。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='原批次 mesh_algorithms_* 目录')
    args = parser.parse_args()
    with analysis.run_guard(args.root) as acquired:
        if not acquired:
            raise SystemExit('该批次正在恢复，请等待已有分析结束。')
        recover(args.root.resolve())
