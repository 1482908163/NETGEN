#!/usr/bin/env python3
"""Exercise the real three-route preset; synthetic profiles are not speedups."""
import ast
import csv
import os
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
tree=ast.parse((ROOT/'tests/test_generation_cost_runner.py').read_text())
script=next(n.args[0].value for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
    and n.func.attr=='write_text' and n.args and isinstance(n.args[0],ast.Constant)
    and isinstance(n.args[0].value,str) and '# phase_full_cost_v3 cost_' in n.args[0].value)
script=script.replace("route=value('--kernel-scheduler')", "scheduler=value('--kernel-scheduler')\nfixed=int(sys.argv[len(sys.argv)-1-sys.argv[::-1].index('--repair-fixed-point')+1]) if '--repair-fixed-point' in sys.argv else 0\nroute=('repair_fixed' if fixed else 'repair_fixed_profile') if scheduler=='cost_profile' else scheduler\nif fixed==2:assert '--validate-volume' in sys.argv")
script=script.replace("route in ('batch_parallel','cost_profile')", "route in ('batch_parallel','cost_profile','repair_fixed_profile','repair_fixed')")
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);launcher=tmp/'launcher';launcher.write_text('#!/bin/bash\nshift 2\nexec "$@"\n');launcher.chmod(0o755)
    binary=tmp/'mesh';binary.write_text(script);binary.chmod(0o755)
    source=tmp/'input.step';source.write_text('input');lib=tmp/'lib';lib.mkdir();(lib/'libnglib.so').write_text('library')
    env=dict(os.environ)
    for k in ('EXPERIMENT_STAGE','WORKLET_ROUTES','KERNEL_REPEAT','PARTITION_SEEDS','ALGORITHMS','TIMING_MODES','STRONG_SCALING_DIR','REPAIR_FIXED_POINT','MOCK_COST_FAILURE','MOCK_FAIL'):
        env.pop(k,None)
    env.update(EXPERIMENT_PRESET='repair_fixed',MESH_EXPERIMENT_WORKER='1',PROCESS_COUNT='2',PROCESS_COUNTS='2',
        RANKS_PER_NODE='2',CPUS_PER_TASK='2',KERNEL_THREADS='2',WARMUPS='1',LEVELS='0',REFINES='0',
        LOAD_MODULES='0',CLUSTER_ENV_STRICT='0',NETGEN_INSTALL_LIB=str(lib),MPI_LAUNCHER=str(launcher),MPI_EXTRA_ARGS=' ',
        START_EPOCH='0',BINARY=str(binary),INPUT_PATH=str(source),MOCK_FIXTURES=str(ROOT/'tests'),
        RESUME='0',TIMEOUT_SECONDS='10',RUN_ROOT=str(tmp/'results'),MOCK_CALLS=str(tmp/'calls'))
    result=subprocess.run(['bash',str(ROOT/'strong_scaling/run_experiments.sh')],env=env,capture_output=True,text=True,timeout=240)
    reasons=[p.read_text() for p in (tmp/'results').rglob('failure_reason.txt')]
    assert result.returncode==0,(result.stdout,result.stderr,reasons)
    calls=[line.split() for line in (tmp/'calls').read_text().splitlines()]
    assert len(calls)==36
    routes=['batch_parallel','repair_fixed_profile','repair_fixed']
    for repeat in range(1,4):
        order=[c[0] for c in calls if c[1:]==['natural',str(repeat),'-1']]
        assert order==routes[repeat%3:]+routes[:repeat%3]
    p=tmp/'results/p2'
    measured=list(csv.DictReader((p/'route_runs.csv').open()));assert len(measured)==27
    comparisons=list(csv.DictReader((p/'route_comparisons.csv').open()))
    assert len(comparisons)==9 and all(r['validated']=='True' for r in comparisons)
    import sys
    sys.path.insert(0,str(ROOT/'strong_scaling'));from analyze_results import inspect
    base=tmp/'results/route_repair_fixed/p2/sparse_natural'
    warm,_=inspect(base/'repeat_0/rank_profiles.jsonl.gz');formal,_=inspect(base/'repeat_1/rank_profiles.jsonl.gz')
    assert warm['volume_repair_fixedpoint']=='reference_verify_v1' and warm['repair_fixed_verified']>0 and warm['repair_fixed_skipped']==0
    assert formal['volume_repair_fixedpoint']=='exact_state_stop_v1' and formal['repair_fixed_skipped']>0
    # Quality equality alone cannot establish that this optimization ran.
    import gzip,json
    profile=base/'repeat_1/rank_profiles.jsonl.gz'
    with gzip.open(profile,'rt') as stream:rows=[json.loads(line) for line in stream]
    for row in rows:
        for phase in ('generation','repair','optimization'):
            for field in ('stable_rounds','potential_skipped','skipped'):
                row['metrics'][f'repair_fixed_{phase}_{field}']=0
    with gzip.open(profile,'wt') as stream:stream.write(''.join(json.dumps(row)+'\n' for row in rows))
    from analyze_worklet_routes import analyze
    assert not analyze(tmp/'results',2,routes)
    assert 'early stop not exercised' in (p/'route_issues.txt').read_text()
print('PASS: three-route matrix, exact quality pairing, warmup reference verification, formal early-stop mode and route rotation')
