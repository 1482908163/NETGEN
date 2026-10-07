#!/usr/bin/env python3
"""Exercise per-rank replay, conservation and cost failures, not performance."""
import copy
import json
import tempfile
from pathlib import Path
from test_worklet_routes import fixture,profiles

cases=0
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'rank_profiles.jsonl'
    def inspect(rows):
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        return profiles.inspect(path)[0]
    for route in ('combine_profile','combine_waves'):
        for repeat in (0,1):
            rows=fixture(route,repeat)
            result=inspect(rows)
            assert result['combine_commit_attempts']==16
            signature=json.loads(result['combine_commit_verification_signature'])
            assert len(signature)==2
            changes=[('candidates',7),('applied',9),('scanned_edges',6),('seconds',.2),
                     ('planning_seconds',.03),('evaluation_seconds',.05),('mismatches',1)]
            if route=='combine_waves':
                changes.extend([('parallel_attempts',7),('waves',3),('max_wave_size',3)])
                changes.append(('verified_attempts',0 if repeat==0 else 1))
                changes.append(('verified_waves',0 if repeat==0 else 1))
            else:changes.extend([('waves',1),('max_wave_size',1)])
            for key,value in changes:
                bad=copy.deepcopy(rows);bad[0]['metrics']['combine_commit_'+key]=value
                try:inspect(bad)
                except ValueError:cases+=1
                else:raise AssertionError((route,repeat,key))
            # Matching totals cannot hide a missing check on one rank.
            if route=='combine_waves' and repeat==0:
                bad=copy.deepcopy(rows)
                bad[0]['metrics']['combine_commit_verified_attempts']=0
                bad[1]['metrics']['combine_commit_verified_attempts']=16
                try:inspect(bad)
                except ValueError:cases+=1
                else:raise AssertionError('rank coverage hidden by total')
print(f'PASS: {cases} invalid per-rank collapse/replay/cost cases rejected')
