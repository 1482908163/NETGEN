#!/usr/bin/env python3
"""用假提交器验证整批编排，不申请节点，不运行 MPI。"""
import csv
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT/'test_code_expansion/strong_scaling/run_experiments.sh'
HELPER = DRIVER.with_name('rule_search_batch.py')
SCHEDULERS = 'node_original node_fixed node_elastic node_rules_fixed node_rules_elastic'.split()
CONTRIBUTIONS = 'rule_search_fixed rule_search_elastic rule_search_borrowing rule_search_total'.split()


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='netgen-batch-test-')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.capture = self.folder/'calls.jsonl'
        fake = self.folder/'fake_submit'
        fake.write_text('''#!/usr/bin/env python3
import json,os,sys
keys='START_EPOCH RUN_ROOT RULE_SEARCH_BATCH_GROUP RULE_SEARCH_BATCH_ROOT LEVELS REPEATS WARMUPS KERNEL_SCHEDULERS CLEANUP_RESULTS COMPACT_RESULTS MESH_EXPERIMENT_WORKER RULE_SEARCH_ALL RULE_SEARCH_SWEEP'.split()
with open(os.environ['BATCH_CAPTURE'],'a') as f:f.write(json.dumps({'args':sys.argv[1:],'env':{k:os.environ.get(k) for k in keys}})+'\\n')
if os.environ.get('FAIL_CHECK')=='1' and os.environ['RULE_SEARCH_BATCH_GROUP']=='precheck':sys.exit(19)
print('12345')
''')
        fake.chmod(0o755)
        self.env = dict(os.environ, EXPERIMENT_PRESET='rule_search_all',
                        RUN_ROOT=str(self.folder/'results'), SBATCH_COMMAND=str(fake),
                        BATCH_CAPTURE=str(self.capture), DRY_RUN='0', MESH_EXPERIMENT_WORKER='0',
                        START_DELAY_SECONDS='120', MESH_SOURCE_REVISION='test-only')
        for k in ('START_EPOCH','RULE_SEARCH_BATCH_ROOT','RULE_SEARCH_BATCH_GROUP','BASH_ENV'):
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
            self.assertIn('--exclusive', a)
            self.assertFalse(any(s.startswith('--dependency') for s in a))
            self.assertEqual(e['KERNEL_SCHEDULERS'].split(), SCHEDULERS)
            for key in ('CLEANUP_RESULTS','COMPACT_RESULTS','WARMUPS','MESH_EXPERIMENT_WORKER'):
                self.assertEqual(e[key], '1')
            self.assertEqual(e['RULE_SEARCH_ALL'], '0')
            self.assertEqual(e['RULE_SEARCH_SWEEP'], '0')
        self.assertEqual(actual, [('precheck',2,16,1),('main',2,64,5),('main',2,256,5),
                                  ('main',4,64,5),('main',4,256,5)])
        self.assertIn('待运行', (self.folder/'results/RESULT_SUMMARY.txt').read_text())

    def test_failed_submission_does_not_stop_other_scales(self):
        result = self.launch(FAIL_CHECK='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 5)
        state = json.loads((self.folder/'results/precheck/levels2/p64/rule_search_job_state.json').read_text())
        self.assertEqual(state['exit_code'], 125)
        self.assertIn('存在失败', (self.folder/'results/RESULT_SUMMARY.txt').read_text())

    def test_invalid_configuration_submits_nothing(self):
        result = self.launch(RULE_SEARCH_MAIN_NODES='0')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.capture.exists())

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


if __name__ == '__main__':
    unittest.main()
