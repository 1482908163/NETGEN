#!/usr/bin/env python3
"""只读复核无输出规则候选；每规模两策略、一次预热、三次正式。"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from review_recovery_scaling import check_group, check_topology, require

MODES = ('node_rules_recovery_topology', 'node_rules_recovery_quiet')
WORK = ('volume_kernel_elements', 'volume_kernel_input_surface_elements',
        'local_volume_elements_before_adjacency', 'kernel_front_iterations',
        'kernel_front_rotations', 'kernel_rules_calls', 'kernel_rules_face_trials',
        'kernel_rules_point_trials', 'kernel_rules_mappings', 'kernel_gen_recovery_sweeps',
        'kernel_gen_recovery_team_sweeps', 'kernel_gen_domains', 'kernel_gen_front_passes',
        'kernel_group_calls', 'kernel_group_rejected', 'kernel_group_inside_calls',
        'kernel_topology_calls', 'kernel_topology_optimized', 'kernel_topology_skipped_rebuilds',
        'kernel_quiet_high_calls')
TOTAL_WORK = ('scaling_kernel_work_sha256', 'scaling_work_sha256',
              'volume_kernel_elements_sum', 'volume_elements_sum', 'post_refine_elements_sum',
              'kernel_rules_calls', 'kernel_rules_rules', 'kernel_rules_eligible_rules',
              'kernel_rules_face_trials', 'kernel_rules_point_trials', 'kernel_rules_mappings',
              'kernel_final_illegal', 'kernel_quiet_high_calls')
PHASES = ('compute_seconds', 'kernel_generation_seconds', 'kernel_delaunay_seconds',
          'kernel_front_seconds', 'kernel_front_applyrules_seconds', 'kernel_quiet_high_seconds',
          'kernel_group_build_seconds', 'kernel_gen_recovery_seconds',
          'kernel_domain_repair_seconds', 'kernel_repair_seconds',
          'kernel_optimization_seconds', 'volume_refine_seconds')
QUIET_COUNTS = tuple('kernel_quiet_' + k for k in
                     ('high_calls', 'eligible_calls', 'parallel_calls', 'verified', 'mismatches', 'serial_calls'))


def quiet_check(r, candidate, warmup):
    values = [r[k] for k in QUIET_COUNTS]
    require(all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 and int(v) == v
                for v in values), '无效高容错计数')
    h, e, p, v, m, s = values
    require(h == p+s and 0 <= p <= e <= h <= r['kernel_rules_calls'] and
            p <= r['kernel_rules_parallel_calls'] and
            s <= r['kernel_rules_calls']-r['kernel_rules_parallel_calls'] and m == 0 and
            v == (p if warmup else 0), '高容错工作守恒或重放不符')
    require(candidate or (e == p == 0), '对照意外启用候选')
    t, pt = r['kernel_quiet_high_seconds'], r['kernel_quiet_parallel_seconds']
    require(all(math.isfinite(x) for x in (t, pt)) and 0 <= pt <= t and
            (candidate or pt == 0), '高容错时间不符')


def review(batch):
    report = dict(batch=batch.name, runs=0, warmups=0, formal=0, scales={}, source_sha256={})
    def read(path):
        data = path.read_bytes()
        report['source_sha256'][str(path.relative_to(batch))] = hashlib.sha256(data).hexdigest()
        return data.decode()
    requested = [int(s) for s in read(batch/'levels4/requested_process_counts.txt').split()]
    require(requested and len(set(requested)) == len(requested) and
            all(n > 0 and n % 4 == 0 for n in requested), '无效请求规模')
    for n in requested:
        scale = dict(nodes=n//4, ranks=n, strategies={})
        reference_work = reference_quality = reference_identity = reference_rank_work = None
        reference_configuration = None
        retained = {}
        for mode in MODES:
            candidate = mode == MODES[1]
            root = batch/f'levels4/kernel_{mode}_t4/p{n}'
            status = list(csv.DictReader(read(root/'run_status.tsv').splitlines(), delimiter='\t'))
            require(len(status) == 4 and sorted(int(r['repeat']) for r in status) == [0,1,2,3]
                    and all(int(r['exit_code']) == 0 and r['algorithm'] == 'sparse' and
                            r['timing'] == 'natural' and int(r['partition_seed']) == -1 for r in status),
                    '运行缺失、重复、失败或配置不符')
            configuration = read(root/'configuration.txt')
            require('KERNEL_ORDER=interleaved_v2' in configuration, '缺少交错执行配置')
            normalized = configuration.replace(mode, '<scheduler>')
            if reference_configuration is None: reference_configuration = normalized
            require(normalized == reference_configuration, '两策略运行配置不同')
            times, runs = [], []
            for rep in range(4):
                d = json.loads(read(root/f'sparse_natural/repeat_{rep}/analysis_metrics.json'))
                r = d['result']
                require((r['ranks'], r['repeat'], r['partition_seed'], r['surface_levels'],
                         r['volume_refines'], r['algorithm'], r['timing']) ==
                        (n, rep, -1, 4, 2, 'sparse', 'natural'), '运行身份不符')
                require(r['quiet_rule_profile'] == 'no_buffer_v1' and
                        r['quiet_rule_parallelism'] == ('on' if candidate else 'off'), '候选模式不符')
                check_group(r, MODES[0], rep)
                check_topology(r, MODES[0], rep)
                quiet_check(r, candidate, rep == 0)
                require(r['shared_rule_preparation'] == 'off' and r['kernel_shared_calls'] == 0,
                        '混入共享准备候选')
                require(r['rule_search_mode'] == (2 if rep == 0 else 1) and
                        r['kernel_rules_mismatches'] == 0 and r['kernel_rules_verified_calls'] ==
                        (r['kernel_rules_parallel_calls'] if rep == 0 else 0), '规则重放模式不符')
                require(r['coop_phase_0_min_threads'] == r['coop_phase_0_max_threads'] == 4 and
                        r['coop_phase_0_borrowed_core_seconds'] == 0 and
                        r['coop_node_ranks'] == 4 and r['coop_node_cpus'] == 16, '资源配置不符')
                work = {k:r[k] for k in TOTAL_WORK}
                require(r['volume_elements_sum'] == r['post_refine_elements_sum'] ==
                        64*r['volume_kernel_elements_sum'], '细化工作量不符')
                if reference_work is None: reference_work = work
                require(work == reference_work, '同规模全局工作不一致')
                report['runs'] += 1
                if rep == 0:
                    q = r['mesh_quality']
                    require(q['structural_pass'] and not q['issues'] and q['counts']['hard_errors'] == 0
                            and q['counts']['checked'] == q['counts']['elements'] == r['volume_elements_sum']
                            and (q['scheduler'], q['ranks'], q['threads'], q['seed']) == (mode,n,4,-1),
                            '预热质量或审计覆盖失败')
                    quality = {k:v for k,v in q.items() if k != 'scheduler'}
                    if reference_quality is None: reference_quality = quality
                    require(quality == reference_quality, '同规模质量、指纹或身份不一致')
                    identity = {k:v for k,v in q['identity'].items() if k.startswith('MESH_')}
                    if reference_identity is None: reference_identity = identity
                    scale.setdefault('warmup_verification', {})[mode] = {
                        k:r[k] for k in ('kernel_rules_verified_calls', 'kernel_quiet_verified',
                                        'kernel_group_verified', 'kernel_group_inside_verified',
                                        'kernel_topology_verified')}
                    report['warmups'] += 1
                    continue
                rp = d['rank_phases']
                require(rp['schema'] == 'all_ranks_v1' and rp['identity'] == reference_identity,
                        '正式全进程来源不符')
                rows = [dict(zip(rp['columns'], row)) for row in rp['rows']]
                require(all(len(row) == len(rp['columns']) for row in rp['rows']) and
                        len(rows) == n and sorted(x['rank'] for x in rows) == list(range(n)),
                        '进程记录缺失或重复')
                rows = sorted(rows, key=lambda x:x['rank'])
                rank_work = [{k:x[k] for k in ('rank','node',*WORK)} for x in rows]
                if reference_rank_work is None: reference_rank_work = rank_work
                require(rank_work == reference_rank_work, '同进程工作量或节点映射改变')
                for x in rows:
                    quiet_check(x, candidate, False)
                    check_topology(x, MODES[0], rep, aggregate=False)
                    require(x['kernel_group_verified'] == x['kernel_group_inside_verified'] ==
                            x['kernel_group_mismatches'] == x['kernel_shared_calls'] ==
                            x['kernel_shared_verified'] == x['kernel_shared_mismatches'] == 0,
                            '正式轮混入验证、共享准备或差异')
                    require(x['kernel_quiet_high_seconds'] <= x['kernel_rules_wall_seconds'],
                            '同进程计时包含关系错误')
                    require(all(isinstance(x[k],(int,float)) and math.isfinite(x[k]) and x[k]>=0
                                for k in (*PHASES,'face_complete_elapsed','vertex_arrival_elapsed')),
                            '无效阶段计时')
                for k in (*QUIET_COUNTS, 'kernel_rules_calls', 'kernel_rules_parallel_calls',
                          'kernel_rules_face_trials', 'kernel_rules_point_trials', 'kernel_rules_mappings'):
                    require(sum(x[k] for x in rows) == r[k], '全进程计数与汇总不符')
                for k in ('kernel_quiet_high_seconds','kernel_quiet_parallel_seconds'):
                    require(math.isclose(max(x[k] for x in rows),r[k],abs_tol=1e-9), '计时最大值与汇总不符')
                require(math.isfinite(r['core_seconds']) and r['core_seconds'] > 0, '无效核心时间')
                times.append(r['core_seconds']); runs.append(rows); report['formal'] += 1
            retained[mode] = runs
            scale['strategies'][mode] = dict(seconds=times, median_seconds=median(times),
                compute_critical=[max(rows,key=lambda x:x['compute_seconds'])['rank'] for rows in runs],
                arrival_critical=[max(rows,key=lambda x:x['vertex_arrival_elapsed'])['rank'] for rows in runs],
                quiet_counts={k:r[k] for k in QUIET_COUNTS})
        left,right = [scale['strategies'][m]['seconds'] for m in MODES]
        paired = [100*(a-b)/a for a,b in zip(left,right)]
        scale.update(work=reference_work, quality=reference_quality, identity=reference_identity,
                     paired_reduction_pct=paired, paired_median_pct=median(paired), wins=sum(x>0 for x in paired),
                     median_time_reduction_pct=100*(1-median(right)/median(left)),
                     all_rank_work_fields=list(WORK), all_rank_work_comparisons=5*n)
        published = next(x for x in csv.DictReader(read(batch/f'levels4/p{n}/ablation_summary.csv').splitlines())
                         if x['contribution'] == 'quiet_rules')
        require(math.isclose(float(published['paired_reduction_pct']),median(paired),abs_tol=1e-9) and
                int(published['paired_count'])==3 and int(published['paired_wins'])==scale['wins'] and
                published['quality_pass']==published['same_volume_fingerprint']=='True' and
                not published['quality_issues'], '发布汇总与独立复算不同')
        selected = sorted({x for s in scale['strategies'].values() for k in
                           ('compute_critical','arrival_critical') for x in s[k]})
        scale['matched_ranks'] = {}
        for rank in selected:
            samples = {m:[{k:rows[rank][k] for k in
                          ('face_complete_elapsed','vertex_arrival_elapsed',*PHASES,*QUIET_COUNTS)}
                          for rows in retained[m]] for m in MODES}
            scale['matched_ranks'][str(rank)] = dict(samples=samples,
                phase_medians={m:{k:median(x[k] for x in samples[m]) for k in PHASES} for m in MODES},
                paired_phase_median_pct={k:median(100*(a[k]-b[k])/a[k] for a,b in
                    zip(samples[MODES[0]],samples[MODES[1]])) for k in PHASES
                    if all(x[k]>0 for x in samples[MODES[0]])})
        scale['arrival_decomposition'] = {m:[dict(core_seconds=t,
            latest_vertex_arrival_seconds=max(x['vertex_arrival_elapsed'] for x in rows),
            after_latest_vertex_seconds=t-max(x['vertex_arrival_elapsed'] for x in rows))
            for t,rows in zip(scale['strategies'][m]['seconds'],retained[m])] for m in MODES}
        report['scales'][str(n)] = scale
    report['limits'] = ['完整逐单元质量和逐调用重放只在预热执行；正式轮核查保留计数与签名。',
                        'source revision 为 manual，哈希只证明两策略二进制/输入一致，不能证明确切源码提交。',
                        '三对正式重复不足以宣称统计显著；同规模比较不能代替固定工作量强扩展。',
                        '最晚顶点到达之后的区间包含其他工作，不是纯网络传输时间。']
    return report


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('batch',type=Path);p.add_argument('output',type=Path)
    args=p.parse_args()
    result=review(args.batch)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(f"复核通过：{result['runs']} 次运行，{result['formal']} 次正式；{len(result['scales'])} 个规模")
