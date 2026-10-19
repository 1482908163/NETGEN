#!/usr/bin/env python3
"""Same-resource borrowing validation; standard library, no mesh regeneration."""
import argparse
import csv
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import statistics as st
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
HISTORICAL_BASE = 'fe28c76e90975730bb7a7f6f0dec6f3fee4eed38'
LABELS = {'node_fixed': '固定核', 'node_elastic': '弹性借核', 'node_phaseaware': '阶段受限借核'}


def atomic(path, text):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def revision():
    return subprocess.check_output(['git', '-C', str(PROJECT), 'rev-parse', 'HEAD'], text=True).strip()


def build_info():
    binary = Path(os.environ.get('BINARY', PROJECT / 'build/mesh_occ_mpi/mesh_occ_mpi')).resolve()
    install = Path(os.environ.get('NETGEN_INSTALL_DIR', PROJECT.parent / 'install'))
    kernel = Path(os.environ.get('NETGEN_INSTALL_LIB', install / 'lib')) / 'libnglib.so'
    # A dirty source tree cannot be attributed to the recorded commit.
    dirty = subprocess.check_output(['git', '-C', str(PROJECT), 'diff', 'HEAD', '--name-only',
                                    '--', '.', '../netgen'], text=True).strip()
    if dirty:
        raise ValueError('源码有未提交改动，请提交后重新编译：' + dirty)
    return dict(source_revision=revision(), binary_path=str(binary), kernel_path=str(kernel.resolve()),
                binary_sha256=digest(binary), kernel_sha256=digest(kernel))


def check_build(seal=False):
    actual = build_info()
    stamp = PROJECT / 'build/node_scaling_build.json'
    if seal:
        atomic(stamp, json.dumps(actual, indent=2) + '\n')
    elif json.loads(stamp.read_text()) != actual:
        raise ValueError('程序或内核库与当前源码不匹配，请在本分支重跑 bash build_project.sh')
    expected = os.environ.get('MESH_SOURCE_REVISION')
    if not seal and expected and actual['source_revision'] != expected:
        raise ValueError('排队后源码分支发生变化，请恢复提交时版本')
    return actual


def words(name):
    return os.environ[name].replace(',', ' ').split()


def initialize(root):
    schedulers = words('KERNEL_SCHEDULERS')
    counts = list(map(int, words('PROCESS_COUNTS')))
    seeds = list(map(int, words('PARTITION_SEEDS')))
    modes = words('TIMING_MODES')
    if len(set(counts)) != len(counts) or len(set(seeds)) != len(seeds):
        raise ValueError('规模和种子不能重复')
    if set(schedulers) != set(LABELS) or len(schedulers) != 3:
        raise ValueError('借核扩展验证必须保留固定核、弹性借核、阶段受限借核三条路线')
    if words('KERNEL_THREAD_COUNTS') != ['4'] or os.environ['CPUS_PER_TASK'] != '4' or os.environ['RANKS_PER_NODE'] != '4':
        raise ValueError('本轮固定每节点4进程、每进程4个物理核，以保持历史布局')
    if os.environ['EXPERIMENT_STAGE'] != 'kernel' or os.environ['ALGORITHMS'] != 'sparse' or 'natural' not in modes or len(set(modes)) != len(modes):
        raise ValueError('本轮只比较 sparse 算法，必须包含 natural 计时')
    plan = dict(schema='node_scaling_v1', historical_base=HISTORICAL_BASE, source_revision=revision(),
                counts=counts, schedulers=schedulers, seeds=seeds, modes=modes,
                repeats=int(os.environ['REPEATS']), warmups=int(os.environ['WARMUPS']),
                ranks_per_node=4, cpus_per_task=4, threads=4, exclusive_nodes=True,
                levels=int(os.environ['LEVELS']), refines=int(os.environ['REFINES']),
                partition_variant=os.environ['PARTITION_VARIANT'],
                input_path=os.environ['INPUT_PATH'], maxh=os.environ['MAXH'], minh=os.environ['MINH'],
                quality_warmup=int(os.environ['QUALITY_WARMUP']),
                input_sha256=digest(Path(os.environ['INPUT_PATH'])) if os.environ.get('DRY_RUN') != '1' else 'dry_run',
                start_epoch=int(os.environ['START_EPOCH']))
    target = root / 'node_scaling_plan.json'
    if target.exists():
        old = json.loads(target.read_text())
        if {k:v for k,v in old.items() if k != 'start_epoch'} != {k:v for k,v in plan.items() if k != 'start_epoch'}:
            raise ValueError('结果目录已经包含其他计划，请使用新的 RUN_ROOT')
        plan = old
    atomic(target, json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    if os.environ.get('DRY_RUN') != '1':
        atomic(root / 'node_scaling_build.json', json.dumps(check_build(), indent=2) + '\n')


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def numeric(row, field):
    v = float(row[field])
    if not math.isfinite(v):
        raise ValueError('非有限指标：' + field)
    return v


def csv_text(data):
    if not data:
        return ''
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(data[0]))
    writer.writeheader()
    writer.writerows(data)
    return stream.getvalue()


