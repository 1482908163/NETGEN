"""Validate complete host operation costs; export all ranks, including zeros."""
import math

PHASES=('generation','repair','optimization')
OPERATIONS=('combine','split','swap','swap2')
STAGES=('prepare_seconds','evaluate_seconds','order_seconds','commit_seconds','cleanup_seconds')
GOALS=('quality_seconds','conform_seconds','rest_seconds','worstcase_seconds','legal_seconds')
COUNTS=('calls','input_points','input_elements','evaluated_items','candidates','commit_attempts','applied','team_evaluations')
FIELDS=('total_seconds',)+STAGES+COUNTS+GOALS+('serial_evaluate_seconds','team_evaluate_seconds')

def validate(rows,metadata,result,slowest_rank,sink=None,compute=None):
    if metadata.get('volume_cost_profile')!='phase_full_cost_v1':
        raise ValueError('missing full operation cost contract')
    signature=[]
    for row in rows:
        metrics=row['metrics']
        rank=row['rank']
        workload=[]
        for phase in PHASES:
            phase_total=0
            for op in OPERATIONS:
                prefix=f'cost_{phase}_{op}_'
                v={}
                for field in FIELDS:
                    value=metrics.get(prefix+field)
                    if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0 or (field in COUNTS and int(value)!=value):
                        raise ValueError('invalid operation cost: '+prefix+field)
                    v[field]=value
                total=v['total_seconds']
                tolerance=1e-7+total*1e-6
                if abs(sum(v[f] for f in STAGES)-total)>tolerance or abs(sum(v[f] for f in GOALS)-total)>tolerance:
                    raise ValueError('operation time partitions do not match total')
                if abs(v['serial_evaluate_seconds']+v['team_evaluate_seconds']-v['evaluate_seconds'])>tolerance:
                    raise ValueError('operation evaluation team partition mismatch')
                if not (v['applied']<=v['commit_attempts']<=v['candidates']<=v['evaluated_items']):
                    raise ValueError('operation candidate/commit conservation mismatch')
                if v['team_evaluations']>v['calls'] or (v['calls']==0 and any(v.values())):
                    raise ValueError('operation call coverage mismatch')
                if v['team_evaluations']==0 and v['team_evaluate_seconds']!=0:
                    raise ValueError('operation team timing without team')
                if v['team_evaluations']==v['calls'] and v['serial_evaluate_seconds']!=0:
                    raise ValueError('operation serial timing without serial call')
                phase_total+=total
                workload.extend(v[f] for f in COUNTS)
                if rank==slowest_rank:
                    for field,value in v.items():result['slowest_compute_'+prefix+field]=value
                if sink is not None:
                    sink.append(dict(algorithm=metadata['algorithm'],timing=metadata['timing_mode'],
                        ranks=len(rows),partition_seed=int(metadata['partition_seed']),
                        repeat=row['repeat'],rank=rank,kernel_scheduler=metadata['kernel_scheduler'],
                        phase=phase,operation=op,phase_seconds=metrics['kernel_'+phase+'_seconds'],
                        core_seconds=metrics['core_seconds'],compute_seconds=compute[rank] if compute is not None else None,
                        source_revision=metadata.get('MESH_SOURCE_REVISION'),binary_sha256=metadata.get('MESH_BINARY_SHA256'),
                        kernel_sha256=metadata.get('MESH_KERNEL_SHA256'),core_sha256=metadata.get('MESH_NGCORE_SHA256','unrecorded'),
                        input_sha256=metadata.get('MESH_INPUT_SHA256'),**v))
            if phase_total>metrics['kernel_'+phase+'_seconds']+1e-5:
                raise ValueError('operation totals exceed same-rank phase')
        signature.append([rank,workload])
    import json
    result['volume_cost_profile']='phase_full_cost_v1'
    result['cost_work_signature']=json.dumps(signature,separators=(',',':'))
    result['cost_active_ranks']=sum(any(row['metrics'][f'cost_{phase}_{op}_calls'] for phase in PHASES for op in OPERATIONS) for row in rows)
