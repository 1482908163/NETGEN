#!/usr/bin/env python3
"""规则搜索整批计划、独立作业状态和汇总；不调用 Git 或集群调度命令。"""
import argparse
from contextlib import contextmanager
import csv
import io
import json
import math
import os
from pathlib import Path
import socket
import tempfile
import time

SCHEDULERS = ('node_original', 'node_fixed', 'node_elastic', 'node_rules_fixed', 'node_rules_elastic')
JOINT_SCHEDULERS = SCHEDULERS + ('node_search_local','node_search_idle','node_search_remote','node_search_online','node_search_full','node_search_elastic')
FOCUSED_SCHEDULERS = ('node_fixed','node_rules_fixed','node_rules_elastic','node_search_local','node_search_elastic','node_search_online','node_search_full')
FOCUSED_CONTRIBUTIONS = ('search_adaptive','search_online','search_remote_online','search_previous','search_order_elastic','search_model')
CONTRIBUTIONS = ('rule_search_fixed', 'rule_search_elastic', 'rule_search_borrowing', 'rule_search_total')
JOINT_CONTRIBUTIONS = ('search_adaptive','search_protocol','search_remote','search_online',
                       'search_remote_online','search_incremental','search_total','search_previous','search_order_elastic','search_model')
LABELS = dict(zip(CONTRIBUTIONS, ('固定核规则并行', '借核基础上规则并行', '规则并行基础上借核', '相对原程序总收益')))
LABELS.update(zip(JOINT_CONTRIBUTIONS,('规则搜索策略增量','跨节点协议开销','跨节点搜索增量',
                                     '在线借核增量','在线借核上的跨节点增量','相对原借核增量','联合方案总收益',
                                     '相对上轮规则并行与借核','旧借核基础上的搜索排序','同搜索下操作模型净收益')))


def atomic(path, content):
    # 同目录唯一临时文件：即使共享文件系统的 flock 只在节点内生效，
    # 两个写入者也不能删除或替换对方正在使用的临时文件。
    fd, name = tempfile.mkstemp(prefix='.'+path.name+'.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def summary_lock(root, timeout=10.):
    """用共享目录的原子 mkdir 互斥，不依赖跨节点 advisory lock（建议锁）。"""
    lock = root/'.rule_search_batch.lock.d'
    deadline = time.monotonic()+timeout
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f'汇总锁等待超时：{lock}；作业状态已独立保留。'
                                   '若持锁进程已终止，确认后移走该锁目录，再单独重建汇总。')
            time.sleep(0.05)
    try:
        (lock/'owner.json').write_text(json.dumps(dict(host=socket.gethostname(), pid=os.getpid(),
                                                     created_epoch=time.time()))+'\n')
        yield
    finally:
        (lock/'owner.json').unlink(missing_ok=True)
        lock.rmdir()


def integers(name, minimum=1):
    values = [int(x) for x in os.environ[name].replace(',', ' ').split()]
    if not values or len(values) != len(set(values)) or any(x < minimum for x in values):
        raise ValueError('配置无效或重复：'+name)
    return values


def initialize(root):
    schedulers=JOINT_SCHEDULERS if os.environ.get('SEARCH_JOINT_EXPERIMENT')=='1' else SCHEDULERS
    joint=schedulers==JOINT_SCHEDULERS
    contributions=JOINT_CONTRIBUTIONS if joint else CONTRIBUTIONS
    if joint and os.environ.get('SEARCH_FOCUSED')=='1':
        schedulers=FOCUSED_SCHEDULERS;contributions=FOCUSED_CONTRIBUTIONS
    if tuple(os.environ['KERNEL_SCHEDULERS'].split()) != schedulers:
        raise ValueError('整批预设要求完整策略集；自定义子集请使用 rule_search 预设')
    check_levels = integers('RULE_SEARCH_CHECK_LEVEL', 0)
    check_nodes = integers('RULE_SEARCH_CHECK_NODES')
    check_repeats = integers('RULE_SEARCH_CHECK_REPEATS')
    main_repeats = integers('RULE_SEARCH_MAIN_REPEATS')
    if any(len(v) != 1 for v in (check_levels, check_nodes, check_repeats, main_repeats)):
        raise ValueError('预检级数、预检节点数和两组重复次数必须为单个整数')
    main_levels = integers('RULE_SEARCH_MAIN_LEVELS', 0)
    main_nodes = integers('RULE_SEARCH_MAIN_NODES')
    seeds = integers('PARTITION_SEEDS', -1)
    jobs = []
    for group, levels, nodes, repeats in (
            ('precheck', check_levels, check_nodes, check_repeats[0]),
            ('main', main_levels, main_nodes, main_repeats[0])):
        for level in levels:
            for n in nodes:
                jobs.append(dict(key=f'{group}/levels{level}/p{n*4}', group=group,
                                 levels=level, nodes=n, ranks=n*4, repeats=repeats))
    plan = dict(schema='rule_search_batch_v1', jobs=jobs, schedulers=list(schedulers),
                contributions=list(contributions),
                seeds=seeds, threads=4, ranks_per_node=4, warmups=1,
                refines=int(os.environ['REFINES']), start_epoch=int(os.environ['START_EPOCH']),
                dry_run=os.environ.get('DRY_RUN') == '1',
                source_revision=os.environ.get('MESH_SOURCE_REVISION', 'manual'),
                joint_revision='matched_tasks_v4' if joint else None,
                input_path=os.environ['INPUT_PATH'], maxh=os.environ['MAXH'], minh=os.environ['MINH'])
    target = root/'rule_search_batch_plan.json'
    if target.exists():
        old = json.loads(target.read_text())
        old.setdefault('contributions',list(CONTRIBUTIONS))
        old.setdefault('joint_revision',None)
        ignored = {'start_epoch', 'dry_run'}
        if {k: v for k, v in old.items() if k not in ignored} != {k: v for k, v in plan.items() if k not in ignored}:
            raise ValueError('该结果目录已有不同实验计划，请换用新的 RUN_ROOT')
    atomic(target, json.dumps(plan, ensure_ascii=False, indent=2)+'\n')
    return plan


