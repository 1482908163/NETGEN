#!/usr/bin/env python3
"""Actual badness formula with real arithmetic; discrete geometry outcomes are
controlled inputs for pruning-state tests, not real intersection tests.
"""
from pathlib import Path
import subprocess,tempfile
ROOT=Path(__file__).resolve().parents[2]
s=(ROOT/'netgen/libsrc/meshing/ruler3.cpp').read_text()
a=s.index('  static double CalcElementBadness');b=s.index('\nint Meshing3::ApplyRules',a)
formula=s[a:b]
prefix=r'''
#include <array>
#include <vector>
#include <cmath>
#include <cassert>
#include <cstring>
#include <limits>
#include <iostream>
#include <random>
#include "front_quality_bound.hpp"
using PointIndex=int;
struct Point3d {
 double v[3]={};Point3d()=default;Point3d(double x,double y,double z):v{x,y,z}{}
 double& X(){return v[0];}double X()const{return v[0];}
 double& Y(){return v[1];}double Y()const{return v[1];}
 double& Z(){return v[2];}double Z()const{return v[2];}
};
struct Vec3d {double v[3];double Length()const{return std::sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2]);}};
Vec3d operator-(const Point3d&a,const Point3d&b){return {{a.X()-b.X(),a.Y()-b.Y(),a.Z()-b.Z()}};}
Vec3d Cross(const Vec3d&a,const Vec3d&b){return {{a.v[1]*b.v[2]-a.v[2]*b.v[1],a.v[2]*b.v[0]-a.v[0]*b.v[2],a.v[0]*b.v[1]-a.v[1]*b.v[0]}};}
double operator*(const Vec3d&a,const Vec3d&b){return a.v[0]*b.v[0]+a.v[1]*b.v[1]+a.v[2]*b.v[2];}
double Dist(const Point3d&a,const Point3d&b){return (a-b).Length();}
template<class T,class I>using Array=std::vector<T>;
struct Element {std::array<int,6> p;int np=4;int GetNP()const{return np;}int PNum(int i)const{return p[i-1];}};
'''
suffix=r'''
bool exact(float a,float b){return std::memcmp(&a,&b,sizeof(float))==0;}
struct Candidate {float quality;bool topology,freezone,orientation,tetrahedral,known_possible,debug;};
struct Outcome {int selected,usable,returned,geometry_calls;float best;};
Outcome run(const std::vector<Candidate>& stream,int tolerance,float sloppy,bool optimized) {
 float best=sloppy*tolerance;int found=0,usable=0,calls=0;bool impossible=true;
 for(size_t i=0;i<stream.size();++i) {
  auto c=stream[i];if(c.known_possible)impossible=false;
  if(optimized && !c.debug && !c.topology)continue;
  if(optimized && c.topology && c.tetrahedral &&
     netgen::FrontQualityCannotWin(c.quality,best,tolerance,impossible,found,c.debug))continue;
  ++calls; // real free-zone/intersection outcomes are supplied independently
  if(!c.topology || !c.freezone)continue;
  if(c.orientation && c.quality<tolerance)++usable;
  if(c.quality>best)impossible=false;
  if(c.orientation && c.quality<best){found=int(i)+1;best=c.quality;}
 }
 return {found,usable,impossible && !found?-1:found,calls,best};
}
int main() {
 std::mt19937 rng(77192026);std::vector<float> qualities;
 int comparisons=0,skipped=0;
 for(int trial=0;trial<20000;++trial) {
  Array<Point3d,PointIndex> original(31),projected(7);
  auto coordinate=[&](){return (int(rng()%20001)-10000)/4096.0;};
  for(int i=1;i<=30;++i)original[i]={coordinate(),coordinate(),coordinate()};
  std::array<int,7> map{0,13,4,27,8,0,0};
  std::array<Point3d,7> rule_points;
  double allp[18]={};
  for(int i=1;i<=4;++i)for(int k=0;k<3;++k)allp[3*i-3+k]=original[map[i]].v[k];
  for(int i=5;i<=6;++i) {
   rule_points[i]={coordinate(),coordinate(),coordinate()};
   Point3d actual=rule_points[i];
   for(int k=0;k<3;++k) {
    double u=coordinate();
    allp[3*i-3+k]=rule_points[i].v[k]+u;
    actual.v[k]+=u; // original append path, independent of allp
   }
   map[i]=int(original.size());original.push_back(actual);
  }
  for(int i=1;i<=6;++i)projected[i]={allp[3*i-3],allp[3*i-2],allp[3*i-1]};
  std::array<Element,3> proposals{{{{1,2,3,5,0,0},4},{{1,3,5,6,0,0},4},{{2,3,4,6,0,0},4}}};
  float old_max=0,new_max=0;
  for(auto element:proposals) {
   Element actual=element;
   for(int j=0;j<4;++j)actual.p[j]=map[element.p[j]];
   double old_value=CalcElementBadness(original,actual);
   double new_value=CalcElementBadness(projected,element);
   assert(std::memcmp(&old_value,&new_value,sizeof(double))==0);
   if(old_value>old_max)old_max=old_value;
   if(new_value>new_max)new_max=new_value;
   ++comparisons;
  }
  assert(exact(old_max,new_max));qualities.push_back(new_max);
 }
 qualities.insert(qualities.end(),{0,.5f,1,2,3,5,10,std::nextafter(1.f,0.f),std::nextafter(1.f,2.f)});
 for(int trial=0;trial<20000;++trial) {
  std::vector<Candidate> stream;
  for(int i=0;i<40;++i)stream.push_back({qualities[rng()%qualities.size()],bool(rng()%5),bool(rng()%2),bool(rng()%4),bool(rng()%5),rng()%7==0,rng()%9==0});
  int tolerance=1+rng()%8;float sloppy=.5f*(1+rng()%12);
  auto a=run(stream,tolerance,sloppy,false),b=run(stream,tolerance,sloppy,true);
  assert(a.selected==b.selected && a.usable==b.usable && a.returned==b.returned && exact(a.best,b.best));
  assert(b.geometry_calls<=a.geometry_calls);skipped+=a.geometry_calls-b.geometry_calls;
 }
 // A quality failure alone must not turn an untested impossible front from
 // -1 into 0. Ties must not remove an otherwise usable diagnostic candidate.
 assert(!netgen::FrontQualityCannotWin(10,1,1,true,0,false));
 assert(netgen::FrontQualityCannotWin(1,1,1,false,0,false));
 assert(!netgen::FrontQualityCannotWin(.5f,.5f,1,false,1,false));
 assert(!netgen::FrontQualityCannotWin(10,1,1,false,0,true));
 assert(!netgen::FrontQualityCannotWin(std::numeric_limits<float>::infinity(),1,1,false,0,false));
 assert(!netgen::FrontQualityCannotWin(std::numeric_limits<float>::quiet_NaN(),1,1,false,0,false));
 assert(skipped>0);
 std::cout<<"PASS: "<<comparisons<<" actual objective comparisons; 20000 ordered state sequences, avoided geometry="<<skipped<<"\n";
}
'''
with tempfile.TemporaryDirectory() as directory:
    tmp=Path(directory);cpp=tmp/'bound.cpp';exe=tmp/'bound'
    cpp.write_text(prefix+formula+suffix)
    subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror',
        '-I',str(ROOT/'netgen/libsrc/meshing'),str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
