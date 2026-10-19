"""One bounded all-rank startup probe per allocation and binary identity."""
import argparse,fcntl,json,os,re,signal,subprocess,time
from pathlib import Path

def probe(directory,stop,ranks,binary_sha,timeout,command):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    identity=dict(job=os.environ.get('SLURM_JOB_ID',os.environ.get('JOB_ID','local')),ranks=ranks,binary_sha256=binary_sha,
        command=command,cpus=os.environ.get('CPUS_PER_TASK'),rpn=os.environ.get('RANKS_PER_NODE'))
    with (directory/'probe.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        receipt=directory/'receipt.json';old=json.loads(receipt.read_text()) if receipt.exists() else {}
        if old.get('identity')==identity:
            if old.get('success'):return 0
            stop.parent.mkdir(parents=True,exist_ok=True);stop.write_text(json.dumps(old['stop'],indent=2)+'\n');return 75
        start=time.time();rc=2;error=''
        with (directory/'run.log').open('wb') as log:
            try:
                process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                try:rc=process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGTERM)
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
                    rc=124
            except OSError as e:error=str(e);log.write(error.encode())
        with (directory/'run.log').open('rb') as stream:
            stream.seek(0,2);stream.seek(max(0,stream.tell()-8192));tail=stream.read()
        content=tail.decode(errors='replace');(directory/'log_tail.txt').write_text(content)
        success=rc==0 and bool(re.search(r'\[MPI_PREFLIGHT_OK\] ranks='+str(ranks)+r' thread_support=[1-9][0-9]*',content))
        status=dict(schema='mpi_startup_check_v1',identity=identity,success=success,exit_code=rc,elapsed_seconds=time.time()-start,error=error)
        if not success:
            status['stop']=dict(schema='allocation_stop_v1',reason='mpi_startup_probe_failed',job_id=identity['job'],exit_code=rc,timeout_seconds=timeout,action='stop_remaining_runs_in_this_allocation',evidence=str(directory/'run.log'))
            stop.parent.mkdir(parents=True,exist_ok=True);stop.write_text(json.dumps(status['stop'],indent=2)+'\n')
        else:stop.unlink(missing_ok=True)
        receipt.write_text(json.dumps(status,indent=2)+'\n')
        return 0 if success else 75

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--stop-output',type=Path,required=True);p.add_argument('--ranks',type=int,required=True);p.add_argument('--binary-sha',required=True);p.add_argument('--timeout',type=int,default=60);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    if a.ranks<1 or a.timeout<1 or not command:p.error('invalid startup probe configuration')
    raise SystemExit(probe(a.directory,a.stop_output,a.ranks,a.binary_sha,a.timeout,command))
