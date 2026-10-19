"""Warmup-only dictionary feasibility; never claim transport acceleration."""
import csv,hashlib,json
from pathlib import Path
from analyze_results import inspect,profile_path,write_csv
from ghost_codec_checks import FIELDS,PREFIX,SCHEMA
WORK=FIELDS[:12]

def analyze(root,ranks):
    root=Path(root);out=root/f'p{ranks}';records=[];sources=[];summaries=[];indexed={};absent=0
    routes=('workspace_temporary','workspace_reuse')
    plans=[json.loads((root/f'route_{route}/p{ranks}/plan.json').read_text()) for route in routes]
    if plans[0]!=plans[1] or not plans[0]['quality_warmup']:raise ValueError('ghost codec plan mismatch')
    plan=plans[0]
    for route in routes:
        for algorithm in plan['algorithms']:
            for seed in plan['partition_seeds']:
                stem=algorithm+('' if seed==-1 else f'_seed{seed}')+'_natural'
                directory=root/f'route_{route}/p{ranks}/{stem}/repeat_0'
                if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():raise ValueError('ghost codec warmup incomplete')
                ledger=[];run,_=inspect(profile_path(directory),codec_sink=ledger)
                if run.get('ghost_codec_audit') is None:absent+=1;continue
                if (run['ranks'],run['algorithm'],run['partition_seed'],run['repeat'],run['timing'],run['ghost_codec_audit'])!=(ranks,algorithm,seed,0,'natural',SCHEMA):raise ValueError('ghost codec warmup identity mismatch')
                path=directory/'ghost_codec_certificate.csv'
                with path.open() as s:cert=list(csv.DictReader(s))
                cert.sort(key=lambda r:int(r['rank']))
                if [int(r['rank']) for r in cert]!=list(range(ranks)):raise ValueError('ghost codec certificate ranks incomplete')
                for c,r in zip(cert,ledger):
                    for k,v in r.items():
                        if isinstance(v,(float,int)):
                            if float(c[k])!=v:raise ValueError('ghost codec certificate mismatch')
                        elif c[k]!=v:raise ValueError('ghost codec certificate identity mismatch')
                indexed[route,algorithm,seed]=ledger
                sources.append(dict(path=str(path.relative_to(root)),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
                records.extend(dict(route=route,**r) for r in ledger)
                summaries.append(dict(route=route,ranks=ranks,algorithm=algorithm,seed=seed,items=run[PREFIX+'items'],peers=run[PREFIX+'peers'],
                    unique_vertices=run[PREFIX+'unique_vertices'],raw_bytes=run[PREFIX+'raw_bytes'],dictionary_bytes=run[PREFIX+'dictionary_bytes'],
                    selected_bytes=run[PREFIX+'selected_bytes'],payload_reduction_percent=run['ghost_codec_payload_reduction_percent'],
                    profitable_peers=run[PREFIX+'profitable_peers'],bypass_peers=run[PREFIX+'bypass_peers'],
                    encode_seconds_rank_sum=run[PREFIX+'encode_seconds'],decode_seconds_rank_sum=run[PREFIX+'decode_seconds'],
                    lifecycle_seconds_rank_sum=run[PREFIX+'lifecycle_seconds'],max_rank_codec_lifecycle_seconds=max(r[PREFIX+'lifecycle_seconds'] for r in ledger),
                    max_peer_raw_bytes=run[PREFIX+'max_peer_raw_bytes'],max_peer_selected_bytes=run[PREFIX+'max_peer_selected_bytes']))
    expected=2*len(plan['partition_seeds'])*len(plan['algorithms'])
    if absent:
        if absent!=expected:raise ValueError('ghost codec audit coverage differs between routes/seeds')
        return [],[] # Old snapshots and explicitly disabled audits remain compatible.
    for algorithm in plan['algorithms']:
        for seed in plan['partition_seeds']:
            a,b=(indexed[route,algorithm,seed] for route in routes)
            for x,y in zip(a,b):
                if (x['source_revision'],x['binary_sha256'])!=(y['source_revision'],y['binary_sha256']):raise ValueError('ghost codec mixed source/binary')
                if any(x[PREFIX+f]!=y[PREFIX+f] for f in WORK):raise ValueError('ghost codec per-rank payload changed')
    write_csv(out/'ghost_codec_rank_audit.csv',records);write_csv(out/'ghost_codec_summary.csv',summaries)
    (out/'ghost_codec_sources.json').write_text(json.dumps(sources,indent=2)+'\n')
    lines=['幽灵顶点字典预热审计：全部发送字段往返核对；正式通信仍发送原载荷。',
           '候选字节数含字典头；不划算的邻居按原 MPI 载荷计入选择后字节数。',
           '字节削减仅是可行性上限，不含未来新协议计数/头部和网络成本；预热编解码时间不是正式加速基准。']
    for s in summaries:
        reduction=s['payload_reduction_percent']
        lines.append(f"{s['route']} {s['algorithm']} seed={s['seed']}："+(f"潜在载荷下降 {reduction:.2f}%" if reduction is not None else '无幽灵发送工作')+f"，可压缩邻居消息 {s['profitable_peers']}/{s['peers']}，最慢进程完整编解码诊断 {s['max_rank_codec_lifecycle_seconds']:.6f} 秒。")
    (out/'GHOST_CODEC_SUMMARY.txt').write_text('\n'.join(lines)+'\n')
    return records,summaries

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--ranks',type=int,required=True);a=p.parse_args()
    try:analyze(a.root,a.ranks)
    except (OSError,ValueError,KeyError,TypeError) as error:p.exit(1,str(error)+'\n')
