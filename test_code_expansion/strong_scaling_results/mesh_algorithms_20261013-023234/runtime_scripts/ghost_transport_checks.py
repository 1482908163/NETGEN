"""Actual wire bytes, complete receive oracle, and coherent full volume cost."""
import json,math,csv
from pathlib import Path
PREFIX='ghost_transport_'
MODES=('literal_transport_v1','vertex_dictionary_transport_v1','vertex_dictionary_reference_v1')
FIELDS=tuple('calls send_items receive_items raw_send_bytes raw_receive_bytes payload_send_bytes payload_receive_bytes metadata_send_bytes metadata_receive_bytes dictionary_send_peers dictionary_receive_peers literal_send_peers literal_receive_peers payload_send_messages payload_receive_messages metadata_send_messages metadata_receive_messages verify_calls verified_items verified_peers mismatches retained_wire_bytes_peak prepare_seconds metadata_seconds exchange_seconds decode_seconds release_seconds total_seconds reference_seconds full_seconds'.split())
WORK=('send_items','receive_items','raw_send_bytes','raw_receive_bytes')
PHASES={'prepare':'ghost_dictionary_prepare','metadata':'ghost_dictionary_metadata','exchange':'volume_payload_exchange','decode':'ghost_dictionary_decode','release':'ghost_dictionary_release'}

def validate(rows,metadata,result):
    mode=metadata.get('ghost_transport')
    codec_stages=tuple(name for phase,name in PHASES.items() if phase!='exchange')+('ghost_dictionary_reference',)
    if mode is None:
        if any(row['stages'].get(name,{}).get('calls',0) for row in rows for name in codec_stages):raise ValueError('dictionary stages without transport protocol')
        if any(k.startswith(PREFIX) for row in rows for k in row['metrics']):raise ValueError('transport metrics without protocol')
        return
    if mode not in MODES or metadata.get('timing_mode')!='natural' or metadata.get('global_numbering')!='owner_local_v2' or metadata.get('ghost_exchange_policy','legacy_v1')!='legacy_v1' or metadata.get('volume_exchange_policy')!='batch_v1':raise ValueError('invalid ghost transport base')
    if mode==MODES[0] and any(row['stages'].get(name,{}).get('calls',0) for row in rows for name in codec_stages):raise ValueError('literal transport entered dictionary stages')
    reference=mode==MODES[2]
    if reference and (rows[0]['repeat']!=0 or metadata.get('mesh_quality')!='volume_audit_v1'):raise ValueError('transport reference outside quality warmup')
    signature=[]
    for row in rows:
        m=row['metrics'];v={f:m.get(PREFIX+f) for f in FIELDS}
        if any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 or ('seconds' not in f and (int(x)!=x or x>2**53)) for f,x in v.items()):raise ValueError('invalid ghost transport metric')
        if v['calls']!=1 or v['mismatches']:raise ValueError('ghost transport call/mismatch')
        for f,k in (('send_items','volume_send_items'),('receive_items','volume_receive_items')):
            if v[f]!=m.get(k):raise ValueError('ghost transport independent payload mismatch')
        for direction in ('send','receive'):
            items=v[direction+'_items'];raw=v['raw_'+direction+'_bytes'];payload=v['payload_'+direction+'_bytes']
            peers=m.get('volume_num_s' if direction=='send' else 'volume_num_r')
            if raw!=140*items or v['dictionary_'+direction+'_peers']+v['literal_'+direction+'_peers']!=peers or peers>items or payload>raw:raise ValueError('ghost transport byte/peer accounting mismatch')
            if (items==0)!=(payload==0) or v['payload_'+direction+'_messages']<peers:raise ValueError('ghost transport payload messages missing')
            if mode==MODES[0]:
                if v['dictionary_'+direction+'_peers'] or payload!=raw or v['metadata_'+direction+'_bytes'] or v['metadata_'+direction+'_messages'] or v['payload_'+direction+'_messages']!=peers:raise ValueError('literal transport changed wire format')
            elif v['metadata_'+direction+'_bytes']!=24*peers or v['metadata_'+direction+'_messages']!=peers:raise ValueError('dictionary metadata accounting mismatch')
            stage=row['stages'].get('volume_payload_exchange',{})
            if stage.get(direction+'_bytes')!=payload or stage.get(direction+'_messages')!=v['payload_'+direction+'_messages']:raise ValueError('transport wire profile differs from counters')
            if mode!=MODES[0]:
                header=row['stages'].get('ghost_dictionary_metadata',{})
                if header.get(direction+'_bytes')!=24*peers or header.get(direction+'_messages')!=peers:raise ValueError('transport metadata profile differs from counters')
        if v['verify_calls']!=int(reference) or v['verified_items']!=(v['receive_items'] if reference else 0) or v['verified_peers']!=(m['volume_num_r'] if reference else 0):raise ValueError('transport receive reference coverage incomplete')
        if not reference and (v['reference_seconds'] or row['stages'].get('ghost_dictionary_reference',{}).get('calls',0)):raise ValueError('transport reference entered formal cost')
        if v['total_seconds']>v['full_seconds']+1e-6 or v['full_seconds']>m['core_seconds']+1e-3 or sum(v[p+'_seconds'] for p in PHASES)>v['total_seconds']+1e-6:raise ValueError('ghost transport timing coverage mismatch')
        expected=('exchange',) if mode==MODES[0] else tuple(PHASES)
        for phase in expected:
            stage=row['stages'].get(PHASES[phase],{})
            if stage.get('calls')!=1 or v[phase+'_seconds']>1.05*stage.get('seconds',-1)+1e-3:raise ValueError('ghost transport phase scope missing')
        if reference and row['stages'].get('ghost_dictionary_reference',{}).get('calls')!=1:raise ValueError('ghost transport reference scope missing')
        if mode==MODES[0] and any(v[f] for f in ('prepare_seconds','metadata_seconds','decode_seconds','release_seconds','retained_wire_bytes_peak')):raise ValueError('literal transport entered codec')
        signature.append([row['rank']]+[v[f] for f in WORK])
        for f,x in v.items():
            key=PREFIX+f;result[key]=max(result.get(key,0),x) if 'seconds' in f or f=='retained_wire_bytes_peak' else result.get(key,0)+x
    for prefix in ('raw','payload','metadata'):
        if result[PREFIX+prefix+'_send_bytes']!=result[PREFIX+prefix+'_receive_bytes']:raise ValueError('global ghost transport bytes not conserved')
    if result[PREFIX+'send_items']!=result[PREFIX+'receive_items']:raise ValueError('global ghost transport items not conserved')
    result['ghost_transport']=mode;result[PREFIX+'work_signature']=json.dumps(signature,separators=(',',':'))