def collect_scale(root, plan, p):
    """Only expose comparisons after the whole scale passed its existing audits."""
    state = root / f'p{p}/node_scaling_state.json'
    if not state.exists():
        return [], [], ['尚未完成或尚未启动'], 'pending'
    status = json.loads(state.read_text())
    if status['exit_code'] != 0:
        return [], [], [f"作业失败，退出码 {status['exit_code']}；查看 p{p}/RESULT_SUMMARY.txt 和作业日志"], 'failed'
    issue_path = root / f'p{p}/kernel_issues.txt'
    if not issue_path.exists() or issue_path.read_text().strip():
        raise ValueError('缺少或未通过逐规模内核检查')
    overview = rows(root / f'p{p}/kernel_summary.csv')
    audit = rows(root / f'p{p}/ablation_summary.csv')
    summaries = {(r['scheduler'], r['timing'], int(r['seed'])): r for r in overview}
    raw = {}
    critical = {}
    for scheduler in plan['schedulers']:
        folder = root / f'kernel_{scheduler}_t4/p{p}'
        for name in ('issues.txt', 'critical_issues.txt'):
            path = folder / 'analysis' / name
            if not path.exists() or path.read_text().strip():
                raise ValueError(f'{scheduler}: {name} 未通过')
        for r in rows(folder / 'analysis/runs.csv'):
            if r['algorithm'] != 'sparse' or int(r['ranks']) != p:
                raise ValueError('意外的算法或规模')
            key = scheduler, r['timing'], int(r['partition_seed']), int(r['repeat'])
            if key in raw:
                raise ValueError('重复的正式测量')
            raw[key] = r
        for r in rows(folder / 'analysis/kernel_critical_ranks.csv'):
            if 'compute' in r['selection'].split(';'):
                key = scheduler, r['timing'], int(r['partition_seed']), int(r['repeat'])
                if key in critical:
                    raise ValueError('重复的最慢计算进程')
                critical[key] = r
    pairs = []
    report = []
    build = json.loads((root / 'node_scaling_build.json').read_text())
    for candidate, contribution in (('node_elastic', 'borrowing'), ('node_phaseaware', 'phaseaware')):
        for mode in plan['modes']:
            for seed in plan['seeds']:
                gate = [r for r in audit if r['contribution'] == contribution and r['timing'] == mode and int(r['seed']) == seed]
                if len(gate) != 1 or gate[0]['quality_pass'] != 'True':
                    raise ValueError(f'{candidate}/{mode}/{seed} 质量对照未通过')
                group = []
                for repeat in range(1, plan['repeats'] + 1):
                    ck = 'node_fixed', mode, seed, repeat
                    nk = candidate, mode, seed, repeat
                    control, new = raw[ck], raw[nk]
                    oldcrit, newcrit = critical[ck], critical[nk]
                    for r in (control, new):
                        if (r['scaling_source_revision'] != build['source_revision'] or
                            r['scaling_binary_sha256'] != build['binary_sha256'] or
                            r['scaling_kernel_sha256'] != build['kernel_sha256'] or
                            r['scaling_input_sha256'] != plan['input_sha256']):
                            raise ValueError('源码、程序或内核库指纹不匹配')
                    if control['scaling_work_sha256'] != new['scaling_work_sha256']:
                        raise ValueError(f'{candidate}/{mode}/{seed}/{repeat} 逐进程单元/边界/幽灵计数不同')
                    ct, nt = numeric(control, 'core_seconds'), numeric(new, 'core_seconds')
                    row = dict(ranks=p, nodes=p // 4, candidate=candidate, timing=mode, seed=seed, repeat=repeat,
                               control_seconds=ct, candidate_seconds=nt, reduction_pct=100 * (1 - nt / ct),
                               speedup=ct / nt, control_compute_max=numeric(control, 'compute_max_seconds'),
                               candidate_compute_max=numeric(new, 'compute_max_seconds'),
                               control_imbalance=numeric(control, 'compute_imbalance'),
                               candidate_imbalance=numeric(new, 'compute_imbalance'),
                               control_elements=numeric(control, 'volume_elements_sum'),
                               candidate_elements=numeric(new, 'volume_elements_sum'),
                               critical_rank=int(newcrit['rank']),
                               first_loan_fraction=newcrit.get('first_loan_fraction', ''),
                               critical_borrowed_core_seconds=numeric(newcrit, 'coop_borrowed_core_seconds'),
                               critical_compute_seconds=numeric(newcrit, 'compute_seconds'),
                               control_critical_compute_seconds=numeric(oldcrit, 'compute_seconds'),
                               critical_generation_seconds=numeric(newcrit, 'kernel_generation_seconds'),
                               critical_repair_seconds=numeric(newcrit, 'kernel_repair_seconds'),
                               critical_optimization_seconds=numeric(newcrit, 'kernel_optimization_seconds'),
                               critical_team_start_seconds=numeric(newcrit, 'kernel_team_start_seconds'),
                               critical_team_stop_seconds=numeric(newcrit, 'kernel_team_stop_seconds'),
                               critical_management_seconds=numeric(newcrit, 'coop_management_seconds'),
                               source_revision=build['source_revision'], workload_sha256=new['scaling_work_sha256'])
                    pairs.append(row)
                    group.append(row)
                old = summaries['node_fixed', mode, seed]
                new = summaries[candidate, mode, seed]
                if int(old['repeats']) != plan['repeats'] or int(new['repeats']) != plan['repeats']:
                    raise ValueError('正式重复不完整')
                report.append(dict(ranks=p, nodes=p // 4, candidate=candidate, timing=mode, seed=seed,
                                   control_seconds=st.median(r['control_seconds'] for r in group),
                                   candidate_seconds=st.median(r['candidate_seconds'] for r in group),
                                   paired_reduction_pct=st.median(r['reduction_pct'] for r in group),
                                   wins=sum(r['reduction_pct'] > 0 for r in group), pairs=len(group),
                                   control_imbalance=old['compute_imbalance'], candidate_imbalance=new['compute_imbalance'],
                                   volume_elements=new['volume_elements'], quality='pass'))
    expected = len(plan['schedulers']) * len(plan['seeds']) * len(plan['modes']) * plan['repeats']
    if len(raw) != expected or len(critical) != expected:
        raise ValueError('正式运行总数与计划不一致')
    return report, pairs, [], 'complete'


