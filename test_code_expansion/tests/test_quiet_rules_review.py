#!/usr/bin/env python3
"""以真实精简批次做缺失/篡改注入；不运行内核或提交集群作业。"""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'test_code_expansion/strong_scaling'))
from review_quiet_rules import review, phase_sensitivity

SOURCE=ROOT/'test_code_expansion/strong_scaling_results/mesh_algorithms_20261029-141532'


class QuietReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch=Path(self.tmp.name)/SOURCE.name
        for f in SOURCE.rglob('*'):
            if f.is_file() and f.name in ('analysis_metrics.json','run_status.tsv','configuration.txt',
                                         'requested_process_counts.txt','ablation_summary.csv'):
                target=self.batch/f.relative_to(SOURCE)
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(f,target)

    def mutate(self, rep, update):
        path=self.batch/f'levels4/kernel_node_rules_recovery_quiet_t4/p256/sparse_natural/repeat_{rep}/analysis_metrics.json'
        data=json.loads(path.read_text());update(data);path.write_text(json.dumps(data))

    def test_real_batch(self):
        r=review(self.batch);s=r['scales']['256']
        self.assertEqual((r['runs'],r['warmups'],r['formal']),(8,2,6))
        self.assertEqual(s['wins'],3)
        self.assertAlmostEqual(s['paired_median_pct'],1.774872468525801)
        self.assertEqual(s['strategies']['node_rules_recovery_quiet']['compute_critical'],[73]*3)

    def test_new_scaling_batch_retains_negative_pair_and_migrating_ranks(self):
        source=SOURCE.with_name('mesh_algorithms_20261029-200648')
        r=review(source)
        self.assertEqual((r['runs'],r['warmups'],r['formal']),(16,4,12))
        self.assertEqual([r['scales'][n]['wins'] for n in ('512','1024')],[2,3])
        self.assertLess(r['scales']['512']['paired_reduction_pct'][2],0)
        self.assertEqual(set(r['scales']['1024']['matched_ranks']),{'92','440'})
        self.assertEqual(r['scales']['512']['all_rank_work_comparisons'],2560)
        self.assertEqual(r['scales']['1024']['all_rank_work_comparisons'],5120)

    def test_sensitivity_follows_new_bottleneck(self):
        rows=[dict(rank=0,vertex_arrival_elapsed=10,compute_seconds=8,kernel_front_seconds=4),
              dict(rank=1,vertex_arrival_elapsed=9.5,compute_seconds=8,kernel_front_seconds=1)]
        r=phase_sensitivity(rows,12,'kernel_front_seconds')
        self.assertEqual((r['before_rank'],r['after_rank'],r['saved_seconds']),(0,1,1))
        rows[0]['kernel_front_seconds']=9
        with self.assertRaisesRegex(ValueError,'阶段不在'):phase_sensitivity(rows,12,'kernel_front_seconds')

    def test_missing_rank_fails(self):
        self.mutate(1,lambda d:d['rank_phases']['rows'].pop())
        with self.assertRaisesRegex(ValueError,'进程记录'):review(self.batch)

    def test_noncritical_work_change_fails(self):
        def change(d):
            k=d['rank_phases']['columns'].index('kernel_rules_face_trials')
            d['rank_phases']['rows'][0][k]+=1
        self.mutate(1,change)
        with self.assertRaisesRegex(ValueError,'同进程工作量'):review(self.batch)

    def test_formal_replay_fails(self):
        self.mutate(1,lambda d:d['result'].__setitem__('kernel_quiet_verified',1))
        with self.assertRaisesRegex(ValueError,'重放不符'):review(self.batch)

    def test_missing_warmup_replay_fails(self):
        self.mutate(0,lambda d:d['result'].__setitem__('kernel_quiet_verified',0))
        with self.assertRaisesRegex(ValueError,'重放不符'):review(self.batch)

    def test_changed_quality_fails(self):
        self.mutate(0,lambda d:d['result']['mesh_quality'].__setitem__('volume_fingerprint_sha256','changed'))
        with self.assertRaisesRegex(ValueError,'指纹'):review(self.batch)

    def test_changed_binary_fails(self):
        self.mutate(1,lambda d:d['rank_phases']['identity'].__setitem__('MESH_BINARY_SHA256','changed'))
        with self.assertRaisesRegex(ValueError,'来源'):review(self.batch)

    def test_missing_requested_scale_fails(self):
        (self.batch/'levels4/requested_process_counts.txt').write_text('256 512\n')
        with self.assertRaises(FileNotFoundError):review(self.batch)


if __name__=='__main__':unittest.main()
