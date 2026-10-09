#include "front_distance_cache.hpp"
#include <array>
#include <cassert>
#include <cmath>
#include <cstring>
#include <iostream>
#include <limits>
#include <random>
using netgen::FrontDistanceCache;
struct Point {double x[3];};
double Dist2(const Point &a,const Point &b) {
  return (a.x[0]-b.x[0])*(a.x[0]-b.x[0])+
    (a.x[1]-b.x[1])*(a.x[1]-b.x[1])+
    (a.x[2]-b.x[2])*(a.x[2]-b.x[2]);
}
int main() {
  std::mt19937 rng(77192026);std::size_t comparisons=0;
  for(int trial=0;trial<1000;++trial) {
    std::array<Point,31> local{};std::array<Point,7> ref{};
    auto coordinate=[&](){return (int(rng()%20001)-10000)/4096.;};
    for(auto &p:local)for(double &x:p.x)x=coordinate();
    for(auto &p:ref)for(double &x:p.x)x=coordinate();
    FrontDistanceCache cache(local.size());double counts[12]={};std::size_t evaluated=0;
    for(int rule=1;rule<=3;++rule) {
      // Different rules reuse reference/local indices with different geometry.
      for(auto &p:ref)for(double &x:p.x)x=coordinate();
      double factor=(trial%11+1)/7.;
      for(int q=0;q<700;++q) {
        auto i=rng()%ref.size(),j=rng()%local.size();
        double expected=Dist2(local[j],ref[i])*factor;
        double actual=cache.Get(rule,i,j,[&](){++evaluated;return Dist2(local[j],ref[i])*factor;},counts);
        assert(std::memcmp(&actual,&expected,sizeof(double))==0);
        for(double threshold:{expected,std::nextafter(expected,0.),std::nextafter(expected,INFINITY)})
          assert((actual>threshold)==(expected>threshold));
        ++comparisons;
      }
    }
    assert(counts[2]>0 && counts[2]+counts[3]==2100 && counts[3]==evaluated);
    assert(cache.Cells()==7*31 && counts[11]==cache.Cells());
  }
  FrontDistanceCache cache(2);double counts[12]={};
  for(double value:{-0.,std::numeric_limits<double>::quiet_NaN(),double(INFINITY),-double(INFINITY)}) {
    static unsigned rule=1;
    auto a=cache.Get(rule,0,0,[&](){return value;},counts);
    auto b=cache.Get(rule++,0,0,[](){assert(false);return 0.;},counts);
    assert(std::memcmp(&a,&value,sizeof(double))==0 && std::memcmp(&b,&value,sizeof(double))==0);
  }
  FrontDistanceCache bounded(65536);double bounded_counts[12]={};
  bounded.Get(1,0,65535,[](){return 2.;},bounded_counts);
  assert(bounded.Get(1,1,2,[](){return 3.;},bounded_counts)==3.);
  assert(bounded.Cells()==65536 && bounded_counts[4]==1);
  FrontDistanceCache empty(0),oversized(65537);
  assert(empty.Get(1,0,0,[](){return 4.;},bounded_counts)==4.);
  assert(oversized.Get(1,0,0,[](){return 5.;},bounded_counts)==5.);
  assert(oversized.Cells()==0);
  // A new ApplyRules invocation must not inherit coordinates from the last one.
  FrontDistanceCache next(2);double next_counts[12]={};
  assert(next.Get(1,0,0,[](){return 123.;},next_counts)==123. && next_counts[2]==0);
  std::cout<<"PASS: "<<comparisons<<" ordered bit-exact distances, rule/call isolation and memory fallback\n";
}
