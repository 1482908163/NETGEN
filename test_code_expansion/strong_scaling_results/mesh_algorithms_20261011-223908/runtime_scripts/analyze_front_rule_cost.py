"""Screen exclusive rule costs on each uninstrumented control's slow rank."""
import csv
import hashlib
import json
import math
import statistics as st
from pathlib import Path
from analyze_results import inspect,profile_path,write_csv
from front_rule_checks import FIELDS,STAGES,PREFIX,MODES,check_certificates

LABELS=dict(zip(STAGES,('调用准备','规则派发与回溯映射','边约束与候选坐标构造','自由区变换',
    '凸性与旧点检查','面扫描与几何派发','有共同点的三角形求交','无共同点的三角形求交',
    '候选构造与质量评价','候选选择与回滚','最终输出与诊断收尾')))

def analyze(root,ranks):
    root=Path(root);routes=('incidence_ordered','rules_totals','rules_stages')
    plans={r:json.loads((root/f'route_{r}/p{ranks}/plan.json').read_text()) for r in routes}
    keys=('algorithms','timings','partition_seeds','repeats','quality_warmup')
    if any(any(plans[r][k]!=plans[routes[0]][k] for k in keys) for r in routes[1:]):
        raise ValueError('front rule screen plan mismatch')
    plan=plans[routes[0]]
    if not plan['quality_warmup']:raise ValueError('front rule screen lacks quality warmup')
    out=root/f'p{ranks}'
    with (out/'route_comparisons.csv').open() as stream:comparisons=list(csv.DictReader(stream))
    pairs={(a,b) for a,b in zip(routes,routes[1:])}|{(routes[0],routes[2])}
    if any(c['validated']!='True' for c in comparisons if (c['control'],c['candidate']) in pairs):
        raise ValueError('front rule screen paired quality/work failed')
    records=[];sources=[]
    for algorithm in plan['algorithms']:
        for seed in plan['partition_seeds']:
            # These diagnostics must not change any quality detail.
            qualities=[]
            for route in routes:
                stem=f'{algorithm}_natural' if seed==-1 else f'{algorithm}_seed{seed}_natural'
                p=root/f'route_{route}/p{ranks}'/stem/'repeat_0/quality_summary.json'
                qualities.append(json.loads(p.read_text()))
                sources.append({'path':str(p.relative_to(root)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
            if any(q!=qualities[0] for q in qualities[1:]) or not qualities[0]['structural_pass']:
                raise ValueError('front rule screen full quality detail changed')
            for timing in plan['timings']:
                stem=f'{algorithm}_{timing}' if seed==-1 else f'{algorithm}_seed{seed}_{timing}'
                for repeat in range(1,plan['repeats']+1):
                    runs={};ledgers={}
                    for route in routes:
                        directory=root/f'route_{route}/p{ranks}'/stem/f'repeat_{repeat}'
                        if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():
                            raise ValueError('front rule screen incomplete run')
                        ledger=[];runs[route],_=inspect(profile_path(directory),rule_sink=ledger)
                        run=runs[route]
                        if (run['ranks'],run['algorithm'],run['partition_seed'],run['timing'],run['repeat'])!=(ranks,algorithm,seed,timing,repeat):
                            raise ValueError('front rule screen run identity mismatch')
                        if route!=routes[0]:
                            if run.get('volume_front_rules')!=MODES[0 if route=='rules_totals' else 1]:
                                raise ValueError('front rule screen formal route mode mismatch')
                            p=directory/'rule_cost_certificate.csv'
                            with p.open() as stream:cert=list(csv.DictReader(stream))
                            check_certificates(cert,run,repeat,ranks,run['volume_front_rules'])
                            # Certificate also preserves every rank's compute and
                            # native front time; verify both against the raw run.
                            by_rank={int(c['rank']):c for c in cert}
                            for row in ledger:
                                c=by_rank[row['rank']]
                                for f in ('compute_seconds','kernel_front_seconds','front_distance_calls','front_distance_rules','front_distance_candidates'):
                                    if float(c[f])!=row[f]:raise ValueError('front rule screen certificate timing/input mismatch')
                            ledgers[route]={row['rank']:row for row in ledger}
                            sources.append({'path':str(p.relative_to(root)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
                    if len({(r['source_revision'],r['binary_sha256']) for r in runs.values()})!=1:
                        raise ValueError('front rule screen changed binary/source')
                    base=runs[routes[0]];coarse_run=runs[routes[1]];detailed_run=runs[routes[2]]
                    if coarse_run[PREFIX+'work_signature']!=detailed_run[PREFIX+'work_signature']:
                        raise ValueError('front rule screen per-rank work changed')
                    rank=base['slowest_compute_rank'];coarse=ledgers[routes[1]][rank];detailed=ledgers[routes[2]][rank]
                    a=coarse[PREFIX+'total_seconds'];b=detailed[PREFIX+'total_seconds']
                    if a<=0 or b<=0:raise ValueError('front rule screen chosen rank has no rule work')
                    record=dict(ranks=ranks,algorithm=algorithm,seed=seed,timing=timing,repeat=repeat,
                        rank=rank,source_revision=base['source_revision'],binary_sha256=base['binary_sha256'],
                        control_compute_seconds=base['slowest_compute_seconds'],
                        totals_compute_seconds=coarse['compute_seconds'],stages_compute_seconds=detailed['compute_seconds'],
                        totals_rules_seconds=a,stages_rules_seconds=b,
                        control_core_seconds=base['core_seconds'],totals_core_seconds=coarse_run['core_seconds'],stages_core_seconds=detailed_run['core_seconds'],
                        stage_timing_rules_overhead_percent=100*(b/a-1),
                        counter_core_overhead_percent=100*(coarse_run['core_seconds']/base['core_seconds']-1),
                        total_diagnostic_core_overhead_percent=100*(detailed_run['core_seconds']/base['core_seconds']-1))
                    record.update({PREFIX+f:detailed[PREFIX+f] for f in FIELDS})
                    records.append(record)
    summaries=[]
    for algorithm in plan['algorithms']:
        for seed in plan['partition_seeds']:
            for timing in plan['timings']:
                group=[r for r in records if (r['algorithm'],r['seed'],r['timing'])==(algorithm,seed,timing)]
                for stage in STAGES:
                    summaries.append(dict(ranks=ranks,algorithm=algorithm,seed=seed,timing=timing,stage=stage,label=LABELS[stage],
                        repetitions=len(group),selected_ranks=json.dumps([r['rank'] for r in group]),
                        seconds_median=st.median(r[PREFIX+stage] for r in group),
                        fraction_rules_percent_median=st.median(100*r[PREFIX+stage]/r['stages_rules_seconds'] for r in group),
                        fraction_control_compute_percent_median=st.median(100*r[PREFIX+stage]/r['control_compute_seconds'] for r in group),
                        stage_timing_rules_overhead_percent_median=st.median(r['stage_timing_rules_overhead_percent'] for r in group),
                        counter_core_overhead_percent_median=st.median(r['counter_core_overhead_percent'] for r in group),
                        total_diagnostic_core_overhead_percent_median=st.median(r['total_diagnostic_core_overhead_percent'] for r in group)))
    write_csv(out/'rule_cost_fixed_rank_pairs.csv',records)
    write_csv(out/'rule_cost_stage_summary.csv',summaries)
    (out/'rule_cost_sources.json').write_text(json.dumps(sources,indent=2)+'\n')
    lines=['完整规则成本筛选：诊断结果，不是算法加速。',
        '固定每次无新增诊断控制的计算最慢进程；另外两路线读取同一进程。',
        '十一桶互斥并覆盖完整规则调用；参考重放时间另列，不进入正式运行。',
        '三角形在实际凸子集内判断共同点；四边形内部三角形逐一归桶。',
        '面扫描桶仍含包围盒、编号转换、四边形直接分支和几何派发。',
        '评价桶含临时点/面/单元构造、方向与新点合法性、坏度和边长；不解释成纯坏度耗时。',
        '拒绝计数可能重叠，不能相加为互斥候选集合。诊断开销已实测，不从计时扣除。',
        '成本占比用于选择研究方向；不能当作未经测量的可实现加速，也不能相加不同进程的最大值。']
    for algorithm in plan['algorithms']:
        for seed in plan['partition_seeds']:
            for timing in plan['timings']:
                group=[r for r in summaries if (r['algorithm'],r['seed'],r['timing'])==(algorithm,seed,timing)]
                leaders=sorted(group,key=lambda r:-r['seconds_median'])[:3]
                lines.append(f'{algorithm} seed={seed} {timing}：'+ '；'.join(f"{r['label']} {1000*r['seconds_median']:.3f} ms，占规则 {r['fraction_rules_percent_median']:.2f}%" for r in leaders))
                lines.append(f"细分计时相对总计的同进程规则耗时变化中位数 {group[0]['stage_timing_rules_overhead_percent_median']:+.2f}%；全部诊断核心耗时变化 {group[0]['total_diagnostic_core_overhead_percent_median']:+.2f}%。")
    (out/'RULE_COST_SUMMARY.txt').write_text('\n'.join(lines)+'\n')
    return records,summaries

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--ranks',type=int,required=True)
    args=p.parse_args()
    try:analyze(args.root,args.ranks)
    except (OSError,ValueError,KeyError,TypeError) as error:p.exit(1,str(error)+'\n')
