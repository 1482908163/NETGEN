"""Compare complete cost on each linear control's original critical rank."""
import csv
import hashlib
import json
import statistics as st
from pathlib import Path
from analyze_results import inspect,profile_path,write_csv
from front_lookup_checks import FIELDS,PREFIX,MODES,check_certificates

def analyze(root,ranks):
    root=Path(root);routes=('lookup_linear','lookup_inverse');out=root/f'p{ranks}'
    plans=[json.loads((root/f'route_{r}/p{ranks}/plan.json').read_text()) for r in routes]
    if plans[0]!=plans[1] or not plans[0]['quality_warmup']:raise ValueError('front lookup plan mismatch')
    plan=plans[0]
    with (out/'route_comparisons.csv').open() as stream:
        comparisons=[c for c in csv.DictReader(stream) if (c['control'],c['candidate'])==routes]
    expected={(a,str(s),t) for a in plan['algorithms'] for s in plan['partition_seeds'] for t in plan['timings']}
    if len(comparisons)!=len(expected) or {(c['algorithm'],c['seed'],c['timing']) for c in comparisons}!=expected or any(c['validated']!='True' for c in comparisons):
        raise ValueError('front lookup paired gates incomplete')
    records=[];sources=[]
    def source(p):sources.append(dict(path=str(p.relative_to(root)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    def reduction(a,b):return 100*(1-b/a) if a>0 else None
    for algorithm in plan['algorithms']:
        for seed in plan['partition_seeds']:
            stem=algorithm if seed==-1 else f'{algorithm}_seed{seed}'
            quality=[]
            for route in routes:
                p=root/f'route_{route}/p{ranks}'/(stem+'_natural')/'repeat_0/quality_summary.json'
                quality.append(json.loads(p.read_text()));source(p)
            if quality[0]!=quality[1] or not quality[0]['structural_pass']:raise ValueError('front lookup full quality changed')
            for timing in plan['timings']:
                for repeat in range(1,plan['repeats']+1):
                    runs=[];ledgers=[]
                    for mode,route in enumerate(routes):
                        directory=root/f'route_{route}/p{ranks}'/(stem+'_'+timing)/f'repeat_{repeat}'
                        if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():raise ValueError('front lookup run incomplete')
                        ledger=[];run,_=inspect(profile_path(directory),lookup_sink=ledger)
                        if (run['ranks'],run['algorithm'],run['partition_seed'],run['timing'],run['repeat'],run.get('volume_front_lookup'))!=(ranks,algorithm,seed,timing,repeat,MODES[mode]):
                            raise ValueError('front lookup formal identity mismatch')
                        p=directory/'front_lookup_certificate.csv'
                        with p.open() as stream:cert=list(csv.DictReader(stream))
                        check_certificates(cert,run,repeat,ranks,MODES[mode])
                        cert.sort(key=lambda c:int(c['rank']))
                        for c,row in zip(cert,ledger):
                            for key,value in row.items():
                                if isinstance(value,(int,float)):
                                    if float(c[key])!=value:raise ValueError('front lookup certificate timing/work mismatch')
                                elif c[key]!=value:raise ValueError('front lookup certificate identity mismatch')
                        runs.append(run);ledgers.append(ledger);source(p)
                    a,b=runs
                    if (a['source_revision'],a['binary_sha256'])!=(b['source_revision'],b['binary_sha256']):raise ValueError('front lookup source/binary mismatch')
                    for key in (PREFIX+'work_signature','front_distance_work_signature','incidence_work_signature'):
                        if a[key]!=b[key]:raise ValueError('front lookup per-rank workload changed')
                    rank=a['slowest_compute_rank'];x,y=ledgers[0][rank],ledgers[1][rank]
                    record=dict(ranks=ranks,algorithm=algorithm,seed=seed,timing=timing,repeat=repeat,rank=rank,
                        source_revision=a['source_revision'],binary_sha256=a['binary_sha256'],
                        control_core_seconds=a['core_seconds'],candidate_core_seconds=b['core_seconds'],
                        control_compute_seconds=x['compute_seconds'],candidate_compute_seconds=y['compute_seconds'],
                        control_front_seconds=x['kernel_front_seconds'],candidate_front_seconds=y['kernel_front_seconds'],
                        control_rules_seconds=x['front_distance_apply_seconds'],candidate_rules_seconds=y['front_distance_apply_seconds'],
                        core_reduction_percent=reduction(a['core_seconds'],b['core_seconds']),
                        compute_reduction_percent=reduction(x['compute_seconds'],y['compute_seconds']),
                        front_reduction_percent=reduction(x['kernel_front_seconds'],y['kernel_front_seconds']),
                        rules_reduction_percent=reduction(x['front_distance_apply_seconds'],y['front_distance_apply_seconds']))
                    for label,row in (('control',x),('candidate',y)):
                        record.update({label+'_'+PREFIX+f:row[PREFIX+f] for f in FIELDS})
                    records.append(record)
    summaries=[]
    for algorithm in plan['algorithms']:
        for seed in plan['partition_seeds']:
            for timing in plan['timings']:
                g=[r for r in records if (r['algorithm'],r['seed'],r['timing'])==(algorithm,seed,timing)]
                result=dict(ranks=ranks,algorithm=algorithm,seed=seed,timing=timing,repetitions=len(g),selected_ranks=json.dumps([r['rank'] for r in g]))
                for metric in ('core','compute','front','rules'):
                    values=[r[metric+'_reduction_percent'] for r in g if r[metric+'_reduction_percent'] is not None]
                    result.update({metric+'_pairs_with_work':len(values),metric+'_reduction_percent_median':st.median(values) if values else None,metric+'_wins':sum(v>0 for v in values)})
                for label in ('control','candidate'):
                    for metric in ('core','compute','front','rules'):
                        result[label+'_'+metric+'_seconds_median']=st.median(r[label+'_'+metric+'_seconds'] for r in g)
                summaries.append(result)
    write_csv(out/'front_lookup_fixed_rank_pairs.csv',records)
    write_csv(out/'front_lookup_summary.csv',summaries)
    (out/'front_lookup_sources.json').write_text(json.dumps(sources,indent=2)+'\n')
    lines=['候选反向映射：固定每次原线性路线计算最慢进程，对照同一编号。',
           '完整规则与前沿计时包含建表、清理、分配释放和工作计数；正式运行没有细分桶计时和参考重放。',
           '比较次数减少不是加速结论；百分数为配对中位数，正值表示更快。每组仅三次正式重复。']
    for s in summaries:
        rules=s['rules_reduction_percent_median']
        lines.append(f"{s['algorithm']} seed={s['seed']} {s['timing']}：核心 {s['core_reduction_percent_median']:+.2f}%，同进程计算 {s['compute_reduction_percent_median']:+.2f}%，规则 "+(f'{rules:+.2f}%' if rules is not None else '无规则工作')+f"；核心胜出 {s['core_wins']}/{s['repetitions']}。")
    (out/'FRONT_LOOKUP_SUMMARY.txt').write_text('\n'.join(lines)+'\n')
    return records,summaries

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--ranks',type=int,required=True);a=p.parse_args()
    try:analyze(a.root,a.ranks)
    except (OSError,ValueError,KeyError,TypeError) as error:p.exit(1,str(error)+'\n')
