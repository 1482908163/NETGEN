#!/usr/bin/env python3
"""Compile and execute the application's real option dispatch, not a mock parser."""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'mesh_occ_mpi/occmesh_MPI_main.cpp').read_text()
start=source.index('else if(!strcmp(argv[i],"--kernel-threads")')
end=source.index('else if(!strcmp(argv[i],"--profile-core-only"))',start)
dispatch=source[start:end]
prefix='''
#include <cstring>
#include <iostream>
#include <sstream>
#include <set>
#include <algorithm>
#include "research_options.h"
namespace mesh_research { ResearchOptions &options() { static ResearchOptions value;return value; } }
const int MPI_COMM_WORLD=0;
void print_help() {}
void MPI_Abort(int,int) { throw std::runtime_error("abort"); }
int main(int argc,char **argv) {
 int id=0;
 try { for(int i=1;i<argc;++i) { if(false) {}
'''
suffix='''
 } auto &o=mesh_research::options();
 std::cout<<o.front_components<<" "<<o.kernel_threads<<" "<<o.repair_fixed_point<<" "<<o.split_active_evaluation<<" "<<o.front_distance<<" "<<o.front_transform;
 return 0;
 } catch(const std::exception&) { return 2; }
}
'''
with tempfile.TemporaryDirectory() as directory:
    directory=Path(directory)
    def compile_dispatch(text,name):
        cpp=directory/(name+'.cpp');binary=directory/name
        cpp.write_text(prefix+text+suffix)
        subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra','-Werror',
                        '-I',str(ROOT/'mesh_occ_mpi'),str(cpp),'-o',str(binary)],check=True)
        return binary
    binary=compile_dispatch(dispatch,'actual_cli')
    def run(args,expected):
        result=subprocess.run([str(binary),*args],capture_output=True,text=True)
        assert result.returncode==0 and result.stdout==expected,(args,result.returncode,result.stdout,result.stderr)
    run([],'-1 0 0 0 0 0')
    for mode in range(3):run(['--front-components',str(mode)],f'{mode} 0 0 0 0 0')
    run(['--front-components','1','--repair-fixed-point','1','--split-active-evaluation','1',
         '--kernel-threads','4','--front-distance','0','--front-transform','0',
         '--front-components','2','--repair-fixed-point','2','--split-active-evaluation','2'],
        '2 4 2 2 0 0')
    for args in (['--front-components'],*(['--front-components',value] for value in ('-1','3','1x','nan'))):
        assert subprocess.run([str(binary),*args],capture_output=True).returncode==2,args
    # The previous missing outer predicate must be detected by these executions.
    mutant=compile_dispatch(dispatch.replace(' || !strcmp(argv[i],"--front-components")','',1),'old_bug')
    result=subprocess.run([str(mutant),'--front-components','1'],capture_output=True,text=True)
    assert result.returncode==0 and result.stdout=='-1 0 0 0 0 0'
print('PASS: real application CLI dispatch, all front component modes, warmup overrides, invalid values and previous bug reproduction')
