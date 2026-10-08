#!/usr/bin/env python3
"""Failed data survive archival; active/successful runs are not modified."""
import gzip
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
ANALYZER=ROOT/'strong_scaling/analyze_results.py'
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);run=root/'route_cost_profile/p2/sparse_natural/repeat_1'
    run.mkdir(parents=True)
    reason=run/'failure_reason.txt';reason.write_text('analysis_validation_failure\n')
    raw=b'{"incomplete":true}\n'*300
    (run/'rank_profiles.jsonl').write_bytes(raw)
    (run/'run.log').write_bytes(b'generated successfully\n'*200)
    mesh=run/'mesh/volfined/volfined0.vol';mesh.parent.mkdir(parents=True);mesh.write_text('preserve mesh')
    def invoke():return subprocess.run([sys.executable,str(ANALYZER),str(root),'--archive-failed'],capture_output=True,text=True)
    (run/'RUNNING').touch()
    assert invoke().returncode==1 and (run/'rank_profiles.jsonl').read_bytes()==raw
    (run/'RUNNING').unlink()
    result=invoke();assert result.returncode==0,result.stderr
    assert gzip.decompress((run/'rank_profiles.jsonl.gz').read_bytes())==raw
    assert not (run/'SUCCESS').exists() and not (run/'rank_profiles.jsonl').exists()
    assert reason.read_text()=='analysis_validation_failure\n' and mesh.read_text()=='preserve mesh'
    packed=(run/'rank_profiles.jsonl.gz').read_bytes()
    assert invoke().returncode==0 and (run/'rank_profiles.jsonl.gz').read_bytes()==packed
    (run/'SUCCESS').touch();(run/'rank_profiles.jsonl').write_bytes(raw)
    assert invoke().returncode==0 and (run/'rank_profiles.jsonl').exists()
    (run/'SUCCESS').unlink();(run/'rank_profiles.jsonl').unlink()
    external=root/'outside.jsonl';external.write_bytes(raw)
    (run/'rank_profiles.jsonl').symlink_to(external)
    assert invoke().returncode==0 and external.read_bytes()==raw
    assert (run/'rank_profiles.jsonl').is_symlink()
print('PASS: failed evidence is lossless, idempotent, retained as failure, and preserves active/successful runs and mesh files')
