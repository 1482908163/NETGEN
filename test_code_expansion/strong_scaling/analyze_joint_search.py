#!/usr/bin/env python3
"""离线复核十策略联合实验，补算旧最佳基线和组件净收益；不运行网格、不清理源数据。"""
import argparse
import csv
import json
import math
from pathlib import Path
from statistics import median

from rule_search_batch import audit_job

L, I, R, O, F = ('node_search_'+x for x in ('local','idle','remote','online','full'))
PAIRS = (
    ('new_vs_previous', 'node_rules_elastic', F),
    ('online_vs_previous', 'node_rules_elastic', O),
    ('new_vs_elastic', 'node_elastic', F),
    ('new_vs_original', 'node_original', F),
    ('adaptive_search', 'node_rules_fixed', L),
    ('protocol_path', L, I),
    ('remote_increment', I, R),
    ('remote_net', L, R),
    ('online_local', L, O),
    ('remote_with_online', O, F),
    ('online_with_remote', R, F),
)
WORK = ('scaling_kernel_work_sha256','kernel_rules_calls','kernel_rules_rules',
        'kernel_rules_face_trials','kernel_rules_point_trials','kernel_rules_mappings')


def check(valid, reason):
    if not valid:
        raise ValueError(reason)


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        writer=csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)


