#!/usr/bin/env python3
"""复算候选反向映射配对统计；可核验上传目录的完整保留证据。"""
import argparse,csv,hashlib,json,math,statistics
from pathlib import Path

HERE=Path(__file__).resolve().parent
FIELDS='calls candidates queried_candidates queries baseline_comparisons linear_comparisons direct_queries table_builds table_writes table_clears table_bytes_peak verified mismatches reference_seconds'.split()
WORK=['front_lookup_'+f for f in FIELDS[:5]]+['front_distance_calls','front_distance_rules','front_distance_candidates']

def read_csv(p):
    with p.open(newline='') as stream:return list(csv.DictReader(stream))

def reduction(a,b):return 100*(1-b/a)

def summarize(pairs):
    groups=[]
    for p in (128,256,512):
        for seed in (-1,17,41):
            g=[r for r in pairs if (r['ranks'],r['seed'])==(p,seed)]
            if sorted(r['repeat'] for r in g)!=[1,2,3]:raise ValueError('配对矩阵不完整')
            row=dict(ranks=p,seed=seed)
            for metric in ('core','compute','front','rules'):
                values=[reduction(r['control_'+metric+'_seconds'],r['candidate_'+metric+'_seconds']) for r in g]
                for r,v in zip(g,values):
                    if not math.isclose(v,r[metric+'_reduction_percent'],abs_tol=1e-10):raise ValueError('配对变化值不符')
                row[metric+'_reduction_percent_median']=statistics.median(values)
                row[metric+'_wins']=sum(v>0 for v in values)
            groups.append(row)
    scales=[]
    for p in (128,256,512,None):
        g=[r for r in pairs if p is None or r['ranks']==p];row=dict(ranks=p or 'all',pairs=len(g))
        for metric in ('core','compute','front','rules'):
            values=[r[metric+'_reduction_percent'] for r in g]
            row[metric+'_reduction_percent_median']=statistics.median(values);row[metric+'_wins']=sum(v>0 for v in values)
        scales.append(row)
    return groups,scales

