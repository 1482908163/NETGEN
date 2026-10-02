#pragma once
#include <algorithm>
#include <cstddef>
#include <utility>
#include <vector>

namespace netgen {
// A point evaluation traverses its incident elements. This is a work proxy,
// not a prediction of the variable number of BFGS iterations.
inline std::vector<std::pair<size_t,size_t>> MakeWeightedPointRanges(
    const std::vector<size_t> & weights, int workers)
{
  std::vector<std::pair<size_t,size_t>> result;
  if(weights.empty())return result;
  const size_t tasks=std::min(weights.size(),size_t(4)*size_t(std::max(1,workers)));
  long double remaining=0;
  for(auto w:weights)remaining+=std::max(size_t(1),w);
  size_t first=0;
  for(size_t task=0;task<tasks;++task) {
    const size_t left=tasks-task;
    const long double target=remaining/left;
    long double cost=0;
    size_t last=first;
    do {
      cost+=std::max(size_t(1),weights[last++]);
    } while(last<weights.size()-(left-1) && (left==1 || cost<target));
    result.emplace_back(first,last);
    first=last;
    remaining-=cost;
  }
  return result;
}
}
