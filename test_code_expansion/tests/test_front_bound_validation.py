#!/usr/bin/env python3
"""Full replay coverage, geometric work conservation and same-rank mapping."""
import copy,json,tempfile
from pathlib import Path
from test_worklet_routes import fixture,profiles,routes
with tempfile.TemporaryDirectory() as directory:
    path=Path(directory)/'rank_profiles.jsonl'
    def inspect(rows):
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows));return profiles.inspect(path)[0]
    for route in ('front_bound_profile','front_bound'):
        for repeat in (0,1):
            rows=fixture(route,repeat);assert inspect(rows)['front_bound_candidates']==200
            changes=[('quality_pruned',81),('evaluations',101),('geometry_candidates',101),('face_tests',-1),('seconds',1),('mismatches',1),('calls',float('nan'))]
            changes.append(('verified',0 if route=='front_bound' and repeat==0 else 1))
            for key,value in changes:
                bad=copy.deepcopy(rows);bad[0]['metrics']['front_bound_'+key]=value
                try:inspect(bad)
                except ValueError:pass
                else:raise AssertionError((route,repeat,key))
    rows=fixture('front_bound',1)
    for row in rows:
        for key in row['metrics']:
            if key.startswith('front_bound_'):row['metrics'][key]=0
    assert inspect(rows)['front_bound_calls']==0
    root=Path(directory)/'routes'
    selected=('batch_parallel','front_bound_profile','front_bound')
    for route in selected:
        folder=root/('route_'+route)/'p2';folder.mkdir(parents=True)
        (folder/'plan.json').write_text(json.dumps(dict(ranks=2,repeats=1,algorithms=['sparse'],timings=['natural'],partition_seeds=[-1],quality_warmup=True)))
        for repeat in (0,1):
            d=folder/'sparse_natural'/f'repeat_{repeat}';d.mkdir(parents=True);(d/'SUCCESS').touch()
            (d/'rank_profiles.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in fixture(route,repeat)))
    assert routes.analyze(root,2,selected=selected)
    changed=root/'route_front_bound/p2/sparse_natural/repeat_1/rank_profiles.jsonl'
    original=changed.read_text()
    rows=[json.loads(line) for line in original.splitlines()]
    rows[0]['metrics']['front_bound_calls']+=1;rows[1]['metrics']['front_bound_calls']-=1
    changed.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    assert not routes.analyze(root,2,selected=selected)
print('PASS: complete replay coverage, work conservation, zero work and redistributed mapping gates')
