#!/usr/bin/env python3
import json,tempfile
from pathlib import Path
from test_incidence_validation import fixture as incidence,profiles
from front_lookup_checks import FIELDS,MODES,check_certificates,require_warmup

def fixture(route,repeat,mode='natural'):
    rows=incidence('incidence_ordered',repeat,mode)
    inverse=route=='lookup_inverse'
    assert route in ('lookup_linear','lookup_inverse')
    for row in rows:
        row['metadata']['volume_front_lookup']=MODES[2 if inverse and repeat==0 else int(inverse)]
        values=dict.fromkeys(FIELDS,0)
        values.update(calls=2,candidates=2,queried_candidates=2,queries=12,baseline_comparisons=48)
        if inverse:values.update(direct_queries=12,table_builds=2,table_writes=8,table_clears=4,table_bytes_peak=64,
            verified=2 if repeat==0 else 0,reference_seconds=.001 if repeat==0 else 0)
        else:values['linear_comparisons']=48
        row['metrics'].update({'front_lookup_'+f:v for f,v in values.items()})
    return rows

def main():
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'rank_profiles.jsonl'
        def inspect(rows,cert=None,ledger=None):
            p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            return profiles.inspect(p,certificate_sink=cert,lookup_sink=ledger)[0]
        for route in ('lookup_linear','lookup_inverse'):
            for repeat in (0,1):inspect(fixture(route,repeat))
        for field,value in [('verified',0),('mismatches',1),('calls',1),('candidates',3),('queried_candidates',3),
            ('queries',1),('baseline_comparisons',float('nan')),('direct_queries',13),('table_builds',1),
            ('table_writes',.5),('table_clears',9),('table_bytes_peak',0),('linear_comparisons',1),('reference_seconds',-1)]:
            bad=fixture('lookup_inverse',0);bad[0]['metrics']['front_lookup_'+field]=value
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted '+field)
        for field in ('verified','reference_seconds'):
            bad=fixture('lookup_inverse',1);bad[0]['metrics']['front_lookup_'+field]=1
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted formal '+field)
        bad=fixture('lookup_linear',1);bad[0]['metrics']['front_lookup_direct_queries']=1
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('control entered inverse path')
        bad=fixture('lookup_inverse',1)
        for row in bad:row['metadata']['volume_front_rules']='rule_totals_v1'
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('accepted mixed observation')
        cert=[];ledger=[];run=inspect(fixture('lookup_inverse',0),cert,ledger)
        check_certificates(cert,run,0,2,MODES[2])
        assert len(ledger)==2 and all('compute_seconds' in r for r in ledger)
        assert run['front_lookup_table_bytes_peak']==64
        cert[0]['front_lookup_queries']+=1;cert[1]['front_lookup_queries']-=1
        try:check_certificates(cert,run,0,2,MODES[2])
        except ValueError:pass
        else:raise AssertionError('aggregate hid rank drift')
        for route,mode in (('lookup_linear',0),('lookup_inverse',1)):
            cert=[];run=inspect(fixture(route,0),cert)
            profiles.write_csv(Path(tmp)/'algorithm_certificate.csv',cert)
            (Path(tmp)/'quality_summary.json').write_text(json.dumps({'structural_pass':True}));(Path(tmp)/'SUCCESS').touch()
            require_warmup(tmp,mode,2,run['source_revision'],run['binary_sha256'])
            cert[0]['volume_front_distance']='changed'
            profiles.write_csv(Path(tmp)/'algorithm_certificate.csv',cert)
            try:require_warmup(tmp,mode,2,run['source_revision'],run['binary_sha256'])
            except ValueError:pass
            else:raise AssertionError('warmup mixed base accepted')
    print('PASS: lookup conservation, independent work, rank evidence, formal isolation and warmup gates')
if __name__=='__main__':main()
