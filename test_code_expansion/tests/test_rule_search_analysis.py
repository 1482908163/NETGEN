#!/usr/bin/env python3
"""规则搜索审计的故障注入检查，不需要实验数据或集群。"""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
import sys

path=Path(__file__).resolve().parents[1]/'strong_scaling/analyze_results.py'
sys.path.insert(0,str(path.parent))
spec=importlib.util.spec_from_file_location('analyze_results',path)
analyze=importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze)


class RuleSearchAudit(unittest.TestCase):
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

    def test_local_cannot_offload(self):
        rows,meta=self.joint_fixture()
        with self.assertRaises(ValueError):analyze.inspect_search_policy(rows,{})


if __name__=='__main__':unittest.main()
