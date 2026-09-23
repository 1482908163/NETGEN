#pragma once
#include <algorithm>
#include <cmath>
#include <limits>
#include <vector>
namespace mesh_node {
// A conservative prediction, not a measured speedup: discount ideal scaling
// by half and charge two observed restarts plus a fixed 5 ms margin.
inline double window_net_gain(int threads,int extra,int remaining,double last,double restart) {
    if(threads<1 || extra<1 || remaining<2 || !std::isfinite(last) || last<=0 ||
       !std::isfinite(restart) || restart<0) return 0.;
    const double ideal=last*remaining*double(extra)/(double(threads)+extra);
    return std::max(0.,.5*ideal-2*std::max(.001,restart)-.005);
}

// A later lease requires a new donor completion, and is bounded to two grants.
inline bool window_grant_allowed(int grants,int generation,int last_generation,bool repeat) {
    return grants==0 || (repeat && grants==1 && generation>last_generation);
}

struct WindowDemand {
    int threads=0,remaining=0;
    double last=0,restart=0,age=0;
    double seconds() const {
        if(threads<1 || remaining<2 || !std::isfinite(last) || last<=0 ||
           !std::isfinite(restart) || restart<0 || !std::isfinite(age) || age<0 ||
           age>std::max(.05,4*last)) return 0.;
        const double value=last*remaining-age;
        return std::isfinite(value)?std::max(0.,value):0.;
    }
    double gain(int extra) const {
        if(extra<1 || seconds()<=0) return 0.;
        return std::max(0.,.5*seconds()*extra/(double(threads)+extra)
                          -2*std::max(.001,restart)-.005);
    }
};

// Minimize the predicted last completion among fresh final-optimization peers.
// Exact small node-local DP: unprofitable leases are forbidden, unused CPUs are
// allowed, and ties prefer fewer CPUs. This is a heuristic time model, not a
// guarantee about actual or global makespan. Only the caller's share is reserved.
inline std::vector<int> window_tail_allocation(const std::vector<WindowDemand>& peers,int budget) {
    std::vector<int> allocation(peers.size(),0);
    if(budget<=0 || peers.empty()) return allocation;
    const double inf=std::numeric_limits<double>::infinity();
    std::vector<double> previous(budget+1,inf),next(budget+1,inf);
    std::vector<std::vector<int>> choice(peers.size(),std::vector<int>(budget+1,-1));
    previous[0]=0;
    for(size_t i=0;i<peers.size();++i) {
        std::fill(next.begin(),next.end(),inf);
        for(int used=0;used<=budget;++used) if(std::isfinite(previous[used])) {
            for(int extra=0;extra<=budget-used;++extra) {
                const double gain=peers[i].gain(extra);
                if(extra && gain<=0) continue;
                const double tail=std::max(previous[used],peers[i].seconds()-gain);
                if(tail<next[used+extra]) {
                    next[used+extra]=tail;choice[i][used+extra]=extra;
                }
            }
        }
        previous.swap(next);
    }
    int used=int(std::min_element(previous.begin(),previous.end())-previous.begin());
    for(size_t i=peers.size();i>0;--i) {
        const int extra=choice[i-1][used];
        allocation[i-1]=extra;used-=extra;
    }
    return allocation;
}
}
