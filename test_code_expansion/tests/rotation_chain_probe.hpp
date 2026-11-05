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
inline std::atomic<double> claim_sum{0};
inline std::atomic<unsigned long long> claim_attempts{0},claim_successes{0};
inline thread_local double local_claim_seconds=0;
inline thread_local unsigned long long local_claim_attempts=0,local_claim_successes=0;
inline void Add(std::atomic<double> &target,double value) {
  double old=target.load(std::memory_order_relaxed);
  while(!target.compare_exchange_weak(old,old+value,std::memory_order_relaxed)) {}
}
// Per-claim clocks stay thread-local; publish only once per ApplyRulesImpl call.
struct ClaimTimer {
  Clock::time_point start{};
  ClaimTimer(){if constexpr(enabled)start=Clock::now();}
  ~ClaimTimer(){if constexpr(enabled){local_claim_seconds+=Since(start);++local_claim_attempts;}}
};
struct ClaimScope {
  double seconds=0;unsigned long long attempts=0,successes=0;
  ClaimScope(){if constexpr(enabled){seconds=local_claim_seconds;attempts=local_claim_attempts;successes=local_claim_successes;}}
  ~ClaimScope(){if constexpr(enabled){
    Add(claim_sum,local_claim_seconds-seconds);
    claim_attempts.fetch_add(local_claim_attempts-attempts,std::memory_order_relaxed);
    claim_successes.fetch_add(local_claim_successes-successes,std::memory_order_relaxed);
  }}
};
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
inline double group_dispatch_wall=0,launch_delay_sum=0,completion_to_join_sum=0,join_tail_wall=0;
inline unsigned long long groups=0;
inline double shared_prepare_wall=0,worker_copy_sum=0,chain_claim_sum=0;
inline unsigned long long shared_preparations=0,chain_rules=0,chain_rule_rotations=0,input_copies=0,prefix_checks=0;
struct WallTimer {
  double &total;Clock::time_point start{};
  explicit WallTimer(double &value):total(value){if constexpr(enabled)start=Clock::now();}
  ~WallTimer(){if constexpr(enabled)total+=Since(start);}
};
}
