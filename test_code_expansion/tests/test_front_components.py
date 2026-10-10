#!/usr/bin/env python3
"""Execute production union plan AND the actual legacy relaxation loop.
The tiny container interfaces test labels, not geometry or performance.
"""
import os,re,subprocess,tempfile
from pathlib import Path
R=Path(__file__).resolve().parents[2]
s=(R/'netgen/libsrc/meshing/adfront3.cpp').read_text()
a=s.index('  auto original_labels=[&]() {')+len('  auto original_labels=[&]() {')
b=s.index('\n  };',a);legacy=s[a:b]
start=s.index('  if(component_stats) {',s.index('void AdFront3 :: RebuildInternalTables'))
end=s.index('  NgProfiler::StopTimer (timer_b)',start)
production=s[start:end]
prefix=r'''
#include "front_components.hpp"
#include <array>
#include <cassert>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>
struct PointIndex {static inline int BASE=0;int n;PointIndex(int a=0):n(a){}operator int()const{return n;} bool operator<(PointIndex b)const{return n<b.n;}bool operator>(PointIndex b)const{return n>b.n;}};
struct Point {PointIndex cluster;};
struct Points:std::vector<Point>{using std::vector<Point>::vector;std::vector<PointIndex> Range()const{std::vector<PointIndex> r;for(int i=PointIndex::BASE;i<int(size());++i)r.emplace_back(i);return r;}};
struct Face {std::array<int,4> p;const Face& FaceValue()const{return *this;}int PNum(int j)const{return p[j-1];}};
using MiniElement2d=Face;
struct FaceRecord {struct Face f;const struct Face& Face()const{return f;}};
struct Faces:std::vector<FaceRecord>{int Size()const{return size();}const FaceRecord& Get(int i)const{return at(i-1);}};
struct Stats {double values[16]={};void Add(int i,double x){values[i]+=x;}};
using netgen::FrontComponents;
struct VolumeFrontComponentTimer {VolumeFrontComponentTimer(Stats*,int){}};
using NgException=std::runtime_error;
int main(){
 std::mt19937 rng(20261010);size_t comparisons=0;
 for(int base:{0,1})for(int trial=0;trial<4000;++trial){
  PointIndex::BASE=base;int np=1+rng()%100;Points points(np+base);Faces faces;Stats stats;auto *component_stats=&stats;
  int nf=rng()%300;
  for(int i=0;i<nf;++i){FaceRecord f;for(auto &p:f.f.p)p=base+rng()%np;faces.push_back(f);}
  // Adversarial decreasing-label propagation requires O(N) original passes.
  if(trial%7==0){faces.clear();for(int i=np-1;i>0;--i)faces.push_back({{{base+i,base+i-1,base+i,base}}});}
  for(int i=0;i<np;++i)points[base+i].cluster={base+i};
'''
middle=r'''
  std::vector<int> expected;for(int i=0;i<np;++i)expected.push_back(int(points[base+i].cluster));
  for(int component_mode:{0,1,2}) {
    stats=Stats();for(int i=0;i<np;++i)points[base+i].cluster=PointIndex(base+i);
'''
check=r'''
    for(int i=0;i<np;++i)assert(int(points[base+i].cluster)==expected[i]);
    assert(stats.values[0]==1 && stats.values[1]==np && stats.values[2]==nf_actual);
    assert(stats.values[7]==(component_mode==2?1:0) && stats.values[8]==0);
    assert(stats.values[3]==(component_mode?2*nf_actual:0));
  }
'''
suffix=r'''
  netgen::FrontComponents plan(np);for(auto &f:faces){plan.Join(f.Face().PNum(1)-base,f.Face().PNum(2)-base);plan.Join(f.Face().PNum(1)-base,f.Face().PNum(3)-base);}
  for(int i=0;i<np;++i){assert(plan.Label(i)+base==int(points[base+i].cluster));++comparisons;}
  if(np>3){netgen::FrontComponents quad(4);quad.Join(0,1);quad.Join(0,2);assert(quad.Label(3)==3);}
 }
 // Incremental additions, duplicates, merges, disconnected and isolated points.
 netgen::FrontComponents p(100);assert(p.Join(9,7));assert(!p.Join(9,7));assert(p.Join(20,9));assert(p.Label(20)==7);assert(p.Label(6)==6);
 std::cout<<"PASS: 8000 fronts, "<<comparisons<<" exact production legacy labels; bases 0/1, reverse chains, duplicates, isolated points, first-three quad semantics\n";
}
'''
# Execute the actual replay failure path with an injected wrong expected label.
fault=production.replace('!=components.Label(int(pi)-PointIndex::BASE)+PointIndex::BASE', '!=components.Label(int(pi)-PointIndex::BASE)+PointIndex::BASE+1')
assert fault!=production
negative="""
  if(trial<10) {
    int component_mode=2;bool rejected=false;stats=Stats();
    for(int i=0;i<np;++i)points[base+i].cluster=PointIndex(base+i);
    try {
"""+fault+"""
    } catch(const NgException &error) {rejected=true;assert(std::string(error.what())=="front connected-component labels mismatch");}
    assert(rejected && stats.values[7]==1 && stats.values[8]==1);
  }
"""
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);cpp=p/'check.cpp';cpp.write_text(prefix+legacy+middle+production+check.replace("nf_actual","faces.Size()")+negative+suffix)
 subprocess.run(['g++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(R/'netgen/libsrc/meshing'),str(cpp),'-o',str(p/'check')],check=True)
 subprocess.run([str(p/'check')],check=True,env=dict(os.environ,ASAN_OPTIONS="detect_leaks=0"))
