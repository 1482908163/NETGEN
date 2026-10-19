"""Strict transaction-local incidence construction protocol; no performance claims."""
import json
import math
import csv
from pathlib import Path

PHASES=('generation','repair','optimization')
OPS=('combine','split','swap','swap2')
FIELDS=('calls','input_points','input_elements','entries','ordered_builds','fallback_builds',
        'avoided_atomic_updates','verified','mismatches','build_seconds','reference_seconds','scratch_peak_bytes')
MODES=('original_incidence_profile_v1','ordered_incidence_v1','ordered_incidence_replay_v1')


def require_warmup(directory,mode,ranks,source,binary):
    directory=Path(directory)
    if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():
        raise ValueError('incidence quality warmup did not succeed')
    quality=json.loads((directory/'quality_summary.json').read_text())
    if not quality.get('structural_pass'):
        raise ValueError('incidence quality warmup did not pass structural audit')
    expected=MODES[0] if mode==0 else MODES[2]
    with (directory/'algorithm_certificate.csv').open() as stream:
        certificates=list(csv.DictReader(stream))
    if sorted(int(c['rank']) for c in certificates)!=list(range(ranks)):
        raise ValueError('incidence warmup certificate ranks incomplete')
    ordered=0
    for c in certificates:
        if (int(c['repeat']),int(c['ranks']),c.get('volume_incidence_build'),c['source_revision'],c['binary_sha256'])!=(0,ranks,expected,source,binary):
            raise ValueError('incidence warmup certificate mode/source mismatch')
        for phase in PHASES:
            for op in OPS:
                prefix=f'incidence_{phase}_{op}_'
                if float(c[prefix+'mismatches']) or float(c[prefix+'verified'])!=(float(c[prefix+'calls']) if mode else 0):
                    raise ValueError('incidence warmup replay incomplete')
                ordered+=float(c[prefix+'ordered_builds'])
    if mode and not ordered:
        raise ValueError('ordered incidence warmup was not exercised')


def validate(rows,metadata,result,slowest_rank):
    mode=metadata.get('volume_incidence_build')
    if mode is None:
        if any(k.startswith('incidence_') for row in rows for k in row['metrics']):
            raise ValueError('incidence metrics without protocol')
        return
    if mode not in MODES or metadata.get('kernel_scheduler')!='cost_profile':
        raise ValueError('invalid incidence construction protocol')
    if metadata.get('volume_front_distance')!='distance_original_v1' or metadata.get('volume_front_transform')!='dense_rule_operator_v1':
        raise ValueError('incidence experiment changed front algorithm')
    if mode==MODES[2] and (rows[0]['repeat']!=0 or metadata.get('mesh_quality')!='volume_audit_v1'):
        raise ValueError('incidence replay outside quality warmup')
    signature=[];complete=[]
    totals={f:0 for f in FIELDS}
    for row in rows:
        workload=[];certificate=[];m=row['metrics']
        for phase in PHASES:
            for op in OPS:
                prefix=f'incidence_{phase}_{op}_'
                v={f:m.get(prefix+f) for f in FIELDS}
                if any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0
                       or ('seconds' not in f and int(x)!=x) for f,x in v.items()):
                    raise ValueError('invalid incidence metric: '+prefix)
                calls=v['calls'];cost=f'cost_{phase}_{op}_'
                expected=m[cost+'calls']
                # Swap2 returns before preparation for non-segment conforming calls.
                if calls>expected or (op!='swap2' and calls!=expected) or (op=='swap2' and calls<expected and not m[cost+'conform_seconds']):
                    raise ValueError('incidence/native call coverage mismatch')
                if v['input_points']>m[cost+'input_points'] or v['input_elements']>m[cost+'input_elements']:
                    raise ValueError('incidence input exceeds native operation input')
                if op!='swap2' and (v['input_points']!=m[cost+'input_points'] or v['input_elements']!=m[cost+'input_elements']):
                    raise ValueError('incidence native input identity mismatch')
                if v['mismatches'] or v['ordered_builds']+v['fallback_builds']>calls:
                    raise ValueError('incidence construction conservation mismatch')
                if mode==MODES[0] and any(v[f] for f in ('ordered_builds','fallback_builds','avoided_atomic_updates','verified','reference_seconds','scratch_peak_bytes')):
                    raise ValueError('original incidence control entered candidate')
                if mode!=MODES[0] and v['ordered_builds']+v['fallback_builds']!=calls:
                    raise ValueError('incidence tasks missing or duplicated')
                if v['avoided_atomic_updates']>2*v['entries'] or (mode!=MODES[0] and not v['fallback_builds'] and v['avoided_atomic_updates']!=2*v['entries']):
                    raise ValueError('incidence atomic-work accounting mismatch')
                if v['scratch_peak_bytes']>64*1024*1024 or (not v['ordered_builds'] and v['scratch_peak_bytes']):
                    raise ValueError('incidence scratch budget exceeded')
                if mode==MODES[2] and v['verified']!=calls:
                    raise ValueError('incidence exact replay incomplete')
                if mode!=MODES[2] and (v['verified'] or v['reference_seconds']):
                    raise ValueError('incidence reference entered formal timing')
                if v['build_seconds']+v['reference_seconds']>m[cost+'prepare_seconds']+1e-5:
                    raise ValueError('incidence timing exceeds same-operation preparation')
                if not calls and any(v.values()):
                    raise ValueError('incidence work outside construction')
                workload.extend(v[f] for f in ('calls','input_points','input_elements','entries'))
                certificate.extend(v[f] for f in FIELDS)
                for f,value in v.items():
                    if f=='scratch_peak_bytes':totals[f]=max(totals[f],value)
                    else:totals[f]+=value
                    result[prefix+f]=result.get(prefix+f,0)+value if f!='scratch_peak_bytes' else max(result.get(prefix+f,0),value)
                    if row['rank']==slowest_rank:result['slowest_compute_'+prefix+f]=value
        signature.append([row['rank'],workload])
        complete.append([row['rank'],certificate])
    result['volume_incidence_build']=mode
    result['incidence_work_signature']=json.dumps(signature,separators=(',',':'))
    result['incidence_certificate_signature']=json.dumps(complete,separators=(',',':'))
    result.update({'incidence_'+f:v for f,v in totals.items()})
    for f in FIELDS:
        values=[rows[slowest_rank]['metrics'][f'incidence_{p}_{o}_{f}'] for p in PHASES for o in OPS]
        result['slowest_compute_incidence_'+f]=max(values) if f=='scratch_peak_bytes' else sum(values)