def verify(root,evidence,manifest):
    # This is a retained-evidence audit, not a rerun of mesh generation or raw-profile analysis.
    for item in manifest['files']:
        d=(root/item['path']).read_bytes()
        actual=hashlib.sha1(b'blob '+str(len(d)).encode()+b'\0'+d).hexdigest()
        if actual!=item['git_blob_sha']:raise ValueError('来源文件变化：'+item['path'])
    identity=set();calls=0;rank_pairs=0;formal_rank_pairs=0;quality_pairs=0;starts=0
    for p in (128,256,512):
        for route in ('lookup_linear','lookup_inverse'):
            with (root/f'route_{route}/p{p}/run_status.tsv').open(newline='') as s:status=list(csv.DictReader(s,delimiter='\t'))
            if len(status)!=12 or any(r['exit_code']!='0' for r in status):raise ValueError('启动状态不完整')
            starts+=len(status)
        for seed in (-1,17,41):
            stem='sparse'+('' if seed==-1 else f'_seed{seed}')+'_natural'
            qs=[json.loads((root/f'route_{r}/p{p}/{stem}/repeat_0/quality_summary.json').read_text()) for r in ('lookup_linear','lookup_inverse')]
            if qs[0]!=qs[1] or not qs[0]['structural_pass'] or len(qs[0]['per_rank'])!=p:raise ValueError('完整质量记录不一致')
            quality_pairs+=1
            for repeat in range(4):
                routes=[]
                for mode,route in enumerate(('lookup_linear','lookup_inverse')):
                    rows=read_csv(root/f'route_{route}/p{p}/{stem}/repeat_{repeat}/front_lookup_certificate.csv');rows.sort(key=lambda r:int(r['rank']))
                    if [int(r['rank']) for r in rows]!=list(range(p)):raise ValueError('进程证书不完整')
                    expected='linear_face_lookup_v1' if not mode else 'inverse_face_lookup_replay_v1' if not repeat else 'inverse_face_lookup_v1'
                    for row in rows:
                        if (int(row['ranks']),int(row['repeat']),int(row['partition_seed']),row['algorithm'],row['timing'],row['volume_front_lookup'])!=(p,repeat,seed,'sparse','natural',expected):raise ValueError('证书身份不符')
                        identity.add((row['source_revision'],row['binary_sha256']))
                        v={f:float(row['front_lookup_'+f]) for f in FIELDS}
                        if any(not math.isfinite(n) or n<0 or ('seconds' not in f and (not n.is_integer() or n>2**53)) for f,n in v.items()):raise ValueError('计数无效')
                        if v['mismatches'] or v['verified']!=(v['calls'] if mode and not repeat else 0):raise ValueError('完整重放覆盖不符')
                        if (not mode or repeat) and v['reference_seconds']:raise ValueError('正式计时混入参考重放')
                        if v['calls']!=float(row['front_distance_calls']) or v['candidates']!=float(row['front_distance_candidates']):raise ValueError('独立计数不符')
                        if v['queried_candidates']>v['candidates'] or v['queries']<v['queried_candidates'] or v['direct_queries']>v['queries'] or v['linear_comparisons']>v['baseline_comparisons'] or v['table_clears']>v['table_writes']:raise ValueError('工作守恒不符')
                        if mode:
                            if v['table_builds']!=v['queried_candidates'] or v['direct_queries']!=v['queries'] or v['linear_comparisons']:raise ValueError('反向查找未完整执行')
                            if not repeat:calls+=v['verified']
                        elif v['linear_comparisons']!=v['baseline_comparisons'] or any(v[f] for f in FIELDS[6:11]):raise ValueError('线性对照改变实现')
                    routes.append(rows)
                a,b=routes
                if any(float(x[k])!=float(y[k]) for x,y in zip(a,b) for k in WORK):raise ValueError('逐进程工作漂移')
                rank_pairs+=p
                if not repeat:continue
                formal_rank_pairs+=p
                rank=max(range(p),key=lambda i:float(a[i]['compute_seconds']))
                pair=next(r for r in evidence['pairs'] if (r['ranks'],r['seed'],r['repeat'])==(p,seed,repeat))
                if rank!=pair['rank']:raise ValueError('固定进程选择不符')
                for label,row in (('control',a[rank]),('candidate',b[rank])):
                    for metric,col in (('core','core_seconds'),('compute','compute_seconds'),('front','kernel_front_seconds'),('rules','front_distance_apply_seconds')):
                        if float(row[col])!=pair[label+'_'+metric+'_seconds']:raise ValueError('证书与配对耗时不符')
    expected=(evidence['successful_runs'],evidence['quality_pairs'],evidence['rank_pairs'],evidence['formal_rank_pairs'],evidence['warmup_full_replays'])
    if (starts,quality_pairs,rank_pairs,formal_rank_pairs,calls)!=expected:raise ValueError('完整覆盖计数不符')
    if len(identity)!=1 or ' '.join(next(iter(identity)))!=evidence['identity']:raise ValueError('源码或二进制混用')
    return len(manifest['files'])

def write_csv(p,rows):
    with p.open('w',newline='') as s:
        w=csv.DictWriter(s,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-source-root',type=Path)
    parser.add_argument('--output',type=Path,default=Path('front_lookup_analysis'))
    args=parser.parse_args()
    e=json.loads((HERE/'20261012_front_lookup_evidence.json').read_text())
    groups,scales=summarize(e['pairs'])
    if groups!=e['groups']:raise ValueError('复算与归档组摘要不一致')
    verified=None
    if args.verify_source_root:
        m=json.loads((HERE/'20261012_front_lookup_source_manifest.json').read_text());verified=verify(args.verify_source_root,e,m)
    args.output.mkdir(parents=True,exist_ok=True)
    write_csv(args.output/'pairs.csv',e['pairs']);write_csv(args.output/'groups.csv',groups);write_csv(args.output/'scales.csv',scales)
    (args.output/'summary.json').write_text(json.dumps(dict(source=e['source_result_commit'],batch=e['batch'],groups=groups,scales=scales,verified_source_files=verified),indent=2)+'\n')
    print(f'已复算 {len(e["pairs"])} 个固定进程配对；'+(f'来源核验 {verified} 个文件。' if verified else '未在本次调用核验原目录。'))
if __name__=='__main__':main()
