// Link the public kernel API to the host callbacks without OCC or a mesh run.
#include <filesystem>
#include <stdexcept>
#include <iostream>
#include "../mesh_occ_mpi/distributed_search.h"
#include "../mesh_occ_mpi/node_resources.h"
namespace nglib {
#include "../../netgen/nglib/nglib.h"
}
int main() {
    auto require=[](bool valid) {if(!valid) throw std::runtime_error("公共搜索接口回归失败");};
    nglib::Ng_RuleSearchTransport transport{nullptr,mesh_search::DistributedSearch::OfferCallback,
        mesh_search::DistributedSearch::StartCallback,mesh_search::DistributedSearch::FinishCallback};
    require(nglib::Ng_SetRuleSearchTransport(&transport)==nglib::NG_OK);
    require(nglib::Ng_SetRuleSearchTransportReady(&transport,mesh_search::DistributedSearch::ReadyCallback)==nglib::NG_OK);
    require(nglib::Ng_SetRuleSearchTransportSized(&transport,mesh_search::DistributedSearch::ReadyCallback,
                mesh_search::DistributedSearch::OfferSizedCallback)==nglib::NG_OK);
    require(nglib::Ng_SetRuleSearchTransport(nullptr)==nglib::NG_OK);
    require(nglib::Ng_GenerateVolumeMeshCooperativeOperations(nullptr,nullptr,4,nullptr,
        mesh_node::NodeResources::poll_operations_callback,nullptr,nullptr)==nglib::NG_ERROR);
    double old_stats[9]={};old_stats[8]=12345;nglib::Ng_GetVolumeSearchPolicyStats(old_stats);
    require(old_stats[8]==12345);
    double subtree[7]={};subtree[6]=54321;nglib::Ng_GetVolumeSearchSubtreeStats(subtree);require(subtree[6]==54321);
    std::cout<<"通过：旧传输结构、就绪扩展接口、操作借核回调编译与链接。\n";
}
