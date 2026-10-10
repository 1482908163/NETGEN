#!/usr/bin/env python3
"""Freeze the shared scripts used by a batch before submitting any jobs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

REQUIRED=('run_experiments.sh','submit_experiments.sh','cluster_env.sh',
          'node_affinity_yhrun.sh','analyze_results.py','analyze_worklet_routes.py',
          'cost_profile_checks.py','front_component_checks.py','incidence_checks.py','combine_commit_checks.py','diagnose_failures.py',
          'fit_cost_model.py','resource_model.py','runtime_snapshot.py')

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def verify(snapshot,manifest,expected_sha=None):
    snapshot=Path(snapshot);manifest=Path(manifest)
    actual=digest(manifest)
    if expected_sha and actual!=expected_sha:
        raise ValueError('batch runtime manifest hash changed')
    report=json.loads(manifest.read_text())
    if report.get('schema')!='mesh_runtime_snapshot_v1':
        raise ValueError('unknown batch runtime manifest')
    if str(snapshot.resolve())!=report['snapshot_directory']:
        raise ValueError('batch runtime directory changed')
    names=set(report['files'])
    if not set(REQUIRED)<=names:
        raise ValueError('batch runtime lacks required dependencies')
    for name,sha in report['files'].items():
        if Path(name).name!=name:
            raise ValueError('invalid batch runtime member')
        path=snapshot/name
        if not path.is_file() or path.is_symlink() or digest(path)!=sha:
            raise ValueError('batch runtime member missing or changed: '+name)
    return actual

def capture(source,snapshot,manifest,project,revision):
    source=Path(source).resolve();snapshot=Path(snapshot).resolve()
    manifest=Path(manifest).resolve();project=Path(project).resolve()
    if snapshot.exists() or manifest.exists():
        sha=verify(snapshot,manifest)
        old=json.loads(manifest.read_text())
        if (old['source_directory'],old['project_root'],old['source_revision'])!=(str(source),str(project),revision):
            raise ValueError('existing batch runtime belongs to another source/configuration; use a new RUN_ROOT')
        return sha
    missing=[name for name in REQUIRED if not (source/name).is_file()]
    if missing:
        raise ValueError('source dependencies missing before submission: '+', '.join(missing))
    snapshot.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='runtime_scripts.',dir=snapshot.parent))
    try:
        hashes={}
        for path in sorted(source.iterdir()):
            if path.suffix not in ('.py','.sh') or not path.is_file():continue
            target=staging/path.name;data=path.read_bytes()
            target.write_bytes(data)
            mode=path.stat().st_mode & 0o777
            if path.suffix=='.sh':mode|=0o111
            target.chmod(mode & ~0o222)
            hashes[path.name]=hashlib.sha256(data).hexdigest()
        if not set(REQUIRED)<=set(hashes):
            raise ValueError('source dependencies changed while capturing runtime')
        report=dict(schema='mesh_runtime_snapshot_v1',source_directory=str(source),
                    snapshot_directory=str(snapshot),project_root=str(project),
                    source_revision=revision,files=hashes)
        staging.rename(snapshot)
        manifest.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
        return verify(snapshot,manifest)
    finally:
        if staging.exists():shutil.rmtree(staging)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('capture','verify'))
    parser.add_argument('--snapshot',required=True)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--source');parser.add_argument('--project')
    parser.add_argument('--revision');parser.add_argument('--expected-sha')
    args=parser.parse_args()
    try:
        if args.action=='capture':
            if not all((args.source,args.project,args.revision)):parser.error('capture requires source, project and revision')
            sha=capture(args.source,args.snapshot,args.manifest,args.project,args.revision)
        else:sha=verify(args.snapshot,args.manifest,args.expected_sha)
    except (OSError,ValueError,KeyError) as error:
        parser.exit(2,'Runtime snapshot: '+str(error)+'\n')
    print(sha)

if __name__=='__main__':main()
