#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

source=Path(__file__).resolve().parents[1]/'strong_scaling/collect_failure_diagnostics.py'
spec=importlib.util.spec_from_file_location('failure_diagnostics',source)
diag=importlib.util.module_from_spec(spec);spec.loader.exec_module(diag)


class DiagnosticsTests(unittest.TestCase):
    def test_preserves_error_categories_and_failed_ranks(self):
        q=dict(identity={'MESH_SOURCE_REVISION':'old'},counts={'hard_errors':3},issues=['structural_errors'],
               structural_pass=False,per_rank=[dict(rank=0,hard_errors=0,checked=10,elements=10),
                dict(rank=73,hard_errors=3,invalid_indices=1,orphan_surface=2,checked=9,elements=10,
                     volume_sum=18446744073709551600,volume_xor=99)])
        result=diag.quality_details(q)
        self.assertEqual(result['failed_ranks'],[q['per_rank'][1]])
        self.assertEqual(len(q['per_rank']),2)

    def test_existing_failure_does_not_change_status_or_invent_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);text='退出码：139\n收尾各阶段已观测进程数：{"finalize.end":256}\nCaught signal 11\n'
            (root/'failure_summary.txt').write_text(text)
            result=diag.collect(root)
            self.assertEqual(result['exit_code'],139)
            self.assertEqual(result['exit_checkpoints'],{'finalize.end':256})
            self.assertIn('quality_read_error',result)
            self.assertEqual((root/'failure_summary.txt').read_text(),text)
            self.assertFalse((root/'SUCCESS').exists())

    def test_early_errors_survive_long_finalize_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'run.log').write_text('Caught signal 11\n'+'padding\n'*15000+
                                      '[EXIT_CHECKPOINT] rank=73 stage=finalize.end\n')
            result=diag.collect(root,exit_code=139)
            self.assertEqual(result['exit_checkpoints'],{'finalize.end':1})
            self.assertIn('Caught signal 11',(root/'failure_summary.txt').read_text())

    def test_rebuilt_binary_and_unrecorded_frames_are_not_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'mesh';binary.write_bytes(b'not the original binary')
            text=f' 5 {binary}() [0x401000]\n 6 /unrecorded() [0x402000]\n'
            with patch.object(diag.subprocess,'run') as run:
                result=diag.resolve_frames(text,'0'*64+'  '+str(binary)+'\n',{})
                run.assert_not_called()
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['status'],'binary_hash_mismatch')

    def test_stage_progress_does_not_hide_failure_or_stack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);lines=[]
            for stage in ('kernel_complete','refine_1','refine_2'):
                for rank in range(256):
                    lines.append('[MESH_EVIDENCE] '+json.dumps(dict(kind='checkpoint',rank=rank,stage=stage,index_hash='abc'))+'\n')
            lines.append('[MESH_EVIDENCE] '+json.dumps(dict(kind='invalid_connectivity',rank=73,stage='volume_exchange_pack',ids=[0,1,2,3]))+'\n')
            lines.append('Caught signal 11\n[MESH_EVIDENCE] broken\n')
            (root/'run.log').write_text(''.join(lines));result=diag.collect(root,exit_code=86)
            self.assertEqual(result['mesh_checkpoint_counts'],dict(kernel_complete=256,refine_1=256,refine_2=256))
            self.assertEqual([r['rank'] for r in result['mesh_checkpoints']],[73]*3)
            self.assertEqual(result['mesh_evidence'][0]['ids'],[0,1,2,3])
            self.assertEqual(result['messages'],['Caught signal 11'])
            self.assertEqual(result['mesh_evidence_malformed'],1)

    def test_no_source_lines_still_preserves_function_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'mesh';binary.write_bytes(b'\x7fELF\x02\x01'+b'\0'*10+b'\x02\0')
            with patch.object(diag.shutil,'which',return_value='/usr/bin/addr2line'),patch.object(diag.subprocess,'run') as run:
                run.return_value=subprocess.CompletedProcess([],0,'com_baryVolumeElements()\n??:?\n','')
                result=diag.resolve_frames(f'5 {binary}() [0x430ac0]\n',diag.file_hash(binary)+'  '+str(binary)+'\n',{})
            self.assertEqual(result[0]['status'],'function_only')

    @unittest.skipUnless(shutil.which('g++') and shutil.which('addr2line') and shutil.which('nm'),'requires local debug toolchain')
    def test_resolves_matching_debug_binary_to_source_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cpp=root/'fixture.cpp';binary=root/'mesh'
            cpp.write_text('int main(){return 0;}\n')
            subprocess.run(['g++','-g','-no-pie',str(cpp),'-o',str(binary)],check=True,capture_output=True)
            symbols=subprocess.check_output(['nm',str(binary)],text=True)
            address=next(s.split()[0] for s in symbols.splitlines() if s.endswith(' T main'))
            result=diag.resolve_frames(f' 5 {binary}() [0x{address}]\n',diag.file_hash(binary)+'  '+str(binary)+'\n',{})
            self.assertEqual(result[0]['status'],'resolved')
            self.assertIn('fixture.cpp:1',result[0]['location'])


if __name__=='__main__':unittest.main()
