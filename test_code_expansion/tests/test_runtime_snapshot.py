#!/usr/bin/env python3
"""Submit frozen scripts, remove live dependencies, then run the spooled job."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'strong_scaling'))
from runtime_snapshot import capture,verify

tree=ast.parse((ROOT/'tests/test_generation_cost_runner.py').read_text())
mock_code=next(call.args[0].value for call in ast.walk(tree) if isinstance(call,ast.Call)
    and isinstance(call.func,ast.Attribute) and call.func.attr=='write_text'
    and call.args and isinstance(call.args[0],ast.Constant)
    and isinstance(call.args[0].value,str) and '# phase_full_cost_v3 cost_' in call.args[0].value)

with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);project=tmp/'repo'/'test_code_expansion';live=project/'strong_scaling'
    live.mkdir(parents=True)
    (project/'mesh_occ_mpi').mkdir()
    for path in (ROOT/'strong_scaling').iterdir():
        if path.is_file() and path.suffix in ('.py','.sh'):shutil.copy2(path,live/path.name)
    for path in live.glob('*.sh'):path.chmod(0o755)
    subprocess.run(['git','init','-q',str(project.parent)],check=True)
    subprocess.run(['git','-C',str(project.parent),'add','test_code_expansion'],check=True)
    subprocess.run(['git','-C',str(project.parent),'-c','user.name=Test','-c','user.email=test@example.invalid',
                    'commit','-qm','测试运行快照'],check=True)
    binary=tmp/'mock_mesh';binary.write_text(mock_code);binary.chmod(0o755)
    input_file=tmp/'input.step';input_file.write_text('mock input')
    lib=tmp/'lib';lib.mkdir();(lib/'libnglib.so').write_text('mock library')
    yhrun=tmp/'real_yhrun'
    yhrun.write_text('#!/bin/bash\nwhile [[ "$1" != "$BINARY" ]]; do shift;done\nexec "$@"\n');yhrun.chmod(0o755)
    batch=tmp/'yhbatch'
    batch.write_text('''#!/usr/bin/env python3
import json,os,sys
keys=('STRONG_SCALING_DIR','STRONG_SCALING_PROJECT_DIR','MESH_SOURCE_REVISION','MESH_EXPERIMENT_WORKER',
      'MESH_RUNTIME_MANIFEST','MESH_RUNTIME_SHA256','MPI_LAUNCHER','PROJECT_ROOT','PROCESS_COUNT','START_EPOCH')
with open(os.environ['MOCK_SUBMISSIONS'],'a') as stream:
    stream.write(json.dumps(dict(script=sys.argv[-1],args=sys.argv[1:],
        env={key:os.environ[key] for key in keys if key in os.environ}))+'\\n')
print('12345')
''');batch.chmod(0o755)
    submissions=tmp/'submissions';calls=tmp/'calls';out=tmp/'results'
    env=dict(os.environ)
    for key in ('STRONG_SCALING_DIR','STRONG_SCALING_PROJECT_DIR','MESH_RUNTIME_MANIFEST','MESH_RUNTIME_SHA256',
                'MESH_EXPERIMENT_WORKER','MESH_EXPERIMENT_DRIVER_READY','MESH_SOURCE_REVISION',
                'PROJECT_ROOT','PROJ_DIR','KERNEL_REPEAT','WORKLET_ROUTES','EXPERIMENT_STAGE'):
        env.pop(key,None)
    env.update(EXPERIMENT_PRESET='generation_cost',PROCESS_COUNTS='2 4 8',RANKS_PER_NODE='2',CPUS_PER_TASK='2',
        KERNEL_THREADS='2',LEVELS='0',REFINES='0',WARMUPS='1',REPEATS='1',RESUME='0',
        SBATCH_COMMAND=str(batch),RUN_ROOT=str(out),START_DELAY_SECONDS='0',
        LOAD_MODULES='0',CLUSTER_ENV_STRICT='0',NETGEN_INSTALL_LIB=str(lib),MPI_EXTRA_ARGS=' ',
        REAL_YHRUN=str(yhrun),BINARY=str(binary),INPUT_PATH=str(input_file),
        MOCK_FIXTURES=str(ROOT/'tests'),MOCK_CALLS=str(calls),MOCK_SUBMISSIONS=str(submissions),
        MOCK_FAIL='0',TIMEOUT_SECONDS='10')
    result=subprocess.run(['bash',str(live/'run_experiments.sh')],env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,(result.stdout,result.stderr)
    jobs=[json.loads(line) for line in submissions.read_text().splitlines()]
    assert len(jobs)==3 and {job['env']['PROCESS_COUNT'] for job in jobs}=={'2','4','8'}
    assert len({job['env']['START_EPOCH'] for job in jobs})==1
    frozen=out/'runtime_scripts'
    assert all(job['script']==str(frozen/'run_experiments.sh') for job in jobs)
    assert all(job['env']['MPI_LAUNCHER']==str(frozen/'node_affinity_yhrun.sh') for job in jobs)
    assert all(job['env']['STRONG_SCALING_PROJECT_DIR']==str(project) for job in jobs)
    (live/'node_affinity_yhrun.sh').unlink();(live/'cost_profile_checks.py').unlink()
    job=jobs[0];spool=tmp/'slurm_script';shutil.copyfile(job['script'],spool)
    worker={**env,**job['env'],'MESH_EXPERIMENT_WORKER':'1'}
    result=subprocess.run(['bash',str(spool)],env=worker,capture_output=True,text=True,timeout=180)
    assert result.returncode==0,(result.stdout,result.stderr)
    assert len(calls.read_text().splitlines())==12
    assert (out/'route_cost_profile/p2/sparse_natural/repeat_1/operation_cost_profile.csv').exists()
    assert (out/'p2/route_comparisons.csv').exists()
    verify(frozen,out/'runtime_manifest.json',job['env']['MESH_RUNTIME_SHA256'])
    # An incomplete live runtime is rejected before allocating another batch.
    try:capture(live,tmp/'bad_runtime',tmp/'bad_manifest.json',project,'test')
    except ValueError as error:assert 'before submission' in str(error)
    else:raise AssertionError('missing live dependencies were submitted')
    changed=frozen/'analyze_results.py';changed.chmod(0o644);changed.write_text(changed.read_text()+'\\n# tampered\\n')
    result=subprocess.run(['bash',str(spool)],env=worker,capture_output=True,text=True,timeout=30)
    assert result.returncode!=0 and 'missing or changed: analyze_results.py' in result.stderr
    assert len(calls.read_text().splitlines())==12
print('PASS: concurrent submission, common epoch, spooled frozen jobs, missing live files and tamper refusal')
