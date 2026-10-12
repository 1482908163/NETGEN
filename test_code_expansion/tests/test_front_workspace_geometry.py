#!/usr/bin/env python3
"""执行生产变换、稠密乘法和快照；包括相同/不同矩阵、退化历史和回退。
小型几何容器替身不执行网格生成或相交判定，不能作为性能证据。
"""
import ast,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
tree=ast.parse((ROOT/'test_code_expansion/tests/test_front_bound_state.py').read_text())
parts={n.targets[0].id:n.value.value for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str)}
prefix=parts['prefix']
# Use the real Vector/FlatVector implementation, including actual new[] calls.
a=prefix.index('struct Vector:');b=prefix.index('using FlatVector=Vector;',a)
vector_header=(ROOT/'netgen/libsrc/linalg/vector.hpp').read_text()
prefix=prefix[:a]+"#include <iomanip>\n#include <cstdlib>\nusing namespace std;\n"+vector_header+"\nusing netgen::Vector;using netgen::FlatVector;\n"+prefix[b+len('using FlatVector=Vector;'):]
prefix=prefix.replace('#include <array>', '#include <array>\n#include <new>')
prefix=prefix.replace('struct vnetrule {', 'std::size_t array_allocations=0;\n__attribute__((noinline)) void *operator new[](std::size_t n){++array_allocations;if(void *p=std::malloc(n?n:1))return p;throw std::bad_alloc();}\n__attribute__((noinline)) void operator delete[](void *p)noexcept{std::free(p);}\n__attribute__((noinline)) void operator delete[](void *p,std::size_t)noexcept{std::free(p);}\nstruct vnetrule {')

prefix=prefix.replace('#include <array>','#include <array>\n#include <random>\n#include <limits>')

