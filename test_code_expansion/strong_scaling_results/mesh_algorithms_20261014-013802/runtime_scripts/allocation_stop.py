#!/usr/bin/env python3
"""Decide whether to stop reusing a failed allocation; not a root-cause diagnosis."""
import argparse
import json
import os
from pathlib import Path
import re
import time

# Match concrete launcher/PMIx failures, not generic MPI/application errors.
PATTERNS = (
    ('pmix_fence_failure', re.compile(r'PMIx_Fence returned -\d+')),
    ('pmix_collective_timeout', re.compile(r'pmixp[^\n]*collective timeout', re.I)),
    ('step_creation_failure', re.compile(r'Unable to create step[^\n]*(?:Duplicate job id|Invalid job id)', re.I)),
    ('step_creation_blocked', re.compile(r'step creation still disabled, retrying \(Requested nodes are busy\)')),
)

def classify(run, exit_code):
    if exit_code == 0:
        return None
    if exit_code in (124, 137):
        # 137 can also mean a kill unrelated to timeout; never infer OOM from it.
        return {'reason': 'run_timeout' if exit_code == 124 else 'run_killed', 'evidence': f'exit_code={exit_code}'}
    for name in ('run.log', 'node_coop_stderr.txt'):
        path = Path(run)/name
        if not path.is_file():
            continue
        # Stream the complete log: the first PMIx failure may precede a long tail.
        with path.open(errors='replace') as stream:
            for line in stream:
                for reason, pattern in PATTERNS:
                    if pattern.search(line):
                        return {'reason': reason, 'evidence_file': str(path), 'evidence': line.strip()[:1024]}
    return None

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--exit-code', type=int, required=True)
    parser.add_argument('--timeout-seconds', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reason = classify(args.run, args.exit_code)
    if reason is None:
        return 1
    record = dict(schema='allocation_stop_v1', time=time.time(),
                  job_id=os.environ.get('SLURM_JOB_ID'), run=str(args.run),
                  exit_code=args.exit_code, timeout_seconds=args.timeout_seconds,
                  action='stop_remaining_runs_in_this_allocation', **reason)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
    print('停止当前规模的后续启动：'+reason['reason'])
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
