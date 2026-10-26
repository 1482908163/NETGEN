// Exact differential check of the original recovery sequence on open cavities.
#include <mystdlib.h>
#include <meshing.hpp>
#include <map>
#include <array>
#include <sstream>
#include <cstring>
#include <iostream>
using namespace netgen;
static void Require(bool ok,const char *why) {if(!ok) throw std::runtime_error(why);}
static std::unique_ptr<Mesh> Fixture(int n,int variant) {
  auto m=std::make_unique<Mesh>();
  m->SetGlobalH(1);m->SetLocalH(Point<3>(-1,-1,-1),Point<3>(n+1,n+1,n+1),.3);
  auto id=[&](int x,int y,int z){return 1+x+(n+1)*(y+(n+1)*z);};
  for(int z=0;z<=n;++z) for(int y=0;y<=n;++y) for(int x=0;x<=n;++x) {
    bool inner=x && y && z && x<n && y<n && z<n;
    double d=inner?.12*std::sin(7*x+11*y+3*z+variant):0;
    m->AddPoint(Point<3>(x+d,y-.6*d,z+.3*d),1,inner?INNERPOINT:FIXEDPOINT);
  }
  const int tet[6][4]={{0,1,3,7},{0,3,2,7},{0,2,6,7},{0,6,4,7},{0,4,5,7},{0,5,1,7}};
  std::map<std::array<int,3>,std::pair<int,Element2d>> faces;
  m->AddFaceDescriptor(FaceDescriptor(1,1,0,0));
  for(int z=0;z<n;++z) for(int y=0;y<n;++y) for(int x=0;x<n;++x) for(int k=0;k<6;++k) {
    Element e(TET);e.SetIndex(1);
    for(int j=0;j<4;++j) {int v=tet[k][j];e.PNum(j+1)=id(x+(v&1),y+((v>>1)&1),z+((v>>2)&1));}
    if(e.Volume(m->Points())<0) std::swap(e.PNum(1),e.PNum(2));
    for(int f=1;f<=4;++f) {
      Element2d face; e.GetFace(f,face);face.SetIndex(1);
      std::array<int,3> key={int(face.PNum(1)),int(face.PNum(2)),int(face.PNum(3))};
      std::sort(key.begin(),key.end());auto &v=faces[key];++v.first;v.second=face;
    }
    if(!(x==n/2 && y==n/2 && z==n/2 && k<=variant)) m->AddVolumeElement(e);
  }
  for(auto &f:faces) if(f.second.first==1) m->AddSurfaceElement(f.second.second);
  m->CalcSurfacesOfNode();m->FindOpenElements(1);
  Require(m->GetNOpenElements()>0,"fixture lacks an open cavity");return m;
}
static void Original(Mesh &mesh,const MeshingParameters &mp) {
  MeshOptimize3d optmesh(mesh,mp,OPT_REST);
  const char *optstr="mcmstmcmstmcmstmcm";
  for(size_t j=1;j<=strlen(optstr);++j) {
    mesh.FindOpenElements();mesh.CalcSurfacesOfNode();
    mesh.FreeOpenElementsEnvironment(2);mesh.CalcSurfacesOfNode();
    switch(optstr[j-1]) {
      case 'c':optmesh.CombineImprove();break;
      case 'd':optmesh.SplitImprove();break;
      case 's':optmesh.SwapImprove();break;
      case 't':optmesh.SwapImprove2();break;
      case 'm':optmesh.ImproveMesh();break;
    }
  }
  mesh.FindOpenElements(1);RemoveProblem(mesh,1);mesh.FindOpenElements(1);
}
static std::string State(const Mesh &m) {
  std::string s;auto add=[&](auto v){s.append(reinterpret_cast<const char*>(&v),sizeof(v));};
  add(m.GetNP());add(m.GetNE());add(m.GetNSE());add(m.GetNOpenElements());
  for(auto &p:m.Points()) for(int j=0;j<3;++j) add(p(j));
  for(auto &e:m.VolumeElements()) {add(e.GetIndex());add(e.GetNP());for(auto p:e.PNums())add(int(p));}
  for(auto &e:m.SurfaceElements()) {add(e.GetIndex());add(e.GetNP());for(auto p:e.PNums())add(int(p));}
  return s;
}
struct Lease {int threads,acquires=0,releases=0,polls=0;bool active=false,resize=false;};
int main() {
  std::ostringstream diagnostics;testout=&diagnostics;testmode=0;printmessage_importance=0;
  int cases=0;
  for(int n:{4,5,6}) for(int variant:{0,1}) {
    auto reference=Fixture(n,variant);auto before=State(*reference);
    MeshingParameters mp;mp.nthreads=1;Original(*reference,mp);
    Require(reference->GetNE()>0 && State(*reference)!=before,"trivial recovery fixture");
    auto expected=State(*reference);
    for(int threads:{0,1,2,4}) for(bool resize:{false,true}) {
      auto mesh=Fixture(n,variant);VolumeKernelStats stats;Lease lease{std::max(1,threads)};lease.resize=resize;
      VolumeResources resources;resources.context=&lease;
      resources.acquire=[](void *ctx,int phase,double,int) {
        auto &l=*static_cast<Lease*>(ctx);Require(phase==7 && !l.active && !ngcore::task_manager,"nested acquisition");
        l.active=true;++l.acquires;return l.threads;
      };
      resources.release=[](void *ctx,int phase,double,int,double) {
        auto &l=*static_cast<Lease*>(ctx);Require(phase==7 && l.active && !ngcore::task_manager,"release before worker join");
        l.active=false;++l.releases;
      };
      resources.poll=[](void *ctx) {auto &l=*static_cast<Lease*>(ctx);++l.polls;
        if(l.resize && l.polls==5){l.threads=1;return 1;}return 0;};
      mp.volume_generation_recovery=threads>0;mp.nthreads=std::max(1,threads);
      mp.volume_resources=&resources;mp.volume_kernel_stats=&stats;
      RecoverOpenElements(*mesh,mp,1);
      Require(State(*mesh)==expected,"recovery differs from original sequential operations");
      Require(!ngcore::task_manager && !lease.active && lease.acquires==lease.releases,"unbalanced recovery lease");
      Require(lease.acquires==(threads>0?1+int(resize):0),"wrong rebuild count");
      Require(lease.polls==(threads>0?int(strlen("mcmstmcmstmcmstmcm")):0),"missing safe checkpoint");
      Require(stats.generation_values[15]==1 && stats.generation_values[16]==int(threads>0),"missing coverage");
      ++cases;
    }
    // A caller-owned team must never recursively acquire a host CPU lease.
    auto nested=Fixture(n,variant);VolumeResources no_nested;
    no_nested.acquire=[](void *,int,double,int)->int {throw std::runtime_error("nested host lease");};
    no_nested.release=[](void *,int,double,int,double) {};
    VolumeKernelStats nested_stats;mp.volume_generation_recovery=true;mp.volume_resources=&no_nested;
    mp.volume_kernel_stats=&nested_stats;
    {ngcore::RegionTaskManager parent(2);RecoverOpenElements(*nested,mp,1);}
    Require(State(*nested)==expected && nested_stats.generation_values[16]==0,"caller-owned team changed recovery");
    ++cases;
  }
  auto mesh=Fixture(4,0);MeshingParameters mp;Lease lease{2};VolumeResources resources;
  resources.context=&lease;
  resources.acquire=[](void *p,int,double,int) {auto &l=*static_cast<Lease*>(p);l.active=true;++l.acquires;return 2;};
  resources.release=[](void *p,int,double,int,double) {auto &l=*static_cast<Lease*>(p);
    Require(!ngcore::task_manager,"exception returned cores before joining workers");l.active=false;++l.releases;};
  resources.poll=[](void *)->int {throw std::runtime_error("injected checkpoint failure");};
  mp.volume_generation_recovery=true;mp.volume_resources=&resources;
  bool threw=false;try {RecoverOpenElements(*mesh,mp,1);} catch(const std::runtime_error &){threw=true;}
  Require(threw && !ngcore::task_manager && !lease.active && lease.acquires==1 && lease.releases==1,"exception leaked lease");
  std::cout<<"通过："<<cases<<" 组开腔恢复与原串行坐标、单元顺序完全一致；包含线程重建及租约归还。\n";
  std::cout<<"通过：恢复检查点异常时先回收工作线程，再归还核。\n";
}
