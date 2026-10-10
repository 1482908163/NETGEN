#!/usr/bin/env python3
"""Actual batch orchestration, synthetic evidence; no mesh performance claims."""
import ast,csv,gzip,json,os,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
tree=ast.parse((ROOT/'tests/test_generation_cost_runner.py').read_text())
script=next(n.args[0].value for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
 and n.func.attr=='write_text' and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str) and '# phase_full_cost_v3 cost_' in n.args[0].value)
script=script.replace('#!/usr/bin/env python3','#!/usr/bin/env python3\n# --incidence-build original_incidence_profile_v1 ordered_incidence_v1 ordered_incidence_replay_v1 incidence_\n# --front-projection original_projection_profile_v1 fixed_projection_v1 fixed_projection_replay_v1 front_projection_',1)
script=script.replace('# --split-active-evaluation original_ranges_v1 split_active_','# --split-active-evaluation original_ranges_v1 active_queue_v1 active_exact_replay_v1 split_active_')
script=script.replace('from test_cost_profile import fixture','from test_front_projection_validation import fixture')
script=script.replace("route=value('--kernel-scheduler')", "scheduler=value('--kernel-scheduler')\nlast=lambda key:int(sys.argv[len(sys.argv)-1-sys.argv[::-1].index(key)+1])\nfixed=last('--repair-fixed-point');active=last('--split-active-evaluation')\nassert scheduler=='cost_profile' and fixed in (1,2) and active==fixed\nassert last('--front-distance')==last('--front-transform')==0\nbuild=last('--incidence-build');projection=last('--front-projection') if '--front-projection' in sys.argv else -1\nassert build==(2 if '--validate-volume' in sys.argv else 1)\nroute='incidence_ordered' if projection==-1 else 'projection_profile' if projection==0 else 'projection_fixed'\nassert projection==(2 if '--validate-volume' in sys.argv else 1) if projection>0 else True")
script=script.replace("route in ('batch_parallel','cost_profile')","route in ('incidence_ordered','projection_profile','projection_fixed')")
script=script.replace("rows=fixture(route,repeat,mode)","""rows=fixture(route,repeat,mode)
for row in rows:
    for key in ('MESH_SOURCE_REVISION','MESH_BINARY_SHA256'):row['metadata'][key]=os.environ[key]
if os.environ.get('MOCK_PROJECTION_FAILURE')=='1' and (route,repeat,seed)==('projection_fixed',1,17):rows[0]['metrics']['front_projection_compiled_planes']=0
if os.environ.get('MOCK_IGNORED_PROJECTION')=='1' and route=='projection_fixed':
    for row in rows:
        row['metadata'].pop('volume_front_projection',None)
        row['metrics']={k:v for k,v in row['metrics'].items() if not k.startswith('front_projection_')}
""")
with tempfile.TemporaryDirectory() as tmp:
 tmp=Path(tmp);launcher=tmp/'launcher';launcher.write_text('#!/bin/bash\nshift 2\nexec "$@"\n');launcher.chmod(0o755)
 binary=tmp/'mesh';binary.write_text(script);binary.chmod(0o755)
 source=tmp/'input.step';source.write_text('input');lib=tmp/'lib';lib.mkdir();(lib/'libnglib.so').write_text('library')
 env=dict(os.environ)
 for k in ('EXPERIMENT_STAGE','WORKLET_ROUTES','KERNEL_REPEAT','PARTITION_SEEDS','ALGORITHMS','TIMING_MODES','STRONG_SCALING_DIR','REPAIR_FIXED_POINT','SPLIT_ACTIVE_EVALUATION','FRONT_DISTANCE','FRONT_TRANSFORM','FRONT_PROJECTION','INCIDENCE_BUILD','REPEATS','MOCK_PROJECTION_FAILURE','MOCK_IGNORED_PROJECTION','MOCK_COST_FAILURE','MOCK_FAIL'):env.pop(k,None)
 env.update(EXPERIMENT_PRESET='front_projection',MESH_EXPERIMENT_WORKER='1',PROCESS_COUNT='2',PROCESS_COUNTS='2',
 RANKS_PER_NODE='2',CPUS_PER_TASK='2',KERNEL_THREADS='2',WARMUPS='1',LEVELS='0',REFINES='0',LOAD_MODULES='0',CLUSTER_ENV_STRICT='0',
 NETGEN_INSTALL_LIB=str(lib),MPI_LAUNCHER=str(launcher),MPI_EXTRA_ARGS=' ',START_EPOCH='0',BINARY=str(binary),INPUT_PATH=str(source),
 MOCK_FIXTURES=str(ROOT/'tests'),RESUME='0',TIMEOUT_SECONDS='10',RUN_ROOT=str(tmp/'results'),MOCK_CALLS=str(tmp/'calls'))
 command=['bash',str(ROOT/'strong_scaling/run_experiments.sh')]
 def run(overrides={}):
  r=subprocess.run(command,env=dict(env,**overrides),capture_output=True,text=True,timeout=240)
  root=Path(overrides.get('RUN_ROOT',env['RUN_ROOT']))
  return r,[p.read_text() for p in root.rglob('failure_reason.txt')]+[p.read_text() for p in root.rglob('route_issues.txt')]
 forbidden,_=run(dict(RUN_ROOT=str(tmp/'no_warm'),MOCK_CALLS=str(tmp/'no_warm_calls'),QUALITY_WARMUP='0'))
 assert forbidden.returncode!=0 and not (tmp/'no_warm_calls').exists()
 result,issues=run();assert result.returncode==0,(result.stdout,result.stderr,issues)
 calls=[line.split() for line in (tmp/'calls').read_text().splitlines()];assert len(calls)==36
 routes=['incidence_ordered','projection_profile','projection_fixed']
 for repeat in range(1,4):
  order=[c[0] for c in calls if c[1:]==['natural',str(repeat),'-1']]
  assert order==routes[repeat%3:]+routes[:repeat%3]
 comparisons=list(csv.DictReader((tmp/'results/p2/route_comparisons.csv').open()))
 assert len(comparisons)==9 and all(r['validated']=='True' for r in comparisons)
 sys.path.insert(0,str(ROOT/'strong_scaling'));from analyze_results import inspect,write_csv
 from analyze_worklet_routes import analyze
 base=tmp/'results/route_projection_fixed/p2/sparse_natural'
 warm,_=inspect(base/'repeat_0/rank_profiles.jsonl.gz');formal,_=inspect(base/'repeat_1/rank_profiles.jsonl.gz')
 assert warm['front_projection_verified']==warm['front_projection_calls']>0
 assert formal['front_projection_verified']==formal['front_projection_reference_seconds']==0
 result,issues=run(dict(RUN_ROOT=str(tmp/'failed'),MOCK_CALLS=str(tmp/'failed_calls'),MOCK_PROJECTION_FAILURE='1'))
 assert result.returncode==1,(result.stdout,result.stderr,issues)
 bad=tmp/'failed/route_projection_fixed/p2/sparse_seed17_natural/repeat_1'
 assert not (bad/'SUCCESS').exists() and not (bad/'algorithm_certificate.csv').exists()
 assert 'front projection compiled work mismatch' in (bad/'failure_reason.txt').read_text()
 assert (bad/'rank_profiles.jsonl.gz').exists() and (bad/'failure_symbols.json').exists()
 assert (tmp/'failed/route_projection_fixed/p2/sparse_seed41_natural/repeat_3/SUCCESS').exists()
 ignored=tmp/'ignored';result,issues=run(dict(RUN_ROOT=str(ignored),MOCK_CALLS=str(tmp/'ignored_calls'),MOCK_IGNORED_PROJECTION='1'))
 assert result.returncode==1,(result.stdout,result.stderr,issues)
 warmdir=ignored/'route_projection_fixed/p2/sparse_natural/repeat_0'
 assert 'requested front projection mode was not executed' in (warmdir/'failure_reason.txt').read_text()
 assert not (warmdir/'SUCCESS').exists() and not (warmdir/'algorithm_certificate.csv').exists()
 skipped=ignored/'route_projection_fixed/p2/sparse_seed17_natural/repeat_3'
 assert 'dependent_quality_warmup_failure' in (skipped/'failure_reason.txt').read_text() and not (skipped/'run.log').exists()
 assert len((tmp/'ignored_calls').read_text().splitlines())==27
 # Global equality cannot hide changed per-rank rebuild input.
 profile=base/'repeat_1/rank_profiles.jsonl.gz'
 with gzip.open(profile,'rt') as stream:rows=[json.loads(line) for line in stream]
 for row,delta in zip(rows,(1,-1)):row['metrics']['front_projection_triangles']+=delta
 with gzip.open(profile,'wt') as stream:stream.write(''.join(json.dumps(row)+'\n' for row in rows))
 assert not analyze(tmp/'results',2,routes)
 assert 'certificate per-rank work mismatch' in (tmp/'results/p2/route_issues.txt').read_text()
 cert=[];inspect(profile,certificate_sink=cert);write_csv(base/'repeat_1/algorithm_certificate.csv',cert)
 assert not analyze(tmp/'results',2,routes)
 assert 'per-rank projection solve work changed' in (tmp/'results/p2/route_issues.txt').read_text()
 binary.write_text(script.replace('fixed_projection_v1','old_projections_binary'))
 result,_=run(dict(RUN_ROOT=str(tmp/'stale'),MOCK_CALLS=str(tmp/'stale_calls')))
 assert result.returncode!=0 and not (tmp/'stale_calls').exists()
print('PASS: 36 starts, 9 paired gates, replay/formal isolation, ignored-option rejection, dependent warmup skip, failure continuation, rank drift and stale-binary rejection')
