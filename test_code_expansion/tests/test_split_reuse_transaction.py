#!/usr/bin/env python3
"""Execute the real split-edge function against a deterministic geometry double.
Checks transaction invalidation and exact commit order, not real BFGS geometry.
"""
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[2]
src=(ROOT/'netgen/libsrc/meshing/improve3.cpp').read_text()
a=src.index('bool MeshOptimize3d::PrepareSplitCavity');b=src.index('\nvoid MeshOptimize3d :: SplitImprove ()',a)
function=src[a:b]
# Same function with reuse disabled is the original recomputation path.
prefix=r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <iostream>
#include <numeric>
#include <random>
#include <set>
#include <stdexcept>
using NgException=std::runtime_error;
#include <tuple>
#include <vector>
using std::max;
struct PointIndex {int i=0; PointIndex()=default; PointIndex(int x):i(x){} operator int()const{return i;}};
struct ElementIndex {int i=0; ElementIndex()=default; ElementIndex(int x):i(x){} operator int()const{return i;}};
struct Point3d {
 double v[3]={0,0,0};Point3d()=default;Point3d(double x,double y,double z):v{x,y,z}{}
 double& X(){return v[0];}double X()const{return v[0];}
 double& Y(){return v[1];}double Y()const{return v[1];}
 double& Z(){return v[2];}double Z()const{return v[2];}
 bool operator==(const Point3d& p)const{return v[0]==p.v[0]&&v[1]==p.v[1]&&v[2]==p.v[2];}
};
template<int N>using Point=Point3d;
Point3d Center(Point3d a,Point3d b){return {(a.X()+b.X())/2,(a.Y()+b.Y())/2,(a.Z()+b.Z())/2};}
template<class T>struct Arr:std::vector<T>{
 using std::vector<T>::vector;
 int Size()const{return int(this->size());}
 void Append(T x){this->push_back(x);}void SetSize(int n){this->resize(n);}
 bool Contains(T x)const{return std::find(this->begin(),this->end(),x)!=this->end();}
};
template<class T,int N>using ArrayMem=Arr<T>;
template<class T>using NgArray=Arr<T>;
template<int N>using PointIndices=std::array<PointIndex,N>;
template<class T,class I>using Table=std::vector<Arr<T>>;
enum {TRIG=3,TET=4,OPT_LEGAL,OPT_QUALITY};
struct Element2d {PointIndex p[3];explicit Element2d(int){} PointIndex& operator[](int i){return p[i];}};
struct Element {
 Arr<PointIndex> p;bool deleted=false,legal=true;int type=TET,index=1;double bad=10000;
 Element():p(4){} PointIndex& operator[](int i){return p[i];}PointIndex operator[](int i)const{return p[i];}
 const Arr<PointIndex>& PNums()const{return p;}
 bool IsDeleted()const{return deleted;}int GetType()const{return type;}int GetIndex()const{return index;}
 double GetBadness()const{return bad;}void Touch(){}void Delete(){deleted=true;}
 void GetFace(int n,Element2d& f)const{int j=0;for(int k=0;k<4;++k)if(k!=n-1)f[j++]=p[k];}
 bool operator==(const Element& e)const{return p==e.p&&deleted==e.deleted&&legal==e.legal&&type==e.type&&index==e.index&&bad==e.bad;}
};
struct Stats {double v[12]={};void Add(int i,double x){v[i]+=x;}};
struct Params {int only3D_domain_nr=0;bool volume_legal_split_prune=false,volume_split_active=true,volume_split_verify=false;Stats* volume_split_stats=nullptr;};
struct Mesh {
 std::vector<Point3d> points;std::vector<Element> elements;std::set<std::pair<int,int>> boundary;
 Point3d& operator[](PointIndex p){return points[p.i];}Element& operator[](ElementIndex e){return elements[e.i];}
 auto& Points(){return points;}bool BoundaryEdge(PointIndex a,PointIndex b){return boundary.count({a.i,b.i});}
 bool LegalTet(Element& e){return e.legal;}
 PointIndex AddPoint(Point3d p){points.push_back(p);return int(points.size()-1);}
 void AddVolumeElement(Element e){elements.push_back(e);}
};
struct Vector:std::vector<double>{using std::vector<double>::vector;double& operator()(int i){return (*this)[i];}double operator()(int i)const{return (*this)[i];}};
struct PointFunction1 {
 std::vector<Point3d>& points;NgArray<PointIndices<3>>& faces;
 PointFunction1(std::vector<Point3d>& p,NgArray<PointIndices<3>>& f,const Params&,int):points(p),faces(f){}
 double Func(const Vector& x)const{return faces.Size()+x(0)*x(0)+x(1)*x(1)+x(2)*x(2);}
};
struct OptiParameters {int maxit_linsearch,maxit_bfgs;};
int optimizations=0;
void BFGS(Vector& x,PointFunction1& pf,OptiParameters&){
 ++optimizations;for(int k=0;k<3;++k){double s=0;for(auto f:pf.faces)for(auto p:f)s+=pf.points[p.i].v[k];x(k)=.5*x(k)+.5*s/(3*pf.faces.Size());}
}
bool FindInnerPoint(std::vector<Point3d>&,NgArray<PointIndices<3>>&,Point3d&){return true;}
bool WrongOrientation(Point3d,Point3d,Point3d,Point3d){return false;}
struct MeshOptimize3d {
 Mesh& mesh;Params mp;int goal=OPT_QUALITY;
 struct SplitProposal {Point3d point;double badness=0;bool valid=false;};
 bool NeedsOptimization(const Arr<ElementIndex>& es){return !es.empty();}
 bool PrepareSplitCavity(Table<ElementIndex,PointIndex>&,PointIndex,PointIndex,ArrayMem<ElementIndex,20>&,double&,double&,bool*);
 double SplitImproveEdge(Table<ElementIndex,PointIndex>&,NgArray<PointIndices<3>>&,double,PointIndex,PointIndex,PointIndex,bool,bool*,SplitProposal*,bool,bool* eligible=nullptr);
};
'''
suffix=r'''
Table<ElementIndex,PointIndex> incidence(const Mesh& m){
 Table<ElementIndex,PointIndex> t(m.points.size());for(size_t i=0;i<m.elements.size();++i)for(auto p:m.elements[i].p)t[p.i].Append(int(i));return t;
}
struct Outcome {Mesh mesh;int bfgs;double reused;std::vector<int> accepted;};
Outcome execute(Mesh mesh,bool reuse,bool verify=false){
 auto table=incidence(mesh);std::set<std::pair<int,int>> unique;
 for(auto el:mesh.elements)for(int i=0;i<4;++i)for(int j=i+1;j<4;++j)unique.insert(std::minmax(int(el[i]),int(el[j])));
 std::vector<std::pair<int,int>> edges(unique.begin(),unique.end());
 std::vector<MeshOptimize3d::SplitProposal> proposals(edges.size());
 Stats stats;MeshOptimize3d opt{mesh,Params{}};opt.mp.volume_split_stats=&stats;
 opt.mp.volume_split_verify=verify;
 auto tmp=mesh.AddPoint({0,0,0});NgArray<PointIndices<3>> faces;
 std::vector<std::pair<double,int>> candidates;optimizations=0;
 for(size_t i=0;i<edges.size();++i){auto [a,b]=edges[i];double d=opt.SplitImproveEdge(table,faces,0,a,b,tmp,true,nullptr,reuse?&proposals[i]:nullptr,false);if(d<0)candidates.emplace_back(d,i);}
 std::sort(candidates.begin(),candidates.end());std::vector<int> accepted;
 for(auto [d,i]:candidates){auto [a,b]=edges[i];double result=opt.SplitImproveEdge(table,faces,0,a,b,tmp,false,nullptr,reuse?&proposals[i]:nullptr,reuse);if(result<0)accepted.push_back(i);}
 return {mesh,optimizations,stats.v[3],accepted};
}
int main(){
 std::mt19937 rng(73519);int total_reused=0;
 for(int trial=0;trial<100;++trial){
  Mesh m;for(int p=0;p<30;++p)m.points.emplace_back(double(rng()%100)/100,double(rng()%100)/100,double(rng()%100)/100);
  for(int n=0;n<24;++n){Element e;std::set<int> ps;while(ps.size()<4)ps.insert(rng()%30);int i=0;for(auto p:ps)e[i++]=p;
   if(trial%5==0 && n%7==0)e.legal=false;
   if(trial%7==0 && n%9==0)e.type=5;
   if(trial%3==0 && n%3==0)e.bad=1;
   m.elements.push_back(e);
  }
  if(trial%2==0)m.boundary.insert({0,1});
  auto a=execute(m,false),b=execute(m,true),v=execute(m,true,true);
  assert(v.mesh.points==b.mesh.points && v.mesh.elements==b.mesh.elements && v.bfgs==a.bfgs);
  assert(a.mesh.points==b.mesh.points && a.mesh.elements==b.mesh.elements && a.accepted==b.accepted);
  assert(b.bfgs<=a.bfgs);total_reused+=int(b.reused);
  if(b.reused)assert(b.bfgs<a.bfgs);
 }
 // A deleted cell outside the edge cavity, but in the first endpoint star,
 // must invalidate the candidate before any cached proposal can be used.
 Mesh m;for(int p=0;p<7;++p)m.points.emplace_back(p*.1,p*.2,p*.3);
 Element a,b;for(int k=0;k<4;++k)a[k]=k;b[0]=0;b[1]=4;b[2]=5;b[3]=6;m.elements={a,b};
 auto table=incidence(m);auto tmp=m.AddPoint({0,0,0});Stats stats;MeshOptimize3d opt{m,Params{}};opt.mp.volume_split_stats=&stats;
 MeshOptimize3d::SplitProposal proposal;NgArray<PointIndices<3>> faces;
 assert(opt.SplitImproveEdge(table,faces,0,0,1,tmp,true,nullptr,&proposal,false)<0);
 m.elements[1].Delete();auto n=m.points.size();
 assert(opt.SplitImproveEdge(table,faces,0,0,1,tmp,false,nullptr,&proposal,true)==0);
 assert(m.points.size()==n && stats.v[3]==0 && total_reused>0);
 m.elements[1].deleted=false;opt.mp.volume_split_verify=true;
 proposal.badness+=1;bool failed=false;
 try {opt.SplitImproveEdge(table,faces,0,0,1,tmp,false,nullptr,&proposal,true);}catch(const NgException&){failed=true;}
 assert(failed && stats.v[11]==1);
 std::cout<<"PASS: actual split-edge function, 100 ordered transactions, intact-star invalidation, reused="<<total_reused<<"\n";
}
'''
with tempfile.TemporaryDirectory() as directory:
    tmp=Path(directory);cpp=tmp/'split.cpp';exe=tmp/'split'
    cpp.write_text(prefix+function+suffix)
    subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-pthread',str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
