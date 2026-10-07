#ifndef NETGEN_FRONT_QUALITY_BOUND_HPP
#define NETGEN_FRONT_QUALITY_BOUND_HPP
#include <cmath>
namespace netgen {
// Both original strict quality comparisons must fail. Preserve the -1 retry
// result until it is already impossible to change that result by pruning.
inline bool FrontQualityCannotWin(float quality,float best,int tolerance,
    bool impossible,int found,bool debug) {
  return !debug && (!impossible || found!=0) && std::isfinite(quality)
      && quality>=best && quality>=float(tolerance);
}
}
#endif
