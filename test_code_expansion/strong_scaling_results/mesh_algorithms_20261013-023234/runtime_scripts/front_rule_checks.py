"""Complete rule cost partition, replay isolation and per-rank certificates."""
import csv
import json
import math
from pathlib import Path

FIELDS=tuple('calls rules face_trials point_trials mappings edge_rejected nonconvex point_rejected face_rejected evaluated usable best_updates shared_triangles unshared_triangles quad_tests point_tests verified mismatches total_seconds reference_seconds prepare_seconds mapping_seconds setup_seconds transform_seconds point_seconds face_scan_seconds shared_seconds unshared_seconds evaluate_seconds selection_seconds finish_seconds stage_transitions'.split())
STAGES=FIELDS[20:31]
WORK=FIELDS[:16]+('stage_transitions',)
MODES=('rule_totals_v1','rule_stages_v1','rule_stages_replay_v1')
PREFIX='front_rules_'

def validate(rows,metadata,result,slowest_rank):
    mode=metadata.get('volume_front_rules')
    if mode is None:
        if any(k.startswith(PREFIX) for row in rows for k in row['metrics']):
            raise ValueError('front rule metrics without protocol')
        return
    if mode not in MODES or metadata.get('kernel_scheduler')!='cost_profile':
        raise ValueError('invalid front rule protocol')
    from incidence_checks import MODES as IMODES
    if metadata.get('volume_incidence_build')!=IMODES[2 if rows[0]['repeat']==0 else 1]:
        raise ValueError('front rule experiment changed incidence base')
    if (metadata.get('volume_front_projection') is not None or
        metadata.get('volume_front_components') is not None or
        metadata.get('volume_front_distance')!='distance_original_v1' or
        metadata.get('volume_front_transform')!='dense_rule_operator_v1'):
        raise ValueError('front rule experiment mixed algorithms')
    if mode==MODES[2] and (rows[0]['repeat']!=0 or metadata.get('mesh_quality')!='volume_audit_v1'):
        raise ValueError('front rule replay outside quality warmup')
    work=[];complete=[]
    for row in rows:
        v={f:row['metrics'].get(PREFIX+f) for f in FIELDS}
        if any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 or
               ('seconds' not in f and (int(x)!=x or x>2**53)) for f,x in v.items()):
            raise ValueError('invalid front rule metric')
        if v['mismatches'] or v['verified']!=(v['calls'] if mode==MODES[2] else 0):
            raise ValueError('front rule full replay incomplete')
        if mode!=MODES[2] and v['reference_seconds']:
            raise ValueError('front rule reference entered formal timing')
        if (any(v[f]>v['mappings'] for f in ('edge_rejected','nonconvex','point_rejected','face_rejected','evaluated')) or
            v['evaluated']>v['mappings']-v['edge_rejected'] or
            any(v[f]>v['evaluated'] for f in ('usable','best_updates')) or
            v['point_rejected']>v['point_tests']):
            raise ValueError('front rule candidate conservation mismatch')
        if not v['calls'] and any(v.values()):
            raise ValueError('front rule work without calls')
        for field,old in (('calls','calls'),('rules','rules'),('mappings','candidates')):
            if v[field]!=row['metrics'].get('front_distance_'+old):
                raise ValueError('front rule independent work counter mismatch')
        if v['calls'] and (not v['stage_transitions'] or v['total_seconds']<=0):
            raise ValueError('front rule timing path not exercised')
        partition=sum(v[f] for f in STAGES)
        if mode==MODES[0]:
            if partition:raise ValueError('front rule total control entered stage timing')
        elif not math.isclose(partition,v['total_seconds'],rel_tol=1e-9,abs_tol=1e-9):
            raise ValueError('front rule exclusive partition mismatch')
        if v['total_seconds']>1.05*row['metrics']['kernel_front_seconds']+1e-3:
            raise ValueError('front rule timing exceeds native front')
        work.append([row['rank']]+[v[f] for f in WORK])
        complete.append([row['rank']]+[v[f] for f in FIELDS])
        for f,x in v.items():
            result[PREFIX+f]=result.get(PREFIX+f,0)+x
            if row['rank']==slowest_rank:result['slowest_compute_'+PREFIX+f]=x
    result['volume_front_rules']=mode
    result[PREFIX+'work_signature']=json.dumps(work,separators=(',',':'))
    result[PREFIX+'certificate_signature']=json.dumps(complete,separators=(',',':'))

