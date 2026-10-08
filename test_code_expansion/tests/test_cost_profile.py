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
        assert result['cost_coverage_complete']
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
        missing=copy.deepcopy(rows)
        for row in missing:
            for field in FIELDS:row['metrics']['cost_generation_swap_'+field]=0
        try:inspect(missing)
        except ValueError as error:assert 'native operation call coverage mismatch' in str(error)
        else:raise AssertionError('native timer exposes a dropped diagnostic parameter')
        for row in missing:row['metadata']['volume_cost_profile']='phase_full_cost_v1'
        historical,_=inspect(missing)
        assert not historical['cost_coverage_complete'] and historical['cost_coverage_gap_count']==2
        for row in missing:row['metadata']['volume_cost_profile']='phase_full_cost_v2'
        try:inspect(missing)
        except ValueError as error:assert 'native operation coverage mismatch' in str(error)
        else:raise AssertionError('v2 time rejection must remain unchanged')
        clocks=copy.deepcopy(rows);clocks[0]['metrics']['native_generation_swap_seconds']=.012
        clock_result,_=inspect(clocks)
        assert clock_result['cost_coverage_complete'] and clock_result['cost_clock_gap_count']==1
        missing_call=copy.deepcopy(rows);del missing_call[0]['metrics']['native_generation_swap_calls']
        try:inspect(missing_call)
        except ValueError:pass
        else:raise AssertionError('v3 requires independent native calls')
        fixed=fixture('repair_fixed',1)
        fixed_result,_=inspect(fixed)
        assert fixed_result['repair_fixed_skipped']>0
        for field,value in [('mismatches',1),('skipped',8),('verified',1),('snapshot_seconds',float('nan'))]:
            bad=copy.deepcopy(fixed);bad[0]['metrics']['repair_fixed_repair_'+field]=value
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('invalid fixed-point '+field)
        reference=fixture('repair_fixed',0);inspect(reference)
        bad=copy.deepcopy(reference);bad[0]['metrics']['repair_fixed_repair_verified']=0
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('incomplete reference continuation')
    print('PASS: all-rank costs, independent call coverage, historical rejection and fixed-point verification failures')

if __name__=='__main__':main()
