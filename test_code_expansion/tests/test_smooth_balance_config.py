#!/usr/bin/env python3
"""Read the real runner configuration without submitting any job."""
import os
from pathlib import Path
import subprocess

root=Path(__file__).resolve().parents[1]
script=(root/'strong_scaling/run_experiments.sh').read_text()
prefix=script.split('# 登录节点：',1)[0]
env=dict(os.environ)
for key in ('EXPERIMENT_STAGE','PROCESS_COUNTS','PARTITION_SEEDS','REPEATS','WORKLET_ROUTES',
            'KERNEL_THREADS','CPUS_PER_TASK','RANKS_PER_NODE','ALGORITHMS','TIMING_MODES',
            'QUALITY_WARMUP','BALANCE_METHOD','KERNEL_SCHEDULER','PLACEMENT_ROTATION'):
    env.pop(key,None)
env.update(EXPERIMENT_PRESET='smooth_balance',STRONG_SCALING_DIR=str(root/'strong_scaling'),
           LOAD_MODULES='0',CLUSTER_ENV_STRICT='0')
probe='\nprintf "%s\\n" "$PROCESS_COUNTS" "$PARTITION_SEEDS" "$WORKLET_ROUTES" "$REPEATS" "$RANKS_PER_NODE" "$CPUS_PER_TASK" "$QUALITY_WARMUP"\n'
r=subprocess.run(['bash'],input=prefix+probe,env=env,text=True,capture_output=True,check=True)
assert r.stdout.splitlines()[-7:]==['128 256 512','-1 17 41','batch_parallel smooth_profile smooth_balanced','8','4','4','1'],r.stdout
print('PASS: real scale preset selects three scales, three seeds, three routes and strict warmup')
