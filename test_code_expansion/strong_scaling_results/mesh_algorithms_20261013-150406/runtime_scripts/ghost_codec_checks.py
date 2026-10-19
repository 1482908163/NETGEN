"""Warmup-only ghost dictionary roundtrip and payload-size feasibility receipts."""
import math
PREFIX='ghost_codec_';SCHEMA='vertex_dictionary_audit_v1'
FIELDS=tuple('calls peers items unique_vertices raw_bytes dictionary_bytes selected_bytes profitable_peers bypass_peers verified_items verified_peers mismatches encode_seconds decode_seconds max_peer_raw_bytes max_peer_dictionary_bytes max_peer_selected_bytes max_peer_encode_seconds max_peer_decode_seconds lifecycle_seconds max_peer_lifecycle_seconds'.split())
WORK=FIELDS[:12]

def validate(rows,metadata,result):
    mode=metadata.get('ghost_codec_audit')
    if mode is None:
        if any(k.startswith(PREFIX) for row in rows for k in row['metrics']):raise ValueError('ghost codec metrics without protocol')
        return
    if mode!=SCHEMA or metadata.get('mesh_quality')!='volume_audit_v1' or rows[0]['repeat']!=0 or metadata.get('timing_mode')!='natural':raise ValueError('ghost codec audit outside quality warmup')
    for row in rows:
        v={f:row['metrics'].get(PREFIX+f) for f in FIELDS}
        if any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 or ('seconds' not in f and (int(x)!=x or x>2**53)) for f,x in v.items()):raise ValueError('invalid ghost codec metric')
        if v['calls']!=1 or v['mismatches'] or v['verified_items']!=v['items'] or v['verified_peers']!=v['peers']:raise ValueError('ghost codec roundtrip coverage incomplete')
        if v['items']!=row['metrics'].get('volume_send_items') or v['peers']!=row['metrics'].get('volume_num_s'):raise ValueError('ghost codec independent payload mismatch')
        if (v['raw_bytes']!=140*v['items'] or v['dictionary_bytes']!=16*v['peers']+32*v['unique_vertices']+28*v['items'] or
            v['profitable_peers']+v['bypass_peers']!=v['peers'] or v['peers']>v['items'] or
            v['unique_vertices']<v['peers'] or v['unique_vertices']>4*v['items'] or
            v['selected_bytes']>min(v['raw_bytes'],v['dictionary_bytes'])):raise ValueError('ghost codec byte conservation mismatch')
        if (not v['peers'] and any(v[f] for f in FIELDS[1:])):raise ValueError('ghost codec empty payload work')
        if not v['profitable_peers'] and v['selected_bytes']!=v['raw_bytes']:raise ValueError('ghost codec bypass byte mismatch')
        if not v['bypass_peers'] and v['selected_bytes']!=v['dictionary_bytes']:raise ValueError('ghost codec dictionary byte mismatch')
        for peak,total in (('max_peer_raw_bytes','raw_bytes'),('max_peer_dictionary_bytes','dictionary_bytes'),('max_peer_selected_bytes','selected_bytes'),('max_peer_encode_seconds','encode_seconds'),('max_peer_decode_seconds','decode_seconds'),('max_peer_lifecycle_seconds','lifecycle_seconds')):
            if v[peak]>v[total]:raise ValueError('ghost codec peak accounting mismatch')
        if v['peers'] and (v['max_peer_raw_bytes']<140 or v['max_peer_selected_bytes']<=0):raise ValueError('ghost codec missing peer maxima')
        scope=row['stages'].get('ghost_codec_audit',{})
        if scope.get('calls')!=1 or v['lifecycle_seconds']>scope.get('seconds',-1)+1e-6 or v['encode_seconds']+v['decode_seconds']>v['lifecycle_seconds']+1e-6:raise ValueError('ghost codec validation timing mismatch')
        for f,x in v.items():
            key=PREFIX+f;result[key]=max(result.get(key,0),x) if f.startswith('max_peer_') else result.get(key,0)+x
    result['ghost_codec_audit']=mode
    result['ghost_codec_payload_reduction_percent']=100*(1-result[PREFIX+'selected_bytes']/result[PREFIX+'raw_bytes']) if result[PREFIX+'raw_bytes'] else None
