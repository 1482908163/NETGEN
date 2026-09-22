#!/usr/bin/env python3
"""Exercise production surface refinement with a tiny mesh API substitute, not MPI/OCC."""
import os
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'mesh_occ_mpi/3DNgmesher.cpp').read_text(errors='replace')
# Keep the production function bodies, including midpoint construction.
def function(start,end):
    return source[source.index(start):source.index(end,source.index(start))]
refine=function('void Refine(', '//初始化重心坐标')
bary=function('Barycentric InitBarycv(', 'void Refineforvol(')
header=r'''
#include <array>
#include <cassert>
#include "parallelMeshData.h"
namespace nglib {
struct Ng_Mesh {std::vector<std::array<double,3>> points; int surfaces=0;};
constexpr int NG_TRIG=1;
void Ng_GetPoint(Ng_Mesh*m,int i,double*x){std::copy_n(m->points.at(i-1).data(),3,x);}
void Ng_AddPoint(Ng_Mesh*m,double*x,int&i){m->points.push_back({x[0],x[1],x[2]});i=m->points.size();}
void Ng_AddSurfaceElementwithIndex(Ng_Mesh*m,int,int*,int){++m->surfaces;}
}
void SortInt(int*x){std::sort(x,x+3);}
'''
test=r'''
int main(){
 for(short orientation: {short(0),short(1)}){
  nglib::Ng_Mesh mesh;mesh.points={{0,0,0},{1,0,0},{0,1,0}};
  xdFace face{};face.outw=orientation;face.geoboundary=7;
  for(int i=0;i<3;++i){face.lsvrtx[i]=i+1;face.barycv[i]=InitBarycv(i+1,16);}
  std::list<xdFace> faces{face};std::map<Barycentric,int,CompBarycentric> bary;
  std::map<IntPair,int,IntPairCompare> edges;
  Refine(&mesh,2,0,faces,bary,edges);
  assert(faces.size()==16 && mesh.surfaces==16);
  double area=0;
  for(const auto&f:faces){
   assert(f.outw==orientation && f.geoboundary==7);
   const auto&a=mesh.points.at(f.lsvrtx[0]-1);const auto&b=mesh.points.at(f.lsvrtx[1]-1);
   const auto&c=mesh.points.at(f.lsvrtx[2]-1);
   double cross=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
   assert(cross>0);area+=cross/2;
  }
  assert(area==0.5);
 }
}
'''
with tempfile.TemporaryDirectory() as tmp:
    folder=Path(tmp)
    (folder/'mpi.h').write_text('using MPI_Datatype=int;\n')
    cpp=folder/'refine.cpp';cpp.write_text(header+bary+refine+test)
    exe=folder/'refine'
    subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-ftrivial-auto-var-init=pattern',
                    '-I',str(folder),'-I',str(ROOT/'mesh_occ_mpi'),str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
print('PASS: production refinement preserves parent orientation, descriptor and area over two levels')
