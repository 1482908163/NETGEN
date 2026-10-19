"""Exact front-projection rebuild protocol and complete per-rank evidence."""
import csv
import json
import math
from pathlib import Path

FIELDS=('calls','triangles','iterative_tests','iterations','plane_visits',
        'derivative_evaluations','compiled_planes','positive_visits','verified',
        'mismatches','compile_seconds','solve_seconds','rules_seconds',
        'reference_seconds','common_tests','limit_exits')
MODES=('original_projection_profile_v1','fixed_projection_v1','fixed_projection_replay_v1')
WORK=('calls','triangles','iterative_tests','iterations','plane_visits','positive_visits','common_tests','limit_exits')
PREFIX='front_projection_'


def validate(rows,metadata,result,slowest_rank):
    mode=metadata.get('volume_front_projection')
    if mode is None:
        if any(k.startswith(PREFIX) for row in rows for k in row['metrics']):
            raise ValueError('front projection metrics without protocol')
        return
    if mode not in MODES or metadata.get('kernel_scheduler')!='cost_profile':
        raise ValueError('invalid front projection protocol')
    from incidence_checks import MODES as IMODES
    expected=IMODES[2] if rows[0]['repeat']==0 else IMODES[1]
    if metadata.get('volume_incidence_build')!=expected:
        raise ValueError('front projection experiment changed incidence base')
    if mode==MODES[2] and (rows[0]['repeat']!=0 or metadata.get('mesh_quality')!='volume_audit_v1'):
        raise ValueError('front projection replay outside quality warmup')
    work=[];complete=[]
    for row in rows:
        v={f:row['metrics'].get(PREFIX+f) for f in FIELDS}
        if any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0
               or ('seconds' not in f and int(x)!=x) for f,x in v.items()):
            raise ValueError('invalid front projection metric')
        calls=v['calls']
        if v['mismatches'] or v['verified']!=(calls if mode==MODES[2] else 0):
            raise ValueError('front projection full replay incomplete')
        if metadata.get('volume_front_components') is not None or metadata.get('volume_front_distance')!='distance_original_v1' or metadata.get('volume_front_transform')!='dense_rule_operator_v1':
            raise ValueError('front projection experiment mixed algorithms')
        if mode==MODES[0]:
            if v['compiled_planes'] or v['compile_seconds'] or v['derivative_evaluations']!=v['positive_visits']:
                raise ValueError('front projection control work mismatch')
        elif v['compiled_planes']!=v['derivative_evaluations'] or v['compiled_planes']>v['plane_visits'] or v['compiled_planes']<v['iterative_tests']:
            raise ValueError('front projection compiled work mismatch')
        if mode!=MODES[2] and v['reference_seconds']:
            raise ValueError('front projection reference entered formal timing')
        if v['common_tests']+v['iterative_tests']>v['triangles'] or v['iterations']<v['iterative_tests'] or v['limit_exits']>v['iterative_tests'] or v['positive_visits']>v['plane_visits']:
            raise ValueError('front projection solve coverage mismatch')
        if not calls and any(v.values()):
            raise ValueError('front projection work without calls')
        if v['compile_seconds']>v['solve_seconds']+1e-6 or v['solve_seconds']>v['rules_seconds']+1e-6 or v['rules_seconds']>1.05*row['metrics']['kernel_front_seconds']+1e-3:
            raise ValueError('front projection nested timing mismatch')
        work.append([row['rank']]+[v[f] for f in WORK])
        complete.append([row['rank']]+[v[f] for f in FIELDS])
        for f,x in v.items():
            result[PREFIX+f]=result.get(PREFIX+f,0)+x
            if row['rank']==slowest_rank:result['slowest_compute_'+PREFIX+f]=x
    result['volume_front_projection']=mode
    result[PREFIX+'work_signature']=json.dumps(work,separators=(',',':'))
    result[PREFIX+'certificate_signature']=json.dumps(complete,separators=(',',':'))


def check_certificates(certificates,run,repeat,ranks,mode):
    ordered=sorted(certificates,key=lambda c:int(c['rank']))
    if [int(c['rank']) for c in ordered]!=list(range(ranks)):
        raise ValueError('front projection certificate ranks incomplete')
    for c in ordered:
        if (int(c['ranks']),int(c['repeat']),c.get('volume_front_projection'),c['source_revision'],c['binary_sha256'])!=(ranks,repeat,mode,run['source_revision'],run['binary_sha256']):
            raise ValueError('front projection certificate identity mismatch')
    for name,fields in (('work',WORK),('certificate',FIELDS)):
        signature=[[int(c['rank'])]+[float(c[PREFIX+f]) for f in fields] for c in ordered]
        if signature!=json.loads(run[PREFIX+name+'_signature']):
            raise ValueError('front projection certificate per-rank '+name+' mismatch')
    for f in FIELDS:
        if not math.isclose(sum(float(c[PREFIX+f]) for c in ordered),run[PREFIX+f],rel_tol=1e-12,abs_tol=1e-9):
            raise ValueError('front projection certificate aggregate mismatch')


def require_warmup(directory,mode,ranks,source,binary):
    directory=Path(directory)
    if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():
        raise ValueError('front projection quality warmup did not succeed')
    if not json.loads((directory/'quality_summary.json').read_text()).get('structural_pass'):
        raise ValueError('front projection structural audit failed')
    with (directory/'algorithm_certificate.csv').open() as stream:cert=list(csv.DictReader(stream))
    if sorted(int(c['rank']) for c in cert)!=list(range(ranks)):
        raise ValueError('front projection warmup ranks incomplete')
    expected=MODES[0] if mode==0 else MODES[2]
    calls_total=0;compiled=0;iterative=0
    for c in cert:
        if (int(c['ranks']),int(c['repeat']),c.get('volume_front_projection'),c['source_revision'],c['binary_sha256'])!=(ranks,0,expected,source,binary):
            raise ValueError('front projection warmup mode/source mismatch')
        metrics={PREFIX+f:float(c[PREFIX+f]) for f in FIELDS}
        # Reapply finite/conservation/timing checks to the certificate used to
        # unlock formal runs. SUCCESS alone cannot authorize forged evidence.
        metadata={'volume_front_projection':expected,'kernel_scheduler':'cost_profile',
                  'volume_incidence_build':'ordered_incidence_replay_v1',
                  'volume_front_distance':'distance_original_v1',
                  'volume_front_transform':'dense_rule_operator_v1','mesh_quality':'volume_audit_v1'}
        validate([{'rank':int(c['rank']),'repeat':0,'metrics':dict(metrics,kernel_front_seconds=metrics[PREFIX+'rules_seconds'])}],metadata,{},int(c['rank']))
        calls=metrics[PREFIX+'calls'];calls_total+=calls;compiled+=metrics[PREFIX+'compiled_planes'];iterative+=metrics[PREFIX+'iterative_tests']
        if float(c[PREFIX+'mismatches']) or float(c[PREFIX+'verified'])!=(calls if mode else 0):
            raise ValueError('front projection warmup replay incomplete')
    if not calls_total or not iterative or (mode and not compiled):raise ValueError('front projection warmup not exercised')


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path)
    p.add_argument('--mode',type=int,choices=(0,1),required=True)
    p.add_argument('--ranks',type=int,required=True);p.add_argument('--source',required=True);p.add_argument('--binary',required=True)
    args=p.parse_args()
    try:require_warmup(args.directory,args.mode,args.ranks,args.source,args.binary)
    except (OSError,ValueError,KeyError,TypeError) as error:p.exit(1,str(error)+'\n')
