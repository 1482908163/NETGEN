#!/usr/bin/env python3
"""Map failure frames before rebuilding, only against hash-matched ELF files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time

FRAME=re.compile(r'(?P<path>/[^\n]+?)\((?P<location>[^()\n]*)\)\s*\[(?P<address>0x[0-9a-fA-F]+)\]')
HASH=re.compile(r'^[0-9a-f]{64}$')
SYMBOL=re.compile(r'^(.*)\+0x([0-9a-fA-F]+)$')

def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def executable_type(path):
    with path.open('rb') as stream:header=stream.read(20)
    if len(header)<20 or header[:4]!=b'\x7fELF':return None
    return struct.unpack(('<' if header[5]==1 else '>')+'H',header[16:18])[0]

def command(args,timeout=8):
    try:
        result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
        return dict(command=args,exit_code=result.returncode,
                    stdout=result.stdout[:1048576],stderr=result.stderr[:8192])
    except (OSError,subprocess.TimeoutExpired) as error:
        return dict(command=args,error=str(error))

def configuration(run):
    for parent in (run,)+tuple(run.parents):
        file=parent/'configuration.txt'
        if file.is_file():
            values={}
            for line in file.read_text(errors='replace').splitlines():
                if '=' in line:
                    key,value=line.split('=',1);values[key]=value
                match=re.match(r'^([0-9a-f]{64})\s+[* ]?(.*)$',line)
                if match:values['file:'+str(Path(match[2]).resolve())]=match[1]
            return values
    return {}

def scheduler(run):
    job=os.environ.get('SLURM_JOB_ID')
    for parent in (run,)+tuple(run.parents):
        jobs=parent/'submitted_jobs.tsv'
        if not jobs.is_file():continue
        ranks=next((p.name[1:] for p in run.parents if re.fullmatch(r'p\d+',p.name)),None)
        if not job and ranks:
            for line in jobs.read_text().splitlines():
                fields=line.split()
                if len(fields)>1 and fields[0]==ranks:job=fields[1].split(';')[0]
        directory=parent/f'p{ranks}' if ranks else parent
        cache=directory/'scheduler_diagnostics.json'
        if cache.exists():
            try:
                if json.loads(cache.read_text()).get('job_id')==job:return str(cache)
            except (OSError,ValueError):pass
        if not job or not re.fullmatch(r'\d+',job):return None
        report=dict(schema='scheduler_snapshot_v1',job_id=job,time=time.time(),
                    note='Read during the job; state/reason may not be final.')
        for program,args in (
            ('sacct',['-j',job,'--format=JobID,State,ExitCode,NodeList,Reason,MaxRSS,ReqMem','-P']),
            ('scontrol',['show','job',job])):
            report[program]=command([program]+args) if shutil.which(program) else dict(unavailable=True)
        directory.mkdir(parents=True,exist_ok=True)
        cache.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        return str(cache)
    return None

def diagnose(run,binary,kernel,core,expected=None):
    run=Path(run);config=configuration(run);expected=expected or {}
    files={'binary':Path(binary),'kernel':Path(kernel),'core':Path(core)}
    identities={}
    for name,path in files.items():
        recorded=expected.get(name) or config.get(
            {'binary':'MESH_BINARY_SHA256','kernel':'MESH_KERNEL_SHA256','core':'MESH_NGCORE_SHA256'}[name])
        if not recorded:recorded=config.get('file:'+str(path.resolve()))
        item=dict(path=str(path.resolve()),expected_sha256=recorded)
        if path.is_file():
            item['actual_sha256']=sha256(path)
            item['matched']=bool(recorded and HASH.fullmatch(recorded) and recorded==item['actual_sha256'])
        else:item.update(matched=False,unavailable=True)
        identities[name]=item
    sources=[p for p in (run/'node_coop_stderr.txt',run/'run.log') if p.is_file()]
    # Bound log parsing, retaining both startup and root-error tails.
    text=''
    for source in sources:
        with source.open('rb') as stream:
            first=stream.read(2*1024*1024)
            if source.stat().st_size>4*1024*1024:
                stream.seek(-2*1024*1024,2);first+=b'\n[log middle omitted]\n'+stream.read()
            else:first+=stream.read()
        text+=first.decode('utf8',errors='replace')+'\n'
    frames=[];seen=set();symbols={}
    for match in FRAME.finditer(text):
        logged=Path(match['path'].strip())
        # Never replace an unrelated library solely because its basename agrees.
        name=next((n for n,p in files.items() if p.resolve()==logged.resolve()),None)
        if name is None:continue
        location=match['location'];key=(name,location)
        if key in seen:continue
        seen.add(key);frame=dict(object=name,logged_path=str(logged),location=location)
        frames.append(frame)
        if not identities[name]['matched']:
            frame['skipped']='ELF unavailable, expected hash absent, or hash mismatch';continue
        offset=None
        if re.fullmatch(r'\+0x[0-9a-fA-F]+',location):
            # DYN (shared library/PIE): offset is ELF-relative; EXEC: use the
            # reported fixed virtual address, not the module-relative offset.
            kind=executable_type(files[name])
            if kind==3:offset=int(location[1:],16)
            elif kind==2:offset=int(match['address'],16)
        else:
            symbol=SYMBOL.fullmatch(location)
            if symbol:
                if name not in symbols:
                    result=command(['nm','-D','--defined-only',str(files[name])])
                    symbols[name]={}
                    for line in result.get('stdout','').splitlines():
                        parts=line.split()
                        if len(parts)>=3 and re.fullmatch(r'[0-9a-fA-F]+',parts[0]):
                            symbols[name][parts[2].split('@')[0]]=int(parts[0],16)
                address=symbols[name].get(symbol[1])
                if address is not None:offset=address+int(symbol[2],16)
        if offset is None:
            frame['skipped']='No resolvable ELF-relative offset';continue
        frame['offset']=hex(offset)
    # One bounded addr2line process per object, regardless of the frame count.
    # Address markers keep inline frames attached to the correct query.
    for name,path in files.items():
        selected=[frame for frame in frames if frame['object']==name and 'offset' in frame]
        if not selected:continue
        offsets=list(dict.fromkeys(frame['offset'] for frame in selected))
        result=command(['addr2line','-a','-C','-f','-i','-e',str(path)]+offsets)
        mapped={};current=None
        for line in result.get('stdout','').splitlines():
            if re.fullmatch(r'0x[0-9a-fA-F]+',line):
                current=hex(int(line,16));mapped[current]=[]
            elif current is not None:mapped[current].append(line)
        for frame in selected:
            resolution=dict(result);resolution['stdout']='\n'.join(mapped.get(frame['offset'],[]))
            frame['resolution']=resolution
            frame['has_source_line']=bool(re.search(r':([1-9]\d*)(?:\s|$)',resolution['stdout']))
    report=dict(schema='failure_symbols_v1',run=str(run),time=time.time(),identities=identities,
        source_revision=config.get('SOURCE_REVISION') or config.get('MESH_SOURCE_REVISION'),
        root_messages=[line for line in text.splitlines() if re.search(r'Caught signal|double free|corruption|free\(\):|invalid pointer|adjacency local vertex',line)][:80],
        sources=[str(p) for p in sources],frames=frames,scheduler_snapshot=scheduler(run),
        note='Frames and scheduler snapshots are evidence, not a root-cause or OOM diagnosis.')
    (run/'failure_symbols.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['failure_symbols_v1',str(run)]
    for name,item in identities.items():lines.append(f"{name}: hash matched={item['matched']}; expected={item['expected_sha256']}; actual={item.get('actual_sha256','unavailable')}")
    for frame in frames:
        lines.extend([f"{frame['object']} ({frame['location']})",
                      frame.get('skipped') or frame['resolution'].get('stdout') or frame['resolution'].get('error','No mapping')])
    (run/'failure_symbols.txt').write_text('\n'.join(lines)+'\n')
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--run',type=Path);group.add_argument('--previous-root',type=Path)
    parser.add_argument('--binary',required=True);parser.add_argument('--kernel',required=True)
    parser.add_argument('--core',required=True)
    for name in ('binary','kernel','core'):parser.add_argument('--expected-'+name)
    args=parser.parse_args();runs=[]
    if args.run:runs=[args.run]
    elif args.previous_root.exists():
        for batch in sorted(args.previous_root.glob('mesh_algorithms_*'),reverse=True):
            runs=sorted({p.parent for p in batch.rglob('failure_reason.txt')})
            if runs:break
    expected={name:getattr(args,'expected_'+name) for name in ('binary','kernel','core')}
    mapped=0
    for run in runs:
        report=diagnose(run,args.binary,args.kernel,args.core,expected)
        mapped+=sum(f.get('has_source_line',False) for f in report['frames'])
    print(f'Failure diagnostics: {len(runs)} runs; {mapped} source-line frames. Unmatched ELF files are not mapped.')

if __name__=='__main__':main()
