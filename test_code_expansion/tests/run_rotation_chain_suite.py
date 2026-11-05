#!/usr/bin/env python3
"""一次运行完成旋转链消融、差分、分项测量和内存检查；不改生产入口。"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROUTES = {0: '原动态队列', 1: '固定分工旋转链', 2: '固定分工逐旋转汇合', 3: '批量输入动态队列逐旋转汇合'}
COMPARISONS = [(0, 1), (0, 2), (0, 3), (2, 1), (3, 2)]


def resolve_tool(request, root, build, tool='cmake'):
    """Ignore invalid PATH entries; recover CMake from existing project caches."""
    clean_path = os.pathsep.join(p for p in os.get_exec_path() if p and Path(p).is_dir())
    candidates = [request] if os.path.dirname(request) else [shutil.which(request, path=clean_path)]
    # An explicit path/name is authoritative; only default cmake gets fallbacks.
    if request == 'cmake' and tool == 'cmake':
        for directory in (build, root/'build-netgen', root/'test_code_expansion/build', root/'build'):
            cache = directory/'CMakeCache.txt'
            if cache.is_file():
                for line in cache.read_text(errors='replace').splitlines():
                    if line.startswith('CMAKE_COMMAND:INTERNAL='):
                        candidates.append(line.split('=', 1)[1])
        candidates += [shutil.which('cmake3', path=clean_path),
                       str(Path(sys.executable).parent/'cmake'),
                       '/usr/local/bin/cmake', '/usr/bin/cmake', '/usr/bin/cmake3']
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    hint = '--cmake /实际路径/bin/cmake 或设置 ROTATION_CMAKE' if tool == 'cmake' else '请配置该构建工具的可执行路径'
    raise RuntimeError(f'找不到可执行文件 {request!r}；{hint}。目录不能当作可执行文件；不会自动安装工具。')


def write_measurement_report(state, records, path):
    fields = {
        'input_wall': '批量输入准备', 'copy_wall': '槽位复制', 'queue_wall': '队列构造',
        'claim_elapsed_sum': '领任务累计', 'prepare_elapsed_sum': '规则准备累计',
        'group_dispatch_wall': '派发至汇合', 'launch_delay_sum': '任务启动延迟累计',
        'completion_to_join_sum': '任务结束至汇合累计', 'join_tail_wall': '最后任务结束至汇合',
    }
    table = {}
    lines = ['# 旋转链成本分解', '', '正式耗时仅取普通构建；下列成本来自独立插桩构建。', '',
             '| 完整前沿样例 | 路线 | ' + ' | '.join(fields.values()) + ' |',
             '|---|---|' + '---:|' * len(fields)]
    for label, keys in state['profile_samples'].items():
        profiles = [records[k]['result']['profile'] for k in keys]
        medians = {f: statistics.median(p[f] for p in profiles) for f in fields}
        table[label] = dict(median_seconds=medians,
                           claim_attempts=[p['claim_attempts'] for p in profiles],
                           claim_successes=[p['claim_successes'] for p in profiles])
        if label.endswith('/0'):
            continue  # Original route has no candidate batch/join instrumentation.
        lines.append('| ' + ' | '.join(label.split('/')) + ' | ' +
                     ' | '.join(f'{medians[f]*1000:.4f}' for f in fields) + ' |')
    lines += ['', '单位：毫秒；各列独立取中位数。累计字段按工作任务相加，与主线程墙钟区间重叠，不能相加为总时间。',
              '任务结束至汇合记录现有任务结束到主线程返回的间隔，不增加屏障；它不是操作系统测得的线程阻塞或 CPU 空闲时间。路线 1 只在整链结束汇合，路线 2/3 每次旋转汇合。',
              '领任务计时包含时钟开销，空队列探测也计入次数；线程私有累加后每次规则调用发布。它不能直接当作无探针构建的真实队列成本。',
              '输入/复制/规则准备分开记录；规则准备不含更早的数组构造。路线 0 只有领任务和规则准备等原始字段可比，候选批量/汇合字段为零表示未覆盖，不能当作零成本。', '',
              '| 输入 | 0→1 | 0→2 | 0→3 | 2→1 | 3→2 |', '|---|---:|---:|---:|---:|---:|']
    for label, value in state['benchmarks'].items():
        values = [value['comparisons'][f'{x}->{y}'] for x, y in COMPARISONS]
        lines.append('| '+label+' | '+' | '.join(f"{v['median_pct']:+.2f}% ({v['wins']}/{v['count']})" for v in values)+' |')
    lines += ['', '正数为配对降时。2→1 评估合并汇合，3→2 包含固定分工及队列结构影响，0→3 包含多项布局变化；不把差值提前归因于单一因素。',
              '本报告是分项阶段输出，整个套件是否完成须查看 summary.json 的 complete 字段。', '']
    path.write_text('\n'.join(lines))
    return table


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_run(record):
    if record['returncode'] or not isinstance(record['result'], dict):
        raise ValueError('运行失败或缺少结果对象')
    if 'runtime error:' in record['stderr'] or 'ERROR: AddressSanitizer' in record['stderr']:
        raise ValueError('内存/未定义行为检查报告错误')
    d = record['result']
    if not isinstance(d.get('seconds'), (int, float)) or not 0 <= d['seconds'] < float('inf'):
        raise ValueError('无效耗时')
    if not isinstance(d.get('fingerprint'), str) or not d['fingerprint']:
        raise ValueError('缺少指纹')


def check_group(cases, fixture=None):
    if not cases or cases[0]['arguments'][:2] != [1, -1]:
        raise ValueError('必须先提供同一构建的串行基线')
    expected = cases[0]['result']['fingerprint']
    for c in cases:
        check_run(c)
        if c['result']['fingerprint'] != expected:
            raise ValueError('同一构建的结果/工作指纹不一致')
        t, route = c['arguments'][:2]
        if fixture is not None:
            d = c['result']
            if d['status'] != (2 if fixture in (3, 5) else 0):
                raise ValueError('生成状态不符合预期；失败退出不能当作生成成功')
            covered = d['front_batches'] > 0
            if covered != (t > 1 and route > 0):
                raise ValueError('候选实际覆盖不符')
            if covered and d['dispatches'] != d['front_batches'] * (1 if route == 1 else 3):
                raise ValueError('完整前沿候选汇合/派发次数不符')
        else:
            d = c['result']
            if t > 1 and route > 0:
                limit, repeats = c['arguments'][3], c['arguments'][5]
                expected_dispatches = 160 * limit * repeats if route == 1 else d['calls']
                if d['dispatches'] != expected_dispatches:
                    raise ValueError('局部候选汇合/派发次数不符')
    return expected


def summarize(blocks):
    if not blocks or any(set(b) != {'0', '1', '2', '3'} for b in blocks):
        raise ValueError('计时块必须包含全部四条路线')
    output = {}
    for baseline, candidate in COMPARISONS:
        pairs = []
        for block in blocks:
            x, y = (block[str(k)]['result']['seconds'] for k in (baseline, candidate))
            if x <= 0 or y <= 0:
                raise ValueError('计时必须为正数')
            pairs.append(100 * (x-y)/x)
        output[f'{baseline}->{candidate}'] = dict(paired_reduction_pct=pairs,
            median_pct=statistics.median(pairs), wins=sum(v > 0 for v in pairs), count=len(pairs))
    return output


def main():
    root = Path(__file__).resolve().parents[2]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-dir', type=Path, default=root/'build/rotation_chain_costs_v2')
    p.add_argument('--output-dir', type=Path, default=root/'test_code_expansion/strong_scaling_results'/time.strftime('rotation_chain_suite_%Y%m%d-%H%M%S'))
    p.add_argument('--cpus', default='auto', help='auto 使用进程允许的前四个处理器')
    p.add_argument('--blocks', type=int, default=12, help='正式四路线计时块数，至少 4 且为 4 的倍数')
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--cmake', default=os.environ.get('ROTATION_CMAKE') or 'cmake')
    p.add_argument('--ninja', default=None, help='可选指定 Ninja 路径；默认使用 CMake 原生生成器')
    p.add_argument('--resume', action='store_true', help='仅复用源码、配置和二进制摘要一致的已保存运行')
    a = p.parse_args()
    allowed = sorted(os.sched_getaffinity(0))
    cpus = allowed[:4] if a.cpus == 'auto' else [int(v) for v in a.cpus.split(',')]
    if len(set(cpus)) != 4 or not set(cpus) <= set(allowed) or a.blocks < 4 or a.blocks % 4 or a.jobs < 1:
        p.error('需要四个不同的可用处理器、正编译并发数和至少四个完整计时块')
    os.sched_setaffinity(0, cpus)
    build, out = a.build_dir.resolve(), a.output_dir.resolve()
    sources = [Path(__file__), root/'test_code_expansion/tests/test_rotation_chain_suite.py',
               root/'test_code_expansion/tests/run_rotation_chain_suite.sh',
               root/'test_code_expansion/tests/build_rotation_chain_experiment.py',
               root/'test_code_expansion/tests/rotation_chain_experiment.cpp',
               root/'test_code_expansion/tests/rotation_chain_probe.hpp',
               root/'test_code_expansion/strong_scaling/cluster_env.sh', root/'netgen/CMakeLists.txt',
               root/'netgen/libsrc/meshing/ruler3.cpp', root/'netgen/libsrc/meshing/meshing3.cpp']
    hashes = {str(s.relative_to(root)): digest(s) for s in sources}
    config = dict(build_dir=str(build), cpus=cpus, blocks=a.blocks, jobs=a.jobs, cmake=a.cmake, ninja=a.ninja)
    summary_path, raw_path = out/'summary.json', out/'runs.jsonl'
    if summary_path.exists():
        if not a.resume:
            p.error('输出目录已有记录；使用新目录或显式 --resume，禁止覆盖结果')
        state = json.loads(summary_path.read_text())
        if state['source_sha256'] != hashes or state['config'] != config:
            p.error('源码或配置变化，不能续用旧结果')
    else:
        if out.exists() and any(out.iterdir()):
            p.error('输出目录非空，禁止覆盖无清单文件')
        out.mkdir(parents=True, exist_ok=True)
        state = dict(schema='rotation_chain_suite_v2', config=config, source_sha256=hashes,
                     platform=platform.platform(), cpu_max=Path('/sys/fs/cgroup/cpu.max').read_text() if Path('/sys/fs/cgroup/cpu.max').exists() else None,
                     started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), complete=False,
                     routes=ROUTES, builds={}, validation={}, benchmarks={}, profile_samples={},
                     expected_run_count=276+8*4*(a.blocks+1)+24)
    cache = {}
    if raw_path.exists():
        for line in raw_path.read_text().splitlines():
            record = json.loads(line)
            cache[record['key']] = record

    def save():
        tmp = summary_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2)+'\n')
        tmp.replace(summary_path)

    def command(key, argv):
        logfile = out/(key+'.txt')
        print('START', key, flush=True)
        start = time.monotonic()
        launch_error = None
        with logfile.open('w') as stream:
            try:
                result = subprocess.run(list(map(str, argv)), cwd=root, stdout=stream, stderr=subprocess.STDOUT)
                code = result.returncode
            except OSError as exc:
                code = 127; launch_error = str(exc)
                stream.write(f'启动失败：{exc}\n可执行文件：{argv[0]}\n工作目录：{root}\n')
        state.setdefault('commands', {})[key] = dict(argv=list(map(str, argv)), returncode=code,
            launch_error=launch_error,
            seconds=time.monotonic()-start, log=logfile.name, log_sha256=digest(logfile))
        save()
        if code:
            raise RuntimeError(f'{key} 失败，查看 {logfile}')
        print('PASS', key, flush=True)

    def compile_binary(kind):
        flags = [] if kind == 'release' else ['--'+('sanitize' if kind == 'sanitize' else 'profile')]
        command('build_'+kind, [sys.executable, root/'test_code_expansion/tests/build_rotation_chain_experiment.py', '--build-dir', build, *flags])
        binary = build/('rotation_chain_experiment'+dict(release='', profile='_profile', sanitize='_san')[kind])
        value = dict(binary_sha256=digest(binary), **json.loads(binary.with_suffix('.build.json').read_text()))
        if kind in state['builds'] and state['builds'][kind]['binary_sha256'] != value['binary_sha256'] and any(c['kind'] == kind for c in cache.values()):
            raise RuntimeError('二进制变化，禁止混用已保存样本；请使用新输出目录')
        state['builds'][kind] = value
        save()
        return binary

    def run(key, kind, binary, t, route, variant=0, limit=7, verify=1, repeats=2, fixture=None):
        args = [t, route, variant, limit, verify, repeats]
        if fixture is not None:
            args.append(fixture)
        if key in cache:
            record = cache[key]
            if record['kind'] != kind or record['arguments'] != args or record['binary_sha256'] != digest(binary):
                raise RuntimeError('缓存输入或二进制不一致')
            check_run(record)
            return record
        env = dict(os.environ)
        if kind == 'sanitize':
            env.update(ASAN_OPTIONS='detect_leaks=0:halt_on_error=1', UBSAN_OPTIONS='halt_on_error=1')
        cpu_stat=Path('/sys/fs/cgroup/cpu.stat')
        cpu_before=cpu_stat.read_text() if cpu_stat.exists() else None
        start = time.monotonic()
        try:
            proc = subprocess.run([str(binary), *map(str, args)], cwd=build, capture_output=True, text=True, timeout=180, env=env)
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors='replace') if isinstance(exc.stdout, bytes) else exc.stdout or ''
            stderr = exc.stderr.decode(errors='replace') if isinstance(exc.stderr, bytes) else exc.stderr or ''
            stderr += '\nTIMEOUT after 180 seconds'; code = -124
        try:
            data = json.loads(stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            data = None
        record = dict(key=key, kind=kind, arguments=args, result=data, returncode=code, stderr=stderr,
                      process_seconds=time.monotonic()-start, binary_sha256=state['builds'][kind]['binary_sha256'],
                      cpu_stat_before=cpu_before,cpu_stat_after=cpu_stat.read_text() if cpu_stat.exists() else None)
        if code or data is None:
            record['stdout'] = stdout
        with raw_path.open('a') as stream:
            stream.write(json.dumps(record, ensure_ascii=False)+'\n'); stream.flush(); os.fsync(stream.fileno())
        cache[key] = record
        if len(cache) % 25 == 0:
            print('PROGRESS', len(cache), '/', state['expected_run_count'], key, flush=True)
        check_run(record)
        if data['profile']['enabled'] != (kind == 'profile'):
            raise RuntimeError('分项测量构建与正式计时混用')
        return record

    def validate(kind, binary):
        routes = [(1, -1), (4, 0)] + [(t, route) for route in (1, 2, 3) for t in ((1, 2, 4) if kind == 'release' else (2, 4))]
        for limit in ((7, 45) if kind == 'release' else (45,)):
            for variant in (0, 1, 2):
                key = f'{kind}/local/{limit}/{variant}'
                cases = [run(f'{key}/{t}/{route}', kind, binary, t, route, variant, limit) for t, route in routes]
                fp = check_group(cases)
                state['validation'][key] = dict(passed=True, runs=[c['key'] for c in cases], fingerprint=fp)
                save()
        for fixture in range(6):
            key = f'{kind}/front/{fixture}'
            cases = [run(f'{key}/{t}/{route}', kind, binary, t, route, verify=0, fixture=fixture) for t, route in routes]
            fp = check_group(cases, fixture)
            item = dict(passed=True, runs=[c['key'] for c in cases], fingerprint=fp,
                        status=cases[0]['result']['status'], elements=cases[0]['result']['elements'])
            if kind != 'release':
                item['cross_build_equal'] = fp == state['validation'][f'release/front/{fixture}']['fingerprint']
            state['validation'][key] = item
            save()
        print('PASS differential', kind, 'groups', sum(k.startswith(kind+'/') for k in state['validation']), flush=True)

    def benchmark(binary, label, variant=0, limit=7, fixture=None):
        blocks, orders, keys = [], [], []
        for block in range(-1, a.blocks):
            # Four positions are balanced in each group of four blocks.
            order = list(range(4))
            if block >= 0 and (block//4) % 2:
                order.reverse()
            shift = max(0, block) % 4
            order = order[shift:]+order[:shift]
            values = {}
            for route in order:
                record = run(f'benchmark/{label}/{block}/{route}', 'release', binary, 4, route,
                             variant, limit, verify=0, repeats=10 if limit == 7 else 2, fixture=fixture)
                if fixture is not None:
                    d = record['result']
                    expected = state['validation'][f'release/front/{fixture}']['fingerprint']
                    if d['status'] != 0 or d['fingerprint'] != expected or (route > 0 and d['front_batches'] == 0):
                        raise RuntimeError('成功计时样本的状态、指纹或候选覆盖不符')
                values[str(route)] = record
            if block >= 0:
                blocks.append(values); orders.append(order); keys.append({r:v['key'] for r,v in values.items()})
        state['benchmarks'][label] = dict(orders=orders, blocks=keys, warmup_prefix=f'benchmark/{label}/-1', comparisons=summarize(blocks))
        save()
        print('BENCH', label, {k:round(v['median_pct'], 3) for k,v in state['benchmarks'][label]['comparisons'].items()}, flush=True)

    state.pop('error', None); state['complete'] = False; save()
    try:
        state['environment'] = dict(requested_cmake=a.cmake,
            invalid_path_entries=[v for v in os.get_exec_path() if v and not Path(v).is_dir()],
            compiler_cc=os.environ.get('CC'), compiler_cxx=os.environ.get('CXX'),
            scheduler_job=os.environ.get('SLURM_JOB_ID'))
        save()
        cmake_bin = resolve_tool(a.cmake, root, build)
        make_bin = resolve_tool(a.ninja, root, build, 'ninja') if a.ninja else resolve_tool('make', root, build, 'make')
        state['environment'].update(cmake=cmake_bin, make_program=make_bin)
        save()
        command('cmake_version', [cmake_bin, '--version'])
        version = re.search(r'cmake version (\d+)\.(\d+)', (out/'cmake_version.txt').read_text())
        if not version or tuple(map(int, version.groups())) < (3, 16):
            raise RuntimeError('本项目需要 CMake 3.16 或更高版本，请通过 --cmake 指定')
        command('make_version', [make_bin, '--version'])
        if os.environ.get('CXX'):
            command('compiler_version', [os.environ['CXX'], '--version'])
        command('driver_tests', [sys.executable, '-m', 'unittest', 'discover', '-s', root/'test_code_expansion/tests', '-p', 'test_rotation_chain_suite.py', '-v'])
        cmake = [cmake_bin, '-S', root/'netgen', '-B', build, '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON']
        cmake += ['-D'+flag+'=OFF' for flag in ('USE_SUPERBUILD','USE_GUI','USE_PYTHON','USE_MPI','USE_OCC','USE_JPEG','USE_MPEG','USE_NUMA')]
        cmake += ['-DCMAKE_MAKE_PROGRAM='+make_bin]
        for variable, option in (('CC', 'CMAKE_C_COMPILER'), ('CXX', 'CMAKE_CXX_COMPILER')):
            if os.environ.get(variable):
                cmake += ['-D'+option+'='+os.environ[variable]]
        if a.ninja:
            cmake += ['-G', 'Ninja']
        else:
            cmake += ['-G', 'Unix Makefiles']
        command('configure', cmake)
        command('kernel_build', [cmake_bin, '--build', build, '--target', 'nglib', '-j', a.jobs])
        release = compile_binary('release'); validate('release', release)
        for limit in (7, 45):
            for variant in (0, 1, 2):
                benchmark(release, f'local/{limit}/{variant}', variant, limit)
        for fixture in (2, 4):
            benchmark(release, f'front/{fixture}', fixture=fixture)
        profile = compile_binary('profile'); validate('profile', profile)
        for fixture in (2, 4):
            for route in range(4):
                keys = []
                for repeat in range(3):
                    key = f'profile_measure/{fixture}/{route}/{repeat}'
                    record = run(key, 'profile', profile, 4, route, verify=0, fixture=fixture)
                    if record['result']['fingerprint'] != state['validation'][f'profile/front/{fixture}']['fingerprint']:
                        raise RuntimeError('分项测量改变结果')
                    keys.append(key)
                state['profile_samples'][f'{fixture}/{route}'] = keys; save()
        state['cost_breakdown'] = write_measurement_report(state, cache, out/'measurement_report.md')
        save()
        sanitizer = compile_binary('sanitize'); validate('sanitize', sanitizer)
        assert all(digest(root/path) == value for path,value in hashes.items()), '运行期间源码发生变化'
        state['run_count'] = len(cache)
        if state['run_count'] != state['expected_run_count']:
            raise RuntimeError('实际运行数量与计划不一致')
        state['raw_sha256'] = digest(raw_path)
        state['complete'] = True
        state['finished_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        save(); print('ALL COMPLETE', summary_path, 'runs', len(cache), flush=True)
    except Exception as exc:
        state['error'] = str(exc); state['run_count'] = len(cache); save()
        (out/'failure.txt').write_text(str(exc)+'\n')
        print('FAILED', out/'summary.json', str(exc), flush=True)
        raise


if __name__ == '__main__':
    main()
