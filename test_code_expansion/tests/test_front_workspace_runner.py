#!/usr/bin/env python3
"""Real unified orchestration with synthetic evidence, never mesh performance."""
import ast,csv,gzip,json,os,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
tree=ast.parse((ROOT/'tests/test_generation_cost_runner.py').read_text())
script=next(n.args[0].value for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
 and n.func.attr=='write_text' and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str) and '# phase_full_cost_v3 cost_' in n.args[0].value)
script=script.replace('#!/usr/bin/env python3','#!/usr/bin/env python3\n# --incidence-build original_incidence_profile_v1 ordered_incidence_v1 ordered_incidence_replay_v1 incidence_\n# --ghost-codec-audit vertex_dictionary_audit_v1 ghost_codec_\n# --front-workspace temporary_workspace_v1 reused_workspace_v1 reused_workspace_replay_v1 front_workspace_',1)
script=script.replace('# --split-active-evaluation original_ranges_v1 split_active_','# --split-active-evaluation original_ranges_v1 active_queue_v1 active_exact_replay_v1 split_active_')
script=script.replace('from test_cost_profile import fixture','from test_front_workspace_validation import fixture\nfrom test_ghost_codec_validation import attach')
script=script.replace("route=value('--kernel-scheduler')", """scheduler=value('--kernel-scheduler')
last=lambda key:int(sys.argv[len(sys.argv)-1-sys.argv[::-1].index(key)+1])
fixed=last('--repair-fixed-point');active=last('--split-active-evaluation')
assert scheduler=='cost_profile' and fixed in (1,2) and active==fixed
assert last('--front-distance')==last('--front-transform')==0
assert '--front-projection' not in sys.argv and '--front-components' not in sys.argv and '--front-rules' not in sys.argv and '--front-lookup' not in sys.argv
build=last('--incidence-build');rules=last('--front-workspace') if '--front-workspace' in sys.argv else -1
assert build==(2 if '--validate-volume' in sys.argv else 1)
assert rules in (0,1,2)
route='workspace_temporary' if rules==0 else 'workspace_reuse'
assert rules==(2 if '--validate-volume' in sys.argv else 1) if rules>0 else True""")
script=script.replace("route in ('batch_parallel','cost_profile')","route in ('workspace_temporary','workspace_reuse')")
script=script.replace('rows=fixture(route,repeat,mode)',"""rows=fixture(route,repeat,mode)
if '--ghost-codec-audit' in sys.argv:
    assert repeat==0 and '--validate-volume' in sys.argv
    attach(rows)
if os.environ.get('MOCK_IGNORED_CODEC')=='1':
    for row in rows:
        row['metadata'].pop('ghost_codec_audit',None)
        row['metrics']={k:v for k,v in row['metrics'].items() if not k.startswith('ghost_codec_')}
for row in rows:
    for key in ('MESH_SOURCE_REVISION','MESH_BINARY_SHA256'):row['metadata'][key]=os.environ[key]
if os.environ.get('MOCK_WORKSPACE_FAILURE')=='1' and (route,repeat,seed)==('workspace_reuse',1,17):rows[0]['metrics']['front_workspace_heap_requests']+=1
if os.environ.get('MOCK_IGNORED_WORKSPACE')=='1' and route=='workspace_reuse':
    for row in rows:
        row['metadata'].pop('volume_front_workspace',None)
        row['metrics']={k:v for k,v in row['metrics'].items() if not k.startswith('front_workspace_')}
""")
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);launcher=tmp/'launcher';launcher.write_text('#!/bin/bash\nshift 2\nexec "$@"\n');launcher.chmod(0o755)
    binary=tmp/'mesh';binary.write_text(script);binary.chmod(0o755)
    source=tmp/'input.step';source.write_text('input');lib=tmp/'lib';lib.mkdir();(lib/'libnglib.so').write_text('library')
    env=dict(os.environ)
    for k in ('EXPERIMENT_STAGE','WORKLET_ROUTES','KERNEL_REPEAT','PARTITION_SEEDS','ALGORITHMS','TIMING_MODES','STRONG_SCALING_DIR','REPAIR_FIXED_POINT','SPLIT_ACTIVE_EVALUATION','FRONT_DISTANCE','FRONT_TRANSFORM','FRONT_PROJECTION','FRONT_RULES','FRONT_LOOKUP','LOOKUP_PROTOCOL_REQUIRED','FRONT_WORKSPACE','WORKSPACE_PROTOCOL_REQUIRED','RULE_PROTOCOL_REQUIRED','FRONT_COMPONENTS','INCIDENCE_BUILD','REPEATS','MOCK_WORKSPACE_FAILURE','MOCK_IGNORED_WORKSPACE','MOCK_COST_FAILURE','MOCK_FAIL','MOCK_IGNORED_CODEC','GHOST_CODEC_AUDIT'):env.pop(k,None)
    env.update(EXPERIMENT_PRESET='front_workspace',MESH_EXPERIMENT_WORKER='1',PROCESS_COUNT='2',PROCESS_COUNTS='2',
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
    calls=[line.split() for line in (tmp/'calls').read_text().splitlines()];assert len(calls)==24
    routes=['workspace_temporary','workspace_reuse']
    for repeat in range(1,4):
        order=[c[0] for c in calls if c[1:]==['natural',str(repeat),'-1']]
        assert order==routes[repeat%2:]+routes[:repeat%2]
    comparisons=list(csv.DictReader((tmp/'results/p2/route_comparisons.csv').open()))
    assert len(comparisons)==3 and all(r['validated']=='True' for r in comparisons)
    pairs=list(csv.DictReader((tmp/'results/p2/front_workspace_fixed_rank_pairs.csv').open()))
    summary=list(csv.DictReader((tmp/'results/p2/front_workspace_summary.csv').open()))
    assert len(pairs)==9 and len(summary)==3
    sys.path.insert(0,str(ROOT/'strong_scaling'))
    from analyze_results import inspect,write_csv
    from analyze_worklet_routes import analyze
    from analyze_front_workspace import analyze as cost_screen
    base=tmp/'results/route_workspace_reuse/p2/sparse_natural'
    warm,_=inspect(base/'repeat_0/rank_profiles.jsonl.gz');formal,_=inspect(base/'repeat_1/rank_profiles.jsonl.gz')
    assert warm['front_workspace_verified']==warm['front_workspace_calls']>0
    assert formal['front_workspace_verified']==formal['front_workspace_reference_seconds']==0
    from analyze_ghost_codec import analyze as codec_screen
    receipts=list(csv.DictReader((base/'repeat_0/ghost_codec_certificate.csv').open()))
    assert len(receipts)==2 and warm['ghost_codec_calls']==2
    assert 'ghost_codec_audit' not in formal and not (base/'repeat_1/ghost_codec_certificate.csv').exists()
    assert (tmp/'results/p2/GHOST_CODEC_SUMMARY.txt').is_file()
    codec_certificate=base/'repeat_0/ghost_codec_certificate.csv';saved=codec_certificate.read_bytes()
    receipts[0]['ghost_codec_raw_bytes']='999';write_csv(codec_certificate,receipts)
    try:codec_screen(tmp/'results',2)
    except ValueError as error:assert 'certificate mismatch' in str(error)
    else:raise AssertionError('forged codec receipt accepted')
    codec_certificate.write_bytes(saved)
    result,issues=run(dict(RUN_ROOT=str(tmp/'codec_missing'),MOCK_CALLS=str(tmp/'codec_missing_calls'),MOCK_IGNORED_CODEC='1'))
    assert result.returncode==1 and len((tmp/'codec_missing_calls').read_text().splitlines())==6,(result.stdout,result.stderr,issues)
    reason=tmp/'codec_missing/route_workspace_temporary/p2/sparse_natural/repeat_0/failure_reason.txt'
    assert 'requested ghost codec audit was not executed' in reason.read_text()
    # A copied per-rank compute time cannot silently alter the cost screen.
    certificate=base/'repeat_1/front_workspace_certificate.csv';original=certificate.read_bytes()
    ledger=list(csv.DictReader(certificate.open()));ledger[0]['compute_seconds']='999'
    write_csv(certificate,ledger)
    try:cost_screen(tmp/'results',2)
    except ValueError as error:assert 'certificate timing/work mismatch' in str(error)
    else:raise AssertionError('forged rank time accepted')
    certificate.write_bytes(original)
    result,issues=run(dict(RUN_ROOT=str(tmp/'failed'),MOCK_CALLS=str(tmp/'failed_calls'),MOCK_WORKSPACE_FAILURE='1'))
    assert result.returncode==1,(result.stdout,result.stderr,issues)
    bad=tmp/'failed/route_workspace_reuse/p2/sparse_seed17_natural/repeat_1'
    assert not (bad/'SUCCESS').exists() and not (bad/'algorithm_certificate.csv').exists()
    assert 'workspace allocation conservation mismatch' in (bad/'failure_reason.txt').read_text()
    assert (bad/'rank_profiles.jsonl.gz').exists() and (bad/'failure_symbols.json').exists()
    assert (tmp/'failed/route_workspace_reuse/p2/sparse_seed41_natural/repeat_3/SUCCESS').exists()
    ignored=tmp/'ignored';result,issues=run(dict(RUN_ROOT=str(ignored),MOCK_CALLS=str(tmp/'ignored_calls'),MOCK_IGNORED_WORKSPACE='1'))
    assert result.returncode==1,(result.stdout,result.stderr,issues)
    warmdir=ignored/'route_workspace_reuse/p2/sparse_natural/repeat_0'
    assert 'requested front workspace mode was not executed' in (warmdir/'failure_reason.txt').read_text()
    skipped=ignored/'route_workspace_reuse/p2/sparse_seed17_natural/repeat_3'
    assert 'dependent_quality_warmup_failure' in (skipped/'failure_reason.txt').read_text() and not (skipped/'run.log').exists()
    assert len((tmp/'ignored_calls').read_text().splitlines())==15
    # Even an apparently better quality detail invalidates semantic equivalence.
    qpath=base/'repeat_0/quality_summary.json';qoriginal=qpath.read_bytes()
    q=json.loads(qoriginal);q['unexpected_quality_change']=True;qpath.write_text(json.dumps(q))
    try:cost_screen(tmp/'results',2)
    except ValueError as error:assert 'full quality changed' in str(error)
    else:raise AssertionError('quality drift accepted')
    qpath.write_bytes(qoriginal)
    result,issues=run(dict(RUN_ROOT=str(tmp/'audit_off'),MOCK_CALLS=str(tmp/'audit_off_calls'),GHOST_CODEC_AUDIT='0',REPEATS='1'))
    assert result.returncode==0,(result.stdout,result.stderr,issues)
    assert not (tmp/'audit_off/p2/GHOST_CODEC_SUMMARY.txt').exists()
    # Equal global work cannot hide a redistribution between two ranks.
    profile=base/'repeat_1/rank_profiles.jsonl.gz'
    with gzip.open(profile,'rt') as stream:rows=[json.loads(line) for line in stream]
    for row,delta in zip(rows,(1,-1)):
        for key,factor in (('vector_entries',1),):row['metrics']['front_workspace_'+key]+=factor*delta
    with gzip.open(profile,'wt') as stream:stream.write(''.join(json.dumps(row)+'\n' for row in rows))
    cert=[];ledger=[];inspect(profile,certificate_sink=cert,lookup_sink=ledger)
    write_csv(base/'repeat_1/algorithm_certificate.csv',cert);write_csv(certificate,ledger)
    assert not analyze(tmp/'results',2,routes)
    assert 'per-rank front workspace work changed' in (tmp/'results/p2/route_issues.txt').read_text()
    assert not (tmp/'results/p2/front_workspace_summary.csv').exists()
    assert '未发布' in (tmp/'results/p2/FRONT_WORKSPACE_SUMMARY.txt').read_text()
    binary.write_text(script.replace('reused_workspace_v1','old_lookup_binary'))
    result,_=run(dict(RUN_ROOT=str(tmp/'stale'),MOCK_CALLS=str(tmp/'stale_calls')))
    assert result.returncode!=0 and not (tmp/'stale_calls').exists()
print('PASS: 24 starts, 3 paired gates, fixed-rank full-cost reports, forged timing/rank rejection, warmup gates and failure continuation')
