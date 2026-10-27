#!/usr/bin/env python3
"""独立复核恢复并行三规模实验；只读原始缓存，输出可追溯的 JSON。

用法：python3 review_recovery_scaling.py <批次目录> <输出 JSON> [--front-group]
要求每组 1 次质量预热、3 次正式重复；缺失或矛盾数据直接失败。
"""
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import median
import sys

MODES = ('node_rules_elastic', 'node_rules_recovery', 'node_rules_recovery_online')
PHASES = ('compute_seconds', 'kernel_generation_seconds', 'kernel_front_seconds',
          'kernel_front_applyrules_seconds', 'front_unattributed_seconds',
          'kernel_gen_recovery_seconds', 'kernel_gen_recovery_topology_seconds',
          'kernel_gen_recovery_smooth_seconds', 'volume_refine_seconds')
QUALITY = ('counts', 'shape_min', 'shape_mean', 'angle_min', 'angle_max',
           'abs_volume', 'shape_hist', 'surface_fingerprint_sha256',
           'volume_fingerprint_sha256', 'integrity_profile')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_group(r, mode, repeat):
    expected = '2' if mode.endswith('_front') and repeat == 0 else '1' if mode.endswith('_front') else '0'
    require(r.get('front_group_profile') == 'bounded_group_v1' and r.get('front_group_mode') == expected,
            '前沿组模式或版本不符')
    counts = {k: r['kernel_group_' + k] for k in ('calls', 'rejected', 'verified', 'inside_calls', 'inside_verified', 'mismatches')}
    require(all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 and v == int(v)
                for v in counts.values()), '无效前沿组计数')
    require(counts['calls'] > 0 and counts['rejected'] <= counts['calls'] and
            counts['inside_calls'] <= counts['calls'] - counts['rejected'] and counts['mismatches'] == 0 and
            counts['verified'] == (counts['calls'] if expected == '2' else 0) and
            counts['inside_verified'] == (counts['inside_calls'] if expected == '2' else 0),
            '前沿组重放覆盖不完整或不一致')
    for k in ('build_seconds', 'inner_seconds', 'inside_seconds'):
        require(math.isfinite(r['kernel_group_' + k]) and r['kernel_group_' + k] >= 0, '无效前沿组计时')
    return counts


