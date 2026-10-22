// Differential regression against both sequential replay and the unchanged
// parent rule-search sources. No CAD input, MPI launch, or cluster allocation is required.
#include <mystdlib.h>
#include <meshing.hpp>
#include <random>
#include <sstream>
#include <cstdint>
#include <cstring>
#include <iostream>

using namespace netgen;
namespace netgen { extern double minother, minwithoutother; }

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
  Meshing3 mesher(static_cast<const char **>(nullptr));
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
  if(threads>1 && mode>0 && stats.rule_values[1]==0) return 2;
#endif
  return successes==0 || retries==0;
}