def check_certificates(certificates,run,repeat,ranks,mode):
    ordered=sorted(certificates,key=lambda c:int(c['rank']))
    if [int(c['rank']) for c in ordered]!=list(range(ranks)):
        raise ValueError('front rule certificate ranks incomplete')
    for c in ordered:
        if (int(c['ranks']),int(c['repeat']),c.get('volume_front_rules'),c['source_revision'],c['binary_sha256'])!=(ranks,repeat,mode,run['source_revision'],run['binary_sha256']):
            raise ValueError('front rule certificate identity mismatch')
    for name,fields in (('work',WORK),('certificate',FIELDS)):
        signature=[[int(c['rank'])]+[float(c[PREFIX+f]) for f in fields] for c in ordered]
        if signature!=json.loads(run[PREFIX+name+'_signature']):
            raise ValueError('front rule certificate per-rank '+name+' mismatch')
    for f in FIELDS:
        if not math.isclose(sum(float(c[PREFIX+f]) for c in ordered),run[PREFIX+f],rel_tol=1e-12,abs_tol=1e-9):
            raise ValueError('front rule certificate aggregate mismatch')

def require_warmup(directory,mode,ranks,source,binary):
    directory=Path(directory)
    if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():
        raise ValueError('front rule quality warmup did not succeed')
    if not json.loads((directory/'quality_summary.json').read_text()).get('structural_pass'):
        raise ValueError('front rule structural audit failed')
    with (directory/'algorithm_certificate.csv').open() as stream:cert=list(csv.DictReader(stream))
    if sorted(int(c['rank']) for c in cert)!=list(range(ranks)):
        raise ValueError('front rule warmup ranks incomplete')
    expected=MODES[0 if mode==0 else 2];calls_total=0;mappings=0
    for c in cert:
        if (int(c['ranks']),int(c['repeat']),c.get('volume_front_rules'),c['source_revision'],c['binary_sha256'])!=(ranks,0,expected,source,binary):
            raise ValueError('front rule warmup mode/source mismatch')
        metrics={PREFIX+f:float(c[PREFIX+f]) for f in FIELDS}
        metrics.update({f:float(c[f]) for f in ('front_distance_calls','front_distance_rules','front_distance_candidates')})
        metrics['kernel_front_seconds']=metrics[PREFIX+'total_seconds']
        metadata={'volume_front_rules':expected,'kernel_scheduler':'cost_profile',
                  'volume_incidence_build':'ordered_incidence_replay_v1',
                  'volume_front_distance':'distance_original_v1',
                  'volume_front_transform':'dense_rule_operator_v1','mesh_quality':'volume_audit_v1'}
        validate([{'rank':int(c['rank']),'repeat':0,'metrics':metrics}],metadata,{},int(c['rank']))
        calls_total+=metrics[PREFIX+'calls'];mappings+=metrics[PREFIX+'mappings']
    if not calls_total or not mappings:raise ValueError('front rule warmup not exercised')

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path)
    p.add_argument('--mode',type=int,choices=(0,1),required=True)
    p.add_argument('--ranks',type=int,required=True);p.add_argument('--source',required=True);p.add_argument('--binary',required=True)
    args=p.parse_args()
    try:require_warmup(args.directory,args.mode,args.ranks,args.source,args.binary)
    except (OSError,ValueError,KeyError,TypeError) as error:p.exit(1,str(error)+'\n')
