#!/usr/bin/env python3
"""Synthetic profiles test validation, pairing, missing runs and numbering drift."""
import copy
import csv
import json
import sys
import subprocess
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'strong_scaling'))
import analyze_results as profiles
import analyze_worklet_routes as routes

def fixture(route,repeat,mode='natural'):
    worklet=route in ('a_static','a_dynamic','b_remaining','b_critical','worklet_static_fixed','worklet_owner_fixed','critical_worklet');deferred=route in ('c_deferred','c_fused','a_fused','b_fused')
    scheduler={'recovery_global':'recovery','front_spatial':'spatial','spatial_ready':'spatial','a_bound':'node_window','b_bound':'node_window_balanced','b_repeat':'node_window_repeat','a_prefix':'node_window','b_prefix':'node_window_repeat','a_native':'node_native','a_fixed':'node_fixed','a_window':'node_window','b_window':'node_window_priority','b_balanced':'node_window_balanced','a_fused':'node_window','b_fused':'node_window_balanced'}.get(route,'repair')
    meta={k:'fixture' for k in profiles.QUALITY_IDENTITY}
    meta.update(feature_schema='mesh_worklets_v1' if worklet else 'mesh_comm_v1',
        core_only='true',algorithm='sparse',timing_mode=mode,numrefine='0',partition_seed='-1',
        kernel_scheduler=scheduler,kernel_threads='1',kernel_diagnostics='repair_v2',cost_model='none',balance_method='none',
        mesh_tasks='2' if worklet else '0',active_workers='1' if worklet else '2',
        global_numbering='fused_pair_v1' if route in ('c_fused','a_fused','b_fused') else 'deferred_pair_v1' if deferred else 'eager_v1')
    prefix=route in ('c_prefix','a_prefix','b_prefix')
    if prefix:meta['global_numbering']='prefix_neighbor_v1'
    meta['node_cpu_bind']='cores' if route in ('a_bound','b_bound','b_repeat','a_prefix','b_prefix') or not scheduler.startswith('node_') else 'none'
    if scheduler.startswith('node_'):meta['node_affinity_layout']='disjoint' if meta['node_cpu_bind']=='cores' else 'shared_pool'
    if worklet:meta.update(worklet_policy=route.split('_')[1],worklets_per_owner='1',
        task_mesh_signature='1234',worklet_ownership_signature='abcd')
    meta['node_affinity_layout']='disjoint'
    meta['adjacency_audit_schema']='canonical_ghost_v1'
    meta['adjacency_id_path']='owner_local_v2' if route in ('async_global','ready_global','spatial_ready','recovery_global') else 'contiguous_v1'
    if route in ('async_global','ready_global','spatial_ready','recovery_global'):meta['global_numbering']='owner_local_v2'
    if route in ('worklet_static_fixed','worklet_owner_fixed','critical_worklet'):
        meta['worklet_scheduler']='mandatory_home_v2'
        meta['worklet_decomposition']='original_owner_v1'
        meta['worklet_policy']={'worklet_static_fixed':'static','worklet_owner_fixed':'dynamic','critical_worklet':'critical'}[route]
    meta['front_search']='conservative_boxes_v1' if route in ('front_spatial','spatial_ready') else 'linear_v1'
    meta['volume_exchange_policy']='peer_ready_v1' if route in ('ready_global','spatial_ready') else 'batch_v1'
    if route in ('reference_bound','async_global','recovery_global'):
        meta['recovery_evaluation']='parallel_readonly_v1' if route=='recovery_global' else 'serial_readonly_v1'
    if repeat==0:meta.update(mesh_quality='volume_audit_v1',quality_face_reference='allgather')
    if scheduler.startswith('node_'):
        meta['node_resources']='node_coop_v2'
        if scheduler in ('node_window','node_window_priority','node_window_balanced','node_window_repeat'):
            meta.update(node_checkpoints='node_work_v1',node_work_policy={'node_window_repeat':'tail_repeat_v4','node_window':'net_window_v2','node_window_priority':'net_priority_v2','node_window_balanced':'tail_share_v3'}[scheduler])
    rows=[]
    for rank in range(2):
        m=dict(core_seconds=1.,local_points_before_adjacency=4,
            local_volume_elements_before_adjacency=1,local_surface_elements_before_adjacency=4,
            kernel_generation_seconds=.1,kernel_repair_seconds=.1,kernel_optimization_seconds=.1)
        if 'recovery_evaluation' in meta:
            m.update(recovery_calls=1,recovery_seconds=.04,recovery_evaluation_calls=2,recovery_evaluation_seconds=.02,recovery_candidates=64,recovery_parallel_evaluations=0)
        for name in ('delaunay_seconds','front_seconds','domain_repair_seconds','repair_mark_seconds',
                     'repair_split_seconds','repair_swap_seconds','repair_swap2_seconds',
                     'repair_rounds','repair_candidates_total','repair_candidates_active','repair_fallbacks'):
            m['kernel_'+name]=0
        m['kernel_final_illegal']=187
        stages={s:dict(seconds=.1,calls=1) for s in profiles.COMPUTE}
        if worklet:
            k=0 if rank==0 else 2
            m.update(tasks_completed=k,task_generated_elements_global=2,task_cross_node_faces_before=1,
                task_cross_node_faces_after=1,task_cross_node_faces_limit=1,task_moved_between_nodes=0,
                worklets_owned=1,worklet_return_checked=1,worklet_ownership_errors=0)
            m.update(worklet_remote_claims=0,worklet_remote_budget=1,worklet_mandatory_remote_claims=0)
            for s in profiles.COMPUTE[:3]:stages[s]=dict(seconds=.1*k,calls=k)
        if scheduler.startswith('node_'):
            for name in ('setup_seconds','management_seconds','epochs','borrow_epochs','borrowed_core_seconds',
                         'leased_core_seconds','phase_seconds','peak_threads','model_decisions','model_rejections',
                         'cold_decisions','node_ranks','node_cpus'):
                m['coop_'+name]=0
            m.update(coop_work_repeat_grants=0,coop_work_competition_checks=0,coop_work_shared_grants=0,coop_work_unreserved_cores=0)
            m.update(coop_peak_threads=1,coop_node_ranks=2,coop_node_cpus=2,coop_work_net_gain_estimate_seconds=0)
            for phase in range(8):
                for name in ('epochs','seconds','core_seconds','borrowed_core_seconds','below_base_epochs','min_threads','max_threads'):
                    m[f'coop_phase_{phase}_{name}']=0
            for name in ('checks','restarts','grants','seconds'):m['coop_checkpoint_'+name]=0
            for name in ('checks','unknown','short','cost','deferred','no_capacity','grants','reserved_cores','already','gain_estimate_seconds','restart_estimate_seconds'):
                m['coop_work_'+name]=0
        if route in ('async_global','ready_global','spatial_ready','recovery_global'):
            m['id_global_count_collectives']=0
            stages['id_owner_local_encode']=dict(seconds=.01,calls=1)
        if route in ('front_spatial','spatial_ready'):
            m.update(front_search_queries=1,front_search_point_scan_opportunities=100,front_search_point_candidates=10,
                front_search_face_scan_opportunities=80,front_search_face_candidates=8,front_search_build_seconds=.001)
        if route in ('ready_global','spatial_ready'):
            m.update(volume_count_peers=1,ready_volume_count_completions=1,ready_volume_early_receives=0,ready_volume_payload_sends=1)
            stages['volume_ready_exchange']=dict(seconds=.01,calls=1)
        if deferred:
            for s in ('id_count_begin','id_count_commit_wait','id_compaction'):stages[s]=dict(seconds=.01,calls=1)
        if prefix:
            m['id_prefix_buffer_bytes']=32
            for name in ('id_prefix_scan','id_neighbor_offsets','id_compaction'):stages[name]=dict(seconds=.01,calls=1)
        m.update(local_points_after_adjacency=4,local_volume_elements_after_adjacency=1,ghost_volume_elements_added=0)
        if repeat==0:
            stages['adjacency_audit']=dict(seconds=.01,calls=1)
            m.update(quality_adjacency_points=4,quality_adjacency_elements=1,quality_adjacency_ghosts=0)
            for name in ('point_sum','point_xor','element_sum','element_xor'):
                m['quality_adjacency_'+name+'_hi']=rank;m['quality_adjacency_'+name+'_lo']=200
            for k in profiles.QUALITY_COUNTS:m['quality_'+k]=0
            m.update(quality_points=4,quality_elements=1,quality_surfaces=4,quality_checked=1,
                quality_negative=1,quality_shape_min=.95,quality_shape_sum=.95,quality_angle_min=50,
                quality_angle_max=80,quality_abs_volume=1,quality_face_reference_passed=1)
            for i in range(10):m['quality_shape_bin_'+str(i)]=int(i==9)
            for name in ('surface_sum','surface_xor','volume_sum','volume_xor','numbering'):
                m['quality_'+name+'_hi']=rank;m['quality_'+name+'_lo']=100
        rows.append(dict(rank=rank,ranks=2,repeat=repeat,metadata=meta,metrics=m,stages=stages))
    return rows

