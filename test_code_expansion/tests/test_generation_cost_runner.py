#!/usr/bin/env python3
"""Exercise default joint routes with mock profiles, not MPI or performance data."""
import csv
import os
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ROUTES=['batch_parallel','cost_profile']
with tempfile.TemporaryDirectory() as directory:
    tmp=Path(directory)
    launcher=tmp/'launcher'
    launcher.write_text('#!/bin/bash\nshift 2\nexec "$@"\n');launcher.chmod(0o755)
    binary=tmp/'mock_mesh'
    binary.write_text('''#!/usr/bin/env python3
# phase_full_cost_v3 cost_ exact_state_stop_v1 reference_verify_v1 repair_fixed_
# --split-active-evaluation original_ranges_v1 split_active_
# --profile-core-only --algorithm research_1 global_id_bits mesh_phase_v3
# mesh_comm_v1 --communication-only --kernel-threads --kernel-scheduler repair_v2
# volume_audit_v1 node_coop_v2 node_work_v1 node_stage_metrics_v1 node_timeline_v1
# node_cpu_bind node_affinity_layout tail_repeat_v4 prefix_neighbor_v1 --prefix-global-ids
# net_window_v2 tail_share_v3 fused_pair_v1 --fused-global-ids
# mesh_worklets_v1 --worklets-per-owner --worklet-policy worklet_failure_v1
# heavy_q75_q90_v1 --adaptive-worklets owner_local_v2 --async-global-ids
# mandatory_home_v2 original_owner_v1 canonical_ghost_v1 adjacency_id_path
# batch_serial_v1 batch_parallel_v1 recovery_batch_ ordered_disjoint_stars_v1 serial_order_v1 serial_wave_state_exact_v1 combine_commit_
# first_encounter_bulk_v1 refinement_threads phase_operations_v1 parallel_readonly_v1 serial_readonly_v1 recovery_
import os,sys,json
from pathlib import Path
sys.path.insert(0,os.environ['MOCK_FIXTURES'])
from test_cost_profile import fixture
value=lambda key:sys.argv[sys.argv.index(key)+1]
assert '--worklets-per-owner' not in sys.argv
route=value('--kernel-scheduler')
assert '--async-global-ids' in sys.argv
mode='natural' if '--profile-natural' in sys.argv else 'split'
repeat=int(value('--profile-repeat'))
seed=int(value('--partition-seed'))
with open(os.environ['MOCK_CALLS'],'a') as stream:stream.write(f'{route} {mode} {repeat} {seed}\\n')
if os.environ.get('MOCK_FAIL')=='1' and (route,mode,repeat,seed)==('cost_profile','natural',1,17):sys.exit(7)
rows=fixture(route,repeat,mode)
for row in rows:
    row['metadata']['partition_seed']=str(seed)
    row['metadata']['kernel_threads']='2'
    if repeat==0 and os.environ.get('MOCK_SAME_PARTITIONS')!='1':row['metrics']['quality_surface_sum_lo']+=seed+1
    row['metrics']['recovery_parallel_evaluations']=2 if route in ('batch_parallel','cost_profile') else 0
    if route in ('batch_parallel','cost_profile'):row['metrics']['recovery_batch_parallel_calls']=1 if route in ('batch_parallel','cost_profile') else 0
    row['metadata']['refinement_threads']='1'
    if os.environ.get('MOCK_COST_FAILURE')=='1' and (route,repeat,seed)==('cost_profile',1,17):
        row['metrics']['native_generation_swap_calls']=2
if mode=='split':
    for row in rows:row['metadata'].pop('mesh_quality',None)
(Path(value('--profile-dir'))/'rank_profiles.jsonl').write_text(''.join(json.dumps(r)+'\\n' for r in rows))
''');binary.chmod(0o755)
    source=tmp/'input.step';source.write_text('mock input')
    lib=tmp/'lib';lib.mkdir();(lib/'libnglib.so').write_text('mock library')
    env=dict(os.environ)
    for key in ('KERNEL_REPEAT','WORKLET_ROUTES','REPEATS','MOCK_FAIL','EXPERIMENT_STAGE',
                'STRONG_SCALING_DIR','WORKLET_FACTOR_SWEEP_DONE','ALGORITHMS','TIMING_MODES','PARTITION_SEEDS'):
        env.pop(key,None)
    env.pop('MOCK_SAME_PARTITIONS',None)
    env.update(EXPERIMENT_PRESET='generation_cost',MESH_EXPERIMENT_WORKER='1',PROCESS_COUNT='2',PROCESS_COUNTS='2',
        RANKS_PER_NODE='2',CPUS_PER_TASK='2',KERNEL_THREADS='2',WARMUPS='1',LEVELS='0',REFINES='0',
        LOAD_MODULES='0',CLUSTER_ENV_STRICT='0',NETGEN_INSTALL_LIB=str(lib),MPI_LAUNCHER=str(launcher),
        MPI_EXTRA_ARGS=' ',START_EPOCH='0',BINARY=str(binary),INPUT_PATH=str(source),
        MOCK_FIXTURES=str(ROOT/'tests'),RESUME='0',TIMEOUT_SECONDS='10')
    command=['bash',str(ROOT/'strong_scaling/run_experiments.sh')]
    for failure in (False,True,'validation'):
        out=tmp/('failure' if failure else 'success');calls=tmp/(out.name+'_calls')
        if failure=='validation':out=tmp/'validation';calls=tmp/'validation_calls'
        case=dict(env,RUN_ROOT=str(out),MOCK_CALLS=str(calls),MOCK_FAIL=str(int(failure is True)),
                  MOCK_COST_FAILURE=str(int(failure=='validation')))
        if failure:case['REPEATS']='2'
        run=subprocess.run(command,env=case,capture_output=True,text=True,timeout=240)
        assert run.returncode==int(bool(failure)),(run.returncode,run.stdout,run.stderr,(out/'p2/route_issues.txt').read_text()[:6000] if (out/'p2/route_issues.txt').exists() else '')
        rows=[line.split() for line in calls.read_text().splitlines()]
        repeats=2 if failure else 3
        assert len(rows)==len(ROUTES)*3*(repeats+1),(len(rows),rows)
        for repeat in range(1,repeats+1):
            actual=[r[0] for r in rows if r[1:] == ['natural',str(repeat),'-1']]
            expected=ROUTES[repeat%len(ROUTES):]+ROUTES[:repeat%len(ROUTES)]
            assert actual==expected,(repeat,actual,expected)
        if failure:
            bad=out/'route_cost_profile/p2/sparse_seed17_natural/repeat_1'
            assert not (bad/'SUCCESS').exists()
            reason=(bad/'failure_reason.txt').read_text()
            assert ('exit_code=1' if failure=='validation' else 'exit_code=7') in reason
            if failure=='validation':
                assert reason.startswith('analysis_validation_failure\n')
                assert 'native operation call coverage mismatch' in reason
                assert (bad/'rank_profiles.jsonl.gz').exists()
                assert not (bad/'rank_profiles.jsonl').exists()
            assert (out/'route_batch_parallel/p2/sparse_natural/repeat_2/SUCCESS').exists()
            assert (out/'p2/route_issues.txt').read_text().strip()
        else:
            measured=list(csv.DictReader((out/'p2/route_runs.csv').open()))
            assert len(measured)==18
            assert {r['partition_seed'] for r in measured}=={'-1','17','41'}
            comparisons=list(csv.DictReader((out/'p2/route_comparisons.csv').open()))
            assert len(comparisons)==3 and all(r['validated']=='True' for r in comparisons)
            costs=list(csv.DictReader((out/'route_cost_profile/p2/analysis/volume_operation_costs.csv').open()))
            assert len(costs)==2*3*4*3*4 and {r['rank'] for r in costs}=={'0','1'}
            assert {r['repeat'] for r in costs}=={'0','1','2','3'}
            assert (out/'route_cost_profile/p2/sparse_natural/repeat_0/operation_cost_profile.csv').exists()
            assert '不能作为算法加速' in (out/'p2/ROUTE_SUMMARY.txt').read_text()
        if failure:
            assert (bad/'failure_symbols.json').exists()
            assert (bad/'failure_diagnostics.log').exists()
    out=tmp/'same_partitions'
    run=subprocess.run(command,env=dict(env,RUN_ROOT=str(out),REPEATS='1',
        MOCK_CALLS=str(tmp/'same_calls'),MOCK_FAIL='0',MOCK_SAME_PARTITIONS='1'),
        capture_output=True,text=True,timeout=240)
    assert run.returncode==0,(run.stdout,run.stderr)
    diversity=list(csv.DictReader((out/'p2/partition_diversity.csv').open()))
    assert all(r['distinct_surface_assignments']=='1' for r in diversity)
    assert '相同指纹不能视为独立分区覆盖' in (out/'p2/ROUTE_SUMMARY.txt').read_text()
print('PASS: two full-cost routes, 18 formal mock runs, exact export, rotated order and diagnosed failure continuation')
