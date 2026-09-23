#!/usr/bin/env python3
"""A1/B1/C1 paired reports. Never pool different routes into one algorithm."""
import argparse
import json
import statistics as st
from pathlib import Path
from analyze_results import inspect, profile_path, compare_quality, write_csv

ROUTES=('reference','a_static','a_dynamic','b_remaining','b_critical','c_deferred','a_fixed','a_window','b_window','c_fused','a_native','b_balanced','a_fused','b_fused','a_bound','b_bound','b_repeat','c_prefix','a_prefix','b_prefix')
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
       ('reference','a_prefix','end_to_end'),('reference','b_prefix','end_to_end'))

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
                                expected={'a_bound':'node_window','b_bound':'node_window_balanced','b_repeat':'node_window_repeat','a_prefix':'node_window','b_prefix':'node_window_repeat','a_native':'node_native','a_fixed':'node_fixed','a_window':'node_window',
                                          'b_window':'node_window_priority','b_balanced':'node_window_balanced',
                                          'a_fused':'node_window','b_fused':'node_window_balanced'}.get(route,'repair')
                                if expected and run.get('kernel_scheduler')!=expected:
                                    raise ValueError('route kernel scheduler mismatch')
                                numbering={'c_prefix':'prefix_neighbor_v1','a_prefix':'prefix_neighbor_v1','b_prefix':'prefix_neighbor_v1','c_fused':'fused_pair_v1','a_fused':'fused_pair_v1','b_fused':'fused_pair_v1','c_deferred':'deferred_pair_v1'}.get(route,'eager_v1')
                                if numbering and run.get('global_numbering')!=numbering:
                                    raise ValueError('route numbering mismatch')
                                if route in ('a_bound','b_bound','b_repeat','a_prefix','b_prefix'):
                                    if run.get('node_cpu_bind')!='cores' or run.get('node_affinity_layout')!='disjoint':
                                        raise ValueError('bound route lacks verified disjoint startup affinity')
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
                    schedule_pair=control in ('a_static','a_dynamic','b_remaining') and candidate in ('a_dynamic','b_remaining','b_critical')
                    valid=gate['quality_pass'] and len(pairs)==plan['repeats'] and (not schedule_pair or (ownership and deterministic))
                    if not valid:errors.append(f'{control}/{candidate}/{algorithm}/{seed}/{mode}: comparison not validated ({gate["quality_issues"]}); ownership={ownership}, deterministic={deterministic}')
                    changes=[100*(1-b['core_seconds']/a['core_seconds']) for a,b in pairs]
                    comparisons.append(dict(control=control,candidate=candidate,contribution=contribution,algorithm=algorithm,
                        seed=seed,timing=mode,paired_count=len(pairs),paired_wins=sum(x>0 for x in changes),
                        control_seconds=st.median(a['core_seconds'] for a,b in pairs),
                        candidate_seconds=st.median(b['core_seconds'] for a,b in pairs),
                        paired_reduction_pct=st.median(changes),paired_min_pct=min(changes),paired_max_pct=max(changes),validated=valid,
                        ownership_equal=ownership if schedule_pair else None,
                        task_mesh_equal=deterministic if schedule_pair else None,**gate))
    out=root/f'p{ranks}';out.mkdir(parents=True,exist_ok=True)
    for name,data in (('route_runs.csv',flat),('route_comparisons.csv',comparisons)):
        (out/name).write_text('');write_csv(out/name,data)
    (out/'route_issues.txt').write_text('\n'.join(errors)+('\n' if errors else ''))
    lines=[f'A1/B1/C1: {len(flat)} measured runs; {len(errors)} issues.',
           'a_window/b_window keep original domains and borrow node-local CPUs; a_static/a_dynamic/b_remaining/b_critical are historical task routes.',
           'C compares fused counts with prefix scans plus neighbor offsets; global synchronization remains.',
           'Only validated comparisons support performance claims. Decomposition quality changes need review.']
    lines.extend(f'{r["control"]} -> {r["candidate"]} {r["timing"]}: {r["paired_reduction_pct"]:.2f}% reduction; wins={r["paired_wins"]}/{r["paired_count"]}; validated={r["validated"]}' for r in comparisons)
    (out/'ROUTE_SUMMARY.txt').write_text('\n'.join(lines)+'\n')
    print(lines[0]);return not errors

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path);parser.add_argument('--ranks',type=int,required=True)
    parser.add_argument('--routes',nargs='+',choices=ROUTES,default=list(ROUTES))
    args=parser.parse_args();raise SystemExit(0 if analyze(args.root,args.ranks,args.routes) else 1)
