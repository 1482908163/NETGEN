#!/usr/bin/env python3
import copy,json,tempfile
from pathlib import Path
from test_ghost_transport_validation import fixture as transport,profiles
from multi_point_checks import GROUPS,layouts
MASKS={'multi_control':0,'multi_front':1,'multi_pack':2,'multi_unpack':4,'multi_all':7,'multi_exchange':6,'multi_pack_compact':14,'multi_unpack_flat':22,'multi_layout':30}

def fixture(route,repeat,mode='natural'):
    mask=MASKS[route];rows=transport('ghost_literal',repeat,mode)
    for row in rows:
        meta=row['metadata'];meta.update(multi_point_policy='exact_local_work_v1',multi_point_mask=str(mask),multi_point_verification='whole_reference_v1' if repeat==0 else 'disabled_v1')
        if mask>7:meta.update(layouts(mask))
        m=row['metrics']
        m['local_volume_elements_before_adjacency']=20
        m['refine_initial_elements']=20
        m['local_volume_elements_after_adjacency']=30;m['ghost_volume_elements_added']=10
        m['local_points_after_adjacency']=53
        if repeat==0:
            for k in ('quality_elements','quality_checked','quality_negative'):m[k]=20
            m['quality_adjacency_elements']=30;m['quality_adjacency_ghosts']=10;m['quality_adjacency_points']=53
            m['quality_shape_sum']*=20;m['quality_abs_volume']*=20
            for k in list(m):
                if k.startswith('quality_shape_bin_'):m[k]*=20
        f=dict.fromkeys(GROUPS['front'],0);f.update(calls=5,score_reads=50,selection_seconds=.003)
        if mask&1:f.update(score_reads=20,refreshed_faces=10,dirty_blocks=1,visited_blocks=3,rebuilds=1,indexed_faces=10,point_links=30,verified=5 if repeat==0 else 0,reference_seconds=.001 if repeat==0 else 0)
        p=dict.fromkeys(GROUPS['pack'],0);p.update(calls=1,input_elements=m['local_volume_elements_before_adjacency'],candidate_elements=m['local_volume_elements_before_adjacency'],selected_elements=10,send_items=m['volume_send_items'],seconds=.004)
        if mask&2:p.update(candidate_elements=10,verified_elements=p['input_elements'] if repeat==0 else 0,reference_seconds=.001 if repeat==0 else 0)
        u=dict.fromkeys(GROUPS['unpack'],0);u.update(calls=1,old_points=50,receive_items=m['volume_receive_items'],new_points=3,vertex_references=4*m['volume_receive_items'],seconds=.003)
        if mask&4:u.update(verified_items=u['receive_items'] if repeat==0 else 0,reference_seconds=.001 if repeat==0 else 0)
        for group,values in (('front',f),('pack',p),('unpack',u)):m.update({'multi_'+group+'_'+k:v for k,v in values.items()})
        for name in ('volume_exchange_pack','volume_exchange_unpack'):row['stages'][name]=dict(calls=1,seconds=.004,category='compute')
        # Distinct local core values expose incorrect selection by stage maxima.
        m['core_seconds']+=row['rank']*.01
    return rows

def main():
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'rank_profiles.jsonl'
        def inspect(rows):p.write_text(''.join(json.dumps(r)+'\n' for r in rows));return profiles.inspect(p,multi_sink=[])[0]
        for route in MASKS:
            for repeat in (0,1):inspect(fixture(route,repeat))
        for key in ('multi_point_pack_layout','multi_point_unpack_layout'):
            bad=fixture('multi_layout',0);bad[0]['metadata'][key]='legacy'
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('ignored layout '+key)
        for mask in (8,9,12,16,18,24,28,32):
            bad=fixture('multi_control',1)
            for row in bad:row['metadata']['multi_point_mask']=str(mask)
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('invalid dependency mask '+str(mask))
        corruptions=[('multi_front_verified',0),('multi_front_mismatches',1),('multi_pack_verified_elements',0),('multi_unpack_verified_items',0),('multi_pack_send_items',9),('multi_unpack_vertex_references',39),('multi_unpack_new_points',100),('multi_pack_seconds',100),('multi_front_calls',0)]
        for key,value in corruptions:
            bad=fixture('multi_all',0);bad[0]['metrics'][key]=value
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted '+key)
        for group in ('front','pack','unpack'):
            bad=fixture('multi_all',1);bad[0]['metrics']['multi_'+group+'_reference_seconds']=.1
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('formal reference '+group)
        bad=fixture('multi_all',1);bad[0]['metadata']['multi_point_mask']='0'
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('mask ignored')
        bad=fixture('multi_all',1)
        for row in bad:
            for k in ('multi_point_policy','multi_point_mask','multi_point_verification'):row['metadata'].pop(k,None)
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('unmarked metrics')
    print('PASS: nine routes and layout execution identities, complete warmup references, independent work, formal isolation and requested-mode guards')
if __name__=='__main__':main()
