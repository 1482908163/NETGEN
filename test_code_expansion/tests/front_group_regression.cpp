// Differential tests use the retained, unmodified GetGroup/PointInsideGroup.
#include <mystdlib.h>
#include <meshing.hpp>
#include <random>
#include <map>
#include <sstream>
#include <iostream>
using namespace netgen;
static void Require(bool ok,const char *why) { if(!ok) throw std::runtime_error(why); }
struct Group {
  Array<MeshPoint,PointIndex> points;
  Array<MiniElement2d> faces;
  Array<PointIndex,PointIndex> index;
  Array<INDEX> findex;
};
static std::string State(const Group &g) {
  std::string s;
  auto add=[&](auto x){s.append(reinterpret_cast<const char*>(&x),sizeof(x));};
  add(g.points.Size());add(g.faces.Size());
  for(auto i:g.points.Range()) {
    add(int(g.index[i]));for(int k=0;k<3;++k) add(g.points[i](k));
  }
  for(auto i:g.faces.Range()) {
    add(g.findex[i]);add(g.faces[i].GetNP());
    for(int k=1;k<=g.faces[i].GetNP();++k) add(int(g.faces[i].PNum(k)));
  }
  return s;
}
static MiniElement2d Face(std::initializer_list<int> ids) {
  MiniElement2d f(ids.size());int j=1;
  for(int i:ids) f.PNum(j++)=i;
  return f;
}
static int checks=0,accepted=0,rejected=0;
static void Check(AdFront3 &front,int base,int limit) {
  Group reference,result;
  front.GetGroup(base,reference.points,reference.faces,reference.index,reference.findex);
  bool expected=reference.faces.Size()<=limit;
  for(auto &f:reference.faces) if(f.GetNP()!=3) expected=false;
  bool ok=front.GetGroupBounded(base,limit,result.points,result.faces,result.index,result.findex);
  Require(ok==expected,"bounded closure changed eligibility");
  if(ok) {
    Require(State(reference)==State(result),"accepted group changed ordered points/faces");
    ++accepted;
  } else {
    Require(result.points.Size()==0 && result.faces.Size()==0 &&
            result.index.Size()==0 && result.findex.Size()==0,"rejected group materialized partial output");
    ++rejected;
  }
  ++checks;
}
static std::string Generate(int mode,int variant,int refines=0) {
  Mesh mesh;mesh.SetGlobalH(1);
  mesh.SetLocalH(Point<3>(-10,-10,-10),Point<3>(10,10,10),.3);
  Meshing3 mesher(static_cast<const char **>(nullptr));
  std::vector<Point<3>> p={{0,0,0},{1,0,0},{.5,.8660254037844386,0},{.5,.2886751345948129,-.816496580927726}};
  std::vector<MiniElement2d> faces={Face({1,2,3}),Face({1,4,2}),Face({2,4,3}),Face({3,4,1})};
  for(int level=0;level<refines;++level) {
    std::map<std::pair<int,int>,int> mids;
    auto mid=[&](int a,int b) {
      auto key=std::minmax(a,b);auto it=mids.find(key);
      if(it!=mids.end()) return it->second;
      p.push_back(Center(p[a-1],p[b-1]));return mids[key]=p.size();
    };
    std::vector<MiniElement2d> next;
    for(auto &f:faces) {
      int a=f.PNum(1),b=f.PNum(2),c=f.PNum(3),ab=mid(a,b),bc=mid(b,c),ca=mid(c,a);
      for(auto child:{Face({a,ab,ca}),Face({ab,b,bc}),Face({ca,bc,c}),Face({ab,bc,ca})}) next.push_back(child);
    }
    faces=std::move(next);
  }
  for(auto &v:p) {v(0)+=.2*variant;auto pi=mesh.AddPoint(v);mesher.AddPoint(v,pi);}
  for(auto f:faces) mesher.AddBoundaryElement(f);
  MeshingParameters mp;VolumeKernelStats stats;
  mp.uselocalh=false;mp.starshapeclass=1;mp.giveuptol=10;
  mp.volume_front_group_mode=mode;mp.volume_kernel_stats=&stats;
  auto result=mesher.GenerateMesh(mesh,mp);
  Require(result==MESHING3_OK && mesh.GetNE()>0,"star fixture did not complete");
#ifndef FRONT_GROUP_ORIGINAL_GENERATOR
  Require(stats.group_values[3]>0 && stats.group_values[6]>0,"star path not covered");
  if(refines==2) Require(stats.group_values[4]>0,"large surface did not exercise rejection/fallback");
  if(mode==2) Require(stats.group_values[3]==stats.group_values[5] &&
                     stats.group_values[6]==stats.group_values[7] && stats.group_values[8]==0,
                     "incomplete exact replay");
#endif
  std::string state;auto add=[&](auto x){state.append(reinterpret_cast<const char*>(&x),sizeof(x));};
  add(mesh.GetNP());add(mesh.GetNE());
  for(auto &v:mesh.Points()) for(int k=0;k<3;++k) add(v(k));
  for(auto &e:mesh.VolumeElements()) for(int k=1;k<=e.GetNP();++k) add(int(e.PNum(k)));
  return state;
}
static void Benchmark() {
  AdFront3 large;
  for(int i=1;i<=2002;++i) large.AddPoint(Point<3>(i,i%3,i%7),PointIndex(i));
  int base=0;
  for(int i=2000;i>=1;--i) base=large.AddFace(Face({i,i+1,i+2}));
  AdFront3 rays;
  Point<3> p[4]={{0,0,0},{1,0,0},{0,1,0},{0,0,1}};
  for(int i=0;i<4;++i) rays.AddPoint(p[i],PointIndex(i+1));
  for(auto f:{Face({1,2,3}),Face({1,4,2}),Face({2,4,3}),Face({3,4,1})}) rays.AddFace(f);
  for(int i=0;i<10000;++i) rays.AddPoint(Point<3>(-1.-i*.001,2,3),PointIndex(i+5));
  Group g;rays.GetGroup(1,g.points,g.faces,g.index,g.findex);
  auto measure=[](auto fn) {
    std::vector<double> samples;
    for(int i=0;i<5;++i) {
      auto start=std::chrono::steady_clock::now();fn();
      samples.push_back(std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count());
    }
    std::sort(samples.begin(),samples.end());return samples[2];
  };
  double original=measure([&]{Group x;large.GetGroup(base,x.points,x.faces,x.index,x.findex);
    Require(x.faces.Size()>30,"benchmark closure not rejected");});
  double bounded=measure([&]{Group x;Require(!large.GetGroupBounded(base,30,x.points,x.faces,x.index,x.findex),"benchmark gate");});
  double ray_original=measure([&]{Require(!rays.PointInsideGroup(g.index,g.faces),"benchmark outside point");});
  double ray_cached=measure([&]{Require(!rays.PointInsideGroupCached(g.index,g.faces),"benchmark outside point cached");});
  std::cout<<"MICROBENCH {\"group_original_seconds\":"<<original<<",\"group_bounded_seconds\":"<<bounded
           <<",\"inside_original_seconds\":"<<ray_original<<",\"inside_cached_seconds\":"<<ray_cached<<"}\n";
}
int main(int argc,char **argv) {
  std::ostringstream out;testout=&out;testmode=0;printmessage_importance=0;
  // Reverse chain needs multiple sweeps; a one-pass traversal is incorrect.
  AdFront3 chain;
  for(int i=1;i<=80;++i) chain.AddPoint(Point<3>(i,i%3,i%7),PointIndex(i));
  int base=0;
  for(int i=60;i>=1;--i) base=chain.AddFace(Face({i,i+1,i+2}));
  for(int limit:{0,1,2,20,59,60,61,200}) Check(chain,base,limit);
  // A disconnected quad must not reject a triangular connected component.
  chain.AddFace(Face({70,71,72,73}));Check(chain,base,60);
  int bridge=chain.AddFace(Face({1,2,70,71}));Check(chain,base,200);
  chain.DeleteFace(bridge);Check(chain,base,60);
  int dead=chain.AddFace(Face({74,75,76}));chain.DeleteFace(dead);
  auto recycled=chain.AddPoint(Point<3>(99,98,97),PointIndex(99));
  chain.AddFace(Face({1,2,int(recycled)}));Check(chain,base,100);

  std::mt19937 rng(682910);
  for(int sample=0;sample<120;++sample) {
    AdFront3 front;
    for(int i=1;i<=40;++i) front.AddPoint(Point<3>(i,.1*(i%7),.03*(i%11)),PointIndex(i));
    int root=front.AddFace(Face({1,2,3}));
    for(int j=0;j<60;++j) {
      int a=1+rng()%37,b=a+1,c=a+2,d=a+3;
      int fi=front.AddFace(j%17==sample%17?Face({a,b,c,d}):Face({a,b,c}));
      if(j%9==0) front.DeleteFace(fi);
    }
    for(int limit:{0,1,3,12,30,60,100}) Check(front,root,limit);
  }
  int inside_checks=0;
  for(int sample=0;sample<120;++sample) {
    AdFront3 front;
    Point<3> p[4]={{0,0,0},{1,0,0},{0,1,0},{0,0,1}};
    for(int i=0;i<4;++i) front.AddPoint(p[i],PointIndex(i+1));
    for(auto f:{Face({1,2,3}),Face({1,4,2}),Face({2,4,3}),Face({3,4,1})}) front.AddFace(f);
    // The original tests invalid (unreferenced) points too: retain that behavior.
    double x=(sample%5==0)?0.:double(rng()%40001)/10000.-2;
    if(sample%6==1) x=.6;
    if(sample%6==2) x=std::nextafter(.6,0.);
    if(sample%6==3) x=std::nextafter(.6,1.);
    front.AddPoint(Point<3>(x,.2,.2),PointIndex(5));
    Group g;front.GetGroup(1,g.points,g.faces,g.index,g.findex);
    Require(front.PointInsideGroup(g.index,g.faces)==front.PointInsideGroupCached(g.index,g.faces),
            "cached ray test changed inside decision");
    ++inside_checks;
  }
  uint64_t fingerprint=1469598103934665603ULL;
  auto hash=[&](const std::string &s) {for(unsigned char c:s) {fingerprint^=c;fingerprint*=1099511628211ULL;}};
  for(int variant=0;variant<4;++variant) {
    auto reference=Generate(0,variant);
    hash(reference);
    Require(Generate(1,variant)==reference && Generate(2,variant)==reference,
            "full star construction changed mesh coordinates or connectivity order");
  }
  for(int refines:{1,2}) {
    auto reference=Generate(0,0,refines);
    hash(reference);
    Require(Generate(1,0,refines)==reference && Generate(2,0,refines)==reference,
            "refined front fallback changed mesh");
  }
  std::cout<<"通过："<<checks<<" 组闭包差分（接受 "<<accepted<<"，拒绝 "<<rejected
           <<"）；"<<inside_checks<<" 组射线判断；18 次完整前沿生成（含拒绝回退）及重放。\n";
  std::cout<<"front_fingerprint="<<std::hex<<fingerprint<<std::dec<<"\n";
  if(argc>1 && std::string(argv[1])=="--benchmark") Benchmark();
}
