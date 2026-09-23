#!/usr/bin/env python3
"""Exercise default joint routes with mock profiles, not MPI or performance data."""
import csv
import os
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ROUTES=['reference','a_native','a_fixed','a_window','b_balanced','c_fused','a_fused','b_fused']
with tempfile.TemporaryDirectory() as directory:
    tmp=Path(directory)
    launcher=tmp/'launcher'
    launcher.write_text('#!/bin/bash\nshift 2\nexec "$@"\n');launcher.chmod(0o755)
    binary=tmp/'mock_mesh'
    binary.write_text('''#!/usr/bin/env python3
# --profile-core-only --algorithm research_1 global_id_bits mesh_phase_v3
# mesh_comm_v1 --communication-only --kernel-threads --kernel-scheduler repair_v2
# volume_audit_v1 node_coop_v2 node_work_v1 node_stage_metrics_v1 node_timeline_v1
# net_window_v2 tail_share_v3 fused_pair_v1 --fused-global-ids
import os,sys,json
from pathlib import Path
sys.path.insert(0,os.environ['MOCK_FIXTURES'])
from test_worklet_routes import fixture
value=lambda key:sys.argv[sys.argv.index(key)+1]
route={'node_native':'a_native','node_fixed':'a_fixed','node_window':'a_window',
       'node_window_balanced':'b_balanced'}.get(value('--kernel-scheduler'),'reference')
if '--fused-global-ids' in sys.argv:
    route={'a_window':'a_fused','b_balanced':'b_fused'}.get(route,'c_fused')
mode='natural' if '--profile-natural' in sys.argv else 'split'
repeat=int(value('--profile-repeat'))
with open(os.environ['MOCK_CALLS'],'a') as stream:stream.write(f'{route} {mode} {repeat}\\n')
if os.environ.get('MOCK_FAIL')=='1' and (route,mode,repeat)==('a_window','natural',1):sys.exit(7)
rows=fixture(route,repeat,mode)
if mode=='split':
    for row in rows:row['metadata'].pop('mesh_quality',None)
(Path(value('--profile-dir'))/'rank_profiles.jsonl').write_text(''.join(json.dumps(r)+'\\n' for r in rows))
''');binary.chmod(0o755)
    source=tmp/'input.step';source.write_text('mock input')
    lib=tmp/'lib';lib.mkdir();(lib/'libnglib.so').write_text('mock library')
    env=dict(os.environ)
    for key in ('KERNEL_REPEAT','WORKLET_ROUTES','REPEATS','MOCK_FAIL','EXPERIMENT_STAGE',
                'STRONG_SCALING_DIR','WORKLET_FACTOR_SWEEP_DONE','ALGORITHMS','TIMING_MODES'):
        env.pop(key,None)
    env.update(EXPERIMENT_PRESET='inplace',MESH_EXPERIMENT_WORKER='1',PROCESS_COUNT='2',PROCESS_COUNTS='2',
        RANKS_PER_NODE='2',CPUS_PER_TASK='2',KERNEL_THREADS='2',WARMUPS='1',LEVELS='0',REFINES='0',
        LOAD_MODULES='0',CLUSTER_ENV_STRICT='0',NETGEN_INSTALL_LIB=str(lib),MPI_LAUNCHER=str(launcher),
        MPI_EXTRA_ARGS=' ',START_EPOCH='0',BINARY=str(binary),INPUT_PATH=str(source),
        MOCK_FIXTURES=str(ROOT/'tests'),PARTITION_SEEDS='-1',RESUME='0',TIMEOUT_SECONDS='10')
    command=['bash',str(ROOT/'strong_scaling/run_experiments.sh')]
    for failure in (False,True):
        out=tmp/('failure' if failure else 'success');calls=tmp/(out.name+'_calls')
        case=dict(env,RUN_ROOT=str(out),MOCK_CALLS=str(calls),MOCK_FAIL=str(int(failure)))
        if failure:case['REPEATS']='2'
        run=subprocess.run(command,env=case,capture_output=True,text=True,timeout=240)
        assert run.returncode==int(failure),(run.returncode,run.stdout,run.stderr)
        rows=[line.split() for line in calls.read_text().splitlines()]
        repeats=2 if failure else 8
        assert len(rows)==len(ROUTES)*2*(repeats+1),(len(rows),rows)
        for repeat in range(1,repeats+1):
            actual=[r[0] for r in rows if r[1:] == ['natural',str(repeat)]]
            expected=ROUTES[repeat%8:]+ROUTES[:repeat%8]
            assert actual==expected,(repeat,actual,expected)
        if failure:
            bad=out/'route_a_window/p2/sparse_natural/repeat_1'
            assert not (bad/'SUCCESS').exists()
            assert 'exit_code=7' in (bad/'failure_reason.txt').read_text()
            assert (out/'route_b_fused/p2/sparse_split/repeat_2/SUCCESS').exists()
            assert (out/'p2/route_issues.txt').read_text().strip()
        else:
            measured=list(csv.DictReader((out/'p2/route_runs.csv').open()))
            assert len(measured)==128
            assert not (out/'p2/route_issues.txt').read_text().strip()
            comparisons=list(csv.DictReader((out/'p2/route_comparisons.csv').open()))
            assert comparisons and all(r['validated']=='True' for r in comparisons)
print('PASS: 8 joint routes, 8-position rotation, 128 formal mock runs, failure continuation')
