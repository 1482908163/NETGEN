#!/usr/bin/env python3
"""Synthetic profiles test validation, pairing, missing runs and numbering drift."""
import copy
import json
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'strong_scaling'))
import analyze_results as profiles
import analyze_worklet_routes as routes

def fixture(route,repeat,mode='natural'):
    worklet=route.startswith(('a_','b_'));deferred=route=='c_deferred'
    meta={k:'fixture' for k in profiles.QUALITY_IDENTITY}
    meta.update(feature_schema='mesh_worklets_v1' if worklet else 'mesh_comm_v1',
        core_only='true',algorithm='sparse',timing_mode=mode,numrefine='0',partition_seed='-1',
        kernel_scheduler='repair',kernel_threads='1',cost_model='none',balance_method='none',
        mesh_tasks='2' if worklet else '0',active_workers='1' if worklet else '2',
        global_numbering='deferred_pair_v1' if deferred else 'eager_v1')
    if worklet:meta.update(worklet_policy=route.split('_')[1],worklets_per_owner='1',
        task_mesh_signature='1234',worklet_ownership_signature='abcd')
    if repeat==0:meta.update(mesh_quality='volume_audit_v1',quality_face_reference='allgather')
    rows=[]
    for rank in range(2):
        m=dict(core_seconds=1.,local_points_before_adjacency=4,
            local_volume_elements_before_adjacency=1,local_surface_elements_before_adjacency=4,
            kernel_generation_seconds=.1,kernel_repair_seconds=.1,kernel_optimization_seconds=.1)
        stages={s:dict(seconds=.1,calls=1) for s in profiles.COMPUTE}
        if worklet:
            k=0 if rank==0 else 2
            m.update(tasks_completed=k,task_generated_elements_global=2,task_cross_node_faces_before=1,
                task_cross_node_faces_after=1,task_cross_node_faces_limit=1,task_moved_between_nodes=0,
                worklets_owned=1,worklet_return_checked=1,worklet_ownership_errors=0)
            for s in profiles.COMPUTE[:3]:stages[s]=dict(seconds=.1*k,calls=k)
        if deferred:
            for s in ('id_count_begin','id_count_commit_wait','id_compaction'):stages[s]=dict(seconds=.01,calls=1)
        if repeat==0:
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
        assert routes.analyze(root,2,['a_static'])
        path=root/'route_c_deferred/p2/sparse_natural/repeat_0/rank_profiles.jsonl'
        original=path.read_text();changed=fixture('c_deferred',0);changed[1]['metrics']['quality_numbering_lo']+=1
        path.write_text(''.join(json.dumps(r)+'\n' for r in changed))
        assert not routes.analyze(root,2)
        assert 'global_numbering_changed' in (root/'p2/route_issues.txt').read_text()
        path.write_text(original)
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
        # An eager collective must not silently creep into the deferred branch.
        bad=fixture('c_deferred',1);bad[0]['stages']['element_count_allgather']=dict(seconds=.1,calls=1)
        path=root/'bad.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in bad))
        try:profiles.inspect(path);assert False
        except ValueError as e:assert 'eager collective' in str(e)
    print('PASS: A/B/C profiles, quality gates, exact pairing, missing runs and numbering drift')

if __name__=='__main__':
    main()
