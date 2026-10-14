#!/usr/bin/env python3
"""Actual five-route orchestration, synthetic native output; never performance."""
import ast,csv,json,os,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
tree=ast.parse((ROOT/'tests/test_ghost_transport_runner.py').read_text())
nodes=[]
for node in tree.body:
    if isinstance(node,ast.With):break
    nodes.append(node)
scope=dict(__file__=str(ROOT/'tests/test_ghost_transport_runner.py'));exec(compile(ast.Module(body=nodes,type_ignores=[]),'<transport generator>','exec'),scope,scope)
script=scope['script']
script=script.replace('# --mpi-preflight [MPI_PREFLIGHT_OK]','# --mpi-preflight [MPI_PREFLIGHT_OK]\n# --multi-point --multi-point-verify exact_local_work_v1 multi_front_ multi_pack_ multi_unpack_ csr_exact_pack_v1 flat_gid_index_v1')
script=script.replace('from test_ghost_transport_validation import fixture','from test_multi_point_validation import fixture')
script=script.replace("route='ghost_literal' if transport==0 else 'ghost_dictionary'", """assert transport==0
mask=last('--multi-point');assert mask in (0,1,2,4,6,7,10,14,22,30)
route={0:'multi_control',1:'multi_front',2:'multi_pack',4:'multi_unpack',6:'multi_exchange',7:'multi_all',14:'multi_pack_compact',22:'multi_unpack_flat',30:'multi_layout',10:'multi_compact_only'}[mask]
if '--validate-volume' in sys.argv:assert last('--multi-point-verify')==1
else:assert '--multi-point-verify' not in sys.argv""")
script=script.replace("route in ('ghost_literal','ghost_dictionary')","route.startswith('multi_')")
script=script.replace('rows=fixture(route,repeat,mode)',"""rows=fixture(route,repeat,mode)
if os.environ.get('MOCK_IGNORE_MULTI')=='1' and route=='multi_front':
    for row in rows:
        for k in ('multi_point_policy','multi_point_mask','multi_point_verification'):row['metadata'].pop(k,None)
        row['metrics']={k:v for k,v in row['metrics'].items() if not k.startswith('multi_')}
if os.environ.get('MOCK_BAD_MULTI')=='1' and (route,repeat,seed)==('multi_pack',1,17):rows[0]['metrics']['multi_pack_send_items']+=1
""")

