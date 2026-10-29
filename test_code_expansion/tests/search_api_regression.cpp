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
    require(nglib::Ng_SetVolumeGenerationRecovery(-1)==nglib::NG_ERROR);
    require(nglib::Ng_SetVolumeGenerationRecovery(2)==nglib::NG_ERROR);
    require(nglib::Ng_SetVolumeGenerationRecovery(1)==nglib::NG_OK);
    require(nglib::Ng_SetVolumeGenerationRecovery(0)==nglib::NG_OK);
    require(nglib::Ng_SetVolumeFrontGroupMode(-1)==nglib::NG_ERROR);
    require(nglib::Ng_SetVolumeFrontGroupMode(3)==nglib::NG_ERROR);
    for(int mode:{2,1,0}) require(nglib::Ng_SetVolumeFrontGroupMode(mode)==nglib::NG_OK);
    double group_stats[11];std::fill_n(group_stats,11,-7.0);
    nglib::Ng_GetVolumeFrontGroupStats(group_stats+1);
    require(group_stats[0]==-7.0 && group_stats[10]==-7.0);
    require(nglib::Ng_SetVolumeRecoveryTopologyMode(-1)==nglib::NG_ERROR);
    require(nglib::Ng_SetVolumeRecoveryTopologyMode(3)==nglib::NG_ERROR);
    for(int mode:{2,1,0}) require(nglib::Ng_SetVolumeRecoveryTopologyMode(mode)==nglib::NG_OK);
    double topology_stats[7];std::fill_n(topology_stats,7,-9.0);
    nglib::Ng_GetVolumeRecoveryTopologyStats(topology_stats+1);
    require(topology_stats[0]==-9.0 && topology_stats[6]==-9.0);
    for(int i=1;i<=5;++i) require(topology_stats[i]==0);
    require(nglib::Ng_SetVolumeSharedRulePreparation(-1)==nglib::NG_ERROR);
    require(nglib::Ng_SetVolumeSharedRulePreparation(2)==nglib::NG_ERROR);
    for(int enabled:{1,0}) require(nglib::Ng_SetVolumeSharedRulePreparation(enabled)==nglib::NG_OK);
    double shared[8];std::fill_n(shared,8,-11.0);nglib::Ng_GetVolumeSharedRuleStats(shared+1);
    require(shared[0]==-11.0 && shared[7]==-11.0);
    for(int i=1;i<=6;++i) require(shared[i]==0);
    require(nglib::Ng_SetVolumeQuietRuleParallelism(-1)==nglib::NG_ERROR);
    require(nglib::Ng_SetVolumeQuietRuleParallelism(2)==nglib::NG_ERROR);
    for(int enabled:{1,0}) require(nglib::Ng_SetVolumeQuietRuleParallelism(enabled)==nglib::NG_OK);
    double quiet[10];std::fill_n(quiet,10,-13.0);nglib::Ng_GetVolumeQuietRuleStats(quiet+1);
    require(quiet[0]==-13.0 && quiet[9]==-13.0);
    for(int i=1;i<=8;++i) require(quiet[i]==0);
    nglib::Ng_GetVolumeQuietRuleStats(nullptr);
    double generation[20]={};generation[19]=6789;
    nglib::Ng_GetVolumeGenerationStats(generation);require(generation[19]==6789);
    for(int i=0;i<19;++i) require(generation[i]==0);
    std::cout<<"通过：旧传输结构、就绪扩展接口、操作借核回调编译与链接。\n";
}
