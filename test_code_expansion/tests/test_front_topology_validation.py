#!/usr/bin/env python3
"""Reject missing query verification and changed rule matching coverage."""
import copy
import json
import tempfile
from pathlib import Path
from test_worklet_routes import fixture, profiles, routes
with tempfile.TemporaryDirectory() as directory:
    path=Path(directory)/'rank_profiles.jsonl'
    def inspect(rows):
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        return profiles.inspect(path)[0]
    for route in ('front_profile','front_topology'):
        for repeat in (0,1):
            rows=fixture(route,repeat)
            result=inspect(rows)
            assert result['front_match_calls']==40
            changes=[('builds',21),('mismatches',1),('build_seconds',1),('queries',float('nan')),
                     ('seconds',1),('indexed_visits',101),('linear_visits',-1)]
            changes.append(('verified',0 if repeat==0 and route=='front_topology' else 1))
            if route=='front_profile':changes.append(('queries',1))
            for key,value in changes:
                bad=copy.deepcopy(rows);bad[0]['metrics']['front_match_'+key]=value
                try:inspect(bad)
                except ValueError:pass
                else:raise AssertionError((route,repeat,key))
            bad=copy.deepcopy(rows);bad[0]['metadata']['front_match_policy']='unknown'
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted missing policy')
    # All-zero fronts are legitimate but cannot claim the index was exercised.
    rows=fixture('front_topology',1)
    for row in rows:
        for key in row['metrics']:
            if key.startswith('front_match_'):row['metrics'][key]=0
    assert inspect(rows)['front_match_queries']==0
    root=Path(directory)/'routes'
    for route in ('batch_parallel','front_profile','front_topology'):
        folder=root/('route_'+route)/'p2';folder.mkdir(parents=True)
        (folder/'plan.json').write_text(json.dumps(dict(ranks=2,repeats=1,
            algorithms=['sparse'],timings=['natural'],partition_seeds=[-1],quality_warmup=True)))
        for repeat in (0,1):
            d=folder/'sparse_natural'/f'repeat_{repeat}';d.mkdir(parents=True);(d/'SUCCESS').touch()
            (d/'rank_profiles.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in fixture(route,repeat)))
    assert routes.analyze(root,2,selected=('batch_parallel','front_profile','front_topology'))
    changed=root/'route_front_topology/p2/sparse_natural/repeat_1/rank_profiles.jsonl'
    original=changed.read_text()
    for key in ('calls','mappings','admitted_rules'):
        rows=[json.loads(line) for line in original.splitlines()]
        rows[0]['metrics']['front_match_'+key]+=1
        changed.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        assert not routes.analyze(root,2,selected=('batch_parallel','front_profile','front_topology')),key
    rows=[json.loads(line) for line in original.splitlines()]
    rows[0]['metrics']['front_match_mappings']+=1
    rows[1]['metrics']['front_match_mappings']-=1
    changed.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    assert not routes.analyze(root,2,selected=('batch_parallel','front_profile','front_topology'))
print('PASS: front query verification, invalid counters, zero-work and changed mapping coverage gates')