def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);launcher=tmp/'launcher';launcher.write_text('#!/bin/bash\nshift 2\nexec "$@"\n');launcher.chmod(0o755)
        binary=tmp/'mesh';binary.write_text(script);binary.chmod(0o755)
        source=tmp/'input.step';source.write_text('input');lib=tmp/'lib';lib.mkdir();(lib/'libnglib.so').write_text('library')
        env=dict(os.environ)
        for k in ('EXPERIMENT_STAGE','KERNEL_REPEAT','WORKLET_ROUTES','PARTITION_SEEDS','ALGORITHMS','TIMING_MODES','STRONG_SCALING_DIR','GHOST_DICTIONARY','TRANSPORT_PROTOCOL_REQUIRED','MPI_STARTUP_CHECK','MPI_STARTUP_PROBE_DIR','GHOST_CODEC_AUDIT','MULTI_POINT','MULTI_POINT_PROTOCOL_REQUIRED','WORKSPACE_PROTOCOL_REQUIRED','LOOKUP_PROTOCOL_REQUIRED','RULE_PROTOCOL_REQUIRED','MOCK_IGNORE_MULTI','MOCK_BAD_MULTI','MOCK_STARTUP_FAIL','MULTI_LAYOUT_PROTOCOL_REQUIRED'):env.pop(k,None)
        env.update(EXPERIMENT_PRESET='multi_point',MESH_EXPERIMENT_WORKER='1',PROCESS_COUNT='2',PROCESS_COUNTS='2',RANKS_PER_NODE='2',CPUS_PER_TASK='2',KERNEL_THREADS='2',WARMUPS='1',LEVELS='0',REFINES='0',LOAD_MODULES='0',CLUSTER_ENV_STRICT='0',NETGEN_INSTALL_LIB=str(lib),MPI_LAUNCHER=str(launcher),MPI_EXTRA_ARGS=' ',START_EPOCH='0',BINARY=str(binary),INPUT_PATH=str(source),MOCK_FIXTURES=str(ROOT/'tests'),RESUME='0',TIMEOUT_SECONDS='10',MPI_STARTUP_TIMEOUT='2')
        def run(name,**extra):
            root=tmp/name;calls=tmp/(name+'_calls');probes=tmp/(name+'_probes')
            r=subprocess.run(['bash',str(ROOT/'strong_scaling/run_experiments.sh')],env=dict(env,RUN_ROOT=str(root),MOCK_CALLS=str(calls),MOCK_PROBES=str(probes),**extra),capture_output=True,text=True,timeout=300)
            errors=[p.read_text() for p in root.rglob('failure_reason.txt')]+[p.read_text() for p in root.rglob('route_issues.txt')]
            return r,root,calls,probes,errors
        r,root,calls,probes,errors=run('success');assert r.returncode==0,(r.stdout,r.stderr,errors)
        assert len(calls.read_text().splitlines())==60 and probes.read_text()=='probe\n'
        pairs=list(csv.DictReader((root/'p2/multi_point_pairs.csv').open()));assert len(pairs)==36
        assert {p['route'] for p in pairs}=={'multi_front','multi_pack','multi_unpack','multi_all'}
        assert all(int(p['control_core_slowest_rank'])==1 for p in pairs)
        sys.path.insert(0,str(ROOT/'strong_scaling'))
        from analyze_multi_point import analyze
        from analyze_results import write_csv
        path=root/'route_multi_all/p2/sparse_natural/repeat_1/multi_point_certificate.csv';saved=path.read_bytes();rows=list(csv.DictReader(path.open()));rows[0]['local_core_seconds']='999';write_csv(path,rows)
        try:analyze(root,2,['multi_control','multi_front','multi_pack','multi_unpack','multi_all'])
        except ValueError as e:assert 'certificate timing/work mismatch' in str(e)
        else:raise AssertionError('forged local core accepted')
        path.write_bytes(saved)
        r,root,calls,probes,errors=run('ignored',MOCK_IGNORE_MULTI='1');assert r.returncode==1,(r.stdout,r.stderr,errors)
        assert len(calls.read_text().splitlines())==51,(len(calls.read_text().splitlines()),r.stdout[-4000:],r.stderr[-4000:],errors)
        assert any('requested multi point mask was not executed' in s for s in errors)
        assert not (root/'p2/multi_point_pairs.csv').exists()
        r,root,calls,probes,errors=run('exchange',EXPERIMENT_PRESET='multi_exchange');assert r.returncode==0,(r.stdout,r.stderr,errors)
        assert len(calls.read_text().splitlines())==48 and probes.read_text()=='probe\n'
        exchange_pairs=list(csv.DictReader((root/'p2/multi_point_pairs.csv').open()))
        assert len(exchange_pairs)==27 and {p['route'] for p in exchange_pairs}=={'multi_pack','multi_unpack','multi_exchange'}
        assert all(int(p['mask']) in (2,4,6) for p in exchange_pairs)
        assert not (root/'route_multi_front').exists() and not (root/'route_multi_all').exists()
        r,root,calls,probes,errors=run('layout',EXPERIMENT_PRESET='multi_layout');assert r.returncode==0,(r.stdout,r.stderr,errors)
        assert len(calls.read_text().splitlines())==72 and probes.read_text()=='probe\n'
        layout_pairs=list(csv.DictReader((root/'p2/multi_point_pairs.csv').open()))
        assert len(layout_pairs)==45 and {int(p['mask']) for p in layout_pairs}=={2,6,14,22,30}
        incremental=list(csv.DictReader((root/'p2/multi_layout_incremental.csv').open()))
        assert len(incremental)==54 and all(float(p['core_reduction_percent'])==0 for p in incremental)
        r,root,calls,probes,errors=run('followup',EXPERIMENT_PRESET='multi_layout_followup');assert r.returncode==0,(r.stdout,r.stderr,errors)
        assert len(calls.read_text().splitlines())==60 and probes.read_text()=='probe\n'
        followup_pairs=list(csv.DictReader((root/'p2/multi_point_pairs.csv').open()))
        assert len(followup_pairs)==36 and {int(p['mask']) for p in followup_pairs}=={2,10,14,30}
        followup_incremental=list(csv.DictReader((root/'p2/multi_layout_incremental.csv').open()))
        assert len(followup_incremental)==45
        assert {(p['candidate'],p['baseline']) for p in followup_incremental}=={('multi_layout','multi_pack'),('multi_layout','multi_pack_compact'),('multi_compact_only','multi_pack'),('multi_pack_compact','multi_compact_only'),('multi_layout','multi_compact_only')}
        # Previous binary markers must be rejected before launching any mesh.
        binary.write_text(script.replace('csr_exact_pack_v1 flat_gid_index_v1',''))
        r,root,calls,probes,errors=run('stale_layout_binary',EXPERIMENT_PRESET='multi_layout')
        assert r.returncode!=0 and not calls.exists(),(r.stdout,r.stderr,errors)
        binary.write_text(script)
        r,root,calls,probes,errors=run('bad_formal',MOCK_BAD_MULTI='1');assert r.returncode==1,(r.stdout,r.stderr,errors)
        assert len(calls.read_text().splitlines())==60 and (root/'route_multi_all/p2/sparse_seed41_natural/repeat_3/SUCCESS').is_file()
        assert not (root/'p2/multi_point_pairs.csv').exists()
        r,root,calls,probes,errors=run('startup_failure',MOCK_STARTUP_FAIL='1');assert r.returncode==75,(r.stdout,r.stderr,errors)
        assert not calls.exists() and (root/'p2/ALLOCATION_STOPPED.json').is_file()
    print('PASS: five-route 60 starts/36 pairs; exchange 48 starts/27 pairs; layout 72 starts/45 pairs and 54 incremental contrasts; followup 60 starts/36 pairs and 45 incremental contrasts; stale binary rejected; ignored-mode gate, forged receipts and failure continuation')
if __name__=='__main__':main()
