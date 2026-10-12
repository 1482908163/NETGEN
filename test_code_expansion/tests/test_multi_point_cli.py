#!/usr/bin/env python3
"""Compile the real application parser; defaults, masks and warmup override."""
import ast,os,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'mesh_occ_mpi/occmesh_MPI_main.cpp').read_text()
a=source.index('else if(!strcmp(argv[i],"--kernel-threads")');b=source.index('else if(!strcmp(argv[i],"--profile-core-only"))',a)
dispatch=source[a:b]
tree=ast.parse((ROOT/'tests/test_front_components_cli.py').read_text())
values={n.targets[0].id:n.value.value for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str)}
prefix=values['prefix'];suffix=values['suffix'];a=suffix.index(' std::cout');b=suffix.index('\n return 0;',a)
suffix=suffix[:a]+' std::cout<<o.multi_point<<" "<<o.multi_point_verify;'+suffix[b:]
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);cpp=tmp/'cli.cpp';binary=tmp/'cli';cpp.write_text(prefix+dispatch+suffix)
    subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra','-Werror','-I',str(ROOT/'mesh_occ_mpi'),str(cpp),'-o',str(binary)],check=True)
    def run(args,out):
        r=subprocess.run([str(binary),*args],capture_output=True,text=True);assert r.returncode==0 and r.stdout==out,(args,r.returncode,r.stderr,r.stdout)
    run([],'-1 0')
    for mask in range(8):run(['--multi-point',str(mask)],f'{mask} 0')
    run(['--multi-point','1','--multi-point','7','--multi-point-verify','1'],'7 1')
    run(['--multi-point','7','--multi-point-verify','1','--multi-point-verify','0'],'7 0')
    for flag,values in (('--multi-point',('-1','8','1x','nan')),('--multi-point-verify',('-1','2','1x','nan'))):
        for args in ([flag],*([flag,x] for x in values)):
            assert subprocess.run([str(binary),*args],capture_output=True).returncode==2,args
print('PASS: actual CLI, all eight masks, explicit warmup/reference overrides and malformed values')
