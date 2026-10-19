#!/usr/bin/env python3
"""Conditional compute makespan bounds from already validated route exports.

Assume one original domain per rank, identical worker allocations, invariant
task durations, and a common compute release time. These are model bounds,
not forecasts of MPI waiting, placement effects or end-to-end speedup.
"""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def bounds(compute_max, compute_mean, local_volume_max):
    values=(compute_max,compute_mean,local_volume_max)
    if any(not math.isfinite(v) or v<0 for v in values) or compute_max<=0:
        raise ValueError('invalid compute durations')
    if compute_mean>compute_max+1e-6 or local_volume_max>compute_max+1e-6:
        raise ValueError('inconsistent compute durations')
    lower=max(compute_mean,local_volume_max)
    return dict(compute_seconds=compute_max,mean_work_seconds=compute_mean,
                longest_volume_seconds=local_volume_max,
                whole_task_reassignment_reduction_pct=0.0,
                keep_volume_indivisible_lower_seconds=lower,
                keep_volume_indivisible_reduction_ceiling_pct=100*(compute_max-lower)/compute_max,
                fully_divisible_reduction_ceiling_pct=100*(compute_max-compute_mean)/compute_max)


def analyze(root,route='refine_serial'):
    output=[]
    for directory in sorted(root.glob('p*')):
        path=directory/'route_runs.csv'
        if not path.exists():continue
        comparisons=list(csv.DictReader((directory/'route_comparisons.csv').open()))
        rows=list(csv.DictReader(path.open()))
        selected=[r for r in rows if r['route']==route]
        groups={}
        for r in selected:groups.setdefault((r['timing'],r['partition_seed']),[]).append(r)
        for (timing,seed),runs in sorted(groups.items()):
            gates=[c for c in comparisons if c['candidate']==route and c['timing']==timing
                   and c['seed']==seed and c['validated']=='True'
                   and c['same_volume_fingerprint']=='True']
            if not gates or not any(int(g['paired_count'])==len(runs) for g in gates):
                raise ValueError(f'{directory}/{route}/{timing}: no complete validated comparison')
            if len({r['repeat'] for r in runs})!=len(runs):
                raise ValueError('duplicate repeat')
            samples=[bounds(float(r['compute_max_seconds']),float(r['compute_mean_seconds']),
                            float(r['slowest_local_volume_seconds'])) for r in runs]
            item=dict(ranks=int(runs[0]['ranks']),route=route,timing=timing,seed=seed,repeats=len(runs))
            item.update({k:statistics.median(s[k] for s in samples) for k in samples[0]})
            output.append(item)
    if not output:raise ValueError('no validated route exports')
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--route',default='refine_serial',choices=['refine_serial'])
    args=parser.parse_args()
    print(json.dumps(analyze(args.root,args.route),ensure_ascii=False,indent=2))
