#!/usr/bin/env python3
import csv,json,tempfile
from pathlib import Path
from test_incidence_validation import fixture as incidence,profiles
from front_rule_checks import FIELDS,MODES,STAGES,check_certificates,require_warmup

def fixture(route,repeat,mode='natural'):
    rows=incidence('incidence_ordered',repeat,mode)
    if route=='incidence_ordered':return rows
    for row in rows:
        detailed=route=='rules_stages'
        row['metadata']['volume_front_rules']=MODES[2] if detailed and repeat==0 else MODES[1] if detailed else MODES[0]
        values=dict.fromkeys(FIELDS,0)
        values.update(calls=2,rules=4,face_trials=4,point_trials=8,mappings=2,
            evaluated=2,usable=1,best_updates=1,shared_triangles=4,unshared_triangles=8,
            quad_tests=1,point_tests=3,verified=2 if detailed and repeat==0 else 0,
            total_seconds=.0052,reference_seconds=.001 if detailed and repeat==0 else 0,stage_transitions=28)
        if detailed:values.update({f:.0052/11 for f in STAGES})
        row['metrics'].update({'front_rules_'+f:v for f,v in values.items()})
    return rows

def main():
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'rank_profiles.jsonl'
        def inspect(rows,cert=None,ledger=None):
            p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            return profiles.inspect(p,certificate_sink=cert,rule_sink=ledger)[0]
        for route in ('incidence_ordered','rules_totals','rules_stages'):
            for repeat in (0,1):inspect(fixture(route,repeat))
        for field,value in [('verified',0),('mismatches',1),('mappings',1),('rules',3),('usable',3),('edge_rejected',1),
            ('shared_triangles',.5),('prepare_seconds',.1),('total_seconds',float('nan')),('stage_transitions',0),('point_tests',-1)]:
            bad=fixture('rules_stages',0);bad[0]['metrics']['front_rules_'+field]=value
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted '+field)
        for field in ('verified','reference_seconds'):
            bad=fixture('rules_stages',1);bad[0]['metrics']['front_rules_'+field]=1
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted formal '+field)
        bad=fixture('rules_totals',1);bad[0]['metrics']['front_rules_prepare_seconds']=.001
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('coarse control entered fine timer')
        cert=[];ledger=[];run=inspect(fixture('rules_stages',0),cert,ledger)
        check_certificates(cert,run,0,2,MODES[2])
        assert len(ledger)==2 and all('compute_seconds' in r for r in ledger)
        cert[0]['front_rules_shared_triangles']+=1;cert[1]['front_rules_shared_triangles']-=1
        try:check_certificates(cert,run,0,2,MODES[2])
        except ValueError:pass
        else:raise AssertionError('aggregate hid rank drift')
        for route,mode in (('rules_totals',0),('rules_stages',1)):
            cert=[];run=inspect(fixture(route,0),cert)
            profiles.write_csv(Path(tmp)/'algorithm_certificate.csv',cert)
            (Path(tmp)/'quality_summary.json').write_text(json.dumps({'structural_pass':True}))
            (Path(tmp)/'SUCCESS').touch()
            require_warmup(tmp,mode,2,run['source_revision'],run['binary_sha256'])
    print('PASS: exclusive partition, independent work counters, full-rank evidence, formal isolation and warmup gates')
if __name__=='__main__':main()