dense=(ROOT/'netgen/libsrc/linalg/densemat.hpp').read_text()
method=dense[dense.index('  DLL_HEADER void Mult ('):dense.index('  DLL_HEADER void MultTrans (')]
method=method[:method.rfind('  ///')].replace('DLL_HEADER ','')
method=method.replace('    double sum;','    const int height=h,width=w;const double *data=this->v.data();\n    double sum;')
start=prefix.index('void Mult(const Vector&');end=prefix.index('};\nstruct Box3d',start)
prefix=prefix[:start]+method+prefix[end:]
header=(ROOT/'netgen/libsrc/meshing/ruler3.hpp').read_text()
snapshot=header[header.index('  using FreeZoneReplayState'):header.index('  void SetFreeZoneTransformation')]
source=(ROOT/'netgen/libsrc/meshing/netrule3.cpp').read_text()
transform=source[source.index('void vnetrule :: SetFreeZoneTransformation'):source.index('\nint vnetrule :: ConvexFreeZone')]
main=r'''
int main() {
 std::mt19937_64 rng(20261010);std::size_t compared=0,removed=0;
 NgArray<threeint> faces{{1,2,3},{1,3,4},{1,4,2},{2,4,3}},empty_faces;
 for(int trial=0;trial<20000;++trial) {
  const int np=4+trial%9;DenseMatrix first(4,np),second(4,np),planes(4,4),empty(0,4);
  for(int i=0;i<4;++i)for(int j=0;j<np;++j) {
   first(i,j)=rng()%4==0?double(int(rng()%2001)-1000)/1234.567:0.;
   second(i,j)=i%2==0?first(i,j):(rng()%4==0?double(int(rng()%2001)-1000)/1234.567:0.);
  }
  // Identity rows and explicit negative zeros occur in real rule operators.
  if(trial%3==0)for(int i=0;i<4;++i)for(int j=0;j<np;++j)first(i,j)=second(i,j)=i==j?1.:-0.;
  vnetrule rule;rule.points.SetSize(np);rule.freezone.SetSize(4);rule.freesets={1,2};
  rule.freefaces={&faces,&empty_faces};rule.freefaceinequ={&planes,&empty};
  rule.oldutofreezone=&first;rule.oldutofreezonelimit=&second;
  for(size_t i=0;i<planes.v.size();++i)planes.v[i]=double(trial+1)+i*.125;
  Vector a(3*np),b(3*np);
  for(int i=0;i<3*np;++i) {
   a[i]=double(int(rng()%2001)-1000)/3141.59;
   b[i]=trial%5==0?0.:double(int(rng()%2001)-1000)/3141.59;
  }
  if(trial%11==0)for(int i=0;i<3*np;++i)a[i]=(i%2==0?-1:1)*std::numeric_limits<double>::denorm_min();
  if(trial%13==0)for(int i=0;i<3*np;++i)a[i]=(i%2==0?-1:1)*std::numeric_limits<double>::max();
  if(trial%17==0)a[0]=std::numeric_limits<double>::infinity();
  if(trial%19==0)first(0,0)=std::numeric_limits<double>::quiet_NaN();
  if(trial%23==0)a[0]=std::numeric_limits<double>::quiet_NaN();
  const int tolerance=1+trial%20;
  auto initial=rule.SaveFrontReplayState();
  auto control_begin=array_allocations;
  rule.SetFreeZoneTransformation(a,tolerance);rule.SetFreeZoneTransformation(b,tolerance);
  assert(array_allocations-control_begin==6);
  auto reference=rule.SaveFrontReplayState();
  rule.RestoreFrontReplayState(initial);VolumeKernelStats stats;double workspace[netgen::VolumeFrontWorkspaceStats::count]={};auto reuse_begin=array_allocations;
  rule.SetFreeZoneTransformationPlanned(a,tolerance,0,stats.values,1,workspace);
  // Poison scratch between candidates: every logical entry must be overwritten.
  for(int k=0;k<rule.workspace_vp.Size();++k)rule.workspace_vp[k]=std::numeric_limits<double>::quiet_NaN();
  for(int k=0;k<rule.workspace_vfp1.Size();++k)rule.workspace_vfp1[k]=rule.workspace_vfp2[k]=std::numeric_limits<double>::quiet_NaN();
  rule.SetFreeZoneTransformationPlanned(b,tolerance,0,stats.values,1,workspace);
  assert(array_allocations-reuse_begin==3);
  assert(workspace[1]==2 && workspace[3]==6 && workspace[4]==6 && workspace[5]==3 && workspace[6]==3 && workspace[7]==1 && workspace[8]==np+8 && workspace[9]==np+8 && workspace[10]==1);
  auto candidate=rule.SaveFrontReplayState();
  if(candidate!=reference) {
   std::cerr<<"transform mismatch trial="<<trial<<" np="<<np<<" fallback="<<stats.values[5]<<"\n";
   for(size_t part=0;part<candidate.size();++part)for(size_t byte=0;byte<candidate[part].size();++byte)
    if(candidate[part][byte]!=reference[part][byte]){std::cerr<<"part="<<part<<" byte="<<byte<<"\n";break;}
  }
  assert(candidate==reference);++compared;
  assert(stats.values[1]==2 && stats.values[6]==0);
  assert(stats.values[2]==stats.values[3]+stats.values[4]);removed+=stats.values[4];
  // Rule dimensions may change in a reload: resize only the affected vector.
  if(trial<64) {
    DenseMatrix wider(4,np+5),wider_limit(4,np+5);
    for(int i=0;i<4;++i)for(int j=0;j<np+5;++j) {
      wider(i,j)=double(int(rng()%201)-100)/271.0;
      wider_limit(i,j)=double(int(rng()%201)-100)/317.0;
    }
    rule.points.SetSize(np+5);rule.oldutofreezone=&wider;rule.oldutofreezonelimit=&wider_limit;
    Vector c(3*(np+5));for(int i=0;i<c.Size();++i)c[i]=double(int(rng()%201)-100)/431.0;
    auto before=rule.SaveFrontReplayState();rule.SetFreeZoneTransformation(c,tolerance);
    auto expected=rule.SaveFrontReplayState();rule.RestoreFrontReplayState(before);
    auto alloc_before=array_allocations;double changed[netgen::VolumeFrontWorkspaceStats::count]={};
    rule.SetFreeZoneTransformationPlanned(c,tolerance,0,nullptr,1,changed);
    assert(array_allocations-alloc_before==1 && changed[5]==1 && changed[6]==2 && changed[10]==0);
    assert(rule.SaveFrontReplayState()==expected);
    rule.points.SetSize(np);rule.oldutofreezone=&first;rule.oldutofreezonelimit=&second;
    before=rule.SaveFrontReplayState();rule.SetFreeZoneTransformation(a,tolerance);
    expected=rule.SaveFrontReplayState();rule.RestoreFrontReplayState(before);
    alloc_before=array_allocations;rule.SetFreeZoneTransformationPlanned(a,tolerance,0,nullptr,1,changed);
    assert(array_allocations-alloc_before==1 && changed[5]==2 && changed[6]==4);
    assert(rule.SaveFrontReplayState()==expected);
  }
  // Non-default rounding uses the entire old transform, including history.
  if(trial<100) {
   std::fesetround(FE_DOWNWARD);rule.RestoreFrontReplayState(initial);
   rule.SetFreeZoneTransformation(a,tolerance);auto down=rule.SaveFrontReplayState();
   rule.RestoreFrontReplayState(initial);VolumeKernelStats fallback;
   rule.SetFreeZoneTransformationPlanned(a,tolerance,0,fallback.values,1,workspace);
   assert(rule.SaveFrontReplayState()==down && fallback.values[5]==0 && fallback.values[4]==0);
   std::fesetround(FE_TONEAREST);
  }
 }
 assert(removed==0);
 std::cout<<"PASS: "<<compared<<" full production transform state sequences; actual Vector allocations halved, poisoned scratch, exact full state, signed zero/nonfinite/overflow/subnormal/rounding; removed terms="<<removed<<"\n";
}
'''
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);cpp=tmp/'geometry.cpp';cpp.write_text(prefix+snapshot+parts['suffix']+transform+main)
    for name,flags in [('checked',['-O2','-fsanitize=undefined','-fno-sanitize-recover=all']),('native',['-O3','-march=native'])]:
        exe=tmp/name
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(ROOT/'netgen/libsrc/meshing'),*flags,str(cpp),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
