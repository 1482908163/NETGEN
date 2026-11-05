#!/usr/bin/env python3
"""一次运行完成旋转链消融、差分、分项测量和内存检查；不改生产入口。"""
import argparse
import hashlib
import json
import os
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROUTES = {0: '原动态队列', 4: '动态整规则链', 5: '动态整规则链与前缀缓冲复用', 6: '同布局固定整规则链对照'}
COMPARISONS = [(0, 4), (0, 5), (6, 4), (4, 5)]


class AddressSpaceUnavailable(RuntimeError):
    pass


def shadow_memory_failure(record):
    return bool(record['returncode'] and record['result'] is None and
                'ReserveShadowMemoryRange failed' in record['stderr'])


def memory_limits():
    return {name: list(resource.getrlimit(getattr(resource, name)))
            for name in ('RLIMIT_AS', 'RLIMIT_DATA', 'RLIMIT_STACK')}


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
        'chain_claim_sum': '整链领取累计', 'worker_copy_sum': '工作任务重置/复制累计',
        'shared_prepare_wall': '共享准备墙钟', 'prepare_elapsed_sum': '规则调用准备区间累计',
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
                           claim_successes=[p['claim_successes'] for p in profiles],
                           work_counts=[{k:p[k] for k in ('chain_rules','chain_rule_rotations','shared_preparations','input_copies','prefix_checks')} for p in profiles])
        if label.endswith('/0'):
            continue  # Original route has no candidate batch/join instrumentation.
        lines.append('| ' + ' | '.join(label.split('/')) + ' | ' +
                     ' | '.join(f'{medians[f]*1000:.4f}' for f in fields) + ' |')
    lines += ['', '单位：毫秒；各列独立取中位数。累计字段按工作任务相加，与主线程墙钟区间重叠，不能相加为总时间。',
              '任务结束至汇合记录现有任务结束到主线程返回的间隔，不增加屏障；它不是操作系统测得的线程阻塞或 CPU 空闲时间。新路线 4/5/6 都在整个批次结束汇合。',
              '整链领取计时包含时钟开销；共享准备在每个旋转建立一次，规则调用中的准备区间另记，不将两者混为一谈。它不能直接当作无探针构建的真实队列成本。',
              '输入/复制/规则准备分开记录；规则准备不含更早的数组构造。路线 0 只有领任务和规则准备等原始字段可比，候选批量/汇合字段为零表示未覆盖，不能当作零成本。', '',
              '| 输入 | 0→4 | 0→5 | 6→4 | 4→5 |', '|---|---:|---:|---:|---:|']
    for label, value in state['benchmarks'].items():
        values = [value['comparisons'][f'{x}->{y}'] for x, y in COMPARISONS]
        lines.append('| '+label+' | '+' | '.join(f"{v['median_pct']:+.2f}% ({v['wins']}/{v['count']})" for v in values)+' |')
    lines += ['', '| 样例/路线 | 整规则链数 | 规则×旋转访问数 | 共享准备数 | 输入前缀复制数 |',
              '|---|---:|---:|---:|---:|']
    for label, value in table.items():
        if label.endswith('/0'):continue
        counts=value['work_counts']
        lines.append('| '+label+' | '+' | '.join(str(statistics.median(c[k] for c in counts))
                     for k in ('chain_rules','chain_rule_rotations','shared_preparations','input_copies'))+' |')
    lines += ['', '正数为配对降时。6→4 比较同布局下动态/固定链分配，4→5 比较前缀缓冲复用；0→4/5 是包含准备组织变化的总体净收益，不将其归因于单项机制。',
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
        if t > 1 and route >= 4:
            d=c['result']; probe=d.get('profile', {})
            if probe.get('chain_rules', 0) <= 0 or probe.get('shared_preparations', 0) <= 0:
                raise ValueError('整链任务或共享准备未实际覆盖')
            if route == 5:
                if probe.get('input_copies') != t * probe['shared_preparations']:
                    raise ValueError('复用路线仍在逐规则复制输入')
                if c['arguments'][4] and probe.get('prefix_checks', 0) != probe.get('chain_rule_rotations'):
                    raise ValueError('缺少原始几何前缀不变性检查')
            elif probe.get('input_copies') != probe.get('chain_rule_rotations'):
                raise ValueError('分配对照的逐规则复制次数不符')
        if fixture is not None:
            d = c['result']
            if d['status'] != (2 if fixture in (3, 5) else 0):
                raise ValueError('生成状态不符合预期；失败退出不能当作生成成功')
            covered = d['front_batches'] > 0
            if covered != (t > 1 and route > 0):
                raise ValueError('候选实际覆盖不符')
            if covered and d['dispatches'] != d['front_batches'] * (1 if route in (1, 4, 5, 6) else 3):
                raise ValueError('完整前沿候选汇合/派发次数不符')
        else:
            d = c['result']
            if t > 1 and route > 0:
                limit, repeats = c['arguments'][3], c['arguments'][5]
                expected_dispatches = 160 * limit * repeats if route in (1, 4, 5, 6) else d['calls']
                if d['dispatches'] != expected_dispatches:
                    raise ValueError('局部候选汇合/派发次数不符')
    return expected


def summarize(blocks):
    if not blocks or any(set(b) != {str(r) for r in ROUTES} for b in blocks):
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


def source_hashes(root):
    sources = [Path(__file__), root/'test_code_expansion/tests/test_rotation_chain_suite.py',
               root/'test_code_expansion/tests/run_rotation_chain_suite.sh',
               root/'test_code_expansion/tests/build_rotation_chain_suite.py',
               root/'test_code_expansion/tests/build_rotation_chain_experiment.py',
               root/'test_code_expansion/tests/rotation_chain_experiment.cpp',
               root/'test_code_expansion/tests/rotation_chain_probe.hpp',
               root/'test_code_expansion/build_project.sh', root/'test_code_expansion/strong_scaling/run_experiments.sh',
               root/'test_code_expansion/strong_scaling/cluster_env.sh', root/'netgen/CMakeLists.txt',
               root/'netgen/libsrc/meshing/ruler3.cpp', root/'netgen/libsrc/meshing/meshing3.cpp']
    return {str(s.relative_to(root)): digest(s) for s in sources}


def load_build(root, build, memory_only=False):
    path=build/"rotation_chain_build.json"
    if not path.is_file():
        raise RuntimeError("尚未编译：请先运行 bash test_code_expansion/build_project.sh")
    manifest=json.loads(path.read_text())
    if not memory_only and manifest["source_sha256"] != source_hashes(root):
        raise RuntimeError("源码已变化，请重新运行 bash test_code_expansion/build_project.sh")
    for kind,suffix in ((("sanitize","_san"),) if memory_only else (("release",""),("profile","_profile"),("sanitize","_san"),("ubsan","_ubsan"))):
        binary=build/("rotation_chain_experiment"+suffix)
        if not binary.is_file() or digest(binary)!=manifest["builds"][kind]["binary_sha256"]:
            raise RuntimeError("实验程序缺失或变化，请重新编译："+str(binary))
    core=build/"libsrc/core/libngcore.so"
    if not core.is_file() or digest(core)!=manifest["core_sha256"]:
        raise RuntimeError("实验运行库缺失或变化，请重新编译")
    return manifest


def main():
    root = Path(__file__).resolve().parents[2]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-dir', type=Path, default=root/'build/rotation_chain_dynamic_v3')
    p.add_argument('--output-dir', type=Path, default=root/'test_code_expansion/strong_scaling_results'/time.strftime('rotation_chain_suite_%Y%m%d-%H%M%S'))
    p.add_argument('--cpus', default='auto', help='auto 使用进程允许的前四个处理器')
    p.add_argument('--blocks', type=int, default=12, help='正式四路线计时块数，至少 4 且为 4 的倍数')
    p.add_argument('--check-build', action='store_true', help='仅检查编译产物，不运行任何实验')
    p.add_argument('--memory-only', action='store_true', help='仅补测指定构建的地址检查，不重跑性能；可指定旧构建目录')
    p.add_argument('--resume', action='store_true', help='仅复用源码、配置和二进制摘要一致的已保存运行')
    a = p.parse_args()
    allowed = sorted(os.sched_getaffinity(0))
    cpus = allowed[:4] if a.cpus == 'auto' else [int(v) for v in a.cpus.split(',')]
    if not a.check_build and (len(set(cpus)) != 4 or not set(cpus) <= set(allowed) or a.blocks < 4 or a.blocks % 4):
        p.error('需要四个不同的可用处理器和至少四个完整计时块')
    build, out = a.build_dir.resolve(), a.output_dir.resolve()
    hashes = source_hashes(root)
    try:
        manifest = load_build(root, build, a.memory_only)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        p.error(str(exc))
    candidate_routes = (1, 2, 3) if a.memory_only and manifest.get('campaign') != 'dynamic_chain_v3' else (4, 5, 6)
    if a.check_build:
        print('指定构建的程序及运行库摘要匹配；仅补内存检查。' if a.memory_only else '已编译，源码及可执行文件匹配；可提交测试。')
        return
    config = dict(build_dir=str(build), cpus=cpus, blocks=a.blocks, memory_only=a.memory_only, binary_source_sha256=manifest['source_sha256'])
    os.sched_setaffinity(0, cpus)
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
        state = dict(schema='rotation_chain_suite_v3', config=config, source_sha256=hashes,
                     platform=platform.platform(), cpu_max=Path('/sys/fs/cgroup/cpu.max').read_text() if Path('/sys/fs/cgroup/cpu.max').exists() else None,
                     started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), complete=False,
                     routes=({0:'原动态队列',1:'旧固定旋转链',2:'旧固定逐旋转',3:'旧批量动态'} if candidate_routes==(1,2,3) else ROUTES), builds={}, validation={}, benchmarks={}, profile_samples={},
                     expected_run_count=72 if a.memory_only else 348+8*4*(a.blocks+1)+24)
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

    def prepared_binary(kind):
        binary = build/('rotation_chain_experiment'+dict(release='', profile='_profile', sanitize='_san', ubsan='_ubsan')[kind])
        value = manifest['builds'][kind]
        if kind in state['builds'] and state['builds'][kind]['binary_sha256'] != value['binary_sha256'] and any(c['kind'] == kind for c in cache.values()):
            raise RuntimeError('二进制变化，禁止混用已保存样本；请使用新输出目录')
        state['builds'][kind] = value
        save()
        return binary

    def run(key, kind, binary, t, route, variant=0, limit=7, verify=1, repeats=2, fixture=None):
        args = [t, route, variant, limit, verify, repeats]
        if fixture is not None:
            args.append(fixture)
        attempt=1
        if key in cache:
            record = cache[key]
            if record['kind'] != kind or record['arguments'] != args or record['binary_sha256'] != digest(binary):
                raise RuntimeError('缓存输入或二进制不一致')
            if shadow_memory_failure(record) and a.resume:
                attempt=record.get('attempt', 1)+1
            else:
                check_run(record)
                return record
        env = dict(os.environ)
        if kind in ('sanitize', 'ubsan'):
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
        except OSError as exc:
            stdout='';stderr=str(exc);code=127
        try:
            data = json.loads(stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            data = None
        record = dict(key=key, attempt=attempt, kind=kind, arguments=args, result=data, returncode=code, stderr=stderr,
                      memory_limits=memory_limits(),
                      process_seconds=time.monotonic()-start, binary_sha256=state['builds'][kind]['binary_sha256'],
                      cpu_stat_before=cpu_before,cpu_stat_after=cpu_stat.read_text() if cpu_stat.exists() else None)
        if code or data is None:
            record['stdout'] = stdout
        with raw_path.open('a') as stream:
            stream.write(json.dumps(record, ensure_ascii=False)+'\n'); stream.flush(); os.fsync(stream.fileno())
        cache[key] = record
        if len(cache) % 25 == 0:
            print('PROGRESS', len(cache), '/', state['expected_run_count'], key, flush=True)
        if kind == 'sanitize' and shadow_memory_failure(record):
            raise AddressSpaceUnavailable('地址检查器无法预留影子虚拟地址空间；限制已记录，未更改系统或作业限制')
        check_run(record)
        if data['profile']['enabled'] != (kind == 'profile'):
            raise RuntimeError('分项测量构建与正式计时混用')
        return record

    def validate(kind, binary):
        routes = [(1, -1), (4, 0)] + [(t, route) for route in candidate_routes for t in ((1, 2, 4) if kind == 'release' else (2, 4))]
        for limit in ((7, 45) if kind == 'release' else (45,)):
            for variant in (0, 1, 2):
                key = f'{kind}/local/{limit}/{variant}'
                cases = [run(f'{key}/{t}/{route}', kind, binary, t, route, variant, limit) for t, route in routes]
                fp = check_group(cases)
                state['validation'][key] = dict(passed=True, runs=[c['key'] for c in cases], fingerprint=fp)
                save()
        for fixture in range(6):
            key = f'{kind}/front/{fixture}'
            cases = [run(f'{key}/{t}/{route}', kind, binary, t, route, verify=1, fixture=fixture) for t, route in routes]
            fp = check_group(cases, fixture)
            item = dict(passed=True, runs=[c['key'] for c in cases], fingerprint=fp,
                        status=cases[0]['result']['status'], elements=cases[0]['result']['elements'])
            if kind != 'release' and f'release/front/{fixture}' in state['validation']:
                item['cross_build_equal'] = fp == state['validation'][f'release/front/{fixture}']['fingerprint']
            state['validation'][key] = item
            save()
        print('PASS differential', kind, 'groups', sum(k.startswith(kind+'/') for k in state['validation']), flush=True)

    def benchmark(binary, label, variant=0, limit=7, fixture=None):
        blocks, orders, keys = [], [], []
        for block in range(-1, a.blocks):
            # Four positions are balanced in each group of four blocks.
            order = list(ROUTES)
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
        state['environment'] = dict(scheduler_job=os.environ.get('SLURM_JOB_ID'),
                                    build_environment=manifest['environment'], memory_limits=memory_limits())
        save()
        sanitizer = prepared_binary('sanitize')
        state['address_check'] = dict(status='pending')
        try:
            run('sanitize/local/45/0/1/-1', 'sanitize', sanitizer, 1, -1, limit=45)
        except AddressSpaceUnavailable as exc:
            state['address_check'] = dict(status='blocked', reason=str(exc))
            print('ADDRESS CHECK BLOCKED:', exc, flush=True)
        save()
        if a.memory_only:
            if state['address_check']['status'] != 'blocked':
                try:
                    validate('sanitize', sanitizer)
                    state['address_check']['status']='passed'
                except AddressSpaceUnavailable as exc:
                    state['address_check']=dict(status='blocked',reason=str(exc))
            state['run_count']=len(cache);state['raw_sha256']=digest(raw_path)
            state['complete']=state['address_check']['status']=='passed'
            if state['complete'] and state['run_count'] != state['expected_run_count']:
                raise RuntimeError('内存补测运行数量与计划不一致')
            state['attempt_count']=len(raw_path.read_text().splitlines())
            state['finished_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            save()
            if not state['complete']:raise SystemExit(3)
            print('MEMORY CHECK COMPLETE', summary_path, flush=True)
            return
        command('driver_tests', [sys.executable, '-m', 'unittest', 'discover', '-s', root/'test_code_expansion/tests', '-p', 'test_rotation_chain_suite.py', '-v'])
        release = prepared_binary('release'); validate('release', release)
        ubsan = prepared_binary('ubsan'); validate('ubsan', ubsan)
        for limit in (7, 45):
            for variant in (0, 1, 2):
                benchmark(release, f'local/{limit}/{variant}', variant, limit)
        for fixture in (2, 4):
            benchmark(release, f'front/{fixture}', fixture=fixture)
        profile = prepared_binary('profile'); validate('profile', profile)
        for fixture in (2, 4):
            for route in ROUTES:
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
        state['performance_complete']=True
        if state['address_check']['status'] != 'blocked':
            try:
                validate('sanitize', sanitizer)
                state['address_check']['status']='passed'
            except AddressSpaceUnavailable as exc:
                state['address_check']=dict(status='blocked',reason=str(exc))
        assert all(digest(root/path) == value for path,value in hashes.items()), '运行期间源码发生变化'
        state['run_count'] = len(cache)
        if state['address_check']['status']=='passed' and state['run_count'] != state['expected_run_count']:
            raise RuntimeError('实际运行数量与计划不一致')
        state['raw_sha256'] = digest(raw_path)
        state['complete'] = state['address_check']['status']=='passed'
        state['attempt_count']=len(raw_path.read_text().splitlines())
        state['finished_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        save()
        if not state['complete']:
            print('PARTIAL: 性能及独立未定义行为检查完成，地址检查受阻；无需重跑性能。', summary_path, flush=True)
            raise SystemExit(3)
        print('ALL COMPLETE', summary_path, 'runs', len(cache), flush=True)
    except Exception as exc:
        state['error'] = str(exc); state['run_count'] = len(cache); save()
        (out/'failure.txt').write_text(str(exc)+'\n')
        print('FAILED', out/'summary.json', str(exc), flush=True)
        raise


if __name__ == '__main__':
    main()
