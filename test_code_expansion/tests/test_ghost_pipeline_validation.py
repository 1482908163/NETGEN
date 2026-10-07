#!/usr/bin/env python3
"""Reject scheduling, oracle and packet coverage drift."""
import copy,json,tempfile
from pathlib import Path
from test_worklet_routes import fixture,profiles,routes

def inspect(rows):
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'rank_profiles.jsonl'
        p.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        return profiles.inspect(p)[0]
for route in ('ghost_staged','ghost_pipeline'):
    for repeat in (0,1):
        good=fixture(route,repeat);inspect(good)
        for key in ('prepared_before_vertex_wait','preposted_count_peers','send_elements','reference_bytes','oracle_calls','oracle_verified_elements','oracle_mismatches','scanned_elements'):
            bad=copy.deepcopy(good);bad[0]['metrics']['ghost_plan_'+key]+=1
            try:inspect(bad)
            except (ValueError,KeyError):pass
            else:raise AssertionError('accepted bad '+key)
        bad=copy.deepcopy(good);bad[0]['stages']['vertex_exchange']=dict(seconds=.1,calls=1,category='communication')
        try:inspect(bad)
        except ValueError:pass
        else:raise AssertionError('accepted legacy vertex wait')
print('PASS: ghost plan lifecycle, scheduling, exact packet oracle and reference buffer gates')

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);selected=('batch_parallel','ghost_staged','ghost_pipeline')
    for route in selected:
        folder=root/('route_'+route)/'p2';folder.mkdir(parents=True)
        (folder/'plan.json').write_text(json.dumps(dict(ranks=2,repeats=1,algorithms=['sparse'],timings=['natural'],partition_seeds=[-1],quality_warmup=True)))
        for repeat in (0,1):
            d=folder/'sparse_natural'/f'repeat_{repeat}';d.mkdir(parents=True);(d/'SUCCESS').touch()
            (d/'rank_profiles.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in fixture(route,repeat)))
    assert routes.analyze(root,2,selected=selected)
    changed=root/'route_ghost_pipeline/p2/sparse_natural/repeat_1/rank_profiles.jsonl'
    rows=[json.loads(line) for line in changed.read_text().splitlines()]
    rows[0]['metrics']['vertex_send_items']+=1;rows[1]['metrics']['vertex_send_items']-=1
    changed.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    assert not routes.analyze(root,2,selected=selected)
for route in ('ghost_staged','ghost_pipeline'):
    rows=fixture(route,1)
    for row in rows:
        for key in ('selected_elements','send_elements','reference_bytes'):row['metrics']['ghost_plan_'+key]=0
        row['metrics']['volume_send_items']=0
    inspect(rows)
print('PASS: zero ghost work and unchanged aggregate/per-rank redistributed payload rejection')
