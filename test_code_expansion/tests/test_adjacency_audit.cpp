#include "adjacency_audit.h"
#include <cassert>
#include <iostream>
#include <functional>
using mesh_audit::AdjacencyAudit;
static void rejects(const std::function<void()> &f) {
    bool failed=false;try{f();}catch(const std::runtime_error &){failed=true;}assert(failed);
}
int main() {
    const auto offsets=make_id_offsets({3000000000LL,4,0});
    assert(compact_owner_local_id(make_owner_local_id(1,4),offsets)==3000000004LL);
    for(GlobalId id:{GlobalId(0),GlobalId(-1),GlobalId(1),make_owner_local_id(1,5),
                    make_owner_local_id(2,1),make_owner_local_id(3,1),GlobalId(1ULL<<40)})
        rejects([&]{compact_owner_local_id(id,offsets);});
    const auto max=std::numeric_limits<GlobalId>::max();
    assert(compact_owner_local_id(make_owner_local_id(1,1),make_id_offsets({max-1,1}))==max);
    AdjacencyAudit contiguous,temporary;
    std::array<GlobalId,4> ids{};
    for(int i=1;i<=4;++i) {
        ids[i-1]=3000000000LL+i;
        contiguous.point(ids[i-1],{double(i),0.,1.});
    }
    for(int i=4;i>=1;--i)
        temporary.point(compact_owner_local_id(make_owner_local_id(1,i),offsets),{double(i),-0.,1.});
    contiguous.element(9000000000LL,ids,7,true);
    temporary.element(9000000000LL,ids,7,true);
    assert(contiguous.point_sum==temporary.point_sum && contiguous.point_xor==temporary.point_xor);
    assert(contiguous.element_sum==temporary.element_sum && temporary.ghosts==1);
    auto changed=temporary;changed.element(9000000001LL,ids,7,false);
    assert(changed.element_sum!=temporary.element_sum);
    rejects([&]{temporary.element(9000000000LL,ids,7,true);});
    rejects([&]{temporary.point(ids[0],{1.,2.,3.});});
    rejects([&]{AdjacencyAudit a;a.point(1,{INFINITY,0.,0.});});
    rejects([&]{auto a=contiguous;a.element(10,{ids[0],ids[1],ids[2],20},7,true);});
    rejects([&]{auto a=contiguous;a.element(10,{ids[0],ids[1],ids[2],ids[2]},7,true);});
    std::cout<<"PASS: canonical ghost audit, receive-order independence, 64-bit bounds and invalid connectivity\n";
}
