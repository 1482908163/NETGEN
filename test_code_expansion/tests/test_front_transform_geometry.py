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
prefix=prefix.replace('double operator()(int i)const','const double& operator()(int i)const')
prefix=prefix.replace('#include <array>','#include <array>\n#include <random>\n#include <limits>')
prefix=prefix.replace('struct DenseMatrix {','using FlatVector=Vector;\nstruct DenseMatrix {')
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
  rule.SetFreeZoneTransformation(a,tolerance);rule.SetFreeZoneTransformation(b,tolerance);
  auto reference=rule.SaveFrontReplayState();
  rule.RestoreFrontReplayState(initial);VolumeKernelStats stats;
  rule.SetFreeZoneTransformationPlanned(a,tolerance,1,stats.values);
  rule.SetFreeZoneTransformationPlanned(b,tolerance,1,stats.values);
  auto candidate=rule.SaveFrontReplayState();
  if(candidate!=reference) {
   std::cerr<<"transform mismatch trial="<<trial<<" np="<<np<<" fallback="<<stats.values[5]<<"\n";
   for(size_t part=0;part<candidate.size();++part)for(size_t byte=0;byte<candidate[part].size();++byte)
    if(candidate[part][byte]!=reference[part][byte]){std::cerr<<"part="<<part<<" byte="<<byte<<"\n";break;}
  }
  assert(candidate==reference);++compared;
  assert(stats.values[1]==2 && stats.values[6]==1);
  assert(stats.values[2]==stats.values[3]+stats.values[4]);removed+=stats.values[4];
  // Non-default rounding uses the entire old transform, including history.
  if(trial<100) {
   std::fesetround(FE_DOWNWARD);rule.RestoreFrontReplayState(initial);
   rule.SetFreeZoneTransformation(a,tolerance);auto down=rule.SaveFrontReplayState();
   rule.RestoreFrontReplayState(initial);VolumeKernelStats fallback;
   rule.SetFreeZoneTransformationPlanned(a,tolerance,1,fallback.values);
   assert(rule.SaveFrontReplayState()==down && fallback.values[5]==int(rule.front_transform_plan->Identical()) && fallback.values[4]==0);
   std::fesetround(FE_TONEAREST);
  }
 }
 assert(removed>0);
 std::cout<<"PASS: "<<compared<<" full production transform state sequences; identical/different operators, signed zero, nonfinite, overflow, subnormal, rounding fallback; removed terms="<<removed<<"\n";
}
'''
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);cpp=tmp/'geometry.cpp';cpp.write_text(prefix+snapshot+parts['suffix']+transform+main)
    for name,flags in [('checked',['-O2','-fsanitize=undefined','-fno-sanitize-recover=all']),('native',['-O3','-march=native'])]:
        exe=tmp/name
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(ROOT/'netgen/libsrc/meshing'),*flags,str(cpp),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
