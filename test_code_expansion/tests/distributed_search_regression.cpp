// Real MPI protocol test. Synthetic topology/gains are test-only, not benchmarks.
#define NETGEN_SEARCH_PROTOCOL_TEST
#include "../mesh_occ_mpi/distributed_search.h"
#include <map>
#include <iostream>
#include <thread>
#include <chrono>
struct Metrics {
    std::map<std::string,double> values;
    void set_metric(const std::string &k,double v){values[k]=v;}
};
int main(int argc,char **argv) {
    int provided;MPI_Init_thread(&argc,&argv,MPI_THREAD_FUNNELED,&provided);
    int rank,size;MPI_Comm_rank(MPI_COMM_WORLD,&rank);MPI_Comm_size(MPI_COMM_WORLD,&size);
    try {
        if(size<2 || provided<MPI_THREAD_FUNNELED) throw std::runtime_error("至少需要两个 FUNNELED 进程");
        for(int test=0;test<5;++test) {
            mesh_search::DistributedSearch service(MPI_COMM_WORLD);
            if(test) service.UseRankAsTestNode();
            const int clients=test==2?size-1:1;
            int sent=0;
            if((test==1 || test==2) && rank<clients) {
                for(int i=0;i<12;++i) {
                    double deadline=MPI_Wtime()+20;
                    // Deliberately large synthetic gain, to test contention and
                    // transport without relying on noisy microsecond timings.
                    while(!service.Offer(100,1000,0)) {
                        if(MPI_Wtime()>deadline) throw std::runtime_error("远程资源领取超时");
                    }
                    std::string payload(i==0?8*1024*1024:73,char('a'+rank));
                    payload.back()=char(i);
                    service.Start(payload.data(),payload.size());
                    int bytes=0;const char *result=service.Finish(&bytes);
                    if(bytes!=int(payload.size()) || std::string(result,bytes)!=payload)
                        throw std::runtime_error("远程返回内容不一致");
                    ++sent;
                }
            }
            if(test==3 && rank==0) {
                // Declines must recycle credits, including at termination.
                const double until=MPI_Wtime()+.05;
                while(MPI_Wtime()<until) if(service.Offer(0,100,0)) throw std::runtime_error("零收益被接受");
            }
            if(test==4) {
                MPI_Barrier(MPI_COMM_WORLD);
                if(rank) std::this_thread::sleep_for(std::chrono::milliseconds(400));
                else {
                    const double begin=MPI_Wtime();
                    for(int i=0;i<1000;++i) if(service.Offer(100,1000,0)) throw std::runtime_error("忙进程被领取");
                    if(MPI_Wtime()-begin>.25) throw std::runtime_error("控制路径等待了忙进程进展");
                }
            }
            if(!test) {
                if(service.Offer(-1,1,0) || service.Offer(1,0,0))
                    throw std::runtime_error("非正收益没有被拒绝");
                // All ranks are still busy; there is no eligible helper.
                if(service.Offer(1,1,0)) throw std::runtime_error("未完成进程被错误借用");
                MPI_Barrier(MPI_COMM_WORLD);
            }
            service.ServeUntilComplete([](const char *data,int bytes){return std::string(data,bytes);});
            Metrics metrics;service.Report(metrics);
            double stats[3]={double(sent),metrics.values["search_remote_grants"],metrics.values["search_remote_service_calls"]};
            MPI_Allreduce(MPI_IN_PLACE,stats,3,MPI_DOUBLE,MPI_SUM,MPI_COMM_WORLD);
            if(stats[0]!=stats[1] || stats[1]!=stats[2] || ((test==1 || test==2) && stats[0]!=clients*12))
                throw std::runtime_error("任务预留、返回和服务计数不闭合");
            double credits[2]={metrics.values["search_remote_ready_sent"],metrics.values["search_remote_ready_received"]};
            MPI_Allreduce(MPI_IN_PLACE,credits,2,MPI_DOUBLE,MPI_SUM,MPI_COMM_WORLD);
            double demands[2]={metrics.values["search_remote_demand_sent"],metrics.values["search_remote_demand_received"]};
            MPI_Allreduce(MPI_IN_PLACE,demands,2,MPI_DOUBLE,MPI_SUM,MPI_COMM_WORLD);
            if(demands[0]!=demands[1]) throw std::runtime_error("需求请求未闭合");
            if(metrics.values["search_remote_event_waits"]>2+metrics.values["search_remote_demand_received"]+
                metrics.values["search_remote_ready_sent"]+metrics.values["search_remote_demand_sent"])
                throw std::runtime_error("服务循环出现无消息重复唤醒");
            if(credits[0]!=credits[1]) throw std::runtime_error("终止时存在未回收信用");
            if(!rank) std::cout<<"协议场景 "<<test<<" 通过：请求 "<<stats[0]<<"，进程 "<<size<<"\n";
        }
    } catch(const std::exception &error) {
        std::cerr<<"rank "<<rank<<": "<<error.what()<<std::endl;
        MPI_Abort(MPI_COMM_WORLD,1);
    }
    MPI_Finalize();
}
