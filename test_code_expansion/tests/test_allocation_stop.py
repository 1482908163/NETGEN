#!/usr/bin/env python3
"""Exercise real route orchestration after launcher faults, not mesh speed."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'strong_scaling'))
from allocation_stop import classify

# Reuse the existing valid full-rule synthetic binary without running its suite.
fixture = ROOT/'tests/test_front_rule_runner.py'
namespace = {'__file__': str(fixture)}
exec(compile(fixture.read_text().split('with tempfile.TemporaryDirectory() as tmp:')[0], str(fixture), 'exec'), namespace)
script = namespace['script']

with tempfile.TemporaryDirectory() as temporary:
    tmp = Path(temporary)
    logs = tmp/'logs';logs.mkdir()
    for code in (1, 15, 42, 139):
        (logs/'run.log').write_text('MPI application: invalid mesh candidate\n')
        assert classify(logs, code) is None
    for text, reason in (
        ('MPIR_pmi_barrier: PMIx_Fence returned -24', 'pmix_fence_failure'),
        ('pmixp_coll_ring_reset_if_to: collective timeout seq=2', 'pmix_collective_timeout'),
        ('Unable to create step for job 12: Duplicate job id', 'step_creation_failure'),
        ('step creation still disabled, retrying (Requested nodes are busy)', 'step_creation_blocked'),
    ):
        # Preserve early evidence even when the tail no longer contains it.
        (logs/'run.log').write_text(text+'\n'+('ordinary tail\n'*2000))
        assert classify(logs, 15)['reason'] == reason
        assert classify(logs, 0) is None
    (logs/'run.log').unlink()
    (logs/'node_coop_stderr.txt').write_text('PMIx_Fence returned -24\n')
    assert classify(logs, 15)['reason'] == 'pmix_fence_failure'
    assert classify(logs, 124)['reason'] == 'run_timeout'
    assert classify(logs, 137)['reason'] == 'run_killed'

    launcher = tmp/'launcher'
    launcher.write_text('''#!/usr/bin/env bash
set -eu
shift 2
first=0
[[ -e "$LAUNCH_CALLS" ]] || first=1
echo launch >> "$LAUNCH_CALLS"
if ((first)); then
  case "$FAULT" in
    timeout) sleep 5; exit 0 ;;
    pmix) echo 'MPIR_pmi_barrier: PMIx_Fence returned -24' >&2; exit 15 ;;
    blocked) echo 'step creation still disabled, retrying (Requested nodes are busy)' >&2; exit 1 ;;
    duplicate) echo 'Unable to create step for job 12: Duplicate job id' >&2; exit 1 ;;
    killed) exit 137 ;;
    ordinary) echo 'invalid mesh candidate' >&2; exit 42 ;;
  esac
fi
exec "$@"
''');launcher.chmod(0o755)
    binary = tmp/'mesh';binary.write_text(script);binary.chmod(0o755)
    source = tmp/'input.step';source.write_text('input')
    lib = tmp/'lib';lib.mkdir();(lib/'libnglib.so').write_text('library')
    env = dict(os.environ)
    for key in ('EXPERIMENT_STAGE','WORKLET_ROUTES','KERNEL_REPEAT','PARTITION_SEEDS','ALGORITHMS','TIMING_MODES',
                'STRONG_SCALING_DIR','STRONG_SCALING_PROJECT_DIR','MESH_RUNTIME_MANIFEST','MESH_RUNTIME_SHA256',
                'REPAIR_FIXED_POINT','SPLIT_ACTIVE_EVALUATION','FRONT_DISTANCE','FRONT_TRANSFORM','FRONT_PROJECTION',
                'FRONT_RULES','FRONT_LOOKUP','LOOKUP_PROTOCOL_REQUIRED','FRONT_WORKSPACE','WORKSPACE_PROTOCOL_REQUIRED','FRONT_COMPONENTS','INCIDENCE_BUILD','MOCK_RULE_FAILURE','MOCK_IGNORED_RULE',
                'MOCK_COST_FAILURE','MOCK_FAIL','RULE_PROTOCOL_REQUIRED'):
        env.pop(key, None)
    env.update(EXPERIMENT_PRESET='front_rules', MESH_EXPERIMENT_WORKER='1', PROCESS_COUNT='2', PROCESS_COUNTS='2',
        RANKS_PER_NODE='2', CPUS_PER_TASK='2', KERNEL_THREADS='2', WARMUPS='1', REPEATS='1', LEVELS='0', REFINES='0',
        LOAD_MODULES='0', CLUSTER_ENV_STRICT='0', NETGEN_INSTALL_LIB=str(lib), MPI_LAUNCHER=str(launcher),
        MPI_EXTRA_ARGS=' ', START_EPOCH='0', BINARY=str(binary), INPUT_PATH=str(source), MOCK_FIXTURES=str(ROOT/'tests'),
        RESUME='0', TIMEOUT_SECONDS='10', ALLOCATION_FAIL_FAST='1')
    command = ['bash', str(ROOT/'strong_scaling/run_experiments.sh')]
    def run(name, fault, **overrides):
        root = tmp/name;parent = root/'p2';parent.mkdir(parents=True)
        # A stopped retry must not retain a stale valid comparison.
        (parent/'route_comparisons.csv').write_text('stale success\n')
        (parent/'rule_cost_stage_summary.csv').write_text('stale success\n')
        calls = tmp/(name+'_launches')
        result = subprocess.run(command, env=dict(env, RUN_ROOT=str(root), MOCK_CALLS=str(tmp/(name+'_mesh_calls')),
             LAUNCH_CALLS=str(calls), FAULT=fault, **overrides), capture_output=True, text=True, timeout=90)
        return result, root, len(calls.read_text().splitlines())
    for fault in ('pmix','blocked','duplicate','killed','timeout'):
        override = {'TIMEOUT_SECONDS':'1'} if fault == 'timeout' else {}
        result, root, count = run(fault, fault, **override)
        assert result.returncode == 75, (fault, result.stdout, result.stderr)
        assert count == 1, (fault, count)
        stopped = json.loads((root/'p2/ALLOCATION_STOPPED.json').read_text())
        assert stopped['action'] == 'stop_remaining_runs_in_this_allocation'
        assert stopped['exit_code'] == {'pmix':15,'blocked':1,'duplicate':1,'killed':137,'timeout':124}[fault]
        assert not (root/'p2/route_comparisons.csv').exists()
        assert not (root/'p2/rule_cost_stage_summary.csv').exists()
        assert not (root/'route_rules_totals').exists()
        assert not (root/'route_rules_stages').exists()
        assert '未完成' in (root/'p2/ROUTE_SUMMARY.txt').read_text()
        first = root/'route_incidence_ordered/p2/sparse_natural/repeat_0'
        assert not (first/'SUCCESS').exists() and not (first/'RUNNING').exists()
        assert (first/'failure_reason.txt').is_file()
        assert (root/'route_incidence_ordered/p2/run_status.tsv').read_text().count('\n') == 2
    # Validation/application failures retain the original continue-on-failure contract.
    for name, fault, overrides in (('ordinary','ordinary',{}), ('optout','pmix',{'ALLOCATION_FAIL_FAST':'0'})):
        result, root, count = run(name, fault, **overrides)
        assert result.returncode == 1, (result.stdout,result.stderr)
        assert count > 1 and not (root/'p2/ALLOCATION_STOPPED.json').exists()
        assert (root/'route_rules_stages/p2/sparse_seed41_natural/repeat_1/SUCCESS').exists()
    # A later independent batch still runs normally after another allocation stops.
    result, root, count = run('healthy','none')
    assert result.returncode == 0, (result.stdout,result.stderr)
    assert count == 18 and (root/'p2/rule_cost_stage_summary.csv').exists()

    for name, overrides in (
        ('kernel', {'EXPERIMENT_PRESET':'kernel','KERNEL_THREAD_COUNTS':'2','KERNEL_SCHEDULERS':'repair'}),
        ('factors', {'EXPERIMENT_PRESET':'worklets','WORKLET_FACTORS':'1 2','WORKLET_ROUTES':'reference'}),
    ):
        result, root, count = run(name, 'pmix', **overrides)
        assert result.returncode == 75 and count == 1, (name,result.stdout,result.stderr)
        assert (root/'p2/ALLOCATION_STOPPED.json').is_file()

    # New lookup preset must stop after one launcher fault and invalidate its reports.
    lookup_fixture=ROOT/'tests/test_front_lookup_runner.py'
    lookup_namespace={'__file__':str(lookup_fixture)}
    exec(compile(lookup_fixture.read_text().split('with tempfile.TemporaryDirectory() as tmp:')[0],str(lookup_fixture),'exec'),lookup_namespace)
    binary.write_text(lookup_namespace['script'])
    result,root,count=run('lookup_pmix','pmix',EXPERIMENT_PRESET='front_lookup')
    assert result.returncode==75 and count==1,(result.stdout,result.stderr)
    assert not (root/'route_lookup_inverse').exists()
    assert '未完成' in (root/'p2/FRONT_LOOKUP_SUMMARY.txt').read_text()

    workspace_fixture=ROOT/'tests/test_front_workspace_runner.py'
    workspace_namespace={'__file__':str(workspace_fixture)}
    exec(compile(workspace_fixture.read_text().split('with tempfile.TemporaryDirectory() as tmp:')[0],str(workspace_fixture),'exec'),workspace_namespace)
    binary.write_text(workspace_namespace['script'])
    result,root,count=run('workspace_pmix','pmix',EXPERIMENT_PRESET='front_workspace')
    assert result.returncode==75 and count==1,(result.stdout,result.stderr)
    assert not (root/'route_workspace_reuse').exists()
    assert '未完成' in (root/'p2/FRONT_WORKSPACE_SUMMARY.txt').read_text()

    # Inspect unified defaults before driver submission; no cluster required.
    runner = ROOT/'strong_scaling/run_experiments.sh'
    prefix = runner.read_text().split('# 登录节点：转入提交驱动；')[0]
    probe = tmp/'config.sh';probe.write_text(prefix+'\nprintf "%s\\n" "$TIMEOUT_SECONDS"\n')
    base = dict(env, STRONG_SCALING_DIR=str(ROOT/'strong_scaling'))
    base.pop('TIMEOUT_SECONDS')
    for preset, explicit, expected in (('front_workspace',None,'600'),('front_workspace','900','900'),('front_lookup',None,'600'),('front_lookup','900','900'),('front_rules',None,'600'),('production',None,'7200'),('front_rules','900','900')):
        config = dict(base, EXPERIMENT_PRESET=preset)
        if explicit is not None:config['TIMEOUT_SECONDS']=explicit
        result = subprocess.run(['bash',str(probe)],env=config,capture_output=True,text=True)
        assert result.returncode == 0 and result.stdout.strip() == expected, (preset,result.stdout,result.stderr)
print('PASS: real timeout, PMIx/blocked/killed stop after one launch; ordinary failures continue; independent batch and defaults preserved')
