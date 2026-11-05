import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from run_rotation_chain_suite import check_group, check_run, summarize


def record(route, threads=4, seconds=1., status=0, batches=None):
    return dict(arguments=[threads, route, 0, 7, 0, 2, 2], returncode=0, stderr='',
                result=dict(seconds=seconds, status=status, fingerprint='same', calls=7000,
                            front_batches=(170 if route > 0 and threads > 1 else 0) if batches is None else batches,
                            dispatches=170*(1 if route == 1 else 3)))


class GateTests(unittest.TestCase):
    def test_equivalence_and_fallback(self):
        check_group([record(-1, 1),record(0),record(1),record(2),record(3),record(2, 1)],2)

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
            blocks.append({str(r):record(r,seconds=x if r==0 else y) for r in range(4)})
        s=summarize(blocks)['0->1']
        self.assertAlmostEqual(s['median_pct'],20.)
        self.assertEqual(s['wins'],1)
        with self.assertRaises(ValueError):summarize([{'0':record(0)}])

    def test_reject_invalid_time(self):
        for value in (-1.,float('nan'),float('inf')):
            with self.assertRaises(ValueError):check_run(record(1,seconds=value))

    def test_one_submission_and_spool_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);tests=root/'test_code_expansion/tests';tests.mkdir(parents=True)
            (tests/'run_rotation_chain_suite.py').write_text('# fixture\n')
            wrapper=tests/'run_rotation_chain_suite.sh'
            wrapper.write_text(Path(__file__).with_name('run_rotation_chain_suite.sh').read_text())
            mock=root/'yhbatch'
            mock.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            mock.chmod(0o755)
            env=dict(os.environ,PATH=str(root)+os.pathsep+os.environ['PATH'],ROTATION_SUITE_ROOT=str(root))
            result=subprocess.run(['bash',str(wrapper),'--submit','--blocks','4'],env=env,capture_output=True,text=True,check=True)
            args=json.loads(result.stdout)
            self.assertEqual(args.count('--job'),1)
            self.assertIn('--export=ALL',args)
            self.assertEqual(args[args.index('-N')+1],'1')
            self.assertEqual(args[-3:],['--job','--blocks','4'])
            mock.rename(root/'yhrun')
            # A spool path has no project beside it; the exported root must win.
            spool=root/'spool_script';spool.write_text(wrapper.read_text())
            result=subprocess.run(['bash',str(spool),'--job','--blocks','4'],env=dict(env,SLURM_JOB_ID='mock'),capture_output=True,text=True,check=True)
            args=json.loads(result.stdout)
            self.assertIn('--cpu-bind=cores',args)
            self.assertIn(str(tests/'run_rotation_chain_suite.py'),args)

    def test_job_requires_allocation(self):
        env=dict(os.environ);env.pop('SLURM_JOB_ID',None)
        result=subprocess.run(['bash',str(Path(__file__).with_name('run_rotation_chain_suite.sh')),'--job'],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,2)


if __name__=='__main__':unittest.main()
