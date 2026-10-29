// Exercise the real front's high-tolerance retry and bounded failure path.
#include <mystdlib.h>
#include <meshing.hpp>
#include <sstream>
#include <iostream>
using namespace netgen;

static std::string Generate(double sloppy,int mode,bool quiet,double &covered) {
  static std::ostream disabled(nullptr);testout=&disabled;testmode=0;printmessage_importance=0;
  Mesh mesh;mesh.SetGlobalH(2.0);
  mesh.SetLocalH(Point<3>(-2,-2,-2),Point<3>(2,2,2),.3);
  Meshing3 mesher(static_cast<const char **>(nullptr));
  const Point3d points[]={{0,0,0},{1,0,0},{.5,.8660254037844386,0},
                        {.5,.2886751345948129,-.816496580927726}};
  for(auto &point:points) {auto pi=mesh.AddPoint(point);mesher.AddPoint(point,pi);}
  for(auto tri: {std::array<int,3>{1,2,3},{1,4,2},{2,4,3},{3,4,1}}) {
    MiniElement2d face(3);for(int i=1;i<=3;++i)face.PNum(i)=tri[i-1];mesher.AddBoundaryElement(face);
  }
  VolumeKernelStats stats;MeshingParameters mp;
  mp.maxh=2.;mp.uselocalh=false;mp.sloppy=sloppy;mp.giveuptol=50;mp.starshapeclass=100;
  mp.nthreads=4;mp.volume_rule_search=mode;mp.volume_quiet_rule_parallelism=quiet;
  mp.volume_kernel_stats=&stats;
  int status=mesher.GenerateMesh(mesh,mp);
  // This intentionally overstrict quality fixture does not produce a mesh.
  // Preserve the existing return code, empty output, retry limit and work.
  if(status!=MESHING3_NEGVOL || mesh.GetNE()!=0 || mesh.GetNP()!=4 || stats.front_values[18]!=50)
    throw std::runtime_error("受限质量前沿没有按原条件到达退出界限");
  covered=stats.quiet_rule_values[3].load();
  std::string result;
  auto add=[&](auto value){result.append(reinterpret_cast<const char*>(&value),sizeof(value));};
  add(status);add(mesh.GetNP());add(mesh.GetNE());
  for(auto &p:mesh.Points())for(int i=0;i<3;++i)add(p(i));
  for(auto &e:mesh.VolumeElements()) {add(e.GetType());for(auto pi:e.PNums())add(int(pi));}
  for(int i:{4,5,6,7,8}) add(stats.rule_values[i].load());
  for(int i=8;i<20;++i) add(stats.front_values[i].load());
  std::cout<<"sloppy="<<sloppy<<" mode="<<mode<<" quiet="<<quiet
           <<" high="<<stats.quiet_rule_values[0]<<" parallel="<<covered
           <<" verified="<<stats.quiet_rule_values[5]<<"\n";
  return result;
}

int main() {
  double covered,total=0;uint64_t fingerprint=1469598103934665603ULL;
  for(double sloppy:{.2,.1,.05}) {
    auto reference=Generate(sloppy,0,false,covered);
    for(unsigned char c:reference) {fingerprint^=c;fingerprint*=1099511628211ULL;}
#ifndef RULE_SEARCH_REFERENCE
    for(int mode:{1,2}) {
      if(Generate(sloppy,mode,true,covered)!=reference)
        throw std::runtime_error("高容错前沿退出状态、网格或搜索工作不同");
      if(covered==0)throw std::runtime_error("前沿重试未覆盖高容错并行");
      total+=covered;
    }
#endif
  }
  std::cout<<"高容错有界前沿重试/退出对照通过，覆盖 "<<total<<" 次并行调用。\n";
  std::cout<<"quiet_front_fingerprint="<<std::hex<<fingerprint<<std::dec<<"\n";
}
