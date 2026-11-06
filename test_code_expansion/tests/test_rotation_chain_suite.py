import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from run_rotation_chain_suite import check_group, check_run, summarize, resolve_tool, ROUTES, shadow_memory_failure, dispatches_per_batch, check_prepare_fault


def record(route, threads=4, seconds=1., status=0, batches=None):
    return dict(arguments=[threads, route, 0, 7, 0, 2, 2], returncode=0, stderr='',
                result=dict(seconds=seconds, status=status, fingerprint='same', calls=7000,
                            front_batches=(170 if route > 0 and threads > 1 else 0) if batches is None else batches,
                            dispatches=170*dispatches_per_batch(route),
                            profile=dict(chain_rules=170*27,chain_rule_rotations=170*3*27,
                                         shared_preparations=170*3,input_copies=170*3*(threads if route in (5,7,8,9) else 27),
                                         workspace_builds=170*threads if route>=7 else 0,
                                         workspace_calls=170*3*27 if route>=7 else 0,
                                         worker_input_copies=170*3*threads if route>=7 else 0,
                                         prepare_dispatches=170 if route==8 else 0,
                                         worker_preparations=170*3 if route==9 else 0,
                                         prefix_checks=170*3*27)))


class GateTests(unittest.TestCase):
    def test_tool_resolution_skips_non_directory_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); bad=root/'not_a_directory';bad.write_text('bad path entry')
            binary=root/'cmake';binary.write_text('#!/bin/sh\n');binary.chmod(0o755)
            with mock.patch.dict(os.environ, PATH=str(bad)+os.pathsep+str(root)):
                self.assertEqual(resolve_tool('cmake',root,root/'build'),str(binary))

    def test_tool_resolution_recovers_project_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);build=root/'build-netgen';build.mkdir()
            binary=root/'custom-cmake';binary.write_text('#!/bin/sh\n');binary.chmod(0o755)
            (build/'CMakeCache.txt').write_text('CMAKE_COMMAND:INTERNAL='+str(binary)+'\n')
            with mock.patch.dict(os.environ, PATH=str(root/'missing')):
                self.assertEqual(resolve_tool('cmake',root,root/'independent'),str(binary))

    def test_explicit_tool_path_does_not_silently_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with self.assertRaisesRegex(RuntimeError,'ROTATION_CMAKE'):
                resolve_tool(str(root/'missing'),root,root/'build')
            with self.assertRaises(RuntimeError):
                resolve_tool(str(root),root,root/'build')

    def test_equivalence_and_fallback(self):
        check_group([record(-1, 1),record(0),record(1),record(2),record(3),record(4),record(5),record(6),
                     record(7),record(8),record(9),record(5, 1),record(7, 1),record(8, 1),record(9, 1)],2)

    def test_reject_changed_geometry(self):
        candidate=record(1);candidate['result']['fingerprint']='changed'
        with self.assertRaises(ValueError):check_group([record(-1,1),candidate],2)

    def test_reject_zero_coverage(self):
        with self.assertRaises(ValueError):check_group([record(-1,1),record(1,batches=0)],2)

    def test_reject_wrong_barrier_count(self):
        candidate=record(2);candidate['result']['dispatches']=170
        with self.assertRaises(ValueError):check_group([record(-1,1),candidate],2)

    def test_failure_exit_is_not_success(self):
        with self.assertRaises(ValueError):check_group([record(-1,1,status=2),record(1,status=2)],2)
        check_group([record(-1,1,status=2),record(1,status=2)],3)

    def test_reject_process_and_sanitizer_errors(self):
        for field,value in (('returncode',-11),('stderr','runtime error: broken'),('stderr','ERROR: AddressSanitizer')):
            data=record(1);data[field]=value
            with self.assertRaises(ValueError):check_run(data)

    def test_pairs_use_same_block(self):
        blocks=[]
        for x,y in ((1.,.5),(10.,11.)):
            blocks.append({str(r):record(r,seconds=x if r==0 else y) for r in ROUTES})
        s=summarize(blocks)['0->9']
        self.assertAlmostEqual(s['median_pct'],20.)
        self.assertEqual(s['wins'],1)
        with self.assertRaises(ValueError):summarize([{'0':record(0)}])

    def test_reject_invalid_time(self):
        for value in (-1.,float('nan'),float('inf')):
            with self.assertRaises(ValueError):check_run(record(1,seconds=value))

    def test_reject_missing_prefix_verification(self):
        for route in (5,7,8,9):
            candidate=record(route);candidate['arguments'][4]=1
            candidate['result']['profile']['prefix_checks']=0
            with self.assertRaises(ValueError):check_group([record(-1,1),candidate],2)

    def test_reject_repeated_copy_in_reuse_route(self):
        for route in (5,7,8,9):
            candidate=record(route);candidate['result']['profile']['input_copies']=170*3*27
            with self.assertRaises(ValueError):check_group([record(-1,1),candidate],2)

    def test_reject_per_rule_workspace_construction(self):
        candidate=record(7);candidate['result']['profile']['workspace_builds']=170*3*27
        with self.assertRaisesRegex(ValueError,'工作区没有按任务批次构造'):
            check_group([record(-1,1),candidate],2)

    def test_reject_workspace_path_bypass(self):
        candidate=record(7);candidate['result']['profile']['workspace_calls']=0
        with self.assertRaisesRegex(ValueError,'入口未覆盖'):
            check_group([record(-1,1),candidate],2)

    def test_reject_host_input_initialization(self):
        candidate=record(8);candidate['result']['profile']['worker_input_copies']=0
        with self.assertRaisesRegex(ValueError,'工作任务内初始化'):
            check_group([record(-1,1),candidate],2)

    def test_reject_unaccounted_preparation_dispatch(self):
        candidate=record(8);candidate['result']['profile']['prepare_dispatches']=0
        with self.assertRaisesRegex(ValueError,'准备派发次数不符'):
            check_group([record(-1,1),candidate],2)

    def test_reject_missing_task_preparation(self):
        candidate=record(9);candidate['result']['profile']['worker_preparations']=0
        with self.assertRaisesRegex(ValueError,'工作任务内完成全部准备'):
            check_group([record(-1,1),candidate],2)

    def test_reject_hidden_pipeline_dispatch(self):
        candidate=record(9);candidate['result']['dispatches']=340
        with self.assertRaisesRegex(ValueError,'派发次数不符'):
            check_group([record(-1,1),candidate],2)

    def test_preparation_failure_must_terminate(self):
        ok=dict(returncode=1,stderr='ROTATION_PREPARATION_FAULT',stdout='',timed_out=False)
        check_prepare_fault(ok)
        for field,value in (('returncode',0),('stderr','unrelated failure'),('timed_out',True),('stdout','result')):
            bad=dict(ok);bad[field]=value
            with self.assertRaises(ValueError):check_prepare_fault(bad)

    def test_shadow_failure_is_not_algorithm_error(self):
        candidate=record(0);candidate.update(returncode=-6,result=None,stderr='ReserveShadowMemoryRange failed')
        self.assertTrue(shadow_memory_failure(candidate))
        candidate['stderr']='ERROR: AddressSanitizer: heap-buffer-overflow'
        self.assertFalse(shadow_memory_failure(candidate))

    def test_one_submission_and_spool_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);tests=root/'test_code_expansion/tests';tests.mkdir(parents=True)
            (tests/'run_rotation_chain_suite.py').write_text('# fixture\n')
            wrapper=tests/'run_rotation_chain_suite.sh'
            wrapper.write_text(Path(__file__).with_name('run_rotation_chain_suite.sh').read_text())
            cluster=root/'test_code_expansion/strong_scaling';cluster.mkdir()
            (cluster/'cluster_env.sh').write_text('export GCCHOME="'+str(root)+'"\n')
            mock=root/'yhbatch'
            mock.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            mock.chmod(0o755)
            env=dict(os.environ,PATH=str(root)+os.pathsep+os.environ['PATH'],ROTATION_SUITE_ROOT=str(root),
                     ROTATION_CC=str(mock),ROTATION_CXX=str(mock),
                     ROTATION_CLUSTER_ENV=str(cluster/'cluster_env.sh'))
            result=subprocess.run(['bash',str(wrapper),'--submit','--blocks','4'],env=env,capture_output=True,text=True,check=True)
            args=json.loads(result.stdout)
            self.assertEqual(args.count('--job'),1)
            self.assertIn('--export=ALL',args)
            self.assertEqual(args[args.index('-N')+1],'1')
            self.assertEqual(args[-3:],['--job','--blocks','4'])
            mock.rename(root/'yhrun')
            env.update(ROTATION_CC=str(root/'yhrun'),ROTATION_CXX=str(root/'yhrun'))
            # A spool path has no project beside it; the exported root must win.
            spool=root/'spool_script';spool.write_text(wrapper.read_text())
            result=subprocess.run(['bash',str(spool),'--job','--blocks','4'],env=dict(env,SLURM_JOB_ID='mock'),capture_output=True,text=True,check=True)
            args=json.loads(result.stdout.strip().splitlines()[-1])
            self.assertIn('--cpu-bind=cores',args)
            self.assertIn(str(tests/'run_rotation_chain_suite.py'),args)

    def test_job_requires_allocation(self):
        env=dict(os.environ);env.pop('SLURM_JOB_ID',None)
        result=subprocess.run(['bash',str(Path(__file__).with_name('run_rotation_chain_suite.sh')),'--job'],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,2)


if __name__=='__main__':unittest.main()
