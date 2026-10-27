#!/usr/bin/env python3
"""规则搜索审计的故障注入检查，不需要实验数据或集群。"""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
import sys
import csv
import io
import os
import tempfile
from contextlib import redirect_stdout
from unittest.mock import patch

path=Path(__file__).resolve().parents[1]/'strong_scaling/analyze_results.py'
sys.path.insert(0,str(path.parent))
spec=importlib.util.spec_from_file_location('analyze_results',path)
analyze=importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze)


class RuleSearchAudit(unittest.TestCase):
    def test_quality_compaction_retains_integrity_version_without_inventing_old_evidence(self):
        meta=dict.fromkeys(analyze.QUALITY_IDENTITY,'fixture')
        meta.update(mesh_quality='volume_audit_v1',timing_mode='natural',core_only='true',
                    feature_schema='mesh_comm_v1',algorithm='sparse',quality_face_reference='allgather',
                    kernel_scheduler='node_rules_recovery',partition_seed='-1',kernel_threads='4')
        m={'quality_'+k:0 for k in analyze.QUALITY_COUNTS}
        m.update(quality_points=4,quality_elements=1,quality_surfaces=4,quality_checked=1,quality_negative=1,
                 quality_shape_min=.5,quality_shape_sum=.5,quality_angle_min=30,quality_angle_max=90,
                 quality_abs_volume=1,quality_face_reference_passed=1,local_points_before_adjacency=4,
                 local_volume_elements_before_adjacency=1,local_surface_elements_before_adjacency=4)
        m.update({'quality_shape_bin_'+str(i):int(i==5) for i in range(10)})
        m.update({'quality_'+k+'_'+half:0 for k in ('surface_sum','surface_xor','volume_sum','volume_xor')
                  for half in ('hi','lo')})
        rows=[dict(rank=0,repeat=0,metrics=m)]
        old=analyze.compact_quality(analyze.inspect_quality(rows,meta))
        self.assertEqual(old['integrity_profile'],'unavailable')
        meta['mesh_integrity']='stage_fingerprint_v1'
        new=analyze.compact_quality(analyze.inspect_quality(rows,meta))
        self.assertEqual(new['integrity_profile'],'stage_fingerprint_v1')
        self.assertEqual(old['volume_fingerprint_sha256'],new['volume_fingerprint_sha256'])
        self.assertEqual(old['counts'],new['counts'])

    def test_focused_overview_uses_selected_elastic_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for scheduler in ('node_rules_elastic','node_rules_recovery','node_rules_recovery_online'):
                folder=root/f'kernel_{scheduler}_t4/p256/analysis';folder.mkdir(parents=True)
                (folder/'issues.txt').write_text('')
                summary=dict(algorithm='sparse',timing='natural',partition_seed=-1,successful_repeats=1,
                             core_median=2,volume_elements_sum_median=64,kernel_generation_seconds_median=1,
                             kernel_repair_seconds_median=.1,kernel_optimization_seconds_median=.2)
                run=dict(algorithm='sparse',timing='natural',partition_seed=-1,repeat=1,core_seconds=2,
                         volume_elements_sum=64,kernel_final_illegal=0)
                for name,row in [('summary.csv',summary),('runs.csv',run)]:
                    with (folder/name).open('w') as f:
                        w=csv.DictWriter(f,fieldnames=row);w.writeheader();w.writerow(row)
            with patch.dict(os.environ,{},clear=True),patch.object(analyze,'ablation_reports',return_value=([],[])),patch.object(analyze,'critical_overview',return_value=([],[])),redirect_stdout(io.StringIO()):
                analyze.kernel_overview(root,256)
                self.assertEqual((root/'p256/kernel_issues.txt').read_text().strip(),'')
                # Selecting a baseline which is absent still fails; a different
                # available candidate must never silently become the baseline.
                with patch.dict(os.environ,{'KERNEL_THREAD_COUNTS':'4','KERNEL_SCHEDULERS':'node_rules_recovery'}):
                    with self.assertRaises(SystemExit):analyze.kernel_overview(root,256)
                self.assertIn('缺少 node_fixed',(root/'p256/kernel_issues.txt').read_text())

    def test_unrelated_modes_still_require_fixed_control(self):
        selected=[('4','node_rules_elastic'),('4','node_rules_recovery')]
        self.assertEqual(analyze.node_comparison_control(4,'node_rules_recovery',selected),'node_rules_elastic')
        self.assertEqual(analyze.node_comparison_control(8,'node_rules_recovery',selected),'node_fixed')
        self.assertEqual(analyze.node_comparison_control(4,'node_elastic',selected),'node_fixed')
        self.assertEqual(analyze.node_comparison_control(4,'node_rules_recovery',selected+[('4','node_fixed')]),'node_fixed')

    def test_generation_profile_and_per_rank_coverage(self):
        rows=[{'metrics':dict.fromkeys(analyze.GENERATION_METRICS,0.)} for _ in range(2)]
        meta={'generation_profile':'generation_v1','generation_recovery':'ordered_team_v1'}
        rows[0]['metrics'].update(kernel_gen_recovery_sweeps=2,kernel_gen_recovery_team_sweeps=2)
        result=analyze.inspect_generation(rows,meta,'node_rules_recovery')
        self.assertEqual(result['kernel_gen_recovery_sweeps'],2)
        # Aggregate coverage would hide this impossible rank-local count.
        rows[1]['metrics']['kernel_gen_recovery_team_sweeps']=1
        rows[0]['metrics']['kernel_gen_recovery_team_sweeps']=1
        with self.assertRaises(ValueError):analyze.inspect_generation(rows,meta,'node_rules_recovery')

    def test_generation_diagnostics_cannot_be_missing_or_invalid(self):
        meta={'generation_profile':'generation_v1','generation_recovery':'off'}
        for key in analyze.GENERATION_METRICS:
            for value in (None,-1,float('nan'),float('inf'),'0'):
                with self.subTest(key=key,value=value):
                    rows=[{'metrics':dict.fromkeys(analyze.GENERATION_METRICS,0.)}]
                    rows[0]['metrics'][key]=value
                    with self.assertRaises(ValueError):analyze.inspect_generation(rows,meta,'node_rules_elastic')
        rows=[{'metrics':dict.fromkeys(analyze.GENERATION_METRICS,0.)}]
        with self.assertRaises(ValueError):analyze.inspect_generation(rows,meta,'node_rules_recovery')
        with self.assertRaises(ValueError):analyze.inspect_generation(rows,{},'node_rules_elastic')
        rows[0]['metrics']['kernel_gen_recovery_sweeps']=.5
        with self.assertRaises(ValueError):analyze.inspect_generation(rows,meta,'node_rules_elastic')

    def test_even_partial_group_keeps_labels_out_of_median(self):
        rows=[dict(post_work_profile='rank_counts_v1',search_revision='matched_tasks_v4',
                   seconds=x,scaling_work_sha256='fingerprint') for x in (1,2,3,4)]
        rows[-1]['late_metric']=7
        result=analyze.aggregate_metrics(rows)
        self.assertEqual(result['seconds_median'],2.5)
        self.assertEqual(result['late_metric_median'],7)
        self.assertEqual(result['post_work_profile'],'rank_counts_v1')
        self.assertNotIn('post_work_profile_median',result)
        self.assertNotIn('scaling_work_sha256_median',result)

    def fixture(self,mode='2'):
        meta={'rule_search_mode':mode,'rule_search_resource':'phase0_base_only','kernel_threads':'4'}
        m={k:0. for k in analyze.RULE_SEARCH_METRICS}
        m.update(kernel_rules_calls=4,kernel_rules_parallel_calls=2,kernel_rules_serial_calls=2,
                 kernel_rules_verified_calls=2 if mode in ('2','4') else 0,kernel_rules_calls_le128us=4,
                 kernel_rules_rules=20,kernel_rules_eligible_rules=10,
                 coop_phase_0_epochs=1,coop_phase_0_min_threads=4,coop_phase_0_max_threads=4,
                 coop_phase_0_borrowed_core_seconds=0)
        return [{'metrics':m},{'metrics':deepcopy(m)}],meta

    def test_valid_warmup_and_measurement(self):
        for mode in ('1','2','3','4'):
            rows,meta=self.fixture(mode)
            result=analyze.inspect_rule_search(rows,meta)
            self.assertEqual(result['kernel_rules_calls'],8)
            self.assertEqual(result['rule_search_mode'],int(mode))

    def test_serial_control(self):
        rows,meta=self.fixture('0')
        for r in rows:
            r['metrics']['kernel_rules_parallel_calls']=0
            r['metrics']['kernel_rules_serial_calls']=4
        analyze.inspect_rule_search(rows,meta)

    def test_bad_counter_or_budget_is_rejected(self):
        for key,value in [('kernel_rules_mismatches',1),('kernel_rules_serial_calls',3),
                          ('kernel_rules_calls_le128us',3),('kernel_rules_eligible_rules',21),
                          ('kernel_rules_verified_calls',1),('coop_phase_0_epochs',0),('coop_phase_0_max_threads',5),
                          ('coop_phase_0_min_threads',3),('coop_phase_0_borrowed_core_seconds',.1),
                          ('kernel_rules_wall_seconds',float('nan'))]:
            with self.subTest(key=key):
                rows,meta=self.fixture();rows[0]['metrics'][key]=value
                with self.assertRaises(ValueError):analyze.inspect_rule_search(rows,meta)

    def test_formal_run_cannot_include_replay(self):
        rows,meta=self.fixture('1');rows[0]['metrics']['kernel_rules_verified_calls']=1
        with self.assertRaises(ValueError):analyze.inspect_rule_search(rows,meta)

    def test_control_cannot_silently_parallelize(self):
        rows,meta=self.fixture('0')
        with self.assertRaises(ValueError):analyze.inspect_rule_search(rows,meta)

    def test_contract_required(self):
        rows,meta=self.fixture();meta.pop('rule_search_resource')
        with self.assertRaises(ValueError):analyze.inspect_rule_search(rows,meta)

    def joint_fixture(self):
        policy='decisions serial parallel remote_calls remote_rules predicted_gain_seconds remote_wait_seconds reserved'.split()
        remote='probes grants critical_rejects cost_rejects service_calls payload_bytes wait_seconds service_seconds control_seconds drain_seconds'.split()
        m={**{'search_policy_'+k:0. for k in policy},**{'search_remote_'+k:0. for k in remote}}
        rows=[{'metrics':deepcopy(m)},{'metrics':deepcopy(m)}]
        rows[0]['metrics'].update(search_policy_decisions=2,search_policy_parallel=1,search_policy_serial=1,
                                  search_policy_remote_calls=1,search_policy_remote_rules=3,search_remote_grants=1)
        rows[1]['metrics']['search_remote_service_calls']=1
        return rows,{'search_remote':'completed_anchor_rma_v1'}

    def test_joint_service_counts_close_globally(self):
        rows,meta=self.joint_fixture()
        self.assertEqual(analyze.inspect_search_policy(rows,meta)['search_remote_grants'],1)

    def test_joint_faults_rejected(self):
        for key,value in [('search_remote_grants',2),('search_policy_decisions',3),
                          ('search_policy_parallel',0),('search_remote_service_calls',1),
                          ('search_policy_predicted_gain_seconds',float('nan'))]:
            with self.subTest(key=key):
                rows,meta=self.joint_fixture();rows[0]['metrics'][key]=value
                with self.assertRaises(ValueError):analyze.inspect_search_policy(rows,meta)

    def test_credit_termination_requires_every_credit_consumed(self):
        rows,meta=self.joint_fixture();meta['search_remote']='completed_anchor_credit_v2'
        for r in rows:r['metrics'].update({k:0 for k in analyze.SEARCH_CREDIT_METRICS})
        rows[0]['metrics']['search_remote_ready_sent']=2
        rows[1]['metrics']['search_remote_ready_received']=2
        analyze.inspect_search_policy(rows,meta)
        rows[1]['metrics']['search_remote_ready_received']=1
        with self.assertRaises(ValueError):analyze.inspect_search_policy(rows,meta)

    def test_demand_and_subtree_coverage(self):
        rows,meta=self.joint_fixture()
        meta.update(search_remote='completed_anchor_demand_v3',search_policy='ordered_subtree_demand_v3')
        for r in rows:
            r['metrics'].update({k:0 for k in analyze.SEARCH_CREDIT_METRICS+analyze.SEARCH_DEMAND_METRICS+analyze.SEARCH_SUBTREE_METRICS})
            r['metrics']['search_policy_sampled_calls']=0
        rows[0]['metrics'].update(search_subtree_calls=1,search_subtree_tasks=2,search_remote_demand_sent=1)
        rows[1]['metrics']['search_remote_demand_received']=1
        analyze.inspect_search_policy(rows,meta)
        for key,value in [('search_remote_demand_sent',2),('search_subtree_tasks',1),('search_subtree_calls',2),
                          ('search_subtree_replay_face_trials',float('nan'))]:
            broken=deepcopy(rows);broken[0]['metrics'][key]=value
            with self.assertRaises(ValueError):analyze.inspect_search_policy(broken,meta)

    def test_post_work_fingerprint_catches_ghost_change_and_rank_swap(self):
        keys=('local_points_before_adjacency','local_surface_elements_before_adjacency',
              'local_volume_elements_before_adjacency','ghost_points_added','ghost_volume_elements_added')
        rows=[{'rank':i,'metrics':dict.fromkeys(keys,i+1)} for i in range(2)]
        original=analyze.post_work_fingerprint(rows)
        self.assertEqual(original,analyze.post_work_fingerprint(list(reversed(rows))))
        rows[0]['metrics']['ghost_points_added']+=1
        self.assertNotEqual(original,analyze.post_work_fingerprint(rows))
        rows[0]['metrics']['ghost_points_added']=float('nan')
        with self.assertRaises(ValueError):analyze.post_work_fingerprint(rows)

    def test_local_cannot_offload(self):
        rows,meta=self.joint_fixture()
        with self.assertRaises(ValueError):analyze.inspect_search_policy(rows,{})


if __name__=='__main__':unittest.main()
