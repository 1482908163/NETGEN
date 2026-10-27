#!/usr/bin/env python3
"""链接已构建的无 MPI/OCC 内核，检查顺序、并行、重放及资源生命周期。

C++ 内部符号为 hidden，因此按原对象顺序链接；不更改库的导出或生产构建入口。
可选 --reference-sources 指向父版本 ruler3.cpp、meshing3.cpp 所在目录；本脚本不调用 Git。
"""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--reference-sources', type=Path)
    parser.add_argument('--mpi-prefix',type=Path,help='可选 MPI 安装目录；运行四进程真实规则消息回归')
    args = parser.parse_args()
    build = args.build_dir.resolve()
    repo = Path(__file__).resolve().parents[2]
    entries = json.loads((build/'compile_commands.json').read_text())
    entries = [e for e in entries if 'CMakeFiles/nglib.dir/' in e.get('command', '')]
    if not entries:
        raise SystemExit('需要 CMAKE_EXPORT_COMPILE_COMMANDS=ON 的独立 Netgen 内核构建。')

    def command(entry):
        return shlex.split(entry['command'])

    def flags(entry):
        a = command(entry)
        if any(x in a for x in ('-DPARALLEL', '-DOCCGEOMETRY')):
            raise SystemExit('回归工具使用 USE_MPI=OFF、USE_OCC=OFF 的独立内核构建。')
        i = a.index('-o')
        del a[i:i+2]
        a.remove('-c')
        a.remove(entry['file'])
        return a

    def invoke(argv):
        subprocess.run(argv, cwd=build, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    rule = next(e for e in entries if e['file'].endswith('/ruler3.cpp'))
    # compile_commands 保留目标原始源文件顺序；某些旧注册器依赖静态初始化顺序。
    objects = [str(build/command(e)[command(e).index('-o')+1]) for e in entries]
    core = build/'libsrc/core/libngcore.so'
    libs = [str(core), '-lz', '-ldl', '-pthread', '-Wl,-rpath,'+str(core.parent)]
    output = build/'rule_search_tests'
    output.mkdir(exist_ok=True)
    test = repo/'test_code_expansion/tests/rule_search_regression.cpp'
    binary = output/'rule_search'
    invoke(flags(rule)+[str(test)]+objects+libs+['-o', str(binary)])
    oracle = None
    if args.reference_sources:
        replacements = {}
        for stem in ('ruler3', 'meshing3'):
            entry = next(e for e in entries if e['file'].endswith('/'+stem+'.cpp'))
            source = args.reference_sources.resolve()/(stem+'.cpp')
            obj = output/('reference_'+stem+'.o')
            invoke(flags(entry)+['-I'+str(repo/'netgen/libsrc/meshing'), '-c', str(source), '-o', str(obj)])
            replacements[stem+'.cpp.o'] = str(obj)
        reference = output/'rule_search_reference'
        legacy=[] if 'Meshing3::ExecuteRuleSearchPacket' in (args.reference_sources/'ruler3.cpp').read_text() else ['-DRULE_SEARCH_LEGACY_TRANSPORT']
        invoke(flags(rule)+['-DRULE_SEARCH_REFERENCE']+legacy+[str(test)]+
               [replacements.get(Path(o).name, o) for o in objects]+libs+['-o', str(reference)])
        oracle = subprocess.check_output([str(reference), '1', '0'], text=True, cwd=output)
        (output/'reference.txt').write_text(oracle)
        oracle = re.search(r'fingerprint=(\w+)', oracle)[1]
    logs = []
    front_group=output/'front_group'
    invoke(flags(rule)+[str(repo/'test_code_expansion/tests/front_group_regression.cpp')]+
           objects+libs+['-o',str(front_group)])
    out=subprocess.check_output([str(front_group)],text=True,cwd=output,timeout=120)
    logs.append(out);print(out.strip())
    if args.reference_sources:
        reference_front=output/'front_group_reference'
        invoke(flags(rule)+['-DFRONT_GROUP_ORIGINAL_GENERATOR',
               str(repo/'test_code_expansion/tests/front_group_regression.cpp')]+
               [replacements.get('meshing3.cpp.o',o) if Path(o).name=='meshing3.cpp.o' else o for o in objects]+
               libs+['-o',str(reference_front)])
        before=subprocess.check_output([str(reference_front)],text=True,cwd=output,timeout=120)
        if re.search(r'front_fingerprint=(\w+)',out)[1]!=re.search(r'front_fingerprint=(\w+)',before)[1]:
            raise SystemExit('完整前沿生成与修改前源码不同')
        logs.append(before);print('通过：完整前沿生成与修改前 meshing3.cpp 源码指纹一致。')
    cases=[(1,-1,0),(4,-1,0),(1,0,0),(1,2,0),(2,1,0),(2,2,0),(4,1,0),(4,2,0),
           (2,5,0),(2,6,0),(4,5,0),(4,6,0),(2,3,0),(2,4,0),(4,3,0),(4,4,0),(2,4,1),(4,4,1)]
    for threads, mode, remote in cases:
        out = subprocess.check_output([str(binary), str(threads), str(mode),str(remote)], text=True, cwd=output)
        fingerprint = re.search(r'fingerprint=(\w+)', out)[1]
        if oracle is None:
            oracle = fingerprint
        if fingerprint != oracle:
            raise SystemExit(f'结果不一致：threads={threads}, mode={mode}')
        logs.append(f'threads={threads} mode={mode} remote={remote}\n{out}')
    recovery=output/'generation_recovery'
    invoke(flags(rule)+[str(repo/'test_code_expansion/tests/generation_recovery_regression.cpp')]+
           objects+libs+['-o',str(recovery)])
    out=subprocess.check_output([str(recovery)],text=True,cwd=output,timeout=180)
    logs.append(out);print(out.strip())
    if args.mpi_prefix:
        mpi=args.mpi_prefix.resolve();mpi_binary=output/'rule_search_mpi'
        integrity=output/'mesh_integrity'
        invoke([str(mpi/'bin/mpicxx')]+flags(rule)[1:]+[
            str(repo/'test_code_expansion/tests/mesh_integrity_api_regression.cpp')]+
            objects+libs+['-o',str(integrity)])
        logs.append(subprocess.check_output([str(mpi/'bin/mpiexec'),'-n','2',str(integrity)],text=True,cwd=output,timeout=30))
        for failure in ('invalid','changed'):
            result=subprocess.run([str(mpi/'bin/mpiexec'),'-n','2',str(integrity),failure],
                                  text=True,capture_output=True,cwd=output,timeout=30)
            if result.returncode!=86 or '[MESH_EVIDENCE]' not in result.stderr:
                raise SystemExit('完整性保护未按预期停止：'+failure+'\n'+result.stderr)
            logs.append(result.stderr)
        print('通过：真实内核有界连接读取及两进程坏索引/只读阶段变更拦截。')
        invoke([str(mpi/'bin/mpicxx')]+flags(rule)[1:]+['-DNETGEN_SEARCH_MPI_REGRESSION',str(test)]+
               objects+libs+['-o',str(mpi_binary)])
        mpi_binary.chmod(mpi_binary.stat().st_mode|0o111)
        api_binary=output/'search_api'
        invoke([str(mpi/'bin/mpicxx')]+flags(rule)[1:]+[str(repo/'test_code_expansion/tests/search_api_regression.cpp'),
               str(build/'libnglib.so')]+libs+['-Wl,-rpath,'+str(build),'-o',str(api_binary)])
        api_binary.chmod(api_binary.stat().st_mode|0o111)
        logs.append(subprocess.check_output([str(api_binary)],text=True,cwd=output))

        for mode in (3,4):
            out=subprocess.check_output([str(mpi/'bin/mpiexec'),'-n','4',str(mpi_binary),'4',str(mode),'1'],
                                        text=True,cwd=output,timeout=90)
            if re.search(r'fingerprint=(\w+)',out)[1]!=oracle:
                raise SystemExit('MPI 远程规则结果与原串行指纹不一致')
            logs.append(f'real_mpi mode={mode}\n{out}')
        print('通过：四进程 MPI 真实规则任务，正式与重放两种模式；合成节点身份，非集群性能测试。')
    (output/'results.txt').write_text('\n'.join(logs))
    print(f'通过：{len(cases)} 组顺序/并行/重放、远程消息及资源生命周期检查；指纹 {oracle}。')
    print('父版本源码对照：'+('通过。' if args.reference_sources else '未提供；仅同版本差分。'))
    print(output/'results.txt')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        print((error.stderr or b'').decode() if isinstance(error.stderr, bytes) else (error.stderr or ''))
        raise SystemExit(error.returncode)
