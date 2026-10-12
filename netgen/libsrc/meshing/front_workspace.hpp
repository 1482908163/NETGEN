#ifndef NETGEN_FRONT_WORKSPACE_HPP
#define NETGEN_FRONT_WORKSPACE_HPP
#include <array>
#include <atomic>
#include <algorithm>
namespace netgen {
struct VolumeFrontWorkspaceStats {
  enum Field {calls,transforms,vector_entries,vector_requests,baseline_heap_requests,
    heap_requests,reused_requests,workspace_hits,allocated_entries,retained_entries_peak,
    initialized_rules,verified,mismatches,reference_seconds,count};
  std::array<std::atomic<double>,count> values;
  VolumeFrontWorkspaceStats(){for(auto &v:values)v.store(0);}
  void Add(int field,double value){double old=values[field].load(std::memory_order_relaxed);
    while(!values[field].compare_exchange_weak(old,old+value,std::memory_order_relaxed)){} }
  void Peak(int field,double value){double old=values[field].load(std::memory_order_relaxed);
    while(old<value&&!values[field].compare_exchange_weak(old,value,std::memory_order_relaxed)){} }
};
// No clocks/atomics inside the candidate transform; one aggregation per rule call.
class FrontWorkspaceCounters {
  VolumeFrontWorkspaceStats *stats;
  std::array<double,VolumeFrontWorkspaceStats::count> values{};
public:
  explicit FrontWorkspaceCounters(VolumeFrontWorkspaceStats *s):stats(s){if(stats)values[0]=1;}
  ~FrontWorkspaceCounters(){if(stats)for(int f=0;f<VolumeFrontWorkspaceStats::count;++f){
    if(f==VolumeFrontWorkspaceStats::retained_entries_peak)stats->Peak(f,values[f]);
    else stats->Add(f,values[f]);
  }}
  double *Data(){return stats?values.data():nullptr;}
};
}
#endif
