#ifndef NETGEN_DISTRIBUTED_SEARCH_H
#define NETGEN_DISTRIBUTED_SEARCH_H
#include <mpi.h>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <functional>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace mesh_search {
// Demand initiated by a profitable client. One outstanding solicitation/client,
// one offered credit/helper. Completed helpers wait for events, never scan ranks.
// Only the original host thread enters MPI; no progress thread or extra CPU.
class DistributedSearch {
    enum {RequestTag=61,ResponseTag=62,ReadyTag=64,DecisionTag=65,DemandTag=66};
    static constexpr int limit=64*1024*1024;
    MPI_Comm comm=MPI_COMM_NULL;
    int rank=0,size=0,helper=-1,cursor=0,solicited=-1,offered=-1,competing_peer=-1;
    std::vector<int> node;
    struct Reply {double values[2]={};MPI_Request send=MPI_REQUEST_NULL;};
    std::vector<Reply> replies;
    double incoming[3]={},outgoing[3]={},answer[2]={},decision=0,in_decision=0;
    MPI_Request demand_recv=MPI_REQUEST_NULL,answer_recv=MPI_REQUEST_NULL;
    MPI_Request demand_send=MPI_REQUEST_NULL,decision_send=MPI_REQUEST_NULL,decision_recv=MPI_REQUEST_NULL;
    MPI_Request sending=MPI_REQUEST_NULL;
    std::vector<char> request,response;
    bool answer_ready=false,drained=false,closing=false;
    double rpc_cost=0,rpc_floor=0,rpc_error=0,probe_cost=0,start_time=0,next_request=0;
    double probes=0,grants=0,critical_rejects=0,cost_rejects=0,service_calls=0;
    double payload_bytes=0,wait_seconds=0,service_seconds=0,control_seconds=0,drain_seconds=0;
    double ready_sent=0,ready_received=0,no_ready=0,control_max=0,completion_residual=0;
    double demand_sent=0,demand_received=0,event_waits=0,event_wait_seconds=0;
    double demand_start=0,discovery_cost=0,competing=0,competing_stamp=0;
    unsigned completed_tasks=0;
    double latency=0,seconds_per_byte=0;
    double size_cost[28]={},size_error[28]={};unsigned size_samples[28]={};
    int active_size_bin=0;
    static int SizeBin(int bytes) {
        return std::min(27,std::max(0,int(std::log2(std::max(1,bytes)))));
    }
    double TransferFloor(int bytes) const {return latency+seconds_per_byte*std::max(0,bytes);}
    static void Check(int code) {if(code!=MPI_SUCCESS) throw std::runtime_error("跨节点搜索 MPI 调用失败");}
    static bool Complete(MPI_Request &r) {
        if(r==MPI_REQUEST_NULL) return true;
        int done=0;Check(MPI_Test(&r,&done,MPI_STATUS_IGNORE));return done!=0;
    }
    void PostDemand() {Check(MPI_Irecv(incoming,3,MPI_DOUBLE,MPI_ANY_SOURCE,DemandTag,comm,&demand_recv));}
    void OnDemand(int peer,bool serving) {
        auto &reply=replies[peer];
        // This peer received our previous answer before sending a new demand.
        Check(MPI_Wait(&reply.send,MPI_STATUS_IGNORE));
        const bool available=serving && offered<0 && node[peer]!=node[rank];
        reply.values[0]=available?1:0;
        reply.values[1]=peer==competing_peer?0:std::max(0.0,competing-(MPI_Wtime()-competing_stamp));
        if(std::isfinite(incoming[1]) && incoming[1]>reply.values[1]) {
            competing=incoming[1];competing_stamp=MPI_Wtime();competing_peer=peer;
        }
        if(available) {
            offered=peer;++ready_sent;
            Check(MPI_Irecv(&in_decision,1,MPI_DOUBLE,peer,DecisionTag,comm,&decision_recv));
        }
        Check(MPI_Isend(reply.values,2,MPI_DOUBLE,peer,ReadyTag,comm,&reply.send));
        ++demand_received;PostDemand();
    }
    void OnAnswer() {
        answer_ready=true;
        discovery_cost=MPI_Wtime()-demand_start;
        if(answer[0]>0) ++ready_received;
    }
    void Progress() {
        int flag=0;MPI_Status status;
        Check(MPI_Test(&demand_recv,&flag,&status));if(flag) OnDemand(status.MPI_SOURCE,false);
        if(answer_recv!=MPI_REQUEST_NULL) {
            Check(MPI_Test(&answer_recv,&flag,MPI_STATUS_IGNORE));if(flag) OnAnswer();
        }
    }
    void Decide(bool accept) {
        Check(MPI_Wait(&decision_send,MPI_STATUS_IGNORE));
        if(answer[0]>0) {
            decision=accept?1:0;
            Check(MPI_Isend(&decision,1,MPI_DOUBLE,solicited,DecisionTag,comm,&decision_send));
        }
        if(accept) {helper=solicited;++grants;}
        Check(MPI_Wait(&demand_send,MPI_STATUS_IGNORE));
        solicited=-1;answer_ready=false;
    }
    void Execute(const std::function<std::string(const char *,int)> &execute) {
        if(in_decision==1) {
            MPI_Status status;Check(MPI_Probe(offered,RequestTag,comm,&status));int count=0;
            Check(MPI_Get_count(&status,MPI_BYTE,&count));
            if(count<1 || count>limit) throw std::runtime_error("跨节点搜索请求长度非法");
            std::vector<char> input(count);Check(MPI_Recv(input.data(),count,MPI_BYTE,offered,RequestTag,comm,MPI_STATUS_IGNORE));
            const double t=MPI_Wtime();auto output=execute(input.data(),count);
            double seconds=MPI_Wtime()-t;service_seconds+=seconds;++service_calls;
            if(output.empty() || output.size()>limit) throw std::runtime_error("跨节点搜索结果长度非法");
            output.insert(0,reinterpret_cast<const char*>(&seconds),sizeof(double));
            Check(MPI_Send(output.data(),output.size(),MPI_BYTE,offered,ResponseTag,comm));
        }
        offered=-1;
    }
public:
#ifdef NETGEN_SEARCH_PROTOCOL_TEST
    void UseRankAsTestNode() {for(int i=0;i<size;++i) node[i]=i;}
#endif
    explicit DistributedSearch(MPI_Comm world,int physical_node=-1) {
        Check(MPI_Comm_dup(world,&comm));Check(MPI_Comm_rank(comm,&rank));Check(MPI_Comm_size(comm,&size));
        int leader=physical_node;
        if(leader<0) {
            MPI_Comm local;Check(MPI_Comm_split_type(comm,MPI_COMM_TYPE_SHARED,rank,MPI_INFO_NULL,&local));
            leader=rank;Check(MPI_Allreduce(MPI_IN_PLACE,&leader,1,MPI_INT,MPI_MIN,local));
            Check(MPI_Comm_free(&local));
        }
        node.resize(size);replies.resize(size);
        Check(MPI_Allgather(&leader,1,MPI_INT,node.data(),1,MPI_INT,comm));
        // Payload calibration is a floor, NOT the full task cost. Full exposed
        // completion residuals replace it after the first task, with error bands.
        double measured[2]={};
        for(int scale=0;scale<2;++scale) {
            std::vector<char> calibration(scale?65536:8,0),received(calibration.size());
            measured[scale]=std::numeric_limits<double>::infinity();
            for(int i=0;i<4;++i) {
                const double t=MPI_Wtime();
                Check(MPI_Sendrecv(calibration.data(),calibration.size(),MPI_BYTE,(rank+1)%size,63,
                                  received.data(),received.size(),MPI_BYTE,(rank+size-1)%size,63,comm,MPI_STATUS_IGNORE));
                if(i) measured[scale]=std::min(measured[scale],MPI_Wtime()-t);
            }
        }
        // Include the slow ring links instead of letting an intra-node link
        // supply every rank's inter-node floor. Still only a calibration floor.
        Check(MPI_Allreduce(MPI_IN_PLACE,measured,2,MPI_DOUBLE,MPI_MAX,comm));
        latency=std::max(2*measured[0],std::numeric_limits<double>::epsilon());
        seconds_per_byte=std::max(0.0,(measured[1]-measured[0])/65528.0);
        rpc_cost=rpc_floor=TransferFloor(131072);
        cursor=(rank+1)%size;PostDemand();
    }
    int Ready() {
        const double t=MPI_Wtime();Progress();
        const double elapsed=MPI_Wtime()-t;control_seconds+=elapsed;control_max=std::max(control_max,elapsed);
        if(!answer_ready) ++no_ready;
        return answer_ready && answer[0]>0;
    }
    static int ReadyCallback(void *ctx) {return static_cast<DistributedSearch*>(ctx)->Ready();}
    int Offer(double saving,double remaining,double remote_seconds,int transfer_bytes=131072) {
        if(drained || closing || helper>=0 || size<2) return 0;
        ++probes;const double begin=MPI_Wtime();Progress();
        const double elapsed=MPI_Wtime()-begin;
        control_seconds+=elapsed;control_max=std::max(control_max,elapsed);
        probe_cost=.875*probe_cost+.125*elapsed;
        if(transfer_bytes<1 || transfer_bytes>2*limit) throw std::runtime_error("跨节点传输估计长度非法");
        const int bin=SizeBin(transfer_bytes);
        const double predicted=std::max(TransferFloor(transfer_bytes),size_cost[bin]);
        const double net=saving-predicted-size_error[bin]-probe_cost;
        bool valid=std::isfinite(saving+remaining+remote_seconds) && remaining>0 && remote_seconds>=0 && net>0;
        if(!valid) ++cost_rejects;
        if(answer_ready) {
            valid=valid && answer[0]>0;
            if(valid && std::max(remaining,answer[1])<=std::max(std::max(0.0,remaining-net),answer[1])) {
                ++critical_rejects;valid=false;
            }
            if(valid) active_size_bin=bin;
            Decide(valid);
            // Balance measured solicitation cost with discovery delay. This is
            // a pacing heuristic, not a theorem or a fixed microsecond cutoff.
            next_request=MPI_Wtime()+std::sqrt(2*std::max(discovery_cost,rpc_floor)*std::max(remaining,rpc_floor));
            return valid;
        }
        if(valid && solicited<0 && MPI_Wtime()>=next_request && Complete(decision_send)) {
            for(int n=0;n<size;++n) {
                int peer=cursor;cursor=(cursor+1)%size;
                if(peer==rank || node[peer]==node[rank]) continue;
                solicited=peer;outgoing[0]=saving;outgoing[1]=remaining;outgoing[2]=remote_seconds;
                Check(MPI_Irecv(answer,2,MPI_DOUBLE,peer,ReadyTag,comm,&answer_recv));
                Check(MPI_Isend(outgoing,3,MPI_DOUBLE,peer,DemandTag,comm,&demand_send));
                demand_start=MPI_Wtime();++demand_sent;break;
            }
        }
        return 0;
    }
    void Start(const char *data,int bytes) {
        if(helper<0 || bytes<1 || bytes>limit) throw std::runtime_error("跨节点搜索任务生命周期错误");
        request.assign(data,data+bytes);payload_bytes+=bytes;start_time=MPI_Wtime();
        Check(MPI_Isend(request.data(),bytes,MPI_BYTE,helper,RequestTag,comm,&sending));
    }
    const char * Finish(int *bytes) {
        if(helper<0) throw std::runtime_error("跨节点搜索缺少活动任务");
        const double begin=MPI_Wtime();MPI_Status status;
        Check(MPI_Probe(helper,ResponseTag,comm,&status));int count=0;Check(MPI_Get_count(&status,MPI_BYTE,&count));
        if(count<=int(sizeof(double)) || count>limit+int(sizeof(double))) throw std::runtime_error("跨节点搜索响应长度非法");
        response.resize(count);Check(MPI_Recv(response.data(),count,MPI_BYTE,helper,ResponseTag,comm,MPI_STATUS_IGNORE));
        Check(MPI_Wait(&sending,MPI_STATUS_IGNORE));wait_seconds+=MPI_Wtime()-begin;
        double actual=0;std::memcpy(&actual,response.data(),sizeof(double));
        if(!std::isfinite(actual) || actual<0) throw std::runtime_error("远程搜索服务计时非法");
        const double residual=std::max(0.0,MPI_Wtime()-start_time-std::max(begin-start_time,actual));
        completion_residual+=residual;
        const int bin=active_size_bin;
        size_error[bin]=size_samples[bin]?.75*size_error[bin]+.25*std::abs(residual-size_cost[bin]):0;
        size_cost[bin]=size_samples[bin]?.75*size_cost[bin]+.25*residual:residual;++size_samples[bin];
        rpc_cost=size_cost[bin];rpc_error=size_error[bin];++completed_tasks;
        payload_bytes+=count;helper=-1;*bytes=count-sizeof(double);return response.data()+sizeof(double);
    }
    void ServeUntilComplete(const std::function<std::string(const char *,int)> &execute) {
        if(drained || helper>=0) throw std::runtime_error("跨节点搜索终止状态错误");
        closing=true;const double begin=MPI_Wtime();MPI_Request done=MPI_REQUEST_NULL;bool joined=false,finished=false;
        for(;;) {
            if(answer_ready) Decide(false);
            if(!joined && solicited<0 && Complete(decision_send)) {
                Check(MPI_Ibarrier(comm,&done));joined=true;
            }
            if(finished && offered<0) break;
            MPI_Request requests[]={demand_recv,answer_recv,decision_recv,done,decision_send};
            int which;MPI_Status status;const double wait=MPI_Wtime();
            Check(MPI_Waitany(5,requests,&which,&status));++event_waits;event_wait_seconds+=MPI_Wtime()-wait;
            demand_recv=requests[0];answer_recv=requests[1];decision_recv=requests[2];done=requests[3];decision_send=requests[4];
            if(which==0) OnDemand(status.MPI_SOURCE,true);
            else if(which==1) OnAnswer();
            else if(which==2) Execute(execute);
            else if(which==3) finished=true;
            else if(which==4) {} // Local completion permits barrier entry.
            else throw std::runtime_error("跨节点事件循环没有活动请求");
        }
        // All client solicitations have replies before the first barrier. Any
        // offered credit has now received a decision; no new task can appear.
        Check(MPI_Cancel(&demand_recv));Check(MPI_Wait(&demand_recv,MPI_STATUS_IGNORE));
        for(auto &r:replies) Check(MPI_Wait(&r.send,MPI_STATUS_IGNORE));
        Check(MPI_Barrier(comm));drained=true;drain_seconds=MPI_Wtime()-begin;Check(MPI_Comm_free(&comm));
    }
    static int OfferSizedCallback(void *ctx,double gain,double remaining,double work,int bytes) {
        return static_cast<DistributedSearch*>(ctx)->Offer(gain,remaining,work,bytes);
    }
    static int OfferCallback(void *ctx,double gain,double remaining,double work) {return static_cast<DistributedSearch*>(ctx)->Offer(gain,remaining,work);}
    static void StartCallback(void *ctx,const char *data,int bytes) {static_cast<DistributedSearch*>(ctx)->Start(data,bytes);}
    static const char *FinishCallback(void *ctx,int *bytes) {return static_cast<DistributedSearch*>(ctx)->Finish(bytes);}
    template<class Profiler> void Report(Profiler &p) const {
        p.set_metric("search_remote_latency_seconds",latency);
        p.set_metric("search_remote_seconds_per_byte",seconds_per_byte);
        const char *names[]={"probes","grants","critical_rejects","cost_rejects","service_calls","payload_bytes",
            "wait_seconds","service_seconds","control_seconds","drain_seconds","ready_sent","ready_received",
            "no_ready","control_max_seconds","rpc_floor_seconds","completion_residual_seconds","completion_cost_seconds",
            "demand_sent","demand_received","event_waits","event_wait_seconds"};
        const double values[]={probes,grants,critical_rejects,cost_rejects,service_calls,payload_bytes,wait_seconds,
            service_seconds,control_seconds,drain_seconds,ready_sent,ready_received,no_ready,control_max,rpc_floor,
            completion_residual,rpc_cost,demand_sent,demand_received,event_waits,event_wait_seconds};
        for(int i=0;i<21;++i) p.set_metric(std::string("search_remote_")+names[i],values[i]);
    }
};
}
#endif
