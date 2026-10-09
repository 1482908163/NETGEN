#!/usr/bin/env python3
"""Execute the actual candidate method with controlled geometry dependencies.

This checks control flow and mutation boundaries, not Netgen geometric accuracy.
The cluster's unchanged full mesh/adjacency fingerprint gate covers integration.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]
source=(ROOT/'netgen/libsrc/meshing/improve3.cpp').read_text()
def method(name):
    start=source.index('bool MeshOptimize3d::PrepareSplitCavity') if name=='PrepareSplitCavity' else source.index('bool MeshOptimize3d :: '+name) if name!='SplitImproveEdge' else source.index('double MeshOptimize3d :: '+name)
    begin=source.index('{',start);depth=1;end=begin+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]

prefix=r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <iostream>
#include <vector>
#include <stdexcept>
using NgException=std::runtime_error;
using std::max;
using PointIndex=int;
struct ElementIndex { int v; ElementIndex(int x=0):v(x){} bool operator==(ElementIndex b)const{return v==b.v;} };
template<class T> struct List:std::vector<T> {
 using std::vector<T>::vector;
 bool Contains(T x)const{return std::find(this->begin(),this->end(),x)!=this->end();}
 int Size()const{return int(this->size());}
 void Append(T x){this->push_back(x);} void SetSize(int n){this->resize(n);}
};
template<class T,int N> using ArrayMem=List<T>;
template<class T> using NgArray=List<T>;
template<class T> using FlatArray=List<T>;
template<class T,class I> struct Table:std::vector<List<T>>{using std::vector<List<T>>::vector;};
template<int N> using PointIndices=std::array<int,N>;
struct Point3d {double x=0,y=0,z=0;double& X(){return x;}double& Y(){return y;}double& Z(){return z;}double X()const{return x;}double Y()const{return y;}double Z()const{return z;}};
template<int N> using Point=Point3d;
Point3d Center(Point3d a,Point3d b){return {(a.x+b.x)/2,(a.y+b.y)/2,(a.z+b.z)/2};}
enum {TET=4,TRIG=3,OPT_LEGAL=1,OPT_QUALITY=2,OPT_REST=3};
struct Element2d:std::array<int,3>{explicit Element2d(int){} };
struct Element {
 std::array<int,4> ids{1,2,3,4}; bool legal=true,deleted=false;int type=TET,index=1;double badness=200;
 int& operator[](int i){return ids[i];} int operator[](int i)const{return ids[i];}
 bool IsDeleted()const{return deleted;} int GetType()const{return type;}int GetIndex()const{return index;}
 List<int> PNums()const{return List<int>(ids.begin(),ids.end());}
 double GetBadness()const{return badness;}void Touch(){}void Delete(){deleted=true;}
 void GetFace(int i,Element2d& face)const{int k=0;for(int j=0;j<4;j++)if(j!=i-1)face[k++]=ids[j];}
};
struct Mesh {
 std::vector<Element> els;std::vector<Point3d> points=std::vector<Point3d>(8); bool boundary=false;int mutations=0;
 Element& operator[](ElementIndex i){return els[i.v];}
 Point3d& operator[](PointIndex i){return points[i];}
 bool BoundaryEdge(int,int)const{return boundary;}bool LegalTet(Element& e)const{return e.legal;}
 auto& Points(){return points;}
 PointIndex AddPoint(Point3d p){++mutations;points.push_back(p);return int(points.size())-1;}
 void AddVolumeElement(Element e){++mutations;els.push_back(e);}
};
struct Stats {void Add(int,int){}};
struct Parameters {bool volume_legal_split_prune=false;int only3D_domain_nr=0;bool volume_split_active=false,volume_split_verify=false;Stats* volume_split_stats=nullptr;};
struct Vector {std::vector<double> a;explicit Vector(int n):a(n){}double& operator()(int i){return a[i];}};
static int expensive=0;
struct PointFunction1 {
 template<class P,class F> PointFunction1(P&,F&,Parameters&,int){}
 double Func(Vector&){++expensive;return 1;}
};
struct OptiParameters {int maxit_linsearch=0,maxit_bfgs=0;};
void BFGS(Vector&,PointFunction1&,OptiParameters&){++expensive;}
template<class P,class F> bool FindInnerPoint(P&,F&,Point3d&){++expensive;return true;}
bool WrongOrientation(Point3d,Point3d,Point3d,Point3d){return false;}
struct MeshOptimize3d {
 Mesh& mesh;Parameters mp;int goal=OPT_LEGAL;double min_badness=0;
 bool HasBadElement(FlatArray<ElementIndex>);
 bool HasIllegalElement(FlatArray<ElementIndex>);
 bool NeedsOptimization(FlatArray<ElementIndex>);
 struct SplitProposal {Point3d point;double badness=0;bool valid=false;};
 bool PrepareSplitCavity(Table<ElementIndex,PointIndex>&,PointIndex,PointIndex,ArrayMem<ElementIndex,20>&,double&,double&,bool*);
 double SplitImproveEdge(Table<ElementIndex,PointIndex>&,NgArray<PointIndices<3>>&,double,PointIndex,PointIndex,PointIndex,bool,bool*,SplitProposal* proposal=nullptr,bool reuse=false,bool* eligible=nullptr);
};
'''
suffix=r'''
int main(){
 int cases=0,avoided=0,quality_accepted=0;
 for(int goal:{OPT_LEGAL,OPT_QUALITY,OPT_REST})
 for(int mask=0;mask<16;++mask)
 for(int variant=0;variant<6;++variant)
 for(bool check_only:{false,true}) {
  double scores[2];int mutations[2],costs[2];bool pruned[2];
  for(int enabled=0;enabled<2;++enabled){
   Mesh m;for(int i=0;i<4;++i){Element e;e.legal=!(mask&(1<<i));m.els.push_back(e);}
   if(variant==1)m.boundary=true;
   if(variant==2)m.els[0].deleted=true;
   if(variant==3)m.els[0].type=5;
   if(variant==4)for(auto& e:m.els)e.badness=1;
   MeshOptimize3d opt{m};opt.goal=goal;opt.mp.volume_legal_split_prune=enabled;
   if(variant==5)opt.mp.only3D_domain_nr=2;
   Table<ElementIndex,PointIndex> incidence(8);for(int i=0;i<4;++i)incidence[1].Append(i);
   NgArray<PointIndices<3>> faces;expensive=0;
   scores[enabled]=opt.SplitImproveEdge(incidence,faces,0,1,2,7,check_only,&pruned[enabled]);
   mutations[enabled]=m.mutations;costs[enabled]=expensive;
   if(goal==OPT_LEGAL){assert(scores[enabled]==0);assert(m.mutations==0);}
   if(goal!=OPT_LEGAL)assert(!pruned[enabled]);
  }
  assert(scores[0]==scores[1]);assert(mutations[0]==mutations[1]);
  if(goal!=OPT_LEGAL)assert(costs[0]==costs[1]);
  if(costs[0]>costs[1])++avoided;
  if(goal==OPT_QUALITY && scores[0]<0)++quality_accepted;
  ++cases;
 }
 assert(avoided>0 && quality_accepted>0);
 std::cout<<"PASS: "<<cases<<" actual-method control-flow comparisons; "<<avoided<<" avoid expensive rejected work\n";
}
'''
with tempfile.TemporaryDirectory() as directory:
    tmp=Path(directory);cpp=tmp/'candidate.cpp';exe=tmp/'candidate'
    cpp.write_text(prefix+'\n'.join(method(name) for name in ('HasBadElement','HasIllegalElement','NeedsOptimization','PrepareSplitCavity','SplitImproveEdge'))+suffix)
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-Wno-missing-field-initializers',str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
