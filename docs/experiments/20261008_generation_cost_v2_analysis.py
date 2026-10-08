#!/usr/bin/env python3
"""Recompute successful v2 runs; retain failed checks as failures."""
import argparse
import collections
import csv
import gzip
import hashlib
import json
from pathlib import Path
import statistics as st
import sys

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'test_code_expansion/strong_scaling'))
import analyze_results as profiles
from cost_profile_checks import PHASES,OPERATIONS,STAGES

def read_csv(path,delimiter=','):
    with path.open() as stream:return list(csv.DictReader(stream,delimiter=delimiter))

def write_csv(path,rows):
    with path.open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).parent)
    args=parser.parse_args();root=args.root
    manifest=json.loads((root/'runtime_manifest.json').read_text())
    runtime_sha=hashlib.sha256((root/'runtime_manifest.json').read_bytes()).hexdigest()
    assert manifest['source_revision']=='aef5316da46bf37567c18f8b7d84b1156131cb33'
    statuses={};status_counts=collections.Counter();quality={};baseline={}
    for path in root.glob('route_*/p*/run_status.tsv'):
        route=path.relative_to(root).parts[0];ranks=int(path.parent.name[1:])
        rows=read_csv(path,'\t')
        assert {(int(r['partition_seed']),int(r['repeat'])) for r in rows}=={
            (seed,repeat) for seed in (-1,17,41) for repeat in range(4)} and len(rows)==12
        for row in rows:
            key=(route,ranks,int(row['partition_seed']),int(row['repeat']))
            assert key not in statuses;statuses[key]=int(row['exit_code'])
            status_counts['warmup' if key[-1]==0 else 'formal',statuses[key]]+=1
    assert len(statuses)==72
    for path in root.glob('route_*/p*/*/repeat_0/quality_summary.json'):
        q=json.loads(path.read_text());assert q['structural_pass'] and not q['issues']
        quality[path.relative_to(root).parts[0],q['ranks'],q['seed']]=q
    for ranks in (128,256,512):
        for seed in (-1,17,41):
            a=quality['route_batch_parallel',ranks,seed];b=quality['route_cost_profile',ranks,seed]
            assert {k:v for k,v in a.items() if k!='scheduler'}=={k:v for k,v in b.items() if k!='scheduler'}
    for path in root.glob('route_batch_parallel/p*/analysis/runs.csv'):
        for row in read_csv(path):baseline[tuple(int(row[k]) for k in ('ranks','partition_seed','repeat'))]=row
    pairs=[];critical=[];coverage=collections.defaultdict(list)
    repair=collections.Counter();by_scale=collections.defaultdict(collections.Counter)
    raw_runs=raw_ranks=0;seen=set()
    for path in sorted(root.glob('route_cost_profile/p*/*/repeat_*/rank_profiles.jsonl.gz')):
        with gzip.open(path,'rt') as stream:rows=[json.loads(line) for line in stream if line.strip()]
        row0=rows[0];key=(row0['ranks'],int(row0['metadata']['partition_seed']),row0['repeat'])
        if statuses[('route_cost_profile',)+key]!=0:
            continue  # Newly archived failures must never enter the valid pool.
        costs=[];result,_=profiles.inspect(path,cost_sink=costs)
        assert result['volume_cost_profile']=='phase_full_cost_v2' and result['cost_coverage_complete']
        assert key not in seen;seen.add(key);raw_runs+=1;raw_ranks+=len(rows)
        q=quality['route_cost_profile',key[0],key[1]]
        assert all(row0['metadata'][k]==v for k,v in q['identity'].items())
        assert row0['metadata']['MESH_RUNTIME_SHA256']==runtime_sha
        if key[-1]==0:continue
        b=baseline[key];fixed=int(baseline[key[:2]+(1,)]['slowest_compute_rank'])
        pairs.append(dict(ranks=key[0],seed=key[1],repeat=key[2],
            baseline_core_seconds=float(b['core_seconds']),diagnostic_core_seconds=result['core_seconds'],
            paired_reduction_pct=100*(1-result['core_seconds']/float(b['core_seconds'])),
            baseline_compute_seconds=float(b['compute_max_seconds']),diagnostic_compute_seconds=result['compute_max_seconds'],
            baseline_slowest_rank=int(b['slowest_compute_rank']),diagnostic_slowest_rank=result['slowest_compute_rank']))
        m=next(row['metrics'] for row in rows if row['rank']==fixed)
        fixed_row=next(row for row in rows if row['rank']==fixed)
        compute=sum(fixed_row['stages'][s]['seconds'] for s in profiles.COMPUTE)
        for phase in PHASES:
            cs=[c for c in costs if c['rank']==fixed and c['phase']==phase]
            critical.append(dict(ranks=key[0],seed=key[1],repeat=key[2],fixed_rank=fixed,phase=phase,
                fixed_compute_seconds=compute,phase_seconds=m['kernel_'+phase+'_seconds'],
                final_illegal=m['kernel_final_illegal'],
                **{f:sum(c[f] for c in cs) for f in ('total_seconds',)+STAGES+('calls','candidates','applied')}))
        for c in costs:coverage[c['phase'],c['operation']].append(c)
        for row in rows:
            m=row['metrics'];ranks=row['ranks'];repair['records']+=1;by_scale[ranks]['records']+=1
            calls=[m[f'cost_repair_{op}_calls'] for op in ('split','swap','swap2')]
            applied=sum(m[f'cost_repair_{op}_applied'] for op in ('split','swap','swap2'))
            candidates=sum(m[f'cost_repair_{op}_candidates'] for op in ('split','swap','swap2'))
            if calls==[10,10,10] and applied==0:
                repair['ten_rounds_zero_applied']+=1;by_scale[ranks]['ten_rounds_zero_applied']+=1
            repair['records_with_applied']+=int(applied>0)
            repair['records_with_candidates']+=int(candidates>0)
            for field in ('total_seconds',)+STAGES:
                repair[field]+=sum(m[f'cost_repair_{op}_{field}'] for op in ('split','swap','swap2'))
    expected={(r,s,k) for (route,r,s,k),rc in statuses.items() if route=='route_cost_profile' and k>0 and rc==0}
    assert {key for key in seen if key[-1]>0}==expected and len(pairs)==24
    assert repair['records']==6528 and repair['ten_rounds_zero_applied']==6243
    assert repair['records_with_applied']==285
    comparisons=[];fixed=[]
    for ranks in (128,256,512):
        for seed in (-1,17,41):
            subset=[p for p in pairs if (p['ranks'],p['seed'])==(ranks,seed)]
            comparisons.append(dict(ranks=ranks,seed=seed,pairs=len(subset),complete=len(subset)==3,
                paired_reduction_pct=st.median(p['paired_reduction_pct'] for p in subset)))
            c=[x for x in critical if (x['ranks'],x['seed'])==(ranks,seed) and x['phase']=='repair']
            fixed.append(dict(ranks=ranks,seed=seed,rank=c[0]['fixed_rank'],samples=len(c),
                phase_seconds=st.median(x['phase_seconds'] for x in c),
                four_operation_seconds=st.median(x['total_seconds'] for x in c),
                fraction_of_fixed_compute=st.median(x['phase_seconds']/x['fixed_compute_seconds'] for x in c),
                calls=st.median(x['calls'] for x in c),candidates=max(x['candidates'] for x in c),applied=max(x['applied'] for x in c)))
    coverage_report=[]
    for (phase,op),cs in sorted(coverage.items()):
        native=sum(c['native_seconds'] for c in cs);scoped=sum(c['total_seconds'] for c in cs)
        coverage_report.append(dict(phase=phase,operation=op,records=len(cs),
            native_seconds=native,scoped_seconds=scoped,ratio=scoped/native if native else None,
            all_pass=all(c['coverage_match'] for c in cs)))
    failed=[]
    for path in root.rglob('failure_reason.txt'):
        text=path.read_text();assert 'native operation coverage mismatch' in text and 'successful!!!' in text
        failed.append(dict(path=str(path.relative_to(root)),reason=text.split('finalization_error=')[1].split('\n')[0]))
    assert len(failed)==3 and status_counts=={('warmup',0):18,('formal',0):51,('formal',1):3}
    summary=dict(dataset=root.name,upload_commit='e6670035c6cb4123985905a88095cebaa8b79937',
        measured_source=manifest['source_revision'],runtime_sha256=runtime_sha,
        successful_warmups=18,successful_formal=51,rejected_formal=3,complete_quality_pairs=9,
        verified_raw_runs=raw_runs,verified_raw_rank_records=raw_ranks,
        successful_raw_not_loaded=[dict(ranks=r,seed=s,repeat=k) for (route,r,s,k),rc in statuses.items()
            if route=='route_cost_profile' and rc==0 and (r,s,k) not in seen],
        formal_rank_records=6528,repair=dict(repair),repair_by_scale=dict(by_scale),
        comparisons=comparisons,fixed_rank_repair=fixed,coverage=coverage_report,failed=failed,
        evidence_limit='Rank-wall sums describe cost composition, not critical-path savings. Zero applied operations do not prove every mesh/cache state unchanged; stopping needs an exact state/progress contract. Failed timing checks remain invalid.')
    args.output.mkdir(parents=True,exist_ok=True);prefix=args.output/'20261008_generation_cost_v2'
    Path(str(prefix)+'_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    write_csv(Path(str(prefix)+'_pairs.csv'),pairs);write_csv(Path(str(prefix)+'_critical_costs.csv'),critical)
    print(json.dumps({k:summary[k] for k in ('successful_warmups','successful_formal','rejected_formal','verified_raw_runs','formal_rank_records')},indent=2))

if __name__=='__main__':main()
