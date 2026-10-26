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
    cpu_set_t initial_mask;sched_getaffinity(0,sizeof(initial_mask),&initial_mask);
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
    MPI_Barrier(MPI_COMM_WORLD);
    {
        require(!sched_setaffinity(0,sizeof(initial_mask),&initial_mask),"恢复回归初始绑定失败");
        MPI_Barrier(MPI_COMM_WORLD);
        mesh_node::NodeResources trial(MPI_COMM_WORLD,2,14);trial.prepare(100);
        int n=0;if(!rank)n=trial.acquire(6,100,0);else trial.finish();
        MPI_Barrier(MPI_COMM_WORLD);
        if(!rank) {
            for(int i=0;i<12;++i)trial.poll_operations(100,0,100,.1,.00001,nullptr);
            int partial[8]={20,1};
            require(trial.poll_operations(100,0,100,.1,.00001,partial)==1,"已知工作足以摊销但被未知类型全部阻塞");
            trial.release(6,100,n,.1);n=trial.acquire(6,100,0);
            require(n==5,"唯一拖尾未回退到旧弹性池宽度");
            require(!trial.poll_operations(100,1,100,5.,.00001,nullptr),"未知操作被误计为试探损失");
            require(!trial.poll_operations(100,0,1000,5.,.00001,nullptr),"不同工作量范围被误判为加核退化");
            require(!trial.poll_operations(100,0,100,.5,.00001,nullptr),"单次噪声错误触发全局回退");
            require(trial.poll_operations(100,0,100,.5,.00001,partial)==1,"重复同类负收益未触发退回");
            trial.release(6,100,n,.5);n=trial.acquire(6,100,0);
            require(n==2,"退回没有恢复保底核");
            int same[8]={20};
            require(!trial.poll_operations(100,0,100,.1,.00001,same),"失败上下文被重复试探");
            // A different operation must remain eligible after the first type failed.
            for(int i=0;i<12;++i) trial.poll_operations(100,2,100,1.,.00001,nullptr);
            int changed[8]={0,0,20};
            require(trial.poll_operations(100,2,100,1.,.00001,changed)==1,"单类失败封锁了后续有效操作");
            trial.release(6,100,n,.1);n=trial.acquire(6,100,0);
            require(n==5,"新操作的唯一拖尾借核没有兑现");
            trial.poll_operations(100,2,100,.6,.00001,nullptr);
            trial.poll_operations(100,2,100,.6,.00001,nullptr);
            trial.release(6,100,n,.1);trial.finish();Metrics m;trial.report(m);
            require(m.values["coop_operation_explores"]==2 && m.values["coop_operation_rollbacks"]==1,"试探与退回诊断缺失");
            require(m.values["coop_operation_unmatched"]==2 && m.values["coop_operation_resolved"]==1,"匹配操作证据缺失");
            require(m.values["coop_operation_tail_grants"]==2,"唯一拖尾借核诊断缺失");
            require(m.values["coop_checkpoint_restarts"]==m.values["coop_checkpoint_grants"]+1,"退回错误计为增核");
            std::cout<<"通过：未知类型与工作量隔离、重复负收益退回、新操作恢复试探和实测收益确认。\n";
        }
        MPI_Barrier(MPI_COMM_WORLD);
    }
    for(int scenario=0;scenario<3;++scenario) {
        require(!sched_setaffinity(0,sizeof(initial_mask),&initial_mask),"恢复测试绑定失败");
        MPI_Barrier(MPI_COMM_WORLD);
        mesh_node::NodeResources trial(MPI_COMM_WORLD,2,14);trial.prepare(100);
        int n=0;if(!rank)n=trial.acquire(6,100,0);
        if(rank==1)trial.finish();
        MPI_Barrier(MPI_COMM_WORLD);
        if(!rank) {
            for(int i=0;i<12;++i)trial.poll_operations(100,0,100,scenario==0?(i%2?.11:.09):.1,.00001,nullptr);
            int plan[8]={20};
            require(trial.poll_operations(100,0,100,.1,.00001,plan)==1,"竞争期未启动试探");
            trial.release(6,100,n,.1);n=trial.acquire(6,100,0);
            require(n==3,"竞争期未保持一核试探");
            if(scenario==0) {
                require(!trial.poll_operations(100,0,100,.1,.00001,nullptr),"中性样本错误重建");
                require(!trial.poll_operations(100,0,100,.1,.00001,nullptr),"中性样本错误重建");
            } else if(scenario==1) {
                int unknown[8]={0,20};
                require(!trial.poll_operations(100,1,100,.1,.00001,unknown),"未建模工作错误增核");
            }
        }
        MPI_Barrier(MPI_COMM_WORLD);
        if(rank>=2)trial.finish();
        MPI_Barrier(MPI_COMM_WORLD);
        if(!rank) {
            if(scenario==2) {
                int plan[8]={20};
                require(trial.poll_operations(100,1,100,.1,.00001,plan)==1,"未匹配试探阻塞唯一拖尾增核");
                trial.release(6,100,n,.1);n=trial.acquire(6,100,0);
                require(n==5,"唯一拖尾未兑现全部已完成来源空闲核");
            }
            trial.release(6,100,n,.1);trial.finish();Metrics m;trial.report(m);
            require(m.values["coop_operation_resolved"]==0,"中性或耗尽样本被误报为已证实收益");
            require(m.values["coop_operation_inconclusive"]==int(scenario!=1),"无定论试探未正确结束");
            require(m.values["coop_operation_exhausted"]==int(scenario==1),"不可匹配的剩余计划未结束试探");
            require(m.values["coop_operation_tail_grants"]==int(scenario==2),"唯一拖尾诊断错误");
        }
        MPI_Barrier(MPI_COMM_WORLD);
    }
    if(!rank)std::cout<<"通过：中性试探结束、参考耗尽、未匹配试探切换唯一拖尾；均不误报实测收益。\n";
    MPI_Finalize();
}
