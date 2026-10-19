"""Exact front-component rebuild protocol and complete per-rank evidence."""
import csv
import json
import math
from pathlib import Path

FIELDS=('rebuilds','input_points','input_faces','union_attempts','unions',
        'reference_passes','reference_face_visits','verified','mismatches',
        'label_seconds','reference_seconds','rebuild_seconds','select_seconds',
        'locals_seconds','rules_seconds','iterations')
MODES=('relaxation_components_profile_v1','union_components_v1','union_components_replay_v1')
WORK=('rebuilds','input_points','input_faces','iterations')
PREFIX='front_components_'


def validate(rows,metadata,result,slowest_rank):
    mode=metadata.get('volume_front_components')
    if mode is None:
        if any(k.startswith(PREFIX) for row in rows for k in row['metrics']):
            raise ValueError('front component metrics without protocol')
        return
    if mode not in MODES or metadata.get('kernel_scheduler')!='cost_profile':
        raise ValueError('invalid front component protocol')
    from incidence_checks import MODES as IMODES
    expected=IMODES[2] if rows[0]['repeat']==0 else IMODES[1]
    if metadata.get('volume_incidence_build')!=expected:
        raise ValueError('front component experiment changed incidence base')
    if mode==MODES[2] and (rows[0]['repeat']!=0 or metadata.get('mesh_quality')!='volume_audit_v1'):
        raise ValueError('front component replay outside quality warmup')
    work=[];complete=[]
    for row in rows:
        v={f:row['metrics'].get(PREFIX+f) for f in FIELDS}
        if any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0
               or ('seconds' not in f and int(x)!=x) for f,x in v.items()):
            raise ValueError('invalid front component metric')
        calls=v['rebuilds'];faces=v['input_faces']
        if v['mismatches'] or v['verified']!=(calls if mode==MODES[2] else 0):
            raise ValueError('front component exact verification incomplete')
        if mode==MODES[0]:
            if v['union_attempts'] or v['unions'] or v['reference_seconds']:
                raise ValueError('front component control entered union path')
        elif v['union_attempts']!=2*faces or v['unions']>min(v['union_attempts'],v['input_points']):
            raise ValueError('front component union work mismatch')
        if mode==MODES[1]:
            if v['reference_passes'] or v['reference_face_visits'] or v['reference_seconds']:
                raise ValueError('front component reference entered formal timing')
        elif v['reference_passes']<calls or v['reference_face_visits']<faces:
            raise ValueError('front component legacy coverage incomplete')
        if calls>v['iterations'] or (not calls and any(v[f] for f in FIELDS[:12])):
            raise ValueError('front component rebuild coverage mismatch')
        if v['reference_seconds']>v['label_seconds']+1e-6 or v['label_seconds']>v['rebuild_seconds']+1e-6 or v['rebuild_seconds']>v['select_seconds']+1e-6:
            raise ValueError('front component nested timing mismatch')
        if sum(v[f] for f in ('select_seconds','locals_seconds','rules_seconds'))>1.05*row['metrics']['kernel_front_seconds']+1e-3:
            raise ValueError('front component timing exceeds front stage')
        work.append([row['rank']]+[v[f] for f in WORK])
        complete.append([row['rank']]+[v[f] for f in FIELDS])
        for f,x in v.items():
            result[PREFIX+f]=result.get(PREFIX+f,0)+x
            if row['rank']==slowest_rank:result['slowest_compute_'+PREFIX+f]=x
    result['volume_front_components']=mode
    result[PREFIX+'work_signature']=json.dumps(work,separators=(',',':'))
    result[PREFIX+'certificate_signature']=json.dumps(complete,separators=(',',':'))


def check_certificates(certificates,run,repeat,ranks,mode):
    ordered=sorted(certificates,key=lambda c:int(c['rank']))
    if [int(c['rank']) for c in ordered]!=list(range(ranks)):
        raise ValueError('front component certificate ranks incomplete')
    for c in ordered:
        if (int(c['ranks']),int(c['repeat']),c.get('volume_front_components'),c['source_revision'],c['binary_sha256'])!=(ranks,repeat,mode,run['source_revision'],run['binary_sha256']):
            raise ValueError('front component certificate identity mismatch')
    for name,fields in (('work',WORK),('certificate',FIELDS)):
        signature=[[int(c['rank'])]+[float(c[PREFIX+f]) for f in fields] for c in ordered]
        if signature!=json.loads(run[PREFIX+name+'_signature']):
            raise ValueError('front component certificate per-rank '+name+' mismatch')
    for f in FIELDS:
        if not math.isclose(sum(float(c[PREFIX+f]) for c in ordered),run[PREFIX+f],rel_tol=1e-12,abs_tol=1e-9):
            raise ValueError('front component certificate aggregate mismatch')


def require_warmup(directory,mode,ranks,source,binary):
    directory=Path(directory)
    if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():
        raise ValueError('front component quality warmup did not succeed')
    if not json.loads((directory/'quality_summary.json').read_text()).get('structural_pass'):
        raise ValueError('front component structural audit failed')
    with (directory/'algorithm_certificate.csv').open() as stream:cert=list(csv.DictReader(stream))
    if sorted(int(c['rank']) for c in cert)!=list(range(ranks)):
        raise ValueError('front component warmup ranks incomplete')
    expected=MODES[0] if mode==0 else MODES[2]
    rebuilds=0;faces=0
    for c in cert:
        if (int(c['ranks']),int(c['repeat']),c.get('volume_front_components'),c['source_revision'],c['binary_sha256'])!=(ranks,0,expected,source,binary):
            raise ValueError('front component warmup mode/source mismatch')
        calls=float(c[PREFIX+'rebuilds']);rebuilds+=calls;faces+=float(c[PREFIX+'input_faces'])
        if float(c[PREFIX+'mismatches']) or float(c[PREFIX+'verified'])!=(calls if mode else 0):
            raise ValueError('front component warmup replay incomplete')
    if not rebuilds or not faces:raise ValueError('front component warmup not exercised')


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path)
    p.add_argument('--mode',type=int,choices=(0,1),required=True)
    p.add_argument('--ranks',type=int,required=True);p.add_argument('--source',required=True);p.add_argument('--binary',required=True)
    args=p.parse_args()
    try:require_warmup(args.directory,args.mode,args.ranks,args.source,args.binary)
    except (OSError,ValueError,KeyError,TypeError) as error:p.exit(1,str(error)+'\n')
