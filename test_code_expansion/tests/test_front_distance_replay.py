#!/usr/bin/env python3
"""Execute the production ApplyRules replay wrapper with controlled engine output.
Real free-zone transforms are separately tested; this is not a mesh/perf test.
"""
from pathlib import Path
import subprocess,tempfile
ROOT=Path(__file__).resolve().parents[2]
s=(ROOT/'netgen/libsrc/meshing/ruler3.cpp').read_text()
a=s.index('int Meshing3::ApplyRules(');b=s.index('int Meshing3 :: ApplyRulesImpl',a);wrapper=s[a:b]
prefix=r'''
#include <vector>
#include <string>
#include <memory>
#include <functional>
#include <chrono>
#include <cstring>
#include <cassert>
#include <stdexcept>
#include <iostream>
using std::string;using PointIndex=int;using INDEX=int;
template<class T,class I=int>struct Array:std::vector<T> {
 using std::vector<T>::vector;int Size()const{return int(this->size());}
 std::vector<int> Range()const{std::vector<int> r;for(int i=0;i<Size();++i)r.push_back(i);return r;}
};
template<class T>using NgArray=Array<T>;
struct Point3d {double x=0,y=0,z=0;double X()const{return x;}double Y()const{return y;}double Z()const{return z;}};
struct MiniElement2d {int np=3,p[4]={1,2,3,0};int GetNP()const{return np;}int PNum(int i)const{return p[i-1];}};
struct Element {int type=4,np=4,index=1,p[4]={1,2,3,4};int GetType()const{return type;}int GetNP()const{return np;}int GetIndex()const{return index;}int PNum(int i)const{return p[i-1];}};
template<class T>struct INDEX_2_HASHTABLE{};
struct VolumeKernelStats {double values[12]={};void Add(int i,double v){values[i]+=v;}};
struct vnetrule {
 using FreeZoneReplayState=std::vector<std::vector<unsigned char>>;
 FreeZoneReplayState zone{{0,1,2}};
 auto SaveFrontReplayState()const{return zone;}
 void RestoreFrontReplayState(const FreeZoneReplayState&s){zone=s;}
};
using NgException=std::runtime_error;double minother=10,minwithoutother=11;
struct Meshing3 {
 bool front_bound=false;bool front_bound_verify=false;
 int front_distance_mode=2,fault=0;VolumeKernelStats distance;
 VolumeKernelStats *front_distance_stats=&distance,*front_bound_stats=nullptr,*front_match_stats=nullptr;
 Array<int> foundmap{0},canuse{0};Array<string> problems{"before"};
 Array<std::unique_ptr<vnetrule>> rules;
 Meshing3(){rules.push_back(std::make_unique<vnetrule>());}
 int ApplyRules(Array<Point3d,PointIndex>&,Array<int,PointIndex>&,Array<MiniElement2d>&,INDEX,INDEX_2_HASHTABLE<int>&,NgArray<Element>&,NgArray<INDEX>&,int,double,int,float&);
 int ApplyRulesImpl(Array<Point3d,PointIndex>&p,Array<int,PointIndex>&,Array<MiniElement2d>&f,INDEX,INDEX_2_HASHTABLE<int>&,NgArray<Element>&e,NgArray<INDEX>&d,int,double,int,float&error) {
  if(front_distance_stats)front_distance_stats->Add(0,1);
  ++foundmap[0];++canuse[0];problems[0]="after";
  rules[0]->zone[0][0]++;minother=2;minwithoutother=3;
  p.push_back({1,2,3});f.push_back(MiniElement2d{});e={Element{}};d={1};error=0;
  if(!front_distance_mode) {
   if(fault==1)p.back().z=4;
   if(fault==2)f.back().p[1]=4;
   if(fault==3)e[0].index=2;
   if(fault==4)d[0]=2;
   if(fault==5)error=-0.f;
   if(fault==6)canuse[0]++;
   if(fault==7)rules[0]->zone[0][1]++;
   if(fault==8)minother=-0.;
   if(fault==9)problems[0]="wrong";
   if(fault==10)throw NgException("controlled engine exception");
   if(fault==11)return -1;
  }
  return 1;
 }
};
'''
main=r'''
int main() {
 for(int fault=0;fault<=11;++fault) {
  Meshing3 engine;engine.fault=fault;minother=10;minwithoutother=11;
  Array<Point3d,PointIndex> points{{0,0,0}};Array<int,PointIndex> allow{2};
  Array<MiniElement2d> faces{MiniElement2d{}};INDEX_2_HASHTABLE<int> pairs;
  NgArray<Element> elements;NgArray<INDEX> deleted;float error=1;
  bool failed=false;
  try{assert(engine.ApplyRules(points,allow,faces,1,pairs,elements,deleted,2,1,0,error)==1);}
  catch(const NgException&){failed=true;}
  assert(failed==(fault!=0));
  assert(engine.front_distance_mode==2 && engine.front_distance_stats==&engine.distance);
  assert(engine.foundmap[0]==1 && engine.canuse[0]==1 && engine.problems[0]=="after");
  assert(engine.rules[0]->zone[0][0]==1 && engine.rules[0]->zone[0][1]==1);
  assert(minother==2 && minwithoutother==3 && engine.distance.values[0]==1);
  assert(points.back().z==3 && elements[0].index==1 && error==0 && !std::signbit(error));
  assert(engine.distance.values[8]==(fault==10?0:1));
  assert(engine.distance.values[9]==(fault && fault!=10?1:0));
 }
 std::cout<<"PASS: actual ApplyRules wrapper rejects 10 output/state mismatches, restores optimized state on exceptions and isolates reference counters\n";
}
'''
# cmath is required for signed-zero assertion only, not copied engine arithmetic.
with tempfile.TemporaryDirectory() as tmp:
 tmp=Path(tmp);cpp=tmp/'replay.cpp';exe=tmp/'replay'
 cpp.write_text('#include <cmath>\n'+prefix+wrapper+main)
 subprocess.run(['g++','-std=c++17','-O2','-fsanitize=undefined','-fno-sanitize-recover=all','-Wall','-Wextra','-Werror',str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
