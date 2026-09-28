#include "../../netgen/libsrc/meshing/conservative_box_index.hpp"
#include <cassert>
#include <iostream>
#include <limits>
#include <random>
using Index=netgen::ConservativeBoxIndex;
static bool overlap(const Index::Box &a,const Index::Box &b) {
  for(int k=0;k<3;++k)if(a.lo[k]>b.hi[k] || a.hi[k]<b.lo[k])return false;
  return true;
}
int main() {
  std::mt19937 random(20260928);
  for(int n:{0,1,8,9,100,1000}) {
    std::vector<Index::Box> boxes;
    for(int i=0;i<n;++i) {
      Index::Box b;
      for(int k=0;k<3;++k) {b.lo[k]=int(random()%200)-100;b.hi[k]=b.lo[k]+(i%3?random()%20:0);}
      boxes.push_back(b);
    }
    Index tree(boxes);
    for(int j=0;j<400;++j) {
      Index::Box q;
      for(int k=0;k<3;++k){q.lo[k]=int(random()%240)-120;q.hi[k]=q.lo[k]+random()%80;}
      std::vector<int> expected;
      for(int i=0;i<n;++i)if(overlap(boxes[i],q))expected.push_back(i);
      assert(tree.query(q)==expected);
    }
  }
  const Index::Box point{{1,2,3},{1,2,3}};
  assert(Index({point}).query({{0,0,0},{1,2,3}})==std::vector<int>{0});
  auto invalid=point;invalid.lo[0]=std::numeric_limits<double>::quiet_NaN();
  assert(Index({point,invalid}).query({{100,100,100},{101,101,101}})==std::vector<int>({0,1}));
  assert(Index({point,point}).query(invalid)==std::vector<int>({0,1}));
  const double max=std::numeric_limits<double>::max();
  std::vector<Index::Box> extremes(30,{{max/2,max/2,max/2},{max,max,max}});
  assert(Index(extremes).query(extremes[0]).size()==extremes.size());
  std::cout<<"PASS: 2400 conservative box queries match exhaustive ordered search; degeneracy, boundary, nonfinite fallback and extreme coordinates\n";
}
