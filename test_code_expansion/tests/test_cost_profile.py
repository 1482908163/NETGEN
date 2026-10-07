#!/usr/bin/env python3
"""Full-cost partition/coverage failures, not performance simulations."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from test_worklet_routes import fixture as original_fixture,profiles
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'strong_scaling'))
from cost_profile_checks import PHASES,OPERATIONS,FIELDS

def fixture(route,repeat,mode='natural'):
    return original_fixture(route,repeat,mode)

def main():
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'rank_profiles.jsonl'
        def inspect(rows):
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            sink=[];result,_=profiles.inspect(path,cost_sink=sink)
            return result,sink
        rows=fixture('cost_profile',1)
        result,sink=inspect(rows)
        assert len(sink)==2*3*4 and {r['rank'] for r in sink}=={0,1}
        assert result['cost_active_ranks']==2
        assert result['slowest_compute_cost_generation_swap_commit_seconds']==.003
        cases=[('prepare_seconds',.03),('conform_seconds',.03),('team_evaluate_seconds',.01),
               ('candidates',7),('commit_attempts',3),('applied',3),('team_evaluations',2),
               ('calls',0),('input_elements',float('nan')),('input_points',-1),('evaluated_items',6.5)]
        for field,value in cases:
            bad=copy.deepcopy(rows);bad[0]['metrics']['cost_generation_swap_'+field]=value
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError(field)
        bad=copy.deepcopy(rows);del bad[1]['metrics']['cost_generation_swap_commit_seconds']
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('missing one rank field')
        assert all(len(signature)==2 for signature in json.loads(result['cost_work_signature']))
    print('PASS: all-rank cost export, coherent same-rank totals, 12 invalid cost/coverage cases')

if __name__=='__main__':main()
