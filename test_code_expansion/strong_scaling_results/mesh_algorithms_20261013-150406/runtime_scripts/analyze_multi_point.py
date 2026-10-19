"""Independent components plus combination, full core and same control rank."""
import csv,hashlib,json,statistics as st
from pathlib import Path
from analyze_results import inspect,profile_path,write_csv
from multi_point_checks import FIELDS
ROUTES={'multi_control':0,'multi_front':1,'multi_pack':2,'multi_unpack':4,'multi_all':7}

def analyze(root,ranks,selected):
    root=Path(root);out=root/f'p{ranks}';selected=[r for r in selected if r in ROUTES]
    if 'multi_control' not in selected:raise ValueError('multi point control missing')
    plans=[json.loads((root/f'route_{r}/p{ranks}/plan.json').read_text()) for r in selected]
    if any(p!=plans[0] for p in plans[1:]) or plans[0]['timings']!=['natural'] or not plans[0]['quality_warmup']:raise ValueError('multi point plans differ')
    plan=plans[0];sources=[];pairs=[]
    def source(path):sources.append(dict(path=str(path.relative_to(root)),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    def load(route,algorithm,seed,repeat):
        stem=algorithm+('' if seed==-1 else f'_seed{seed}')+'_natural';directory=root/f'route_{route}/p{ranks}/{stem}/repeat_{repeat}'
        if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():raise ValueError('multi point run incomplete')
        ledger=[];run,_=inspect(profile_path(directory),multi_sink=ledger)
        if (run['ranks'],run['algorithm'],run['partition_seed'],run['repeat'],run['multi_point_mask'])!=(ranks,algorithm,seed,repeat,ROUTES[route]):raise ValueError('multi point route identity')
        path=directory/'multi_point_certificate.csv'
        with path.open() as s:cert=list(csv.DictReader(s))
        cert.sort(key=lambda r:int(r['rank']))
        if [int(r['rank']) for r in cert]!=list(range(ranks)):raise ValueError('multi point rank coverage')
        for a,b in zip(cert,ledger):
            for k,v in b.items():
                if isinstance(v,(int,float)):
                    if float(a[k])!=v:raise ValueError('multi point certificate timing/work mismatch')
                elif a[k]!=v:raise ValueError('multi point certificate identity mismatch')
        source(path);return run,ledger,directory
    def compare(a,b):
        if (a['source_revision'],a['binary_sha256'])!=(b['source_revision'],b['binary_sha256']):raise ValueError('multi point source/binary differs')
        for key in ('multi_point_work_signature','ghost_transport_work_signature','ghost_payload_coverage_signature','front_workspace_work_signature','incidence_work_signature','front_distance_work_signature'):
            if a[key]!=b[key]:raise ValueError('multi point per-rank work differs: '+key)
    for algorithm in plan['algorithms']:
        for seed in plan['partition_seeds']:
            warm={r:load(r,algorithm,seed,0) for r in selected};control=warm['multi_control']
            qpath=control[2]/'quality_summary.json';baseline=json.loads(qpath.read_text());source(qpath)
            if not baseline['structural_pass']:raise ValueError('multi point control quality failed')
            for route in selected:
                compare(control[0],warm[route][0]);q=warm[route][2]/'quality_summary.json';source(q)
                if json.loads(q.read_text())!=baseline:raise ValueError('multi point full quality changed')
            for repeat in range(1,plan['repeats']+1):
                a,x,_=load('multi_control',algorithm,seed,repeat)
                # Full core origin/end on each rank, including maintenance,
                # packing, insertion and release. No stage-maximum sums.
                rank=max(range(ranks),key=lambda i:x[i]['local_core_seconds'])
                for route in selected:
                    if route=='multi_control':continue
                    b,y,_=load(route,algorithm,seed,repeat);compare(a,b)
                    r=dict(ranks=ranks,algorithm=algorithm,seed=seed,repeat=repeat,route=route,mask=ROUTES[route],control_core_slowest_rank=rank,
                        core_reduction_percent=100*(1-b['core_seconds']/a['core_seconds']),
                        same_rank_core_reduction_percent=100*(1-y[rank]['local_core_seconds']/x[rank]['local_core_seconds']),
                        control_core_seconds=a['core_seconds'],candidate_core_seconds=b['core_seconds'],
                        control_rank_core_seconds=x[rank]['local_core_seconds'],candidate_rank_core_seconds=y[rank]['local_core_seconds'])
                    for side,row in (('control',x[rank]),('candidate',y[rank])):
                        r.update({side+'_'+k:row[k] for k in FIELDS+('local_volume_mesh_seconds','volume_exchange_full_seconds')})
                    pairs.append(r)
    summaries=[]
    for route in selected:
        if route=='multi_control':continue
        for algorithm in plan['algorithms']:
            for seed in plan['partition_seeds']:
                g=[r for r in pairs if (r['route'],r['algorithm'],r['seed'])==(route,algorithm,seed)]
                summaries.append(dict(ranks=ranks,route=route,algorithm=algorithm,seed=seed,pairs=len(g),core_wins=sum(r['core_reduction_percent']>0 for r in g),
                    core_reduction_percent_median=st.median(r['core_reduction_percent'] for r in g),same_rank_core_reduction_percent_median=st.median(r['same_rank_core_reduction_percent'] for r in g)))
    write_csv(out/'multi_point_pairs.csv',pairs);write_csv(out/'multi_point_summary.csv',summaries)
    (out/'multi_point_sources.json').write_text(json.dumps(sources,indent=2)+'\n')
    lines=['多点优化：原控制、单项与组合；核心总时间包含索引维护、打包、装配和释放。','固定原控制完整核心最慢进程；预热完整参考核对不进入正式运行。']
    for r in summaries:lines.append(f"{r['route']} seed={r['seed']}：核心下降 {r['core_reduction_percent_median']:+.2f}%，胜出 {r['core_wins']}/{r['pairs']}，同进程核心下降 {r['same_rank_core_reduction_percent_median']:+.2f}%。")
    (out/'MULTI_POINT_SUMMARY.txt').write_text('\n'.join(lines)+'\n');return pairs,summaries