def analyze(root, output):
    plan=json.loads((root/'rule_search_batch_plan.json').read_text())
    check(plan['warmups']==1,'当前复核格式要求每策略一次预热')
    comparisons=[];coverage=[];interactions=[];all_results=[];identities=set()
    check(set((L,I,R,O,F,'node_rules_elastic')).issubset(plan['schedulers']), '需要联合实验策略集')
    for job in plan['jobs']:
        # Recheck the retained quality/workload comparison evidence before
        # deriving new contrasts. This does not rerun deleted mesh audits.
        audit_job(root,job,plan)
        state=json.loads((root/job['key']/'rule_search_job_state.json').read_text())
        check(state['state']=='finished' and state['exit_code']==0, '作业尚未正常完成：'+job['key'])
        folder=root/job['group']/('levels'+str(job['levels']))
        runs={};job_results=[];qualities=[]
        for name in plan['schedulers']:
            files=list((folder/f'kernel_{name}_t4'/f"p{job['ranks']}").rglob('analysis_metrics.json'))
            records={}
            for path in files:
                data=json.loads(path.read_text());r=data['result']
                check(data['schema']=='analysis_metrics_v1','未知精简数据格式')
                key=(r['partition_seed'],r['repeat'])
                check(key not in records,'重复的种子/重复编号：'+str(path))
                check(r['ranks']==job['ranks'] and r['timing']=='natural','规模/计时不一致：'+str(path))
                check(math.isfinite(r['core_seconds']) and r['core_seconds']>0,'核心时间非法')
                check(r['kernel_rules_mismatches']==0,'规则等价检查失败')
                check(r['kernel_rules_parallel_calls']+r['kernel_rules_serial_calls']==r['kernel_rules_calls'],
                      '规则调用数不闭合')
                if r['repeat']<=0:
                    q=r['mesh_quality'];qualities.append(q)
                    check(q['structural_pass'] and not q['issues'] and q['counts']['hard_errors']==0,'质量检查失败')
                    if name.startswith(('node_rules_','node_search_')):
                        check(r['kernel_rules_verified_calls']==r['kernel_rules_parallel_calls'], '预热未完整重放')
                else:
                    check(r['kernel_rules_verified_calls']==0 and r['kernel_rules_verify_seconds']==0,
                          '正式运行混入重放')
                for c in data['critical']:
                    identities.add(tuple(c[k] for k in ('input_sha256','binary_sha256','kernel_sha256')))
                if name.startswith('node_search_'):
                    check(r['search_policy_decisions']==r['search_policy_serial']+r['search_policy_parallel'],
                          '自适应决策不闭合')
                if name in (I,R,F):
                    check(r['search_remote_grants']==r['search_policy_remote_calls']==r['search_remote_service_calls'],
                          '全局远程计数不闭合')
                records[key]=r;job_results.append(r);all_results.append(r)
            expected={(seed,rep) for seed in plan['seeds'] for rep in range(job['repeats']+1)}
            check(set(records)==expected,'运行覆盖不完整：'+job['key']+'/'+name)
            runs[name]=records
        for seed in plan['seeds']:
            for field in WORK:
                check(len({r[field] for r in job_results if r['partition_seed']==seed})==1,
                      '同种子工作量/搜索工作变化：'+job['key']+'/'+field)
            for field in ('surface_fingerprint_sha256','volume_fingerprint_sha256'):
                check(len({q[field] for q in qualities if q['seed']==seed})==1,
                      '同种子预热几何指纹变化：'+job['key']+'/'+field)
            common=dict(group=job['group'],levels=job['levels'],nodes=job['nodes'],ranks=job['ranks'],seed=seed)
            for label,control,candidate in PAIRS:
                aa=[runs[control][seed,i]['core_seconds'] for i in range(1,job['repeats']+1)]
                bb=[runs[candidate][seed,i]['core_seconds'] for i in range(1,job['repeats']+1)]
                delta=[100*(a-b)/a for a,b in zip(aa,bb)]
                comparisons.append(dict(common,comparison=label,control=control,candidate=candidate,
                    control_median=median(aa),candidate_median=median(bb),paired_median_pct=median(delta),
                    paired_min_pct=min(delta),paired_max_pct=max(delta),wins=sum(d>0 for d in delta),
                    repeats=len(delta),paired_pct=json.dumps(delta,separators=(',',':'))))
            values=[runs[R][seed,i]['core_seconds']+runs[O][seed,i]['core_seconds']
                    -runs[F][seed,i]['core_seconds']-runs[L][seed,i]['core_seconds']
                    for i in range(1,job['repeats']+1)]
            interactions.append(dict(common,median_seconds=median(values),min_seconds=min(values),max_seconds=max(values)))
            for name in plan['schedulers']:
                rs=[runs[name][seed,i] for i in range(1,job['repeats']+1)]
                coverage.append(dict(common,scheduler=name,repeats=len(rs),core_median=median(r['core_seconds'] for r in rs),
                    remote_calls=sum(r.get('search_policy_remote_calls',0) for r in rs),
                    remote_probes=sum(r.get('search_remote_probes',0) for r in rs),
                    cost_rejects=sum(r.get('search_remote_cost_rejects',0) for r in rs),
                    critical_rejects=sum(r.get('search_remote_critical_rejects',0) for r in rs),
                    max_rank_control_seconds=max(r.get('search_remote_control_seconds',0) for r in rs),
                    max_rule_call_seconds=max(r['kernel_rules_max_call_seconds'] for r in rs),
                    borrow_epochs_median=median(r['coop_borrow_epochs'] for r in rs)))
    check(len(identities)==1,'输入或二进制身份不一致')
    output.mkdir(parents=True,exist_ok=True)
    write_csv(output/'comparisons.csv',comparisons)
    write_csv(output/'coverage.csv',coverage)
    write_csv(output/'interaction.csv',interactions)
    warm=[r for r in all_results if r['repeat']<=0];formal=[r for r in all_results if r['repeat']>0]
    validation=dict(schema='joint_search_review_v1',runs=len(all_results),warmups=len(warm),formal=len(formal),
        verified_calls=sum(r['kernel_rules_verified_calls'] for r in warm),
        mismatches=sum(r['kernel_rules_mismatches'] for r in all_results),identities=sorted(identities),
        remote_calls=sum(r.get('search_policy_remote_calls',0) for r in all_results),
        formal_probes=sum(r.get('search_remote_probes',0) for r in formal),
        formal_cost_rejects=sum(r.get('search_remote_cost_rejects',0) for r in formal),
        post_generation_work_fingerprint_runs=sum('scaling_work_sha256' in r for r in all_results),
        note='复核保留的质量与计数证据；远程调用为零不代表跨节点候选执行已验证。')
    (output/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2)+'\n')
    print(f"复核 {len(all_results)} 次运行；远程任务 {validation['remote_calls']}。")
    for r in comparisons:
        if r['group']=='main' and r['comparison']=='new_vs_previous':
            print(f"L{r['levels']}/{r['nodes']}节点：相对上轮组合 {r['paired_median_pct']:+.2f}%，胜出 {r['wins']}/{r['repeats']}。")
    print(output)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args()
    analyze(args.root,args.output_dir or args.root/'joint_analysis')