def read_csv(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def audit_job(root, job, plan):
    """已有逐规模质量审计通过后，才把比较结果放入整批汇总。"""
    folder = root/job['key']
    issues = folder/'kernel_issues.txt'
    if not issues.exists() or issues.read_text().strip():
        raise ValueError('逐规模审计缺失或未通过：'+str(issues.relative_to(root)))
    summary = read_csv(folder/'kernel_summary.csv')
    expected = {(s, str(seed), 'baseline' if s == 'node_original' else 'sparse')
                for s in plan['schedulers'] for seed in plan['seeds']}
    actual = {(r['scheduler'], r['seed'], r['algorithm']) for r in summary}
    if actual != expected or len(summary) != len(expected):
        raise ValueError('策略、种子或算法覆盖不完整')
    if any(int(r['repeats']) != job['repeats'] or r['timing'] != 'natural' or int(r['threads']) != 4 for r in summary):
        raise ValueError('正式重复数、计时模式或核数与计划不符')
    contributions=plan.get('contributions',CONTRIBUTIONS)
    audit = [r for r in read_csv(folder/'ablation_summary.csv') if r['contribution'] in contributions]
    expected = {(c, str(seed)) for c in contributions for seed in plan['seeds']}
    if {(r['contribution'], r['seed']) for r in audit} != expected or len(audit) != len(expected):
        raise ValueError('贡献对照不完整')
    for r in audit:
        if (r['quality_pass'] != 'True' or int(r['paired_count']) != job['repeats'] or
                r['timing'] != 'natural' or int(r['threads']) != 4):
            raise ValueError('质量或同编号重复配对未通过')
        if not 0 <= int(r['paired_wins']) <= job['repeats']:
            raise ValueError('配对胜出次数无效')
        for field in ('control_seconds', 'candidate_seconds', 'speedup', 'paired_reduction_pct'):
            value = float(r[field])
            if not math.isfinite(value) or (field != 'paired_reduction_pct' and value <= 0):
                raise ValueError('无效比较指标：'+field)
    if plan.get('joint_revision') in ('credit_operations_v2','subtree_demand_v3','matched_tasks_v4'):
        review_joint_records(root,job,plan)
    return audit


def review_joint_records(root,job,plan):
    folder=root/job['group']/('levels'+str(job['levels']))
    records=[];coverage=[]
    for scheduler in plan['schedulers']:
        paths=(folder/f'kernel_{scheduler}_t4'/f"p{job['ranks']}").rglob('analysis_metrics.json')
        rows=[json.loads(p.read_text())['result'] for p in paths]
        expected={(seed,i) for seed in plan['seeds'] for i in range(job['repeats']+1)}
        if len(rows)!=len(expected) or {(r['partition_seed'],r['repeat']) for r in rows}!=expected:
            raise ValueError('精简结果覆盖不完整：'+scheduler)
        if any(r.get('post_work_profile')!='rank_counts_v1' or not r.get('scaling_work_sha256') for r in rows):
            raise ValueError('缺少后续工作量指纹：'+scheduler)
        if plan.get('joint_revision') in ('subtree_demand_v3','matched_tasks_v4'):
            if any(r.get('surface_levels')!=job['levels'] or r.get('volume_refines')!=plan['refines'] for r in rows):
                raise ValueError('实际面/体细化级数与本批计划不一致：'+scheduler)
            required=[]
            if scheduler.startswith('node_search_'):
                required+=['search_subtree_calls','search_subtree_tasks','search_subtree_replay_face_trials','search_subtree_replay_point_trials']
            if scheduler in ('node_search_idle','node_search_remote','node_search_full'):
                required+=['search_remote_demand_sent','search_remote_demand_received','search_remote_event_waits']
            if scheduler in ('node_search_online','node_search_full'):
                required+=['coop_operation_explores','coop_operation_rollbacks','coop_operation_unknown_types']
            if any(k not in r for r in rows for k in required):
                raise ValueError('缺少本版算法诊断，可能运行旧程序：'+scheduler)
            if any(r.get('post_refine_elements_sum',0)<=0 for r in rows):
                raise ValueError('缺少体细化后的实际网格计数：'+scheduler)
        if plan.get('joint_revision')=='matched_tasks_v4' and scheduler.startswith('node_search_'):
            if any(r.get('search_revision')!='matched_tasks_v4' for r in rows):
                raise ValueError('缺少本轮版本标识，不能误用旧可执行文件：'+scheduler)
        records.extend(rows)
        formal=[r for r in rows if r['repeat']>0]
        coverage.append(dict(scheduler=scheduler,formal=len(formal),
            generated_elements=formal[0].get('volume_kernel_elements_sum',0),
            refined_elements=formal[0].get('post_refine_elements_sum',0),
            subtree_replay_trials=sum(r.get('search_subtree_replay_face_trials',0)+r.get('search_subtree_replay_point_trials',0) for r in formal),
            remote_calls=sum(r.get('search_policy_remote_calls',0) for r in formal),
            ready_received=sum(r.get('search_remote_ready_received',0) for r in formal),
            subtree_calls=sum(r.get('search_subtree_calls',0) for r in formal),
            subtree_tasks=sum(r.get('search_subtree_tasks',0) for r in formal),
            demand_sent=sum(r.get('search_remote_demand_sent',0) for r in formal),
            event_waits=sum(r.get('search_remote_event_waits',0) for r in formal),
            explores=sum(r.get('coop_operation_explores',0) for r in formal),
            rollbacks=sum(r.get('coop_operation_rollbacks',0) for r in formal),
            cost_rejects=sum(r.get('search_remote_cost_rejects',0) for r in formal),
            critical_rejects=sum(r.get('search_remote_critical_rejects',0) for r in formal),
            control_max_seconds=max(r.get('search_remote_control_max_seconds',0) for r in formal)))
    for seed in plan['seeds']:
        for key in ('scaling_kernel_work_sha256','scaling_work_sha256','kernel_rules_face_trials',
                    'kernel_rules_point_trials','kernel_rules_mappings'):
            if len({r[key] for r in records if r['partition_seed']==seed})!=1:
                raise ValueError('同种子生成/后续工作量变化：'+key)
    return coverage


def csv_text(rows):
    if not rows:
        return ''
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def summarize(root, plan):
    jobs, comparisons, coverage = [], [], []
    for job in plan['jobs']:
        record = dict(job, status='未提交（预览）' if plan['dry_run'] else '待运行', detail='')
        state_path = root/job['key']/'rule_search_job_state.json'
        try:
            if state_path.exists():
                state = json.loads(state_path.read_text())
                if state['state'] == 'running':
                    record['status'] = '运行中'
                elif state['exit_code'] != 0:
                    record.update(status='失败', detail=f"退出码 {state['exit_code']}；查看该作业日志和逐规模汇总")
                else:
                    audit = audit_job(root, job, plan)
                    record['status'] = '通过'
                    if plan.get('joint_revision') in ('credit_operations_v2','subtree_demand_v3','matched_tasks_v4'):
                        for c in review_joint_records(root,job,plan):
                            coverage.append(dict(group=job['group'],levels=job['levels'],nodes=job['nodes'],**c))
                    for row in audit:
                        comparisons.append(dict(group=job['group'], levels=job['levels'], nodes=job['nodes'],
                                                ranks=job['ranks'], **row))
        except (OSError, ValueError, KeyError, TypeError) as error:
            record.update(status='失败', detail=str(error))
        jobs.append(record)
    passed = sum(j['status'] == '通过' for j in jobs)
    failed = sum(j['status'] == '失败' for j in jobs)
    pending = len(jobs)-passed-failed
    status = '全部通过' if passed == len(jobs) else '存在失败' if failed else '预览，未提交作业' if plan['dry_run'] else '尚未全部完成'
    lines = ['规则搜索整批实验汇总', f'状态：{status}；通过 {passed}/{len(jobs)}，失败 {failed}，未完成 {pending}。',
             '各规模独立提交，共享最早启动时刻；每个作业先验证自身预热，再进行正式计时。',
             f"共同最早启动时刻：{plan['start_epoch']}；每节点4进程×4物理核；体细化 {plan['refines']} 次。",
             '通过仅表示流程与正确性审计完成，不表示性能改善或跨节点组件已覆盖。',
             '预检的一次正式重复只用于流程检查，不作为稳定性能结论。', '',
             '用途 面细化 节点数 进程数 正式重复 状态 结果目录']
    for j in jobs:
        lines.append(f"{'预检' if j['group']=='precheck' else '正式对照'} L{j['levels']} {j['nodes']} {j['ranks']} "
                     f"{j['repeats']} {j['status']} {j['key']}/RESULT_SUMMARY.txt")
        if j['detail']:
            lines.append('  '+j['detail'])
    for row in comparisons:
        if row['group'] == 'main':
            lines.append(f"L{row['levels']}/{row['nodes']}节点/种子{row['seed']} {LABELS[row['contribution']]}："
                         f"加速比 {float(row['speedup']):.4f}；配对耗时下降中位数 {float(row['paired_reduction_pct']):.2f}%；"
                         f"胜出 {row['paired_wins']}/{row['paired_count']}。")
    for c in coverage:
        if c['scheduler']=='node_rules_elastic' and plan.get('joint_revision') in ('subtree_demand_v3','matched_tasks_v4'):
            lines.append(f"L{c['levels']}/{c['nodes']}节点，首个正式基准：生成后体单元 {c['generated_elements']:.0f}，体细化后 {c['refined_elements']:.0f}。")
        if c['scheduler'] in ('node_search_remote','node_search_full'):
            lines.append(f"L{c['levels']}/{c['nodes']}节点 {c['scheduler']}：正式远程任务 {c['remote_calls']}；"
                         f"最大单次控制 {c['control_max_seconds']:.6f}s；子树调用 {c['subtree_calls']}；额外前缀试探 {c['subtree_replay_trials']}；"+
                         ('已发生外派，收益另看配对对照。' if c['remote_calls'] else '未覆盖远程执行，不能声称跨节点收益。'))
    if plan.get('joint_revision') in ('credit_operations_v2','subtree_demand_v3','matched_tasks_v4'): atomic(root/'batch_coverage.csv',csv_text(coverage))
    lines += ['', '详细对照：batch_comparisons.csv；作业状态：batch_jobs.csv。',
              '失败数据保留；负收益不筛除；内部计时存在嵌套，不相加推算核心加速。',
              '硬终止可能无法执行完成回调；未收到完成记录的作业保持未完成，请查看调度日志。']
    atomic(root/'batch_jobs.csv', csv_text(jobs))
    atomic(root/'batch_comparisons.csv', csv_text(comparisons))
    atomic(root/'RESULT_SUMMARY.txt', '\n'.join(lines)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--mark')
    parser.add_argument('--running', action='store_true')
    parser.add_argument('--exit-code', type=int)
    args = parser.parse_args()
    if args.initialize and args.mark or (args.running or args.exit_code is not None) and not args.mark:
        parser.error('初始化、状态更新参数组合无效')
    if args.mark and (args.running == (args.exit_code is not None)):
        parser.error('状态更新需选择 --running 或 --exit-code')
    args.root.mkdir(parents=True, exist_ok=True)
    if args.initialize:
        with summary_lock(args.root):
            plan = initialize(args.root)
            summarize(args.root, plan)
    else:
        plan = json.loads((args.root/'rule_search_batch_plan.json').read_text())
        if args.mark:
            if args.mark not in {j['key'] for j in plan['jobs']}:
                raise ValueError('作业不在整批计划中')
            folder = args.root/args.mark
            folder.mkdir(parents=True, exist_ok=True)
            state = dict(state='running' if args.running else 'finished', exit_code=args.exit_code, updated_epoch=time.time())
            atomic(folder/'rule_search_job_state.json', json.dumps(state, ensure_ascii=False)+'\n')
        # 不同作业拥有不同状态文件；先保存真实退出码，汇总锁失败也不丢状态。
        with summary_lock(args.root):
            summarize(args.root, plan)
    if args.initialize:
        print(f"本轮计划：{len(plan['jobs'])} 个独立作业；{len(plan['schedulers'])} 策略、预热验证、正式计时和结果清理均已配置。")


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit('整批实验汇总失败：'+str(error))
