#include "front_point_lookup.hpp"
#include <cassert>
#include <climits>
#include <random>
#include <thread>

using namespace netgen;
int main(){
  std::mt19937 random(81741);
  for(int base:{0,1})for(int size:{0,1,7,37,511}){
    VolumeFrontLookupStats totals;
    {
      FrontLookupCounters stats(&totals);
      FrontPointLookup lookup(size,base);
      for(int candidate=0;candidate<300;++candidate){
        std::vector<int> mapped(random()%30);
        for(int &p:mapped)p=int(random()%(size+7))-3+base;
        if(mapped.size()>3){mapped[0]=INT_MIN;mapped[1]=INT_MAX;mapped[2]=mapped.back();}
        auto get=[&](int j){return mapped.at(j-1);};
        lookup.Build(int(mapped.size()),get,stats);
        for(int k=0;k<size+10;++k){
          int point=k-4+base;
          if(k==0)point=INT_MIN;
          if(k==1)point=INT_MAX;
          int expected=0;
          for(std::size_t j=0;j<mapped.size();++j)if(mapped[j]==point)expected=int(j)+1;
          assert(lookup.Resolve(point,int(mapped.size()),get,true,stats)==expected);
          assert(lookup.Resolve(point,int(mapped.size()),get,false,stats)==expected);
        }
      }
    }
    assert(totals.values[VolumeFrontLookupStats::table_clears]<=totals.values[VolumeFrontLookupStats::table_writes]);
  }
  VolumeFrontLookupStats empty,linear;
  {FrontLookupCounters stats(&empty);FrontPointLookup unused(100000,1);}
  {FrontLookupCounters stats(&linear);FrontPointLookup lookup(100000,1);
   for(int k=0;k<100;++k)assert(lookup.Resolve(3,7,[](int j){return j;},false,stats)==3);}
  assert(empty.values[VolumeFrontLookupStats::table_bytes_peak]==0);
  assert(linear.values[VolumeFrontLookupStats::table_bytes_peak]==0);
  assert(linear.values[VolumeFrontLookupStats::linear_comparisons]==700);
  VolumeFrontLookupStats shared;
  auto task=[&]{for(int k=0;k<50;++k){FrontLookupCounters stats(&shared);FrontPointLookup lookup(8,1);
    lookup.Build(3,[](int){return 2;},stats);
    assert(lookup.Resolve(2,3,[](int){return 2;},true,stats)==3);
    lookup.Build(1,[](int){return 4;},stats);
    assert(lookup.Resolve(2,1,[](int){return 4;},true,stats)==0);
    assert(lookup.Resolve(4,1,[](int){return 4;},true,stats)==1);
  }};
  std::vector<std::thread> threads;for(int k=0;k<4;++k)threads.emplace_back(task);for(auto &t:threads)t.join();
  assert(shared.values[VolumeFrontLookupStats::calls]==200);
  assert(shared.values[VolumeFrontLookupStats::table_writes]==800);
  assert(shared.values[VolumeFrontLookupStats::table_clears]==200);
  assert(shared.values[VolumeFrontLookupStats::direct_queries]==600);
  assert(shared.values[VolumeFrontLookupStats::linear_comparisons]==0);
  // Per-call peak, not the sum of all allocations or a process RSS claim.
  assert(shared.values[VolumeFrontLookupStats::table_bytes_peak]==8*sizeof(int)+sizeof(std::size_t));
}
