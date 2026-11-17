#ifndef NETGEN_MAPPING_POLICY_HPP
#define NETGEN_MAPPING_POLICY_HPP
#include <algorithm>
#include <cmath>
#include <limits>

namespace netgen {
struct MappingPlan {int threads=1,batch=1,window=2;double gain=0;};
// Observed ranges, not statistical confidence intervals. Unknown or unstable
// buckets keep their present resource allocation; no online trial is hidden.
struct MappingCostHistory {
  int samples=0,threads=0;
  double wall_low=std::numeric_limits<double>::infinity();
  double state_high=0,work_high=0,job_high=0,queue_high=0;
  void Observe(int p,double wall,double state,double work,double job,double queue) {
    if(!std::isfinite(wall+state+work+job+queue) || wall<=0 || work<0) return;
    if(threads!=p) {*this=MappingCostHistory{};threads=p;}
    ++samples;wall_low=std::min(wall_low,wall);
    state_high=std::max(state_high,state);work_high=std::max(work_high,work);
    job_high=std::max(job_high,job);queue_high=std::max(queue_high,queue);
  }
  MappingPlan Choose(int current,int capacity,double restart) const {
    MappingPlan best{current,1,2,0};
    if(samples<3 || threads!=current || capacity<current || job_high<=0) return best;
    double best_upper=wall_low;
    for(int p=current;p<=capacity;++p) for(int batch:{1,2,4}) {
      // Serial state supply and indivisible batch tails limit useful CPUs.
      // The 25% allowance is explicit model slack, not a proven error bound.
      double upper=1.25*std::max({state_high,work_high/p,job_high*batch})+
        queue_high/batch+(p==current?0:2*restart);
      if(upper<best_upper) {
        best_upper=upper;best={p,batch,std::min(8,2*batch),wall_low-upper};
      }
    }
    return best;
  }
};
inline int MappingBucket(int tolerance,int points,int faces) {
  int p=0,f=0;
  while(points>1) {points>>=1;++p;}
  while(faces>1) {faces>>=1;++f;}
  return tolerance*1024+std::min(31,p)*32+std::min(31,f);
}
}
#endif
