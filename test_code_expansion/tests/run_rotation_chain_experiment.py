#!/usr/bin/env python3
"""隔离旋转链：差分先行，交错配对计时；逐次保存全部样本，不提交集群任务。"""
import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import time
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--binary', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--phase', choices=('local', 'front', 'sanitize'), required=True)
p.add_argument('--reference', type=Path, help='sanitize 所需：含 local/front 两份结果路径的 JSON')
p.add_argument('--cpus', default='0,1,2,3')
p.add_argument('--pairs', type=int, default=10)
a = p.parse_args()
binary = a.binary.resolve()
cpus = [int(x) for x in a.cpus.split(',')]
if len(set(cpus)) != 4 or not set(cpus) <= os.sched_getaffinity(0) or a.pairs < 1:
    raise SystemExit('需要四个不同的可用处理器核心和正样本数')
if a.phase == 'sanitize' and not a.reference:
    raise SystemExit('sanitize 需要 --reference')
os.sched_setaffinity(0, cpus)
repo = Path(__file__).resolve().parents[2]
paths = ['test_code_expansion/tests/rotation_chain_experiment.cpp',
         'test_code_expansion/tests/build_rotation_chain_experiment.py',
         'test_code_expansion/tests/run_rotation_chain_experiment.py',
         'netgen/libsrc/meshing/ruler3.cpp', 'netgen/libsrc/meshing/meshing3.cpp']


def text_file(path):
    return Path(path).read_text() if Path(path).exists() else None


r = dict(schema='rotation_chain_experiment_v2', phase=a.phase, cpus=cpus, pairs=a.pairs,
         utc_started=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
         platform=platform.platform(),
         binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
         build=json.loads(binary.with_suffix('.build.json').read_text()),
         source_sha256={s: hashlib.sha256((repo / s).read_bytes()).hexdigest() for s in paths},
         cpu_max=text_file('/sys/fs/cgroup/cpu.max'), validation=[], benchmarks=[], complete=False)
a.output.parent.mkdir(parents=True, exist_ok=True)


