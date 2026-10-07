#!/usr/bin/env python3
"""Read the production preset without scheduling jobs."""
import os
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]
prefix=(ROOT/'strong_scaling/run_experiments.sh').read_text().split('# 登录节点：',1)[0]
env=dict(os.environ)
for key in ('EXPERIMENT_STAGE','PROCESS_COUNTS','PARTITION_SEEDS','REPEATS','WORKLET_ROUTES','KERNEL_THREADS',
            'CPUS_PER_TASK','RANKS_PER_NODE','ALGORITHMS','TIMING_MODES','QUALITY_WARMUP','BALANCE_METHOD','KERNEL_SCHEDULER'):
    env.pop(key,None)
env.update(EXPERIMENT_PRESET='generation_cost',STRONG_SCALING_DIR=str(ROOT/'strong_scaling'),
           LOAD_MODULES='0',CLUSTER_ENV_STRICT='0')
probe='\nprintf "%s\\n" "$PROCESS_COUNTS" "$PARTITION_SEEDS" "$WORKLET_ROUTES" "$REPEATS" "$TIMING_MODES" "$RANKS_PER_NODE" "$CPUS_PER_TASK" "$QUALITY_WARMUP"\n'
result=subprocess.run(['bash'],input=prefix+probe,env=env,text=True,capture_output=True,check=True)
assert result.stdout.splitlines()[-8:]==['128 256 512','-1 17 41','batch_parallel cost_profile','3','natural','4','4','1'],result.stdout
print('PASS: production full-cost preset, 3 scales/seeds, 2 routes, natural timing and quality warmup')
