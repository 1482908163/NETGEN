"""Recheck retained certificates; raw rank profiles were not uploaded.

Usage: python3 this_script.py RESULT_DIRECTORY SOURCE_MANIFEST --output evidence.json
The manifest lists Git blob hashes from result commit c816891.
"""
import argparse
import csv
import hashlib
import json
import math
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'test_code_expansion/strong_scaling'))
from front_workspace_checks import validate, FIELDS, WORK, PREFIX, MODES

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('root',type=Path);p.add_argument('manifest',type=Path)
p.add_argument('--output',type=Path)
a=p.parse_args();root=a.root;manifest=json.loads(a.manifest.read_text())
entries={e['path']:e for e in manifest['files'] if e['type']=='blob'}
source='31b75f59b17a276e57efc1142221dd6de61da287'
binary='8ba1a130530517f2fe5f7c28f367b846a087b3cee176c1f616802ffff745234a'
checked={};allpairs=[];alloc=[0,0];verified=0;rankpairs=0;ghost_items=0;quality=[];codec=[]

def read(path):
    path=str(path);data=(root/path).read_bytes()
    digest=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    assert digest==entries[path]['sha'],path
    checked[path]=dict(git_blob_sha=digest,sha256=hashlib.sha256(data).hexdigest())
    return data

def table(path):return list(csv.DictReader(read(path).decode().splitlines()))
def close(x,y):assert math.isclose(float(x),float(y),rel_tol=1e-11,abs_tol=1e-9),(x,y)
def stem(seed):return 'sparse'+('' if seed==-1 else f'_seed{seed}')+'_natural'
def directory(route,n,seed,rep):return f'route_workspace_{route}/p{n}/{stem(seed)}/repeat_{rep}'
def identity(rows,n,seed,rep):
    rows.sort(key=lambda x:int(x['rank']))
    assert [int(x['rank']) for x in rows]==list(range(n))
    for x in rows:
        assert (x['source_revision'],x['binary_sha256'],x['algorithm'],x['timing'])==(source,binary,'sparse','natural')
        assert (int(x['ranks']),int(x['partition_seed']),int(x['repeat']))==(n,seed,rep)

