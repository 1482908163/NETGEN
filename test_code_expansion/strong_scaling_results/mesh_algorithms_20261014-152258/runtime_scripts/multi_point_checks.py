"""Exact selection, unchanged packets and first-encounter assembly protocol."""
import csv,json,math
from pathlib import Path

FRONT=tuple('calls score_reads refreshed_faces dirty_blocks visited_blocks rebuilds verified mismatches reference_seconds selection_seconds indexed_faces point_links'.split())
PACK=tuple('calls input_elements candidate_elements selected_elements send_items verified_elements reference_seconds seconds'.split())
UNPACK=tuple('calls old_points receive_items new_points vertex_references verified_items reference_seconds seconds'.split())
GROUPS={'front':FRONT,'pack':PACK,'unpack':UNPACK}
FIELDS=tuple('multi_'+group+'_'+f for group,names in GROUPS.items() for f in names)
WORK=('multi_front_calls','multi_pack_input_elements','multi_pack_selected_elements','multi_pack_send_items',
      'multi_unpack_old_points','multi_unpack_receive_items','multi_unpack_new_points','multi_unpack_vertex_references')
SCHEMA='exact_local_work_v1'
VALID_MASKS=tuple(m for m in range(32) if (not m&8 or m&2) and (not m&16 or m&4))
def layouts(mask):
    return dict(multi_point_pack_layout='csr_exact_pack_v1' if mask&8 else 'dense_peer_vectors_v1' if mask&2 else 'legacy_pack_v1',
                multi_point_unpack_layout='flat_gid_index_v1' if mask&16 else 'node_hash_index_v1' if mask&4 else 'legacy_unpack_v1')

def validate(rows,metadata,result):
    if metadata.get('multi_point_policy') is None:
        if any(k.startswith('multi_') for row in rows for k in row['metrics']):raise ValueError('multi point metrics without protocol')
        if any(k.startswith('multi_point_') for k in metadata):raise ValueError('multi point metadata without protocol')
        return
    try:mask=int(metadata['multi_point_mask'])
    except (KeyError,ValueError,TypeError) as e:raise ValueError('invalid multi point mask') from e
    warm=rows[0]['repeat']==0
    if (metadata['multi_point_policy']!=SCHEMA or mask not in VALID_MASKS or metadata.get('multi_point_verification')!=('whole_reference_v1' if warm else 'disabled_v1') or
        metadata.get('ghost_transport')!='literal_transport_v1' or metadata.get('volume_front_workspace')!='temporary_workspace_v1' or
        metadata.get('kernel_scheduler')!='cost_profile' or metadata.get('global_numbering')!='owner_local_v2' or
        metadata.get('volume_incidence_build')!=('ordered_incidence_replay_v1' if warm else 'ordered_incidence_v1') or
        metadata.get('timing_mode')!='natural' or (warm and metadata.get('mesh_quality')!='volume_audit_v1')):raise ValueError('invalid multi point base/verification')
    expected_layouts=layouts(mask)
    for k,v in expected_layouts.items():
        if (mask>7 or k in metadata) and metadata.get(k)!=v:raise ValueError('multi point layout not executed: '+k)
    signature=[]
    for row in rows:
        m=row['metrics'];v={k:m.get(k) for k in FIELDS}
        if any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 or ('seconds' not in k and (int(x)!=x or x>2**53)) for k,x in v.items()):raise ValueError('invalid multi point metrics')
        f={k:v['multi_front_'+k] for k in FRONT};p={k:v['multi_pack_'+k] for k in PACK};u={k:v['multi_unpack_'+k] for k in UNPACK}
        if f['mismatches'] or f['verified']!=(f['calls'] if warm and mask&1 else 0):raise ValueError('multi front reference coverage')
        if not mask&1 and any(f[k] for k in ('refreshed_faces','dirty_blocks','visited_blocks','rebuilds','indexed_faces','point_links')):raise ValueError('multi control entered front index')
        if f['calls']==0 and any(f.values()):raise ValueError('multi front work without calls')
        if p['calls']!=1 or u['calls']!=1 or p['input_elements']!=m.get('local_volume_elements_before_adjacency') or p['send_items']!=m.get('volume_send_items') or u['receive_items']!=m.get('volume_receive_items'):raise ValueError('multi independent work mismatch')
        if not 0<=p['selected_elements']<=p['candidate_elements']<=p['input_elements'] or p['selected_elements']>p['send_items'] or (p['selected_elements']==0)!=(p['send_items']==0):raise ValueError('multi pack selection accounting')
        if p['send_items']>p['selected_elements']*m['volume_num_s']:raise ValueError('multi pack peer coverage')
        if u['vertex_references']!=4*u['receive_items'] or u['new_points']>u['vertex_references']:raise ValueError('multi unpack index coverage')
        if u['old_points']+u['new_points']!=m.get('local_points_after_adjacency') or u['receive_items']!=m.get('ghost_volume_elements_added'):raise ValueError('multi final mesh coverage')
        if p['verified_elements']!=(p['input_elements'] if warm and mask&2 else 0) or u['verified_items']!=(u['receive_items'] if warm and mask&4 else 0):raise ValueError('multi pack/assembly reference coverage')
        if not warm and (f['reference_seconds'] or p['reference_seconds'] or u['reference_seconds']):raise ValueError('multi reference entered formal measurement')
        if not mask&1 and f['reference_seconds'] or not mask&2 and p['reference_seconds'] or not mask&4 and u['reference_seconds']:raise ValueError('multi reference on disabled component')
        # Front values are sums over domain objects and may overlap on workers;
        # the two exchange operations each run once on this rank's host.
        if any(v[k]>m['core_seconds']+1e-3 for k in FIELDS if 'seconds' in k and not k.startswith('multi_front_')):raise ValueError('multi timing outside core')
        if row['stages'].get('volume_exchange_pack',{}).get('calls')!=1 or row['stages'].get('volume_exchange_unpack',{}).get('calls')!=1:raise ValueError('multi exchange stage missing')
        signature.append([row['rank']]+[v[k] for k in WORK])
        for k,x in v.items():result[k]=result.get(k,0)+x
    result.update(multi_point_policy=SCHEMA,multi_point_mask=mask,multi_point_verification=metadata['multi_point_verification'])
    result.update({k:v for k,v in expected_layouts.items() if k in metadata})
    result['multi_point_work_signature']=json.dumps(signature,separators=(',',':'))
    if warm:
        for bit,key in ((1,'multi_front_verified'),(2,'multi_pack_verified_elements'),(4,'multi_unpack_verified_items')):
            if mask&bit and not result[key]:raise ValueError('multi point selected component not exercised')

