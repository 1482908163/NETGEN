#pragma once
#include <atomic>
#include <chrono>

// Test-only instrumentation. The normal executable has no clocks or atomic
// updates from these probes. Profiling runs never supply performance samples.
namespace rotation_probe {
#ifdef ROTATION_CHAIN_PROFILE
inline constexpr bool enabled=true;
#else
inline constexpr bool enabled=false;
#endif
using Clock=std::chrono::steady_clock;
inline double Since(Clock::time_point t) {
  return std::chrono::duration<double>(Clock::now()-t).count();
}
inline std::atomic<double> prepare_sum{0};
inline std::atomic<unsigned long long> prepare_calls{0};
inline void Add(std::atomic<double> &target,double value) {
  double old=target.load(std::memory_order_relaxed);
  while(!target.compare_exchange_weak(old,old+value,std::memory_order_relaxed)) {}
}
struct PrepareTimer {
  Clock::time_point start{};
  bool active=enabled;
  PrepareTimer() {if constexpr(enabled)start=Clock::now();}
  void Finish() {
    if constexpr(enabled)if(active) {
      const double elapsed=Since(start);active=false;
      Add(prepare_sum,elapsed);prepare_calls.fetch_add(1,std::memory_order_relaxed);
    }
  }
  ~PrepareTimer(){Finish();}
};
inline double input_wall=0,copy_wall=0,queue_wall=0,dispatch_wall=0,merge_wall=0;
inline double worker_sum=0,group_max_sum=0,group_idle_proxy=0;
inline unsigned long long groups=0;
struct WallTimer {
  double &total;Clock::time_point start{};
  explicit WallTimer(double &value):total(value){if constexpr(enabled)start=Clock::now();}
  ~WallTimer(){if constexpr(enabled)total+=Since(start);}
};
}
