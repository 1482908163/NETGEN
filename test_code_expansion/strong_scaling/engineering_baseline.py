#!/usr/bin/env python3
"""同批工程基准对照；只读已完成结果，不启动网格或编译。

由 analyze_results.py 的 --kernel-overview 自动调用。
缺失、失败、身份/工作量/网格不一致均保留为无效比较，不筛选成功轮报收益。
"""
import csv
import json
import math
from pathlib import Path
from statistics import median

REFERENCE = ('node_engineered', 'baseline')
CONTRACT = 'fixed_recovery_v1'
WORK = ('scaling_kernel_work_sha256', 'scaling_work_sha256')
INCREMENTS = (
    ('工程并行覆盖', ('node_original', 'baseline'), REFERENCE),
    ('稀疏通信', REFERENCE, ('node_engineered', 'sparse')),
    ('固定资源接口', ('node_engineered', 'sparse'), ('node_recovery_fixed', 'sparse')),
    ('恢复基础上的规则并行', ('node_recovery_fixed', 'sparse'), ('node_rules_recovery_fixed', 'sparse')),
    ('恢复与规则并行基础上的借核', ('node_rules_recovery_fixed', 'sparse'), ('node_rules_recovery', 'sparse')),
    ('旧固定核基础上的借核', ('node_fixed', 'sparse'), ('node_elastic', 'sparse')),
    ('旧固定核基础上的规则并行', ('node_fixed', 'sparse'), ('node_rules_fixed', 'sparse')),
    ('旧借核基础上的规则并行', ('node_elastic', 'sparse'), ('node_rules_elastic', 'sparse')),
    ('旧方案补齐恢复并行', ('node_rules_elastic', 'sparse'), ('node_rules_recovery', 'sparse')),
    ('在线借核', ('node_rules_recovery', 'sparse'), ('node_rules_recovery_online', 'sparse')),
    ('前沿组工程优化', ('node_rules_recovery', 'sparse'), ('node_rules_recovery_front', 'sparse')),
    ('恢复拓扑工程优化', ('node_rules_recovery_front', 'sparse'), ('node_rules_recovery_topology', 'sparse')),
    ('高容错规则并行', ('node_rules_recovery_topology', 'sparse'), ('node_rules_recovery_quiet', 'sparse')),
    ('恢复平滑依赖调度', ('node_rules_recovery_quiet', 'sparse'), ('node_rules_recovery_dag', 'sparse')),
    ('紧凑依赖图增量', ('node_rules_recovery_dag', 'sparse'), ('node_rules_recovery_dag_compact', 'sparse')),
    ('紧凑依赖调度整体增量', ('node_rules_recovery_quiet', 'sparse'), ('node_rules_recovery_dag_compact', 'sparse')),
    ('连续边去重增量', ('node_rules_recovery_dag', 'sparse'), ('node_rules_recovery_dag_stream', 'sparse')),
    ('连续边依赖调度整体增量', ('node_rules_recovery_quiet', 'sparse'), ('node_rules_recovery_dag_stream', 'sparse')),
    ('共享规则准备', ('node_rules_recovery_topology', 'sparse'), ('node_rules_recovery_shared', 'sparse')),
    ('自适应搜索', ('node_rules_elastic', 'sparse'), ('node_search_elastic', 'sparse')),
    ('搜索基础上的在线借核', ('node_search_elastic', 'sparse'), ('node_search_online', 'sparse')),
    ('跨节点协议增量', ('node_search_online', 'sparse'), ('node_search_full', 'sparse')),
)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def read_arm(folder, scheduler, algorithm, ranks, threads, seed, repeats):
    """覆盖一项预热和全部正式轮；成功标记与缓存必须同时存在。"""
    subdir = f'{algorithm}_natural' if seed == -1 else f'{algorithm}_seed{seed}_natural'
    records = {}
    for repeat in range(repeats + 1):
        directory = folder / subdir / f'repeat_{repeat}'
        require((directory / 'SUCCESS').is_file() and not (directory / 'RUNNING').exists(),
                f'第{repeat}轮未成功完成')
        data = json.loads((directory / 'analysis_metrics.json').read_text())
        require(data.get('schema') == 'analysis_metrics_v1', '结果格式不符')
        r = data['result']
        require(tuple(r.get(k) for k in ('comparison_scheduler', 'algorithm', 'ranks',
                'timing', 'partition_seed', 'repeat')) ==
                (scheduler, algorithm, ranks, 'natural', seed, repeat), '运行身份不符')
        identity = r.get('comparison_identity', {})
        require(identity and all(v not in ('unset', '', None) for v in identity.values()), '比较身份缺失')
        require(identity.get('core_only') == 'true' and
                identity.get('timing_boundary') == 'post_coarse_barrier_to_adjacency_complete', '核心计时边界不符')
        require(int(identity['kernel_threads']) == threads and
                int(identity['CPUS_PER_TASK']) == threads and int(identity['RANKS_PER_NODE']) == 4,
                '核数或每节点进程数不符')
        require(all(isinstance(r.get(k), str) and len(r[k]) == 64 for k in WORK), '工作量指纹缺失')
        for name in ('kernel_rules_mismatches', 'kernel_group_mismatches',
                     'kernel_topology_mismatches', 'kernel_shared_mismatches', 'kernel_quiet_mismatches', 'kernel_smoothing_mismatches'):
            require(r.get(name, 0) == 0, name + '非零')
        if scheduler in ('node_rules_recovery_dag','node_rules_recovery_dag_compact','node_rules_recovery_dag_stream'):
            stream=scheduler=='node_rules_recovery_dag_stream'
            compact=stream or scheduler=='node_rules_recovery_dag_compact'
            expected=('6' if repeat==0 else '5') if stream else ('4' if repeat==0 else '3') if compact else ('2' if repeat==0 else '1')
            require(r.get('recovery_smoothing_profile')=='color_dependency_v1' and
                    r.get('recovery_smoothing_mode')==expected, '缺少恢复平滑候选身份')
            require(r.get('kernel_smoothing_dag_calls',0)>0 and r.get('kernel_smoothing_tasks',0)>0 and
                    r.get('kernel_smoothing_completed')==r['kernel_smoothing_tasks'] and
                    r.get('kernel_smoothing_verified')==(r['kernel_smoothing_dag_calls'] if repeat==0 else 0),
                    '恢复平滑覆盖或重放不完整')
            if compact:
                require(r.get('recovery_smoothing_graph_profile')==('edge_stream_v3' if stream else 'unique_incident_v2') and
                        r.get('kernel_smoothing_graph_verified')==(r['kernel_smoothing_dag_calls'] if repeat==0 else 0),
                        '紧凑依赖图身份或核验覆盖不完整')
                raw=r.get('kernel_smoothing_raw_edges');removed=r.get('kernel_smoothing_deduplicated_edges')
                require(isinstance(raw,(int,float)) and isinstance(removed,(int,float)) and
                        raw>=removed>=0 and raw-removed==r.get('kernel_smoothing_edges'), '紧凑依赖边计数不一致')
        if scheduler == 'node_engineered':
            require(r.get('engineering_baseline_contract') == CONTRACT and
                    r.get('generation_recovery') == 'ordered_team_v1' and
                    r.get('rule_search_mode') == 0, '工程基准配置不符')
            require(r.get('coop_borrow_epochs') == 0 and r.get('coop_borrowed_core_seconds') == 0 and
                    r.get('coop_peak_threads', threads + 1) <= threads, '工程基准意外借核或超出固定核数')
            require(r.get('kernel_gen_recovery_sweeps', 0) > 0 and
                    r.get('kernel_gen_recovery_team_sweeps', 0) > 0, '本批未覆盖工程恢复线程组')
            require(str(r.get('front_group_mode')) == '0' and
                    str(r.get('recovery_topology_mode')) == '0' and
                    r.get('quiet_rule_parallelism') == 'off' and
                    r.get('shared_rule_preparation') == 'off' and
                    r.get('recovery_smoothing_mode','0') == '0', '工程基准混入其他候选')
        require(isinstance(r.get('core_seconds'), (int, float)) and
                math.isfinite(r['core_seconds']) and r['core_seconds'] > 0, '核心耗时无效')
        if repeat == 0:
            q = r['mesh_quality']
            require(q['structural_pass'] and not q['issues'] and q['counts']['hard_errors'] == 0 and
                    q['counts']['checked'] == q['counts']['elements'] == r['volume_elements_sum'],
                    '预热完整质量审计未通过')
            require(q['scheduler'] == scheduler and q['algorithm'] == algorithm and
                    q['threads'] == threads and q['seed'] == seed and q['ranks'] == ranks, '预热身份不符')
            require(r['kernel_rules_verified_calls'] == r['kernel_rules_parallel_calls'], '预热规则重放不完整')
        else:
            require(r['kernel_rules_verified_calls'] == 0, '正式计时含规则重放')
        records[repeat] = r
    return records


