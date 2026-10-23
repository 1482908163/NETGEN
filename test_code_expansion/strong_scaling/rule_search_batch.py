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
CONTRIBUTIONS = ('rule_search_fixed', 'rule_search_elastic', 'rule_search_borrowing', 'rule_search_total')
LABELS = dict(zip(CONTRIBUTIONS, ('固定核规则并行', '借核基础上规则并行', '规则并行基础上借核', '相对原程序总收益')))


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
    if tuple(os.environ['KERNEL_SCHEDULERS'].split()) != SCHEDULERS:
        raise ValueError('整批预设要求默认五策略；自定义子集请使用 rule_search 预设')
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
    plan = dict(schema='rule_search_batch_v1', jobs=jobs, schedulers=list(SCHEDULERS),
                seeds=seeds, threads=4, ranks_per_node=4, warmups=1,
                refines=int(os.environ['REFINES']), start_epoch=int(os.environ['START_EPOCH']),
                dry_run=os.environ.get('DRY_RUN') == '1',
                source_revision=os.environ.get('MESH_SOURCE_REVISION', 'manual'),
                input_path=os.environ['INPUT_PATH'], maxh=os.environ['MAXH'], minh=os.environ['MINH'])
    target = root/'rule_search_batch_plan.json'
    if target.exists():
        old = json.loads(target.read_text())
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
                for s in SCHEDULERS for seed in plan['seeds']}
    actual = {(r['scheduler'], r['seed'], r['algorithm']) for r in summary}
    if actual != expected or len(summary) != len(expected):
        raise ValueError('策略、种子或算法覆盖不完整')
    if any(int(r['repeats']) != job['repeats'] or r['timing'] != 'natural' or int(r['threads']) != 4 for r in summary):
        raise ValueError('正式重复数、计时模式或核数与计划不符')
    audit = [r for r in read_csv(folder/'ablation_summary.csv') if r['contribution'] in CONTRIBUTIONS]
    expected = {(c, str(seed)) for c in CONTRIBUTIONS for seed in plan['seeds']}
    if {(r['contribution'], r['seed']) for r in audit} != expected or len(audit) != len(expected):
        raise ValueError('四项贡献对照不完整')
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
    return audit


def csv_text(rows):
    if not rows:
        return ''
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def summarize(root, plan):
    jobs, comparisons = [], []
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
        print(f"本轮计划：{len(plan['jobs'])} 个独立作业；五策略、预热验证、正式计时和结果清理均已配置。")


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit('整批实验汇总失败：'+str(error))
