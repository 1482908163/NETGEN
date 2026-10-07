#!/usr/bin/env python3
"""Run the production collapse function/rollback state on adversarial stars.

The small geometry facade has a deterministic tetrahedron objective; this is
not the complete Netgen mesh or its quality formula. Actual geometry is checked
by serial production replay in every candidate natural warmup on the cluster.
"""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
source=(ROOT/'netgen/libsrc/meshing/improve3.cpp').read_text()
def extract(marker):
    first=source.index(marker);begin=source.index('{',first);depth=1;end=begin+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[first:end]

facade=r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <memory>
#include <random>
#include <set>
#include <sstream>
#include <thread>
#include <tuple>
#include <vector>
using namespace std;
namespace netgen {
template<int Tag> struct Index {
 int id=0;static constexpr int BASE=1;
 Index()=default;explicit Index(int i):id(i){} operator int()const{return id;}
};
using PointIndex=Index<1>;using ElementIndex=Index<2>;
enum {INNERPOINT=4,SURFACEPOINT=3,EDGEPOINT=2,TET=20,PRISM=23,OPT_REST=0,OPT_CONFORM=1,OPT_LEGAL=2};
template<class T,int N> struct ArrayMem:vector<T> {
 using vector<T>::vector;
 int Size()const{return this->size();}void Append(T x){this->push_back(x);}
 bool Contains(T x)const{return find(this->begin(),this->end(),x)!=this->end();}
};
inline vector<int> Range(int n){vector<int>r(n);for(int i=0;i<n;++i)r[i]=i;return r;}
template<class T,int N> vector<int> Range(const ArrayMem<T,N>&a){return Range(a.Size());}
template<class T,class I> struct Table {
 vector<vector<T>> data;explicit Table(int n):data(n){}
 auto&operator[](I i){return data[int(i)];}
};
template<class T,class I>struct FlatArray {vector<unsigned char>*p;auto&operator[](I i){return (*p)[int(i)];}};
struct MeshPoint {
 double x[3]={0,0,0};double singular=0;int layer=1,type=INNERPOINT;
 double&operator()(int i){return x[i];}double operator()(int i)const{return x[i];}
 int Type()const{return type;}int GetLayer()const{return layer;}double Singularity()const{return singular;}
};
using Point3d=MeshPoint;
MeshPoint Center(const MeshPoint&a,const MeshPoint&b){MeshPoint p;for(int j=0;j<3;++j)p(j)=.5*(a(j)+b(j));return p;}
template<class T>void Swap(T&a,T&b){std::swap(a,b);}
struct Element {
 array<PointIndex,4>p;int type=TET,index=1;float badness=0;
 struct FlagsType {bool refflag=false,marked=false,badel=false,reverse=false,illegal=false,
  illegal_valid=false,badness_valid=true,strongrefflag=false,deleted=false,fixed=false;}flags;
 int GetType()const{return type;}int GetNP()const{return 4;}int GetIndex()const{return index;}
 auto&operator[](int i){return p[i];}auto&operator[](int i)const{return p[i];}
 const auto&PNums()const{return p;}auto&Flags(){return flags;}const auto&Flags()const{return flags;}
 bool IsDeleted()const{return flags.deleted;}void Touch(){flags.illegal_valid=flags.badness_valid=false;}
 void Delete(){flags.deleted=true;}float GetBadness(){return badness;}bool BadnessValid(){return flags.badness_valid;}
 void SetBadness(float x){badness=x;flags.badness_valid=true;}
};
struct PointStorage {
 mutable vector<MeshPoint>data;MeshPoint&operator[](PointIndex i)const{return data[int(i)];}
};
struct Mesh {
 using T_POINTS=PointStorage;PointStorage points;vector<Element>cells;
 MeshPoint&operator[](PointIndex i){return points[i];}Element&operator[](ElementIndex i){return cells[int(i)];}
 PointStorage&Points(){return points;}
 bool BoundaryEdge(PointIndex a,PointIndex b)const{return (int(a)+2*int(b))%7==0;}
 bool LegalTet(Element&e)const{
  if(!e.flags.illegal_valid){e.flags.illegal=(e.index%17==0);e.flags.illegal_valid=true;}
  return !e.flags.illegal;
 }
};
struct MeshingParameters{};
// Deterministic geometry surrogate; production scheduling/commit code is used.
double CalcTetBadness(const MeshPoint&a,const MeshPoint&b,const MeshPoint&c,const MeshPoint&d,double,const MeshingParameters&){
 double u[3],v[3],w[3];for(int j=0;j<3;++j){u[j]=b(j)-a(j);v[j]=c(j)-a(j);w[j]=d(j)-a(j);}
 double det=u[0]*(v[1]*w[2]-v[2]*w[1])-u[1]*(v[0]*w[2]-v[2]*w[0])+u[2]*(v[0]*w[1]-v[1]*w[0]);
 const MeshPoint*ps[]={&a,&b,&c,&d};double lengths=0;
 for(int i=0;i<4;++i)for(int k=0;k<i;++k)for(int j=0;j<3;++j)lengths+=pow((*ps[i])(j)-(*ps[k])(j),2);
 return pow(lengths,1.5)/max(1e-9,abs(det));
}
ostringstream diagnostics;ostream*testout=&diagnostics;
constexpr int tetedges[6][2]={{0,1},{0,2},{0,3},{1,2},{1,3},{2,3}};
struct MeshOptimize3d {
 Mesh&mesh;MeshingParameters mp;int goal;
 MeshOptimize3d(Mesh&m,int g):mesh(m),goal(g){}
 double GetLegalPenalty(){return goal==OPT_LEGAL?1e15:1e6;}
 double CombineImproveEdge(Table<ElementIndex,PointIndex>&,PointIndex,PointIndex,FlatArray<bool,PointIndex>,bool=false,vector<ElementIndex>*reports=nullptr);
};
}
#include "ordered_cavity_waves.hpp"
#include "combine_wave_verify.hpp"
namespace netgen {
'''

harness=r'''
}
using namespace netgen;
int main(){
 assert(!CavitySameBits(0.,-0.));
 {netgen_cavity::OrderedWaves p(10);vector<int>ids;int builds=0,phase=0;
  auto footprint=[&](size_t i){++builds;return i==0?vector<int>{1}:i==1?vector<int>{phase?8:1}:vector<int>{2};};
  assert(p.Next(0,3,4,footprint,ids)==1);phase=1;
  assert(p.Next(1,3,4,footprint,ids)==3);assert(builds==4);assert(find(ids.begin(),ids.end(),8)!=ids.end());
  bool rejected=false;try{p.Next(0,1,1,[](size_t){return vector<int>{10};},ids);}catch(const out_of_range&){rejected=true;}assert(rejected);
 }
 mt19937 gen(9013);int applied=0,parallel=0,checked=0;
 for(int sample=0;sample<160;++sample){
  int np=sample%2?48:96,ne=sample%2?90:48;
  Mesh initial;initial.points.data.resize(np+1);initial.cells.resize(ne);
  for(int i=1;i<=np;++i){auto&p=initial.points.data[i];for(int j=0;j<3;++j)p(j)=double(gen()%1000)/99.;p.layer=2;p.singular=double(i%3)/2;p.type=i%5?INNERPOINT:EDGEPOINT;}
  Table<ElementIndex,PointIndex> incidence(np+1);
  set<pair<int,int>> all_edges;
  for(int e=0;e<ne;++e){auto&el=initial.cells[e];set<int>used;
   if(sample%2){while(used.size()<4)used.insert(1+gen()%np);}
   else{const int shape[3][4]={{1,2,3,4},{1,3,4,5},{2,3,4,6}};for(int id:shape[e%3])used.insert(6*(e/3)+id);}
   int j=0;for(auto id:used){el.p[j++]=PointIndex(id);incidence[PointIndex(id)].push_back(ElementIndex(e));}
   el.index=e+1;el.type=e%23?TET:PRISM;el.flags.deleted=e%29==28;
   el.badness=1e5;for(int a=0;a<4;++a)for(int b=0;b<a;++b)all_edges.emplace(int(el[b]),int(el[a]));
  }
  vector<pair<PointIndex,PointIndex>>edges;
  for(auto[a,b]:all_edges)edges.emplace_back(PointIndex(a),PointIndex(b));
  vector<unsigned char>flags(np+1,0);FlatArray<bool,PointIndex>removed{&flags};
  Mesh probe=initial;MeshOptimize3d evaluation(probe,sample%3==0?OPT_CONFORM:OPT_REST);
  vector<pair<double,size_t>> candidates;
  for(size_t i=0;i<edges.size();++i){auto[a,b]=edges[i];double v=evaluation.CombineImproveEdge(incidence,a,b,removed,true);if(v<0)candidates.emplace_back(v,i);}
  sort(candidates.begin(),candidates.end());
  Mesh serial=probe,waves=probe;vector<unsigned char>serial_removed(np+1,0),wave_removed(np+1,0);
  FlatArray<bool,PointIndex>sr{&serial_removed},wr{&wave_removed};MeshOptimize3d s(serial,evaluation.goal),w(waves,evaluation.goal);
  vector<double>expected_results(candidates.size()),observed_results(candidates.size());
  vector<vector<ElementIndex>>expected_reports(candidates.size()),observed_reports(candidates.size());
  for(size_t i=0;i<candidates.size();++i){auto[a,b]=edges[candidates[i].second];expected_results[i]=s.CombineImproveEdge(incidence,a,b,sr,false,&expected_reports[i]);applied+=expected_results[i]<0;}
  netgen_cavity::OrderedWaves planner(np+1);vector<int>ids;
  size_t first=0;
  while(first<candidates.size()){
   auto end=planner.Next(first,candidates.size(),4,[&](size_t i){auto[a,b]=edges[candidates[i].second];vector<int>r{int(a),int(b)};
    for(auto pi:{a,b})for(auto ei:incidence[pi])for(auto pj:waves[ei].PNums())r.push_back(int(pj));return r;},ids);
   vector<ElementIndex>cells;for(size_t i=first;i<end;++i){auto[a,b]=edges[candidates[i].second];for(auto pi:{a,b})for(auto ei:incidence[pi])cells.push_back(ei);}
   CombineWaveState before(waves,wr,ids,cells);
   for(size_t i=first;i<end;++i){auto[a,b]=edges[candidates[i].second];vector<ElementIndex>messages;w.CombineImproveEdge(incidence,a,b,wr,false,&messages);}
   CombineWaveState expected(waves,wr,ids,cells);before.Restore(waves,wr);
   vector<thread>team;
   for(size_t i=first;i<end;++i)team.emplace_back([&,i]{auto[a,b]=edges[candidates[i].second];observed_results[i]=w.CombineImproveEdge(incidence,a,b,wr,false,&observed_reports[i]);});
   for(auto&t:team)t.join();assert(expected.Equal(waves,wr));
   if(end-first>1)++parallel;checked+=end-first;
   if(sample==0){waves[PointIndex(ids[0])](0)+=1;assert(!expected.Equal(waves,wr));expected.Restore(waves,wr);}
   first=end;
  }
  vector<int>all_points;for(int i=1;i<=np;++i)all_points.push_back(i);vector<ElementIndex>all_cells;for(int i=0;i<ne;++i)all_cells.emplace_back(i);
  CombineWaveState final(serial,sr,all_points,all_cells);assert(final.Equal(waves,wr));assert(expected_reports==observed_reports);
  for(size_t i=0;i<candidates.size();++i)assert(CavitySameBits(expected_results[i],observed_results[i]));
 }
 assert(applied>100 && parallel>100 && checked>1000);
 cout<<"PASS: 160 production collapse transactions, "<<checked<<" ordered attempts, "<<parallel<<" parallel waves, "<<applied<<" commits; exact state/replay/tamper checks\n";
}
'''

with tempfile.TemporaryDirectory() as tmp:
    cpp=Path(tmp)/'transactions.cpp';binary=Path(tmp)/'transactions'
    cpp.write_text(facade+extract('double CalcBadReplacePoints (')+'\n'+extract('double MeshOptimize3d :: CombineImproveEdge (')+'\n'+harness)
    flags=os.environ.get('CAVITY_TEST_FLAGS','-O1 -fsanitize=undefined -fno-sanitize-recover=all').split()
    subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-pthread',*flags,
                    '-I',str(ROOT/'netgen/libsrc/meshing'),str(cpp),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True,timeout=60)