def require_warmup(directory,mode,ranks,source,binary):
    directory=Path(directory)
    if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():raise ValueError('ghost transport warmup failed')
    if not json.loads((directory/'quality_summary.json').read_text()).get('structural_pass'):raise ValueError('ghost transport structural audit failed')
    with (directory/'ghost_transport_certificate.csv').open() as stream:rows=list(csv.DictReader(stream))
    if sorted(int(r['rank']) for r in rows)!=list(range(ranks)):raise ValueError('ghost transport warmup ranks incomplete')
    for r in rows:
        if (r['source_revision'],r['binary_sha256'],r['ghost_transport'],int(r['repeat']))!=(source,binary,MODES[2 if mode else 0],0):raise ValueError('ghost transport warmup identity mismatch')
        if float(r[PREFIX+'mismatches']) or float(r[PREFIX+'verify_calls'])!=mode or float(r[PREFIX+'verified_items'])!=(float(r[PREFIX+'receive_items']) if mode else 0):raise ValueError('ghost transport warmup reference incomplete')
    if mode and not sum(float(r[PREFIX+'dictionary_send_peers']) for r in rows):raise ValueError('dictionary transport never exercised')
    from analyze_results import inspect,profile_path
    ledger=[];run,_=inspect(profile_path(directory),transport_sink=ledger)
    rows.sort(key=lambda r:int(r['rank']))
    if (run['ranks'],run['repeat'],run['ghost_transport'],run['source_revision'],run['binary_sha256'])!=(ranks,0,MODES[2 if mode else 0],source,binary):raise ValueError('ghost transport warmup raw identity mismatch')
    for c,r in zip(rows,ledger):
        for k,v in r.items():
            if isinstance(v,(float,int)):
                if float(c[k])!=v:raise ValueError('ghost transport warmup certificate mismatch')
            elif c[k]!=v:raise ValueError('ghost transport warmup certificate identity mismatch')


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);p.add_argument('--mode',type=int,choices=(0,1),required=True);p.add_argument('--ranks',type=int,required=True);p.add_argument('--source',required=True);p.add_argument('--binary',required=True);a=p.parse_args()
    try:require_warmup(a.directory,a.mode,a.ranks,a.source,a.binary)
    except (OSError,ValueError,KeyError,TypeError) as e:p.exit(1,str(e)+'\n')
