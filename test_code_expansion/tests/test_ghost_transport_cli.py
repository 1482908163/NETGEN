#!/usr/bin/env python3
"""Compile the real option dispatch and verify startup precedes Ng_Init."""
import os,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=(ROOT/'mesh_occ_mpi/occmesh_MPI_main.cpp').read_text()
assert s.index('MPIStartupProbe(MPI_COMM_WORLD,mpi_thread_support)')<s.index('    Ng_Init();')
start=s.index('else if(!strcmp(argv[i],"--kernel-threads")');end=s.index('else if(!strcmp(argv[i],"--profile-core-only"))',start)
dispatch=s[start:end]
prefix='''#include <cstring>
#include <iostream>
#include <sstream>
#include <set>
#include <algorithm>
#include "research_options.h"
namespace mesh_research{ResearchOptions &options(){static ResearchOptions r;return r;}}
constexpr int MPI_COMM_WORLD=0;
void MPI_Abort(int,int){throw std::runtime_error("abort");}void print_help(){}
int main(int argc,char **argv){int id=0;try{for(int i=1;i<argc;++i){if(false){}
'''
suffix='''}std::cout<<mesh_research::options().ghost_dictionary;return 0;}catch(const std::exception&){return 2;}}
'''
with tempfile.TemporaryDirectory() as d:
    d=Path(d);(d/'test.cpp').write_text(prefix+dispatch+suffix)
    subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra','-Werror','-I',str(ROOT/'mesh_occ_mpi'),str(d/'test.cpp'),'-o',str(d/'test')],check=True)
    for args,expected in (([],'-1'),(['--ghost-dictionary','0'],'0'),(['--ghost-dictionary','1'],'1'),(['--ghost-dictionary','1','--ghost-dictionary','2'],'2')):
        r=subprocess.run([str(d/'test'),*args],capture_output=True,text=True);assert r.returncode==0 and r.stdout==expected
    for args in (['--ghost-dictionary'],*(['--ghost-dictionary',x] for x in ('-1','3','1x','nan'))):assert subprocess.run([str(d/'test'),*args],capture_output=True).returncode==2
print('PASS: real dictionary option dispatch, repeated warmup override, invalid inputs, and startup before mesh initialization')
