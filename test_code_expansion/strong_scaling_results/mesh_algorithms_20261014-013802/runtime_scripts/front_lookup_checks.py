"""Candidate-local reverse lookup protocol, exact replay and rank-local work."""
import csv
import json
import math
from pathlib import Path

FIELDS=tuple('calls candidates queried_candidates queries baseline_comparisons linear_comparisons direct_queries table_builds table_writes table_clears table_bytes_peak verified mismatches reference_seconds'.split())
WORK=FIELDS[:5]
PREFIX='front_lookup_'
MODES=('linear_face_lookup_v1','inverse_face_lookup_v1','inverse_face_lookup_replay_v1')

def validate(rows,metadata,result,slowest_rank):
    mode=metadata.get('volume_front_lookup')
    if mode is None:
        if any(k.startswith(PREFIX) for row in rows for k in row['metrics']):raise ValueError('front lookup metrics without protocol')
        return
    if mode not in MODES or metadata.get('kernel_scheduler')!='cost_profile':raise ValueError('invalid front lookup protocol')
    if metadata.get('volume_incidence_build')!=('ordered_incidence_replay_v1' if rows[0]['repeat']==0 else 'ordered_incidence_v1'):
        raise ValueError('front lookup changed incidence base')
    if (any(metadata.get(k) is not None for k in ('volume_front_rules','volume_front_projection','volume_front_components')) or
        metadata.get('volume_front_distance')!='distance_original_v1' or metadata.get('volume_front_transform')!='dense_rule_operator_v1'):
        raise ValueError('front lookup mixed algorithms or fine timers')
    if mode==MODES[2] and (rows[0]['repeat']!=0 or metadata.get('mesh_quality')!='volume_audit_v1'):
        raise ValueError('front lookup replay outside quality warmup')
    work=[];complete=[]
    for row in rows:
        v={f:row['metrics'].get(PREFIX+f) for f in FIELDS}
        if any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 or
               ('seconds' not in f and (int(x)!=x or x>2**53)) for f,x in v.items()):raise ValueError('invalid front lookup metric')
        if v['mismatches'] or v['verified']!=(v['calls'] if mode==MODES[2] else 0):raise ValueError('front lookup full replay incomplete')
        if mode!=MODES[2] and v['reference_seconds']:raise ValueError('front lookup reference entered formal timing')
        if not v['calls'] and any(v.values()):raise ValueError('front lookup work without calls')
        if (v['calls']!=row['metrics'].get('front_distance_calls') or
            v['candidates']!=row['metrics'].get('front_distance_candidates')):raise ValueError('front lookup independent work mismatch')
        if (v['queried_candidates']>v['candidates'] or v['queries']<v['queried_candidates'] or
            v['direct_queries']>v['queries'] or v['linear_comparisons']>v['baseline_comparisons'] or
            v['table_clears']>v['table_writes']):raise ValueError('front lookup conservation mismatch')
        if not v['queried_candidates'] and any(v[f] for f in FIELDS[3:11]):raise ValueError('front lookup work without queried candidates')
        if mode==MODES[0]:
            if any(v[f] for f in FIELDS[6:11]) or v['linear_comparisons']!=v['baseline_comparisons']:
                raise ValueError('linear front lookup entered inverse path')
        else:
            if v['table_builds']!=v['queried_candidates']:raise ValueError('front lookup build coverage mismatch')
            if v['direct_queries']==v['queries'] and v['linear_comparisons']:raise ValueError('front lookup unexpected linear work')
            if v['direct_queries'] and not v['table_bytes_peak']:raise ValueError('front lookup missing table storage')
        work.append([row['rank']]+[v[f] for f in WORK]);complete.append([row['rank']]+[v[f] for f in FIELDS])
        for f,x in v.items():
            key=PREFIX+f
            result[key]=max(result.get(key,0),x) if f=='table_bytes_peak' else result.get(key,0)+x
            if row['rank']==slowest_rank:result['slowest_compute_'+key]=x
    result.update(volume_front_lookup=mode)
    result[PREFIX+'work_signature']=json.dumps(work,separators=(',',':'))
    result[PREFIX+'certificate_signature']=json.dumps(complete,separators=(',',':'))

