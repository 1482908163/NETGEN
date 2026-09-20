#ifndef NETGEN_NODE_RESOURCES_H
#define NETGEN_NODE_RESOURCES_H
// Linux node-local, non-preemptive CPU leases. No mesh pointers cross processes.
#include <mpi.h>
#include <pthread.h>
#include <sched.h>
#include <time.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdio>
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
        int active=0,done=0,prepared=0,phase=0,threads=1,requesting=0;
        double work=1,start=0;
        int remaining=-1,work_granted=0;
        double progress_time=0,last_operation=0,restart_cost=0;
        Model model[phases];
    };
    struct Shared {
        pthread_mutex_t mutex;
        pthread_cond_t changed;
        int size=0,cpus=0,completion_generation=0,leader_world=0;
        int cpu[CPU_SETSIZE],home[CPU_SETSIZE],owner[CPU_SETSIZE];
        int reserved[CPU_SETSIZE]; // 0=unreserved, local rank+1 otherwise.
        int anchor[max_ranks];
        double origin=0,clock=0,idle_core_seconds=0,reserved_core_seconds=0;
        double phase_idle_core_seconds=0,early_loan_core_seconds=0;
        RankState rank[max_ranks];
    };

    MPI_Comm node=MPI_COMM_NULL;
    Shared *shared=nullptr;
    int shm_fd=-1;
    std::string shm_name;
    cpu_set_t initial,home_mask;
    int me=0,size=0,base=1,mode=0,held=1,borrowed=0;
    bool live=false,shared_pool=false,hostname_grouping=false;
    double management=0,setup=0,epochs=0,borrow_epochs=0,borrow_seconds=0;
    double lease_seconds=0,phase_seconds=0,peak=1,decisions=0,rejected=0,cold=0;
    int seen_completion=0,checkpoint_old_threads=0;
    double checkpoint_checks=0,checkpoint_restarts=0,checkpoint_grants=0,checkpoint_seconds=0;
    double work_checks=0,work_unknown=0,work_short=0,work_cost=0,work_deferred=0;
    double work_no_capacity=0,work_grants=0,work_reserved=0,work_already=0;
    double work_gain_estimate=0,work_restart_estimate=0;
    double start_offset=0,finish_offset=0,first_loan=0,last_loan=0,loan_events=0;
    double node_idle=0,node_reserved=0,node_last=0;
    int node_leader=0;
    double elastic_no_capacity=0,elastic_no_event=0,elastic_reserved=0;
    double stage_waits=0,stage_wait_seconds=0,stage_early_epochs=0,stage_early_cores=0;
    double stage_reclaims=0,stage_shrinks=0,stage_unchanged=0,stage_no_change=0;
    double node_phase_idle=0,node_early_loan=0;
    bool force_base=false,early_used=false;
    double stage_growth_deferred=0;
    bool selective_policy() const { return mode==11 || mode==12; }
    bool stage_policy() const { return mode==9 || mode==10 || selective_policy(); }
    bool early_allowed() const {
        return !selective_policy() || (shared->rank[me].phase==6 && (mode!=12 || !early_used));
    }
    bool donor_available(int home) const {
        const auto &d=shared->rank[home];
        return d.done || (stage_policy() && early_allowed() && d.prepared && !d.active && !d.requesting);
    }
    bool home_ready() const {
        for(int c=0;c<shared->cpus;++c) if(shared->home[c]==me && shared->owner[c]>=0 && shared->owner[c]!=me) return false;
        return true;
    }
    bool atomic_tail() const { return mode==7 || mode==8; }
    bool can_take(int c) const {
        if(stage_policy()) return shared->home[c]!=me && donor_available(shared->home[c]) &&
            (shared->reserved[c]==0 || shared->reserved[c]==me+1);
        if(work_policy() || (atomic_tail() && checkpoint_old_threads))
            return shared->reserved[c]==me+1;
        return shared->reserved[c]==0;
    }
    // Called under the resource mutex BEFORE each change to ownership or done flags.
    // Fixed control counts finished owners' non-anchor CPUs as idle donor capacity.
    // End integration at the last local kernel completion; no MPI wait is included.
    void account_pool() {
        const double stamp=now();int unfinished=0,idle=0,reserving=0,phase_idle=0,early=0;
        for(int i=0;i<size;++i) unfinished+=!shared->rank[i].done;
        if(unfinished) for(int c=0;c<shared->cpus;++c) {
            const int home=shared->home[c],owner=shared->owner[c];
            if(shared->cpu[c]==shared->anchor[home]) continue;
            const auto &donor=shared->rank[home];
            if(!donor.done) {
                if(donor.prepared && !donor.active && !donor.requesting && !shared->reserved[c] &&
                   (owner<0 || owner==home)) ++phase_idle;
                if(owner>=0 && owner!=home && shared->rank[owner].active) ++early;
                continue;
            }
            if(owner<0) {
                if(shared->reserved[c]) ++reserving;else ++idle;
            } else if(shared->rank[owner].done) ++idle;
        }
        const double dt=stamp-shared->clock;
        shared->idle_core_seconds+=idle*dt;
        shared->reserved_core_seconds+=reserving*dt;
        shared->phase_idle_core_seconds+=phase_idle*dt;shared->early_loan_core_seconds+=early*dt;
        shared->clock=stamp;
    }
    struct PhaseStats {
        double calls=0,seconds=0,core_seconds=0,borrowed_seconds=0,below_base=0;
        int minimum=0,maximum=0;
        double early_calls=0,reclaims=0;
    };
    PhaseStats phase_stats[phases];
    bool protected_base() const { return mode>=2; }
    bool work_policy() const { return mode==5 || mode==6; }
    bool work_eligible(const RankState &r,int extra,double stamp) const {
        if(!r.active || r.done || r.phase!=6 || r.work_granted || r.remaining<2 || r.last_operation<=0) return false;
        // A stale peer observation must not reserve the queue indefinitely.
        if(stamp-r.progress_time>std::max(.05,4*r.last_operation)) return false;
        double gain=r.last_operation*r.remaining*extra/(r.threads+extra);
        return gain>2*std::max(.001,r.restart_cost)+.005;
    }
    static std::string mask_text(const cpu_set_t &mask) {
        std::string value;
        for(int c=0;c<CPU_SETSIZE;++c) if(CPU_ISSET(c,&mask)) {
            if(!value.empty()) value+=",";
            value+=std::to_string(c);
        }
        return value;
    }

    static double now() {
        timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+1e-9*t.tv_nsec;
    }
    [[noreturn]] void fail(const char *message) const {
        std::cerr<<"node_coop_v1: "<<message<<std::endl;
        MPI_Abort(MPI_COMM_WORLD,87);std::terminate();
    }
    [[noreturn]] void fail_errno(const char *message) const {
        std::cerr<<"node_coop_v1: "<<message<<": "<<std::strerror(errno)<<std::endl;
        MPI_Abort(MPI_COMM_WORLD,87);std::terminate();
    }
    void lock() { if(pthread_mutex_lock(&shared->mutex)!=0) fail("节点资源锁失败"); }
    void unlock() { if(pthread_mutex_unlock(&shared->mutex)!=0) fail("节点资源解锁失败"); }
    void pin(const cpu_set_t &mask) {
        if(sched_setaffinity(0,sizeof(mask),&mask)!=0) fail_errno("集群不允许作业内跨进程核亲和性调整");
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
                if(r.phase==5) limit=1;
                const int minimum=protected_base() && r.phase!=5 ? base : 1;
                for(int k=minimum;k<=limit;++k) {
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
        double overhead=epochs>0?management/epochs:0;
        bool shorter=fixed-best>2*uncertainty+2*overhead;
        bool slack=target<fair && pending_upper+2*overhead<locked_lower;
        if(target==fair || (!shorter && !slack)) { ++rejected;return fair; }
        ++decisions;return target;
    }

    void create_posix_shared_state(int world_rank) {
        char name[128]={0};
        if(me==0) {
            timespec stamp{};clock_gettime(CLOCK_REALTIME,&stamp);
            std::snprintf(name,sizeof(name),"/netgen_node_%d_%ld_%lld",world_rank,
                          static_cast<long>(getpid()),
                          static_cast<long long>(stamp.tv_nsec));
        }
        if(MPI_Bcast(name,sizeof(name),MPI_CHAR,0,node)!=MPI_SUCCESS)
            fail("无法广播POSIX共享内存名称");
        shm_name=name;

        if(me==0) {
            shm_fd=shm_open(shm_name.c_str(),O_CREAT|O_EXCL|O_RDWR,0600);
            if(shm_fd<0) fail_errno("创建POSIX节点共享内存失败");
            if(ftruncate(shm_fd,sizeof(Shared))!=0) fail_errno("设置POSIX共享内存大小失败");
        }
        MPI_Barrier(node);
        if(me!=0) {
            shm_fd=shm_open(shm_name.c_str(),O_RDWR,0600);
            if(shm_fd<0) fail_errno("打开POSIX节点共享内存失败");
        }
        void *mapping=mmap(nullptr,sizeof(Shared),PROT_READ|PROT_WRITE,MAP_SHARED,shm_fd,0);
        if(mapping==MAP_FAILED) { shared=nullptr;fail_errno("映射POSIX节点共享内存失败"); }
        shared=static_cast<Shared*>(mapping);
    }

public:
    // 0=fixed, 1=greedy, 2=model, 3=guarded, 4=tail, 5=cost gate, 6=work priority.
    // 7=atomic event-driven tail; 8=atomic capacity-driven tail.
    // 9=stage lending; 10=stage lending with safe-point return.
    // 11=early lending only in final optimization; 12=at most one early lease.
    NodeResources(MPI_Comm world,int threads,int policy):base(threads),mode(policy) {
        const double start=now();CPU_ZERO(&initial);CPU_ZERO(&home_mask);
        if(sched_getaffinity(0,sizeof(initial),&initial)!=0) fail("无法读取初始核绑定");

        int world_rank=0,world_size=0;
        MPI_Comm_rank(world,&world_rank);MPI_Comm_size(world,&world_size);
        int split_rc=MPI_Comm_split_type(world,MPI_COMM_TYPE_SHARED,0,MPI_INFO_NULL,&node);
        if(split_rc!=MPI_SUCCESS || node==MPI_COMM_NULL)
            fail("MPI_COMM_TYPE_SHARED 节点分组失败");
        MPI_Comm_rank(node,&me);MPI_Comm_size(node,&size);

        // MPI-X/PMIx can return singleton shared communicators on this cluster.
        // Fall back to explicit processor-name grouping for node-local coordination.
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
        shared_pool=true;
        for(int r=1;r<size;++r) if(!CPU_EQUAL(&masks[0],&masks[r])) { shared_pool=false;break; }

        cpu_set_t total;CPU_ZERO(&total);
        if(shared_pool) {
            const int required=size*base;
            if(CPU_COUNT(&initial)<required)
                fail("节点共享CPU池不足：请关闭逐进程绑核并保证节点分配核数充足");
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
            pin(total);pin(home_mask);
        } else {
            for(int r=0;r<size;++r) {
                if(CPU_COUNT(&masks[r])!=base)
                    fail("每进程绑定核数不一致；验证分支要求共享CPU池或每进程固定核数");
                for(int c=0;c<CPU_SETSIZE;++c) if(CPU_ISSET(c,&masks[r])) {
                    if(CPU_ISSET(c,&total)) fail("进程初始核绑定重叠，无法进行无超售借核");
                    CPU_SET(c,&total);
                }
            }
            home_mask=initial;
            if(CPU_COUNT(&total)>256) fail("当前节点调度原型最多支持256个已分配核");
            if(mode!=0) { pin(total);pin(home_mask); }
        }

        // Do not use MPI_Win_allocate_shared here: MPI-X accepts the communicator
        // but MPI_Win_shared_query fails after processor-name fallback grouping.
        // POSIX shared memory is node-local by construction and independent of MPI's
        // shared-memory communicator implementation.
        create_posix_shared_state(world_rank);
        if(me==0) {
            std::memset(shared,0,sizeof(Shared));shared->size=size;
            shared->origin=shared->clock=now();shared->leader_world=world_rank;
            pthread_mutexattr_t attr;
            if(pthread_mutexattr_init(&attr) || pthread_mutexattr_setpshared(&attr,PTHREAD_PROCESS_SHARED) ||
               pthread_mutexattr_setrobust(&attr,PTHREAD_MUTEX_ROBUST) || pthread_mutex_init(&shared->mutex,&attr))
                fail("无法建立进程共享资源锁");
            pthread_mutexattr_destroy(&attr);
            pthread_condattr_t condattr;
            if(pthread_condattr_init(&condattr) || pthread_condattr_setpshared(&condattr,PTHREAD_PROCESS_SHARED) ||
               pthread_cond_init(&shared->changed,&condattr)) fail("无法建立节点资源条件变量");
            pthread_condattr_destroy(&condattr);
            for(int r=0;r<size;++r) {
                bool first=true;
                for(int c=0;c<CPU_SETSIZE;++c) if(CPU_ISSET(c,&masks[r])) {
                    int index=shared->cpus++;shared->cpu[index]=c;shared->home[index]=r;
                    if(first) {shared->anchor[r]=c;first=false;}
                    shared->owner[index]=r;
                }
            }
        }
        MPI_Barrier(node);
        // Every local rank has opened and mapped the segment; remove its namespace
        // entry now so crashes cannot leave persistent /dev/shm garbage.
        if(me==0 && shm_unlink(shm_name.c_str())!=0 && errno!=ENOENT)
            fail_errno("删除POSIX共享内存名称失败");
        MPI_Barrier(node);
        node_leader=shared->leader_world;
        live=true;setup=now()-start;
        if(me==0) for(int r=0;r<size;++r) {
            const auto layout=std::string("node_coop_v2_layout: leader_world_rank=")+std::to_string(world_rank)+
                " local_rank="+std::to_string(r)+" pool="+mask_text(total)+" home="+mask_text(masks[r]);
            std::cerr<<layout<<std::endl;
        }
        if(me==0)
            std::cerr<<"node_coop_v1: node_group="<<(hostname_grouping?"processor_name":"MPI_COMM_TYPE_SHARED")
                     <<" shared_state=posix_shm"
                     <<" affinity_layout="<<(shared_pool?"shared_pool":"disjoint")
                     <<" node_ranks="<<size<<" node_cpus="<<shared->cpus<<" home_cpus="<<base<<std::endl;
    }

    NodeResources(const NodeResources&)=delete;
    ~NodeResources() = default;

    void prepare(double work) {
        if(mode!=0) {cpu_set_t anchor;CPU_ZERO(&anchor);CPU_SET(shared->anchor[me],&anchor);pin(anchor);}
        lock();account_pool();auto &r=shared->rank[me];
        if(r.prepared) fail("内核资源准备被重复调用");
        if(mode!=0 && (!protected_base() || stage_policy())) for(int c=0;c<shared->cpus;++c)
            if(shared->owner[c]==me && shared->cpu[c]!=shared->anchor[me]) shared->owner[c]=-1;
        start_offset=now()-shared->origin;
        r.prepared=1;r.work=std::max(1.0,work);pthread_cond_broadcast(&shared->changed);unlock();
    }
    static int acquire_callback(void *ctx,int phase,double work,int maximum) {
        return static_cast<NodeResources*>(ctx)->acquire(phase,work,maximum);
    }
    static void release_callback(void *ctx,int phase,double work,int threads,double seconds) {
        static_cast<NodeResources*>(ctx)->release(phase,work,threads,seconds);
    }
    static int poll_callback(void *ctx) { return static_cast<NodeResources*>(ctx)->poll(); }
    static int poll_work_callback(void *ctx,double work,int remaining,double last,double restart) {
        return static_cast<NodeResources*>(ctx)->poll_work(work,remaining,last,restart);
    }
    int poll_work(double work,int remaining,double last,double restart) {
        if(!work_policy()) return 0;
        if(!std::isfinite(work) || work<=0 || !std::isfinite(last) || last<0 ||
           !std::isfinite(restart) || restart<0) fail("非法安全点进度");
        const double stamp=now();++checkpoint_checks;++work_checks;
        lock();account_pool();auto &r=shared->rank[me];
        if(!r.active || r.done) fail("工作量检查不在活动租约内");
        r.work=work;r.remaining=remaining;r.last_operation=last;
        r.restart_cost=restart;r.progress_time=stamp;
        int extra=0;
        for(int c=0;c<shared->cpus;++c)
            if(shared->owner[c]<0 && shared->reserved[c]==0 && shared->rank[shared->home[c]].done) ++extra;
        bool grant=false;
        if(r.work_granted) ++work_already;
        else if(r.phase!=6 || remaining<0 || last<=0) ++work_unknown;
        else if(remaining<2) ++work_short;
        else if(!extra) ++work_no_capacity;
        else if(!work_eligible(r,extra,stamp)) ++work_cost;
        else {
            int winner=me;double score=work*remaining/r.threads;
            if(mode==6) for(int i=0;i<size;++i) {
                const auto &peer=shared->rank[i];
                if(!work_eligible(peer,extra,stamp)) continue;
                double candidate=peer.work*peer.remaining/peer.threads;
                if(candidate>score || (candidate==score && i<winner)) {winner=i;score=candidate;}
            }
            if(winner!=me) ++work_deferred;
            else {
                // Reserve before stopping workers: another claimant cannot consume
                // the selected cores in the release/acquire gap.
                for(int c=0;c<shared->cpus;++c)
                    if(shared->owner[c]<0 && shared->reserved[c]==0 && shared->rank[shared->home[c]].done)
                        shared->reserved[c]=me+1;
                r.work_granted=1;checkpoint_old_threads=r.threads;
                ++checkpoint_restarts;++work_grants;work_reserved+=extra;
                work_gain_estimate+=last*remaining*extra/(r.threads+extra);
                work_restart_estimate+=restart;grant=true;
            }
        }
        unlock();checkpoint_seconds+=now()-stamp;return grant?1:0;
    }
    int poll_stage() {
        const double start=now();++checkpoint_checks;
        lock();account_pool();auto &r=shared->rank[me];
        if(!r.active || r.done || checkpoint_old_threads) fail("阶段借核检查生命周期错误");
        int reclaim=0,extra=0,unfinished_held=0;
        for(int c=0;c<shared->cpus;++c) {
            int home=shared->home[c];
            if(shared->owner[c]==me && home!=me && !shared->rank[home].done) {
                ++unfinished_held;
                if(shared->rank[home].requesting) ++reclaim;
            }
            if(shared->owner[c]<0 && shared->reserved[c]==0 && home!=me && donor_available(home)) ++extra;
        }
        bool refresh=false;
        // Return all borrowed CPUs before reacquiring the protected base. Waiting
        // ranks never hold a foreign CPU; only active finite operations delay them.
        if((mode==10 || selective_policy()) && reclaim) {
            force_base=true;++stage_reclaims;++phase_stats[r.phase].reclaims;refresh=true;
        }
        // Keep the one early lease intact until owner demand or normal release.
        // Demand has priority over this suppression; never postpone returning CPUs.
        else if(mode==12 && unfinished_held && extra) ++stage_growth_deferred;
        else if(!reclaim && extra) {
            for(int c=0;c<shared->cpus;++c) {
                int home=shared->home[c];
                if((shared->owner[c]==me && home!=me) ||
                   (shared->owner[c]<0 && !shared->reserved[c] && home!=me && donor_available(home)))
                    shared->reserved[c]=me+1;
            }
            refresh=true;
        }
        if(refresh) {r.requesting=1;checkpoint_old_threads=r.threads;++checkpoint_restarts;}
        else ++stage_no_change;
        unlock();checkpoint_seconds+=now()-start;return refresh?1:0;
    }
    int poll_atomic() {
        const double start=now();++checkpoint_checks;
        lock();account_pool();const auto &r=shared->rank[me];
        if(!r.active || r.done || checkpoint_old_threads) fail("原子借核检查生命周期错误");
        bool event=shared->completion_generation!=seen_completion;
        seen_completion=shared->completion_generation;
        int extra=0;
        if(mode==7 && !event) ++elastic_no_event;
        else {
            for(int c=0;c<shared->cpus;++c)
                if(shared->owner[c]<0 && !shared->reserved[c] && shared->rank[shared->home[c]].done) ++extra;
            if(!extra) ++elastic_no_capacity;
        }
        if(extra) {
            // Protect BOTH new CPUs and the current borrowed set through release/acquire.
            // Releasing old borrowed CPUs without reserving them can turn an apparent
            // increase into a shrink when another recipient reaches its safe point.
            for(int c=0;c<shared->cpus;++c)
                if((shared->owner[c]<0 && !shared->reserved[c] && shared->rank[shared->home[c]].done) ||
                   (shared->owner[c]==me && shared->home[c]!=me)) shared->reserved[c]=me+1;
            checkpoint_old_threads=r.threads;++checkpoint_restarts;elastic_reserved+=extra;
        }
        unlock();checkpoint_seconds+=now()-start;return extra?1:0;
    }
    int poll() {
        if(stage_policy()) return poll_stage();
        if(atomic_tail()) return poll_atomic();
        if(mode!=4) return 0;
        const double start=now();++checkpoint_checks;
        lock();const auto &r=shared->rank[me];
        if(!r.active || r.done) fail("安全点检查不在活动租约内");
        bool refresh=false;
        if(shared->completion_generation!=seen_completion) {
            seen_completion=shared->completion_generation;
            for(int c=0;c<shared->cpus;++c)
                if(shared->owner[c]<0 && shared->reserved[c]==0 && shared->rank[shared->home[c]].done) refresh=true;
        }
        if(refresh) {++checkpoint_restarts;checkpoint_old_threads=r.threads;}
        unlock();checkpoint_seconds+=now()-start;
        return refresh?1:0;
    }
    int acquire(int phase,double work,int maximum) {
        const double start=now();if(phase<0 || phase>=phases) fail("未知内核资源阶段");
        lock();account_pool();auto &r=shared->rank[me];if(r.active || r.done) fail("资源租约嵌套或生命周期错误");
        r.phase=phase;r.work=std::max(1.0,work);
        if(stage_policy()) {
            r.requesting=1;
            // Owner demand overrides any not-yet-consumed reservation. A waiting
            // requester must not reserve foreign CPUs, avoiding wait cycles.
            for(int c=0;c<shared->cpus;++c) if(shared->reserved[c]==me+1 &&
                (force_base || shared->rank[shared->home[c]].requesting || !home_ready())) shared->reserved[c]=0;
            pthread_cond_broadcast(&shared->changed);
            const bool wait=!home_ready();const double begin=now();
            while(!home_ready()) {
                if(pthread_cond_wait(&shared->changed,&shared->mutex)!=0) fail("等待保底核归还失败");
                account_pool();
            }
            if(wait) {++stage_waits;stage_wait_seconds+=now()-begin;}
        }
        int free=protected_base()?base:1;
        for(int c=0;c<shared->cpus;++c) if(shared->owner[c]<0 &&
            can_take(c)) ++free;
        if(maximum<=0) maximum=shared->cpus-size+1;
        int cap=std::min(maximum,mode==0?base:free);
        int target=force_base?std::min(base,cap):cap;
        if(mode==2 && phase!=5) {
            int fair=std::min(cap,fair_target());target=model_target(fair,cap);
        }
        if(phase==5) target=1;
        target=std::max(1,std::min(target,cap));
        if(protected_base() && phase!=5 && target<base)
            fail("保底核策略产生低于初始核数的租约");
        cpu_set_t mask;CPU_ZERO(&mask);CPU_SET(shared->anchor[me],&mask);held=1;borrowed=0;
        int early_cores=0;
        for(int pass=0;pass<2;++pass) for(int c=0;c<shared->cpus && held<target;++c) {
            if(shared->cpu[c]==shared->anchor[me]) continue;
            if((shared->home[c]==me)!=(pass==0)) continue;
            if(((mode==0 || protected_base()) && shared->home[c]==me) || (mode!=0 && shared->owner[c]<0 &&
                can_take(c))) {
                if(protected_base() && !stage_policy() && shared->home[c]!=me && !shared->rank[shared->home[c]].done)
                    fail("保底核策略借用了未完成进程的核");
                shared->owner[c]=me;shared->reserved[c]=0;CPU_SET(shared->cpu[c],&mask);++held;
                borrowed+=shared->home[c]!=me;
                early_cores+=shared->home[c]!=me && !shared->rank[shared->home[c]].done;
            }
        }
        if(held!=target) fail("实际分配核数与租约目标不一致");
        if(checkpoint_old_threads) {
            if((work_policy() || atomic_tail()) && held<=checkpoint_old_threads) fail("已预留的增核请求没有兑现");
            if(held>checkpoint_old_threads) ++checkpoint_grants;
            else if(stage_policy()) {if(held<checkpoint_old_threads) ++stage_shrinks;else ++stage_unchanged;}
            checkpoint_old_threads=0;
        }
        seen_completion=shared->completion_generation;
        r.remaining=-1;r.last_operation=0;
        if(stage_policy()) {
            for(int c=0;c<shared->cpus;++c) if(shared->reserved[c]==me+1) shared->reserved[c]=0;
            if(early_cores) {
                if(selective_policy() && phase!=6) fail("受限提前借核进入了非最终优化阶段");
                if(mode==12 && early_used) fail("单次提前借核策略重复借用了未完成进程的核");
                early_used=true;++stage_early_epochs;stage_early_cores+=early_cores;
                ++phase_stats[phase].early_calls;
            }
        }
        force_base=false;r.requesting=0;
        r.active=1;r.threads=held;r.start=now();pthread_cond_broadcast(&shared->changed);unlock();pin(mask);
        ++epochs;if(borrowed) {
            ++borrow_epochs;++loan_events;last_loan=now()-shared->origin;
            if(loan_events==1) first_loan=last_loan;
        }peak=std::max(peak,double(held));
        auto &ps=phase_stats[phase];++ps.calls;
        if(held<base) ++ps.below_base;
        ps.minimum=ps.minimum?std::min(ps.minimum,held):held;
        ps.maximum=std::max(ps.maximum,held);
        management+=now()-start;return held;
    }
    void release(int phase,double work,int threads,double seconds) {
        const double start=now();
        cpu_set_t anchor;CPU_ZERO(&anchor);CPU_SET(shared->anchor[me],&anchor);
        pin(mode==0?home_mask:anchor);
        lock();account_pool();auto &r=shared->rank[me];
        if(!r.active || r.phase!=phase || r.threads!=threads) fail("资源归还与活动租约不匹配");
        if(mode!=0) for(int c=0;c<shared->cpus;++c)
            if(shared->owner[c]==me && shared->cpu[c]!=shared->anchor[me] &&
               (!protected_base() || stage_policy() || shared->home[c]!=me)) shared->owner[c]=-1;
        r.model[phase].observe(threads,work,seconds);r.active=0;r.threads=1;
        pthread_cond_broadcast(&shared->changed);unlock();borrow_seconds+=borrowed*seconds;lease_seconds+=threads*seconds;
        phase_seconds+=seconds;management+=now()-start;
        auto &ps=phase_stats[phase];ps.seconds+=seconds;
        ps.core_seconds+=threads*seconds;ps.borrowed_seconds+=borrowed*seconds;
    }
    void finish() {
        if(mode!=0) {cpu_set_t anchor;CPU_ZERO(&anchor);CPU_SET(shared->anchor[me],&anchor);pin(anchor);}
        lock();account_pool();auto &r=shared->rank[me];if(r.active || r.done || r.requesting || checkpoint_old_threads) fail("内核结束时仍持有活动租约或重复结束");
        finish_offset=now()-shared->origin;
        r.done=1;
        ++shared->completion_generation;
        if(mode!=0) for(int c=0;c<shared->cpus;++c)
            if(shared->owner[c]==me && shared->cpu[c]!=shared->anchor[me]) shared->owner[c]=-1;
        bool last=true;for(int i=0;i<size;++i) if(!shared->rank[i].done) last=false;
        if(last) {node_last=1;node_idle=shared->idle_core_seconds;node_reserved=shared->reserved_core_seconds;node_phase_idle=shared->phase_idle_core_seconds;node_early_loan=shared->early_loan_core_seconds;}
        pthread_cond_broadcast(&shared->changed);unlock();
    }
    template<class Profiler> void report(Profiler &p,bool include_stage=true) const {
        const char *stage_names[]={"waits","wait_seconds","early_epochs","early_cores","reclaims","shrinks","unchanged","no_change","idle_core_seconds","early_core_seconds"};
        const double stage_values[]={stage_waits,stage_wait_seconds,stage_early_epochs,stage_early_cores,stage_reclaims,stage_shrinks,stage_unchanged,stage_no_change,node_phase_idle,node_early_loan};
        if(include_stage) for(int i=0;i<10;++i) p.set_metric(std::string("coop_stage_")+stage_names[i],stage_values[i]);
        if(include_stage) p.set_metric("coop_stage_growth_deferred",stage_growth_deferred);
        p.set_metric("coop_elastic_no_capacity",elastic_no_capacity);
        p.set_metric("coop_elastic_no_event",elastic_no_event);
        p.set_metric("coop_elastic_reserved_cores",elastic_reserved);
        p.set_metric("coop_timeline_node",node_leader);
        p.set_metric("coop_timeline_start_seconds",start_offset);
        p.set_metric("coop_timeline_finish_seconds",finish_offset);
        p.set_metric("coop_timeline_first_loan_seconds",first_loan);
        p.set_metric("coop_timeline_last_loan_seconds",last_loan);
        p.set_metric("coop_timeline_loan_events",loan_events);
        p.set_metric("coop_timeline_node_last",node_last);
        p.set_metric("coop_timeline_idle_core_seconds",node_idle);
        p.set_metric("coop_timeline_reserved_core_seconds",node_reserved);
        p.set_metric("coop_checkpoint_checks",checkpoint_checks);
        p.set_metric("coop_checkpoint_restarts",checkpoint_restarts);
        p.set_metric("coop_checkpoint_grants",checkpoint_grants);
        p.set_metric("coop_checkpoint_seconds",checkpoint_seconds);
        const char *work_names[]={"checks","unknown","short","cost","deferred","no_capacity",
                                  "grants","reserved_cores","already","gain_estimate_seconds","restart_estimate_seconds"};
        const double work_values[]={work_checks,work_unknown,work_short,work_cost,work_deferred,work_no_capacity,
                                    work_grants,work_reserved,work_already,work_gain_estimate,work_restart_estimate};
        for(int i=0;i<11;++i) p.set_metric(std::string("coop_work_")+work_names[i],work_values[i]);
        const char *names[]={"setup_seconds","management_seconds","epochs","borrow_epochs","borrowed_core_seconds",
            "leased_core_seconds","phase_seconds","peak_threads","model_decisions","model_rejections","cold_decisions",
            "node_ranks","node_cpus","shared_pool_layout","hostname_grouping"};
        double values[]={setup,management,epochs,borrow_epochs,borrow_seconds,lease_seconds,phase_seconds,epochs?peak:double(base),
            decisions,rejected,cold,double(size),double(shared->cpus),shared_pool?1.0:0.0,hostname_grouping?1.0:0.0};
        for(int i=0;i<15;++i) p.set_metric(std::string("coop_")+names[i],values[i]);
        for(int phase=0;phase<phases;++phase) {
            const auto &s=phase_stats[phase];
            const auto prefix=std::string("coop_phase_")+std::to_string(phase)+"_";
            p.set_metric(prefix+"epochs",s.calls);
            p.set_metric(prefix+"seconds",s.seconds);
            p.set_metric(prefix+"core_seconds",s.core_seconds);
            p.set_metric(prefix+"borrowed_core_seconds",s.borrowed_seconds);
            p.set_metric(prefix+"below_base_epochs",s.below_base);
            p.set_metric(prefix+"min_threads",s.minimum);
            p.set_metric(prefix+"max_threads",s.maximum);
            if(include_stage) {
                p.set_metric(prefix+"early_epochs",s.early_calls);
                p.set_metric(prefix+"reclaims",s.reclaims);
            }
        }
    }
    void close() {
        if(!live) return;
        MPI_Barrier(node);pin(initial);
        if(me==0) {pthread_cond_destroy(&shared->changed);pthread_mutex_destroy(&shared->mutex);}
        MPI_Barrier(node);
        if(shared && munmap(shared,sizeof(Shared))!=0) fail_errno("解除POSIX共享内存映射失败");
        shared=nullptr;
        if(shm_fd>=0) { ::close(shm_fd);shm_fd=-1; }
        MPI_Comm_free(&node);live=false;
    }
};
}
#endif