def check_certificates(certificates,run,repeat,ranks,mode):
    ordered=sorted(certificates,key=lambda c:int(c['rank']))
    if len(ordered)!=ranks or [int(c['rank']) for c in ordered]!=list(range(ranks)):
        raise ValueError('incidence certificate ranks incomplete')
    signature=[];complete=[]
    for c in ordered:
        if int(c['ranks'])!=ranks or int(c['repeat'])!=repeat or c.get('volume_incidence_build')!=mode:
            raise ValueError('incidence certificate identity mismatch')
        if c['source_revision']!=run['source_revision'] or c['binary_sha256']!=run['binary_sha256']:
            raise ValueError('incidence certificate source mismatch')
        work=[];counters=[]
        for p in PHASES:
            for o in OPS:
                prefix=f'incidence_{p}_{o}_'
                for f in FIELDS:
                    if float(c[prefix+f])<0 or not math.isfinite(float(c[prefix+f])):
                        raise ValueError('invalid incidence certificate metric')
                if float(c[prefix+'mismatches']) or float(c[prefix+'verified'])!=(float(c[prefix+'calls']) if mode==MODES[2] else 0):
                    raise ValueError('incidence certificate verification incomplete')
                work.extend(float(c[prefix+f]) for f in ('calls','input_points','input_elements','entries'))
                counters.extend(float(c[prefix+f]) for f in FIELDS)
        signature.append([int(c['rank']),work])
        complete.append([int(c['rank']),counters])
    if signature!=json.loads(run['incidence_work_signature']):
        raise ValueError('incidence certificate per-rank work mismatch')
    if complete!=json.loads(run['incidence_certificate_signature']):
        raise ValueError('incidence certificate per-rank counters mismatch')
    for p in PHASES:
        for o in OPS:
            for f in FIELDS:
                key=f'incidence_{p}_{o}_{f}';values=[float(c[key]) for c in ordered]
                expected=max(values) if f=='scratch_peak_bytes' else sum(values)
                if not math.isclose(expected,run[key],rel_tol=1e-12,abs_tol=1e-9):
                    raise ValueError('incidence certificate aggregate mismatch: '+key)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Verify incidence warmup before formal launch')
    parser.add_argument('directory',type=Path)
    parser.add_argument('--mode',type=int,choices=(0,1),required=True)
    parser.add_argument('--ranks',type=int,required=True)
    parser.add_argument('--source',required=True)
    parser.add_argument('--binary',required=True)
    args=parser.parse_args()
    try:
        require_warmup(args.directory,args.mode,args.ranks,args.source,args.binary)
    except (OSError,ValueError,KeyError,TypeError) as error:
        parser.exit(1,'Invalid incidence warmup: '+str(error)+'\n')
