#!/usr/bin/env python3
"""A1/B1/C1 paired reports. Never pool different routes into one algorithm."""
import argparse
import json
import statistics as st
from pathlib import Path
from analyze_results import inspect, profile_path, compare_quality, write_csv

ROUTES=('reference','a_static','a_dynamic','b_remaining','b_critical','c_deferred','a_fixed','a_window','b_window','c_fused','a_native','b_balanced','a_fused','b_fused','a_bound','b_bound','b_repeat','c_prefix','a_prefix','b_prefix','reference_bound','worklet_static_fixed','worklet_owner_fixed','critical_worklet','async_global','front_spatial','ready_global','spatial_ready','recovery_global','profile_global')
PAIRS=(('reference','a_static','decomposition'),('a_static','a_dynamic','execution_balance'),
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
       ('async_global','profile_global','diagnostic_overhead'))

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
                                expected={'profile_global':'profile','recovery_global':'recovery','front_spatial':'spatial','spatial_ready':'spatial','a_bound':'node_window','b_bound':'node_window_balanced','b_repeat':'node_window_repeat','a_prefix':'node_window','b_prefix':'node_window_repeat','a_native':'node_native','a_fixed':'node_fixed','a_window':'node_window',
                                          'b_window':'node_window_priority','b_balanced':'node_window_balanced',
                                          'a_fused':'node_window','b_fused':'node_window_balanced'}.get(route,'repair')
                                if expected and run.get('kernel_scheduler')!=expected:
                                    raise ValueError('route kernel scheduler mismatch')
                                numbering={'c_prefix':'prefix_neighbor_v1','a_prefix':'prefix_neighbor_v1','b_prefix':'prefix_neighbor_v1','c_fused':'fused_pair_v1','a_fused':'fused_pair_v1','b_fused':'fused_pair_v1','c_deferred':'deferred_pair_v1','async_global':'owner_local_v2','ready_global':'owner_local_v2','spatial_ready':'owner_local_v2','recovery_global':'owner_local_v2','profile_global':'owner_local_v2'}.get(route,'eager_v1')
                                if numbering and run.get('global_numbering')!=numbering:
                                    raise ValueError('route numbering mismatch')
                                if route in ('a_bound','b_bound','b_repeat','a_prefix','b_prefix','reference_bound','worklet_static_fixed','worklet_owner_fixed','critical_worklet','async_global','front_spatial','ready_global','spatial_ready','recovery_global','profile_global'):
                                    if run.get('node_cpu_bind')!='cores' or run.get('node_affinity_layout')!='disjoint':
                                        raise ValueError('bound route lacks verified disjoint startup affinity')
                                if route in ('worklet_static_fixed','worklet_owner_fixed','critical_worklet'):
                                    policy={'worklet_static_fixed':'static','worklet_owner_fixed':'dynamic','critical_worklet':'critical'}[route]
                                    if run.get('worklet_policy')!=policy:
                                        raise ValueError('route worklet policy mismatch')
                                if route in ('async_global','ready_global','spatial_ready','recovery_global','profile_global') and run.get('adjacency_audit_schema')!='canonical_ghost_v1':
                                    raise ValueError('missing temporary-ID adjacency audit contract')
                                if route in ('async_global','ready_global','spatial_ready','recovery_global','profile_global') and run.get('adjacency_id_path')!='owner_local_v2':
                                    raise ValueError('async adjacency did not use temporary IDs')
                                if route in ('front_spatial','spatial_ready') and run.get('front_search')!='conservative_boxes_v1':
                                    raise ValueError('route conservative front search mismatch')
                                if route in ('ready_global','spatial_ready') and run.get('volume_exchange_policy')!='peer_ready_v1':
                                    raise ValueError('route ready exchange mismatch')
                                if route=='recovery_global' and run.get('recovery_evaluation')!='parallel_readonly_v1':
                                    raise ValueError('missing recovery parallel contract')
                                if route=='profile_global' and run.get('volume_native_profile')!='phase_operations_v1':
                                    raise ValueError('missing phase operation profile')
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
                if candidate in ('async_global','ready_global','front_spatial','spatial_ready','recovery_global','profile_global') and qa and qb and not gate.get('same_volume_fingerprint'):
                    gate['quality_pass']=False
                    gate['quality_issues']+=';structural_owned_mesh_changed'
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
                    if not valid:errors.append(f'{control}/{candidate}/{algorithm}/{seed}/{mode}: comparison not validated ({gate["quality_issues"]}); ownership={ownership}, deterministic={deterministic}')
                    changes=[100*(1-b['core_seconds']/a['core_seconds']) for a,b in pairs]
                    comparisons.append(dict(control=control,candidate=candidate,contribution=contribution,algorithm=algorithm,
                        seed=seed,timing=mode,paired_count=len(pairs),paired_wins=sum(x>0 for x in changes),
                        control_seconds=st.median(a['core_seconds'] for a,b in pairs),
                        candidate_seconds=st.median(b['core_seconds'] for a,b in pairs),
                        control_compute_max_seconds=st.median(a['compute_max_seconds'] for a,b in pairs),
                        candidate_compute_max_seconds=st.median(b['compute_max_seconds'] for a,b in pairs),
                        control_slowest_recovery_seconds=st.median(a['slowest_compute_recovery_seconds'] for a,b in pairs) if candidate=='recovery_global' else None,
                        candidate_slowest_recovery_seconds=st.median(b['slowest_compute_recovery_seconds'] for a,b in pairs) if candidate=='recovery_global' else None,
                        recovery_exercised=all(b.get('recovery_exercised',False) for a,b in pairs) if candidate=='recovery_global' else None,
                        paired_reduction_pct=st.median(changes),paired_min_pct=min(changes),paired_max_pct=max(changes),validated=valid,
                        ownership_equal=ownership if schedule_pair else None,
                        task_mesh_equal=deterministic if schedule_pair else None,**gate))
    out=root/f'p{ranks}';out.mkdir(parents=True,exist_ok=True)
    for name,data in (('route_runs.csv',flat),('route_comparisons.csv',comparisons)):
        (out/name).write_text('');write_csv(out/name,data)
    (out/'route_issues.txt').write_text('\n'.join(errors)+('\n' if errors else ''))
    lines=[f'A1/B1/C1: {len(flat)} measured runs; {len(errors)} issues.',
           'a_window/b_window keep original domains and borrow node-local CPUs; a_static/a_dynamic/b_remaining/b_critical are historical task routes.',
           'Structural routes preserve original domains: conservative front search, peer-ready exchange, and their composition. Historical whole-domain task routes are explicit controls only.',
           'Only validated comparisons support performance claims. Decomposition quality changes need review.']
    lines.extend(f'{r["control"]} -> {r["candidate"]} {r["timing"]}: {r["paired_reduction_pct"]:.2f}% reduction; wins={r["paired_wins"]}/{r["paired_count"]}; validated={r["validated"]}' for r in comparisons)
    lines.extend(f"Recovery {r['control']} -> {r['candidate']} {r['timing']}: exercised={r['recovery_exercised']}; compute max {r['control_compute_max_seconds']:.6f} -> {r['candidate_compute_max_seconds']:.6f}s; recovery on each run's slowest rank {r['control_slowest_recovery_seconds']:.6f} -> {r['candidate_slowest_recovery_seconds']:.6f}s" for r in comparisons if r['candidate']=='recovery_global')
    if any(r['candidate']=='profile_global' for r in comparisons):
        lines.append('profile_global is an instrumentation control, not an optimization. Inspect analysis/critical_rank_breakdown.csv for same-rank phase/operation data; native timers are inclusive and must not be summed.')
    if any(r.get('recovery_exercised') is False for r in comparisons):
        lines.append('Recovery path not exercised in every pair: do not claim a recovery speedup from total-time differences.')
    (out/'ROUTE_SUMMARY.txt').write_text('\n'.join(lines)+'\n')
    print(lines[0]);return not errors

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path);parser.add_argument('--ranks',type=int,required=True)
    parser.add_argument('--routes',nargs='+',choices=ROUTES,default=list(ROUTES))
    args=parser.parse_args();raise SystemExit(0 if analyze(args.root,args.ranks,args.routes) else 1)
