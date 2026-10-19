"""Exact rule-private workspace protocol and rank-local allocation accounting."""
import csv,json,math
from pathlib import Path
FIELDS=tuple('calls transforms vector_entries vector_requests baseline_heap_requests heap_requests reused_requests workspace_hits allocated_entries retained_entries_peak initialized_rules verified mismatches reference_seconds'.split())
WORK=FIELDS[:5];PREFIX='front_workspace_'
MODES=('temporary_workspace_v1','reused_workspace_v1','reused_workspace_replay_v1')
PEAK='retained_entries_peak'

def validate(rows,metadata,result,slowest_rank):
    mode=metadata.get('volume_front_workspace')
    if mode is None:
        if any(k.startswith(PREFIX) for row in rows for k in row['metrics']):raise ValueError('workspace metrics without protocol')
        return
    if mode not in MODES or metadata.get('kernel_scheduler')!='cost_profile':raise ValueError('invalid workspace protocol')
    if metadata.get('volume_incidence_build')!=('ordered_incidence_replay_v1' if rows[0]['repeat']==0 else 'ordered_incidence_v1'):raise ValueError('workspace changed incidence base')
    if (any(metadata.get(k) is not None for k in ('volume_front_lookup','volume_front_rules','volume_front_projection','volume_front_components')) or
        metadata.get('volume_front_distance')!='distance_original_v1' or metadata.get('volume_front_transform')!='dense_rule_operator_v1'):raise ValueError('workspace mixed algorithms or fine timers')
    if mode==MODES[2] and (rows[0]['repeat']!=0 or metadata.get('mesh_quality')!='volume_audit_v1'):raise ValueError('workspace replay outside quality warmup')
    work=[];complete=[]
    for row in rows:
        v={f:row['metrics'].get(PREFIX+f) for f in FIELDS}
        if any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 or ('seconds' not in f and (int(x)!=x or x>2**53)) for f,x in v.items()):raise ValueError('invalid workspace metric')
        if v['mismatches'] or v['verified']!=(v['calls'] if mode==MODES[2] else 0):raise ValueError('workspace full replay incomplete')
        if mode!=MODES[2] and v['reference_seconds']:raise ValueError('workspace reference entered formal timing')
        if not v['calls'] and any(v.values()):raise ValueError('workspace work without calls')
        if (v['calls']!=row['metrics'].get('front_distance_calls') or v['transforms']!=row['metrics'].get('front_distance_candidates') or
            v['transforms']!=row['metrics'].get('front_transform_transforms')):raise ValueError('workspace independent work mismatch')
        if (v['vector_requests']!=3*v['transforms'] or v['baseline_heap_requests']!=v['vector_requests'] or
            v['heap_requests']+v['reused_requests']!=v['vector_requests'] or v['allocated_entries']>v['vector_entries'] or
            v['initialized_rules']>v['transforms'] or 3*v['workspace_hits']>v['reused_requests'] or
            v['workspace_hits']+v['initialized_rules']>v['transforms']):raise ValueError('workspace allocation conservation mismatch')
        if not v['transforms'] and any(v[f] for f in FIELDS[2:11]):raise ValueError('workspace storage without transforms')
        if mode==MODES[0]:
            if any(v[f] for f in ('reused_requests','workspace_hits',PEAK,'initialized_rules')) or v['heap_requests']!=v['baseline_heap_requests'] or v['allocated_entries']!=v['vector_entries']:
                raise ValueError('temporary workspace entered reuse path')
        elif v['transforms'] and not v[PEAK]:raise ValueError('reusable workspace missing storage')
        work.append([row['rank']]+[v[f] for f in WORK]);complete.append([row['rank']]+[v[f] for f in FIELDS])
        for f,x in v.items():
            key=PREFIX+f
            result[key]=max(result.get(key,0),x) if f==PEAK else result.get(key,0)+x
            if row['rank']==slowest_rank:result['slowest_compute_'+key]=x
    result.update(volume_front_workspace=mode)
    result[PREFIX+'work_signature']=json.dumps(work,separators=(',',':'));result[PREFIX+'certificate_signature']=json.dumps(complete,separators=(',',':'))

