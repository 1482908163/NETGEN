#!/usr/bin/env python3
"""复算已核对的本轮保留数据；可另核对全部原始保留文件的 Git 内容哈希。"""
import argparse
import csv
import hashlib
import json
import statistics as st
from pathlib import Path

BASE=Path(__file__).resolve().parent

def read_json(path):
    return json.loads(Path(path).read_text())

def write_csv(path,rows):
    with Path(path).open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,default=BASE/'20261009_split_active_evidence.json')
    parser.add_argument('--out',type=Path,default=Path('split_active_analysis'))
    parser.add_argument('--verify-source-root',type=Path,help='本轮实验根目录：核对全部 220 个保留文件')
    args=parser.parse_args()
    data=read_json(args.evidence)
    assert data['schema']=='split_active_verified_evidence_v1'
    if args.verify_source_root:
        manifest=read_json(BASE/'20261009_split_active_source_manifest.json')
        assert len(manifest)==220 and len({r['path'] for r in manifest})==220
        for record in manifest:
            raw=(args.verify_source_root/record['path']).read_bytes()
            sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
            assert sha==record['sha'],record['path']
    rows=data['runs']
    assert len(rows)==81
    index={(r['route'],int(r['ranks']),int(r['partition_seed']),int(r['repeat'])):r for r in rows}
    assert len(index)==81
    pairs=[];groups=[]
    for p in (128,256,512):
        for seed in (-1,17,41):
            group={'ranks':p,'seed':seed}
            for control,candidate,label in (
                ('repair_fixed','split_active','queue'),
                ('batch_parallel','split_active','combined'),
                ('batch_parallel','repair_fixed','fixed')):
                records=[]
                for repeat in (1,2,3):
                    a=index[control,p,seed,repeat];b=index[candidate,p,seed,repeat]
                    assert a['source_revision']==b['source_revision']==data['algorithm_commit']
                    assert a['binary_sha256']==b['binary_sha256']
                    ac,bc=float(a['core_seconds']),float(b['core_seconds'])
                    av,bv=float(a['compute_max_seconds']),float(b['compute_max_seconds'])
                    assert min(ac,bc,av,bv)>0
                    record=dict(control=control,candidate=candidate,ranks=p,seed=seed,repeat=repeat,
                        control_core=ac,candidate_core=bc,core_reduction_pct=100*(1-bc/ac),
                        compute_reduction_pct=100*(1-bv/av),
                        control_rank=int(a['slowest_compute_rank']),candidate_rank=int(b['slowest_compute_rank']))
                    pairs.append(record);records.append(record)
                group[label]=dict(paired_core_pct=st.median(r['core_reduction_pct'] for r in records),
                    wins=sum(r['core_reduction_pct']>0 for r in records),
                    paired_compute_pct=st.median(r['compute_reduction_pct'] for r in records),
                    ratio_of_medians_pct=100*(1-st.median(r['candidate_core'] for r in records)/st.median(r['control_core'] for r in records)))
            groups.append(group)
    scales=[]
    for p in (128,256,512):
        here=[g for g in groups if g['ranks']==p]
        scales.append(dict(ranks=p,
            queue_pct=st.median(g['queue']['paired_core_pct'] for g in here),
            queue_wins=sum(g['queue']['wins'] for g in here),
            combined_pct=st.median(g['combined']['paired_core_pct'] for g in here),
            combined_wins=sum(g['combined']['wins'] for g in here),
            fixed_pct=st.median(g['fixed']['paired_core_pct'] for g in here)))
    fixed=data['certificate_checks'];operations=data['operation_checks']
    assert fixed['mismatches']==0 and fixed['rows']==21504
    assert fixed['warmReferenceEdges']==50174717
    assert operations['files']==72 and operations['rows']==258048
    assert operations['matchedOperationPairs']==129024
    assert len(data['quality_checks'])==18 and all(q['full_quality_equal'] for q in data['quality_checks'])
    same_rank=[]
    for t in data['same_rank']:
        a=t['repair_fixed']['samples'];b=t['split_active']['samples']
        assert [s['repeat'] for s in a]==[s['repeat'] for s in b]==[1,2,3]
        for x,y in zip(a,b):
            assert x['active']==y['active'] and x['edges']==y['edges']
        same_rank.append(dict(ranks=t['ranks'],seed=t['seed'],rank=t['rank'],
            control_compute=st.median(s['compute'] for s in a),candidate_compute=st.median(s['compute'] for s in b),
            control_split_evaluate=st.median(s['split_evaluate'] for s in a),candidate_split_evaluate=st.median(s['split_evaluate'] for s in b),
            paired_split_reduction_pct=st.median(100*(1-y['split_evaluate']/x['split_evaluate']) for x,y in zip(a,b)),
            candidate_screen=st.median(s['screen'] for s in b),
            control_worker_sum=st.median(s['worker_sum'] for s in a),candidate_worker_sum=st.median(s['worker_sum'] for s in b),
            control_worker_max_sum=st.median(s['worker_max_sum'] for s in a),candidate_worker_max_sum=st.median(s['worker_max_sum'] for s in b)))
    summary=dict(batch_commit=data['batch_commit'],algorithm_commit=data['algorithm_commit'],
        groups=groups,scales=scales,quality=data['quality_checks'],certificates=fixed,operations=operations,
        global_work=data['global_work'],runtime_sources_matched=data['runtime_sources_matched'],raw_profiles_uploaded=data['raw_profiles_uploaded'],
        queue_wins=sum(g['queue']['wins'] for g in groups),queue_positive_seed_groups=sum(g['queue']['paired_core_pct']>0 for g in groups),
        combined_wins=sum(g['combined']['wins'] for g in groups),same_rank=same_rank)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'20261009_split_active_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    write_csv(args.out/'20261009_split_active_pairs.csv',pairs)
    write_csv(args.out/'20261009_split_active_same_rank.csv',same_rank)
    print(json.dumps(dict(scales=scales,queue_wins=summary['queue_wins'],combined_wins=summary['combined_wins']),ensure_ascii=False))

if __name__=='__main__':
    main()
