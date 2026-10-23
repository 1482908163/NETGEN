#ifndef NETGEN_DISTRIBUTED_SEARCH_H
#define NETGEN_DISTRIBUTED_SEARCH_H
#include <mpi.h>
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <functional>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace mesh_search {
// A completed rank serves immutable rule queries on its retained anchor CPU.
// MPI calls are confined to the host thread (MPI_THREAD_FUNNELED). No mesh,
// front, partition, or borrowed CPU is moved to the service rank.
class DistributedSearch {
    struct Slot {int owner;double remaining;};
    enum {Busy=-1,Idle=-2,Closed=-3,RequestTag=61,ResponseTag=62};
    MPI_Comm comm=MPI_COMM_NULL;
    MPI_Win window=MPI_WIN_NULL;
    Slot *storage=nullptr;
    int rank=0,size=0,helper=-1,cursor=0;
    std::vector<int> node;
    std::vector<double> demand,observed_at;
    std::vector<char> request,response;
    MPI_Request sending=MPI_REQUEST_NULL;
    double rpc_cost=0,probe_cost=0,start_time=0,expected_remote=0;
    double probes=0,grants=0,critical_rejects=0,cost_rejects=0,service_calls=0;
    double payload_bytes=0,wait_seconds=0,service_seconds=0,control_seconds=0,drain_seconds=0;
    bool drained=false;
    static void Check(int code) {if(code!=MPI_SUCCESS) throw std::runtime_error("跨节点搜索 MPI 调用失败");}
    void SetOwner(int owner) {
        Check(MPI_Accumulate(&owner,1,MPI_INT,rank,offsetof(Slot,owner),1,MPI_INT,MPI_REPLACE,window));
        Check(MPI_Win_flush(rank,window));
    }
    int Owner() {
        int value=Busy;
        Check(MPI_Fetch_and_op(nullptr,&value,MPI_INT,rank,offsetof(Slot,owner),MPI_NO_OP,window));
        Check(MPI_Win_flush(rank,window));return value;
    }
public:
#ifdef NETGEN_SEARCH_PROTOCOL_TEST
    // Test binary only: exercise cross-node arbitration on a single host.
    // Production always uses MPI's actual shared-memory node membership.
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
        node.resize(size);demand.assign(size,0);observed_at.assign(size,0);
        Check(MPI_Allgather(&leader,1,MPI_INT,node.data(),1,MPI_INT,comm));
        Check(MPI_Win_allocate(sizeof(Slot),1,MPI_INFO_NULL,comm,&storage,&window));
        Check(MPI_Win_lock_all(0,window));
        SetOwner(Busy);double zero=0;
        Check(MPI_Accumulate(&zero,1,MPI_DOUBLE,rank,offsetof(Slot,remaining),1,MPI_DOUBLE,MPI_REPLACE,window));
        Check(MPI_Win_flush(rank,window));
        Check(MPI_Barrier(comm));
        // In-job communication calibration; its cost stays inside the core time.
        double begin=MPI_Wtime();
        Check(MPI_Sendrecv(nullptr,0,MPI_BYTE,(rank+1)%size,63,nullptr,0,MPI_BYTE,
                           (rank+size-1)%size,63,comm,MPI_STATUS_IGNORE));
        rpc_cost=std::max(MPI_Wtime()-begin,std::numeric_limits<double>::epsilon());
        cursor=(rank+1)%size;
    }
    int Offer(double saving,double remaining,double remote_seconds) {
        if(drained || helper>=0 || size<2) return 0;
        ++probes;const double begin=MPI_Wtime();
        struct ControlTime {double &total,begin;~ControlTime(){total+=MPI_Wtime()-begin;}} control{control_seconds,begin};
        if(!std::isfinite(saving) || !std::isfinite(remaining) || saving<=rpc_cost+probe_cost || remaining<=0) {
            ++cost_rejects;return 0;
        }
        Check(MPI_Accumulate(&remaining,1,MPI_DOUBLE,rank,offsetof(Slot,remaining),1,MPI_DOUBLE,MPI_REPLACE,window));
        Check(MPI_Win_flush(rank,window));
        // One peer per offer bounds control traffic; rotate rather than always
        // querying a leader. Actual live search estimates drive the tail gate.
        int peer=cursor;cursor=(cursor+1)%size;
        if(peer==rank || node[peer]==node[rank]) return 0;
        Slot candidate{};
        // Read the same basic types as the concurrent atomic updates. Reading
        // the padded struct as MPI_BYTE would not preserve atomicity.
        Check(MPI_Fetch_and_op(nullptr,&candidate.owner,MPI_INT,peer,offsetof(Slot,owner),MPI_NO_OP,window));
        Check(MPI_Fetch_and_op(nullptr,&candidate.remaining,MPI_DOUBLE,peer,offsetof(Slot,remaining),MPI_NO_OP,window));
        Check(MPI_Win_flush(peer,window));
        double observed=MPI_Wtime()-begin;
        probe_cost=std::max(observed,.75*probe_cost);
        demand[peer]=candidate.owner==Busy?candidate.remaining:0;observed_at[peer]=MPI_Wtime();
        if(candidate.owner!=Idle) {
            // A competing live tail already longer than ours removes the
            // predicted makespan gain. This is a sampled, not global, estimate.
            if(candidate.owner==Busy && candidate.remaining>remaining) ++critical_rejects;
            return 0;
        }
        double other=0,stamp=MPI_Wtime();
        for(int i=0;i<size;++i) if(i!=rank)
            other=std::max(other,std::max(0.0,demand[i]-(stamp-observed_at[i])));
        double net=saving-rpc_cost-probe_cost;
        double critical=std::max(remaining,other)-std::max(std::max(0.0,remaining-net),other);
        if(critical<=0) {++critical_rejects;return 0;}
        if(saving<=rpc_cost+probe_cost) {++cost_rejects;return 0;}
        int idle=Idle,previous=Busy;
        Check(MPI_Compare_and_swap(&rank,&idle,&previous,MPI_INT,peer,offsetof(Slot,owner),window));
        Check(MPI_Win_flush(peer,window));
        if(previous!=Idle) return 0;
        helper=peer;expected_remote=remote_seconds;++grants;return 1;
    }
    void Start(const char * data,int bytes) {
        if(helper<0 || bytes<1 || bytes>64*1024*1024) throw std::runtime_error("跨节点搜索任务生命周期错误");
        request.assign(data,data+bytes);payload_bytes+=bytes;start_time=MPI_Wtime();
        Check(MPI_Isend(request.data(),bytes,MPI_BYTE,helper,RequestTag,comm,&sending));
    }
    const char * Finish(int * bytes) {
        if(helper<0) throw std::runtime_error("跨节点搜索缺少活动任务");
        double begin=MPI_Wtime();MPI_Status status;
        Check(MPI_Probe(helper,ResponseTag,comm,&status));int count=0;Check(MPI_Get_count(&status,MPI_BYTE,&count));
        if(count<1 || count>64*1024*1024) throw std::runtime_error("跨节点搜索响应长度非法");
        response.resize(count);Check(MPI_Recv(response.data(),count,MPI_BYTE,helper,ResponseTag,comm,MPI_STATUS_IGNORE));
        Check(MPI_Wait(&sending,MPI_STATUS_IGNORE));wait_seconds+=MPI_Wtime()-begin;
        const double overhead=std::max(0.0,MPI_Wtime()-start_time-expected_remote);
        rpc_cost=std::max(overhead,.75*rpc_cost);payload_bytes+=count;helper=-1;*bytes=count;
        return response.data();
    }
    void ServeUntilComplete(const std::function<std::string(const char *,int)> & execute) {
        if(drained || helper>=0) throw std::runtime_error("跨节点搜索终止状态错误");
        double begin=MPI_Wtime();SetOwner(Idle);
        MPI_Request done;Check(MPI_Ibarrier(comm,&done));
        for(;;) {
            int pending=0;MPI_Status status;Check(MPI_Iprobe(MPI_ANY_SOURCE,RequestTag,comm,&pending,&status));
            if(pending) {
                if(Owner()!=status.MPI_SOURCE) throw std::runtime_error("跨节点搜索服务未被正确预留");
                int count=0;Check(MPI_Get_count(&status,MPI_BYTE,&count));
                if(count<1 || count>64*1024*1024) throw std::runtime_error("跨节点搜索请求长度非法");
                std::vector<char> input(count);Check(MPI_Recv(input.data(),count,MPI_BYTE,status.MPI_SOURCE,
                                                            RequestTag,comm,MPI_STATUS_IGNORE));
                double start=MPI_Wtime();auto output=execute(input.data(),count);
                service_seconds+=MPI_Wtime()-start;++service_calls;
                Check(MPI_Send(output.data(),output.size(),MPI_BYTE,status.MPI_SOURCE,ResponseTag,comm));
                SetOwner(Idle);
            }
            int complete=0;Check(MPI_Test(&done,&complete,MPI_STATUS_IGNORE));
            if(complete) break;
        }
        SetOwner(Closed);drained=true;drain_seconds=MPI_Wtime()-begin;
        Check(MPI_Win_unlock_all(window));Check(MPI_Win_free(&window));
        Check(MPI_Comm_free(&comm));
    }
    static int OfferCallback(void *ctx,double gain,double remaining,double work) {
        return static_cast<DistributedSearch*>(ctx)->Offer(gain,remaining,work);
    }
    static void StartCallback(void *ctx,const char *data,int bytes) {static_cast<DistributedSearch*>(ctx)->Start(data,bytes);}
    static const char * FinishCallback(void *ctx,int *bytes) {return static_cast<DistributedSearch*>(ctx)->Finish(bytes);}
    template<class Profiler> void Report(Profiler &p) const {
        const char *names[]={"probes","grants","critical_rejects","cost_rejects","service_calls",
                            "payload_bytes","wait_seconds","service_seconds","control_seconds","drain_seconds"};
        const double values[]={probes,grants,critical_rejects,cost_rejects,service_calls,payload_bytes,
                               wait_seconds,service_seconds,control_seconds,drain_seconds};
        for(int i=0;i<10;++i) p.set_metric(std::string("search_remote_")+names[i],values[i]);
    }
};
}
#endif
