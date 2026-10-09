#!/usr/bin/env python3
"""复算前沿距离复用结果；可核对本轮全部 200 个保留来源文件。"""
import argparse,csv,hashlib,json,math,re,statistics as st
from pathlib import Path
BASE=Path(__file__).resolve().parent
PREFIX='20261010_front_distance'
DISTANCE=('calls','queries','hits','computed','fallback','rules','candidates','apply_seconds','verified','mismatches','reference_seconds','allocated_cells')
COUNTS=('calls','input_points','input_elements','evaluated_items','candidates','commit_attempts','applied','team_evaluations')
PHASES=('generation','repair','optimization')

def csv_rows(path):
    with Path(path).open(newline='') as stream:return list(csv.DictReader(stream))

def identity(path):
    m=re.search(r'route_(.*?)\/p(\d+)\/sparse(?:_seed(\d+))?_natural\/repeat_(\d+)\/',path)
    assert m,path
    return m[1],int(m[2]),int(m[3]) if m[3] else -1,int(m[4])

def verify_sources(root,data,manifest):
    certificates={};operations={};quality={};cert_rows=op_rows=0
    for record in manifest:
        path=root/record['path'];raw=path.read_bytes()
        digest=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        assert len(raw)==record['size'] and digest==record['sha'],record['path']
        if path.name=='algorithm_certificate.csv':
            route,p,seed,repeat=identity(record['path']);rows=csv_rows(path)
            assert len(rows)==p and [int(r['rank']) for r in rows]==list(range(p))
            vectors=[];totals=[0]*12
            for r in rows:
                assert int(r['ranks'])==p and int(r['repeat'])==repeat and r['source_revision']==data['algorithm_commit']
                mode='distance_original_v1' if route=='split_active' else 'distance_exact_replay_v1' if repeat==0 else 'distance_memo_v1'
                assert r['volume_front_distance']==mode
                v=[float(r['front_distance_'+f]) for f in DISTANCE]
                assert all(math.isfinite(x) and x>=0 for x in v)
                assert v[1]==v[2]+v[3] and v[4]<=v[3] and v[9]==0 and v[11]<=65536*v[0]
                assert v[8]==(v[0] if route=='front_distance' and repeat==0 else 0)
                if repeat:assert v[10]==0
                if route=='split_active':assert all(v[i]==0 for i in (2,4,8,10,11))
                active=[float(r['split_active_'+f]) for f in ('calls','edges','active','rejected','dispatched')]
                assert active[1]==active[2]+active[3] and active[2]==active[4] and float(r['split_active_mismatches'])==0
                assert float(r['split_active_verified'])==(active[1] if repeat==0 else 0)
                vector=[v[i] for i in (0,1,5,6)]+active
                for phase in PHASES:
                    f={k:float(r['repair_fixed_'+phase+'_'+k]) for k in ('calls','rounds','checks','stable_rounds','potential_skipped','skipped','verified','mismatches','reference_rounds')}
                    assert f['mismatches']==0 and f['stable_rounds']<=f['checks']<=f['rounds']
                    assert f['skipped']<=f['potential_skipped']
                    if repeat==0:assert f['verified']==f['stable_rounds'] and f['skipped']==0
                    else:assert f['verified']==f['reference_rounds']==0 and f['skipped']==f['potential_skipped']
                    vector.extend(f[k] for k in ('calls','rounds','checks','stable_rounds','potential_skipped','skipped','verified','reference_rounds'))
                vectors.append(vector);totals=[a+b for a,b in zip(totals,v)]
            certificates[route,p,seed,repeat]=vectors;cert_rows+=len(rows)
            if route=='front_distance':
                proof=next(x for x in data['certificate_pairs'] if (x['p'],x['seed'],x['repeat'])==(p,seed,repeat))
                for field,i in (('front_calls',0),('queries',1),('hits',2),('computed',3),('reference_verified',8),('allocated_cells',11)):
                    assert proof[field]==totals[i],field
        elif path.name=='operation_cost_profile.csv':
            route,p,seed,repeat=identity(record['path']);rows=csv_rows(path);values={}
            assert len(rows)==p*12
            for r in rows:
                assert int(r['ranks'])==p and int(r['repeat'])==repeat and int(r['partition_seed'])==seed
                assert r['source_revision']==data['algorithm_commit'] and r['coverage_match']=='True'
                key=(int(r['rank']),r['phase'],r['operation']);assert key not in values and 0<=key[0]<p
                counts=tuple(int(r[k]) for k in COUNTS)
                assert int(r['native_calls'])==counts[0] and counts[6]<=counts[5]<=counts[4]<=counts[3]
                total=float(r['total_seconds']);tolerance=1e-7+total*1e-6
                stages=[float(r[k+'_seconds']) for k in ('prepare','evaluate','order','commit','cleanup')]
                goals=[float(r[k+'_seconds']) for k in ('quality','conform','rest','worstcase','legal')]
                assert all(math.isfinite(x) and x>=0 for x in [total]+stages+goals)
                assert abs(sum(stages)-total)<=tolerance and abs(sum(goals)-total)<=tolerance
                values[key]=counts
            operations[route,p,seed,repeat]=values;op_rows+=len(rows)
        elif path.name=='quality_summary.json':
            q=json.loads(raw);assert q['structural_pass'] and not q['issues']
            route,p,seed,repeat=identity(record['path']);quality[route,p,seed]=q
    assert cert_rows==data['certificate_checks']['rows'] and op_rows==data['operation_checks']['rows']
    for (route,p,seed,repeat),v in certificates.items():
        if route=='split_active':assert v==certificates['front_distance',p,seed,repeat]
    for (route,p,seed,repeat),v in operations.items():
        if route=='split_active':assert v==operations['front_distance',p,seed,repeat]
    for (route,p,seed),v in quality.items():
        if route=='split_active':assert v==quality['front_distance',p,seed]

