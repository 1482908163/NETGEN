#include "../../netgen/libsrc/meshing/volume_cost_profile.hpp"
#include <cassert>
#include <cmath>
#include <stdexcept>
#include <thread>
#include <vector>

using namespace netgen;
int main() {
  // Disabled collection must tolerate unused indices and do no writes.
  {VolumeCostScope disabled(nullptr,0,0,0,4,1);disabled.Stage(2);disabled.Count(6);}
  VolumeCostStats stats;
  const int repetitions=1000,workers=4;
  std::vector<std::thread> threads;
  for(int worker=0;worker<workers;++worker)threads.emplace_back([&] {
    for(int i=0;i<repetitions;++i) {
      VolumeCostScope scope(&stats,0,2,1,4,1);
      scope.EvaluationTeam(true);scope.Stage(VolumeCostStats::evaluate_seconds);
      scope.Count(VolumeCostStats::evaluated_items,6);
      scope.Stage(VolumeCostStats::order_seconds);
      scope.Count(VolumeCostStats::candidates,2);
      scope.Stage(VolumeCostStats::commit_seconds);
      scope.Count(VolumeCostStats::commit_attempts,2);scope.Count(VolumeCostStats::applied);
      scope.Stage(VolumeCostStats::cleanup_seconds);
    }
  });
  for(auto &thread:threads)thread.join();
  const int base=2*VolumeCostStats::width;
  auto value=[&](int field){return stats.values[base+field].load();};
  assert(value(VolumeCostStats::calls)==workers*repetitions);
  assert(value(VolumeCostStats::input_elements)==workers*repetitions);
  assert(value(VolumeCostStats::evaluated_items)==6*workers*repetitions);
  assert(value(VolumeCostStats::applied)==workers*repetitions);
  double stages=0;for(int i=1;i<=5;++i)stages+=value(i);
  assert(std::abs(stages-value(0))<1e-8);
  assert(value(VolumeCostStats::conform_seconds)==value(0));
  assert(value(VolumeCostStats::team_evaluate_seconds)==value(VolumeCostStats::evaluate_seconds));
  assert(value(VolumeCostStats::serial_evaluate_seconds)==0);
  // An exception records the unfinished operation without inventing a commit.
  try {
    VolumeCostScope scope(&stats,1,1,4,4,1);
    scope.Stage(VolumeCostStats::evaluate_seconds);
    throw std::runtime_error("test interruption");
  } catch(const std::runtime_error&) {}
  const int aborted=(1*4+1)*VolumeCostStats::width;
  assert(stats.values[aborted+VolumeCostStats::calls]==1);
  assert(stats.values[aborted+VolumeCostStats::commit_attempts]==0);
}