def review(batch, front_group=False):
    modes = ('node_rules_recovery', 'node_rules_recovery_front') if front_group else MODES
    comparisons = [('front_group', 0, 1)] if front_group else [
        ('generation_recovery', 0, 1), ('recovery_online', 1, 2), ('recovery_total', 0, 2)]
    root = batch / 'levels4'
    report = {'batch': batch.name, 'audit': {'runs': 0, 'warmups': 0, 'formal': 0},
              'scales': {}, 'source_sha256': {}}

    def read(path):
        data = path.read_bytes()
        report['source_sha256'][str(path.relative_to(batch))] = hashlib.sha256(data).hexdigest()
        return data.decode()

    identities = []
    for ranks in (256, 512, 1024):
        scale = {'nodes': ranks // 4, 'ranks': ranks, 'strategies': {}, 'comparisons': {}}
        quality_reference = None
        work_reference = None
        group_reference = None
        retained = {}
        for mode in modes:
            retained[mode] = []
            group = root / f'kernel_{mode}_t4' / f'p{ranks}'
            status = list(csv.DictReader(read(group / 'run_status.tsv').splitlines(), delimiter='\t'))
            require(len(status) == 4 and sorted(int(r['repeat']) for r in status) == [0, 1, 2, 3],
                    f'{group}: 缺失或重复运行')
            require(all(int(r['exit_code']) == 0 and r['algorithm'] == 'sparse' and
                        r['timing'] == 'natural' and int(r['partition_seed']) == -1 for r in status),
                    f'{group}: 运行失败或配置不符')
            times, critical = [], []
            for repeat in range(4):
                path = group / 'sparse_natural' / f'repeat_{repeat}' / 'analysis_metrics.json'
                data = json.loads(read(path))
                r = data['result']
                if front_group:
                    counts = check_group(r, mode, repeat)
                    signature = {k: counts[k] for k in ('calls', 'rejected', 'inside_calls')}
                    if group_reference is None:
                        group_reference = signature
                    require(signature == group_reference, f'{path}: 同规模前沿组工作次数不一致')
                    if repeat == 0 and mode.endswith('_front'):
                        scale['candidate_warmup_group_replay'] = counts
                require((r['ranks'], r['repeat'], r['algorithm'], r['timing'], r['partition_seed'],
                         r['surface_levels'], r['volume_refines']) ==
                        (ranks, repeat, 'sparse', 'natural', -1, 4, 2), f'{path}: 缓存身份不符')
                work = {k: r[k] for k in ('scaling_kernel_work_sha256', 'scaling_work_sha256',
                        'volume_kernel_elements_sum', 'post_refine_elements_sum', 'volume_elements_sum')}
                require(work['post_refine_elements_sum'] == work['volume_elements_sum'] ==
                        64 * work['volume_kernel_elements_sum'], f'{path}: 两轮细化计数不符')
                if work_reference is None:
                    work_reference = work
                require(work == work_reference, f'{path}: 同规模工作签名不同')
                require(r['kernel_rules_mismatches'] == 0, f'{path}: 规则重放不一致')
                report['audit']['runs'] += 1
                if repeat == 0:
                    q = r['mesh_quality']
                    require(q['structural_pass'] and not q['issues'] and
                            q['counts']['hard_errors'] == 0 and
                            q['counts']['checked'] == q['counts']['elements'] == r['volume_elements_sum'],
                            f'{path}: 质量检查未通过或覆盖不完整')
                    require((q['scheduler'], q['ranks'], q['threads'], q['seed']) == (mode, ranks, 4, -1),
                            f'{path}: 质量身份不符')
                    require(r['kernel_rules_parallel_calls'] > 0 and
                            r['kernel_rules_verified_calls'] == r['kernel_rules_parallel_calls'],
                            f'{path}: 预热重放覆盖不完整')
                    current = {k: q[k] for k in QUALITY}
                    if quality_reference is None:
                        quality_reference = current
                    require(current == quality_reference, f'{path}: 同规模质量或指纹不同')
                    identities.append(q['identity'])
                    identity = q['identity']
                    scale['verified_rule_calls_per_warmup'] = r['kernel_rules_verified_calls']
                    report['audit']['warmups'] += 1
                else:
                    require(r['kernel_rules_verified_calls'] == 0, f'{path}: 正式计时含规则重放')
                    seconds = r['core_seconds']
                    require(math.isfinite(seconds) and seconds > 0, f'{path}: 无效正式时间')
                    rows = [c for c in data['critical'] if 'compute' in c['selection'].split(';')]
                    require(len(rows) == 1, f'{path}: 关键计算进程不唯一')
                    c = rows[0]
                    require((c['scheduler'], int(c['ranks']), int(c['repeat'])) == (mode, ranks, repeat),
                            f'{path}: 关键进程身份不符')
                    for key, qkey in [('binary_sha256', 'MESH_BINARY_SHA256'),
                                      ('kernel_sha256', 'MESH_KERNEL_SHA256'),
                                      ('input_sha256', 'MESH_INPUT_SHA256'),
                                      ('source_revision', 'MESH_SOURCE_REVISION')]:
                        require(c[key] == identity[qkey], f'{path}: 正式轮来源与预热不同')
                    require(math.isclose(float(c['run_core_seconds']), seconds), f'{path}: 核心时间矛盾')
                    times.append(seconds)
                    critical.append(c)
                    retained[mode].append(data['critical'])
                    report['audit']['formal'] += 1
            scale['strategies'][mode] = {
                'seconds': times, 'median_seconds': median(times),
                'critical_ranks': [int(c['rank']) for c in critical],
                'critical_phase_medians': {k: median(float(c[k]) for c in critical) for k in PHASES},
            }
        scale['work'] = work_reference
        scale['quality'] = quality_reference
        published = {r['contribution']: r for r in csv.DictReader(
            read(root / f'p{ranks}' / 'ablation_summary.csv').splitlines())}
        for name, a, b in comparisons:
            left = scale['strategies'][modes[a]]['seconds']
            right = scale['strategies'][modes[b]]['seconds']
            paired = [100 * (1 - y / x) for x, y in zip(left, right)]
            reduction = 100 * (1 - median(right) / median(left))
            wins = sum(y < x for x, y in zip(left, right))
            row = published[name]
            for field, expected in [('control_seconds', median(left)), ('candidate_seconds', median(right)),
                                    ('reduction_pct', reduction), ('paired_reduction_pct', median(paired)),
                                    ('paired_count', 3), ('paired_wins', wins)]:
                require(math.isclose(float(row[field]), expected, rel_tol=1e-9, abs_tol=1e-9),
                        f'{ranks}/{name}: 汇总 {field} 与逐轮结果不符')
            require(row['quality_pass'] == row['same_volume_fingerprint'] == 'True' and not row['quality_issues'],
                    f'{ranks}/{name}: 汇总质量状态不符')
            scale['comparisons'][name] = {'paired_reduction_pct': paired, 'paired_median_pct': median(paired),
                                         'median_time_reduction_pct': reduction, 'wins': wins, 'pairs': 3}
        if front_group:
            # Compare each run's compute bottlenecks at the SAME rank across
            # variants, using retained phase maxima when the bottleneck moves.
            ranks_to_match = sorted({rank for s in scale['strategies'].values() for rank in s['critical_ranks']})
            scale['matched_ranks'] = {}
            for rank in ranks_to_match:
                matched = {}
                count_reference = None
                for mode in modes:
                    rows = [next((c for c in run if c['rank'] == rank), None) for run in retained[mode]]
                    require(all(c is not None for c in rows), f'{ranks}/{rank}: 缺少同进程阶段证据')
                    for repeat, c in enumerate(rows, 1):
                        require((c['scheduler'], int(c['ranks']), int(c['repeat'])) == (mode, ranks, repeat) and
                                c['binary_sha256'] == identity['MESH_BINARY_SHA256'] and
                                c['kernel_sha256'] == identity['MESH_KERNEL_SHA256'] and
                                c['input_sha256'] == identity['MESH_INPUT_SHA256'] and
                                c['source_revision'] == identity['MESH_SOURCE_REVISION'], '同进程来源不符')
                        require(all(c['kernel_group_' + k] == 0 for k in ('verified', 'inside_verified', 'mismatches')),
                                '正式关键进程含重放或不一致')
                        signature = {k: c[k] for k in ('kernel_front_iterations', 'kernel_front_rotations',
                            'kernel_front_successful_iterations', 'kernel_front_failed_iterations',
                            'kernel_group_calls', 'kernel_group_rejected', 'kernel_group_inside_calls')}
                        if count_reference is None:
                            count_reference = signature
                        require(signature == count_reference, f'{ranks}/{rank}: 前沿决定次数不一致')
                    fields = set(PHASES) | {k for k in rows[0] if k.startswith(('kernel_gen_', 'kernel_group_'))}
                    fields |= {'kernel_delaunay_seconds', 'kernel_domain_repair_seconds',
                               'kernel_repair_seconds', 'kernel_optimization_seconds'}
                    matched[mode] = {
                        'seconds_by_repeat': {k: [c[k] for c in rows] for k in sorted(fields) if k.endswith('_seconds')},
                        'medians': {k: median(float(c[k]) for c in rows) for k in sorted(fields)},
                        'front_decision_counts': signature,
                    }
                scale['matched_ranks'][str(rank)] = matched
        report['scales'][str(ranks)] = scale
    require(all(i == identities[0] for i in identities), '跨策略/规模二进制、输入或实验配置不同')
    report['identity'] = identities[0]
    base = report['scales']['256']
    for scale in report['scales'].values():
        scale['generated_work_growth_pct_vs_256'] = 100 * (
            scale['work']['volume_kernel_elements_sum'] / base['work']['volume_kernel_elements_sum'] - 1)
        for mode, result in scale['strategies'].items():
            result['observed_speedup_vs_64_nodes'] = base['strategies'][mode]['median_seconds'] / result['median_seconds']
    report['limitations'] = [
        '只有预热执行完整质量审计；正式轮核查工作签名与已保存来源，不能声称正式轮逐单元审计。',
        '节点数改变时生成网格数量与工作签名改变；耗时比不是固定离散网格的严格强扩展效率。',
        '每组仅三次正式重复，逐轮胜出不等于统计显著性。',
        'SOURCE_REVISION=manual；可核查二进制哈希，不能据此证明确切源码提交。',
        '保存了完整性功能版本，未保留成功运行的每条阶段指纹日志。',
        '128节点的关键计算进程变化；不同进程阶段时间之差不能作为同进程优化归因。',
    ]
    if front_group:
        report['limitations'].append('逐进程原画像已清理；独立检查覆盖总重放计数和保留的关键进程，不重建未保存的逐调用日志。')
    return report


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != '--front-group'):
        raise SystemExit(__doc__)
    result = review(Path(sys.argv[1]), front_group=len(sys.argv) == 4)
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['audit'], ensure_ascii=False))
