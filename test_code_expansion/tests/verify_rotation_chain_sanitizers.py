#!/usr/bin/env python3
"""同一插桩构建内比较串行与候选；单独记录跨构建差异，不将其当作线程差异。"""
import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--binary', type=Path, required=True)
p.add_argument('--local', type=Path, required=True)
p.add_argument('--front', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--cpus', default='0,1,2,3')
a = p.parse_args()
root = Path(__file__).resolve().parents[2]
cpus = [int(x) for x in a.cpus.split(',')]
if len(set(cpus)) != 4 or not set(cpus) <= os.sched_getaffinity(0):
    raise SystemExit('需要四个不同的可用处理器')
os.sched_setaffinity(0, cpus)
refs = {k: json.loads(getattr(a, k).read_text()) for k in ('local', 'front')}
for ref in refs.values():
    assert ref['complete'] and all(v['passed'] for v in ref['validation'])
    for path, digest in ref['source_sha256'].items():
        assert hashlib.sha256((root/path).read_bytes()).hexdigest() == digest
source_hashes = dict(refs['local']['source_sha256'])
source_hashes[str(Path(__file__).resolve().relative_to(root))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
r = dict(schema='rotation_chain_sanitizer_v2', complete=False, cpus=cpus,
         source_sha256=source_hashes, binary_sha256=hashlib.sha256(a.binary.read_bytes()).hexdigest(),
         build=json.loads(a.binary.with_suffix('.build.json').read_text()),
         reference_sha256={k: hashlib.sha256(getattr(a, k).read_bytes()).hexdigest() for k in refs},
         options=dict(ASAN_OPTIONS='detect_leaks=0:halt_on_error=1', UBSAN_OPTIONS='halt_on_error=1'),
         validation=[], benchmarks=[], utc_started=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))


def save():
    temporary = a.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(r, ensure_ascii=False, indent=2)+'\n')
    temporary.replace(a.output)


def run(threads, route, variant=0, limit=7, fixture=None):
    args = [threads, route, variant, limit, int(fixture is None), 2]
    if fixture is not None:
        args.append(fixture)
    start = time.monotonic()
    result = subprocess.run([str(a.binary.resolve()), *map(str, args)], capture_output=True,
                            text=True, timeout=180, env=dict(os.environ, **r['options']))
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        data = None
    record = dict(arguments=args, result=data, returncode=result.returncode, stdout=result.stdout,
                  stderr=result.stderr, process_seconds=time.monotonic()-start)
    if result.returncode or data is None or 'runtime error:' in result.stderr or 'ERROR: AddressSanitizer' in result.stderr:
        r['failed_run'] = record
        save()
        raise SystemExit('插桩运行失败；原始输出已保存')
    return record


save()
for category in ('local', 'front'):
    for ref in refs[category]['validation']:
        if category == 'local' and ref['limit'] != 45:
            continue
        keys = dict(variant=ref['variant'], limit=45) if category == 'local' else dict(fixture=ref['fixture'])
        routes = [(1, -1), (2, 1), (4, 1)] if category == 'local' else [(1, -1), (4, 0), (2, 1), (4, 1)]
        cases = [run(t, route, **keys) for t, route in routes]
        serial = cases[0]['result']
        passed = all(c['result']['fingerprint'] == serial['fingerprint'] for c in cases)
        if category == 'front':
            passed &= all(c['result']['status'] == serial['status'] for c in cases)
            for c in cases:
                t, route = c['arguments'][:2]
                passed &= (c['result']['front_batches'] > 0) if route == 1 else c['result']['front_batches'] == 0
        baseline = ref['cases'][0]['result']
        r['validation'].append(dict(category=category, input=keys, cases=cases, passed=passed,
                                    release_baseline=baseline,
                                    cross_build_fingerprint_equal=serial['fingerprint'] == baseline['fingerprint']))
        save()
        if not passed:
            raise SystemExit('同一插桩构建中的串行/候选差分失败')
        print('PASS', category, keys, 'runs', len(cases), 'cross_build_equal', serial['fingerprint'] == baseline['fingerprint'], flush=True)
r['complete'] = True
r['utc_finished'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
save()