def require_warmup(directory,mask,ranks,source,binary):
    directory=Path(directory)
    if not (directory/'SUCCESS').is_file() or (directory/'RUNNING').exists():raise ValueError('multi point warmup incomplete')
    if not json.loads((directory/'quality_summary.json').read_text()).get('structural_pass'):raise ValueError('multi point warmup quality failed')
    from analyze_results import inspect,profile_path
    ledger=[];run,_=inspect(profile_path(directory),multi_sink=ledger)
    if (run['multi_point_mask'],run['ranks'],run['repeat'],run['source_revision'],run['binary_sha256'])!=(mask,ranks,0,source,binary):raise ValueError('multi point warmup identity')
    with (directory/'multi_point_certificate.csv').open() as s:cert=list(csv.DictReader(s))
    cert.sort(key=lambda r:int(r['rank']))
    if [int(r['rank']) for r in cert]!=list(range(ranks)):raise ValueError('multi point warmup ranks')
    for x,y in zip(cert,ledger):
        for k,v in y.items():
            if isinstance(v,(int,float)):
                if float(x[k])!=v:raise ValueError('multi point warmup certificate mismatch')
            elif x[k]!=v:raise ValueError('multi point warmup certificate identity')
    if mask&1 and not run['multi_front_verified']:raise ValueError('multi front not exercised')
    if mask&2 and not run['multi_pack_verified_elements']:raise ValueError('multi pack not exercised')
    if mask&4 and not run['multi_unpack_verified_items']:raise ValueError('multi unpack not exercised')

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);p.add_argument('--mask',type=int,choices=VALID_MASKS,required=True);p.add_argument('--ranks',type=int,required=True);p.add_argument('--source',required=True);p.add_argument('--binary',required=True);a=p.parse_args()
    try:require_warmup(a.directory,a.mask,a.ranks,a.source,a.binary)
    except (OSError,ValueError,KeyError,TypeError) as e:p.exit(1,str(e)+'\n')
