#ifndef NETGEN_FRONT_RULE_COST_HPP
#define NETGEN_FRONT_RULE_COST_HPP
#include <array>
#include <atomic>
#include <chrono>

namespace netgen {
struct VolumeFrontRuleStats {
  static constexpr int count=32;
  enum Field { calls, rules, face_trials, point_trials, mappings, edge_rejected,
    nonconvex, point_rejected, face_rejected, evaluated, usable, best_updates,
    shared_triangles, unshared_triangles, quad_tests, point_tests, verified,
    mismatches, total_seconds, reference_seconds, prepare_seconds,
    mapping_seconds, setup_seconds, transform_seconds, point_seconds,
    face_scan_seconds, shared_seconds, unshared_seconds, evaluate_seconds,
    selection_seconds, finish_seconds, stage_transitions };
  std::array<std::atomic<double>,count> values;
  VolumeFrontRuleStats(){for(auto &v:values)v.store(0);}
  void Add(int field,double value){
    double old=values[field].load(std::memory_order_relaxed);
    while(!values[field].compare_exchange_weak(old,old+value,std::memory_order_relaxed)){}
  }
};

// One exclusive wall-clock partition of ApplyRulesImpl. The total-only control
// executes the same counters/transitions but reads the clock only twice.
template<class Clock=std::chrono::steady_clock>
class FrontRuleCostScope {
  VolumeFrontRuleStats *stats;
  bool detailed;
  int stage=VolumeFrontRuleStats::prepare_seconds;
  typename Clock::time_point begin{},stamp{};
  std::array<double,VolumeFrontRuleStats::count> local{};
public:
  FrontRuleCostScope(VolumeFrontRuleStats *s,int mode):stats(s),detailed(mode!=0){
    if(stats){begin=stamp=Clock::now();local[VolumeFrontRuleStats::calls]=1;}
  }
  FrontRuleCostScope(const FrontRuleCostScope&)=delete;
  ~FrontRuleCostScope(){
    if(!stats)return;
    auto end=Clock::now();
    if(detailed)local[stage]+=std::chrono::duration<double>(end-stamp).count();
    local[VolumeFrontRuleStats::total_seconds]=std::chrono::duration<double>(end-begin).count();
    for(int i=0;i<VolumeFrontRuleStats::count;++i)stats->Add(i,local[i]);
  }
  void Count(int field,double value=1){if(stats)local[field]+=value;}
  int Stage()const{return stage;}
  void Switch(int next){
    if(!stats || next==stage)return;
    if(detailed){auto now=Clock::now();local[stage]+=std::chrono::duration<double>(now-stamp).count();stamp=now;}
    local[VolumeFrontRuleStats::stage_transitions]++;stage=next;
  }
  class Geometry {
    FrontRuleCostScope *scope;
    int previous;
  public:
    Geometry(FrontRuleCostScope *s,bool shared):scope(s),previous(s?s->Stage():0){
      if(scope){scope->Count(shared?VolumeFrontRuleStats::shared_triangles:VolumeFrontRuleStats::unshared_triangles);
        scope->Switch(shared?VolumeFrontRuleStats::shared_seconds:VolumeFrontRuleStats::unshared_seconds);}
    }
    ~Geometry(){if(scope)scope->Switch(previous);}
  };
};
using FrontRuleCost=FrontRuleCostScope<>;
}
#endif
