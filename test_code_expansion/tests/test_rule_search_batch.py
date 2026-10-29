#!/usr/bin/env python3
"""用假提交器验证整批编排，不申请节点，不运行 MPI。"""
import csv
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT/'test_code_expansion/strong_scaling/run_experiments.sh'
HELPER = DRIVER.with_name('rule_search_batch.py')
SCHEDULERS = 'node_original node_fixed node_elastic node_rules_fixed node_rules_elastic'.split()
CONTRIBUTIONS = 'rule_search_fixed rule_search_elastic rule_search_borrowing rule_search_total'.split()
spec = importlib.util.spec_from_file_location('rule_search_batch', HELPER)
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='netgen-batch-test-')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.capture = self.folder/'calls.jsonl'
        fake = self.folder/'fake_submit'
        fake.write_text('''#!/usr/bin/env python3
import json,os,sys
keys='START_EPOCH RUN_ROOT RULE_SEARCH_BATCH_GROUP RULE_SEARCH_BATCH_ROOT LEVELS REFINES REPEATS WARMUPS KERNEL_SCHEDULERS CLEANUP_RESULTS COMPACT_RESULTS MESH_EXPERIMENT_WORKER RULE_SEARCH_ALL RULE_SEARCH_SWEEP'.split()
with open(os.environ['BATCH_CAPTURE'],'a') as f:f.write(json.dumps({'args':sys.argv[1:],'env':{k:os.environ.get(k) for k in keys}})+'\\n')
if os.environ.get('FAIL_CHECK')=='1' and os.environ['RULE_SEARCH_BATCH_GROUP']=='precheck':sys.exit(19)
print('12345')
''')
        fake.chmod(0o755)
        self.env = dict(os.environ, EXPERIMENT_PRESET='rule_search_all',
                        RUN_ROOT=str(self.folder/'results'), SBATCH_COMMAND=str(fake),
                        BATCH_CAPTURE=str(self.capture), DRY_RUN='0', MESH_EXPERIMENT_WORKER='0',
                        START_DELAY_SECONDS='120', MESH_SOURCE_REVISION='test-only')
        for k in ('START_EPOCH','RULE_SEARCH_BATCH_ROOT','RULE_SEARCH_BATCH_GROUP','BASH_ENV','SEARCH_JOINT_EXPERIMENT'):
            self.env.pop(k, None)

    def launch(self, **updates):
        return subprocess.run(['bash', str(DRIVER)], env=dict(self.env, **updates),
                              cwd=ROOT, capture_output=True, text=True)

    def calls(self):
        return [json.loads(s) for s in self.capture.read_text().splitlines()]

    def test_all_five_jobs_share_start_and_cleaning(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 5)
        self.assertEqual(len({r['env']['START_EPOCH'] for r in calls}), 1)
        actual = []
        for r in calls:
            e, a = r['env'], r['args']
            nodes = int(a[a.index('-N')+1]);ranks = int(a[a.index('-n')+1])
            actual.append((e['RULE_SEARCH_BATCH_GROUP'], int(e['LEVELS']), nodes, int(e['REPEATS'])))
            self.assertEqual(ranks, nodes*4)
            self.assertEqual(e['REFINES'], '2')
            self.assertIn('--exclusive', a)
            self.assertFalse(any(s.startswith('--dependency') for s in a))
            self.assertEqual(e['KERNEL_SCHEDULERS'].split(), SCHEDULERS)
            for key in ('CLEANUP_RESULTS','COMPACT_RESULTS','WARMUPS','MESH_EXPERIMENT_WORKER'):
                self.assertEqual(e[key], '1')
            self.assertEqual(e['RULE_SEARCH_ALL'], '0')
            self.assertEqual(e['RULE_SEARCH_SWEEP'], '0')
        self.assertEqual(actual, [('precheck',2,64,1),('main',2,64,5),('main',2,256,5),
                                  ('main',4,64,5),('main',4,256,5)])
        self.assertIn('待运行', (self.folder/'results/RESULT_SUMMARY.txt').read_text())

    def test_failed_submission_does_not_stop_other_scales(self):
        result = self.launch(FAIL_CHECK='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 5)
        state = json.loads((self.folder/'results/precheck/levels2/p256/rule_search_job_state.json').read_text())
        self.assertEqual(state['exit_code'], 125)
        self.assertIn('存在失败', (self.folder/'results/RESULT_SUMMARY.txt').read_text())

    def test_diagnostic_followup_only_submits_level4(self):
        schedulers=' '.join(batch.FOCUSED_SCHEDULERS)
        result=self.launch(EXPERIMENT_PRESET='rule_search',RULE_SEARCH_LEVELS='4',
                           PROCESS_COUNTS='256',REPEATS='3',KERNEL_SCHEDULERS=schedulers)
        self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls();self.assertEqual(len(calls),1)
        e,a=calls[0]['env'],calls[0]['args']
        self.assertEqual((e['LEVELS'],e['REFINES'],e['REPEATS'],e['WARMUPS']),('4','2','3','1'))
        self.assertEqual(e['KERNEL_SCHEDULERS'],schedulers)
        self.assertEqual(a[a.index('-N')+1],'64')
        self.assertEqual(a[a.index('-n')+1],'256')

    def test_recovery_scaling_default_submits_three_node_counts(self):
        self.env.pop('EXPERIMENT_PRESET',None)
        for k in ('PROCESS_COUNTS','RULE_SEARCH_LEVELS','KERNEL_SCHEDULERS','REPEATS','REFINES'):
            self.env.pop(k,None)
        result=self.launch(EXPERIMENT_PRESET='recovery_scaling');self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls();self.assertEqual(len(calls),3)
        self.assertEqual(len({r['env']['START_EPOCH'] for r in calls}),1)
        layouts=[]
        for r in calls:
            e,a=r['env'],r['args'];layouts.append((int(a[a.index('-N')+1]),int(a[a.index('-n')+1])))
            self.assertEqual(e['KERNEL_SCHEDULERS'].split(),
                             ['node_rules_elastic','node_rules_recovery','node_rules_recovery_online'])
            self.assertEqual((e['LEVELS'],e['REFINES'],e['REPEATS'],e['WARMUPS']),('4','2','3','1'))
            self.assertFalse(any(s.startswith('--dependency') for s in a))
        self.assertEqual(layouts,[(64,256),(128,512),(256,1024)])

    def test_recovery_scaling_allows_explicit_smaller_matrix(self):
        result=self.launch(EXPERIMENT_PRESET='recovery_scaling',PROCESS_COUNTS='256 512',
                           RULE_SEARCH_LEVELS='2',REPEATS='2')
        self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls();self.assertEqual(len(calls),2)
        self.assertTrue(all(r['env']['LEVELS']=='2' and r['env']['REPEATS']=='2' for r in calls))

    def test_topology_recovery_explicit_uses_same_batch_control(self):
        self.env.pop('EXPERIMENT_PRESET',None)
        for k in ('PROCESS_COUNTS','RULE_SEARCH_LEVELS','KERNEL_SCHEDULERS','REPEATS','REFINES'):
            self.env.pop(k,None)
        result=self.launch(EXPERIMENT_PRESET='topology_recovery');self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls();self.assertEqual(len(calls),3)
        self.assertEqual(len({r['env']['START_EPOCH'] for r in calls}),1)
        for r in calls:
            e=r['env']
            self.assertEqual(e['KERNEL_SCHEDULERS'].split(),['node_rules_recovery_front','node_rules_recovery_topology'])
            self.assertEqual((e['LEVELS'],e['REFINES'],e['REPEATS'],e['WARMUPS']),('4','2','3','1'))
            self.assertFalse(any(a.startswith('--dependency') for a in r['args']))
        self.assertEqual([r['args'][r['args'].index('-N')+1] for r in calls],['64','128','256'])

    def test_quiet_scaling_default_skips_completed_precheck(self):
        self.env.pop('EXPERIMENT_PRESET',None)
        for k in ('PROCESS_COUNTS','RULE_SEARCH_LEVELS','KERNEL_SCHEDULERS','REPEATS','REFINES'):
            self.env.pop(k,None)
        result=self.launch();self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls();self.assertEqual(len(calls),2)
        self.assertEqual(len({r['env']['START_EPOCH'] for r in calls}),1)
        layouts=[]
        for r in calls:
            e,a=r['env'],r['args']
            layouts.append((int(a[a.index('-N')+1]),int(a[a.index('-n')+1])))
            self.assertEqual(e['KERNEL_SCHEDULERS'].split(),
                ['node_rules_recovery_topology','node_rules_recovery_quiet'])
            self.assertEqual((e['LEVELS'],e['REFINES'],e['REPEATS'],e['WARMUPS']),('4','2','3','1'))
            self.assertFalse(any(x.startswith('--dependency') for x in a))
        self.assertEqual(layouts,[(128,512),(256,1024)])

    def test_quiet_rules_is_optional_and_limited_to_one_scale(self):
        for k in ('PROCESS_COUNTS','KERNEL_SCHEDULERS','REPEATS','REFINES','RULE_SEARCH_LEVELS'):
            self.env.pop(k,None)
        result=self.launch(EXPERIMENT_PRESET='quiet_rules')
        self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls();self.assertEqual(len(calls),1)
        e=calls[0]['env']
        self.assertEqual(e['KERNEL_SCHEDULERS'].split(),['node_rules_recovery_topology','node_rules_recovery_quiet'])
        self.assertEqual((e['LEVELS'],e['REFINES'],e['REPEATS'],e['WARMUPS']),('4','2','3','1'))
        args=calls[0]['args'];self.assertEqual(args[args.index('-N')+1],'64')

    def test_shared_rules_is_optional_and_limited_to_one_scale(self):
        for k in ('PROCESS_COUNTS','KERNEL_SCHEDULERS','REPEATS','REFINES','RULE_SEARCH_LEVELS'):
            self.env.pop(k,None)
        result=self.launch(EXPERIMENT_PRESET='shared_rules')
        self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls();self.assertEqual(len(calls),1)
        e=calls[0]['env']
        self.assertEqual(e['KERNEL_SCHEDULERS'].split(),['node_rules_recovery_topology','node_rules_recovery_shared'])
        self.assertEqual((e['LEVELS'],e['REFINES'],e['REPEATS'],e['WARMUPS']),('4','2','3','1'))
        args=calls[0]['args'];self.assertEqual(args[args.index('-N')+1],'64')

    def test_explicit_front_preset_remains_available(self):
        result=self.launch(EXPERIMENT_PRESET='front_recovery')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(all(r['env']['KERNEL_SCHEDULERS'].split()==
            ['node_rules_recovery','node_rules_recovery_front'] for r in self.calls()))

    def test_joint_plan_has_all_thirteen_strategies_and_contributions(self):
        result=self.launch(EXPERIMENT_PRESET='search_joint_all',SEARCH_FOCUSED='0')
        self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls();self.assertEqual(len(calls),5)
        self.assertEqual(len({r['env']['START_EPOCH'] for r in calls}),1)
        for r in calls:
            self.assertEqual(tuple(r['env']['KERNEL_SCHEDULERS'].split()),batch.JOINT_SCHEDULERS)
        plan=json.loads((self.folder/'results/rule_search_batch_plan.json').read_text())
        self.assertEqual(tuple(plan['contributions']),batch.JOINT_CONTRIBUTIONS)
        self.assertEqual(plan['refines'],2)
        self.assertEqual(plan['joint_revision'],'generation_tail_v5')

    def test_focused_default_keeps_64_nodes_and_three_independent_jobs(self):
        result=self.launch(EXPERIMENT_PRESET='search_joint_all',SEARCH_FOCUSED='1')
        self.assertEqual(result.returncode,0,result.stderr)
        plan=json.loads((self.folder/'results/rule_search_batch_plan.json').read_text())
        self.assertEqual(tuple(plan['schedulers']),batch.FOCUSED_SCHEDULERS)
        self.assertEqual(len(plan['jobs']),3)
        self.assertTrue(all(j['nodes']==64 for j in plan['jobs']))
        self.assertEqual(plan['refines'],2)
        self.assertEqual({j['levels'] for j in plan['jobs']},{2,4})
        self.assertEqual(sum((j['repeats']+1)*len(plan['schedulers']) for j in plan['jobs']),60)
        self.assertEqual(len({c['env']['START_EPOCH'] for c in self.calls()}),1)

    def test_joint_work_evidence_missing_or_changed_is_rejected(self):
        plan=dict(schedulers=['node_search_local','node_search_full'],seeds=[-1],joint_revision='credit_operations_v2')
        job=dict(group='main',levels=2,ranks=256,repeats=1)
        root=self.folder/'review'
        for scheduler in plan['schedulers']:
            for rep in (0,1):
                p=root/'main/levels2'/f'kernel_{scheduler}_t4'/'p256'/f'repeat_{rep}'/'analysis_metrics.json'
                p.parent.mkdir(parents=True,exist_ok=True)
                r=dict(partition_seed=-1,repeat=rep,post_work_profile='rank_counts_v1',
                       scaling_work_sha256='same-post',scaling_kernel_work_sha256='same-kernel',
                       kernel_rules_face_trials=10,kernel_rules_point_trials=12,kernel_rules_mappings=4)
                p.write_text(json.dumps(dict(result=r)))
        coverage=batch.review_joint_records(root,job,plan)
        self.assertEqual(len(coverage),2)
        self.assertEqual(coverage[1]['remote_calls'],0)  # Uncovered, not a correctness failure.
        large_plan=dict(plan,joint_revision='subtree_demand_v3',refines=2)
        with self.assertRaises(ValueError):batch.review_joint_records(root,job,large_plan)
        for path in root.rglob('analysis_metrics.json'):
            data=json.loads(path.read_text());data['result'].update(surface_levels=2,volume_refines=2,post_refine_elements_sum=640)
            data['result'].update(dict.fromkeys(('search_subtree_calls','search_subtree_tasks','search_subtree_replay_face_trials','search_subtree_replay_point_trials','search_remote_demand_sent','search_remote_demand_received','search_remote_event_waits','coop_operation_explores','coop_operation_rollbacks','coop_operation_unknown_types'),0))
            path.write_text(json.dumps(data))
        batch.review_joint_records(root,job,large_plan)
        with self.assertRaises(ValueError):batch.review_joint_records(root,job,dict(large_plan,refines=3))
        path=root/'main/levels2/kernel_node_search_full_t4/p256/repeat_1/analysis_metrics.json'
        data=json.loads(path.read_text());data['result']['scaling_work_sha256']='different'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):batch.review_joint_records(root,job,plan)
        data['result'].pop('scaling_work_sha256');path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):batch.review_joint_records(root,job,plan)

    def test_invalid_configuration_submits_nothing(self):
        result = self.launch(RULE_SEARCH_MAIN_NODES='0')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.capture.exists())

    def test_v5_recovery_version_and_counters_are_required(self):
        schedulers=['node_rules_elastic','node_rules_recovery','node_rules_recovery_online']
        plan=dict(schedulers=schedulers,seeds=[-1],joint_revision='generation_tail_v5',refines=2)
        job=dict(group='main',levels=4,ranks=256,repeats=1)
        root=self.folder/'v5'
        for scheduler in schedulers:
            for rep in (0,1):
                p=root/'main/levels4'/f'kernel_{scheduler}_t4/p256/repeat_{rep}/analysis_metrics.json'
                p.parent.mkdir(parents=True,exist_ok=True)
                r=dict(partition_seed=-1,repeat=rep,post_work_profile='rank_counts_v1',
                       scaling_work_sha256='same',scaling_kernel_work_sha256='same',
                       kernel_rules_face_trials=10,kernel_rules_point_trials=12,kernel_rules_mappings=4,
                       surface_levels=4,volume_refines=2,post_refine_elements_sum=640,
                       search_revision='generation_tail_v5',
                       generation_recovery='off' if scheduler=='node_rules_elastic' else 'ordered_team_v1',
                       kernel_gen_recovery_seconds=1,kernel_gen_recovery_sweeps=2,
                       kernel_gen_recovery_team_sweeps=0 if scheduler=='node_rules_elastic' else 2)
                r.update(dict.fromkeys(('coop_operation_explores','coop_operation_rollbacks','coop_operation_unknown_types',
                                        'coop_operation_inconclusive','coop_operation_exhausted','coop_operation_tail_grants'),0))
                p.write_text(json.dumps({'result':r}))
        coverage=batch.review_joint_records(root,job,plan)
        self.assertEqual([c['recovery_team_sweeps'] for c in coverage],[0,2,2])
        p=root/'main/levels4/kernel_node_rules_recovery_online_t4/p256/repeat_1/analysis_metrics.json'
        good=json.loads(p.read_text())
        for key,value in [('search_revision','matched_tasks_v4'),('generation_recovery','off'),
                          ('kernel_gen_recovery_team_sweeps',3),('coop_operation_tail_grants',float('nan')),
                          ('scaling_kernel_work_sha256','changed')]:
            with self.subTest(key=key):
                bad=json.loads(json.dumps(good));bad['result'][key]=value;p.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):batch.review_joint_records(root,job,plan)
        p.write_text(json.dumps(good))
        good['result'].pop('coop_operation_exhausted');p.write_text(json.dumps(good))
        with self.assertRaises(ValueError):batch.review_joint_records(root,job,plan)

    def test_dry_run_submits_nothing(self):
        result = self.launch(DRY_RUN='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.capture.exists())
        self.assertEqual(result.stdout.count('--parsable'), 5)
        self.assertIn('预览，未提交作业', (self.folder/'results/RESULT_SUMMARY.txt').read_text())

    def test_only_audited_complete_jobs_expose_comparisons(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        root = self.folder/'results';key = 'main/levels2/p256';folder = root/key
        def write_csv(name, rows):
            with (folder/name).open('w') as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader();writer.writerows(rows)
        (folder/'kernel_issues.txt').write_text('')
        write_csv('kernel_summary.csv', [dict(scheduler=s, seed=-1, algorithm='baseline' if s=='node_original' else 'sparse',
                   repeats=5, timing='natural', threads=4) for s in SCHEDULERS])
        audit = [dict(contribution=c, seed=-1, quality_pass=True, paired_count=5, timing='natural', threads=4,
                      paired_wins=1, control_seconds=1., candidate_seconds=1.1,
                      speedup=1/1.1, paired_reduction_pct=-10.) for c in CONTRIBUTIONS]
        write_csv('ablation_summary.csv', audit)
        command = ['python3',str(HELPER),str(root),'--mark',key,'--exit-code','0']
        subprocess.run(command,check=True,capture_output=True)
        self.assertIn('-10.00%', (root/'RESULT_SUMMARY.txt').read_text())
        with (root/'batch_comparisons.csv').open() as stream:
            self.assertEqual(len(list(csv.DictReader(stream))),4)
        audit[0]['quality_pass'] = False;write_csv('ablation_summary.csv',audit)
        subprocess.run(command,check=True,capture_output=True)
        self.assertEqual((root/'batch_comparisons.csv').read_text(),'')
        self.assertIn('质量或同编号重复配对未通过',(root/'RESULT_SUMMARY.txt').read_text())

    def test_failed_warmup_skips_only_that_strategys_measurements(self):
        # 替换子脚本调用和最终汇总，直接执行真实的策略轮换/预热门控循环。
        env_file = self.folder/'shell_env.sh';capture = self.folder/'variants.tsv'
        env_file.write_text('''bash() {
  printf '%s\\t%s\\n' "$KERNEL_SCHEDULER" "$KERNEL_REPEAT" >> "$VARIANT_CAPTURE"
  [[ "$KERNEL_SCHEDULER" != node_rules_fixed || "$KERNEL_REPEAT" != 0 ]]
}
python3() {
  if [[ "$1" == */analyze_results.py && "$3" == --kernel-overview ]]; then return 0; fi
  command python3 "$@"
}
''')
        result = self.launch(EXPERIMENT_PRESET='rule_search', RULE_SEARCH_ALL='0', RULE_SEARCH_SWEEP='0',
                             MESH_EXPERIMENT_WORKER='1', PROCESS_COUNTS='64', PROCESS_COUNT='64',
                             REPEATS='2', BASH_ENV=str(env_file), VARIANT_CAPTURE=str(capture))
        self.assertNotEqual(result.returncode, 0)
        calls = [s.split('\t') for s in capture.read_text().splitlines()]
        self.assertEqual(len(calls),13)
        self.assertEqual([r for s,r in calls if s=='node_rules_fixed'],['0'])
        self.assertEqual([r for s,r in calls if s=='node_rules_elastic'],['0','1','2'])
        self.assertIn('跳过该策略的正式计时',result.stderr)

    def test_atomic_writers_never_share_temporary_names(self):
        # 强制所有写入者完成临时写入后同时替换；固定 .tmp 的旧实现必然失败。
        target = self.folder/'summary.txt'
        barrier = threading.Barrier(8)
        replace = Path.replace
        def synchronized_replace(source, destination):
            barrier.wait(timeout=5)
            return replace(source, destination)
        payloads = [str(i)*10000 for i in range(8)]
        with patch.object(Path, 'replace', synchronized_replace), ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda content: batch.atomic(target, content), payloads))
        self.assertIn(target.read_text(), payloads)
        self.assertEqual(list(self.folder.glob('.*.tmp')), [])

    def test_concurrent_job_callbacks_keep_every_state(self):
        self.assertEqual(self.launch().returncode, 0)
        root = self.folder/'results'
        plan = json.loads((root/'rule_search_batch_plan.json').read_text())
        processes = [subprocess.Popen(['python3', str(HELPER), str(root), '--mark', j['key'],
                                       '--exit-code', '19'], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for j in plan['jobs']]
        for process in processes:
            _, error = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, error)
        with (root/'batch_jobs.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r['status'] == '失败' and '19' in r['detail'] for r in rows))
        self.assertFalse((root/'.rule_search_batch.lock.d').exists())

    def test_busy_lock_keeps_saved_exit_code_and_does_not_remove_owner(self):
        self.assertEqual(self.launch().returncode, 0)
        root = self.folder/'results'
        lock = root/'.rule_search_batch.lock.d'
        lock.mkdir()
        with self.assertRaises(TimeoutError):
            with batch.summary_lock(root, timeout=0):
                self.fail('不应获得已有锁')
        self.assertTrue(lock.exists())
        args = ['rule_search_batch.py', str(root), '--mark', 'main/levels2/p256', '--exit-code', '23']
        with patch('sys.argv', args), patch.object(batch, 'summary_lock', side_effect=TimeoutError('busy')):
            with self.assertRaises(TimeoutError):
                batch.main()
        state = json.loads((root/'main/levels2/p256/rule_search_job_state.json').read_text())
        self.assertEqual(state['exit_code'], 23)

    def test_report_failure_never_aborts_worker_or_masks_experiment_failure(self):
        env_file = self.folder/'report_failure.sh'
        env_file.write_text('''bash() {
  printf '%s\\n' "$KERNEL_REPEAT" >> "$VARIANT_CAPTURE"
  return 0
}
python3() {
  if [[ "$1" == */rule_search_batch.py ]]; then return 88; fi
  if [[ "$1" == */analyze_results.py ]]; then return "$MOCK_ANALYSIS_RC"; fi
  command python3 "$@"
}
''')
        for rc in (0, 23):
            capture = self.folder/f'worker-{rc}.txt'
            result = self.launch(EXPERIMENT_PRESET='rule_search', RULE_SEARCH_ALL='0', RULE_SEARCH_SWEEP='0',
                                 MESH_EXPERIMENT_WORKER='1', PROCESS_COUNTS='64', PROCESS_COUNT='64',
                                 RULE_SEARCH_BATCH_ROOT=str(self.folder/'results'), RULE_SEARCH_BATCH_GROUP='main',
                                 REPEATS='2', BASH_ENV=str(env_file), VARIANT_CAPTURE=str(capture),
                                 MOCK_ANALYSIS_RC=str(rc))
            self.assertEqual(result.returncode, rc, result.stderr)
            self.assertEqual(len(capture.read_text().splitlines()), 15)
            self.assertIn('继续本规模实验', result.stderr)
            self.assertIn(f'保留实验退出码 {rc}', result.stderr)


if __name__ == '__main__':
    unittest.main()
