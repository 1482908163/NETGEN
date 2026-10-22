#!/usr/bin/env python3
"""原通信不应要求稀疏时间戳；误拒记录可离线恢复，真实失败不能改写。"""
import copy
import csv
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'strong_scaling'))
import analyze_results as analysis
import recover_borrow_results as recovery


def profile(algorithm='baseline'):
    metrics = dict(core_seconds=10, local_volume_elements_before_adjacency=8,
                   local_volume_complete_elapsed=3, vertex_arrival_elapsed=4)
    for arrival, complete in (
        ('vertex_count_pre_collective_wait_arrival_elapsed','vertex_count_complete_elapsed'),
        ('element_count_pre_collective_wait_arrival_elapsed','element_count_complete_elapsed'),
        ('volume_size_exchange_pre_collective_wait_arrival_elapsed','volume_size_exchange_complete_elapsed')):
        metrics[arrival], metrics[complete] = 4, 5
    if algorithm == 'sparse':
        for name in ('match','directory','deliver'):
            metrics[f'face_sparse_{name}_arrival_elapsed'] = 1
            metrics[f'face_sparse_{name}_complete_elapsed'] = 2
    return dict(rank=0, ranks=1, repeat=1,
                metadata=dict(algorithm=algorithm, timing_mode='natural', core_only='true',
                              natural_sync_trace='natural_sync_v1'), metrics=metrics,
                stages={'local_volume_mesh':dict(seconds=2,calls=1)})


with tempfile.TemporaryDirectory() as temp:
    root=Path(temp)
    path=root/'rank_profiles.jsonl'
    original=profile()
    path.write_text(json.dumps(original)+'\n')
    result,_=analysis.inspect(path)
    assert result['core_seconds']==10 and result['sync_vertex_count_post_latest_seconds']==1
    assert 'sync_face_match_arrival_skew_seconds' not in result
    sparse=profile('sparse');path.write_text(json.dumps(sparse)+'\n')
    assert analysis.inspect(path)[0]['sync_face_match_max_local_seconds']==1
    # 真正缺失的稀疏阶段、所有算法共用阶段及倒序时间戳仍然报错。
    for data,field,invert in ((sparse,'face_sparse_match_arrival_elapsed',False),
                              (original,'vertex_count_complete_elapsed',False),
                              (original,'vertex_count_complete_elapsed',True)):
        broken=copy.deepcopy(data)
        if invert:broken['metrics'][field]=0
        else:del broken['metrics'][field]
        path.write_text(json.dumps(broken)+'\n')
        try:analysis.inspect(path)
        except ValueError:pass
        else:raise AssertionError('invalid timestamps accepted')
    folder=root/'p1';directory=folder/'baseline_natural/repeat_1';directory.mkdir(parents=True)
    (directory/'rank_profiles.jsonl').write_text(json.dumps(original)+'\n')
    (directory/'run.log').write_text('finished mesh')
    (folder/'plan.json').write_text('{"algorithms":["baseline"]}')
    row=dict(algorithm='baseline',timing='natural',repeat=1,exit_code=1,directory=str(directory),partition_seed=-1)
    with (folder/'run_status.tsv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=recovery.STATUS_FIELDS,delimiter='\t');writer.writeheader();writer.writerow(row)
    (folder/'failures.log').write_text(f'baseline/natural seed -1 repeat 1: Invalid completed run ({directory}): '+recovery.KNOWN_ERROR+'\n')
    plan=dict(modes=['natural'],seeds=[-1],warmups=0,repeats=1)
    assert recovery.recover_folder(folder,plan,'node_original')==1
    assert (directory/'SUCCESS').exists() and (directory/'analysis_metrics.json').exists()
    assert not (directory/'rank_profiles.jsonl').exists()
    assert recovery.recover_folder(folder,plan,'node_original')==0
    # 历史记录中有同一种后处理错误，也不能把随后真实的超时改成成功。
    with (folder/'run_status.tsv').open('a') as stream:
        csv.DictWriter(stream,fieldnames=recovery.STATUS_FIELDS,delimiter='\t').writerow(dict(row,exit_code=124))
    before=(folder/'run_status.tsv').read_bytes()
    try:recovery.recover_folder(folder,plan,'node_original')
    except ValueError:pass
    else:raise AssertionError('real failure recovered')
    assert before==(folder/'run_status.tsv').read_bytes()
print('PASS: baseline/sparse timestamps, invalid records, offline recovery, idempotence and genuine failures')