def check_certificates(certificates,run,repeat,ranks,mode):
    ordered=sorted(certificates,key=lambda c:int(c['rank']))
    if [int(c['rank']) for c in ordered]!=list(range(ranks)):raise ValueError('workspace ranks incomplete')
    for c in ordered:
        if (int(c['ranks']),int(c['repeat']),c.get('volume_front_workspace'),c['source_revision'],c['binary_sha256'])!=(ranks,repeat,mode,run['source_revision'],run['binary_sha256']):raise ValueError('workspace certificate identity mismatch')
    for name,fields in (('work',WORK),('certificate',FIELDS)):
        signature=[[int(c['rank'])]+[float(c[PREFIX+f]) for f in fields] for c in ordered]
        if signature!=json.loads(run[PREFIX+name+'_signature']):raise ValueError('workspace per-rank '+name+' mismatch')
    for f in FIELDS:
        values=[float(c[PREFIX+f]) for c in ordered];expected=max(values) if f==PEAK else sum(values)
        if not math.isclose(expected,run[PREFIX+f],rel_tol=1e-12,abs_tol=1e-9):raise ValueError('workspace aggregate mismatch')

def require_warmup(directory,mode,ranks,source,binary):
    directory=Path(directory)
    if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():raise ValueError('workspace quality warmup did not succeed')
    if not json.loads((directory/'quality_summary.json').read_text()).get('structural_pass'):raise ValueError('workspace structural audit failed')
    with (directory/'algorithm_certificate.csv').open() as s:cert=list(csv.DictReader(s))
    cert.sort(key=lambda c:int(c['rank']))
    if [int(c['rank']) for c in cert]!=list(range(ranks)):raise ValueError('workspace warmup ranks incomplete')
    expected=MODES[0 if mode==0 else 2];rows=[]
    for c in cert:
        if (int(c['ranks']),int(c['repeat']),c.get('volume_front_workspace'),c['source_revision'],c['binary_sha256'])!=(ranks,0,expected,source,binary):raise ValueError('workspace warmup identity mismatch')
        base=dict(volume_incidence_build='ordered_incidence_replay_v1',volume_front_distance='distance_original_v1',volume_front_transform='dense_rule_operator_v1')
        if any(c.get(k)!=v for k,v in base.items()) or any(c.get(k) for k in ('volume_front_lookup','volume_front_rules','volume_front_projection','volume_front_components')):raise ValueError('workspace warmup mixed base')
        m={PREFIX+f:float(c[PREFIX+f]) for f in FIELDS};m.update({f:float(c[f]) for f in ('front_distance_calls','front_distance_candidates','front_transform_transforms')})
        rows.append({'rank':int(c['rank']),'repeat':0,'metrics':m})
    metadata=dict(volume_front_workspace=expected,kernel_scheduler='cost_profile',volume_incidence_build='ordered_incidence_replay_v1',volume_front_distance='distance_original_v1',volume_front_transform='dense_rule_operator_v1',mesh_quality='volume_audit_v1')
    result={};validate(rows,metadata,result,0)
    if not result[PREFIX+'transforms'] or (mode and not result[PREFIX+'reused_requests']):raise ValueError('workspace warmup not exercised')

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path)
    p.add_argument('--mode',type=int,choices=(0,1),required=True);p.add_argument('--ranks',type=int,required=True);p.add_argument('--source',required=True);p.add_argument('--binary',required=True);a=p.parse_args()
    try:require_warmup(a.directory,a.mode,a.ranks,a.source,a.binary)
    except (OSError,ValueError,KeyError,TypeError) as error:p.exit(1,str(error)+'\n')
