#!/usr/bin/env python3
"""Reject malformed scheduler coverage and impossible inclusive timings."""
import copy
import json
import tempfile
from pathlib import Path
from test_worklet_routes import fixture, profiles

with tempfile.TemporaryDirectory() as directory:
    path=Path(directory)/'rank_profiles.jsonl'
    def inspect(rows):
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        return profiles.inspect(path)[0]
    for route in ('smooth_profile','smooth_balanced'):
        rows=fixture(route,1)
        assert inspect(rows)['smooth_balance_active_visits']==100
        for key,value in (('weighted_calls',-1),('active_visits',101),
                          ('dispatches',0),('planning_seconds',1),('seconds',1)):
            bad=copy.deepcopy(rows)
            bad[0]['metrics']['smooth_balance_'+key]=value
            try:
                inspect(bad)
            except ValueError:
                pass
            else:
                raise AssertionError((route,key))
print('PASS: final smoothing coverage and timing rejection gates')
