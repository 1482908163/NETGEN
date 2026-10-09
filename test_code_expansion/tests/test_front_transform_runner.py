#!/usr/bin/env python3
"""Exercise production shell orchestration with synthetic profiles, not MPI."""
import ast,csv,gzip,json,os,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
tree=ast.parse((ROOT/'tests/test_generation_cost_runner.py').read_text())
script=next(n.args[0].value for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
    and n.func.attr=='write_text' and n.args and isinstance(n.args[0],ast.Constant)
    and isinstance(n.args[0].value,str) and '# phase_full_cost_v3 cost_' in n.args[0].value)
script=script.replace('# --split-active-evaluation original_ranges_v1 split_active_', '# --split-active-evaluation original_ranges_v1 active_queue_v1 active_exact_replay_v1 split_active_')
script=script.replace("route=value('--kernel-scheduler')", "scheduler=value('--kernel-scheduler')\nlast=lambda key:int(sys.argv[len(sys.argv)-1-sys.argv[::-1].index(key)+1])\nfixed=last('--repair-fixed-point');active=last('--split-active-evaluation');transform=last('--front-transform');assert last('--front-distance')==0\nassert scheduler=='cost_profile' and fixed in (1,2) and active==fixed\nroute='front_transform' if transform else 'split_active'\nassert transform==(2 if '--validate-volume' in sys.argv else 1) if transform else True")
script=script.replace("route in ('batch_parallel','cost_profile')", "route in ('split_active','front_transform')")
script=script.replace("rows=fixture(route,repeat,mode)", "rows=fixture(route,repeat,mode)\nif os.environ.get('MOCK_TRANSFORM_FAILURE')=='1' and (route,repeat,seed)==('front_transform',1,17):rows[0]['metrics']['front_transform_dense_terms']=121")
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);launcher=tmp/'launcher';launcher.write_text('#!/bin/bash\nshift 2\nexec "$@"\n');launcher.chmod(0o755)
    binary=tmp/'mesh';binary.write_text(script);binary.chmod(0o755)
    source=tmp/'input.step';source.write_text('input');lib=tmp/'lib';lib.mkdir();(lib/'libnglib.so').write_text('library')
    env=dict(os.environ)
    for k in ('EXPERIMENT_STAGE','WORKLET_ROUTES','KERNEL_REPEAT','PARTITION_SEEDS','ALGORITHMS','TIMING_MODES','STRONG_SCALING_DIR','REPAIR_FIXED_POINT','SPLIT_ACTIVE_EVALUATION','FRONT_DISTANCE','FRONT_TRANSFORM','REPEATS','MOCK_TRANSFORM_FAILURE','MOCK_COST_FAILURE','MOCK_FAIL'):
        env.pop(k,None)
    env.update(EXPERIMENT_PRESET='front_transform',MESH_EXPERIMENT_WORKER='1',PROCESS_COUNT='2',PROCESS_COUNTS='2',
        RANKS_PER_NODE='2',CPUS_PER_TASK='2',KERNEL_THREADS='2',WARMUPS='1',LEVELS='0',REFINES='0',
        LOAD_MODULES='0',CLUSTER_ENV_STRICT='0',NETGEN_INSTALL_LIB=str(lib),MPI_LAUNCHER=str(launcher),MPI_EXTRA_ARGS=' ',
        START_EPOCH='0',BINARY=str(binary),INPUT_PATH=str(source),MOCK_FIXTURES=str(ROOT/'tests'),
        RESUME='0',TIMEOUT_SECONDS='10',RUN_ROOT=str(tmp/'results'),MOCK_CALLS=str(tmp/'calls'))
    command=['bash',str(ROOT/'strong_scaling/run_experiments.sh')]
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=240)
    reasons=[p.read_text() for p in (tmp/'results').rglob('failure_reason.txt')]
    assert result.returncode==0,(result.stdout,result.stderr,reasons)
    calls=[line.split() for line in (tmp/'calls').read_text().splitlines()];assert len(calls)==24
    routes=['split_active','front_transform']
    for repeat in range(1,4):
        order=[c[0] for c in calls if c[1:]==['natural',str(repeat),'-1']]
        assert order==routes[repeat%2:]+routes[:repeat%2]
    p=tmp/'results/p2';measured=list(csv.DictReader((p/'route_runs.csv').open()));assert len(measured)==18
    comparisons=list(csv.DictReader((p/'route_comparisons.csv').open()))
    assert len(comparisons)==3 and all(r['validated']=='True' for r in comparisons)
    sys.path.insert(0,str(ROOT/'strong_scaling'));from analyze_results import inspect,write_csv
    from analyze_worklet_routes import analyze
    base=tmp/'results/route_front_transform/p2/sparse_natural'
    warm,_=inspect(base/'repeat_0/rank_profiles.jsonl.gz');formal,_=inspect(base/'repeat_1/rank_profiles.jsonl.gz')
    assert warm['volume_front_transform']=='transform_exact_replay_v1' and warm['front_transform_verified']==warm['front_transform_calls']
    assert formal['volume_front_transform']=='compiled_rule_operator_v1' and formal['front_transform_verified']==0
    certificates=list(csv.DictReader((base/'repeat_0/algorithm_certificate.csv').open()))
    assert len(certificates)==2 and all(float(c['front_transform_verified'])==float(c['front_transform_calls']) for c in certificates)
    failed=tmp/'failed'
    result=subprocess.run(command,env=dict(env,RUN_ROOT=str(failed),MOCK_CALLS=str(tmp/'failed_calls'),MOCK_TRANSFORM_FAILURE='1'),capture_output=True,text=True,timeout=240)
    assert result.returncode==1
    bad=failed/'route_front_transform/p2/sparse_seed17_natural/repeat_1'
    assert not (bad/'SUCCESS').exists() and not (bad/'algorithm_certificate.csv').exists()
    assert 'front transform conservation mismatch' in (bad/'failure_reason.txt').read_text()
    assert (bad/'rank_profiles.jsonl.gz').exists() and (bad/'failure_symbols.json').exists()
    assert (failed/'route_front_transform/p2/sparse_seed41_natural/repeat_3/SUCCESS').exists()
    assert (failed/'route_split_active/p2/sparse_seed17_natural/repeat_3/SUCCESS').exists()
    # Equal global totals are insufficient: drift between ranks invalidates a pair.
    profile=base/'repeat_1/rank_profiles.jsonl.gz'
    with gzip.open(profile,'rt') as stream:rows=[json.loads(line) for line in stream]
    for row,delta in zip(rows,(1,-1)):
        row['metrics']['front_transform_transforms']+=delta;row['metrics']['front_distance_candidates']+=delta
    with gzip.open(profile,'wt') as stream:stream.write(''.join(json.dumps(row)+'\n' for row in rows))
    assert not analyze(tmp/'results',2,routes)
    assert 'certificate per-rank work mismatch' in (p/'route_issues.txt').read_text()
    cert=[];inspect(profile,certificate_sink=cert);write_csv(base/'repeat_1/algorithm_certificate.csv',cert)
    assert not analyze(tmp/'results',2,routes)
    assert 'per-rank work changed' in (p/'route_issues.txt').read_text()
    # A stale binary must be rejected before entering MPI or generating profiles.
    binary.write_text(script.replace('compiled_rule_operator_v1','old_transform_binary'))
    stale=tmp/'stale';stale_calls=tmp/'stale_calls'
    result=subprocess.run(command,env=dict(env,RUN_ROOT=str(stale),MOCK_CALLS=str(stale_calls)),capture_output=True,text=True,timeout=240)
    assert result.returncode!=0 and not stale_calls.exists()
print('PASS: two-route isolation, 24 starts, exact warmup, formal mode, rotation, failure archive/continuation, per-rank work gates and stale-binary rejection')
