#!/usr/bin/env python3
import copy
import json
from pathlib import Path
import tempfile
from test_worklet_routes import fixture,profiles
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp)/'rank_profiles.jsonl'
    def inspect(rows):
        p.write_text(''.join(json.dumps(r)+'\n' for r in rows));return profiles.inspect(p)[0]
    for repeat in (0,1):
        rows=fixture('split_active',repeat);result=inspect(rows)
        assert result['split_active_active']==4 and result['split_active_dispatched']==4
        assert result['split_active_verified']==(12 if repeat==0 else 0)
        for key,value in [('active',3),('rejected',3),('dispatched',1),('calls',0),('calls',2),
                          ('edges',8),('mismatches',1),('worker_max_seconds',.1),('worker_seconds',float('nan')),
                          ('screen_seconds',.02),('verified',1),('reference_seconds',-.001)]:
            bad=copy.deepcopy(rows);bad[0]['metrics']['split_active_'+key]=value
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError(key)
        bad=copy.deepcopy(rows);del bad[1]['metrics']['split_active_verified']
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('missing rank certificate counter')
    print('PASS: active split coverage, conservation, timing, formal/reference isolation and per-rank failures')
