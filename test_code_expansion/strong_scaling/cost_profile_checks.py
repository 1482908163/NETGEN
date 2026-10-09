"""Validate complete host operation costs; export all ranks, including zeros."""
import math

PHASES=('generation','repair','optimization')
OPERATIONS=('combine','split','swap','swap2')
STAGES=('prepare_seconds','evaluate_seconds','order_seconds','commit_seconds','cleanup_seconds')
GOALS=('quality_seconds','conform_seconds','rest_seconds','worstcase_seconds','legal_seconds')
COUNTS=('calls','input_points','input_elements','evaluated_items','candidates','commit_attempts','applied','team_evaluations')
FIELDS=('total_seconds',)+STAGES+COUNTS+GOALS+('serial_evaluate_seconds','team_evaluate_seconds')

def validate(rows,metadata,result,slowest_rank,sink=None,compute=None):
    contract=metadata.get('volume_cost_profile')
    if contract not in ('phase_full_cost_v1','phase_full_cost_v2','phase_full_cost_v3'):
        raise ValueError('missing full operation cost contract')
    signature=[]
    gaps=0;gap_seconds=0.0;clock_gaps=0
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
                native=metrics.get(f'native_{phase}_{op}_seconds')
                if not isinstance(native,(int,float)) or not math.isfinite(native) or native<0:
                    raise ValueError('missing native operation coverage timer')
                # Independent native RegionTimers expose dropped parameter
                # objects even when all exported fields exist and sum to zero.
                coverage_tolerance=0.0002+0.00005*v['calls']+0.01*max(native,total)
                clock_match=abs(native-total)<=coverage_tolerance
                clock_gaps+=int(not clock_match)
                # v3 uses exact independent host call counts for coverage.
                # Outer steady-clock scopes and calibrated hardware timers
                # have different boundaries; their difference remains visible.
                native_calls=metrics.get(f'native_{phase}_{op}_calls')
                if contract=='phase_full_cost_v3':
                    if not isinstance(native_calls,(int,float)) or not math.isfinite(native_calls) or native_calls<0 or int(native_calls)!=native_calls:
                        raise ValueError('missing native operation call count')
                    if native_calls!=v['calls']:
                        raise ValueError(f'native operation call coverage mismatch: rank={rank} {phase}/{op}')
                    covered=True
                else:covered=clock_match
                if not covered:
                    gaps+=1;gap_seconds+=max(0,native-total)
                    if contract=='phase_full_cost_v2':
                        raise ValueError(f'native operation coverage mismatch: rank={rank} {phase}/{op} native={native} scoped={total}')
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
                        native_seconds=native,coverage_match=covered,
                        native_calls=native_calls,clock_match=clock_match,
                        core_seconds=metrics['core_seconds'],compute_seconds=compute[rank] if compute is not None else None,
                        source_revision=metadata.get('MESH_SOURCE_REVISION'),binary_sha256=metadata.get('MESH_BINARY_SHA256'),
                        kernel_sha256=metadata.get('MESH_KERNEL_SHA256'),core_sha256=metadata.get('MESH_NGCORE_SHA256','unrecorded'),
                        input_sha256=metadata.get('MESH_INPUT_SHA256'),runtime_sha256=metadata.get('MESH_RUNTIME_SHA256','unrecorded'),**v))
            if phase_total>metrics['kernel_'+phase+'_seconds']+1e-5:
                raise ValueError('operation totals exceed same-rank phase')
        signature.append([rank,workload])
    import json
    result['volume_cost_profile']=contract
    result['cost_coverage_complete']=gaps==0
    result['cost_clock_gap_count']=clock_gaps
    result['cost_coverage_gap_count']=gaps
    result['cost_coverage_gap_seconds']=gap_seconds
    result['cost_work_signature']=json.dumps(signature,separators=(',',':'))
    result['cost_active_ranks']=sum(any(row['metrics'][f'cost_{phase}_{op}_calls'] for phase in PHASES for op in OPERATIONS) for row in rows)
    validate_active_split(rows,metadata,result,slowest_rank)
    if contract=='phase_full_cost_v3':
        mode=metadata.get('volume_repair_fixedpoint')
        if mode not in ('disabled','exact_state_stop_v1','reference_verify_v1'):
            raise ValueError('missing repair fixed-point contract')
        result['volume_repair_fixedpoint']=mode
        fields=('calls','rounds','checks','stable_rounds','potential_skipped','skipped','verified','mismatches','snapshot_seconds','reference_rounds')
        totals={field:0 for field in fields}
        for row in rows:
            for phase in PHASES:
                values={f:row['metrics'].get(f'repair_fixed_{phase}_{f}') for f in fields}
                if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 or (f!='snapshot_seconds' and int(v)!=v) for f,v in values.items()):
                    raise ValueError('invalid repair fixed-point counters')
                if values['mismatches'] or values['stable_rounds']>values['checks'] or values['checks']>values['rounds'] or values['skipped']>values['potential_skipped'] or values['verified']>values['stable_rounds']:
                    raise ValueError('repair fixed-point conservation mismatch')
                if mode=='disabled' and any(values.values()):raise ValueError('disabled fixed-point route exercised')
                if mode=='reference_verify_v1' and (row['repeat']!=0 or values['skipped'] or values['verified']!=values['stable_rounds']):
                    raise ValueError('fixed-point reference verification incomplete')
                if mode=='exact_state_stop_v1' and (values['verified'] or values['reference_rounds'] or values['skipped']!=values['potential_skipped']):
                    raise ValueError('fixed-point execution contract mismatch')
                if row['rank']==slowest_rank:
                    result.update({f'slowest_compute_repair_fixed_{phase}_{f}':v for f,v in values.items()})
                for f,v in values.items():totals[f]+=v
        result.update({'repair_fixed_'+f:v for f,v in totals.items()})


