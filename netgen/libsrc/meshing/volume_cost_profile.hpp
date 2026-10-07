#ifndef NETGEN_VOLUME_COST_PROFILE_HPP
#define NETGEN_VOLUME_COST_PROFILE_HPP

#include <array>
#include <atomic>
#include <chrono>

namespace netgen {
// Host wall times for complete operations, including their synchronous worker
// jobs. Buckets partition each call; goals partition total_seconds separately.
// Input counts are repeated call inputs, not unique cells or actual scans.
struct VolumeCostStats {
  static constexpr int phases=3, operations=4, width=21;
  static constexpr int count=phases*operations*width;
  enum Field { total_seconds, prepare_seconds, evaluate_seconds, order_seconds,
    commit_seconds, cleanup_seconds, calls, input_points, input_elements,
    evaluated_items, candidates, commit_attempts, applied, team_evaluations,
    quality_seconds, conform_seconds, rest_seconds, worstcase_seconds, legal_seconds,
    serial_evaluate_seconds, team_evaluate_seconds };
  std::array<std::atomic<double>,count> values;
  VolumeCostStats() { for(auto &value:values)value.store(0); }
  void Add(int i,double value) {
    double old=values[i].load(std::memory_order_relaxed);
    while(!values[i].compare_exchange_weak(old,old+value,std::memory_order_relaxed)) {}
  }
};

class VolumeCostScope {
  using Clock=std::chrono::steady_clock;
  VolumeCostStats *stats;
  bool evaluation_team=false;
  int base,goal,stage=VolumeCostStats::prepare_seconds;
  std::array<double,VolumeCostStats::width> local{};
  Clock::time_point begin,stamp;
public:
  VolumeCostScope(VolumeCostStats *s,int phase,int operation,int g,int np,int ne)
    :stats(s),base((phase*VolumeCostStats::operations+operation)*VolumeCostStats::width),goal(g) {
    if(stats) {
      begin=stamp=Clock::now();
      local[VolumeCostStats::calls]=1;
      local[VolumeCostStats::input_points]=np;
      local[VolumeCostStats::input_elements]=ne;
    }
  }
  void Stage(int next) {
    if(!stats)return;
    const auto now=Clock::now();
    const double elapsed=std::chrono::duration<double>(now-stamp).count();
    local[stage]+=elapsed;
    if(stage==VolumeCostStats::evaluate_seconds)
      local[VolumeCostStats::serial_evaluate_seconds+evaluation_team]+=elapsed;
    stamp=now;stage=next;
  }
  void EvaluationTeam(bool active) {
    if(stats) {evaluation_team=active;local[VolumeCostStats::team_evaluations]=active;}
  }
  void Count(int field,double value=1) { if(stats)local[field]+=value; }
  ~VolumeCostScope() {
    if(!stats)return;
    const auto end=Clock::now();
    const double elapsed=std::chrono::duration<double>(end-stamp).count();
    local[stage]+=elapsed;
    if(stage==VolumeCostStats::evaluate_seconds)
      local[VolumeCostStats::serial_evaluate_seconds+evaluation_team]+=elapsed;
    local[VolumeCostStats::total_seconds]=std::chrono::duration<double>(end-begin).count();
    local[VolumeCostStats::quality_seconds+goal]=local[VolumeCostStats::total_seconds];
    // One bounded reduction per call; no counter/clock in per-edge geometry.
    for(int i=0;i<VolumeCostStats::width;++i)stats->Add(base+i,local[i]);
  }
  VolumeCostScope(const VolumeCostScope&)=delete;
  VolumeCostScope& operator=(const VolumeCostScope&)=delete;
};
}
#endif
