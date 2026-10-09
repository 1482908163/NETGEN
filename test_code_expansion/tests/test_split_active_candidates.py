#!/usr/bin/env python3
# Real classifier/edge bodies: active compaction versus unfiltered evaluation.
# Geometry doubles exercise rejection/commit control flow, not performance.
import ast
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
parsed=ast.parse((ROOT/'tests/test_split_reuse_transaction.py').read_text())
strings={n.targets[0].id:n.value.value for n in parsed.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and isinstance(n.value,ast.Constant)}
prefix=strings['prefix'];suffix=strings['suffix']
src=(ROOT.parent/'netgen/libsrc/meshing/improve3.cpp').read_text()
a=src.index('bool MeshOptimize3d::PrepareSplitCavity');b=src.index('\nvoid MeshOptimize3d :: SplitImprove ()',a)
function=src[a:b]
prefix='#include "split_active_queue.hpp"\n'+prefix
suffix=suffix.replace('bool verify=false)','bool verify=false,bool compact=false)')
suffix=suffix.replace('std::vector<std::pair<double,int>> candidates;optimizations=0;',r'''std::vector<std::pair<double,int>> candidates;optimizations=0;
 std::vector<unsigned char> mask(edges.size(),1);
 if(compact)for(size_t i=0;i<edges.size();++i) {
   auto [a,b]=edges[i];ArrayMem<ElementIndex,20> cavity;double old=0,max=0;
   mask[i]=opt.PrepareSplitCavity(table,a,b,cavity,old,max,nullptr);
 }
 auto active=netgen::ActiveSplitIndices(mask);''')
suffix=suffix.replace('for(size_t i=0;i<edges.size();++i){auto [a,b]=edges[i];double d=', 'for(size_t i:active){auto [a,b]=edges[i];double d=')
suffix=suffix.replace('auto a=execute(m,false),b=execute(m,true),v=execute(m,true,true);',r'''auto a=execute(m,false),b=execute(m,true),v=execute(m,true,true),c=execute(m,false,false,true);
  assert(c.mesh.points==a.mesh.points && c.mesh.elements==a.mesh.elements && c.accepted==a.accepted && c.bfgs==a.bfgs);''')
with tempfile.TemporaryDirectory() as tmp:
 tmp=Path(tmp);cpp=tmp/'active.cpp';exe=tmp/'active'
 cpp.write_text(prefix+function+suffix)
 subprocess.run(['g++','-std=c++17','-O1','-fsanitize=undefined','-fno-sanitize-recover=all','-I',str(ROOT.parent/'netgen/libsrc/meshing'),str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
print('PASS: actual classifier, 100 mixed-cavity transactions, candidate ordering and final mesh equality')