ACTIVE_FIELDS=('calls','edges','active','rejected','dispatched','screen_seconds','evaluate_seconds',
    'worker_seconds','worker_max_seconds','verified','mismatches','reference_seconds')

def validate_active_split(rows,metadata,result,slowest_rank):
    mode=metadata.get('volume_split_evaluation')
    if mode is None:return # Historical profiles retain their existing contract.
    if mode not in ('original_ranges_v1','active_queue_v1','active_exact_replay_v1'):
        raise ValueError('unknown active split evaluation contract')
    totals={f:0 for f in ACTIVE_FIELDS}
    for row in rows:
        v={f:row['metrics'].get('split_active_'+f) for f in ACTIVE_FIELDS}
        for f,value in v.items():
            if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0 or (not f.endswith('_seconds') and int(value)!=value):
                raise ValueError('invalid active split counter: '+f)
        if v['active']+v['rejected']!=v['edges'] or v['mismatches'] or v['worker_max_seconds']>v['worker_seconds']+1e-7:
            raise ValueError('active split conservation mismatch')
        if v['calls']==0 and any(v.values()):raise ValueError('active split counters without calls')
        if v['calls']>sum(row['metrics'][f'cost_{p}_split_calls'] for p in PHASES) or v['edges']>sum(row['metrics'][f'cost_{p}_split_evaluated_items'] for p in PHASES):
            raise ValueError('active split exceeds native operation coverage')
        if v['screen_seconds']>v['evaluate_seconds']+1e-7 or v['worker_max_seconds']>v['evaluate_seconds']+1e-7:
            raise ValueError('active split components exceed evaluation time')
        if mode=='original_ranges_v1':
            if any(v[f] for f in ('dispatched','screen_seconds','verified','mismatches','reference_seconds')):
                raise ValueError('original split route entered active queue')
        else:
            if v['dispatched']!=v['active']:raise ValueError('active split tasks missing or duplicated')
            if mode=='active_exact_replay_v1':
                if row['repeat']!=0 or v['verified']!=v['edges']:raise ValueError('active split exact replay incomplete')
            elif v['verified'] or v['reference_seconds']:raise ValueError('active split reference entered formal run')
        if row['rank']==slowest_rank:result.update({'slowest_compute_split_active_'+f:value for f,value in v.items()})
        for f,value in v.items():totals[f]+=value
    result['volume_split_evaluation']=mode
    result.update({'split_active_'+f:v for f,v in totals.items()})
