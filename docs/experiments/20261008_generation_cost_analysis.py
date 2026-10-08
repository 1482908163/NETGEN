#!/usr/bin/env python3
"""Recompute uploaded v1 evidence without accepting omitted repair costs."""
import argparse
import collections
import csv
import gzip
import json
import math
from pathlib import Path
import statistics as st
import sys

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'test_code_expansion/strong_scaling'))
import analyze_results as profiles
from cost_profile_checks import PHASES,OPERATIONS,STAGES

def csv_rows(path,delimiter=','):
    with path.open() as stream:return list(csv.DictReader(stream,delimiter=delimiter))

def write_csv(path,rows):
    with path.open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]) if rows else [])
        if rows:writer.writeheader();writer.writerows(rows)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--old-root',type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).parent)
    args=parser.parse_args();root=args.root
    statuses=[];failures=[];qualities={};baseline={}
    for path in sorted(root.rglob('run_status.tsv')):
        route=path.relative_to(root).parts[0];ranks=int(path.parent.name[1:])
        rows=csv_rows(path,'\t')
        assert len(rows)==12 and {(int(r['partition_seed']),int(r['repeat'])) for r in rows}=={
            (seed,repeat) for seed in (-1,17,41) for repeat in range(4)}
        for row in rows:
            seed=int(row['partition_seed']);repeat=int(row['repeat']);exit_code=int(row['exit_code'])
            statuses.append(dict(route=route,ranks=ranks,seed=seed,repeat=repeat,exit_code=exit_code))
    assert len(statuses)==72
    for path in sorted(root.rglob('failure_reason.txt')):
        text=path.read_text()
        kind=('missing_launcher' if "node_affinity_yhrun.sh': No such file or directory" in text else
              'missing_cost_module' if "No module named 'cost_profile_checks'" in text else 'other')
        failures.append(dict(path=str(path.relative_to(root)),kind=kind))
    for path in sorted(root.rglob('quality_summary.json')):
        q=json.loads(path.read_text())
        assert q['structural_pass'] and not q['issues']
        qualities[path.relative_to(root).parts[0],q['ranks'],q['seed']]=q
    quality_pairs=[]
    for ranks in (128,256,512):
        for seed in (-1,17,41):
            a=qualities['route_batch_parallel',ranks,seed]
            b=qualities['route_cost_profile',ranks,seed]
            equal={k:v for k,v in a.items() if k!='scheduler'}=={k:v for k,v in b.items() if k!='scheduler'}
            assert equal
            quality_pairs.append(dict(ranks=ranks,seed=seed,complete_equal=True))
    for path in sorted(root.glob('route_batch_parallel/p*/analysis/runs.csv')):
        for row in csv_rows(path):
            key=tuple(int(row[k]) for k in ('ranks','partition_seed','repeat'))
            assert row['timing']=='natural' and key not in baseline
            baseline[key]=row
    paired=[];fixed=[];cases=collections.defaultdict(list);native_coverage=collections.defaultdict(list)
    raw_runs=raw_rank_rows=cost_rows_count=partial_formal_runs=0
    for path in sorted(root.rglob('rank_profiles.jsonl.gz')):
        costs=[];result,_=profiles.inspect(path,cost_sink=costs)
        raw_runs+=1;raw_rank_rows+=result['ranks'];cost_rows_count+=len(costs)
        rows=[json.loads(line) for line in gzip.open(path,'rt') if line.strip()]
        metadata=rows[0]['metadata']
        identity=qualities['route_cost_profile',result['ranks'],result['partition_seed']]['identity']
        assert all(metadata[k]==v for k,v in identity.items())
        if result['repeat']==0:
            fresh=result['mesh_quality'];saved=qualities['route_cost_profile',result['ranks'],result['partition_seed']]
            # Re-summing floats on another analysis runtime can change their
            # last bits; all per-rank records and fingerprints remain exact.
            aggregates=('shape_mean','abs_volume')
            assert {k:v for k,v in fresh.items() if k not in aggregates}=={k:v for k,v in saved.items() if k not in aggregates}
            assert all(math.isclose(fresh[k],saved[k],rel_tol=1e-12,abs_tol=1e-12) for k in aggregates)
            continue
        key=tuple(result[k] for k in ('ranks','partition_seed','repeat'))
        assert key in baseline
        control=baseline[key];rank=int(baseline[key[:2]+(1,)]['slowest_compute_rank'])
        assert not result['cost_coverage_complete']
        partial_formal_runs+=1
        paired.append(dict(ranks=key[0],seed=key[1],repeat=key[2],
            baseline_core_seconds=float(control['core_seconds']),diagnostic_core_seconds=result['core_seconds'],
            core_delta_pct=100*(result['core_seconds']/float(control['core_seconds'])-1),
            baseline_compute_seconds=float(control['compute_max_seconds']),
            diagnostic_compute_seconds=result['compute_max_seconds'],
            compute_delta_pct=100*(result['compute_max_seconds']/float(control['compute_max_seconds'])-1),
            baseline_slowest_rank=int(control['slowest_compute_rank']),diagnostic_slowest_rank=result['slowest_compute_rank'],
            planned_repeats=3,complete_repeats=False,cost_coverage_complete=False))
        selected=[dict(row,fixed_baseline_rank=rank) for row in costs if row['rank']==rank]
        fixed.extend(selected)
        cases[key[:2]].append((result,selected))
        for cost in costs:
            native_coverage[cost['phase'],cost['operation']].append(cost)
    summaries=[]
    for key,values in sorted(cases.items()):
        phase_data={}
        for phase in PHASES:
            phase_data[phase]={field:st.median(sum(c[field] for c in selected if c['phase']==phase)
                                            for result,selected in values)
                               for field in ('total_seconds',)+STAGES}
            phase_data[phase]['phase_seconds']=st.median(next(c['phase_seconds'] for c in selected if c['phase']==phase)
                                                       for result,selected in values)
        summaries.append(dict(ranks=key[0],seed=key[1],samples=len(values),
            fixed_rank=values[0][1][0]['fixed_baseline_rank'],phases=phase_data))
    coverage=[]
    for (phase,op),values in sorted(native_coverage.items()):
        native=sum(v['native_seconds'] for v in values);scoped=sum(v['total_seconds'] for v in values)
        coverage.append(dict(phase=phase,operation=op,rank_records=len(values),
            active_native_rank_records=sum(v['native_seconds']>1e-4 for v in values),
            zero_scoped_active_records=sum(v['native_seconds']>1e-4 and v['calls']==0 for v in values),
            mismatched_records=sum(not v['coverage_match'] for v in values),
            aggregate_native_seconds=native,aggregate_scoped_seconds=scoped,
            scoped_native_ratio=scoped/native if native else None))
    old_evidence=dict(reports=0,frames=0,source_line_frames=0,function_mappings=0)
    if args.old_root:
        for path in args.old_root.rglob('failure_symbols.json'):
            report=json.loads(path.read_text());old_evidence['reports']+=1
            old_evidence['frames']+=len(report['frames'])
            old_evidence['source_line_frames']+=sum(f.get('has_source_line',False) for f in report['frames'])
            old_evidence['function_mappings']+=sum(bool(f.get('resolution',{}).get('stdout')) for f in report['frames'])
    summary=dict(upload_commit='33d09de2f307fdedd52e39853d0ec63fae0963c7',
        measured_source_revision=next(iter(qualities.values()))['identity']['MESH_SOURCE_REVISION'],
        dataset=root.name,launches=len(statuses),successful_warmups=sum(s['repeat']==0 and s['exit_code']==0 for s in statuses),
        successful_formal=sum(s['repeat']>0 and s['exit_code']==0 for s in statuses),
        failure_types=dict(collections.Counter(f['kind'] for f in failures)),failures=failures,
        quality_pairs=quality_pairs,raw_runs=raw_runs,raw_rank_rows=raw_rank_rows,
        cost_rows=cost_rows_count,paired_formal=len(paired),partial_formal_runs=partial_formal_runs,
        coverage=coverage,fixed_rank_summaries=summaries,old_failure_evidence=old_evidence,
        note='v1 stage/goal conservation is valid, but generation/repair coverage is partial. '
             'Only final optimization costs are corroborated by native timers. No three-repeat comparison is complete. '
             'Aggregated rank wall times are coverage evidence, not critical-path speedup potential.')
    assert summary['successful_warmups']==18 and summary['successful_formal']==31
    assert summary['failure_types']=={'missing_launcher':21,'missing_cost_module':2}
    assert raw_runs==23 and raw_rank_rows==7424 and cost_rows_count==89088
    assert len(paired)==14 and partial_formal_runs==14
    args.output.mkdir(parents=True,exist_ok=True)
    prefix=args.output/'20261008_generation_cost'
    Path(str(prefix)+'_analysis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    write_csv(Path(str(prefix)+'_pairs.csv'),paired)
    write_csv(Path(str(prefix)+'_fixed_rank_costs.csv'),fixed)
    print(json.dumps({k:summary[k] for k in ('launches','successful_warmups','successful_formal','failure_types',
          'raw_runs','raw_rank_rows','cost_rows','paired_formal','old_failure_evidence')},indent=2))

if __name__=='__main__':main()
