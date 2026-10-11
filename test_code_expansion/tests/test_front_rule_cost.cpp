#include "front_rule_cost.hpp"
#include <cassert>
#include <chrono>
#include <stdexcept>
#include <thread>
#include <iostream>
struct Clock {
  using duration=std::chrono::nanoseconds;
  using time_point=std::chrono::time_point<Clock,duration>;
  static int ticks;
  static time_point now(){return time_point(duration(++ticks));}
};
int Clock::ticks=0;
int main(){
  using S=netgen::VolumeFrontRuleStats;using Scope=netgen::FrontRuleCostScope<Clock>;
  {
    Clock::ticks=0;Scope scope(nullptr,1);scope.Switch(S::mapping_seconds);
    scope.Count(S::rules);Scope::Geometry geometry(&scope,true);
  }
  assert(Clock::ticks==0);
  S coarse,detailed;
  for(int mode=0;mode<2;++mode){
    Clock::ticks=0;
    {
      Scope scope(mode?&detailed:&coarse,mode);scope.Count(S::rules,2);
      scope.Switch(S::mapping_seconds);scope.Switch(S::mapping_seconds);
      scope.Switch(S::setup_seconds);scope.Count(S::mappings);
      scope.Switch(S::face_scan_seconds);
      {Scope::Geometry shared(&scope,true);assert(scope.Stage()==S::shared_seconds);}
      assert(scope.Stage()==S::face_scan_seconds);
      try{Scope::Geometry unshared(&scope,false);throw std::runtime_error("geometry");}
      catch(const std::runtime_error&){assert(scope.Stage()==S::face_scan_seconds);}
      scope.Switch(S::finish_seconds);
    }
    assert(Clock::ticks==(mode?10:2));
  }
  assert(coarse.values[S::calls]==1 && detailed.values[S::calls]==1);
  assert(coarse.values[S::stage_transitions]==8 && detailed.values[S::stage_transitions]==8);
  assert(detailed.values[S::shared_triangles]==1 && detailed.values[S::unshared_triangles]==1);
  double sum=0;
  for(int i=S::prepare_seconds;i<=S::finish_seconds;++i){sum+=detailed.values[i];assert(coarse.values[i]==0);}
  assert(std::abs(sum-detailed.values[S::total_seconds].load())<1e-20);
  assert(coarse.values[S::total_seconds]==1e-9);
  S threaded;
  auto work=[&]{for(int k=0;k<1000;++k){netgen::FrontRuleCost scope(&threaded,1);scope.Switch(S::mapping_seconds);scope.Count(S::mappings,3);}};
  std::thread a(work),b(work);a.join();b.join();
  assert(threaded.values[S::calls]==2000 && threaded.values[S::mappings]==6000);
  assert(threaded.values[S::stage_transitions]==2000);
  sum=0;for(int i=S::prepare_seconds;i<=S::finish_seconds;++i)sum+=threaded.values[i];
  assert(std::abs(sum-threaded.values[S::total_seconds].load())<1e-9);
  std::cout<<"PASS: exclusive clock partition, zero disabled clocks, two control clocks, exception restoration and concurrent aggregation\n";
}
