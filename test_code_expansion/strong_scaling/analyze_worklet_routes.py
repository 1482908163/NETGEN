#!/usr/bin/env python3
"""A1/B1/C1 paired reports. Never pool different routes into one algorithm."""
import argparse
import json
import statistics as st
from pathlib import Path
from analyze_results import inspect, profile_path, compare_quality, write_csv

ROUTES=('reference','a_static','a_dynamic','b_remaining','b_critical','c_deferred','a_fixed','a_window','b_window','c_fused','a_native','b_balanced','a_fused','b_fused','a_bound','b_bound','b_repeat','c_prefix','a_prefix','b_prefix','reference_bound','worklet_static_fixed','worklet_owner_fixed','critical_worklet','async_global','front_spatial','ready_global','spatial_ready','recovery_global','profile_global','refine_serial','refine_parallel','legal_prune','batch_serial','batch_parallel','cost_profile','combine_profile','combine_waves','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced')
PAIRS=(('batch_parallel','cost_profile','diagnostic_overhead'),('reference','a_static','decomposition'),('a_static','a_dynamic','execution_balance'),
       ('a_dynamic','b_remaining','remaining_work'),('b_remaining','b_critical','sync_criticality'),
       ('reference','c_deferred','deferred_numbering'),
       ('reference','a_dynamic','end_to_end'),('reference','b_remaining','end_to_end'),
       ('reference','b_critical','end_to_end'),
       ('reference','a_fixed','resource_setup'),('a_fixed','a_window','window_borrowing'),
       ('a_window','b_window','net_priority'),('reference','a_window','end_to_end'),
       ('reference','b_window','end_to_end'),('reference','c_fused','count_fusion'),
       ('c_fused','c_deferred','count_overlap'),
       ('reference','a_native','affinity_and_setup'),('a_native','a_fixed','grouped_lifecycle'),
       ('a_window','b_balanced','tail_allocation'),('b_window','b_balanced','priority_vs_tail'),
       ('reference','b_balanced','end_to_end'),
       ('a_window','a_fused','fusion_on_a'),('b_balanced','b_fused','fusion_on_b'),
       ('c_fused','a_fused','a_on_fusion'),('a_fused','b_fused','tail_on_fusion'),
       ('reference','a_fused','end_to_end'),('reference','b_fused','end_to_end'),
       ('a_window','a_bound','startup_affinity'),('a_bound','b_bound','bound_tail_share'),
       ('b_bound','b_repeat','second_donor'),('c_fused','c_prefix','distributed_prefix'),
       ('b_repeat','b_prefix','prefix_on_b'),('c_prefix','b_prefix','b_on_prefix'),
       ('a_bound','a_prefix','prefix_on_a'),('a_prefix','b_prefix','b_on_a_prefix'),
       ('reference','a_bound','end_to_end'),('reference','b_bound','end_to_end'),
       ('reference','b_repeat','end_to_end'),('reference','c_prefix','end_to_end'),
       ('reference','a_prefix','end_to_end'),('reference','b_prefix','end_to_end'),
       ('reference','reference_bound','startup_affinity_baseline'),
       ('reference_bound','worklet_static_fixed','task_dispatch_overhead'),
       ('worklet_static_fixed','worklet_owner_fixed','execution_balance'),
       ('reference_bound','worklet_owner_fixed','owner_fixed_worklet'),
       ('worklet_owner_fixed','critical_worklet','sync_criticality'),
       ('reference_bound','critical_worklet','end_to_end'),
       ('reference_bound','async_global','deferred_globalization'),
       ('reference_bound','front_spatial','front_search_culling'),
       ('async_global','ready_global','peer_readiness'),
       ('ready_global','spatial_ready','front_search_on_ready'),
       ('reference_bound','spatial_ready','structural_total'),
       ('async_global','recovery_global','recovery_readonly_parallel'),
       ('reference_bound','recovery_global','recovery_total'),
       ('async_global','profile_global','diagnostic_overhead'),
       ('async_global','refine_serial','deterministic_bulk_structure'),
       ('refine_serial','refine_parallel','refinement_parallelism'),
       ('async_global','refine_parallel','refinement_total'),
       ('refine_serial','legal_prune','exact_split_rejection'),
       ('async_global','legal_prune','bulk_and_exact_split_rejection'),
       ('refine_serial','batch_serial','recovery_batch_instrumentation'),
       ('batch_serial','batch_parallel','native_recovery_batch'),
       ('refine_serial','batch_parallel','native_recovery_total'),
       ('batch_parallel','smooth_profile','smoothing_diagnostic_overhead'),
       ('smooth_profile','smooth_balanced','active_star_dispatch'),
       ('batch_parallel','smooth_balanced','smoothing_total'),
       ('batch_parallel','split_profile','split_diagnostic_overhead'),
       ('split_profile','split_reuse','split_proposal_reuse'),
       ('batch_parallel','split_reuse','split_total'),
       ('batch_parallel','front_profile','front_diagnostic_overhead'),
       ('front_profile','front_topology','front_topology_index'),
       ('batch_parallel','front_topology','front_total'),
       ('batch_parallel','front_bound_profile','front_bound_diagnostic_overhead'),
       ('front_bound_profile','front_bound','front_quality_bound'),
       ('batch_parallel','front_bound','front_bound_total'),
       ('batch_parallel','combine_profile','combine_diagnostic_overhead'),
       ('combine_profile','combine_waves','ordered_cavity_commits'),
       ('batch_parallel','combine_waves','combine_total'),
       ('batch_parallel','ghost_staged','ghost_plan_overhead'),
       ('ghost_staged','ghost_pipeline','ghost_dependency_overlap'),
       ('batch_parallel','ghost_pipeline','ghost_total'))

