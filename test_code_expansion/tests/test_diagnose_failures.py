#!/usr/bin/env python3
"""Resolve real ELF offsets/symbols and refuse mismatched/missing provenance."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'strong_scaling'))
from diagnose_failures import diagnose,sha256

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);source=root/'anchor.cpp';library=root/'libnglib.so'
    source.write_text('extern "C" __attribute__((noinline)) int crash_anchor(int x) {return x+3;}\n')
    subprocess.run([os.environ.get('CXX','g++'),'-shared','-fPIC','-O3','-g1',str(source),'-o',str(library)],check=True)
    symbols=subprocess.check_output(['nm','-D','--defined-only',str(library)],text=True)
    offset=next(parts[0] for line in symbols.splitlines() if (parts:=line.split())[-1]=='crash_anchor')
    run=root/'p256'/'sparse_natural'/'repeat_1';run.mkdir(parents=True)
    (run.parents[1]/'configuration.txt').write_text(f'MESH_KERNEL_SHA256={sha256(library)}\n')
    (run/'node_coop_stderr.txt').write_text(
        f'[cn483:42:0:42] Caught signal 11 (Segmentation fault)\n'
        f' 1 {library}(+0x{offset}) [0x400009001234]\n'
        f' 2 {library}(crash_anchor+0x0) [0x400009001234]\n')
    report=diagnose(run,root/'missing_binary',library,root/'missing_core')
    assert report['identities']['kernel']['matched']
    assert len(report['frames'])==2 and all(frame['has_source_line'] for frame in report['frames'])
    assert all('anchor.cpp:1' in frame['resolution']['stdout'] for frame in report['frames'])
    # File name equality cannot override the captured hash.
    report=diagnose(run,root/'missing_binary',library,root/'missing_core',{'kernel':'0'*64})
    assert not report['identities']['kernel']['matched']
    assert all('resolution' not in frame and 'skipped' in frame for frame in report['frames'])
    (run.parents[1]/'configuration.txt').unlink()
    report=diagnose(run,root/'missing_binary',library,root/'missing_core')
    assert all('resolution' not in frame for frame in report['frames'])
    # An unrelated path with the same basename is excluded entirely.
    (run/'node_coop_stderr.txt').write_text(f'1 {root}/other/libnglib.so(+0x{offset}) [0x40000000]\n')
    report=diagnose(run,root/'missing_binary',library,root/'missing_core',{'kernel':sha256(library)})
    assert not report['frames']
    # Non-PIE executables report a fixed virtual address; a module-relative
    # offset alone would resolve the wrong line.
    binary=root/'mesh_occ_mpi';app_source=root/'app.cpp'
    app_source.write_text('extern "C" __attribute__((noinline)) int app_anchor(int x) {return x+3;}\nint main(int argc,char**) {return app_anchor(argc);}\n')
    subprocess.run([os.environ.get('CXX','g++'),'-no-pie','-O3','-g1',str(app_source),'-o',str(binary)],check=True)
    symbols=subprocess.check_output(['nm','--defined-only',str(binary)],text=True)
    address=next(parts[0] for line in symbols.splitlines() if (parts:=line.split())[-1]=='app_anchor')
    (run/'node_coop_stderr.txt').write_text(f'1 {binary}(+0x100) [0x{address}]\n')
    report=diagnose(run,binary,library,root/'missing_core',{'binary':sha256(binary)})
    assert len(report['frames'])==1 and report['frames'][0]['has_source_line']
    assert 'app.cpp:1' in report['frames'][0]['resolution']['stdout']
print('PASS: shared/PIE offsets, fixed executable addresses, source lines, symbol+offset and provenance refusals')
