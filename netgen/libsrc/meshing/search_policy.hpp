#ifndef NETGEN_SEARCH_POLICY_HPP
#define NETGEN_SEARCH_POLICY_HPP
#include <algorithm>
#include <cmath>
#include <numeric>
#include <vector>

namespace netgen {
// Empirical prediction bands, not statistical confidence guarantees. Work is
// observed search time, not element count; every process learns independently.
struct SearchEstimate {
  double mean=0, deviation=0;
  unsigned samples=0;
  void Observe(double value) {
    if(!std::isfinite(value) || value<0) return;
    if(!samples++) { mean=value; return; }
    deviation=.75*deviation+.25*std::abs(value-mean);
    mean=.75*mean+.25*value;
  }
  double Lower() const { return std::max(0.0,mean-deviation); }
  double Upper() const { return mean+deviation; }
};
struct SearchProfile {
  SearchEstimate serial, parallel, copy, overhead;
  std::vector<SearchEstimate> rules;
  std::vector<int> order;
  unsigned long long calls=0;
  // Logarithmic observation budget per structural bucket. Normal calls reuse
  // the learned ordering; they do not time every cheap rejected rule.
  bool Sample() {++calls;return (calls & (calls-1))==0;}

};
struct SearchAssignment {
  std::vector<int> local, remote;
  double local_seconds=0, remote_seconds=0;
};
inline SearchAssignment AssignSearch(const std::vector<double>& costs, int threads,
                                     bool remote, double remote_overhead=0) {
  SearchAssignment result;
  std::vector<int> order(costs.size());std::iota(order.begin(),order.end(),0);
  std::stable_sort(order.begin(),order.end(),[&](int a,int b){return costs[a]>costs[b];});
  std::vector<double> loads(std::max(1,threads),0.0);
  double remote_load=remote_overhead;
  for(int id:order) {
    auto slot=std::min_element(loads.begin(),loads.end());
    if(remote && remote_load<*slot) {
      result.remote.push_back(id);remote_load+=costs[id];
    } else {result.local.push_back(id);*slot+=costs[id];}
  }
  result.local_seconds=*std::max_element(loads.begin(),loads.end());
  result.remote_seconds=result.remote.empty()?0:remote_load;
  return result;
}
// Predicted reduction of the maximum remaining completion time. A faster
// noncritical rank earns no makespan benefit while another rank dominates.
inline double CriticalSearchGain(double self, double other, double saving) {
  return std::max(self,other)-std::max(std::max(0.0,self-saving),other);
}
}
#endif
