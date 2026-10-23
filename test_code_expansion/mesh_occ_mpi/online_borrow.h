#ifndef NETGEN_ONLINE_BORROW_H
#define NETGEN_ONLINE_BORROW_H
#include <algorithm>
#include <cmath>
namespace mesh_node {
struct BorrowDecision {int threads;double gain;};
inline BorrowDecision ChooseBorrowTarget(int current,int available,int remaining,double last,
                                        double restart,double serial_fraction,double uncertainty,
                                        double other_remaining) {
    BorrowDecision best{current,0};
    if(current<1 || available<current || remaining<1 || last<=0 || restart<0 || uncertainty<0 ||
       !std::isfinite(last+restart+serial_fraction+uncertainty+other_remaining)) return best;
    serial_fraction=std::max(0.0,std::min(1.0,serial_fraction));
    const double before=last*remaining;
    for(int k=current+1;k<=available;++k) {
        double after=before*(serial_fraction+(1-serial_fraction)*current/k)+restart+uncertainty;
        double gain=std::max(before,other_remaining)-std::max(after,other_remaining);
        if(gain>best.gain) best={k,gain};
    }
    return best;
}
}
#endif
