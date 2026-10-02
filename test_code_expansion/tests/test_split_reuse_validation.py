#!/usr/bin/env python3
"""Reject missing exact warmup checks, impossible counts and timings."""
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
    for route in ('split_profile','split_reuse'):
        for repeat in (0,1):
            rows=fixture(route,repeat)
            assert inspect(rows)['split_proposal_applied']==4
            changes=[('proposals',11),('attempts',3),('reused',5),
                     ('evaluation_seconds',1),('mismatches',1)]
            if repeat==0 and route=='split_reuse':changes.append(('verified',0))
            if route=='split_profile':changes.append(('cache_bytes_sum',1))
            for key,value in changes:
                bad=copy.deepcopy(rows)
                bad[0]['metrics']['split_proposal_'+key]=value
                try:
                    inspect(bad)
                except ValueError:
                    pass
                else:
                    raise AssertionError((route,repeat,key))
print('PASS: split proposal counts, exact warmup coverage and timing gates')
