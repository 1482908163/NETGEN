#!/usr/bin/env python3
import json,tempfile
from pathlib import Path
from test_incidence_validation import fixture as incidence,profiles
from front_workspace_checks import FIELDS,MODES,check_certificates,require_warmup

def fixture(route,repeat,mode='natural'):
    rows=incidence('incidence_ordered',repeat,mode)
    reuse=route=='workspace_reuse';assert route in ('workspace_temporary','workspace_reuse')
    for row in rows:
        row['metadata']['volume_front_workspace']=MODES[2 if reuse and repeat==0 else int(reuse)]
        n=row['metrics']['front_transform_transforms']
        assert n==row['metrics']['front_distance_candidates']
        values=dict.fromkeys(FIELDS,0)
        values.update(calls=row['metrics']['front_distance_calls'],transforms=n,vector_entries=12*n,vector_requests=3*n,baseline_heap_requests=3*n)
        if reuse:values.update(heap_requests=3,reused_requests=3*n-3,workspace_hits=n-1,allocated_entries=12,retained_entries_peak=12,initialized_rules=1,
            verified=values['calls'] if repeat==0 else 0,reference_seconds=.001 if repeat==0 else 0)
        else:values.update(heap_requests=3*n,allocated_entries=12*n)
        row['metrics'].update({'front_workspace_'+f:v for f,v in values.items()})
    return rows

def main():
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'rank_profiles.jsonl'
        def inspect(rows,cert=None,ledger=None):
            p.write_text(''.join(json.dumps(r)+'\n' for r in rows));return profiles.inspect(p,certificate_sink=cert,workspace_sink=ledger)[0]
        for route in ('workspace_temporary','workspace_reuse'):
            for repeat in (0,1):inspect(fixture(route,repeat))
        for field,value in [('verified',0),('mismatches',1),('calls',1),('transforms',3),('vector_entries',-1),('vector_requests',5),
            ('baseline_heap_requests',7),('heap_requests',4),('reused_requests',4),('workspace_hits',2),('allocated_entries',100),
            ('retained_entries_peak',0),('initialized_rules',3),('reference_seconds',float('nan'))]:
            bad=fixture('workspace_reuse',0);bad[0]['metrics']['front_workspace_'+field]=value
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted '+field)
        for field in ('verified','reference_seconds'):
            bad=fixture('workspace_reuse',1);bad[0]['metrics']['front_workspace_'+field]=1
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted formal '+field)
        bad=fixture('workspace_temporary',1);bad[0]['metrics']['front_workspace_reused_requests']=1
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('control entered reuse')
        cert=[];ledger=[];run=inspect(fixture('workspace_reuse',0),cert,ledger);check_certificates(cert,run,0,2,MODES[2])
        assert len(ledger)==2 and run['front_workspace_retained_entries_peak']==12
        cert[0]['front_workspace_vector_entries']+=1;cert[1]['front_workspace_vector_entries']-=1
        try:check_certificates(cert,run,0,2,MODES[2])
        except ValueError:pass
        else:raise AssertionError('aggregate hid rank drift')
        for route,mode in (('workspace_temporary',0),('workspace_reuse',1)):
            cert=[];run=inspect(fixture(route,0),cert);profiles.write_csv(Path(tmp)/'algorithm_certificate.csv',cert)
            (Path(tmp)/'quality_summary.json').write_text(json.dumps({'structural_pass':True}));(Path(tmp)/'SUCCESS').touch()
            require_warmup(tmp,mode,2,run['source_revision'],run['binary_sha256'])
            cert[0]['volume_front_transform']='changed';profiles.write_csv(Path(tmp)/'algorithm_certificate.csv',cert)
            try:require_warmup(tmp,mode,2,run['source_revision'],run['binary_sha256'])
            except ValueError:pass
            else:raise AssertionError('mixed base accepted')
    print('PASS: allocation conservation, independent transforms, per-rank evidence, replay isolation and warmup gates')
if __name__=='__main__':main()