def save():
    temporary = a.output.with_suffix(a.output.suffix + '.tmp')
    temporary.write_text(json.dumps(r, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(a.output)


def run(threads, route, variant=0, limit=7, verify=1, repeats=2, fixture=None):
    args = [str(binary), *map(str, (threads, route, variant, limit, verify, repeats))]
    if fixture is not None:
        args.append(str(fixture))
    before = text_file('/sys/fs/cgroup/cpu.stat')
    start = time.monotonic()
    env = dict(os.environ)
    if a.phase == 'sanitize':
        env['ASAN_OPTIONS'] = 'detect_leaks=0:halt_on_error=1'
        env['UBSAN_OPTIONS'] = 'halt_on_error=1'
    result = subprocess.run(args, capture_output=True, text=True, timeout=180, env=env)
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        data = None
    record = dict(arguments=args[1:], result=data, returncode=result.returncode,
                  process_seconds=time.monotonic() - start, cpu_stat_before=before,
                  cpu_stat_after=text_file('/sys/fs/cgroup/cpu.stat'), stderr=result.stderr)
    if result.returncode or data is None:
        r['failed_run'] = dict(record, stdout=result.stdout)
        save()
        raise SystemExit('运行失败；原始输出已保存')
    return record


def validate(cases, **keys):
    expected = cases[0]['result']['fingerprint']
    passed = all(c['result']['fingerprint'] == expected for c in cases)
    if 'fixture' in keys:
        fixture = keys['fixture']
        # Fixture 3 is deliberately too strict. Fixture 5 also fails on the
        # serial baseline (preserved precheck); neither is a speed benchmark.
        keys['purpose'] = 'failure_exit_equivalence' if fixture in (3, 5) else 'successful_generation'
        passed &= all(c['result']['status'] == (2 if fixture in (3, 5) else 0) for c in cases)
        for c in cases:
            t, route = map(int, c['arguments'][:2])
            passed &= (c['result']['front_batches'] > 0) if t > 1 and route == 1 else (c['result']['front_batches'] == 0)
    r['validation'].append(dict(keys, cases=cases, passed=passed))
    save()
    if not passed:
        raise SystemExit('差分、退出状态或候选覆盖检查失败；不继续计时')
    return expected


def benchmark(case, invoke, expected=None):
    case.update(warmup={}, samples=[])
    r['benchmarks'].append(case)
    for pair in range(-1, a.pairs):
        order = (0, 1) if pair % 2 == 0 else (1, 0)
        values = {route: invoke(route) for route in order}
        sample = dict(pair=pair, order=list(order), control=values[0], candidate=values[1])
        c, q = [values[x]['result']['seconds'] for x in (0, 1)]
        sample['reduction_pct'] = 100 * (c - q) / c
        if pair == -1:
            case['warmup'] = sample
        else:
            case['samples'].append(sample)
        save()
        if expected is not None and (any(v['result']['fingerprint'] != expected or v['result']['status'] != 0
                                        for v in values.values()) or values[1]['result']['front_batches'] == 0):
            raise SystemExit('计时样本的网格结果或候选覆盖检查失败')
    case['paired_median_pct'] = statistics.median(s['reduction_pct'] for s in case['samples'])
    case['wins'] = sum(s['reduction_pct'] > 0 for s in case['samples'])
    for name in ('control', 'candidate'):
        case[name + '_median_seconds'] = statistics.median(s[name]['result']['seconds'] for s in case['samples'])
    save()
    print({k: v for k, v in case.items() if k not in ('samples', 'warmup')}, flush=True)


save()
if a.phase == 'local':
    for limit in (7, 45):
        for variant in (0, 1, 2):
            cases = [run(1, -1, variant, limit), run(4, 0, variant, limit)]
            cases += [run(t, 1, variant, limit) for t in (1, 2, 4)]
            validate(cases, limit=limit, variant=variant)
    print('PASS local differential: 30', flush=True)
    for limit in (7, 45):
        for variant in (0, 1, 2):
            repeats = 10 if limit == 7 else 2
            benchmark(dict(limit=limit, variant=variant, repeats=repeats),
                      lambda route: run(4, route, variant, limit, 0, repeats))
elif a.phase == 'front':
    fingerprints = {}
    for fixture in range(6):
        # Always hash the final mesh and work counts. Diagnostic string assembly
        # is excluded from this full-front timing on both paths.
        cases = [run(1, -1, verify=0, fixture=fixture), run(4, 0, verify=0, fixture=fixture)]
        cases += [run(t, 1, verify=0, fixture=fixture) for t in (1, 2, 4)]
        fingerprints[fixture] = validate(cases, fixture=fixture)
        print('PASS front differential:', fixture, cases[0]['result'], flush=True)
    for fixture in (2, 4):
        benchmark(dict(fixture=fixture), lambda route: run(4, route, verify=0, fixture=fixture),
                  fingerprints[fixture])
else:
    references = json.loads(a.reference.read_text())
    reference_data = {k: json.loads(Path(v).read_text()) for k, v in references.items()}
    for key in ('local', 'front'):
        d = reference_data[key]
        if not d['complete'] or d['source_sha256'] != r['source_sha256']:
            raise SystemExit('参考结果未完成或源码不一致')
    r['reference_sha256'] = {k: hashlib.sha256(Path(v).read_bytes()).hexdigest() for k, v in references.items()}
    r['sanitizer_options'] = dict(ASAN_OPTIONS='detect_leaks=0:halt_on_error=1', UBSAN_OPTIONS='halt_on_error=1')
    checks = []
    for v in reference_data['local']['validation']:
        if v['limit'] == 45:
            for t, route in ((1, -1), (2, 1), (4, 1)):
                checks.append((dict(threads=t, route=route, variant=v['variant'], limit=45), v))
    for v in reference_data['front']['validation']:
        checks.append((dict(threads=4, route=1, verify=0, fixture=v['fixture']), v))
    for args, v in checks:
        actual = run(**args)
        expected = v['cases'][0]['result']
        passed = actual['result']['fingerprint'] == expected['fingerprint']
        if 'fixture' in args:
            passed &= actual['result']['status'] == expected['status'] and actual['result']['front_batches'] > 0
        passed &= 'runtime error:' not in actual['stderr'] and 'ERROR: AddressSanitizer' not in actual['stderr']
        r['validation'].append(dict(arguments=args, actual=actual, passed=passed))
        save()
        if not passed:
            raise SystemExit('内存检查结果不一致或报告错误')
        print('PASS sanitizer:', args, flush=True)
r['complete'] = True
r['utc_finished'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
save()
