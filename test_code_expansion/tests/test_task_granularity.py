#!/usr/bin/env python3
"""Check scheduling bounds against small exhaustive assignments."""
import importlib.util
import itertools
from pathlib import Path

path=Path(__file__).resolve().parents[1]/'strong_scaling/analyze_task_granularity.py'
spec=importlib.util.spec_from_file_location('bounds',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

for costs in ([1,2,9],[3,3,3],[0,1,4],[2,5,7,8]):
    n=len(costs)
    best=min(max(sum(t for t,w in zip(costs,assignment) if w==worker)
                 for worker in range(n))
             for assignment in itertools.product(range(n),repeat=n))
    assert best==max(costs)
    r=module.bounds(max(costs),sum(costs)/n,max(costs))
    assert r['keep_volume_indivisible_reduction_ceiling_pct']==0
assert module.bounds(10,4,8)['keep_volume_indivisible_reduction_ceiling_pct']==20
assert module.bounds(10,9,8)['keep_volume_indivisible_reduction_ceiling_pct']==10
for args in [(0,0,0),(1,2,1),(1,0,2),(1,float('nan'),0)]:
    try:module.bounds(*args)
    except ValueError:pass
    else:raise AssertionError(args)
print('PASS: exhaustive whole-task assignments, work conservation and invalid timings')