def main():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        for route in routes.ROUTES:
            folder=root/('route_'+route)/'p2';folder.mkdir(parents=True)
            plan=dict(ranks=2,repeats=2,algorithms=['sparse'],timings=['natural','split'],partition_seeds=[-1],quality_warmup=True)
            (folder/'plan.json').write_text(json.dumps(plan))
            for mode in plan['timings']:
                for repeat in range(0 if mode=='natural' else 1,3):
                    d=folder/f'sparse_{mode}'/f'repeat_{repeat}';d.mkdir(parents=True)
                    (d/'SUCCESS').touch();(d/'rank_profiles.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in fixture(route,repeat,mode)))
        assert routes.analyze(root,2)
        parallel=fixture('recovery_global',1)
        for row in parallel:
            row['metadata']['kernel_threads']='4'
            row['metrics']['recovery_parallel_evaluations']=2
        check=root/'recovery.jsonl'
        def write_rows(rows):check.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        write_rows(parallel)
        run,_=profiles.inspect(check)
        assert run['recovery_exercised'] and run['recovery_parallel_evaluations']==4
        write_rows(list(reversed(parallel)))
        reversed_run,_=profiles.inspect(check)
        assert reversed_run==run
        broken=copy.deepcopy(parallel);broken[0]['metrics']['recovery_parallel_evaluations']=0
        write_rows(broken)
        try:profiles.inspect(check);assert False
        except ValueError as e:assert 'worker activation' in str(e)
        for row in parallel:
            for key in list(row['metrics']):
                if key.startswith('recovery_'):row['metrics'][key]=0
        write_rows(parallel)
        run,_=profiles.inspect(check)
        assert not run['recovery_exercised']
        comparisons=list(csv.DictReader((root/'p2/route_comparisons.csv').open()))
        direct=[r for r in comparisons if r['contribution']=='end_to_end']
        assert len(direct)==30
        assert {r['candidate'] for r in direct}=={'a_dynamic','b_remaining','b_critical','a_window','b_window','b_balanced','a_fused','b_fused','a_bound','b_bound','b_repeat','c_prefix','a_prefix','b_prefix','critical_worklet'}
        assert all(r['control'] in ('reference','reference_bound') and r['validated']=='True' for r in direct)
        assert routes.analyze(root,2,['a_static'])
        misplaced=root/'route_a_window/p2/sparse_natural/repeat_1/rank_profiles.jsonl'
        saved=misplaced.read_text()
        misplaced.write_text(''.join(json.dumps(r)+'\n' for r in fixture('reference',1)))
        assert not routes.analyze(root,2,['reference','a_fixed','a_window','b_window','c_fused','c_deferred'])
        assert 'route kernel scheduler mismatch' in (root/'p2/route_issues.txt').read_text()
        misplaced.write_text(saved)
        # Joint routes must validate BOTH scheduling and numbering, not directory names.
        misplaced=root/'route_b_fused/p2/sparse_natural/repeat_1/rank_profiles.jsonl'
        saved=misplaced.read_text()
        for wrong,reason in [('b_balanced','route numbering mismatch'),('a_fused','route kernel scheduler mismatch')]:
            misplaced.write_text(''.join(json.dumps(r)+'\n' for r in fixture(wrong,1)))
            assert not routes.analyze(root,2)
            assert reason in (root/'p2/route_issues.txt').read_text()
        misplaced.write_text(saved)
        bad=fixture('b_balanced',1);bad[0]['metrics']['coop_work_shared_grants']=1
        invalid=root/'bad_tail.jsonl';invalid.write_text(''.join(json.dumps(r)+'\n' for r in bad))
        try:profiles.inspect(invalid);assert False
        except ValueError as e:assert 'tail allocation diagnostics' in str(e)
        bound=root/'route_a_bound/p2/sparse_natural/repeat_1/rank_profiles.jsonl'
        saved=bound.read_text();wrong=fixture('a_bound',1)
        for row in wrong:row['metadata']['node_affinity_layout']='shared_pool'
        bound.write_text(''.join(json.dumps(r)+'\n' for r in wrong))
        assert not routes.analyze(root,2)
        assert 'verified disjoint startup affinity' in (root/'p2/route_issues.txt').read_text()
        bound.write_text(saved)
        for route,metric,value,error in [('b_repeat','coop_work_repeat_grants',1,'repeat grants'),
                                         ('c_prefix','id_prefix_buffer_bytes',0,'prefix count buffer')]:
            bad=fixture(route,1);bad[0]['metrics'][metric]=value
            invalid=root/'bad_new.jsonl';invalid.write_text(''.join(json.dumps(r)+'\n' for r in bad))
            try:profiles.inspect(invalid);assert False
            except ValueError as e:assert error in str(e),str(e)
        bad=fixture('c_prefix',1);bad[0]['stages']['id_count_begin']=dict(seconds=.1,calls=1)
        invalid=root/'bad_prefix.jsonl';invalid.write_text(''.join(json.dumps(r)+'\n' for r in bad))
        try:profiles.inspect(invalid);assert False
        except ValueError as e:assert 'global count gather' in str(e)
        # Positive kernel diagnostics must survive without bypassing the structural audit.
        check=root/'diagnostic_audit';check.mkdir()
        path=check/'rank_profiles.jsonl'
        rows=fixture('a_static',0)
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        result,_=profiles.inspect(path)
        assert result['kernel_final_illegal']==374
        command=[sys.executable,str(ROOT/'strong_scaling/analyze_results.py'),str(check),
                 '--finish-run','--keep-artifacts']
        assert subprocess.run(command,capture_output=True).returncode==0
        assert (check/'SUCCESS').exists()
        (check/'SUCCESS').unlink()
        rows[0]['metrics']['quality_missing_surface']=1
        rows[0]['metrics']['quality_hard_errors']=1
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        failed=subprocess.run(command,capture_output=True,text=True)
        assert failed.returncode!=0 and 'volume quality audit failed' in failed.stderr
        assert not (check/'SUCCESS').exists()
        assert not json.loads((check/'quality_summary.json').read_text())['structural_pass']
        path=root/'route_c_deferred/p2/sparse_natural/repeat_0/rank_profiles.jsonl'
        original=path.read_text();changed=fixture('c_deferred',0);changed[1]['metrics']['quality_numbering_lo']+=1
        path.write_text(''.join(json.dumps(r)+'\n' for r in changed))
        assert not routes.analyze(root,2)
        assert 'global_numbering_changed' in (root/'p2/route_issues.txt').read_text()
        path.write_text(original)
        path=root/'route_async_global/p2/sparse_natural/repeat_0/rank_profiles.jsonl'
        original=path.read_text();changed=fixture('async_global',0)
        changed[1]['metrics']['quality_adjacency_element_xor_lo']+=1
        path.write_text(''.join(json.dumps(r)+'\n' for r in changed))
        assert not routes.analyze(root,2)
        assert 'adjacency_fingerprint_changed' in (root/'p2/route_issues.txt').read_text()
        path.write_text(original)
        for mutation,error in [('path','bypassed temporary IDs'),('coverage','coverage mismatch'),
                               ('collective','validation count collective'),('missing','missing post-exchange')]:
            bad=fixture('async_global',1 if mutation=='collective' else 0)
            if mutation=='path':
                for row in bad:row['metadata']['adjacency_id_path']='contiguous_v1'
            elif mutation=='coverage':bad[0]['metrics']['quality_adjacency_elements']+=1
            elif mutation=='collective':bad[0]['stages']['id_audit_offsets']=dict(seconds=.01,calls=1)
            else:bad[0]['stages'].pop('adjacency_audit')
            invalid=root/'bad_adjacency.jsonl';invalid.write_text(''.join(json.dumps(r)+'\n' for r in bad))
            try:profiles.inspect(invalid);assert False
            except ValueError as e:assert error in str(e),str(e)
        for route,metric,value,error in [('front_spatial','front_search_point_candidates',101,'candidates exceed'),
                    ('ready_global','ready_volume_count_completions',0,'completion coverage mismatch')]:
            bad=fixture(route,1);bad[0]['metrics'][metric]=value
            invalid=root/'bad_structural.jsonl';invalid.write_text(''.join(json.dumps(r)+'\n' for r in bad))
            try:profiles.inspect(invalid);assert False
            except ValueError as e:assert error in str(e),str(e)
        path=root/'route_front_spatial/p2/sparse_natural/repeat_0/rank_profiles.jsonl'
        original=path.read_text();bad=fixture('front_spatial',0);bad[0]['metrics']['quality_volume_sum_lo']+=1
        path.write_text(''.join(json.dumps(r)+'\n' for r in bad))
        assert not routes.analyze(root,2)
        assert 'structural_owned_mesh_changed' in (root/'p2/route_issues.txt').read_text()
        path.write_text(original)
        bad=fixture('critical_worklet',1);bad[0]['metrics']['worklet_remote_claims']=2
        invalid=root/'bad_budget.jsonl';invalid.write_text(''.join(json.dumps(r)+'\n' for r in bad))
        try:profiles.inspect(invalid);assert False
        except ValueError as e:assert 'optional worklet steal budget exceeded' in str(e),str(e)
        bad=fixture('a_dynamic',1);bad[0]['metrics']['worklet_return_checked']=0
        path=root/'route_a_dynamic/p2/sparse_natural/repeat_1/rank_profiles.jsonl'
        original=path.read_text();path.write_text(''.join(json.dumps(r)+'\n' for r in bad))
        try:profiles.inspect(path);assert False
        except ValueError as e:assert 'return was not verified' in str(e)
        path.write_text(original)
        (path.parent/'SUCCESS').unlink()
        (path.parent/'failure_reason.txt').write_text('stage=owner_merge\nreason=fixture original exception\n')
        assert not routes.analyze(root,2)
        assert 'fixture original exception' in (root/'p2/route_issues.txt').read_text()
        # New policies must not accept a fabricated gain without an actual grant.
        bad=fixture('a_window',1);bad[0]['metrics']['coop_work_net_gain_estimate_seconds']=1
        invalid=root/'bad_net.jsonl';invalid.write_text(''.join(json.dumps(r)+'\n' for r in bad))
        try:profiles.inspect(invalid);assert False
        except ValueError as e:assert 'net gain prediction' in str(e)
        # An eager collective must not silently creep into the deferred branch.
        bad=fixture('c_deferred',1);bad[0]['stages']['element_count_allgather']=dict(seconds=.1,calls=1)
        path=root/'bad.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in bad))
        try:profiles.inspect(path);assert False
        except ValueError as e:assert 'eager collective' in str(e)
    print('PASS: A/B/C profiles, quality gates, exact pairing, missing runs and numbering drift')

if __name__=='__main__':
    main()

