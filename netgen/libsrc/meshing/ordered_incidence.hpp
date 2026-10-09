#ifndef NETGEN_ORDERED_INCIDENCE_HPP
#define NETGEN_ORDERED_INCIDENCE_HPP

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <limits>
#include <stdexcept>
#include <vector>

namespace netgen_incidence {
// Each shard visits a contiguous increasing element-index interval. Counting
// and filling repeat the same immutable traversal. Row segments are assigned
// in shard order, so no atomics or row sort is needed; duplicates are retained.
class OrderedPlan {
  std::size_t points, shards;
  std::vector<std::size_t> cursor, sizes;
public:
  static constexpr std::size_t scratch_limit = 64u * 1024u * 1024u;
  static bool Fits(std::size_t np, std::size_t ns) {
    return ns > 0 && ns < std::numeric_limits<std::size_t>::max()
      && np <= scratch_limit / sizeof(std::size_t) / (ns + 1);
  }
  OrderedPlan(std::size_t np, std::size_t ns) : points(np), shards(ns) {
    if(!Fits(np,ns)) throw std::length_error("ordered incidence scratch budget");
    cursor.resize(np*ns); sizes.resize(np);
  }
  std::size_t ScratchBytes() const { return (cursor.size()+sizes.size())*sizeof(std::size_t); }
  std::vector<std::size_t>& Sizes() { return sizes; }
  template<class VISIT, class EXECUTE>
  void Count(VISIT visit, EXECUTE execute) {
    execute(shards,[&](std::size_t shard) {
      auto *local=points ? cursor.data()+shard*points : nullptr;
      visit(shard,[&](std::size_t point,auto) {
        if(point>=points)throw std::out_of_range("ordered incidence point index");
        ++local[point];
      });
    });
    // Different points own different cursor cells, also in the prefix pass.
    execute(points,[&](std::size_t point) {
      std::size_t total=0;
      for(std::size_t shard=0;shard<shards;++shard) {
        auto &cell=cursor[shard*points+point];
        auto count=cell; cell=total;
        if(count>std::numeric_limits<std::size_t>::max()-total)
          throw std::overflow_error("ordered incidence row size");
        total+=count;
      }
      sizes[point]=total;
    });
  }
  template<class VISIT,class WRITE,class EXECUTE>
  void Fill(VISIT visit,WRITE write,EXECUTE execute) {
    execute(shards,[&](std::size_t shard) {
      auto *local=points ? cursor.data()+shard*points : nullptr;
      visit(shard,[&](std::size_t point,auto element) {
        if(point>=points)throw std::out_of_range("ordered incidence point index");
        write(point,local[point]++,element);
      });
    });
  }
};
}

namespace netgen {
struct VolumeIncidenceStats {
  static constexpr int phases=3,operations=4,width=12,count=phases*operations*width;
  enum Field { calls,input_points,input_elements,entries,ordered_builds,fallback_builds,
    avoided_atomic_updates,verified,mismatches,build_seconds,reference_seconds,scratch_peak_bytes };
  std::array<std::atomic<double>,count> values;
  VolumeIncidenceStats() { for(auto &v:values)v.store(0); }
  void Add(int i,double value) {
    double old=values[i].load(std::memory_order_relaxed);
    while(!values[i].compare_exchange_weak(old,old+value,std::memory_order_relaxed)) {}
  }
  void Max(int i,double value) {
    double old=values[i].load(std::memory_order_relaxed);
    while(old<value && !values[i].compare_exchange_weak(old,value,std::memory_order_relaxed)) {}
  }
};
}
#endif
