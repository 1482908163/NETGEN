"""Coherent, fixed-control-rank comparison of real dictionary MPI transport."""
import csv,hashlib,json,statistics as st
from pathlib import Path
from analyze_results import inspect,profile_path,write_csv
from ghost_transport_checks import PREFIX,FIELDS,MODES

def analyze(root,ranks):
    root=Path(root);routes=('ghost_literal','ghost_dictionary');out=root/f'p{ranks}'
    plans=[json.loads((root/f'route_{r}/p{ranks}/plan.json').read_text()) for r in routes]
    if plans[0]!=plans[1] or not plans[0]['quality_warmup']:raise ValueError('transport plan mismatch')
    plan=plans[0];records=[];sources=[]
    def source(path):sources.append(dict(path=str(path.relative_to(root)),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    def load(route,algorithm,seed,repeat):
        stem=algorithm+('' if seed==-1 else f'_seed{seed}')+'_natural'
        directory=root/f'route_{route}/p{ranks}/{stem}/repeat_{repeat}'
        if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():raise ValueError('transport run incomplete')
        ledger=[];run,_=inspect(profile_path(directory),transport_sink=ledger)
        expected=MODES[0 if route=='ghost_literal' else 2 if repeat==0 else 1]
        if (run['ghost_transport'],run['ranks'],run['algorithm'],run['partition_seed'],run['repeat'])!=(expected,ranks,algorithm,seed,repeat):raise ValueError('transport run identity mismatch')
        path=directory/'ghost_transport_certificate.csv'
        with path.open() as stream:cert=list(csv.DictReader(stream))
        cert.sort(key=lambda r:int(r['rank']))
        if [int(r['rank']) for r in cert]!=list(range(ranks)):raise ValueError('transport certificate ranks incomplete')
        for x,y in zip(cert,ledger):
            for k,v in y.items():
                if isinstance(v,(float,int)):
                    if float(x[k])!=v:raise ValueError('transport certificate timing/work mismatch')
                elif x[k]!=v:raise ValueError('transport certificate identity mismatch')
        source(path)
        return run,ledger,directory
    def compare(a,b):
        if (a['source_revision'],a['binary_sha256'])!=(b['source_revision'],b['binary_sha256']):raise ValueError('transport source/binary mismatch')
        for key in ('ghost_transport_work_signature','ghost_payload_coverage_signature','front_workspace_work_signature','incidence_work_signature','front_distance_work_signature'):
            if a[key]!=b[key]:raise ValueError('transport per-rank work changed')
    for algorithm in plan['algorithms']:
        for seed in plan['partition_seeds']:
            warm=[load(route,algorithm,seed,0) for route in routes];compare(warm[0][0],warm[1][0])
            quality=[]
            for run,_,directory in warm:
                path=directory/'quality_summary.json';quality.append(json.loads(path.read_text()));source(path)
            if quality[0]!=quality[1] or not quality[0]['structural_pass']:raise ValueError('transport full quality changed')
            if not warm[1][0][PREFIX+'dictionary_send_peers']:raise ValueError('transport dictionary never exercised')
            for repeat in range(1,plan['repeats']+1):
                controls=load(routes[0],algorithm,seed,repeat);candidates=load(routes[1],algorithm,seed,repeat)
                a,x,_=controls;b,y,_=candidates;compare(a,b)
                rank=max(range(ranks),key=lambda i:x[i][PREFIX+'full_seconds'])
                # Compare one rank's COMPLETE exchange, never sum stage maxima.
                baseline=x[rank][PREFIX+'full_seconds'];candidate=y[rank][PREFIX+'full_seconds']
                raw=a[PREFIX+'payload_send_bytes'];wire=b[PREFIX+'payload_send_bytes']+b[PREFIX+'metadata_send_bytes']
                r=dict(ranks=ranks,algorithm=algorithm,seed=seed,repeat=repeat,rank=rank,source_revision=a['source_revision'],binary_sha256=a['binary_sha256'],
                    control_core_seconds=a['core_seconds'],candidate_core_seconds=b['core_seconds'],core_reduction_percent=100*(1-b['core_seconds']/a['core_seconds']),
                    control_full_seconds=baseline,candidate_full_seconds=candidate,full_reduction_percent=100*(1-candidate/baseline) if baseline else None,
                    control_payload_bytes=raw,candidate_payload_and_metadata_bytes=wire,wire_reduction_percent=100*(1-wire/raw) if raw else None)
                for label,row in (('control',x[rank]),('candidate',y[rank])):
                    r.update({label+'_'+PREFIX+f:row[PREFIX+f] for f in FIELDS})
                records.append(r)
    summaries=[]
    for algorithm in plan['algorithms']:
        for seed in plan['partition_seeds']:
            g=[r for r in records if r['algorithm']==algorithm and r['seed']==seed]
            summaries.append(dict(ranks=ranks,algorithm=algorithm,seed=seed,repeats=len(g),
                core_reduction_percent_median=st.median(r['core_reduction_percent'] for r in g),core_wins=sum(r['core_reduction_percent']>0 for r in g),
                full_reduction_percent_median=st.median(r['full_reduction_percent'] for r in g if r['full_reduction_percent'] is not None),
                full_wins=sum(r['full_reduction_percent'] is not None and r['full_reduction_percent']>0 for r in g),
                wire_reduction_percent_median=st.median(r['wire_reduction_percent'] for r in g if r['wire_reduction_percent'] is not None)))
    write_csv(out/'ghost_transport_pairs.csv',records);write_csv(out/'ghost_transport_summary.csv',summaries)
    (out/'ghost_transport_sources.json').write_text(json.dumps(sources,indent=2)+'\n')
    lines=['真实幽灵字典传输：完整交换成本包含原打包/数量交换、字典准备/元数据/传输/解码、网格插入与最终载荷释放。',
           '固定原路线整段交换最慢进程，比较同编号进程；预热参考通信不进入正式重复。',
           '字节数含字典新增头部，未加双方共同的原数量消息；字节下降与时间下降分别报告，不能推算纯网络加速。']
    for s in summaries:lines.append(f"{s['algorithm']} seed={s['seed']}：核心 {s['core_reduction_percent_median']:+.2f}%，同进程整段交换 {s['full_reduction_percent_median']:+.2f}%，载荷加新增头部下降 {s['wire_reduction_percent_median']:.2f}%；核心胜出 {s['core_wins']}/{s['repeats']}。")
    (out/'GHOST_TRANSPORT_SUMMARY.txt').write_text('\n'.join(lines)+'\n')
    return records,summaries
