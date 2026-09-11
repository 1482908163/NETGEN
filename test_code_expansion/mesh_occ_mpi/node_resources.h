#ifndef NETGEN_NODE_RESOURCES_H
#define NETGEN_NODE_RESOURCES_H
// Linux node-local, non-preemptive CPU leases. No mesh pointers cross processes.
#include <mpi.h>
#include <pthread.h>
#include <sched.h>
#include <time.h>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace mesh_node {
class NodeResources {
    static constexpr int max_ranks=128, phases=8;
    struct Model {
        double n=0,sx=0,sy=0,sxx=0,sxy=0,error=0;
        int samples=0;
        void coefficients(double &a,double &b) const {
            double determinant=n*sxx-sx*sx;
            if(determinant>1e-15) {
                b=std::max(0.0,(n*sxy-sx*sy)/determinant);
                a=std::max(0.0,(sy-b*sx)/n);
            } else { a=0; b=sx>0?sy/sx:0; }
        }
        void observe(int k,double work,double seconds) {
            double x=1.0/k,y=seconds/std::max(1.0,work),a,b;
            coefficients(a,b);
            error=.8*error+.2*std::abs(y-a-b*x);
            n=.8*n+1;sx=.8*sx+x;sy=.8*sy+y;
            sxx=.8*sxx+x*x;sxy=.8*sxy+x*y;++samples;
        }
    };
    struct RankState {
        int active=0,done=0,prepared=0,phase=0,threads=1;
        double work=1,start=0;
        Model model[phases];
    };
    struct Shared {
        pthread_mutex_t mutex;
        int size=0,cpus=0;
        int cpu[CPU_SETSIZE],home[CPU_SETSIZE],owner[CPU_SETSIZE];
        int anchor[max_ranks];
        RankState rank[max_ranks];
    };
    MPI_Comm node=MPI_COMM_NULL;
    MPI_Win window=MPI_WIN_NULL;
    Shared *shared=nullptr;
    cpu_set_t initial,home_mask;
    int me=0,size=0,base=1,mode=0,held=1,borrowed=0;
    bool live=false,shared_pool=false,hostname_grouping=false;
    double management=0,setup=0,epochs=0,borrow_epochs=0,borrow_seconds=0;
    double lease_seconds=0,phase_seconds=0,peak=1,decisions=0,rejected=0,cold=0;
    static double now() { timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+1e-9*t.tv_nsec; }
    [[noreturn]] void fail(const char *message) const {
        std::cerr<<"node_coop_v1: "<<message<<std::endl;
        MPI_Abort(MPI_COMM_WORLD,87);std::terminate();
    }
    void lock() { if(pthread_mutex_lock(&shared->mutex)!=0) fail("节点资源锁失败"); }
    void unlock() { if(pthread_mutex_unlock(&shared->mutex)!=0) fail("节点资源解锁失败"); }
    void pin(const cpu_set_t &mask) {
        if(sched_setaffinity(0,sizeof(mask),&mask)!=0) fail("集群不允许作业内跨进程核亲和性调整");
        cpu_set_t actual;CPU_ZERO(&actual);
        if(sched_getaffinity(0,sizeof(actual),&actual)!=0 || !CPU_EQUAL(&actual,&mask))
            fail("实际核集合与资源租约不一致，停止实验");
    }
    int fair_target() const {
        int pending=0,available=shared->cpus;
        for(int i=0;i<size;++i) {
            const auto &r=shared->rank[i];
            if(!r.prepared) available-=base;
            else if(r.active) available-=r.threads;
            else if(r.done) --available;
            else ++pending;
        }
        return std::max(1,available/std::max(1,pending));
    }
    int model_target(int fair,int cap) {
        // Asynchronous snapshot: active leases are immutable; idle ranks advertise
        // their last phase until their next entry. Only this caller's target commits.
        const double stamp=now();int budget=shared->cpus;double locked_tail=0,locked_lower=0;
        std::vector<int> pending;
        for(int i=0;i<size;++i) {
            const auto &r=shared->rank[i];
            if(!r.prepared && !r.done) { ++cold;return fair; }
            if(r.active) {
                budget-=r.threads;
                double a,b;r.model[r.phase].coefficients(a,b);
                double remaining=r.work*(a+b/r.threads)-(stamp-r.start);
                locked_tail=std::max(locked_tail,std::max(0.0,remaining));
                if(r.model[r.phase].samples>=2)
                    locked_lower=std::max(locked_lower,std::max(0.0,remaining-2*r.work*r.model[r.phase].error));
            } else if(r.done) --budget;
            else pending.push_back(i);
        }
        if(pending.empty() || budget<int(pending.size())) return fair;
        // Do not invent speedup curves for phases with no runtime observations.
        for(int i:pending) if(shared->rank[i].model[shared->rank[i].phase].samples<2) {
            ++cold;return fair;
        }
        const double inf=std::numeric_limits<double>::infinity();
        std::vector<double> dp(budget+1,inf),next(budget+1,inf);
        std::vector<std::vector<int>> choice(pending.size(),std::vector<int>(budget+1));
        dp[0]=locked_tail;double fixed=locked_tail,uncertainty=0;
        for(size_t j=0;j<pending.size();++j) {
            const auto &r=shared->rank[pending[j]];const auto &m=r.model[r.phase];
            double a,b;m.coefficients(a,b);
            fixed=std::max(fixed,r.work*(a+b/(r.phase==5?1:fair)));
            uncertainty=std::max(uncertainty,r.work*m.error);
            std::fill(next.begin(),next.end(),inf);
            for(int used=0;used<=budget;++used) if(std::isfinite(dp[used])) {
                int limit=std::min(cap,budget-used);
                // SwapImprove2 is serial in this kernel.
                if(r.phase==5) limit=1;
                for(int k=1;k<=limit;++k) {
                    double value=std::max(dp[used],r.work*(a+b/k));
                    if(value<next[used+k]) { next[used+k]=value;choice[j][used+k]=k; }
                }
            }
            dp.swap(next);
        }
        int used=int(std::min_element(dp.begin(),dp.end())-dp.begin());
        if(!std::isfinite(dp[used])) { ++rejected;return fair; }
        const double best=dp[used];
        int target=fair;double pending_upper=0;
        for(int j=int(pending.size())-1;j>=0;--j) {
            int k=choice[j][used];if(k<1) return fair;
            const auto &r=shared->rank[pending[j]];const auto &m=r.model[r.phase];
            double a,b;m.coefficients(a,b);
            pending_upper=std::max(pending_upper,r.work*(a+b/k+2*m.error));
            if(pending[j]==me) target=k;used-=k;
        }
        // Two admissible updates: shorten the predicted tail, or use fewer
        // CPUs while this pending work stays below an immutable active tail.
        // Error bands are empirical guards, not statistical guarantees.
        double overhead=epochs>0?management/epochs:0;
        bool shorter=fixed-best>2*uncertainty+2*overhead;
        bool slack=target<fair && pending_upper+2*overhead<locked_lower;
        if(target==fair || (!shorter && !slack)) { ++rejected;return fair; }
        ++decisions;return target;
    }
public:
    // mode 0=fixed, 1=greedy idle lending, 2=phase cost guided allocation.
    NodeResources(MPI_Comm world,int threads,int policy):base(threads),mode(policy) {
        const double start=now();CPU_ZERO(&initial);CPU_ZERO(&home_mask);
        if(sched_getaffinity(0,sizeof(initial),&initial)!=0) fail("无法读取初始核绑定");

        int world_rank=0,world_size=0;
        MPI_Comm_rank(world,&world_rank);MPI_Comm_size(world,&world_size);
        int split_rc=MPI_Comm_split_type(world,MPI_COMM_TYPE_SHARED,0,MPI_INFO_NULL,&node);
        if(split_rc!=MPI_SUCCESS || node==MPI_COMM_NULL)
            fail("MPI_COMM_TYPE_SHARED 节点分组失败");
        MPI_Comm_rank(node,&me);MPI_Comm_size(node,&size);

        // MPI-X/PMIx on the target cluster can return singleton communicators for
        // MPI_COMM_TYPE_SHARED even when all ranks physically share a node. Fall back
        // to an explicit processor-name grouping so the validation is independent of
        // the MPI implementation's shared-node discovery path.
        if(size==1 && world_size>1) {
            MPI_Comm_free(&node);node=MPI_COMM_NULL;
            char myname[MPI_MAX_PROCESSOR_NAME];std::memset(myname,0,sizeof(myname));
            int name_len=0;
            if(MPI_Get_processor_name(myname,&name_len)!=MPI_SUCCESS)
                fail("无法读取MPI处理器名称用于节点分组");
            std::vector<char> names(size_t(world_size)*MPI_MAX_PROCESSOR_NAME,0);
            if(MPI_Allgather(myname,MPI_MAX_PROCESSOR_NAME,MPI_CHAR,
                             names.data(),MPI_MAX_PROCESSOR_NAME,MPI_CHAR,world)!=MPI_SUCCESS)
                fail("无法收集MPI处理器名称用于节点分组");
            int color=world_rank;
            for(int r=0;r<world_size;++r) {
                const char *other=names.data()+size_t(r)*MPI_MAX_PROCESSOR_NAME;
                if(std::strncmp(other,myname,MPI_MAX_PROCESSOR_NAME)==0) { color=r;break; }
            }
            if(MPI_Comm_split(world,color,world_rank,&node)!=MPI_SUCCESS || node==MPI_COMM_NULL)
                fail("按处理器名称建立节点通信域失败");
            MPI_Comm_rank(node,&me);MPI_Comm_size(node,&size);hostname_grouping=true;
        }
        if(size<2 || size>max_ranks || base<2) {
            std::cerr<<"node_coop_v1: world_rank="<<world_rank<<" world_size="<<world_size
                     <<" local_size="<<size<<" threads="<<base
                     <<" grouping="<<(hostname_grouping?"processor_name":"MPI_COMM_TYPE_SHARED")<<std::endl;
            fail("资源协作需要每节点2至128进程，每进程至少2核");
        }

        std::vector<cpu_set_t> masks(size);
        MPI_Allgather(&initial,sizeof(initial),MPI_BYTE,masks.data(),sizeof(initial),MPI_BYTE,node);

        // Validation branch supports two launch layouts:
        // 1) old per-rank disjoint masks; 2) --cpu-bind none, where every local rank
        // sees the same node-wide allocation. In the second case the program creates
        // deterministic per-rank home masks itself and later lends only within that pool.
        shared_pool=true;
        for(int r=1;r<size;++r) if(!CPU_EQUAL(&masks[0],&masks[r])) { shared_pool=false;break; }

        cpu_set_t total;CPU_ZERO(&total);
        if(shared_pool) {
            const int required=size*base;
            if(CPU_COUNT(&initial)<required)
                fail("节点共享CPU池不足：请使用4进程/节点、4核/进程并关闭逐进程绑核");
            std::vector<int> cpus;
            for(int c=0;c<CPU_SETSIZE;++c) if(CPU_ISSET(c,&initial)) cpus.push_back(c);
            if(int(cpus.size())>256) cpus.resize(256);
            if(int(cpus.size())<required) fail("节点共享CPU池可用核数不足");
            for(int r=0;r<size;++r) {
                CPU_ZERO(&masks[r]);
                for(int j=0;j<base;++j) CPU_SET(cpus[r*base+j],&masks[r]);
            }
            home_mask=masks[me];
            for(int r=0;r<size;++r)
                for(int c=0;c<CPU_SETSIZE;++c) if(CPU_ISSET(c,&masks[r])) CPU_SET(c,&total);
            // Confirm the step cgroup permits the complete node allocation, then enter
            // the deterministic home mask before any Netgen worker threads are created.
            pin(total);pin(home_mask);
        } else {
            for(int r=0;r<size;++r) {
                if(CPU_COUNT(&masks[r])!=base)
                    fail("每进程绑定核数不一致；验证分支要求共享CPU池或每进程恰好4核");
                for(int c=0;c<CPU_SETSIZE;++c) if(CPU_ISSET(c,&masks[r])) {
                    if(CPU_ISSET(c,&total)) fail("进程初始核绑定重叠，无法进行无超售借核");
                    CPU_SET(c,&total);
                }
            }
            home_mask=initial;
            if(CPU_COUNT(&total)>256) fail("当前节点调度原型最多支持256个已分配核");
            // Old layout remains supported for node_fixed. Dynamic modes still verify
            // that the scheduler/cgroup allows expanding to sibling-rank CPUs.
            if(mode!=0) { pin(total);pin(home_mask); }
        }

        void *local=nullptr;
        if(MPI_Win_allocate_shared(me==0?sizeof(Shared):0,1,MPI_INFO_NULL,node,&local,&window)!=MPI_SUCCESS)
            fail("MPI共享窗口创建失败；当前MPI实现可能不支持该节点通信域");
        MPI_Aint bytes;int unit;void *root=nullptr;
        if(MPI_Win_shared_query(window,0,&bytes,&unit,&root)!=MPI_SUCCESS || !root)
            fail("MPI共享窗口查询失败");
        shared=static_cast<Shared*>(root);
        int *memory_model=nullptr,flag=0;
        MPI_Win_get_attr(window,MPI_WIN_MODEL,&memory_model,&flag);
        if(!flag || *memory_model!=MPI_WIN_UNIFIED) fail("资源协作要求MPI共享窗口采用统一内存模型");
        MPI_Win_lock_all(0,window);
        if(me==0) {
            new(shared) Shared{};shared->size=size;
            pthread_mutexattr_t attr;
            if(pthread_mutexattr_init(&attr) || pthread_mutexattr_setpshared(&attr,PTHREAD_PROCESS_SHARED) ||
               pthread_mutexattr_setrobust(&attr,PTHREAD_MUTEX_ROBUST) || pthread_mutex_init(&shared->mutex,&attr))
                fail("无法建立进程共享资源锁");
            pthread_mutexattr_destroy(&attr);
            for(int r=0;r<size;++r) {
                bool first=true;
                for(int c=0;c<CPU_SETSIZE;++c) if(CPU_ISSET(c,&masks[r])) {
                    int index=shared->cpus++;shared->cpu[index]=c;shared->home[index]=r;
                    if(first) {shared->anchor[r]=c;first=false;shared->owner[index]=r;}
                    else shared->owner[index]=r;
                }
            }
        }
        MPI_Win_sync(window);MPI_Barrier(node);MPI_Win_sync(window);live=true;
        setup=now()-start;
        if(me==0)
            std::cerr<<"node_coop_v1: node_group="<<(hostname_grouping?"processor_name":"MPI_COMM_TYPE_SHARED")
                     <<" affinity_layout="<<(shared_pool?"shared_pool":"disjoint")
                     <<" node_ranks="<<size<<" node_cpus="<<shared->cpus<<" home_cpus="<<base<<std::endl;
    }
    NodeResources(const NodeResources&)=delete;
    ~NodeResources() = default; // close() is explicit and must precede MPI_Finalize.
    void prepare(double work) {
        // Preserve the original resources during surface/pre-volume work.
        // Publish idle worker CPUs only after this host is pinned to its anchor.
        if(mode!=0) {cpu_set_t anchor;CPU_ZERO(&anchor);CPU_SET(shared->anchor[me],&anchor);pin(anchor);}
        lock();auto &r=shared->rank[me];
        if(r.prepared) fail("内核资源准备被重复调用");
        if(mode!=0) for(int c=0;c<shared->cpus;++c)
            if(shared->owner[c]==me && shared->cpu[c]!=shared->anchor[me]) shared->owner[c]=-1;
        r.prepared=1;r.work=std::max(1.0,work);unlock();
    }
    static int acquire_callback(void *ctx,int phase,double work,int maximum) {
        return static_cast<NodeResources*>(ctx)->acquire(phase,work,maximum);
    }
    static void release_callback(void *ctx,int phase,double work,int threads,double seconds) {
        static_cast<NodeResources*>(ctx)->release(phase,work,threads,seconds);
    }
    int acquire(int phase,double work,int maximum) {
        const double start=now();if(phase<0 || phase>=phases) fail("未知内核资源阶段");
        lock();auto &r=shared->rank[me];if(r.active || r.done) fail("资源租约嵌套或生命周期错误");
        r.phase=phase;r.work=std::max(1.0,work);
        int free=1;
        for(int c=0;c<shared->cpus;++c) if(shared->owner[c]<0) ++free;
        if(maximum<=0) maximum=shared->cpus-size+1;
        int cap=std::min(maximum,mode==0?base:free);
        int target=cap;
        if(mode==2 && phase!=5) {
            int fair=std::min(cap,fair_target());target=model_target(fair,cap);
        }
        if(phase==5) target=1;
        target=std::max(1,std::min(target,cap));
        cpu_set_t mask;CPU_ZERO(&mask);CPU_SET(shared->anchor[me],&mask);held=1;borrowed=0;
        // Prefer own CPUs; use only idle CPUs, never revoke an active lease.
        for(int pass=0;pass<2;++pass) for(int c=0;c<shared->cpus && held<target;++c) {
            if(shared->cpu[c]==shared->anchor[me]) continue;
            if((shared->home[c]==me)!=(pass==0)) continue;
            if((mode==0 && shared->home[c]==me) || (mode!=0 && shared->owner[c]<0)) {
                shared->owner[c]=me;CPU_SET(shared->cpu[c],&mask);++held;
                borrowed+=shared->home[c]!=me;
            }
        }
        r.active=1;r.threads=held;r.start=now();unlock();pin(mask);
        ++epochs;if(borrowed) ++borrow_epochs;peak=std::max(peak,double(held));
        management+=now()-start;return held;
    }
    void release(int phase,double work,int threads,double seconds) {
        const double start=now();
        // Caller guarantees all workers have joined BEFORE this callback.
        cpu_set_t anchor;CPU_ZERO(&anchor);CPU_SET(shared->anchor[me],&anchor);
        pin(mode==0?home_mask:anchor);
        lock();auto &r=shared->rank[me];
        if(!r.active || r.phase!=phase || r.threads!=threads) fail("资源归还与活动租约不匹配");
        if(mode!=0) for(int c=0;c<shared->cpus;++c)
            if(shared->owner[c]==me && shared->cpu[c]!=shared->anchor[me]) shared->owner[c]=-1;
        r.model[phase].observe(threads,work,seconds);r.active=0;r.threads=1;
        unlock();borrow_seconds+=borrowed*seconds;lease_seconds+=threads*seconds;
        phase_seconds+=seconds;management+=now()-start;
    }
    void finish() {
        lock();auto &r=shared->rank[me];if(r.active) fail("内核结束时仍持有活动租约");r.done=1;unlock();
    }
    template<class Profiler> void report(Profiler &p) const {
        const char *names[]={"setup_seconds","management_seconds","epochs","borrow_epochs","borrowed_core_seconds",
            "leased_core_seconds","phase_seconds","peak_threads","model_decisions","model_rejections","cold_decisions",
            "node_ranks","node_cpus","shared_pool_layout","hostname_grouping"};
        double values[]={setup,management,epochs,borrow_epochs,borrow_seconds,lease_seconds,phase_seconds,peak,
            decisions,rejected,cold,double(size),double(shared->cpus),shared_pool?1.0:0.0,hostname_grouping?1.0:0.0};
        for(int i=0;i<15;++i) p.set_metric(std::string("coop_")+names[i],values[i]);
    }
    void close() {
        if(!live) return;
        // Administrative teardown is outside core timing; all mesh work is done.
        MPI_Barrier(node);pin(initial);
        if(me==0) pthread_mutex_destroy(&shared->mutex);
        MPI_Win_unlock_all(window);MPI_Win_free(&window);MPI_Comm_free(&node);shared=nullptr;live=false;
    }
};
}
#endif
