#!/usr/bin/env python3
import copy,json,tempfile
from pathlib import Path
from test_front_workspace_validation import fixture,profiles
from ghost_codec_checks import FIELDS,SCHEMA

def attach(rows):
    for row in rows:
        assert row['repeat']==0
        row['metadata']['ghost_codec_audit']=SCHEMA
        n=row['metrics']['volume_send_items'];peers=row['metrics']['volume_num_s']
        values=dict.fromkeys(FIELDS,0)
        values.update(calls=1,peers=peers,items=n,unique_vertices=4*n,raw_bytes=140*n,
            dictionary_bytes=16*peers+156*n,selected_bytes=140*n,bypass_peers=peers,verified_items=n,verified_peers=peers,
            encode_seconds=.0001 if peers else 0,decode_seconds=.00005 if peers else 0,
            max_peer_raw_bytes=140 if peers else 0,max_peer_dictionary_bytes=172 if peers else 0,max_peer_selected_bytes=140 if peers else 0,
            max_peer_encode_seconds=.0001 if peers else 0,max_peer_decode_seconds=.00005 if peers else 0,lifecycle_seconds=.000175 if peers else 0,max_peer_lifecycle_seconds=.000175 if peers else 0)
        row['metrics'].update({'ghost_codec_'+f:v for f,v in values.items()})
        row['stages']['ghost_codec_audit']={'seconds':.0002,'calls':1}
    return rows

def main():
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'rank_profiles.jsonl'
        def inspect(rows,ledger=None):
            p.write_text(''.join(json.dumps(r)+'\n' for r in rows));return profiles.inspect(p,codec_sink=ledger)[0]
        rows=attach(fixture('workspace_temporary',0));ledger=[];run=inspect(rows,ledger)
        assert len(ledger)==2 and run['ghost_codec_verified_items']==2 and run['ghost_codec_payload_reduction_percent']==0
        for field,value in [('calls',0),('peers',0),('items',2),('unique_vertices',5),('raw_bytes',144),('dictionary_bytes',171),
            ('selected_bytes',139),('verified_items',0),('verified_peers',0),('mismatches',1),('encode_seconds',.1),
            ('decode_seconds',float('nan')),('max_peer_raw_bytes',141),('max_peer_selected_bytes',0)]:
            bad=copy.deepcopy(rows);bad[0]['metrics']['ghost_codec_'+field]=value
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted '+field)
        bad=copy.deepcopy(rows)
        for row in bad:row['repeat']=1;row['metadata'].pop('mesh_quality',None)
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('formal audit accepted')
        bad=copy.deepcopy(rows)
        for row in bad:row['metadata'].pop('ghost_codec_audit',None)
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('unversioned audit accepted')
        inspect(fixture('workspace_temporary',1))
    print('PASS: exact byte/call coverage, independent send work, full-rank receipts and formal audit isolation')
if __name__=='__main__':main()