def summarize(root):
    plan = json.loads((root / 'node_scaling_plan.json').read_text())
    all_rows, all_pairs, states, errors = [], [], [], []
    for p in plan['counts']:
        try:
            report, pairs, issues, state = collect_scale(root, plan, p)
        except (OSError, ValueError, KeyError, TypeError) as error:
            report, pairs, issues, state = [], [], [str(error)], 'invalid'
        all_rows.extend(report)
        all_pairs.extend(pairs)
        states.append(dict(ranks=p, nodes=p // 4, state=state, issues='; '.join(issues)))
        if state in ('failed', 'invalid'):
            errors.append(f'p{p}: ' + '; '.join(issues))
    atomic(root / 'node_scaling_summary.csv', csv_text(all_rows))
    atomic(root / 'node_scaling_pairs.csv', csv_text(all_pairs))
    atomic(root / 'node_scaling_status.csv', csv_text(states))
    lines = ['弹性借核跨规模验证', '完整核心时间：粗网格结束后的屏障至邻接完成，包含借核与线程管理开销。',
             '固定核/弹性借核/阶段受限借核：相同节点、进程、4个初始物理核；每节点4进程。',
             'natural（自然计时）判断净收益；split（拆分计时）只辅助解释等待。',
             '下表降幅为同种子、同重复编号的逐次降幅中位数；正值更快。',
             'P变化会改变分区和生成单元数，本实验不是严格固定网格强扩展，也不能直接验收P1目标。', '']
    lines.extend(f"P={r['ranks']}，节点={r['nodes']}：{r['state']} {r['issues']}" for r in states)
    lines += ['', '进程 方案 种子 固定核秒 借核秒 配对降幅% 胜出/正式次数 不均衡(固定→借核)']
    for r in all_rows:
        if r['timing'] == 'natural':
            lines.append(f"{r['ranks']} {LABELS[r['candidate']]} {r['seed']} {r['control_seconds']:.6f} "
                         f"{r['candidate_seconds']:.6f} {r['paired_reduction_pct']:.2f} {r['wins']}/{r['pairs']} "
                         f"{float(r['control_imbalance']):.3f}→{float(r['candidate_imbalance']):.3f}")
    lines += ['', '质量门槛只覆盖每组自然预热；正式轮逐进程工作量计数相同不等于几何恒等证明。',
              'node_scaling_pairs.csv 保存慢进程借核时机、阶段时间、线程启停和管理耗时。',
              'first_loan_fraction 为空表示该最慢计算进程没有借核，不用零代替。',
              '每轮最慢计算进程可改变；各阶段最大值不能相加为核心时间。',
              '结果仅覆盖本输入、网格参数、节点布局和已完成规模；不能据此宣称任意规模可扩展。']
    atomic(root / 'RESULT_SUMMARY.txt', '\n'.join(lines) + '\n')
    return states, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--init', action='store_true')
    parser.add_argument('--seal-build', action='store_true')
    parser.add_argument('--verify-build', action='store_true')
    parser.add_argument('--mark-ranks', type=int)
    parser.add_argument('--exit-code', type=int, default=0)
    args = parser.parse_args()
    if args.seal_build or args.verify_build:
        check_build(args.seal_build)
        return
    args.root.mkdir(parents=True, exist_ok=True)
    with (args.root / '.node_scaling.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.init:
            initialize(args.root)
        if args.mark_ranks is not None:
            folder = args.root / f'p{args.mark_ranks}'
            folder.mkdir(exist_ok=True)
            atomic(folder / 'node_scaling_state.json', json.dumps(dict(exit_code=args.exit_code)) + '\n')
        states, errors = summarize(args.root)
    print('借核验证汇总：' + str(args.root / 'RESULT_SUMMARY.txt'))
    if args.mark_ranks is None and not args.init and (errors or any(r['state'] != 'complete' for r in states)):
        raise SystemExit(1)
    if args.mark_ranks is not None:
        own = next(r for r in states if r['ranks'] == args.mark_ranks)
        if own['state'] != 'complete':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