def write_csv(path,rows):
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,default=BASE/(PREFIX+'_evidence.json'))
    parser.add_argument('--out',type=Path,default=Path('front_distance_analysis'))
    parser.add_argument('--verify-source-root',type=Path,help='mesh_algorithms_20261009-232141 根目录')
    args=parser.parse_args();data=json.loads(args.evidence.read_text())
    assert data['schema']=='front_distance_verified_evidence_v1' and len(data['runs'])==54
    assert data['certificate_checks']['front_mismatches']==0 and data['certificate_checks']['work_equal']
    assert len(data['quality_checks'])==9 and all(q['full_quality_equal'] for q in data['quality_checks'])
    assert data['operation_checks']['work_equal'] and data['operation_checks']['native_call_coverage']
    assert data['runtime_source_files_matched']==14 and data['kernel_application_sources_unchanged']
    if args.verify_source_root:
        manifest=json.loads((BASE/(PREFIX+'_source_manifest.json')).read_text())
        assert len(manifest)==200 and len({r['path'] for r in manifest})==200
        verify_sources(args.verify_source_root,data,manifest)
    indexed={(r['route'],int(r['ranks']),int(r['partition_seed']),int(r['repeat'])):r for r in data['runs']}
    assert len(indexed)==54
    pairs=[];groups=[]
    for p in (128,256,512):
        for seed in (-1,17,41):
            here=[]
            for repeat in (1,2,3):
                a=indexed['split_active',p,seed,repeat];b=indexed['front_distance',p,seed,repeat]
                assert a['source_revision']==b['source_revision']==data['algorithm_commit'] and a['binary_sha256']==b['binary_sha256']
                assert a['volume_front_distance']=='distance_original_v1' and b['volume_front_distance']=='distance_memo_v1'
                assert float(a['front_distance_queries'])==float(b['front_distance_queries'])
                assert float(b['front_distance_queries'])==float(b['front_distance_hits'])+float(b['front_distance_computed'])
                r=dict(ranks=p,seed=seed,repeat=repeat,control_core=float(a['core_seconds']),candidate_core=float(b['core_seconds']),
                    core_reduction_pct=100*(1-float(b['core_seconds'])/float(a['core_seconds'])),
                    compute_reduction_pct=100*(1-float(b['compute_max_seconds'])/float(a['compute_max_seconds'])),
                    control_critical=int(a['slowest_compute_rank']),candidate_critical=int(b['slowest_compute_rank']),
                    queries=int(float(b['front_distance_queries'])),hits=int(float(b['front_distance_hits'])),
                    summed_match_reduction_pct=100*(1-float(b['front_distance_apply_seconds'])/float(a['front_distance_apply_seconds'])))
                here.append(r);pairs.append(r)
            groups.append(dict(ranks=p,seed=seed,paired_core_pct=st.median(r['core_reduction_pct'] for r in here),
                paired_compute_pct=st.median(r['compute_reduction_pct'] for r in here),wins=sum(r['core_reduction_pct']>0 for r in here),
                queries_per_repeat=here[0]['queries'],hits_per_repeat=here[0]['hits'],
                hit_pct=100*here[0]['hits']/here[0]['queries'],basic_exercised=all(r['hits']>0 for r in here),
                summed_match_reduction_pct=st.median(r['summed_match_reduction_pct'] for r in here)))
    queries=sum(r['queries'] for r in pairs);hits=sum(r['hits'] for r in pairs)
    assert (queries,hits)==(54294357,156)
    assert sum(r['repeat']==0 for r in data['certificate_pairs'])==9
    samples=data['same_rank_samples'];same_rank=[]
    for g in groups:
        p,seed=g['ranks'],g['seed'];old=[r['control_critical'] for r in pairs if (r['ranks'],r['seed'])==(p,seed)]
        rank=sorted(set(old),key=lambda rank:(-old.count(rank),rank))[0]
        a=sorted((s for s in samples if (s['p'],s['seed'],s['rank'],s['route'])==(p,seed,rank,'split_active')),key=lambda s:s['repeat'])
        b=sorted((s for s in samples if (s['p'],s['seed'],s['rank'],s['route'])==(p,seed,rank,'front_distance')),key=lambda s:s['repeat'])
        assert len(a)==len(b)==3
        for x,y in zip(a,b):assert all(x[k]==y[k] for k in ('queries','calls','rules','candidates'))
        same_rank.append(dict(ranks=p,seed=seed,rank=rank,queries=b[0]['queries'],hits=b[0]['hits'],
            control_match=st.median(s['match'] for s in a),candidate_match=st.median(s['match'] for s in b),
            paired_match_reduction_pct=st.median(100*(1-y['match']/x['match']) for x,y in zip(a,b)),
            control_front=st.median(s['front'] for s in a) if all(s['front'] is not None for s in a) else None,
            candidate_front=st.median(s['front'] for s in b) if all(s['front'] is not None for s in b) else None,
            control_compute=st.median(s['compute'] for s in a),candidate_compute=st.median(s['compute'] for s in b)))
    summary=dict(batch=data['batch'],batch_commit=data['batch_commit'],algorithm_commit=data['algorithm_commit'],
        decision='stop_distance_memoization',queries=queries,hits=hits,hit_pct=100*hits/queries,
        formal_core_wins=sum(r['core_reduction_pct']>0 for r in pairs),groups=groups,same_rank=same_rank,
        certificates=data['certificate_checks'],operations=data['operation_checks'],quality=data['quality_checks'])
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/(PREFIX+'_summary.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    write_csv(args.out/(PREFIX+'_pairs.csv'),pairs);write_csv(args.out/(PREFIX+'_same_rank.csv'),same_rank)
    print(json.dumps(dict(decision=summary['decision'],queries=queries,hits=hits,hit_pct=summary['hit_pct'],groups=groups),ensure_ascii=False))
if __name__=='__main__':main()
