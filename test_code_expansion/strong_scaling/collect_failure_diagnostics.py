#!/usr/bin/env python3
"""导出已有失败轮的质量分类、出错进程和调用位置；不运行网格、不修改成功/退出状态。"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def quality_details(q):
    failed=[]
    for row in q.get('per_rank',[]):
        if row.get('hard_errors',0) or row.get('checked')!=row.get('elements'):
            failed.append(dict(row))  # Keep fingerprints: they distinguish geometry from audit failures.
    return dict(identity=q.get('identity'),counts=q.get('counts'),issues=q.get('issues'),
                structural_pass=q.get('structural_pass'),failed_ranks=failed,
                per_rank_available='per_rank' in q)


def file_hash(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def resolve_frames(text, configuration, cache):
    # Only read binaries named in the run's SHA-256 configuration record. Never
    # execute them or resolve old addresses against a rebuilt executable.
    records={m[2]:m[1] for m in re.finditer(r'^([0-9a-f]{64})  (.+)$',configuration,re.M)}
    frames=[]
    for match in re.finditer(r'^\s*\d+\s+(\S+)\(([^)]*)\)\s+\[(0x[0-9a-fA-F]+)\]',text,re.M):
        name,offset,address=match.groups()
        if name not in records:continue
        path=Path(name);item=dict(binary=name,address=address,expected_sha256=records[name])
        if not path.is_file():item['status']='binary_unavailable'
        else:
            if name not in cache:cache[name]=file_hash(path)
            digest=cache[name]
            if digest!=records[name]:item['status']='binary_hash_mismatch'
            elif not shutil.which('addr2line'):item['status']='addr2line_unavailable'
            else:
                with path.open('rb') as f:header=f.read(18)
                if len(header)<18 or header[:4]!=b'\x7fELF' or header[5] not in (1,2):
                    item['status']='not_supported_elf';frames.append(item);continue
                kind=int.from_bytes(header[16:18],'little' if header[5]==1 else 'big')
                if kind==3:
                    if not re.fullmatch(r'\+0x[0-9a-fA-F]+',offset):
                        item['status']='pie_base_unavailable';frames.append(item);continue
                    address=offset[1:]
                elif kind!=2:
                    item['status']='not_executable_elf';frames.append(item);continue
                try:
                    proc=subprocess.run(['addr2line','-f','-C','-e',str(path),address],
                                        text=True,capture_output=True,timeout=15)
                    lines=proc.stdout.strip().splitlines()
                    status=('resolved' if '??' not in proc.stdout else
                            'function_only' if lines and lines[0] not in ('??','?') else 'symbols_unavailable')
                    item.update(status=status if proc.returncode==0 and lines else 'symbols_unavailable',
                                location=proc.stdout.strip()[:4096])
                except (OSError,subprocess.TimeoutExpired) as e:
                    item.update(status='resolver_failed',error=str(e))
        frames.append(item)
    return frames


def collect(directory, cache=None, exit_code=None):
    cache={} if cache is None else cache
    qpath=directory/'quality_summary.json';summary=directory/'failure_summary.txt'
    data=dict(schema='failure_diagnostics_v1',exit_code=exit_code)
    if qpath.exists():
        try:data['quality']=quality_details(json.loads(qpath.read_text()))
        except (ValueError,OSError) as e:data['quality_read_error']=str(e)
    else:data['quality_read_error']='quality_summary.json unavailable; no per-rank error category can be inferred'
    # Search the complete log, keeping only explicit diagnostic lines and the
    # observed exit-stage rank sets. A long finalization tail must not hide errors.
    candidates=[directory/'run.log',directory/'run.log.gz',summary]
    log=next((p for p in candidates if p.is_file()),None)
    stages={};diagnostics=[];tail='';checkpoints={};evidence=[];evidence_dropped=0;malformed=0
    affected={r['rank'] for r in data.get('quality',{}).get('failed_ranks',[]) if 'rank' in r}
    if log:
        opener=gzip.open if log.suffix=='.gz' else open
        with opener(log,'rt',errors='replace') as stream:
            for line in stream:
                tail=(tail+line)[-32768:]
                m=re.search(r'\[EXIT_CHECKPOINT\] rank=(\d+) stage=([\w.]+)',line)
                if m:stages.setdefault(m[2],set()).add(int(m[1]))
                if '[MESH_EVIDENCE] ' in line:
                    try:
                        record=json.loads(line.split('[MESH_EVIDENCE] ',1)[1])
                        if not isinstance(record,dict) or not isinstance(record.get('rank'),int):raise ValueError('rank')
                        rank=record['rank']
                        if record.get('kind')=='checkpoint':
                            checkpoints.setdefault(rank,[]).append(record)
                            if record.get('invalid') or record.get('unsupported') or record.get('unchanged') is False:
                                affected.add(rank)
                        else:
                            affected.add(rank)
                            if len(evidence)<128:evidence.append(record)
                            else:evidence_dropped+=1
                    except (ValueError,TypeError):malformed+=1
                    continue  # Progress lines must not exhaust the error/stack budget.
                if re.search(r'Caught signal|Segmentation fault|\[MESH_|\[IO_ERROR\]|\[MPI_ERROR\]|^\s*\d+\s+\S+\([^)]*\)\s+\[0x',line):
                    if len(diagnostics)<128:diagnostics.append(line.strip()[:2048])
        data['log_source']=log.name
    if exit_code is None and summary.exists():
        m=re.search(r'^退出码：(\d+)',summary.read_text(errors='replace'))
        if m:data['exit_code']=int(m[1])
    counts={k:len(v) for k,v in stages.items()}
    if log==summary:
        # The saved header was counted from the full log, unlike its truncated tail.
        m=re.search(r'^收尾各阶段已观测进程数：(.*)$',summary.read_text(errors='replace'),re.M)
        if m:
            try:
                saved=json.loads(m[1])
                if isinstance(saved,dict) and all(isinstance(v,int) and v>=0 for v in saved.values()):counts=saved
            except ValueError:pass
    checkpoint_counts={}
    for rows in checkpoints.values():
        for row in rows:
            stage=row.get('stage','unknown');checkpoint_counts[stage]=checkpoint_counts.get(stage,0)+1
    data.update(exit_checkpoints=counts,messages=diagnostics,
                mesh_evidence=evidence,mesh_evidence_dropped=evidence_dropped,mesh_evidence_malformed=malformed,
                mesh_checkpoint_counts=checkpoint_counts,
                mesh_checkpoints=[row for rank,rows in sorted(checkpoints.items()) if rank in affected for row in rows])
    configuration=directory.parent.parent/'configuration.txt'
    data['frames']=resolve_frames('\n'.join(diagnostics),configuration.read_text() if configuration.exists() else '',cache)
    (directory/'failure_diagnostics.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    if exit_code is not None:
        header='退出码：'+str(exit_code)+'\n收尾各阶段已观测进程数：'+json.dumps(data['exit_checkpoints'],ensure_ascii=False)+'\n'
        if 'quality' in data:
            header+='质量问题：'+json.dumps(data['quality'],ensure_ascii=False)+'\n'
        header+='关键错误：\n'+'\n'.join(diagnostics)+'\n'
        for record in data['mesh_checkpoints']+evidence:
            header+='[MESH_EVIDENCE] '+json.dumps(record,ensure_ascii=False)+'\n'
        summary.write_text(header+tail)
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--exit-code',type=int,help='运行器专用：为一个刚结束的失败轮生成摘要')
    args=parser.parse_args();cache={}
    if args.exit_code is not None:
        collect(args.root,cache,args.exit_code);return
    directories=sorted({p.parent for p in args.root.rglob('failure_summary.txt')})
    for directory in directories:
        data=collect(directory,cache)
        print(str(directory.relative_to(args.root))+': '+
              ('已导出质量分类和出错进程' if 'quality' in data else '缺少原始质量文件，已导出可用调用记录'))
    print(f'完成 {len(directories)} 个失败轮；请上传各轮 failure_diagnostics.json。没有运行网格或修改运行状态。')


if __name__=='__main__':main()
