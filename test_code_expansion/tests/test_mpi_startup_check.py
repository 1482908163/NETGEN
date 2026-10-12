#!/usr/bin/env python3
import os,sys,tempfile,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'strong_scaling'))
from mpi_startup_check import probe
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);calls=root/'calls';stop=root/'stop.json'
    command=[sys.executable,'-c',f'from pathlib import Path;p=Path({str(calls)!r});p.write_text(p.read_text()+"x" if p.exists() else "x");print("[MPI_PREFLIGHT_OK] ranks=8 thread_support=1")']
    assert probe(root/'valid',stop,8,'binary',2,command)==0
    assert probe(root/'valid',stop,8,'binary',2,command)==0 and calls.read_text()=='x'
    assert probe(root/'valid',stop,8,'new_binary',2,command)==0 and calls.read_text()=='xx'
    for name,command in (('missing',[sys.executable,'-c','print("no probe")']),('ranks',[sys.executable,'-c','print("[MPI_PREFLIGHT_OK] ranks=16 thread_support=1")']),('exit',[sys.executable,'-c','raise SystemExit(15)']),('timeout',[sys.executable,'-c','import time;time.sleep(10)'])):
        start=time.monotonic();assert probe(root/name,stop,8,'binary',1,command)==75
        assert json.loads(stop.read_text())['reason']=='mpi_startup_probe_failed'
        assert probe(root/name,stop,8,'binary',1,command)==75
        assert time.monotonic()-start<5
print('PASS: one all-rank probe per identity; stale binary refresh, missing marker/rank/failure/timeout and cached allocation stop')
