#ifndef NETGEN_FRONT_POINT_LOOKUP_HPP
#define NETGEN_FRONT_POINT_LOOKUP_HPP
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <vector>

namespace netgen {
struct VolumeFrontLookupStats {
  enum Field { calls, candidates, queried_candidates, queries,
    baseline_comparisons, linear_comparisons, direct_queries, table_builds,
    table_writes, table_clears, table_bytes_peak, verified, mismatches,
    reference_seconds, count };
  std::array<std::atomic<double>,count> values;
  VolumeFrontLookupStats(){for(auto &v:values)v.store(0);}
  void Add(int field,double value){
    double old=values[field].load(std::memory_order_relaxed);
    while(!values[field].compare_exchange_weak(old,old+value,std::memory_order_relaxed)){}
  }
  void Peak(int field,double value){
    double old=values[field].load(std::memory_order_relaxed);
    while(old<value && !values[field].compare_exchange_weak(old,value,std::memory_order_relaxed)){}
  }
};

// No clocks or atomics in candidate/face loops. The existing full-rule and
// native front timers include allocation, construction, clearing and teardown.
class FrontLookupCounters {
  VolumeFrontLookupStats *stats;
  std::array<double,VolumeFrontLookupStats::count> values{};
public:
  explicit FrontLookupCounters(VolumeFrontLookupStats *s):stats(s){Count(VolumeFrontLookupStats::calls);}
  ~FrontLookupCounters(){if(stats)for(int i=0;i<VolumeFrontLookupStats::count;++i){
    if(i==VolumeFrontLookupStats::table_bytes_peak)stats->Peak(i,values[i]);
    else stats->Add(i,values[i]);
  }}
  void Count(int field,double value=1){if(stats)values[field]+=value;}
  void Peak(double value){if(stats)values[VolumeFrontLookupStats::table_bytes_peak]=std::max(values[VolumeFrontLookupStats::table_bytes_peak],value);}
};

// Storage lives for exactly one ApplyRules call. Build only on the first
// overlapping, unused face of a candidate. Increasing rule indices preserve
// the original loop's last-match-wins behavior even with duplicate mappings.
class FrontPointLookup {
  std::size_t point_count;
  int base;
  std::vector<int> inverse;
  std::vector<std::size_t> touched;
  bool Contains(int p)const{
    auto offset=std::int64_t(p)-std::int64_t(base);
    return offset>=0 && std::uint64_t(offset)<point_count;
  }
  std::size_t Offset(int p)const{return std::size_t(std::int64_t(p)-std::int64_t(base));}
public:
  FrontPointLookup(std::size_t n,int first):point_count(n),base(first){}
  template<class Get> void Build(int old_points,Get get,FrontLookupCounters &stats){
    if(inverse.empty())inverse.resize(point_count,0);
    stats.Count(VolumeFrontLookupStats::table_clears,touched.size());
    for(auto p:touched)inverse[p]=0;
    touched.clear();
    stats.Count(VolumeFrontLookupStats::table_builds);
    for(int j=1;j<=old_points;++j){
      int p=get(j);
      if(!Contains(p))continue;
      auto offset=Offset(p);
      if(inverse[offset]==0)touched.push_back(offset);
      inverse[offset]=j;
      stats.Count(VolumeFrontLookupStats::table_writes);
    }
    stats.Peak(inverse.capacity()*sizeof(int)+touched.capacity()*sizeof(std::size_t));
  }
  template<class Get> int Resolve(int point,int old_points,Get get,bool use_inverse,FrontLookupCounters &stats)const{
    if(use_inverse && Contains(point)){
      stats.Count(VolumeFrontLookupStats::direct_queries);
      return inverse[Offset(point)];
    }
    // Out-of-range indices retain original comparison semantics, not a guessed 0.
    stats.Count(VolumeFrontLookupStats::linear_comparisons,old_points);
    int result=0;
    for(int j=1;j<=old_points;++j)if(get(j)==point)result=j;
    return result;
  }
};
}
#endif