for n in (128,256):
    pairs=table(f'p{n}/front_workspace_fixed_rank_pairs.csv');assert len(pairs)==9
    for seed in (-1,17,41):
        qp=directory('temporary',n,seed,0)+'/quality_summary.json'
        eq=directory('reuse',n,seed,0)+'/quality_summary.json'
        assert entries[qp]['sha']==entries[eq]['sha']
        q=json.loads(read(qp));assert q['structural_pass'] and not q['issues']
        quality.append(dict(ranks=n,seed=seed,counts=q['counts'],identical_blob=entries[qp]['sha']))
        for rep in range(4):
            certs=[]
            for route in ('temporary','reuse'):
                rows=table(directory(route,n,seed,rep)+'/front_workspace_certificate.csv')
                identity(rows,n,seed,rep)
                mode=MODES[0 if route=='temporary' else (2 if rep==0 else 1)]
                assert all(x['volume_front_workspace']==mode for x in rows)
                raw=[dict(rank=int(x['rank']),repeat=rep,metrics={k:float(v) for k,v in x.items() if k.startswith(('front_workspace_','front_distance_','front_transform_'))}) for x in rows]
                meta=dict(volume_front_workspace=mode,kernel_scheduler='cost_profile',volume_incidence_build='ordered_incidence_replay_v1' if rep==0 else 'ordered_incidence_v1',volume_front_distance='distance_original_v1',volume_front_transform='dense_rule_operator_v1',mesh_quality='volume_audit_v1')
                sums={};validate(raw,meta,sums,0)
                verified+=int(sums[PREFIX+'verified'])
                if route=='reuse' and rep:
                    alloc[0]+=int(sums[PREFIX+'baseline_heap_requests']);alloc[1]+=int(sums[PREFIX+'heap_requests'])
                for x in rows:
                    for f in ('core_seconds','compute_seconds','kernel_front_seconds','front_distance_apply_seconds'):
                        assert math.isfinite(float(x[f])) and float(x[f])>=0
                certs.append(rows)
            for x,y in zip(*certs):
                for f in (*[PREFIX+f for f in WORK],'front_distance_calls','front_distance_rules','front_distance_candidates','front_transform_transforms'):close(x[f],y[f])
                if rep:rankpairs+=1
            if rep:
                row=next(x for x in pairs if int(x['seed'])==seed and int(x['repeat'])==rep)
                selected=max(certs[0],key=lambda x:float(x['compute_seconds']))
                assert int(row['rank'])==int(selected['rank'])
                for label,rows in zip(('control','candidate'),certs):
                    x=rows[int(row['rank'])]
                    for name,key in (('core','core_seconds'),('compute','compute_seconds'),('front','kernel_front_seconds'),('rules','front_distance_apply_seconds')):close(row[label+'_'+name+'_seconds'],x[key])
                    for f in FIELDS:close(row[label+'_'+PREFIX+f],x[PREFIX+f])
                for name in ('core','compute','front','rules'):close(row[name+'_reduction_percent'],100*(1-float(row['candidate_'+name+'_seconds'])/float(row['control_'+name+'_seconds'])))
                allpairs.append(row)
        gc=[]
        for route in ('temporary','reuse'):
            rows=table(directory(route,n,seed,0)+'/ghost_codec_certificate.csv');identity(rows,n,seed,0)
            for x in rows:
                assert x['ghost_codec_audit']=='vertex_dictionary_audit_v1'
                v={k.removeprefix('ghost_codec_'):float(v) for k,v in x.items() if k.startswith('ghost_codec_') and k!='ghost_codec_audit'}
                assert all(math.isfinite(vv) and vv>=0 for vv in v.values())
                assert all(vv.is_integer() for k,vv in v.items() if 'seconds' not in k)
                assert v['calls']==1 and v['mismatches']==0
                assert v['items']==v['verified_items']==float(x['volume_send_items'])
                assert v['peers']==v['verified_peers']==float(x['volume_num_s'])
                assert v['raw_bytes']==140*v['items']
                assert v['dictionary_bytes']==16*v['peers']+32*v['unique_vertices']+28*v['items']
                assert v['profitable_peers']+v['bypass_peers']==v['peers']
                assert v['selected_bytes']<=min(v['raw_bytes'],v['dictionary_bytes'])
                assert v['encode_seconds']+v['decode_seconds']<=v['lifecycle_seconds']+1e-6
                ghost_items+=int(v['verified_items'])
            gc.append(rows)
            summaries=table(f'p{n}/ghost_codec_summary.csv')
            s=next(x for x in summaries if x['route']=='workspace_'+route and int(x['seed'])==seed)
            for key in ('items','peers','unique_vertices','raw_bytes','dictionary_bytes','selected_bytes','profitable_peers','bypass_peers'):close(s[key],sum(float(x['ghost_codec_'+key]) for x in rows))
            close(s['max_rank_codec_lifecycle_seconds'],max(float(x['ghost_codec_lifecycle_seconds']) for x in rows))
            close(s['payload_reduction_percent'],100*(1-float(s['selected_bytes'])/float(s['raw_bytes'])))
            codec.append(s)
        for x,y in zip(*gc):
            for key in ('calls','peers','items','unique_vertices','raw_bytes','dictionary_bytes','selected_bytes','profitable_peers','bypass_peers','verified_items','verified_peers','mismatches'):close(x['ghost_codec_'+key],y['ghost_codec_'+key])
    for file in ('front_workspace_sources.json','ghost_codec_sources.json'):
        for entry in json.loads(read(f'p{n}/{file}')):
            path=entry['path'];actual=path if (root/path).exists() else path.replace('route_workspace_reuse/','route_workspace_temporary/')
            assert entries[path]['sha']==entries[actual]['sha']
            assert hashlib.sha256(read(actual)).hexdigest()==entry['sha256']

summary=[]
for n in (128,256,None):
    rows=[x for x in allpairs if n is None or int(x['ranks'])==n]
    r=dict(ranks=n,pairs=len(rows))
    for metric in ('core','compute','front','rules'):
        values=[float(x[metric+'_reduction_percent']) for x in rows]
        r[metric]=dict(reduction_percent_median=st.median(values),wins=sum(x>0 for x in values))
    r['rules_saved_seconds_median']=st.median(float(x['control_rules_seconds'])-float(x['candidate_rules_seconds']) for x in rows)
    summary.append(r)
stages={}
for n in (128,256):
    rows=table(f'route_workspace_temporary/p{n}/analysis/stages.csv')
    stages[n]={name:dict(max_seconds_median=st.median(float(x['max_seconds']) for x in rows if x['stage']==name),mean_seconds_median=st.median(float(x['mean_seconds']) for x in rows if x['stage']==name)) for name in sorted({x['stage'] for x in rows}) if 'volume' in name or 'core' in name}
runtime=json.loads(read('runtime_manifest.json'));assert runtime['source_revision']==source
failure=json.loads(read('p512/ALLOCATION_STOPPED.json'))
log=read('route_workspace_temporary/p512/sparse_natural/repeat_0/failure_reason.txt').decode()
assert failure['exit_code']==124 and failure['timeout_seconds']==600
assert 'MPI_Init_thread(' in log and 'PMIx_Fence returned -24' in log
result=dict(result_commit=manifest['commit'],experiment_source=source,binary_sha256=binary,raw_rank_profiles_uploaded=any('rank_profiles.jsonl' in x for x in entries),checked_files=len(checked),formal_runs=36,warmup_runs=12,formal_rank_pairs=rankpairs,full_workspace_replay_calls=verified,ghost_roundtrip_items=ghost_items,allocation_requests=dict(temporary=alloc[0],reused=alloc[1],reduction_percent=100*(1-alloc[1]/alloc[0])),quality=quality,performance=summary,ghost_codec=codec,control_stages=stages,failure_512=failure,source_files=checked)
output=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
if a.output:a.output.write_text(output)
else:print(output)