def compare(old, new, name, kind, threads, seed, arms, failures, ranks):
    key_a = (threads, seed, *old)
    key_b = (threads, seed, *new)
    row = dict(kind=kind, comparison=name, ranks=ranks, threads=threads, seed=seed,
               control='+'.join(old), candidate='+'.join(new), status='invalid', reason='',
               control_median_seconds=None, candidate_median_seconds=None,
               paired_saved_seconds=None, paired_reduction_pct=None, paired_speedup=None,
               paired_wins=None, paired_count=0)
    pairs = []
    try:
        require(key_a not in failures and key_b not in failures,
                '; '.join(failures.get(k, '') for k in (key_a, key_b) if k in failures))
        a, b = arms[key_a], arms[key_b]
        require(set(a) == set(b) and len(a) > 1, '重复数不一致或没有正式轮')
        qa, qb = a[0]['mesh_quality'], b[0]['mesh_quality']
        for field in ('surface_fingerprint_sha256', 'volume_fingerprint_sha256'):
            require(qa.get(field) and qa.get(field) == qb.get(field), '预热网格指纹不同：' + field)
        for field in ('counts', 'shape_hist', 'shape_min', 'shape_mean', 'angle_min', 'angle_max', 'abs_volume'):
            va, vb = qa[field], qb[field]
            equal = math.isclose(va, vb, rel_tol=1e-10, abs_tol=1e-12) if isinstance(va, (int, float)) else va == vb
            require(equal, '预热质量统计不同：' + field)
        for repeat in sorted(a):
            ra, rb = a[repeat], b[repeat]
            require(ra['comparison_identity'] == rb['comparison_identity'], '同轮输入、二进制或资源身份不同')
            for field in WORK:
                require(ra[field] == rb[field] == a[0][field] == b[0][field], '生成或后续工作量改变：' + field)
            if repeat:
                ta, tb = ra['core_seconds'], rb['core_seconds']
                pairs.append(dict(kind=kind, comparison=name, ranks=ranks, threads=threads, seed=seed,
                                  control=row['control'], candidate=row['candidate'], repeat=repeat,
                                  control_seconds=ta, candidate_seconds=tb, saved_seconds=ta-tb,
                                  reduction_pct=100*(1-tb/ta), speedup=ta/tb))
        row.update(status='valid', paired_count=len(pairs), paired_wins=sum(p['saved_seconds'] > 0 for p in pairs),
                   control_median_seconds=median(p['control_seconds'] for p in pairs),
                   candidate_median_seconds=median(p['candidate_seconds'] for p in pairs),
                   paired_saved_seconds=median(p['saved_seconds'] for p in pairs),
                   paired_reduction_pct=median(p['reduction_pct'] for p in pairs),
                   paired_speedup=median(p['speedup'] for p in pairs))
    except (KeyError, ValueError, TypeError) as error:
        row['reason'] = str(error)
        pairs = []
    return row, pairs


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        if rows:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def engineering_report(root, ranks, selected):
    root = Path(root)
    arms, failures, planned = {}, {}, set()
    issues = []
    for threads, scheduler in selected:
        threads = int(threads)
        folder = root / f'kernel_{scheduler}_t{threads}' / f'p{ranks}'
        try:
            plan = json.loads((folder / 'plan.json').read_text())
            require(plan.get('quality_warmup') and 'natural' in plan['timings'] and
                    plan['ranks'] == ranks and plan['repeats'] > 0, '缺少完整自然计时/质量计划')
            for algorithm in plan['algorithms']:
                for seed in plan['partition_seeds']:
                    key = (threads, seed, scheduler, algorithm)
                    planned.add(key)
                    try:
                        arms[key] = read_arm(folder, scheduler, algorithm, ranks, threads, seed, plan['repeats'])
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        failures[key] = str(error)
        except (OSError, ValueError, KeyError, TypeError) as error:
            issues.append(f'{scheduler}/t{threads}: {error}')
    rows, pairs = [], []
    for threads, seed in sorted({k[:2] for k in planned}):
        available = {k[2:] for k in planned if k[:2] == (threads, seed)}
        baseline_key = (threads, seed, *REFERENCE)
        if baseline_key not in planned:
            failures[baseline_key] = '缺少同批原通信工程基准，禁止换用历史数据'
        for candidate in sorted(available):
            if candidate == REFERENCE:
                continue
            row, detail = compare(REFERENCE, candidate, '相对工程基准', 'total', threads, seed, arms, failures, ranks)
            rows.append(row)
            pairs.extend(detail)
        for name, old, new in INCREMENTS:
            if old in available and new in available:
                row, detail = compare(old, new, name, 'increment', threads, seed, arms, failures, ranks)
                rows.append(row)
                pairs.extend(detail)
    out = root / f'p{ranks}'
    out.mkdir(exist_ok=True)
    write_csv(out / 'engineering_baseline_comparisons.csv', rows)
    write_csv(out / 'engineering_baseline_pairs.csv', pairs)
    lines = ['统一工程基准：原通信＋原规则搜索＋固定核修复/恢复并行（fixed_recovery_v1）。',
             '正值为降时；百分比、节省秒数和加速比分别按正式轮配对后取中位数。',
             '候选保留原有开关，用于重评历史组合；旧方案未含恢复并行时，退化不等于其单个组件无效。',
             '预热质量/网格与全部轮次工作量核对通过才发布收益；正式轮不重复完整几何审计。']
    if not rows:
        issues.append('没有工程基准比较，检查计划和必需对照是否缺失')
    for row in rows:
        if row['status'] != 'valid':
            issues.append(f"{row['comparison']}/{row['candidate']}/t{row['threads']}/seed{row['seed']}: {row['reason']}")
            lines.append(f"{row['comparison']} {row['candidate']}: 无效，{row['reason']}")
        else:
            lines.append(f"{row['comparison']} {row['candidate']}：{row['control_median_seconds']:.4f}→"
                         f"{row['candidate_median_seconds']:.4f}秒；配对减少{row['paired_saved_seconds']:.4f}秒，"
                         f"降幅{row['paired_reduction_pct']:.2f}%，加速比{row['paired_speedup']:.4f}，"
                         f"胜出{row['paired_wins']}/{row['paired_count']}（t{row['threads']}/seed{row['seed']}）。")
    lines += ['嵌套阶段不能相加；不将局部四核旋转链计时与此处真实网格核心时间混排。',
              '相邻工程优化收益单列，不自动视为算法创新；远程协议无实际任务时不能称跨节点执行收益。']
    (out / 'ENGINEERING_BASELINE_SUMMARY.txt').write_text('\n'.join(lines + issues) + '\n')
    return lines, issues
