#!/usr/bin/env python3
"""检查真实分析器的逐进程计数和参考回放门槛；不作为性能证据。"""
import copy,json,tempfile
from pathlib import Path
from test_worklet_routes import fixture,profiles
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'rank_profiles.jsonl'
    def inspect(rows):
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        return profiles.inspect(path)[0]
    for repeat in (0,1):
        rows=fixture('front_transform',repeat);result=inspect(rows)
        assert result['front_transform_skipped_terms']==120
        assert result['front_transform_verified']==(4 if repeat==0 else 0)
        for key,value in [('dense_terms',121),('evaluated_terms',59),('skipped_terms',59),
                          ('transforms',1),('fallback',3),('compiled_rules',3),('mismatches',1),
                          ('calls',1),('verified',1),('apply_seconds',1),('compile_seconds',1),
                          ('reference_seconds',-.1),('dense_terms',float('nan'))]:
            bad=copy.deepcopy(rows);bad[0]['metrics']['front_transform_'+key]=value
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError(key)
        bad=copy.deepcopy(rows);del bad[1]['metrics']['front_transform_verified']
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('missing per-rank counter')
    rows=fixture('split_active',1)
    rows[0]['metrics']['front_transform_evaluated_terms']=119
    rows[0]['metrics']['front_transform_skipped_terms']=1
    try:inspect(rows)
    except ValueError:pass
    else:raise AssertionError('compiled work entered control')
print('PASS: compiled transform conservation, candidate coverage, timing, reference isolation and control gates')
