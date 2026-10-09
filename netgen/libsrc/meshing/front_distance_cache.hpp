#ifndef NETGEN_FRONT_DISTANCE_CACHE_HPP
#define NETGEN_FRONT_DISTANCE_CACHE_HPP
#include <cstddef>
#include <cstdint>
#include <vector>

namespace netgen {
// Lifetime: ONE ApplyRules invocation. Rule and local coordinates stay fixed
// during its backtracking. Only memoize the original double expression; never
// cache acceptance, point occupancy, mapping, or free-zone state.
class FrontDistanceCache {
  struct Entry { double value=0; std::uint64_t rule=0; };
  std::vector<std::vector<Entry>> rows;
  std::size_t local_points, cells=0;
  static constexpr std::size_t max_cells=65536; // bounded to 1 MiB of entries
public:
  explicit FrontDistanceCache(std::size_t n):local_points(n) {}
  std::size_t Cells() const {return cells;}
  template<class Compute>
  double Get(std::uint64_t rule,std::size_t reference,std::size_t local,
             Compute compute,double *counts) {
    if(!rule || local>=local_points || reference>=max_cells ||
       local_points>max_cells) {
      ++counts[3];++counts[4];return compute();
    }
    if(reference>=rows.size())rows.resize(reference+1);
    auto &row=rows[reference];
    if(row.empty()) {
      if(local_points>max_cells-cells) {
        ++counts[3];++counts[4];return compute();
      }
      row.resize(local_points);cells+=local_points;counts[11]=double(cells);
    }
    auto &entry=row[local];
    if(entry.rule==rule) {++counts[2];return entry.value;}
    entry.value=compute();entry.rule=rule;++counts[3];return entry.value;
  }
};
}
#endif
