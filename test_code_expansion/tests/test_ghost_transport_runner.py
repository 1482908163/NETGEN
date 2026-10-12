#!/usr/bin/env python3
"""Real orchestration and failure gates with synthetic profiles, not speed."""
import ast,csv,json,os,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
tree=ast.parse((ROOT/'tests/test_generation_cost_runner.py').read_text())
script=next(n.args[0].value for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='write_text' and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str) and '# phase_full_cost_v3 cost_' in n.args[0].value)
script=script.replace('#!/usr/bin/env python3','#!/usr/bin/env python3\n# --incidence-build original_incidence_profile_v1 ordered_incidence_v1 ordered_incidence_replay_v1 incidence_\n# --front-workspace temporary_workspace_v1 reused_workspace_v1 reused_workspace_replay_v1 front_workspace_\n# --ghost-dictionary literal_transport_v1 vertex_dictionary_transport_v1 vertex_dictionary_reference_v1 ghost_transport_\n# --mpi-preflight [MPI_PREFLIGHT_OK]',1)
script=script.replace('original_ranges_v1 split_active_','original_ranges_v1 active_queue_v1 active_exact_replay_v1 split_active_')
script=script.replace('from test_cost_profile import fixture',"""if sys.argv[1:]==['--mpi-preflight']:
    with open(os.environ['MOCK_PROBES'],'a') as stream:stream.write('probe\\n')
    if os.environ.get('MOCK_STARTUP_FAIL')=='1':sys.exit(15)
    print('[MPI_PREFLIGHT_OK] ranks=2 thread_support=1');sys.exit(0)
from test_ghost_transport_validation import fixture""")
script=script.replace("route=value('--kernel-scheduler')", """last=lambda key:int(sys.argv[len(sys.argv)-1-sys.argv[::-1].index(key)+1])
assert value('--kernel-scheduler')=='cost_profile'
transport=last('--ghost-dictionary');assert transport in (0,1,2)
assert last('--front-workspace')==0 and last('--incidence-build')==(2 if '--validate-volume' in sys.argv else 1)
assert last('--repair-fixed-point')==last('--split-active-evaluation')==(2 if '--validate-volume' in sys.argv else 1)
assert '--ghost-codec-audit' not in sys.argv
route='ghost_literal' if transport==0 else 'ghost_dictionary'
if transport==2:assert '--validate-volume' in sys.argv
if transport==1:assert '--validate-volume' not in sys.argv""")
script=script.replace("route in ('batch_parallel','cost_profile')","route in ('ghost_literal','ghost_dictionary')")
script=script.replace('rows=fixture(route,repeat,mode)',"""rows=fixture(route,repeat,mode)
for row in rows:
    for key in ('MESH_SOURCE_REVISION','MESH_BINARY_SHA256'):row['metadata'][key]=os.environ[key]
if os.environ.get('MOCK_IGNORED_TRANSPORT')=='1' and route=='ghost_dictionary':
    for row in rows:
        row['metadata'].pop('ghost_transport',None)
        row['stages']={k:v for k,v in row['stages'].items() if not k.startswith('ghost_dictionary_')}
        row['metrics']={k:v for k,v in row['metrics'].items() if not k.startswith('ghost_transport_')}
if os.environ.get('MOCK_TRANSPORT_FAILURE')=='1' and (route,repeat,seed)==('ghost_dictionary',1,17):rows[0]['metrics']['ghost_transport_payload_send_bytes']+=1
""")
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);launcher=tmp/'launcher';launcher.write_text('#!/bin/bash\nshift 2\nexec "$@"\n');launcher.chmod(0o755)
    binary=tmp/'mesh';binary.write_text(script);binary.chmod(0o755)
    source=tmp/'input.step';source.write_text('input');lib=tmp/'lib';lib.mkdir();(lib/'libnglib.so').write_text('library')
    env=dict(os.environ)
    for key in ('EXPERIMENT_STAGE','KERNEL_REPEAT','WORKLET_ROUTES','PARTITION_SEEDS','ALGORITHMS','TIMING_MODES','STRONG_SCALING_DIR','GHOST_DICTIONARY','TRANSPORT_PROTOCOL_REQUIRED','MPI_STARTUP_CHECK','MPI_STARTUP_PROBE_DIR','GHOST_CODEC_AUDIT','WORKSPACE_PROTOCOL_REQUIRED','LOOKUP_PROTOCOL_REQUIRED','RULE_PROTOCOL_REQUIRED','MOCK_IGNORED_TRANSPORT','MOCK_TRANSPORT_FAILURE','MOCK_STARTUP_FAIL'):env.pop(key,None)
    env.update(EXPERIMENT_PRESET='ghost_dictionary',MESH_EXPERIMENT_WORKER='1',PROCESS_COUNT='2',PROCESS_COUNTS='2',RANKS_PER_NODE='2',CPUS_PER_TASK='2',KERNEL_THREADS='2',WARMUPS='1',LEVELS='0',REFINES='0',LOAD_MODULES='0',CLUSTER_ENV_STRICT='0',NETGEN_INSTALL_LIB=str(lib),MPI_LAUNCHER=str(launcher),MPI_EXTRA_ARGS=' ',START_EPOCH='0',BINARY=str(binary),INPUT_PATH=str(source),MOCK_FIXTURES=str(ROOT/'tests'),RESUME='0',TIMEOUT_SECONDS='10',MPI_STARTUP_TIMEOUT='2')
    command=['bash',str(ROOT/'strong_scaling/run_experiments.sh')]
    def run(name,**extra):
        root=tmp/name;calls=tmp/(name+'_calls');probes=tmp/(name+'_probes')
        r=subprocess.run(command,env=dict(env,RUN_ROOT=str(root),MOCK_CALLS=str(calls),MOCK_PROBES=str(probes),**extra),capture_output=True,text=True,timeout=240)
        issues=[p.read_text() for p in root.rglob('failure_reason.txt')]+[p.read_text() for p in root.rglob('route_issues.txt')]
        return r,root,calls,probes,issues
    r,root,calls,probes,issues=run('success');assert r.returncode==0,(r.stdout,r.stderr,issues)
    assert len(calls.read_text().splitlines())==24 and probes.read_text()=='probe\n'
    pairs=list(csv.DictReader((root/'p2/ghost_transport_pairs.csv').open()));assert len(pairs)==9 and all(int(x['rank'])==1 for x in pairs)
    assert (root/'p2/GHOST_TRANSPORT_SUMMARY.txt').is_file()
    sys.path.insert(0,str(ROOT/'strong_scaling'))
    from analyze_ghost_transport import analyze
    from analyze_results import write_csv
    certificate=root/'route_ghost_dictionary/p2/sparse_natural/repeat_1/ghost_transport_certificate.csv';saved=certificate.read_bytes();rows=list(csv.DictReader(certificate.open()));rows[0]['ghost_transport_full_seconds']='999';write_csv(certificate,rows)
    try:analyze(root,2)
    except ValueError as error:assert 'certificate timing/work mismatch' in str(error)
    else:raise AssertionError('forged full cost accepted')
    certificate.write_bytes(saved)
    r,root,calls,probes,issues=run('startup_failure',MOCK_STARTUP_FAIL='1');assert r.returncode==75,(r.stdout,r.stderr,issues)
    assert not calls.exists() and probes.read_text()=='probe\n' and (root/'p2/ALLOCATION_STOPPED.json').is_file()
    assert json.loads((root/'p2/ALLOCATION_STOPPED.json').read_text())['reason']=='mpi_startup_probe_failed'
    r,root,calls,probes,issues=run('ignored',MOCK_IGNORED_TRANSPORT='1');assert r.returncode==1,(r.stdout,r.stderr,issues)
    assert len(calls.read_text().splitlines())==15 and any('requested ghost transport mode was not executed' in s for s in issues)
    assert not (root/'p2/ghost_transport_summary.csv').exists()
    r,root,calls,probes,issues=run('bad_wire',MOCK_TRANSPORT_FAILURE='1');assert r.returncode==1,(r.stdout,r.stderr,issues)
    assert len(calls.read_text().splitlines())==24 and (root/'route_ghost_dictionary/p2/sparse_seed41_natural/repeat_3/SUCCESS').is_file()
print('PASS: 24 starts, one all-rank startup probe, full receive gates, fixed-rank complete costs, forged receipts, startup allocation stop and ordinary failure continuation')
