#pragma once
#include <algorithm>
#include <cmath>
namespace mesh_node {
// A conservative prediction, not a measured speedup: discount ideal scaling
// by half and charge two observed restarts plus a fixed 5 ms margin.
inline double window_net_gain(int threads,int extra,int remaining,double last,double restart) {
    if(threads<1 || extra<1 || remaining<2 || !std::isfinite(last) || last<=0 ||
       !std::isfinite(restart) || restart<0) return 0.;
    const double ideal=last*remaining*double(extra)/(double(threads)+extra);
    return std::max(0.,.5*ideal-2*std::max(.001,restart)-.005);
}
}
