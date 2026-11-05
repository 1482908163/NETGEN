#!/usr/bin/env python3
"""仅编译旋转链实验，不提交作业、不执行测试。"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from run_rotation_chain_suite import digest, resolve_tool, source_hashes


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,default=root/'build/rotation_chain_costs_v2')
    parser.add_argument('--cmake',default=os.environ.get('ROTATION_CMAKE') or 'cmake')
    parser.add_argument('--ninja',default=None)
    parser.add_argument('--jobs',type=int,default=4)
    args=parser.parse_args()
    if args.jobs<1:parser.error('--jobs 必须为正数')
    build=args.build_dir.resolve();build.mkdir(parents=True,exist_ok=True)
    manifest_path=build/'rotation_chain_build.json'
    # A failed rebuild must not leave a previous success marker.
    if manifest_path.exists():manifest_path.unlink()
    logs=build/'compile_logs';logs.mkdir(exist_ok=True)
    hashes=source_hashes(root)

    def command(name,argv):
        print('编译阶段：'+name,flush=True)
        log=logs/(name+'.txt')
        with log.open('w') as stream:
            try:
                result=subprocess.run(list(map(str,argv)),cwd=root,stdout=stream,stderr=subprocess.STDOUT)
            except OSError as exc:
                stream.write(str(exc)+'\n')
                raise RuntimeError(f'无法启动 {argv[0]}：{exc}；日志 {log}') from exc
        if result.returncode:raise RuntimeError(f'{name} 失败，请查看 {log}')

    try:
        cmake=resolve_tool(args.cmake,root,build)
        make=resolve_tool(args.ninja,root,build,'ninja') if args.ninja else resolve_tool('make',root,build,'make')
        command('cmake_version',[cmake,'--version'])
        version=re.search(r'cmake version (\d+)\.(\d+)',(logs/'cmake_version.txt').read_text())
        if not version or tuple(map(int,version.groups()))<(3,16):
            raise RuntimeError('需要 CMake 3.16 或更高版本')
        configure=[cmake,'-S',root/'netgen','-B',build,'-DCMAKE_BUILD_TYPE=Release',
                   '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON','-DCMAKE_MAKE_PROGRAM='+make,
                   '-G','Ninja' if args.ninja else 'Unix Makefiles']
        configure+=['-D'+flag+'=OFF' for flag in ('USE_SUPERBUILD','USE_GUI','USE_PYTHON','USE_MPI',
                    'USE_OCC','USE_JPEG','USE_MPEG','USE_NUMA','USE_NATIVE_ARCH')]
        for variable,option in (('CC','CMAKE_C_COMPILER'),('CXX','CMAKE_CXX_COMPILER')):
            if os.environ.get(variable):configure+=['-D'+option+'='+os.environ[variable]]
        command('configure',configure)
        command('kernel',[cmake,'--build',build,'--target','nglib','-j',args.jobs])
        builds={}
        for kind,suffix,flags in (('release','',[]),('profile','_profile',['--profile']),
                                  ('sanitize','_san',['--sanitize'])):
            command(kind,[sys.executable,root/'test_code_expansion/tests/build_rotation_chain_experiment.py',
                          '--build-dir',build,*flags])
            binary=build/('rotation_chain_experiment'+suffix)
            builds[kind]=dict(binary_sha256=digest(binary),**json.loads(binary.with_suffix('.build.json').read_text()))
        if hashes!=source_hashes(root):raise RuntimeError('编译期间源码发生变化，请重新编译')
        manifest=dict(source_sha256=hashes,builds=builds,core_sha256=digest(build/'libsrc/core/libngcore.so'),
                      environment=dict(cmake=cmake,make_program=make,cc=os.environ.get('CC'),cxx=os.environ.get('CXX'),
                                       native_arch=False))
        temporary=manifest_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');temporary.replace(manifest_path)
        print('编译完成。未运行任何测试。现在可以提交测试作业。',flush=True)
    except (OSError,ValueError,RuntimeError) as exc:
        (logs/'failure.txt').write_text(str(exc)+'\n')
        parser.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
