// Differential regression against both sequential replay and the unchanged
// parent rule-search sources. No CAD input, MPI launch, or cluster allocation is required.
#include <mystdlib.h>
#include <meshing.hpp>
#include <random>
#include <sstream>
#include <cstdint>
#include <cstring>
#include <iostream>
#ifdef NETGEN_SEARCH_MPI_REGRESSION
#define NETGEN_SEARCH_PROTOCOL_TEST
#include "../mesh_occ_mpi/distributed_search.h"
#endif

using namespace netgen;
namespace netgen { extern double minother, minwithoutother; }
#ifdef RULE_SEARCH_LEGACY_TRANSPORT
// The untouched parent predates the transport API; reference runs never use it.
namespace netgen {
std::string Meshing3::ExecuteRuleSearchPacket(const char *,int) {
  throw std::runtime_error("参考版本不执行远程搜索");
}
}
#endif

static uint64_t fingerprint=1469598103934665603ULL;
template<class T> void Hash(T v) {
  unsigned char bytes[sizeof(T)];std::memcpy(bytes,&v,sizeof(T));
  for(auto c:bytes) {fingerprint^=c;fingerprint*=1099511628211ULL;}
}
int main(int argc,char **argv) {
  const int threads=argc>1?std::atoi(argv[1]):4;
  const int mode=argc>2?std::atoi(argv[2]):2;
  std::ostringstream diagnostics;
  testout=&diagnostics;testmode=0;printmessage_importance=0;
#ifdef NETGEN_SEARCH_MPI_REGRESSION
  int provided,rank;MPI_Init_thread(&argc,&argv,MPI_THREAD_FUNNELED,&provided);
  MPI_Comm_rank(MPI_COMM_WORLD,&rank);
  if(provided<MPI_THREAD_FUNNELED) MPI_Abort(MPI_COMM_WORLD,1);
  mesh_search::DistributedSearch service(MPI_COMM_WORLD);service.UseRankAsTestNode();
  auto execute_packet=[](const char *data,int bytes) {return Meshing3::ExecuteRuleSearchPacket(data,bytes);};
  if(rank) {service.ServeUntilComplete(execute_packet);MPI_Finalize();return 0;}
#endif
  Meshing3 mesher(static_cast<const char **>(nullptr));
  struct Loopback {std::string output;int calls=0;void *service=nullptr;} loopback;
  RuleSearchTransport transport;
  transport.context=&loopback;
  transport.offer=[](void *,double gain,double,double){return gain>0?1:0;};
  transport.start=[](void *ctx,const char *data,int bytes) {
    auto & state=*static_cast<Loopback*>(ctx);
    if(!state.calls) {
      for(int length:{0,bytes-1}) {
        bool rejected=false;
        try {Meshing3::ExecuteRuleSearchPacket(data,length);} catch(const std::exception &) {rejected=true;}
        if(!rejected) throw std::runtime_error("截断消息未被拒绝");
      }
      std::string broken(data,bytes);broken[0]=0;
      bool rejected=false;
      try {Meshing3::ExecuteRuleSearchPacket(broken.data(),bytes);} catch(const std::exception &) {rejected=true;}
      if(!rejected) throw std::runtime_error("错误版本消息未被拒绝");
    }
    state.output=Meshing3::ExecuteRuleSearchPacket(data,bytes);++state.calls;
  };
  transport.finish=[](void *ctx,int *bytes)->const char * {
    auto & state=*static_cast<Loopback*>(ctx);*bytes=state.output.size();return state.output.data();
  };
#ifdef NETGEN_SEARCH_MPI_REGRESSION
  loopback.service=&service;
  transport.offer=[](void *ctx,double gain,double,double) {
    if(gain<=0) return 0;
    auto &s=*static_cast<mesh_search::DistributedSearch*>(static_cast<Loopback*>(ctx)->service);
    const double deadline=MPI_Wtime()+20;
    // Synthetic positive gain tests real geometry transport, NOT performance.
    while(!s.Offer(100,1000,0)) if(MPI_Wtime()>deadline) MPI_Abort(MPI_COMM_WORLD,2);
    return 1;
  };
  transport.offer_sized=[](void *ctx,double gain,double,double,int bytes) {
    if(gain<=0) return 0;
    auto &s=*static_cast<mesh_search::DistributedSearch*>(static_cast<Loopback*>(ctx)->service);
    const double deadline=MPI_Wtime()+20;
    while(!s.Offer(100,1000,0,bytes)) if(MPI_Wtime()>deadline) MPI_Abort(MPI_COMM_WORLD,2);
    return 1;
  };
  transport.start=[](void *ctx,const char *data,int bytes) {
    auto &state=*static_cast<Loopback*>(ctx);++state.calls;
    static_cast<mesh_search::DistributedSearch*>(state.service)->Start(data,bytes);
  };
  transport.finish=[](void *ctx,int *bytes) {
    return static_cast<mesh_search::DistributedSearch*>(static_cast<Loopback*>(ctx)->service)->Finish(bytes);
  };
#endif
  const bool remote=argc>3 && std::atoi(argv[3]);
  if(remote) mesher.SetRuleSearchTransport(&transport);
  VolumeKernelStats stats;
  auto team=std::make_unique<ngcore::RegionTaskManager>(threads);
  std::mt19937 random(928173);
  int successes=0,retries=0,impossible=0,calls=0;
  for(int sample=0;sample<160;sample++) {
    Array<Point3d,PointIndex> input;
    input.Append(Point3d(0,0,0));input.Append(Point3d(1,0,0));
    input.Append(Point3d(.5,.8660254037844386,0));
    input.Append(Point3d(.5,.2886751345948129,-.816496580927726));
    if(sample>=140) input[PointIndex(4)]=Point3d(0,1,0);
    // Open, partly closed and fully closed tetrahedra; crowded/free zones and
    // points that become allowable at later tolerance classes.
    for(int j=0;j<sample%9;j++) {
      auto coord=[&](){return double(random()%30001)/10000.0-1;};
      input.Append(Point3d(coord(),coord(),-std::abs(coord())));
    }
    Array<MiniElement2d> faces;
    const int tris[][3]={{1,2,3},{1,4,2},{2,4,3},{3,4,1}};
    for(int k=0;k<=sample%4;k++) {
      MiniElement2d f(3);
      for(int j=1;j<=3;j++) f.PNum(j)=tris[k][j-1];
      faces.Append(f);
    }
    if(sample>=140) {
      faces.SetSize(0);MiniElement2d quad(4);
      for(int j=1;j<=4;j++) quad.PNum(j)=j;
      faces.Append(quad);
    }
    Array<int,PointIndex> allow(input.Size());allow=2;
    for(auto i:allow.Range()) if(int(i)>4) allow[i]=(sample+int(i))%3;
    INDEX_2_HASHTABLE<int> pairs(10);
    for(int tolerance=1;tolerance<=7;tolerance++) for(int rotation=1;rotation<=3;rotation++) {
      Array<Point3d,PointIndex> p(input);Array<MiniElement2d> f(faces);
      NgArray<Element> elements;NgArray<INDEX> deleted;
      float error=0;minother=minwithoutother=1e10;
      int found;
#ifdef RULE_SEARCH_REFERENCE
      found=mesher.ApplyRules(p,allow,f,f.Size(),pairs,elements,deleted,
                             tolerance,sample%2?5.:1.,rotation,error);
#else
      found=mesher.ApplyRulesTasks(p,allow,f,f.Size(),pairs,elements,deleted,
                                  tolerance,sample%2?5.:1.,rotation,error,mode,&stats);
#endif
      ++calls;if(found>0)++successes;else if(found==0)++retries;else ++impossible;
      Hash(found);Hash(error);Hash(minother);Hash(minwithoutother);
      Hash(int(p.Size()));Hash(int(f.Size()));Hash(int(elements.Size()));Hash(int(deleted.Size()));
      for(auto i:p.Range()) for(int j=1;j<=3;j++) Hash(p[i].X(j));
      for(auto i:f.Range()) for(int j=1;j<=f[i].GetNP();j++) Hash(int(f[i].PNum(j)));
      for(auto i:elements.Range()) {
        Hash(int(elements[i].GetType()));
        for(int j=1;j<=elements[i].GetNP();j++) Hash(int(elements[i].PNum(j)));
      }
      for(auto i:deleted.Range()) Hash(int(deleted[i]));
    }
  }
#ifndef RULE_SEARCH_REFERENCE
  if(mode==6 && threads>1) {
    // Stress the inherited DFS face state using shuffled prefixes and nearby
    // points. Every call replays the original search including diagnostics.
    Meshing3 stress(static_cast<const char **>(nullptr));
    for(int trial=0;trial<120;++trial) {
      Array<Point3d,PointIndex> p;
      p.Append(Point3d(0,0,0));p.Append(Point3d(1,0,0));p.Append(Point3d(.5,.8660254037844386,0));
      p.Append(Point3d(.5,.2886751345948129,-.816496580927726));
      for(int i=0;i<trial%12;++i) p.Append(Point3d(double(random()%200)/100.,double(random()%200)/100.,-double(random()%200)/100.));
      Array<int,PointIndex> allow(p.Size());allow=2;
      Array<MiniElement2d> faces;
      int ids[3]={0,1,2};std::shuffle(ids,ids+3,random);
      const int tris[][3]={{1,2,3},{1,4,2},{2,4,3},{3,4,1}};
      for(int i=0;i<4;++i) {
        int k=i?ids[i-1]+1:0;MiniElement2d f(3);
        for(int j=1;j<=3;++j)f.PNum(j)=tris[k][j-1];faces.Append(f);
      }
      INDEX_2_HASHTABLE<int> pairs(10);
      for(int tolerance=1;tolerance<=5;++tolerance)for(int rotation=1;rotation<=3;++rotation) {
        Array<Point3d,PointIndex> points(p);Array<MiniElement2d> f(faces);
        NgArray<Element> elems;NgArray<INDEX> del;float error;
        minother=minwithoutother=1e10;
        stress.ApplyRulesTasks(points,allow,f,f.Size(),pairs,elems,del,tolerance,5.,rotation,error,6,&stats);
      }
    }
    std::cout<<"stress_verified=1800\n";
  }
#endif
  // An empty rule set must preserve the original impossible result, without
  // dispatching an empty team or dereferencing a missing worker result.
  const char * empty_rules[]={nullptr};Meshing3 empty(empty_rules);
  Array<Point3d,PointIndex> ep;
  ep.Append(Point3d(0,0,0));ep.Append(Point3d(1,0,0));ep.Append(Point3d(0,1,0));
  Array<int,PointIndex> ea(3);ea=2;Array<MiniElement2d> ef;
  MiniElement2d triangle(3);for(int j=1;j<=3;j++) triangle.PNum(j)=j;ef.Append(triangle);
  NgArray<Element> ee;NgArray<INDEX> ed;INDEX_2_HASHTABLE<int> pairs(10);float error;
#ifdef RULE_SEARCH_REFERENCE
  int empty_result=empty.ApplyRules(ep,ea,ef,1,pairs,ee,ed,1,5.,1,error);
#else
  int empty_result=empty.ApplyRulesTasks(ep,ea,ef,1,pairs,ee,ed,1,5.,1,error,mode,&stats);
#endif
  if(empty_result!=-1) return 5;
  ++calls;++impossible;Hash(empty_result);
  if(mode>=5 && threads>1 && stats.adaptive_values[8].load()==0)
    throw std::runtime_error("映射子树未被回归覆盖");
  std::cout<<"subtree_calls="<<stats.adaptive_values[8].load()<<" tasks="<<stats.adaptive_values[9].load()<<"\n";
  team.reset();
  // Exercise the real front loop and its resource lifetime, not just ApplyRules.
  Mesh mesh;mesh.SetGlobalH(2.0);
  mesh.SetLocalH(Point<3>(-2,-2,-2),Point<3>(2,2,2),.3);
  Meshing3 front(static_cast<const char **>(nullptr));
  const Point3d corners[]={Point3d(0,0,0),Point3d(1,0,0),
    Point3d(.5,.8660254037844386,0),Point3d(.5,.2886751345948129,-.816496580927726)};
  for(auto & point:corners) {auto pi=mesh.AddPoint(point);front.AddPoint(point,pi);}
  const int boundary[][3]={{1,2,3},{1,4,2},{2,4,3},{3,4,1}};
  for(auto & tri:boundary) {
    MiniElement2d face(3);for(int j=1;j<=3;j++) face.PNum(j)=tri[j-1];
    front.AddBoundaryElement(face);
  }
  struct Lease {int threads,acquired=0,released=0;bool valid=true;} lease{threads};
  VolumeResources resources;resources.context=&lease;
  resources.acquire=[](void * ctx,int phase,double,int maximum) {
    auto & l=*static_cast<Lease*>(ctx);++l.acquired;
    l.valid=l.valid && phase==0 && maximum==l.threads && !ngcore::task_manager;
    return l.threads;
  };
  resources.release=[](void * ctx,int phase,double,int n,double) {
    auto & l=*static_cast<Lease*>(ctx);++l.released;
    l.valid=l.valid && phase==0 && n==l.threads && !ngcore::task_manager;
  };
  MeshingParameters mp;mp.maxh=2.;mp.uselocalh=0;mp.sloppy=5.;mp.nthreads=threads;
  mp.volume_rule_search=mode;mp.volume_resources=&resources;mp.volume_kernel_stats=&stats;
  int status=front.GenerateMesh(mesh,mp);
  if(status!=MESHING3_OK || mesh.GetNE()!=1 || mesh.GetNP()!=4) return 3;
#ifndef RULE_SEARCH_REFERENCE
  const int expected=threads>1 && mode>0?1:0;
  if(!lease.valid || lease.acquired!=expected || lease.released!=expected) return 4;
#endif
  Hash(status);Hash(mesh.GetNP());Hash(mesh.GetNE());
  for(auto & el:mesh.VolumeElements()) for(auto pi:el.PNums()) Hash(int(pi));
  std::cout<<"calls="<<calls<<" success="<<successes<<" retry="<<retries
           <<" impossible="<<impossible<<" fingerprint="<<std::hex<<fingerprint<<std::dec<<"\n";
#ifndef RULE_SEARCH_REFERENCE
  std::cout<<"parallel="<<stats.rule_values[1]<<" verified="<<stats.rule_values[2]
           <<" serial="<<stats.rule_values[3]<<" debug_fallbacks="<<stats.rule_values[16]
           <<" mismatches="<<stats.rule_values[17]<<" mappings="<<stats.rule_values[8]<<"\n";
  std::cout<<"remote_packets="<<loopback.calls<<" adaptive_serial="<<stats.adaptive_values[1]<<"\n";
  if(remote && !loopback.calls) return 6;
  if(threads>1 && mode>0 && stats.rule_values[1]==0) return 2;
#endif
#ifdef NETGEN_SEARCH_MPI_REGRESSION
  service.ServeUntilComplete(execute_packet);MPI_Finalize();
#endif
  return successes==0 || retries==0;
}
