#!/usr/bin/env python3
"""Production selection methods and local packet/assembly helpers; no MPI timing."""
import os,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
source=(ROOT/'netgen/libsrc/meshing/adfront3.cpp').read_text()
selection=source[source.index('long long AdFront3::SelectionScore'):source.index('int AdFront3 :: GetLocals')]
prefix=r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <chrono>
#include <climits>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <map>
#include <random>
#include <stdexcept>
#include <vector>
#include "front_selection_index.hpp"
#include "ghost_local_work.h"
namespace netgen {
using PointIndex=int;
struct FrontPoint {int front=1000;int FrontNr()const{return front;}};
struct Face {std::array<int,4> p{};int np=3;int PNum(int i)const{return p[i-1];}auto PNums()const{return std::vector<int>(p.begin(),p.begin()+np);}};
struct Record {netgen::Face face;int quality=1;bool valid=true;bool Valid()const{return valid;}int QualClass()const{return quality;}const auto &Face()const{return face;}};
template<class T>struct Array:std::vector<T>{using std::vector<T>::vector;int Size()const{return this->size();}const T&Get(int i)const{return this->at(i-1);}T&Elem(int i){return this->at(i-1);}};
struct NgException:std::runtime_error{using std::runtime_error::runtime_error;};
struct AdFront3 {
 Array<FrontPoint> points;Array<Record> faces;int nff=0,rebuildcounter=1000000,lasti=0,minval=-1;
 FrontSelectionIndex selection_index;std::vector<std::vector<int>> selection_incidence;
 double selection_values[12]={};int selection_mode=2;bool selection_ready=false;
 long long SelectionScore(int,bool=true);void PrepareSelection();void SelectionPointChanged(PointIndex);int SelectBaseElement();
 void RebuildInternalTables(){faces.erase(std::remove_if(faces.begin(),faces.end(),[](auto f){return !f.valid;}),faces.end());nff=faces.Size();selection_ready=false;}
};
}
'''.replace('points.Size()+PointIndex::BASE','points.Size()+1')
# PointIndex is a container stand-in here; execute the real selection and
# invalidation methods, preserving actual ordering/state/replay branches.
selection=selection.replace('points.Size()+PointIndex::BASE','points.Size()+1')
main=r'''
using namespace netgen;
struct Vertex{double xyz[3];};
struct Packet{std::int64_t gid;int domidx;std::int64_t Pindex[4];Vertex Vertexs[4];};
struct Mesh {
 std::vector<std::array<int,4>> tets;std::vector<Vertex> points;
 int np()const{return points.size()-1;}int ne()const{return tets.size();}
 void tetrahedron(int e,int *p,int &domain)const{std::copy(tets[e-1].begin(),tets[e-1].end(),p);domain=e%9;}
 void point(int p,double *xyz)const{std::memcpy(xyz,points[p].xyz,3*sizeof(double));}
};
int main(){
 std::mt19937_64 rng(20261013);std::size_t selections=0,records=0;
 for(int trial=0;trial<200;++trial){
  AdFront3 a;a.points.resize(33);for(auto &p:a.points)p.front=rng()%20;
  for(int f=0;f<200;++f){Record r;for(int k=0;k<3;++k)r.face.p[k]=1+rng()%32;r.quality=1+rng()%8;a.faces.push_back(r);}a.nff=a.faces.Size();
  for(int step=0;step<500;++step){
   if(step%17==0){int p=1+rng()%32;a.points[p].front=rng()%20;a.SelectionPointChanged(p);}
   if(step%11==0){int f=1+rng()%a.faces.Size();a.faces.Elem(f).quality=1+rng()%15;if(a.selection_ready)a.selection_index.Changed(f);}
   if(step%13==0){int f=1+rng()%a.faces.Size();a.faces.Elem(f).valid=false;if(a.selection_ready)a.selection_index.Changed(f);}
   if(step%7==0){Record r;for(int k=0;k<3;++k)r.face.p[k]=1+rng()%32;a.faces.push_back(r);a.nff++;
     if(a.selection_ready){a.selection_index.Grow(a.faces.Size());for(int p:r.face.PNums())a.selection_incidence[p].push_back(a.faces.Size());}}
   if(step%53==0)a.rebuildcounter=0;
   assert(a.SelectBaseElement()>0);++selections;
  }
  assert(a.selection_values[0]==500 && a.selection_values[6]==500 && a.selection_values[7]==0);
 }
 // Last-minimum ties, suffix-first ties, deletion and empty front state.
 {AdFront3 a;a.points.resize(4);for(auto &p:a.points)p.front=0;for(int i=0;i<140;++i)a.faces.push_back({{{1,2,3}},1,true});a.nff=140;
  assert(a.SelectBaseElement()==140);assert(a.lasti==0);assert(a.SelectBaseElement()==1);a.faces.Elem(2).valid=false;a.selection_index.Changed(2);assert(a.SelectBaseElement()==3);
  for(auto &f:a.faces)f.valid=false;a.selection_ready=false;assert(a.SelectBaseElement()==0);}
 // A quad scores its first three vertices; a changed fourth vertex may still
 // affect another face that contains it among its first three.
 {AdFront3 a;a.points.resize(5);for(auto &p:a.points)p.front=0;Record quad;quad.face.p={1,2,3,4};quad.face.np=4;a.faces.push_back(quad);
  Record tri;tri.face.p={4,2,3,0};tri.quality=2;a.faces.push_back(tri);a.nff=2;
  assert(a.SelectBaseElement()==1);assert(a.SelectBaseElement()==1);a.points[4].front=-5;a.SelectionPointChanged(4);assert(a.SelectBaseElement()==2);}
 for(int trial=0;trial<2000;++trial){
  Mesh mesh;mesh.points.resize(31);for(auto &v:mesh.points)for(double &x:v.xyz){auto bits=rng();std::memcpy(&x,&bits,sizeof(x));}
  for(int e=0;e<80;++e){std::array<int,4> t;for(int &p:t)p=1+rng()%30;mesh.tets.push_back(t);}
  std::map<int,std::vector<int>> adj;for(int p=1;p<=30;++p)if(rng()%3==0)for(int k=0;k<int(rng()%5);++k)adj[p].push_back(1+rng()%3);
  std::vector<std::int64_t> ids(31),gids(81);for(auto &p:ids)p=std::int64_t(rng()%20)-10;for(auto &g:gids)g=rng();
  std::map<int,std::vector<Packet>> original;
  for(int e=1;e<=mesh.ne();++e){int pi[4],domain;mesh.tetrahedron(e,pi,domain);std::map<int,int> counts;
    for(int p:mesh.tets[e-1]){auto it=adj.find(p);if(it!=adj.end())for(int peer:it->second)++counts[peer];}
    for(auto entry:counts)if(entry.second>=3){Packet packet{};packet.gid=gids[e];packet.domidx=domain;
      for(int k=0;k<4;++k){packet.Pindex[k]=ids[pi[k]];mesh.point(pi[k],packet.Vertexs[k].xyz);}original[entry.first].push_back(packet);}}
  double values[8]={};auto fast=mesh_research::PackBoundary<Packet>(mesh,adj,ids.data(),gids.data(),0,4,values);
  mesh_research::SamePackets(original,fast);assert(values[1]==80 && values[2]<=80);records+=values[4];
  double compact_values[8]={};auto compact=mesh_research::PackBoundaryCompact<Packet>(mesh,adj,ids.data(),gids.data(),0,4,compact_values);
  mesh_research::SamePackets(original,compact);
  for(int k=1;k<=4;++k)assert(compact_values[k]==values[k]);
  std::vector<Packet*> buffers;std::vector<int> sizes;for(auto &entry:fast){buffers.push_back(entry.second.data());sizes.push_back(entry.second.size());}
  using Plan=mesh_research::GhostAssemblyPlan<std::int64_t>;
  auto plan=Plan::Build<std::unordered_map<std::int64_t,int>>(15,ids.data(),buffers,sizes);
  // Independent historical insertion loop, including first-coordinate wins.
  std::map<std::int64_t,int> index;for(int p=1;p<=15;++p)index[ids[p]]=p;
  std::vector<std::array<int,4>> tets;std::vector<Plan::Point> points;int np=15;
  for(std::size_t peer=0;peer<buffers.size();++peer)for(int j=0;j<sizes[peer];++j){std::array<int,4> pi;const auto &packet=buffers[peer][j];
    for(int k=0;k<4;++k){auto it=index.find(packet.Pindex[k]);if(it==index.end()){++np;index[packet.Pindex[k]]=np;Plan::Point p;p.gid=packet.Pindex[k];std::memcpy(p.xyz.data(),packet.Vertexs[k].xyz,3*sizeof(double));points.push_back(p);pi[k]=np;}else pi[k]=it->second;}tets.push_back(pi);}
  Plan expected;expected.elements=tets;expected.points=points;plan.Same(expected);
  auto reference=Plan::Build<std::map<std::int64_t,int>>(15,ids.data(),buffers,sizes);plan.Same(reference);
  auto flat=Plan::Build<mesh_research::GhostFlatIndex<std::int64_t>>(15,ids.data(),buffers,sizes);flat.Same(reference);
  if(!plan.elements.empty()){auto corrupt=plan;corrupt.elements[0][0]++;bool rejected=false;try{plan.Same(corrupt);}catch(const std::logic_error&){rejected=true;}assert(rejected);}
  if(!fast.empty()){auto corrupt=fast;auto &p=corrupt.begin()->second[0];p.domidx++;bool rejected=false;try{mesh_research::SamePackets(fast,corrupt);}catch(const std::logic_error&){rejected=true;}assert(rejected);}
 }
 // Force multiple growths, sparse high IDs and duplicates, and verify every
 // mapping against an independent tree. Zero/negative/extreme IDs are legal.
 {mesh_research::GhostFlatIndex<std::int64_t> index;std::map<std::int64_t,int> expected;
  assert(index.Find(0)==0);
  const std::int64_t extremes[]={0,-1,INT64_MIN,INT64_MAX};
  for(int i=0;i<4;++i){index.Set(extremes[i],i+1);expected[extremes[i]]=i+1;}
  for(int i=0;i<100000;++i){auto id=static_cast<std::int64_t>(rng());index.Set(id,i+5);expected[id]=i+5;}
  for(auto e:expected)assert(index.Find(e.first)==e.second);
  for(auto e:expected){index.Set(e.first,e.second+7);assert(index.Find(e.first)==e.second+7);}
  index.reserve(300000);for(auto e:expected)assert(index.Find(e.first)==e.second+7);
  bool rejected=false;try{index.Set(9,0);}catch(const std::logic_error&){rejected=true;}assert(rejected);}
 {using Plan=mesh_research::GhostAssemblyPlan<std::int64_t>;
  std::int64_t oldids[]={0,INT64_MIN,INT64_MAX,0,INT64_MIN,-1};
  std::vector<Packet> packets(8000);for(auto &packet:packets)for(int k=0;k<4;++k){packet.Pindex[k]=static_cast<std::int64_t>(rng());for(double &x:packet.Vertexs[k].xyz){auto bits=rng();std::memcpy(&x,&bits,sizeof(x));}}
  for(int i=0;i<6;++i)packets[i].Pindex[0]=oldids[i];
  std::vector<Packet*> buffers={packets.data(),packets.data()};std::vector<int> counts={8000,8000};
  auto flat=Plan::Build<mesh_research::GhostFlatIndex<std::int64_t>>(5,oldids,buffers,counts);
  auto reference=Plan::Build<std::map<std::int64_t,int>>(5,oldids,buffers,counts);flat.Same(reference);
  assert(flat.elements[1][0]==4 && flat.elements[2][0]==2 && flat.elements[0][0]==3);}
 {using Plan=mesh_research::GhostAssemblyPlan<std::int64_t>;
  std::vector<Packet*> buffers;std::vector<int> counts;
  auto empty=Plan::Build<mesh_research::GhostFlatIndex<std::int64_t>>(0,nullptr,buffers,counts);assert(empty.points.empty()&&empty.elements.empty());
  Mesh mesh;mesh.points.resize(1);std::map<int,std::vector<int>> adj;std::int64_t ids[1]={0};double v[8]={};
  auto packets=mesh_research::PackBoundaryCompact<Packet>(mesh,adj,ids,ids,0,1,v);assert(packets.empty());
  mesh.points.resize(2);mesh.tets.push_back({1,1,1,1});adj[1]={1};bool rejected=false;
  try{mesh_research::PackBoundaryCompact<Packet>(mesh,adj,ids,ids,0,1,v);}catch(const std::logic_error&){rejected=true;}assert(rejected);}
 std::cout<<"PASS: "<<selections<<" production selector replays and "<<records<<" exact packets; duplicate adjacency/IDs, bit-pattern coordinates, first/last ties, mutation and compaction\n";
}
'''
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);cpp=tmp/'multi.cpp';cpp.write_text(prefix+'\nnamespace netgen {\n'+selection+'\n}\n'+main)
    includes=['-I',str(ROOT/'netgen/libsrc/meshing'),'-I',str(ROOT/'test_code_expansion/mesh_occ_mpi')]
    for name,flags in [('optimized',['-O3']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
        binary=tmp/name
        subprocess.run([os.environ.get('CXX','g++'),'-std=c++17',*flags,*includes,str(cpp),'-o',str(binary)],check=True)
        env=dict(os.environ)
        if name=='sanitized':env['ASAN_OPTIONS']='detect_leaks=0'  # container forbids /proc task inspection
        subprocess.run([str(binary)],check=True,timeout=120,env=env)
