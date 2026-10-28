#!/usr/bin/env python3
"""共享预处理微基准：交替顺序、丢弃预热、逐对检查结果指纹。

先用 run_rule_search_regression.py 生成 rule_search 可执行文件。
测量完整 ApplyRulesTasks 累计耗时，不代表完整网格或集群性能。
"""
import argparse
import json
from pathlib import Path
import re
import statistics
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--pairs', type=int, default=5)
    args = parser.parse_args()
    if args.pairs < 1 or args.threads < 2:
        parser.error('pairs >= 1 and threads >= 2 required')
    binary = args.binary.resolve()
    cases = []
    for variant in (0, 1, 2):
        samples = []
        fingerprints = set()
        for repeat in range(args.pairs + 1):
            pair = {}
            for shared in ((0, 1) if repeat % 2 == 0 else (1, 0)):
                out = subprocess.check_output(
                    [str(binary), str(args.threads), '1', '0', str(shared), str(variant)],
                    text=True, cwd=binary.parent, timeout=120)
                fingerprints.add(re.search(r'fingerprint=(\w+)', out)[1])
                pair[str(shared)] = {
                    key: float(re.search(key + r'=([\d.eE+\-]+)', out)[1])
                    for key in ('rule_wall_seconds', 'build_seconds', 'shared_calls')}
            if repeat:
                samples.append(pair)
        if len(fingerprints) != 1:
            raise RuntimeError('shared preparation changed the result fingerprint')
        item = dict(variant=variant, threads=args.threads,
                    fingerprint=next(iter(fingerprints)), samples=samples,
                    baseline_median_seconds=statistics.median(p['0']['rule_wall_seconds'] for p in samples),
                    candidate_median_seconds=statistics.median(p['1']['rule_wall_seconds'] for p in samples),
                    paired_reduction_median_pct=statistics.median(
                        100 * (1 - p['1']['rule_wall_seconds'] / p['0']['rule_wall_seconds']) for p in samples),
                    wins=sum(p['1']['rule_wall_seconds'] < p['0']['rule_wall_seconds'] for p in samples))
        cases.append(item)
        print(json.dumps(item), flush=True)
    args.output.write_text(json.dumps(dict(
        method='separate processes; alternating order; one discarded warmup pair; full ApplyRulesTasks wall time; formal mode',
        cases=cases), indent=2) + '\n')


if __name__ == '__main__':
    main()
