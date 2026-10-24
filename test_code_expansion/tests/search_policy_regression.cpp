#include "../../netgen/libsrc/meshing/search_policy.hpp"
#include "../mesh_occ_mpi/online_borrow.h"
#include <cassert>
#include <iostream>
#include <limits>
int main() {
    using mesh_node::ChooseBorrowTarget;
    assert(ChooseBorrowTarget(4,16,1,.001,.003,0,0,0).threads==4);
    assert(ChooseBorrowTarget(4,16,100,.1,.001,0,0,0).threads==16);
    assert(ChooseBorrowTarget(4,16,100,.1,.001,1,0,0).threads==4);
    assert(ChooseBorrowTarget(4,16,100,.1,.001,0,0,20).threads==4);
    assert(ChooseBorrowTarget(4,16,100,.1,.001,0,10,0).threads==4);
    assert(ChooseBorrowTarget(4,16,100,std::numeric_limits<double>::quiet_NaN(),0,0,0,0).threads==4);
    using mesh_node::ChooseBorrowComposition;
    assert(ChooseBorrowComposition(4,16,0,.004,0,.003,0).threads==4);
    assert(ChooseBorrowComposition(4,16,100,0,0,0,0).threads==4);
    assert(ChooseBorrowComposition(4,16,0,40,1,.01,0).threads==16);
    assert(ChooseBorrowComposition(4,16,0,40,1,.01,20).threads==4);
    netgen::SearchProfile budget;int samples=0;
    for(int i=0;i<1024;++i) samples+=budget.Sample();
    assert(samples==11);
    std::vector<double> cost{9,8,7,6,5,4,3,2,1};
    auto local=netgen::AssignSearch(cost,2,false);
    auto remote=netgen::AssignSearch(cost,2,true);
    assert(std::max(remote.local_seconds,remote.remote_seconds)<local.local_seconds);
    std::vector<int> covered(cost.size(),0);
    for(int i:remote.local) ++covered.at(i);
    for(int i:remote.remote) ++covered.at(i);
    for(int n:covered) assert(n==1);
    assert(netgen::AssignSearch(cost,2,true,100).remote.empty());
    assert(netgen::CriticalSearchGain(5,10,3)==0);
    assert(netgen::CriticalSearchGain(10,8,5)==2);
    std::cout<<"通过：小任务、串行占比、关键路径和预测不确定性拒绝；任务覆盖无遗漏。\n";
}
