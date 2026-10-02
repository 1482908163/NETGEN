#!/usr/bin/env python3
"""Test actual weighted planner, native coloring and boundary exclusion; not BFGS geometry."""
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]
source=(ROOT/'netgen/libsrc/core/table.hpp').read_text()
start=source.index('  int ComputeColoring(')
begin=source.index('{',start);end=begin+1;depth=1
while depth:
    depth+=(source[end]=='{')-(source[end]=='}');end+=1
native='template<class Tmask>\n'+source[start:end]
prefix=r'''
#include "weighted_point_ranges.hpp"
#include <algorithm>
#include <cassert>
#include <iostream>
#include <numeric>
#include <random>
#include <string>
#include <thread>
#include <vector>
using std::size_t;
struct Timer {explicit Timer(std::string){}};
struct RegionTimer {explicit RegionTimer(Timer&){}};
std::string Demangle(const char* x){return x;}
template<class T>struct Array:std::vector<T>{
 using std::vector<T>::vector;
 size_t Size()const{return this->size();}
 Array& operator=(T x){std::fill(this->begin(),this->end(),x);return *this;}
};
template<class T>using FlatArray=Array<T>&;
auto Range(size_t n){std::vector<size_t> r(n);std::iota(r.begin(),r.end(),0);return r;}
'''
suffix=r'''
int main(){
 for(size_t n:{size_t(0),size_t(1),size_t(3),size_t(17),size_t(1000)})for(int workers:{1,2,4,64}) {
  std::vector<size_t> weights(n,0);if(n)weights[n/2]=1000000;
  auto chunks=netgen::MakeWeightedPointRanges(weights,workers);
  size_t next=0;for(auto [a,b]:chunks){assert(a==next && b>a && b<=n);next=b;}
  assert(next==n && chunks.size()==std::min(n,size_t(4*workers)));
 }
 int comparisons=0;std::mt19937 rng(901);
 for(int fixture=0;fixture<31;++fixture){
  int np=fixture==0?40:100;
  std::vector<std::vector<int>> cells;
  if(fixture==0){std::vector<int> clique(np);std::iota(clique.begin(),clique.end(),0);cells.push_back(clique);}
  else for(int j=0;j<120;++j){std::vector<int> c;while(c.size()<4){int p=rng()%np;if(std::find(c.begin(),c.end(),p)==c.end())c.push_back(p);}cells.push_back(c);}
  std::vector<std::vector<int>> stars(np);
  for(size_t e=0;e<cells.size();++e)for(int p:cells[e])stars[p].push_back(int(e));
  Array<int> colors(np);
  int nc=ComputeColoring(colors,cells.size(),[&](int p)->const auto&{return stars[p];});
  if(fixture==0)assert(nc==40); // cross the native 32-color mask boundary
  for(auto& cell:cells)for(int a:cell)for(int b:cell)if(a!=b)assert(colors[a]!=colors[b]);
  std::vector<std::vector<int>> groups(nc);for(int p=0;p<np;++p)groups[colors[p]].push_back(p);
  std::vector<double> initial(np);for(int p=0;p<np;++p)initial[p]=p*.125;
  auto execute=[&](int workers, bool balanced){
   auto points=initial;
   for(int sweep=0;sweep<3;++sweep)for(auto& group:groups){
    std::vector<int> ids;std::vector<size_t> weights;
    for(int p:group)if(!balanced || p%3!=0){ids.push_back(p);weights.push_back(stars[p].size());}
    auto chunks=netgen::MakeWeightedPointRanges(weights,workers);
    auto work=[&](int w){for(size_t c=w;c<chunks.size();c+=workers)for(size_t k=chunks[c].first;k<chunks[c].second;++k){
     int p=ids[k];if(p%3==0)continue;double sum=0;int count=0;
     for(int e:stars[p])for(int q:cells[e]){sum+=points[q];++count;}
     if(count)points[p]=.5*points[p]+.5*sum/count;
     std::this_thread::yield();
    }};
    std::vector<std::thread> team;for(int w=1;w<workers;++w)team.emplace_back(work,w);
    work(0);for(auto& thread:team)thread.join();
   }
   return points;
  };
  auto serial=execute(1,false);for(int workers:{1,2,4}){assert(serial==execute(workers,true));++comparisons;}
 }
 std::cout<<"PASS: native coloring conflicts, >32 colors, "<<comparisons<<" exact joined point-star comparisons\n";
}
'''
with tempfile.TemporaryDirectory() as directory:
    tmp=Path(directory);cpp=tmp/'color.cpp';exe=tmp/'color'
    cpp.write_text(prefix+native+suffix)
    subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread','-I'+str(ROOT/'netgen/libsrc/meshing'),str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
