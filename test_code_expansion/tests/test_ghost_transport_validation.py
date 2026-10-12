#!/usr/bin/env python3
import copy,json,tempfile
from pathlib import Path
from test_front_workspace_validation import fixture as workspace,profiles
from ghost_transport_checks import FIELDS,PREFIX,MODES,PHASES

def fixture(route,repeat,mode='natural'):
    rows=workspace('workspace_temporary',repeat,mode);dictionary=route=='ghost_dictionary'
    assert route in ('ghost_literal','ghost_dictionary')
    for row in rows:
        row['metadata']['ghost_transport']=MODES[2 if dictionary and repeat==0 else int(dictionary)]
        m=row['metrics'];m['volume_send_items']=m['volume_receive_items']=10;m['volume_num_s']=m['volume_num_r']=1
        v=dict.fromkeys(FIELDS,0)
        v.update(calls=1,send_items=10,receive_items=10,raw_send_bytes=1400,raw_receive_bytes=1400,
            payload_send_bytes=424 if dictionary else 1400,payload_receive_bytes=424 if dictionary else 1400,
            payload_send_messages=1,payload_receive_messages=1,exchange_seconds=.02,total_seconds=.02,
            full_seconds=(.05 if dictionary else .07)+row['rank']*.01)
        if dictionary:v.update(metadata_send_bytes=24,metadata_receive_bytes=24,metadata_send_messages=1,metadata_receive_messages=1,
            dictionary_send_peers=1,dictionary_receive_peers=1,prepare_seconds=.001,metadata_seconds=.001,decode_seconds=.001,release_seconds=.001,total_seconds=.024,retained_wire_bytes_peak=848,
            verify_calls=int(repeat==0),verified_items=10 if repeat==0 else 0,verified_peers=int(repeat==0),reference_seconds=.001 if repeat==0 else 0)
        else:v.update(literal_send_peers=1,literal_receive_peers=1)
        m.update({PREFIX+k:x for k,x in v.items()})
        for phase,name in PHASES.items():
            if not dictionary and phase!='exchange':continue
            row['stages'][name]=dict(seconds=v[phase+'_seconds'],calls=1,category='communication' if phase in ('exchange','metadata') else 'compute')
        for stage,prefix in (('volume_payload_exchange','payload'),('ghost_dictionary_metadata','metadata')):
            if stage not in row['stages']:continue
            row['stages'][stage].update({d+'_'+k:v[prefix+'_'+d+'_'+k] for d in ('send','receive') for k in ('bytes','messages')})
        if dictionary and repeat==0:row['stages']['ghost_dictionary_reference']=dict(seconds=.001,calls=1,category='validation')
    return rows

def main():
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'rank_profiles.jsonl'
        def inspect(rows,ledger=None):p.write_text(''.join(json.dumps(r)+'\n' for r in rows));return profiles.inspect(p,transport_sink=ledger)[0]
        for route in ('ghost_literal','ghost_dictionary'):
            for repeat in (0,1):
                ledger=[];r=inspect(fixture(route,repeat),ledger);assert len(ledger)==2 and r[PREFIX+'send_items']==20
        for key,value in [('calls',0),('send_items',9),('raw_send_bytes',1399),('payload_send_bytes',1401),('metadata_send_bytes',23),('metadata_send_messages',2),('dictionary_send_peers',0),('verified_items',9),('verify_calls',0),('mismatches',1),('full_seconds',.001),('total_seconds',.0001),('prepare_seconds',float('nan'))]:
            bad=fixture('ghost_dictionary',0);bad[0]['metrics'][PREFIX+key]=value
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted '+key)
        for key in ('reference_seconds','verified_items','verify_calls'):
            bad=fixture('ghost_dictionary',1);bad[0]['metrics'][PREFIX+key]=1
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted formal '+key)
        bad=fixture('ghost_dictionary',1);bad[0]['stages']['volume_payload_exchange']['send_bytes']+=1
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('accepted forged wire stats')
    print('PASS: real transport byte/message conservation, full receive oracle, phase/full cost, rank receipts and formal isolation')
if __name__=='__main__':main()