def check_certificates(certificates,run,repeat,ranks,mode):
    ordered=sorted(certificates,key=lambda c:int(c['rank']))
    if [int(c['rank']) for c in ordered]!=list(range(ranks)):raise ValueError('front lookup ranks incomplete')
    for c in ordered:
        if (int(c['ranks']),int(c['repeat']),c.get('volume_front_lookup'),c['source_revision'],c['binary_sha256'])!=(ranks,repeat,mode,run['source_revision'],run['binary_sha256']):
            raise ValueError('front lookup certificate identity mismatch')
    for name,fields in (('work',WORK),('certificate',FIELDS)):
        signature=[[int(c['rank'])]+[float(c[PREFIX+f]) for f in fields] for c in ordered]
        if signature!=json.loads(run[PREFIX+name+'_signature']):raise ValueError('front lookup per-rank '+name+' mismatch')
    for f in FIELDS:
        values=[float(c[PREFIX+f]) for c in ordered]
        expected=max(values) if f=='table_bytes_peak' else sum(values)
        if not math.isclose(expected,run[PREFIX+f],rel_tol=1e-12,abs_tol=1e-9):raise ValueError('front lookup aggregate mismatch')

def require_warmup(directory,mode,ranks,source,binary):
    directory=Path(directory)
    if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():raise ValueError('front lookup quality warmup did not succeed')
    if not json.loads((directory/'quality_summary.json').read_text()).get('structural_pass'):raise ValueError('front lookup structural audit failed')
    with (directory/'algorithm_certificate.csv').open() as stream:cert=list(csv.DictReader(stream))
    cert.sort(key=lambda c:int(c['rank']))
    if [int(c['rank']) for c in cert]!=list(range(ranks)):raise ValueError('front lookup warmup ranks incomplete')
    expected=MODES[0 if mode==0 else 2];rows=[]
    for c in cert:
        if (int(c['ranks']),int(c['repeat']),c.get('volume_front_lookup'),c['source_revision'],c['binary_sha256'])!=(ranks,0,expected,source,binary):
            raise ValueError('front lookup warmup identity mismatch')
        expected_base=dict(volume_incidence_build='ordered_incidence_replay_v1',volume_front_distance='distance_original_v1',volume_front_transform='dense_rule_operator_v1')
        if any(c.get(k)!=v for k,v in expected_base.items()) or any(c.get(k) for k in ('volume_front_rules','volume_front_projection','volume_front_components')):
            raise ValueError('front lookup warmup mixed base')
        metrics={PREFIX+f:float(c[PREFIX+f]) for f in FIELDS}
        metrics.update({f:float(c[f]) for f in ('front_distance_calls','front_distance_candidates')})
        rows.append({'rank':int(c['rank']),'repeat':0,'metrics':metrics})
    metadata=dict(volume_front_lookup=expected,kernel_scheduler='cost_profile',volume_incidence_build='ordered_incidence_replay_v1',volume_front_distance='distance_original_v1',volume_front_transform='dense_rule_operator_v1',mesh_quality='volume_audit_v1')
    result={};validate(rows,metadata,result,0)
    if not result[PREFIX+'queries'] or (mode and not result[PREFIX+'direct_queries']):raise ValueError('front lookup warmup not exercised')

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path)
    p.add_argument('--mode',type=int,choices=(0,1),required=True);p.add_argument('--ranks',type=int,required=True)
    p.add_argument('--source',required=True);p.add_argument('--binary',required=True);args=p.parse_args()
    try:require_warmup(args.directory,args.mode,args.ranks,args.source,args.binary)
    except (OSError,ValueError,KeyError,TypeError) as error:p.exit(1,str(error)+'\n')