def analyze(root,ranks,selected=ROUTES):
    indexed={};qualities={};errors=[];flat=[];plans={}
    for route in selected:
        folder=root/('route_'+route)/f'p{ranks}'
        try:
            plan=json.loads((folder/'plan.json').read_text());plans[route]=plan
            if plan['ranks']!=ranks or not plan.get('quality_warmup'):
                raise ValueError('missing mandatory quality plan')
            for algorithm in plan['algorithms']:
                for seed in plan['partition_seeds']:
                    for mode in plan['timings']:
                        stem=f'{algorithm}_{mode}' if seed==-1 else f'{algorithm}_seed{seed}_{mode}'
                        repeats=range(0 if mode=='natural' else 1,plan['repeats']+1)
                        for repeat in repeats:
                            directory=folder/stem/f'repeat_{repeat}'
                            try:
                                if not (directory/'SUCCESS').is_file():
                                    failure=directory/'failure_reason.txt'
                                    detail=failure.read_text(errors='replace')[:4096] if failure.exists() else 'failure_reason.txt missing'
                                    raise ValueError('not successfully finalized: '+detail)
                                run,_=inspect(profile_path(directory))
                                if (run['ranks'],run['algorithm'],run['partition_seed'],run['repeat'],run['timing'])!=(ranks,algorithm,seed,repeat,mode):
                                    raise ValueError('run identity differs from plan')
                                expected={'cost_profile':'cost_profile','combine_profile':'combine_profile','combine_waves':'combine_waves','batch_serial':'batch_serial','ghost_staged':'batch_parallel','ghost_pipeline':'batch_parallel','batch_parallel':'batch_parallel','front_bound_profile':'front_bound_profile','front_bound':'front_bound','front_profile':'front_profile','front_topology':'front_topology','split_profile':'split_profile','split_reuse':'split_reuse','smooth_profile':'smooth_profile','smooth_balanced':'smooth_balanced','legal_prune':'legal_prune','refine_serial':'refine_serial','refine_parallel':'refine_parallel','profile_global':'profile','recovery_global':'recovery','front_spatial':'spatial','spatial_ready':'spatial','a_bound':'node_window','b_bound':'node_window_balanced','b_repeat':'node_window_repeat','a_prefix':'node_window','b_prefix':'node_window_repeat','a_native':'node_native','a_fixed':'node_fixed','a_window':'node_window',
                                          'b_window':'node_window_priority','b_balanced':'node_window_balanced',
                                          'a_fused':'node_window','b_fused':'node_window_balanced'}.get(route,'repair')
                                if expected and run.get('kernel_scheduler')!=expected:
                                    raise ValueError('route kernel scheduler mismatch')
                                numbering={'cost_profile':'owner_local_v2','combine_profile':'owner_local_v2','combine_waves':'owner_local_v2','batch_serial':'owner_local_v2','ghost_staged':'owner_local_v2','ghost_pipeline':'owner_local_v2','batch_parallel':'owner_local_v2','front_bound_profile':'owner_local_v2','front_bound':'owner_local_v2','front_profile':'owner_local_v2','front_topology':'owner_local_v2','split_profile':'owner_local_v2','split_reuse':'owner_local_v2','smooth_profile':'owner_local_v2','smooth_balanced':'owner_local_v2','legal_prune':'owner_local_v2','c_prefix':'prefix_neighbor_v1','a_prefix':'prefix_neighbor_v1','b_prefix':'prefix_neighbor_v1','c_fused':'fused_pair_v1','a_fused':'fused_pair_v1','b_fused':'fused_pair_v1','c_deferred':'deferred_pair_v1','async_global':'owner_local_v2','ready_global':'owner_local_v2','spatial_ready':'owner_local_v2','recovery_global':'owner_local_v2','profile_global':'owner_local_v2','refine_serial':'owner_local_v2','refine_parallel':'owner_local_v2'}.get(route,'eager_v1')
                                ghost_expected={'ghost_staged':'staged_plan_v1','ghost_pipeline':'id_overlap_plan_v1'}.get(route,'legacy_v1')
                                if run.get('ghost_exchange_policy')!=ghost_expected:
                                    raise ValueError('route ghost exchange policy mismatch')
                                if numbering and run.get('global_numbering')!=numbering:
                                    raise ValueError('route numbering mismatch')
                                if route in ('a_bound','b_bound','b_repeat','a_prefix','b_prefix','reference_bound','worklet_static_fixed','worklet_owner_fixed','critical_worklet','async_global','front_spatial','ready_global','spatial_ready','recovery_global','profile_global','refine_serial','refine_parallel','legal_prune','batch_serial','batch_parallel','cost_profile','combine_profile','combine_waves','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced'):
                                    if run.get('node_cpu_bind')!='cores' or run.get('node_affinity_layout')!='disjoint':
                                        raise ValueError('bound route lacks verified disjoint startup affinity')
                                if route in ('worklet_static_fixed','worklet_owner_fixed','critical_worklet'):
                                    policy={'worklet_static_fixed':'static','worklet_owner_fixed':'dynamic','critical_worklet':'critical'}[route]
                                    if run.get('worklet_policy')!=policy:
                                        raise ValueError('route worklet policy mismatch')
                                if route in ('async_global','ready_global','spatial_ready','recovery_global','profile_global','refine_serial','refine_parallel','legal_prune','batch_serial','batch_parallel','cost_profile','combine_profile','combine_waves','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced') and run.get('adjacency_audit_schema')!='canonical_ghost_v1':
                                    raise ValueError('missing temporary-ID adjacency audit contract')
                                if route in ('async_global','ready_global','spatial_ready','recovery_global','profile_global','refine_serial','refine_parallel','legal_prune','batch_serial','batch_parallel','cost_profile','combine_profile','combine_waves','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced') and run.get('adjacency_id_path')!='owner_local_v2':
                                    raise ValueError('async adjacency did not use temporary IDs')
                                if route in ('front_spatial','spatial_ready') and run.get('front_search')!='conservative_boxes_v1':
                                    raise ValueError('route conservative front search mismatch')
                                if route in ('ready_global','spatial_ready') and run.get('volume_exchange_policy')!='peer_ready_v1':
                                    raise ValueError('route ready exchange mismatch')
                                if route=='recovery_global' and run.get('recovery_evaluation')!='parallel_readonly_v1':
                                    raise ValueError('missing recovery parallel contract')
                                if route=='profile_global' and run.get('volume_native_profile')!='phase_operations_v1':
                                    raise ValueError('missing phase operation profile')
                                if route in ('combine_profile','combine_waves'):
                                    if repeat==0:
                                        (directory/'combine_verification_summary.json').write_text(json.dumps({k:v for k,v in run.items() if k.startswith('combine_commit_')},indent=2,allow_nan=False)+'\n')
                                if repeat==0:
                                    q=run['mesh_quality'];qualities[route,algorithm,seed]=q
                                    if not q['structural_pass']:raise ValueError('mesh structural audit failed')
                                else:
                                    indexed[route,algorithm,seed,mode,repeat]=run
                                    flat.append(dict(route=route,**run))
                            except (OSError,ValueError,KeyError,TypeError) as exc:
                                errors.append(f'{directory}: {exc}')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            errors.append(f'{folder}: {exc}')
    comparisons=[]
    for control,candidate,contribution in PAIRS:
        if control not in plans or candidate not in plans:continue
        if plans[control]!=plans[candidate]:errors.append(f'{control}/{candidate}: experiment plans differ');continue
        plan=plans[control]
        for algorithm in plan['algorithms']:
            for seed in plan['partition_seeds']:
                qa=qualities.get((control,algorithm,seed));qb=qualities.get((candidate,algorithm,seed))
                gate=compare_quality(qa,qb) if qa and qb else dict(quality_pass=False,quality_issues='missing_audit')
                if candidate in ('async_global','ready_global','front_spatial','spatial_ready','recovery_global','profile_global','refine_serial','refine_parallel','legal_prune','batch_serial','batch_parallel','cost_profile','combine_profile','combine_waves','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced') and qa and qb and not gate.get('same_volume_fingerprint'):
                    gate['quality_pass']=False
                    gate['quality_issues']+=';structural_owned_mesh_changed'
                if control=='ghost_staged' and candidate=='ghost_pipeline' and qa and qb:
                    for mode in plan['timings']:
                        for repeat in range(1,plan['repeats']+1):
                            a=indexed.get((control,algorithm,seed,mode,repeat),{});b=indexed.get((candidate,algorithm,seed,mode,repeat),{})
                            if a.get('ghost_plan_coverage_signature')!=b.get('ghost_plan_coverage_signature'):
                                errors.append('ghost plan per-rank work changed')
                                gate['quality_pass']=False
                                gate['quality_issues']+=';ghost_plan_coverage_changed'
                if candidate=='cost_profile' and qa and qb:
                    # Timing instrumentation must preserve every quality field,
                    # including per-rank records, not just an aggregate hash.
                    if {k:v for k,v in qa.items() if k!='scheduler'}!={k:v for k,v in qb.items() if k!='scheduler'}:
                        gate['quality_pass']=False
                        gate['quality_issues']+=';full_cost_diagnostic_changed_mesh'
                # Splitting changes interior meshing. Do not silently waive a
                # conservative quality nonregression failure to claim a speedup.
                for mode in plan['timings']:
                    pairs=[(indexed[control,algorithm,seed,mode,i],indexed[candidate,algorithm,seed,mode,i])
                           for i in range(1,plan['repeats']+1)
                           if (control,algorithm,seed,mode,i) in indexed and (candidate,algorithm,seed,mode,i) in indexed]
                    if not pairs:continue
                    ownership=all(a.get('ownership_signature')==b.get('ownership_signature') for a,b in pairs)
                    deterministic=all(a.get('task_signature')==b.get('task_signature') for a,b in pairs)
                    # reference -> static intentionally changes decomposition.
                    schedule_pair=(control in ('a_static','a_dynamic','b_remaining') and candidate in ('a_dynamic','b_remaining','b_critical')) or (control in ('worklet_static_fixed','worklet_owner_fixed') and candidate in ('worklet_owner_fixed','critical_worklet'))
                    valid=gate['quality_pass'] and len(pairs)==plan['repeats'] and (not schedule_pair or (ownership and deterministic))
                    if control=='combine_profile' and candidate=='combine_waves':
                        if any(a.get('combine_commit_coverage_signature')!=b.get('combine_commit_coverage_signature') for a,b in pairs):
                            valid=False
                            errors.append(f'{control}/{candidate}/{seed}/{mode}: combine per-rank work mismatch')
                    if candidate in ('ghost_staged','ghost_pipeline'):
                        if any(a.get('ghost_payload_coverage_signature')!=b.get('ghost_payload_coverage_signature') for a,b in pairs):
                            valid=False
                            errors.append(f'{control}/{candidate}/{seed}/{mode}: per-rank ghost/vertex payload work changed')
                    if control=='front_bound_profile' and candidate=='front_bound':
                        if any(a.get('front_bound_coverage_signature')!=b.get('front_bound_coverage_signature') for a,b in pairs):
                            valid=False
                            errors.append(f'{control}/{candidate}/{seed}/{mode}: front candidate mapping coverage changed')
                    if control=='front_profile' and candidate=='front_topology':
                        coverage=('calls','mappings','admitted_rules')
                        if any(a.get('front_match_'+k)!=b.get('front_match_'+k) for a,b in pairs for k in coverage) or any(a.get('front_match_coverage_signature')!=b.get('front_match_coverage_signature') for a,b in pairs):
                            valid=False
                            errors.append(f'{control}/{candidate}/{seed}/{mode}: front matching coverage changed')
                    if control=='split_profile' and candidate=='split_reuse':
                        coverage=('calls','candidates','proposals','attempts','applied')
                        if any(a.get('split_proposal_'+k)!=b.get('split_proposal_'+k) for a,b in pairs for k in coverage):
                            valid=False
                            errors.append(f'{control}/{candidate}/{seed}/{mode}: split work coverage changed')
                    if control=='smooth_profile' and candidate=='smooth_balanced':
                        coverage=('calls','point_visits','active_visits','color_waves')
                        if any(a.get('smooth_balance_'+k)!=b.get('smooth_balance_'+k) for a,b in pairs for k in coverage):
                            valid=False
                            errors.append(f'{control}/{candidate}/{seed}/{mode}: final smoothing work coverage changed')
                    if not valid:errors.append(f'{control}/{candidate}/{algorithm}/{seed}/{mode}: comparison not validated ({gate["quality_issues"]}); ownership={ownership}, deterministic={deterministic}')
                    changes=[100*(1-b['core_seconds']/a['core_seconds']) for a,b in pairs]
                    comparisons.append(dict(control=control,candidate=candidate,contribution=contribution,algorithm=algorithm,
                        seed=seed,timing=mode,paired_count=len(pairs),paired_wins=sum(x>0 for x in changes),
                        control_seconds=st.median(a['core_seconds'] for a,b in pairs),
                        candidate_seconds=st.median(b['core_seconds'] for a,b in pairs),
                        control_adjacency_exchange_stage_sum_seconds=st.median(a['adjacency_exchange_stage_sum_max_seconds'] for a,b in pairs),
                        candidate_adjacency_exchange_stage_sum_seconds=st.median(b['adjacency_exchange_stage_sum_max_seconds'] for a,b in pairs),
                        control_compute_max_seconds=st.median(a['compute_max_seconds'] for a,b in pairs),
                        candidate_compute_max_seconds=st.median(b['compute_max_seconds'] for a,b in pairs),
                        combine_waves_exercised=all(b.get('combine_commit_parallel_attempts',0)>0 for a,b in pairs) if candidate=='combine_waves' else None,
                        control_slowest_combine_commit_seconds=st.median(a.get('slowest_compute_combine_commit_commit_seconds',0) for a,b in pairs) if control=='combine_profile' else None,
                        candidate_slowest_combine_commit_seconds=st.median(b.get('slowest_compute_combine_commit_commit_seconds',0) for a,b in pairs) if candidate=='combine_waves' else None,
                        control_slowest_recovery_seconds=st.median(a['slowest_compute_recovery_seconds'] for a,b in pairs) if candidate in ('recovery_global','batch_serial','batch_parallel','cost_profile','combine_profile','combine_waves','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced') else None,
                        candidate_slowest_recovery_seconds=st.median(b['slowest_compute_recovery_seconds'] for a,b in pairs) if candidate in ('recovery_global','batch_serial','batch_parallel','cost_profile','combine_profile','combine_waves','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced') else None,
                        candidate_recovery_active_ranks=st.median(b.get('recovery_active_ranks',0) for a,b in pairs) if candidate in ('batch_serial','batch_parallel','cost_profile','combine_profile','combine_waves','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced') else None,
                        candidate_recovery_point_work_ranks=st.median(b.get('recovery_batch_point_work_ranks',0) for a,b in pairs) if candidate in ('batch_serial','batch_parallel','cost_profile','combine_profile','combine_waves','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced') else None,
                        recovery_batch_exercised=all(b.get('recovery_batch_exercised',False) for a,b in pairs) if candidate in ('batch_serial','batch_parallel','cost_profile','combine_profile','combine_waves','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced') else None,
                        legal_split_exercised=all(b.get('legal_split_exercised',False) for a,b in pairs) if candidate=='legal_prune' else None,
                        refinement_exercised=all(b.get('refinement_exercised',False) for a,b in pairs) if candidate in ('refine_serial','refine_parallel','legal_prune','batch_serial','batch_parallel','cost_profile','combine_profile','combine_waves','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced') else None,
                        control_slowest_refine_seconds=st.median(a.get('slowest_compute_volume_refine_seconds',0) for a,b in pairs),
                        candidate_slowest_refine_seconds=st.median(b.get('slowest_compute_volume_refine_seconds',0) for a,b in pairs),
                        recovery_exercised=all(b.get('recovery_exercised',False) for a,b in pairs) if candidate in ('recovery_global','batch_serial','batch_parallel','cost_profile','combine_profile','combine_waves','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced') else None,
                        control_slowest_smooth_seconds=st.median(a['slowest_compute_smooth_balance_seconds'] for a,b in pairs) if all('slowest_compute_smooth_balance_seconds' in a for a,b in pairs) else None,
                        candidate_slowest_smooth_seconds=st.median(b.get('slowest_compute_smooth_balance_seconds',0) for a,b in pairs) if candidate in ('smooth_profile','smooth_balanced') else None,
                        smooth_exercised=all(b.get('smooth_balance_active_visits',0)>0 for a,b in pairs) if candidate in ('smooth_profile','smooth_balanced') else None,
                        front_bound_exercised=all(b.get('front_bound_quality_pruned',0)+b.get('front_bound_topology_pruned',0)>0 for a,b in pairs) if candidate=='front_bound' else None,
                        control_slowest_front_bound_seconds=st.median(a['slowest_compute_front_bound_seconds'] for a,b in pairs) if control=='front_bound_profile' else None,
                        candidate_slowest_front_bound_seconds=st.median(b['slowest_compute_front_bound_seconds'] for a,b in pairs) if candidate in ('front_bound_profile','front_bound') else None,
                        control_front_geometry_candidates=st.median(a['front_bound_geometry_candidates'] for a,b in pairs) if control=='front_bound_profile' else None,
                        candidate_front_geometry_candidates=st.median(b['front_bound_geometry_candidates'] for a,b in pairs) if candidate=='front_bound' else None,
                        control_front_point_tests=st.median(a['front_bound_point_tests'] for a,b in pairs) if control=='front_bound_profile' else None,
                        candidate_front_point_tests=st.median(b['front_bound_point_tests'] for a,b in pairs) if candidate=='front_bound' else None,
                        control_front_face_tests=st.median(a['front_bound_face_tests'] for a,b in pairs) if control=='front_bound_profile' else None,
                        candidate_front_face_tests=st.median(b['front_bound_face_tests'] for a,b in pairs) if candidate=='front_bound' else None,
                        control_front_objective_elements=st.median(a['front_bound_objective_elements'] for a,b in pairs) if control=='front_bound_profile' else None,
                        candidate_front_objective_elements=st.median(b['front_bound_objective_elements'] for a,b in pairs) if candidate=='front_bound' else None,
                        front_topology_exercised=all(b.get('front_match_queries',0)>0 for a,b in pairs) if candidate=='front_topology' else None,
                        control_slowest_front_match_seconds=st.median(a['slowest_compute_front_match_seconds'] for a,b in pairs) if control=='front_profile' else None,
                        candidate_slowest_front_match_seconds=st.median(b['slowest_compute_front_match_seconds'] for a,b in pairs) if candidate in ('front_profile','front_topology') else None,
                        control_front_visits=st.median(a['front_match_linear_visits'] for a,b in pairs) if control=='front_profile' else None,
                        candidate_front_visits=st.median(b['front_match_linear_visits']+b['front_match_indexed_visits'] for a,b in pairs) if candidate=='front_topology' else None,
                        split_reuse_exercised=all(b.get('split_proposal_reused',0)>0 for a,b in pairs) if candidate=='split_reuse' else None,
                        control_slowest_split_commit_seconds=st.median(a['slowest_compute_split_proposal_commit_seconds'] for a,b in pairs) if control=='split_profile' else None,
                        candidate_slowest_split_commit_seconds=st.median(b['slowest_compute_split_proposal_commit_seconds'] for a,b in pairs) if candidate in ('split_profile','split_reuse') else None,
                        paired_reduction_pct=st.median(changes),paired_min_pct=min(changes),paired_max_pct=max(changes),validated=valid,
                        ownership_equal=ownership if schedule_pair else None,
                        task_mesh_equal=deterministic if schedule_pair else None,**gate))
    diversity=[]
    for route,plan in plans.items():
        seeds=plan['partition_seeds']
        if len(seeds)<2:continue
        for algorithm in plan['algorithms']:
            reports=[qualities.get((route,algorithm,seed)) for seed in seeds]
            signatures=set()
            for q in reports:
                if q and q['structural_pass']:
                    signature=[(r['rank'],r['surface_sum'],r['surface_xor'])
                               for r in sorted(q['per_rank'],key=lambda r:r['rank'])]
                    signatures.add(json.dumps(signature,sort_keys=True))
            diversity.append(dict(route=route,algorithm=algorithm,requested_seeds=len(seeds),
                valid_seed_reports=sum(bool(q and q['structural_pass']) for q in reports),
                distinct_surface_assignments=len(signatures)))
    out=root/f'p{ranks}';out.mkdir(parents=True,exist_ok=True)
    (out/'partition_diversity.csv').write_text('')
    write_csv(out/'partition_diversity.csv',diversity)
    for name,data in (('route_runs.csv',flat),('route_comparisons.csv',comparisons)):
        (out/name).write_text('');write_csv(out/name,data)
    (out/'route_issues.txt').write_text('\n'.join(errors)+('\n' if errors else ''))
    lines=[f'A1/B1/C1: {len(flat)} measured runs; {len(errors)} issues.',
           'a_window/b_window keep original domains and borrow node-local CPUs; a_static/a_dynamic/b_remaining/b_critical are historical task routes.',
           'Structural routes preserve original domains: conservative front search, peer-ready exchange, and their composition. Historical whole-domain task routes are explicit controls only.',
           'Only validated comparisons support performance claims. Decomposition quality changes need review.']
    lines.extend(f'{r["control"]} -> {r["candidate"]} seed={r["seed"]} {r["timing"]}: {r["paired_reduction_pct"]:.2f}% reduction; wins={r["paired_wins"]}/{r["paired_count"]}; validated={r["validated"]}' for r in comparisons)
    if 'cost_profile' in plans:
        lines.append('全成本诊断沿用 batch_parallel 算法；比较只用于衡量诊断开销，不能作为算法加速。所有进程的准备/评价/排序/提交/收尾见 route_cost_profile/p*/analysis/volume_operation_costs.csv；输入点/单元是逐调用累计输入量，不能当唯一工作量。')
    lines.extend(f"Recovery {r['control']} -> {r['candidate']} seed={r['seed']} {r['timing']}: exercised={r['recovery_exercised']}; compute max {r['control_compute_max_seconds']:.6f} -> {r['candidate_compute_max_seconds']:.6f}s; recovery on each run's slowest rank {r['control_slowest_recovery_seconds']:.6f} -> {r['candidate_slowest_recovery_seconds']:.6f}s" for r in comparisons if r['candidate']=='recovery_global')
    lines.extend(f"边合并 {r['control']} -> {r['candidate']} seed={r['seed']} {r['timing']}: 配对核心降时={r['paired_reduction_pct']:.2f}%；有效并行覆盖={r['combine_waves_exercised']}（各次最慢进程可不同，固定进程详见明细）" for r in comparisons if r['candidate'] in ('combine_profile','combine_waves'))
    lines.extend(f"幽灵依赖 {r['control']} -> {r['candidate']} seed={r['seed']} {r['timing']}: 核心 {r['control_seconds']:.6f} -> {r['candidate_seconds']:.6f}s；同进程交换阶段合计最大值 {r['control_adjacency_exchange_stage_sum_seconds']:.6f} -> {r['candidate_adjacency_exchange_stage_sum_seconds']:.6f}s；质量/覆盖={r['validated']}" for r in comparisons if r['candidate'] in ('ghost_staged','ghost_pipeline'))
    lines.append('幽灵计划：提前打包/数量发送是依赖拆分机会；是否存在实际传输重叠须由整段耗时验证，不能相减不同慢进程的最大值推算纯通信。')
    lines.extend(f"Refinement {r['control']} -> {r['candidate']} seed={r['seed']} {r['timing']}: exercised={r['refinement_exercised']}; compute max {r['control_compute_max_seconds']:.6f} -> {r['candidate_compute_max_seconds']:.6f}s; refine on each run's slowest rank {r['control_slowest_refine_seconds']:.6f} -> {r['candidate_slowest_refine_seconds']:.6f}s" for r in comparisons if r['candidate'] in ('refine_serial','refine_parallel','legal_prune','batch_serial','batch_parallel','cost_profile','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced'))
    lines.extend(f"Exact split rejection {r['timing']}: exercised={r['legal_split_exercised']}" for r in comparisons if r['candidate']=='legal_prune')
    if any(r.get('legal_split_exercised') is False for r in comparisons):
        lines.append('No rejected split candidates in every pair: no pruning speedup claim is supported.')
    lines.extend(f"最终平滑 seed={r['seed']} {r['timing']}: 有效点覆盖={r['smooth_exercised']}; 各次最慢进程平滑时间中位数={r['control_slowest_smooth_seconds']:.6f}->{r['candidate_slowest_smooth_seconds']:.6f}s" for r in comparisons if r['control']=='smooth_profile' and r['candidate']=='smooth_balanced')
    lines.extend(f"前沿质量剪枝 seed={r['seed']} {r['timing']}: 实际启用={r['front_bound_exercised']}; 各次最慢进程匹配时间中位数={r['control_slowest_front_bound_seconds']:.6f}->{r['candidate_slowest_front_bound_seconds']:.6f}s（不代表同一固定进程）; 全局候选/点包含/面相交/质量评价中位数={r['control_front_geometry_candidates']}->{r['candidate_front_geometry_candidates']} / {r['control_front_point_tests']}->{r['candidate_front_point_tests']} / {r['control_front_face_tests']}->{r['candidate_front_face_tests']} / {r['control_front_objective_elements']}->{r['candidate_front_objective_elements']}" for r in comparisons if r['control']=='front_bound_profile' and r['candidate']=='front_bound')
    lines.extend(f"前沿拓扑索引 seed={r['seed']} {r['timing']}: 实际启用={r['front_topology_exercised']}; 最慢进程匹配时间={r['control_slowest_front_match_seconds']:.6f}->{r['candidate_slowest_front_match_seconds']:.6f}s; 全局枚举访问={r['control_front_visits']}->{r['candidate_front_visits']}" for r in comparisons if r['control']=='front_profile' and r['candidate']=='front_topology')
    lines.extend(f"分裂候选复用 seed={r['seed']} {r['timing']}: 实际复用={r['split_reuse_exercised']}; 各次最慢进程串行提交中位数={r['control_slowest_split_commit_seconds']:.6f}->{r['candidate_slowest_split_commit_seconds']:.6f}s" for r in comparisons if r['control']=='split_profile' and r['candidate']=='split_reuse')
    lines.extend(f"Recovery batch {r['control']} -> {r['candidate']} seed={r['seed']} {r['timing']}: exercised={r['recovery_batch_exercised']}" for r in comparisons if r['candidate'] in ('batch_serial','batch_parallel','cost_profile','ghost_staged','ghost_pipeline','front_bound_profile','front_bound','front_profile','front_topology','split_profile','split_reuse','smooth_profile','smooth_balanced'))
    lines.extend(f"恢复覆盖 seed={r['seed']} {r['timing']}: 活跃进程={r['candidate_recovery_active_ranks']}/{ranks}; 有点优化工作的进程={r['candidate_recovery_point_work_ranks']}/{ranks}; 各次最慢进程恢复时间中位数={r['control_slowest_recovery_seconds']:.6f}->{r['candidate_slowest_recovery_seconds']:.6f}s" for r in comparisons if r['candidate']=='batch_parallel')
    lines.extend(f"分区多样性 {d['route']}: 有效种子报告={d['valid_seed_reports']}/{d['requested_seeds']}; 不同逐进程表面分配指纹={d['distinct_surface_assignments']}。相同指纹不能视为独立分区覆盖。" for d in diversity)
    if any(r.get('recovery_batch_exercised') is False for r in comparisons):
        lines.append('Recovery point work not exercised in every pair: no batch recovery speedup claim is supported.')
    if any(r.get('refinement_exercised') is False for r in comparisons):
        lines.append('Refinement not exercised: no refinement speedup claim is supported.')
    if any(r['candidate']=='profile_global' for r in comparisons):
        lines.append('profile_global is an instrumentation control, not an optimization. Inspect analysis/critical_rank_breakdown.csv for same-rank phase/operation data; native timers are inclusive and must not be summed.')
    if any(r.get('combine_waves_exercised') is False for r in comparisons):
        lines.append('边合并并行批次未覆盖全部配对，不能把核心波动归为提交并行收益。')
    if any(r.get('recovery_exercised') is False for r in comparisons):
        lines.append('Recovery path not exercised in every pair: do not claim a recovery speedup from total-time differences.')
    (out/'ROUTE_SUMMARY.txt').write_text('\n'.join(lines)+'\n')
    print(lines[0]);return not errors

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path);parser.add_argument('--ranks',type=int,required=True)
    parser.add_argument('--routes',nargs='+',choices=ROUTES,default=list(ROUTES))
    args=parser.parse_args();raise SystemExit(0 if analyze(args.root,args.ranks,args.routes) else 1)
