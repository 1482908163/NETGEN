// MPI/shared-memory lifecycle test with synthetic work estimates, not a benchmark.
#include "../mesh_occ_mpi/node_resources.h"
#include <map>
#include <iostream>
struct Metrics {
    std::map<std::string,double> values;
    void set_metric(const std::string &k,double v){values[k]=v;}
};
int main(int argc,char **argv) {
    int support;MPI_Init_thread(&argc,&argv,MPI_THREAD_FUNNELED,&support);
    int rank,size;MPI_Comm_rank(MPI_COMM_WORLD,&rank);MPI_Comm_size(MPI_COMM_WORLD,&size);
    auto require=[&](bool valid,const char *why) {
        if(!valid){std::cerr<<why<<std::endl;MPI_Abort(MPI_COMM_WORLD,1);}
    };
    require(size==4,"租约回归需要四个同节点进程和八个可用核");
    mesh_node::NodeResources resources(MPI_COMM_WORLD,2,14);
    resources.prepare(100);
    int held=0;
    if(!rank) {
        held=resources.acquire(6,100,0);require(held==2,"初始申请没有保持保底核");
    }
    if(rank==1) resources.finish();
    MPI_Barrier(MPI_COMM_WORLD);
    if(!rank) {
        int small[8]={1},slow[8]={0,100};
        require(!resources.poll_operations(100,0,100,.001,.1,slow),"未知操作类型被错误外推");
        // Learn separate cheap/expensive operations without making a request.
        for(int i=0;i<8;++i) {
            resources.poll_operations(100,0,100,.001,.1,nullptr);
            resources.poll_operations(100,1,100,1.,.1,nullptr);
        }
        require(!resources.poll_operations(100,0,100,.001,.1,small),"小任务错误支付重建成本");
        // Last operation is cheap, but future work is expensive. The old
        // last-time times remaining-count predictor would reject this request.
        require(resources.poll_operations(100,0,100,.001,.1,slow)==1,"异构剩余操作未产生正确借核");
        resources.release(6,100,held,.1);
        held=resources.acquire(6,100,0);require(held==3,"预留核未兑现或借用了未完成来源");
    }
    MPI_Barrier(MPI_COMM_WORLD);
    if(rank>=2) resources.finish();
    MPI_Barrier(MPI_COMM_WORLD);
    if(!rank) {
        int slow[8]={0,100};
        for(int i=0;i<8;++i) resources.poll_operations(100,1,100,.7,.01,nullptr);
        require(resources.poll_operations(100,1,100,.7,.01,slow)==1,"第二次有收益的增核未发生");
        resources.release(6,100,held,.07);
        held=resources.acquire(6,100,0);require(held==5,"重建时原借核丢失或新增预留未兑现");
    }
    cpu_set_t mask;sched_getaffinity(0,sizeof(mask),&mask);
    std::vector<cpu_set_t> masks(size);
    MPI_Allgather(&mask,sizeof(mask),MPI_BYTE,masks.data(),sizeof(mask),MPI_BYTE,MPI_COMM_WORLD);
    for(int i=0;i<size;++i) for(int j=0;j<i;++j) for(int c=0;c<CPU_SETSIZE;++c)
        require(!(CPU_ISSET(c,&masks[i]) && CPU_ISSET(c,&masks[j])),"不同进程使用了重叠核");
    if(!rank) {
        resources.release(6,100,held,.05);
        held=resources.acquire(7,100,0);require(held==5,"生成内修复被错误限制为保底核");
        require(!resources.poll_operations(100,-1,0,0,0,nullptr),"无容量的早期阶段错误重建");
        resources.release(7,100,held,.05);resources.finish();
        Metrics stats;resources.report(stats);
        require(stats.values["coop_checkpoint_grants"]==2 && stats.values["coop_borrow_epochs"]==3,
                "预留、重建和借核租约计数不一致");
        require(stats.values["coop_work_cost"]==1,"小任务拒绝诊断缺失");
        require(stats.values["coop_work_checks"]+stats.values["coop_operation_other_checks"]==stats.values["coop_checkpoint_checks"],
                "操作模型与早期借核检查计数不闭合");
        require(stats.values["coop_work_grants"]+stats.values["coop_operation_other_restarts"]==stats.values["coop_checkpoint_grants"],
                "操作模型与早期借核重建计数不闭合");
        double decisions=0;
        for(auto name:{"unknown","short","cost","deferred","no_capacity","grants","already"})
            decisions+=stats.values[std::string("coop_work_")+name];
        require(decisions==stats.values["coop_work_checks"],"操作决策拒绝原因不闭合");

        std::cout<<"通过：异构操作与未知类型、小任务拒绝、两次增核、早期阶段借核、无核重叠。\n";
    }
    MPI_Barrier(MPI_COMM_WORLD);MPI_Finalize();
}
