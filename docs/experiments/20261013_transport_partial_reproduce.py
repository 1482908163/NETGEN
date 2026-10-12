"""Certificate-level checks for an unpaired experiment, never speedup claims."""
import argparse,csv,hashlib,json,math,statistics as st
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('manifest',type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
manifest=json.loads(a.manifest.read_text());files={e['path']:e for e in manifest['files']};checked={};runs=[];quality=[]
source='205ed97ffc3a7961b32f1b138486efa4ea9ae4f4';binary='94030d0b537745ee5b0fdcc4cb788ff3acd34f0940716ea05ef085f2bf4d91cb'
def read(path):
    b=(a.root/path).read_bytes();h=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest();assert h==files[path]['sha'],path
    checked[path]=dict(git_blob=h,sha256=hashlib.sha256(b).hexdigest());return b
for n in (128,256):
    receipt=json.loads(read(f'p{n}/mpi_startup/receipt.json'));assert receipt['success'] and receipt['exit_code']==0 and receipt['identity']['ranks']==n
    assert receipt['identity']['binary_sha256']==binary
    issue=read(f'p{n}/route_issues.txt').decode();assert issue.count('communication baseline requires one complete local mesh per rank')==3
    assert issue.count('dependent_quality_warmup_failure')==9
    for seed in (-1,17,41):
        stem='sparse'+('' if seed==-1 else f'_seed{seed}')+'_natural'
        qpath=f'route_ghost_dictionary/p{n}/{stem}/repeat_0/quality_summary.json'
        q=json.loads(read(qpath));assert q['structural_pass'] and not q['issues']
        quality.append(dict(ranks=n,seed=seed,elements=q['counts']['elements'],structural_pass=True))
        for repeat in range(4):
            path=f'route_ghost_dictionary/p{n}/{stem}/repeat_{repeat}/ghost_transport_certificate.csv'
            rows=list(csv.DictReader(read(path).decode().splitlines()));rows.sort(key=lambda r:int(r['rank']))
            assert [int(r['rank']) for r in rows]==list(range(n));assert len({r['core_seconds'] for r in rows})==1
            mode='vertex_dictionary_reference_v1' if repeat==0 else 'vertex_dictionary_transport_v1'
            for r in rows:
                assert (r['source_revision'],r['binary_sha256'],r['algorithm'],r['timing'],r['ghost_transport'])==(source,binary,'sparse','natural',mode)
                assert (int(r['ranks']),int(r['partition_seed']),int(r['repeat']))==(n,seed,repeat)
                v={k.removeprefix('ghost_transport_'):float(x) for k,x in r.items() if k.startswith('ghost_transport_') and k!='ghost_transport'}
                assert all(math.isfinite(x) and x>=0 for x in v.values())
                assert v['calls']==1 and v['mismatches']==0
                for direction,peer_key in (('send','volume_num_s'),('receive','volume_num_r')):
                    peers=float(r[peer_key]);assert v['raw_'+direction+'_bytes']==140*v[direction+'_items']
                    assert v['metadata_'+direction+'_bytes']==24*peers and v['metadata_'+direction+'_messages']==peers
                    assert v['dictionary_'+direction+'_peers']+v['literal_'+direction+'_peers']==peers
                    assert v['payload_'+direction+'_bytes']<=v['raw_'+direction+'_bytes']
                assert v['verify_calls']==int(repeat==0)
                assert v['verified_items']==(v['receive_items'] if repeat==0 else 0)
                assert v['verified_peers']==(float(r['volume_num_r']) if repeat==0 else 0)
                if repeat:assert v['reference_seconds']==0
                assert sum(v[k+'_seconds'] for k in ('prepare','metadata','exchange','decode','release'))<=v['total_seconds']+1e-6
                assert v['total_seconds']<=v['full_seconds']+1e-6 and v['full_seconds']<=float(r['core_seconds'])+1e-3
            totals={k:sum(float(r['ghost_transport_'+k]) for r in rows) for k in ('send_items','receive_items','raw_send_bytes','raw_receive_bytes','payload_send_bytes','payload_receive_bytes','metadata_send_bytes','metadata_receive_bytes','verified_items','mismatches')}
            for prefix in ('raw','payload','metadata'):assert totals[prefix+'_send_bytes']==totals[prefix+'_receive_bytes']
            assert totals['send_items']==totals['receive_items']
            reduction=100*(1-(totals['payload_send_bytes']+totals['metadata_send_bytes'])/totals['raw_send_bytes'])
            runs.append(dict(ranks=n,seed=seed,repeat=repeat,certificate_rows=len(rows),payload_and_new_header_reduction_percent=reduction,**totals))
stop=json.loads(read('p512/ALLOCATION_STOPPED.json'));assert stop['reason']=='mpi_startup_probe_failed' and stop['exit_code']==124 and stop['timeout_seconds']==60
tail=read('p512/mpi_startup/log_tail.txt').decode();assert 'pmixp_p2p_send' in tail and 'send failed' in tail
runtime=json.loads(read('runtime_manifest.json'));assert runtime['source_revision']==source
summary=[]
for n in (128,256):
    measured=[r for r in runs if r['ranks']==n and r['repeat']>0]
    values=[r['payload_and_new_header_reduction_percent'] for r in measured]
    summary.append(dict(ranks=n,candidate_formal_runs=len(measured),control_formal_runs=0,wire_reduction_percent_min=min(values),wire_reduction_percent_max=max(values),wire_reduction_percent_median=st.median(values)))
result=dict(result_commit=manifest['commit'],experiment_source=source,binary_sha256=binary,paired_performance_valid=False,control_formal_runs=0,candidate_formal_runs=18,candidate_warmup_runs=6,control_warmups_misclassified=6,verified_receive_records=sum(r['verified_items'] for r in runs),quality=quality,summary=summary,runs=runs,failure_512=stop,source_files=checked)
s=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
if a.output:a.output.write_text(s)
else:print(s)
