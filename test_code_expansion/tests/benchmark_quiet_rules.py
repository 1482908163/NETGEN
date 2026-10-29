#!/usr/bin/env python3
"""固定四核、交错配对；保存预热及全部样本。仅局部合成调用，不代表完整核心。

先运行 run_rule_search_regression.py 构建测试程序；本工具不提交集群作业。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pairs',type=int,default=10)
    parser.add_argument('--cpus',help='四个可用核心，逗号分隔；默认允许的前四个')
    args=parser.parse_args()
    available=sorted(os.sched_getaffinity(0))
    cpus=[int(x) for x in args.cpus.split(',')] if args.cpus else available[:4]
    if len(set(cpus))!=4 or not set(cpus)<=set(available) or args.pairs<1:
        parser.error('需要四个不同的可用核心及正数配对次数')
    binary=args.binary.resolve()
    def cgroup():
        return {k:(Path('/sys/fs/cgroup')/k).read_text() for k in ('cpu.max','cpu.stat')
                if (Path('/sys/fs/cgroup')/k).exists()}
    result=dict(schema='quiet_rules_microbenchmark_v1',cpus=cpus,allowed_cpus=available,
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),pairs=args.pairs,
        cgroup_before=cgroup(),cases=[],scope='synthetic local rule calls, not application core',
        sample_columns=['repeat','warmup','first_enabled','control_wall','candidate_wall','control_high','candidate_high'])
    for limit in (7,45):
        for variant in (0,1,2):
            samples=[];invariants={}
            for repeat in range(args.pairs+1):
                order=(0,1) if repeat%2==0 else (1,0)
                pair={}
                for enabled in order:
                    command=['taskset','-c',','.join(map(str,cpus)),str(binary),
                             '4','1','0','0',str(variant),'1',str(limit),str(enabled)]
                    run=subprocess.run(command,text=True,capture_output=True,timeout=120,check=True)
                    values=dict(re.findall(r'(\w+)=([\w.+-]+)',run.stdout))
                    stable={k:v for k,v in values.items() if not k.endswith('_seconds')}
                    if enabled in invariants and invariants[enabled]!=stable:
                        raise RuntimeError('重复内结果或工作量改变')
                    invariants[enabled]=stable;pair[enabled]=values
                if pair[0]['fingerprint']!=pair[1]['fingerprint']:
                    raise RuntimeError('候选改变结果指纹')
                samples.append([repeat,repeat==0,order[0],
                    *[float(pair[k]['rule_wall_seconds']) for k in (0,1)],
                    *[float(pair[k]['high_seconds']) for k in (0,1)]])
            summary={}
            for metric,index in [('rule_wall_seconds',3),('high_seconds',5)]:
                before=[p[index] for p in samples[1:]];after=[p[index+1] for p in samples[1:]]
                summary[metric]=dict(control_median=statistics.median(before),candidate_median=statistics.median(after),
                    paired_reduction_percent=statistics.median(100*(1-c/b) for b,c in zip(before,after)),
                    wins=sum(c<b for b,c in zip(before,after)),pairs=len(before))
            entry=dict(tolerance_limit=limit,variant=variant,samples=samples,invariants=invariants,summary=summary)
            result['cases'].append(entry);result['cgroup_after']=cgroup()
            args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps({k:v for k,v in entry.items() if k in ('tolerance_limit','variant','summary')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
