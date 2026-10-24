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
// One circulating credit per completed rank, one task per client. Only the host
// thread calls MPI. Busy clients NEVER flush/read remote memory or wait for a
// readiness reply. All control sends have stable storage and bounded requests.
class DistributedSearch {
    enum {RequestTag=61,ResponseTag=62,ReadyTag=64,DecisionTag=65};
    static constexpr int limit=64*1024*1024;
    MPI_Comm comm=MPI_COMM_NULL;
    int rank=0,size=0,helper=-1,cursor=0,advertised=-1;
    std::vector<int> node;
    std::vector<bool> closed;
    std::vector<double> demand,observed_at;
    // A helper cannot send another credit to us until it consumes this reply.
    // MPI_Test still must retire the local request before reusing its buffer.
    struct Reply {double values[2]={};MPI_Request send=MPI_REQUEST_NULL;};
    std::vector<Reply> replies;
    double ready_value=0;
    MPI_Request ready_send=MPI_REQUEST_NULL,sending=MPI_REQUEST_NULL;
    std::vector<char> request,response;
    double rpc_cost=0,rpc_floor=0,probe_cost=0,start_time=0;
    double probes=0,grants=0,critical_rejects=0,cost_rejects=0,service_calls=0;
    double payload_bytes=0,wait_seconds=0,service_seconds=0,control_seconds=0,drain_seconds=0;
    double ready_sent=0,ready_received=0,no_ready=0,control_max=0,completion_residual=0;
    bool drained=false;
    static void Check(int code) {if(code!=MPI_SUCCESS) throw std::runtime_error("跨节点搜索 MPI 调用失败");}
    static bool Complete(MPI_Request &r) {
        if(r==MPI_REQUEST_NULL) return true;
        int done=0;Check(MPI_Test(&r,&done,MPI_STATUS_IGNORE));return done!=0;
    }
    void ReplyTo(int peer,int accept,double remaining) {
        auto &r=replies[peer];
        if(!Complete(r.send)) throw std::runtime_error("跨节点搜索重复信用回复");
        r.values[0]=accept;r.values[1]=remaining;
        Check(MPI_Isend(r.values,2,MPI_DOUBLE,peer,DecisionTag,comm,&r.send));
    }
    // Exactly one arrived credit is consumed per call: O(1) polling, no global
    // idle-rank scan. -1 permanently closes this client during the drain.
    int ReceiveReady(double saving,double remaining,double remote_seconds,bool closing) {
        int pending=0;MPI_Status status;
        Check(MPI_Iprobe(MPI_ANY_SOURCE,ReadyTag,comm,&pending,&status));
        if(!pending) {++no_ready;return 0;}
        int peer=status.MPI_SOURCE;
        // Do not overwrite an unretired nonblocking reply, even if the previous
        // peer receive completed before our send request was locally retired.
        if(!Complete(replies[peer].send)) return 0;
        double other=0;Check(MPI_Recv(&other,1,MPI_DOUBLE,peer,ReadyTag,comm,MPI_STATUS_IGNORE));
        ++ready_received;
        const double net=saving-rpc_cost-probe_cost;
        bool valid=!closing && helper<0 && node[peer]!=node[rank] &&
            std::isfinite(saving+remaining+remote_seconds) && remaining>0 && remote_seconds>=0;
        if(valid && net<=0) {++cost_rejects;valid=false;}
        // The credit carries competing demand sampled by this helper, NOT a
        // global critical-path proof. Unknown peers are not claimed balanced.
        if(valid && std::max(remaining,other)<=std::max(std::max(0.0,remaining-net),other)) {
            ++critical_rejects;valid=false;
        }
        ReplyTo(peer,closing?-1:(valid?1:0),std::isfinite(remaining)?std::max(0.0,remaining):0);
        if(!valid) return 0;
        helper=peer;++grants;return 1;
    }
    void Advertise() {
        if(advertised>=0 || !Complete(ready_send)) return;
        for(int n=0;n<size;++n) {
            int peer=cursor;cursor=(cursor+1)%size;
            if(peer==rank || node[peer]==node[rank] || closed[peer]) continue;
            ready_value=0;const double now=MPI_Wtime();
            for(int i=0;i<size;++i) if(i!=peer && !closed[i])
                ready_value=std::max(ready_value,std::max(0.0,demand[i]-(now-observed_at[i])));
            advertised=peer;
            Check(MPI_Isend(&ready_value,1,MPI_DOUBLE,peer,ReadyTag,comm,&ready_send));++ready_sent;
            break;
        }
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
        node.resize(size);closed.assign(size,false);demand.assign(size,0);observed_at.assign(size,0);replies.resize(size);
        Check(MPI_Allgather(&leader,1,MPI_INT,node.data(),1,MPI_INT,comm));
        // Warm ring calibration: the minimum excludes startup-arrival skew.
        // All ranks are in this collective setup, not in a mesh search call.
        rpc_cost=std::numeric_limits<double>::infinity();
        for(int i=0;i<4;++i) {
            const double t=MPI_Wtime();
            Check(MPI_Sendrecv(nullptr,0,MPI_BYTE,(rank+1)%size,63,nullptr,0,MPI_BYTE,
                              (rank+size-1)%size,63,comm,MPI_STATUS_IGNORE));
            if(i) rpc_cost=std::min(rpc_cost,MPI_Wtime()-t);
        }
        rpc_cost=std::max(2*rpc_cost,std::numeric_limits<double>::epsilon());rpc_floor=rpc_cost;cursor=(rank+1)%size;
    }
    int Ready() {
        const double t=MPI_Wtime();int pending=0;
        Check(MPI_Iprobe(MPI_ANY_SOURCE,ReadyTag,comm,&pending,MPI_STATUS_IGNORE));
        const double elapsed=MPI_Wtime()-t;control_seconds+=elapsed;control_max=std::max(control_max,elapsed);
        if(!pending) ++no_ready;
        return pending;
    }
    static int ReadyCallback(void *ctx) {return static_cast<DistributedSearch*>(ctx)->Ready();}
    int Offer(double saving,double remaining,double remote_seconds) {
        if(drained || helper>=0 || size<2) return 0;
        ++probes;const double begin=MPI_Wtime();
        int accepted=ReceiveReady(saving,remaining,remote_seconds,false);
        const double elapsed=MPI_Wtime()-begin;
        control_seconds+=elapsed;control_max=std::max(control_max,elapsed);
        // Polling cost is distinct from RPC cost; a local overlap never trains it.
        probe_cost=.875*probe_cost+.125*elapsed;
        return accepted;
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
        // Exposed residual excludes measured local overlap AND helper compute.
        // Train exposed completion cost, NOT elapsed time including local work.
        const double residual=std::max(0.0,MPI_Wtime()-start_time-std::max(begin-start_time,actual));
        completion_residual+=residual;
        rpc_cost=std::max(rpc_floor,.75*rpc_cost+.25*residual);
        payload_bytes+=count;helper=-1;*bytes=count-sizeof(double);return response.data()+sizeof(double);
    }
    void ServeUntilComplete(const std::function<std::string(const char *,int)> &execute) {
        if(drained || helper>=0) throw std::runtime_error("跨节点搜索终止状态错误");
        const double begin=MPI_Wtime();MPI_Request done,quiescent=MPI_REQUEST_NULL;
        Check(MPI_Ibarrier(comm,&done));bool stopping=false,joined=false;
        for(;;) {
            ReceiveReady(0,0,0,true);
            if(advertised>=0) {
                int pending=0;MPI_Status status;
                Check(MPI_Iprobe(advertised,DecisionTag,comm,&pending,&status));
                if(pending && Complete(ready_send)) {
                    double decision[2];Check(MPI_Recv(decision,2,MPI_DOUBLE,advertised,DecisionTag,comm,MPI_STATUS_IGNORE));
                    int peer=advertised;advertised=-1;
                    closed[peer]=decision[0]<0;demand[peer]=decision[1];observed_at[peer]=MPI_Wtime();
                    if(decision[0]==1) {
                        Check(MPI_Probe(peer,RequestTag,comm,&status));int count=0;
                        Check(MPI_Get_count(&status,MPI_BYTE,&count));
                        if(count<1 || count>limit) throw std::runtime_error("跨节点搜索请求长度非法");
                        std::vector<char> input(count);Check(MPI_Recv(input.data(),count,MPI_BYTE,peer,RequestTag,comm,MPI_STATUS_IGNORE));
                        const double t=MPI_Wtime();auto output=execute(input.data(),count);
                        double seconds=MPI_Wtime()-t;service_seconds+=seconds;++service_calls;
                        if(output.empty() || output.size()>limit) throw std::runtime_error("跨节点搜索结果长度非法");
                        output.insert(0,reinterpret_cast<const char*>(&seconds),sizeof(double));
                        Check(MPI_Send(output.data(),output.size(),MPI_BYTE,peer,ResponseTag,comm));
                    }
                }
            }
            if(!stopping) stopping=Complete(done);
            if(!stopping) Advertise();
            // Keep consuming late credits while joining the second barrier.
            // Completion means all helpers stopped advertising and every
            // in-flight credit/reply has been consumed before Comm_free.
            bool replies_done=true;for(auto &r:replies) replies_done=Complete(r.send)&&replies_done;
            if(stopping && advertised<0 && Complete(ready_send) && replies_done && !joined) {
                Check(MPI_Ibarrier(comm,&quiescent));joined=true;
            }
            if(joined && Complete(quiescent)) break;
        }
        // Any post-join replies are matched by peers before their barrier entry.
        for(auto &r:replies) if(r.send!=MPI_REQUEST_NULL) Check(MPI_Wait(&r.send,MPI_STATUS_IGNORE));
        drained=true;drain_seconds=MPI_Wtime()-begin;Check(MPI_Comm_free(&comm));
    }
    static int OfferCallback(void *ctx,double gain,double remaining,double work) {
        return static_cast<DistributedSearch*>(ctx)->Offer(gain,remaining,work);
    }
    static void StartCallback(void *ctx,const char *data,int bytes) {static_cast<DistributedSearch*>(ctx)->Start(data,bytes);}
    static const char *FinishCallback(void *ctx,int *bytes) {return static_cast<DistributedSearch*>(ctx)->Finish(bytes);}
    template<class Profiler> void Report(Profiler &p) const {
        const char *names[]={"probes","grants","critical_rejects","cost_rejects","service_calls",
            "payload_bytes","wait_seconds","service_seconds","control_seconds","drain_seconds",
            "ready_sent","ready_received","no_ready","control_max_seconds","rpc_floor_seconds","completion_residual_seconds","completion_cost_seconds"};
        const double values[]={probes,grants,critical_rejects,cost_rejects,service_calls,payload_bytes,
            wait_seconds,service_seconds,control_seconds,drain_seconds,ready_sent,ready_received,no_ready,control_max,rpc_floor,completion_residual,rpc_cost};
        for(int i=0;i<17;++i) p.set_metric(std::string("search_remote_")+names[i],values[i]);
    }
};
}
#endif
