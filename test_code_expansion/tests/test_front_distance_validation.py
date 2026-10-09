#!/usr/bin/env python3
"""Counter and exact-reference gates; fixtures are not performance evidence."""
import copy,json,tempfile
from pathlib import Path
from test_worklet_routes import fixture,profiles
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'rank_profiles.jsonl'
    def inspect(rows):
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows));return profiles.inspect(path)[0]
    for repeat in (0,1):
        rows=fixture('front_distance',repeat);result=inspect(rows)
        assert result['front_distance_hits']==12 and result['front_distance_queries']==20
        assert result['front_distance_verified']==(4 if repeat==0 else 0)
        for key,value in [('queries',11),('computed',5),('fallback',5),('mismatches',1),
                          ('calls',0),('verified',1),('allocated_cells',131073),
                          ('apply_seconds',1),('rules',float('nan')),('reference_seconds',-.1)]:
            bad=copy.deepcopy(rows);bad[0]['metrics']['front_distance_'+key]=value
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError(key)
        bad=copy.deepcopy(rows);del bad[1]['metrics']['front_distance_verified']
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('missing per-rank counter')
    rows=fixture('split_active',1);rows[0]['metrics']['front_distance_hits']=1
    try:inspect(rows)
    except ValueError:pass
    else:raise AssertionError('baseline cache usage')
print('PASS: per-rank front distance conservation, memory, timing and exact-reference isolation')
