#include "task_mesh.h"
#include "mesh_mpi_types.h"
#include "task_schedule.h"
#include "worklet_schedule.h"
#include "worklet_failure.h"
#include "scaling_profiler.h"
#include "sparse_face_exchange.h"
#include <climits>
#include <cstring>
#include <iomanip>
#include <memory>
#include <sstream>
namespace nglib {
#include <nglib.h>
}
namespace {
using FaceKey=std::array<int,3>;
struct TaskMesh {
    int task;
    nglib::Ng_Mesh *mesh;
    std::map<int,int> g2l;
    std::map<Barycentric,int,CompBarycentric> bary;
    std::list<xdFace> faces;
    explicit TaskMesh(int t):task(t),mesh(nglib::Ng_NewMesh()){}
    ~TaskMesh(){nglib::Ng_DeleteMesh(mesh);}
};
FaceKey key(const xdFace &face) {
    std::set<int> vertices;
    for(const auto &b:face.barycv)for(int v:b.gvrtx)if(v>0)vertices.insert(v);
    if(vertices.size()!=3)throw std::runtime_error("task face lost its coarse parent");
    FaceKey result;std::copy(vertices.begin(),vertices.end(),result.begin());return result;
}
std::uint64_t fingerprint(nglib::Ng_Mesh *mesh) {
    std::uint64_t h=1469598103934665603ULL;
    auto add=[&](const void *data,std::size_t n){const auto *b=static_cast<const unsigned char *>(data);for(std::size_t i=0;i<n;++i){h^=b[i];h*=1099511628211ULL;}};
    for(int i=1;i<=nglib::Ng_GetNP(mesh);++i){double x[3];nglib::Ng_GetPoint(mesh,i,x);add(x,sizeof(x));}
    for(int i=1;i<=nglib::Ng_GetNE(mesh);++i){int v[4],domain;nglib::Ng_GetVolumeElement(mesh,i,v,domain);add(v,sizeof(v));add(&domain,sizeof(domain));}
    return h;
}
std::uint64_t task_fingerprint(const TaskMesh &task) {
    std::uint64_t h=fingerprint(task.mesh);
    auto add=[&](int v){h=(h^static_cast<std::uint32_t>(v))*1099511628211ULL;};
    auto add_bary=[&](const Barycentric &b){for(int v:b.gvrtx)add(v);for(short v:b.coord)add(v);};
    add(task.task);
    for(int i=1;i<=nglib::Ng_GetNFD(task.mesh);++i){int x[4];nglib::My_Ng_GetFaceDescriptor(task.mesh,i,x);for(int v:x)add(v);}
    for(const auto &v:task.g2l){add(v.first);add(v.second);}
    for(const auto &v:task.bary){add_bary(v.first);add(v.second);}
    for(const auto &f:task.faces){for(int v:f.lsvrtx)add(v);for(const auto &b:f.barycv)add_bary(b);add(f.outw);add(f.geoboundary);}
    return h;
}
// Portable typed wire format, not raw C++ structs/pointers or MPI int-sized
// byte totals. All ranks visit task IDs in order, preventing cyclic sends.
void return_task(std::unique_ptr<TaskMesh> &task,int t,int source,int target,
                 int rank,nglib::Ng_Mesh *coarse,MPI_Comm comm) {
    using namespace mesh_research;
    if(rank!=source && rank!=target)return;
    const bool send=rank==source;const int peer=send?target:source;
    int header[6]={};
    if(send) {
        header[0]=nglib::Ng_GetNP(task->mesh);header[1]=nglib::Ng_GetNE(task->mesh);
        header[2]=nglib::Ng_GetNFD(task->mesh);header[3]=static_cast<int>(task->g2l.size());
        header[4]=static_cast<int>(task->bary.size());header[5]=static_cast<int>(task->faces.size());
        check_mpi(MPI_Send(header,6,MPI_INT,peer,20,comm),comm);
    } else check_mpi(MPI_Recv(header,6,MPI_INT,peer,20,comm,MPI_STATUS_IGNORE),comm);
    for(int n:header)if(n<0)throw std::runtime_error("negative task wire size");
    if(header[0]==0 || header[1]==0 || header[2]<nglib::Ng_GetNFD(coarse) || header[2]>SHRT_MAX)
        throw std::runtime_error("invalid task wire header");
    const std::size_t np=header[0],ne=header[1],nf=header[2],ng=header[3],nb=header[4],ns=header[5];
    std::vector<double> xyz(3*np);
    std::vector<int> data(5*ne+4*nf+2*ng+7*nb+23*ns);
    if(send) {
        for(int i=1;i<=header[0];++i)nglib::Ng_GetPoint(task->mesh,i,xyz.data()+3*static_cast<std::size_t>(i-1));
        std::size_t pos=0;
        for(int i=1;i<=header[1];++i){nglib::Ng_GetVolumeElement(task->mesh,i,data.data()+pos,data[pos+4]);pos+=5;}
        for(int i=1;i<=header[2];++i){nglib::My_Ng_GetFaceDescriptor(task->mesh,i,data.data()+pos);pos+=4;}
        for(const auto &v:task->g2l){data[pos++]=v.first;data[pos++]=v.second;}
        auto pack_bary=[&](const Barycentric &b){for(int v:b.gvrtx)data[pos++]=v;for(short v:b.coord)data[pos++]=v;};
        for(const auto &v:task->bary){pack_bary(v.first);data[pos++]=v.second;}
        for(const auto &f:task->faces){for(int v:f.lsvrtx)data[pos++]=v;for(const auto &b:f.barycv)pack_bary(b);data[pos++]=f.outw;data[pos++]=f.geoboundary;}
        if(pos!=data.size())throw std::runtime_error("task packing size mismatch");
    }
    auto transfer=[&](void *buffer,std::size_t count,std::size_t width,MPI_Datatype type) {
        auto *bytes=static_cast<char *>(buffer);
        for(std::size_t offset=0;offset<count;) {
            const int chunk=static_cast<int>(std::min<std::size_t>(count-offset,1U<<20));
            if(send)check_mpi(MPI_Send(bytes+offset*width,chunk,type,peer,21,comm),comm);
            else check_mpi(MPI_Recv(bytes+offset*width,chunk,type,peer,21,comm,MPI_STATUS_IGNORE),comm);
            offset+=chunk;
        }
    };
    transfer(xyz.data(),xyz.size(),sizeof(double),MPI_DOUBLE);
    transfer(data.data(),data.size(),sizeof(int),MPI_INT);
    const std::uint64_t bytes=sizeof(header)+xyz.size()*sizeof(double)+data.size()*sizeof(int);
    const std::uint64_t chunks=1+(xyz.size()+(1U<<20)-1)/(1U<<20)+(data.size()+(1U<<20)-1)/(1U<<20);
    scaling::Profiler::instance().add_communication("worklet_return",send?chunks:0,send?0:chunks,send?bytes:0,send?0:bytes);
    if(send){task.reset();return;}
    task.reset(new TaskMesh(t));NewSubmesh(coarse,task->mesh);
    for(int i=1;i<=header[0];++i){int id=0;nglib::Ng_AddPoint(task->mesh,xyz.data()+3*static_cast<std::size_t>(i-1),id);if(id!=i)throw std::runtime_error("task point numbering mismatch");}
    for(std::size_t i=0;i<ne;++i) {
        for(int j=0;j<4;++j)if(data[5*i+j]<1 || data[5*i+j]>header[0])throw std::runtime_error("invalid task tet index");
        nglib::Ng_AddVolumeElement(task->mesh,nglib::NG_TET,data.data()+5*i,data[5*i+4]);
    }
    std::size_t pos=5*ne;
    for(int i=1;i<=header[2];++i,pos+=4)if(i>nglib::Ng_GetNFD(coarse)) {
        const int id=nglib::My_Ng_AddFaceDescriptor(task->mesh,data[pos],data[pos+1],data[pos+2],data[pos+3]);
        if(id!=i)throw std::runtime_error("task descriptor numbering mismatch");
    }
    auto index=[&](){int v=data.at(pos++);if(v<1 || v>header[0])throw std::runtime_error("invalid task vertex index");return v;};
    auto unpack_bary=[&](){Barycentric b{};for(int &v:b.gvrtx)v=data.at(pos++);for(short &v:b.coord){int x=data.at(pos++);if(x<0 || x>SHRT_MAX)throw std::runtime_error("invalid task barycentric coordinate");v=static_cast<short>(x);}return b;};
    for(std::size_t i=0;i<ng;++i){int global=data.at(pos++);if(!task->g2l.emplace(global,index()).second)throw std::runtime_error("duplicate task coarse vertex");}
    for(std::size_t i=0;i<nb;++i){const auto b=unpack_bary();if(!task->bary.emplace(b,index()).second)throw std::runtime_error("duplicate task barycentric vertex");}
    for(std::size_t i=0;i<ns;++i) {
        xdFace f{};for(int &v:f.lsvrtx)v=index();for(auto &b:f.barycv)b=unpack_bary();
        const int outw=data.at(pos++),fd=data.at(pos++);
        if(outw<SHRT_MIN || outw>SHRT_MAX || fd<1 || fd>header[2])throw std::runtime_error("invalid task surface descriptor");
        f.outw=static_cast<short>(outw);f.geoboundary=static_cast<short>(fd);
        task->faces.push_back(f);nglib::Ng_AddSurfaceElementwithIndex(task->mesh,nglib::NG_TRIG,f.lsvrtx,f.geoboundary);
    }
}
void append(TaskMesh &task,nglib::Ng_Mesh *merged,const std::map<FaceKey,xdMeshFaceInfo> &parents,
            const std::vector<int> &owner,int rank,int coarse_fd,
            std::map<int,int> &g2l,std::map<Barycentric,int,CompBarycentric> &bary,std::list<xdFace> &faces) {
    std::vector<int> local(nglib::Ng_GetNP(task.mesh)+1,0);
    std::map<int,Barycentric> reverse;for(const auto &b:task.bary)reverse.emplace(b.second,b.first);
    for(int i=1;i<=nglib::Ng_GetNP(task.mesh);++i) {
        const auto b=reverse.find(i);double x[3];nglib::Ng_GetPoint(task.mesh,i,x);
        if(b!=reverse.end()) {
            const auto found=bary.find(b->second);
            if(found!=bary.end()) {
                double y[3];nglib::Ng_GetPoint(merged,found->second,y);
                for(int j=0;j<3;++j)if(std::abs(x[j]-y[j])>1e-10*std::max({1.,std::abs(x[j]),std::abs(y[j])}))
                    throw std::runtime_error("inconsistent task interface coordinates");
                local[i]=found->second;continue;
            }
        }
        require_local_mesh_capacity(static_cast<GlobalCount>(nglib::Ng_GetNP(merged))+1,MPI_COMM_WORLD);
        nglib::Ng_AddPoint(merged,x,local[i]);
        if(b!=reverse.end())bary.emplace(b->second,local[i]);
    }
    for(const auto &v:task.g2l) {
        auto added=g2l.emplace(v.first,local[v.second]);
        if(!added.second && added.first->second!=local[v.second])throw std::runtime_error("duplicate coarse task vertex");
    }
    std::map<std::pair<int,int>,int> descriptors;
    for(const auto &original:task.faces) {
        const auto &parent=parents.at(key(original));const int other=parent.procids[1];
        if(other>=0 && owner[parent.procids[0]]==rank && owner[other]==rank) {
            if(parent.domainidx[0]==parent.domainidx[1])continue;
            if(task.task!=parent.procids[0])continue;
        }
        xdFace f=original;for(int &v:f.lsvrtx)v=local.at(v);
        if(f.geoboundary>coarse_fd) {
            const int outside=(other>=0 && owner[parent.procids[0]]==rank && owner[other]==rank)?parent.domainidx[1]:0;
            const auto descriptor_key=std::make_pair(f.geoboundary,outside);
            auto found=descriptors.find(descriptor_key);
            if(found==descriptors.end()) {
                int x[4];nglib::My_Ng_GetFaceDescriptor(task.mesh,f.geoboundary,x);
                const int next=nglib::Ng_GetNFD(merged)+1;
                if(next>SHRT_MAX)throw std::runtime_error("too many local task face descriptors");
                const int index=nglib::My_Ng_AddFaceDescriptor(merged,next,x[1],outside,x[3]);
                found=descriptors.emplace(descriptor_key,index).first;
            }
            f.geoboundary=static_cast<short>(found->second);
        }
        nglib::Ng_AddSurfaceElementwithIndex(merged,nglib::NG_TRIG,f.lsvrtx,f.geoboundary);faces.push_back(f);
    }
    require_local_mesh_capacity(static_cast<GlobalCount>(nglib::Ng_GetNE(merged))+nglib::Ng_GetNE(task.mesh),MPI_COMM_WORLD);
    for(int i=1;i<=nglib::Ng_GetNE(task.mesh);++i) {
        int v[4],domain;nglib::Ng_GetVolumeElement(task.mesh,i,v,domain);for(int &j:v)j=local.at(j);
        nglib::Ng_AddVolumeElement(merged,nglib::NG_TET,v,domain);
    }
}
}
double GenerateScheduledTasks(void *coarse_raw,void *merged_raw,int tasks,int levels,int maxbarycoord,
    std::map<int,xdMeshFaceInfo> &facemap,std::map<int,int> &g2l,
    std::map<Barycentric,int,CompBarycentric> &bary,std::list<xdFace> &faces) {
    using namespace mesh_research;
    auto *coarse=static_cast<nglib::Ng_Mesh *>(coarse_raw),*merged=static_cast<nglib::Ng_Mesh *>(merged_raw);
    int rank,p;MPI_Comm_rank(MPI_COMM_WORLD,&rank);MPI_Comm_size(MPI_COMM_WORLD,&p);
    auto &profile=scaling::Profiler::instance();const int ne=nglib::Ng_GetNE(coarse);
    const bool fixed=options().worklets();
    if(p<2 || (!fixed && (tasks<p-1 || tasks>ne)))protocol_error(MPI_COMM_WORLD,"任务数须在进程数减一与粗单元数之间");
    double local_mesh_seconds=0;
    WorkletFailure failure;failure.rank=rank;
    try {
        failure.stage="partition";
        std::vector<int> labels(ne),fixed_owner;
        {
            scaling::StageScope stage("metis_partition","compute");
            if(fixed) {
                idx_t *part=PartitionResearchMesh(coarse,p);
                std::vector<int> original(ne);for(int i=0;i<ne;++i)original[i]=static_cast<int>(part[i]);
                std::free(part);
                std::vector<std::vector<int>> adjacency(ne);std::map<int,int> first;
                bool update=true;
                for(int i=0;i<ne;++i) {
                    int fids[4],orient[4];nglib::My_Ng_GetElement_Faces(coarse,i+1,fids,orient,update);update=false;
                    for(int f:fids) {
                        auto a=first.emplace(f,i);
                        if(!a.second){adjacency[i].push_back(a.first->second);adjacency[a.first->second].push_back(i);}
                    }
                }
                for(auto &a:adjacency)std::sort(a.begin(),a.end());
                failure.stage="split_worklets";
                auto divided=split_worklets(original,adjacency,p,options().worklets_per_owner);
                labels=std::move(divided.labels);fixed_owner=std::move(divided.owners);tasks=static_cast<int>(fixed_owner.size());
                std::uint64_t signature=1469598103934665603ULL;
                for(int i=0;i<ne;++i) {
                    if(fixed_owner.at(labels[i])!=original[i])throw std::runtime_error("worklet changed original owner");
                    signature=(signature^static_cast<unsigned>(original[i]))*1099511628211ULL;
                }
                std::ostringstream sig;sig<<std::hex<<signature;profile.add_metadata("worklet_ownership_signature",sig.str());
                profile.add_metadata("mesh_tasks",std::to_string(tasks));
                profile.set_metric("logical_partition",rank);
            } else if(rank==0) {
                idx_t *part=PartitionMesh(coarse,tasks);
                for(int i=0;i<ne;++i)labels[i]=static_cast<int>(part[i]);
                std::free(part);
            }
            if(!fixed)check_mpi(MPI_Bcast(labels.data(),ne,MPI_INT,0,MPI_COMM_WORLD),MPI_COMM_WORLD);
        }
        std::vector<int> home(tasks),owner(tasks),executor(tasks),node(p),cell_counts(tasks);std::vector<double> weight(tasks,1.);
        failure.stage="node_topology";
        MPI_Comm shared;check_mpi(MPI_Comm_split_type(MPI_COMM_WORLD,MPI_COMM_TYPE_SHARED,rank,MPI_INFO_NULL,&shared),MPI_COMM_WORLD);
        int leader=rank;check_mpi(MPI_Allreduce(MPI_IN_PLACE,&leader,1,MPI_INT,MPI_MIN,shared),shared);
        check_mpi(MPI_Allgather(&leader,1,MPI_INT,node.data(),1,MPI_INT,MPI_COMM_WORLD),MPI_COMM_WORLD);
        check_mpi(MPI_Comm_free(&shared),MPI_COMM_WORLD);
        for(int label:labels) {
            if(label<0 || label>=tasks)throw std::runtime_error("invalid task label");
            ++cell_counts[label];
        }
        if(std::find(cell_counts.begin(),cell_counts.end(),0)!=cell_counts.end())
            throw std::runtime_error("empty closed task in METIS partition");
        for(int t=0;t<tasks;++t)home[t]=1+static_cast<int>(static_cast<long long>(t)*(p-1)/tasks);
        if(fixed)owner=fixed_owner;
        std::map<int,xdMeshFaceInfo> all;std::map<FaceKey,xdMeshFaceInfo> parents;
        std::vector<std::map<int,xdMeshFaceInfo>> task_faces(tasks);
        std::vector<std::map<int,int>> dependencies(tasks);
        {
            failure.stage="task_geometry";
            scaling::StageScope stage("task_geometry_setup","compute");bool update=true;
            for(int i=1;i<=ne;++i) {
                int fids[4],orient[4],v[4],domain;
                nglib::My_Ng_GetElement_Faces(coarse,i,fids,orient,update);update=false;
                nglib::Ng_GetVolumeElement(coarse,i,v,domain);
                const int t=labels[i-1];failure.task=t;
                failure.owner=fixed && t>=0 && t<tasks?owner[t]:-1;
                if(t<0 || t>=tasks)throw std::runtime_error("invalid task partition");
                for(int f=0;f<4;++f) {
                    fid_xdMeshFaceInfo record;ExtractSurfaceMesh(coarse,fids[f],t,v,&record,0,domain);
                    auto inserted=all.emplace(fids[f],record.mfi);
                    if(!inserted.second) {
                        auto &a=inserted.first->second;if(a.procids[1]!=-1)throw std::runtime_error("nonmanifold coarse task face");
                        a.procids[1]=t;a.domainidx[1]=domain;std::copy_n(record.mfi.svrtx[0],3,a.svrtx[1]);
                    }
                }
            }
            for(const auto &entry:all) {
                const auto &f=entry.second;int a=f.procids[0],b=f.procids[1];
                if(a==b && f.domainidx[0]==f.domainidx[1])continue;
                task_faces[a].insert(entry);if(!fixed)weight[a]+=1;
                if(b>=0 && b!=a){task_faces[b].insert(entry);if(!fixed)weight[b]+=1;dependencies[a][b]++;dependencies[b][a]++;}
                FaceKey k;std::copy_n(f.svrtx[0],3,k.begin());std::sort(k.begin(),k.end());parents.emplace(k,f);
            }
        }
        if(fixed)for(int t=0;t<tasks;++t)weight[t]=cell_counts[t];
        std::vector<std::unique_ptr<TaskMesh>> local;
        double kernel_total[3]={},detail_total[12]={},team_before[3]={},team_after[3]={};
        if(options().kernel_threads>0)nglib::Ng_GetVolumeTaskManagerStats(team_before);
        std::vector<std::uint64_t> hashes(tasks);std::vector<GlobalCount> counts(tasks);
        MPI_Comm queue;check_mpi(MPI_Comm_dup(MPI_COMM_WORLD,&queue),MPI_COMM_WORLD);
        constexpr int request_tag=10,reply_tag=11;
        if(rank==0) {
            failure.stage="scheduler";failure.task=-1;failure.owner=-1;
            scaling::StageScope service("task_scheduler_service","scheduling");
            std::unique_ptr<TaskSchedule> legacy;
            std::unique_ptr<WorkletSchedule> worklets;
            if(fixed)worklets.reset(new WorkletSchedule(owner,node,dependencies,weight));
            else legacy.reset(new TaskSchedule(home,node,dependencies,weight,options().task_cut_growth));
            int active=p-1,completed=0;std::vector<int> running(p,-1);
            while(active) {
                double done[6];MPI_Status status;
                check_mpi(MPI_Recv(done,6,MPI_DOUBLE,MPI_ANY_SOURCE,request_tag,queue,&status),queue);
                const int worker=status.MPI_SOURCE,t=static_cast<int>(done[0]);
                failure.task=t;failure.executor=worker;failure.owner=fixed && t>=0 && t<tasks?owner[t]:-1;
                if(t!=running[worker])throw std::runtime_error("task completion does not match assignment");
                if(t>=0) {
                    if(done[1]<0 || !std::isfinite(done[1]) || done[2]<=0)throw std::runtime_error("invalid completed task");
                    hashes[t]=(static_cast<std::uint64_t>(done[4])<<32)|static_cast<std::uint64_t>(done[5]);
                    counts[t]=static_cast<GlobalCount>(done[2]);++completed;
                    if(fixed)worklets->complete(t,worker,done[1]);
                }
                const int next=fixed?worklets->claim(worker,options().worklet_policy,MPI_Wtime()):legacy->claim(worker,options().balance());running[worker]=next;
                check_mpi(MPI_Send(&next,1,MPI_INT,worker,reply_tag,queue),queue);
                if(next<0)--active;
            }
            if(completed!=tasks || (fixed?!worklets->complete():!legacy->empty()))throw std::runtime_error("unfinished task queue");
            if(fixed)executor=worklets->executors();
            else {owner=legacy->owners();executor=owner;}
            long long cut=0;if(fixed)for(int t=0;t<tasks;++t)for(const auto &e:dependencies[t])
                if(e.first>t && node[owner[t]]!=node[owner[e.first]])cut+=e.second;
            profile.set_metric("task_cross_node_faces_before",fixed?cut:legacy->initial_cut());
            profile.set_metric("task_cross_node_faces_after",fixed?cut:legacy->cut());
            profile.set_metric("task_cross_node_faces_limit",fixed?cut:legacy->limit());
            int moved=0;for(int t=0;t<tasks;++t)moved+=node[executor[t]]!=node[fixed?owner[t]:home[t]];
            profile.set_metric("task_moved_between_nodes",moved);
            profile.add_communication("task_dispatch",tasks+p-1,tasks+p-1,
                static_cast<std::uint64_t>(tasks+p-1)*sizeof(int),static_cast<std::uint64_t>(tasks+p-1)*6*sizeof(double));
        } else {
            double done[6]={-1,0,0,0,0,0};int t;
            for(;;) {
                {
                    scaling::StageScope dispatch("task_dispatch","communication");
                    check_mpi(MPI_Send(done,6,MPI_DOUBLE,0,request_tag,queue),queue);
                    check_mpi(MPI_Recv(&t,1,MPI_INT,0,reply_tag,queue,MPI_STATUS_IGNORE),queue);
                }
                profile.add_communication("task_dispatch",1,1,6*sizeof(double),sizeof(int));
                if(t<0)break;
                failure.task=t;failure.owner=fixed?owner[t]:-1;failure.executor=rank;
                failure.status=-1;failure.final_illegal=-1;failure.stage="task_surface";
                const double start=MPI_Wtime();std::unique_ptr<TaskMesh> task(new TaskMesh(t));NewSubmesh(coarse,task->mesh);
                std::map<IntPair,int,IntPairCompare> edges;
                {scaling::StageScope stage("part_face_create","compute");PartFaceCreate(coarse,t,task_faces[t],maxbarycoord,task->mesh,task->g2l,task->bary,task->faces);}
                failure.stage="surface_refine";
                {scaling::StageScope stage("surface_refine","compute");Refine(task->mesh,levels,t,task->faces,task->bary,edges);}
                const double mesh_start=MPI_Wtime();
                failure.stage="volume_generation";
                {scaling::StageScope stage("local_volume_mesh","compute");nglib::Ng_Meshing_Parameters parameters;parameters.fineness=1;
                 double seconds[3]={},details[12]={};
                 const auto result=options().kernel_threads>0?nglib::Ng_GenerateVolumeMeshRepair(task->mesh,&parameters,options().kernel_threads,2,seconds,details):nglib::Ng_GenerateVolumeMesh(task->mesh,&parameters);
                 failure.status=static_cast<int>(result);failure.final_illegal=details[11];
                 for(int k=0;k<3;++k)kernel_total[k]+=seconds[k];
                 for(int k=0;k<12;++k)detail_total[k]+=details[k];
                 if(result!=nglib::NG_OK || details[11]!=0)throw std::runtime_error("封闭子域体网格生成或修复失败");}
                local_mesh_seconds+=MPI_Wtime()-mesh_start;
                failure.stage="task_fingerprint";
                const auto h=fixed?task_fingerprint(*task):fingerprint(task->mesh);
                done[0]=t;done[1]=MPI_Wtime()-start;done[2]=nglib::Ng_GetNE(task->mesh);done[3]=nglib::Ng_GetNP(task->mesh);
                done[4]=static_cast<double>(h>>32);done[5]=static_cast<double>(h&0xffffffffULL);
                local.push_back(std::move(task));
            }
        }
        if(options().kernel_threads>0) {
            nglib::Ng_GetVolumeTaskManagerStats(team_after);
            const char *team[]={"starts","start_seconds","stop_seconds"};
            const char *phase[]={"generation","repair","optimization"};
            const char *details[]={"delaunay_seconds","front_seconds","domain_repair_seconds","repair_mark_seconds","repair_split_seconds","repair_swap_seconds","repair_swap2_seconds","repair_rounds","repair_candidates_total","repair_candidates_active","repair_fallbacks","final_illegal"};
            for(int k=0;k<3;++k){profile.set_metric(std::string("kernel_team_")+team[k],team_after[k]-team_before[k]);profile.set_metric(std::string("kernel_")+phase[k]+"_seconds",kernel_total[k]);}
            for(int k=0;k<12;++k)profile.set_metric(std::string("kernel_")+details[k],detail_total[k]);
        }
        failure.stage="completion_metadata";failure.task=-1;failure.owner=-1;failure.executor=-1;
        {scaling::StageScope wait("task_completion_wait","synchronization");
         check_mpi(MPI_Bcast(owner.data(),tasks,MPI_INT,0,queue),queue);
         if(fixed)check_mpi(MPI_Bcast(executor.data(),tasks,MPI_INT,0,queue),queue);
         check_mpi(MPI_Bcast(hashes.data(),tasks,MPI_UINT64_T,0,queue),queue);
         check_mpi(MPI_Bcast(counts.data(),tasks,MPI_INT64_T,0,queue),queue);}
        profile.set_metric("tasks_completed",local.size());
        if(fixed) {
            scaling::StageScope stage("worklet_return","communication");
            std::vector<std::unique_ptr<TaskMesh>> by_id(tasks);
            for(auto &task:local)by_id[task->task]=std::move(task);
            local.clear();
            for(int t=0;t<tasks;++t) {
                failure.stage="worklet_return";failure.task=t;failure.owner=owner[t];failure.executor=executor[t];
                if(owner[t]!=fixed_owner[t])throw std::runtime_error("logical task owner changed");
                if(executor[t]!=owner[t])return_task(by_id[t],t,executor[t],owner[t],rank,coarse,queue);
                if(owner[t]==rank) {
                    failure.stage="return_verification";
                    if(!by_id[t] || task_fingerprint(*by_id[t])!=hashes[t] || nglib::Ng_GetNE(by_id[t]->mesh)!=counts[t])
                        throw std::runtime_error("returned task fingerprint/count mismatch");
                    local.push_back(std::move(by_id[t]));
                }
            }
            profile.set_metric("worklets_owned",local.size());
            profile.set_metric("worklet_ownership_errors",0);
            profile.set_metric("worklet_return_checked",local.size());
        }
        check_mpi(MPI_Comm_free(&queue),MPI_COMM_WORLD);
        profile.add_communication("task_metadata",rank==0?1:0,rank==0?0:1,
            rank==0?static_cast<std::uint64_t>(tasks)*(fixed?24:20)*(p-1):0,rank==0?0:static_cast<std::uint64_t>(tasks)*(fixed?24:20));
        std::uint64_t signature=1469598103934665603ULL;GlobalCount total=0;
        for(int t=0;t<tasks;++t){signature=(signature^hashes[t])*1099511628211ULL;signature=(signature^static_cast<std::uint64_t>(counts[t]))*1099511628211ULL;total+=counts[t];}
        std::ostringstream sig;sig<<std::hex<<signature;profile.add_metadata("task_mesh_signature",sig.str());
        profile.set_metric("task_generated_elements_global",static_cast<double>(total));
        profile.set_metric("task_count",tasks);
        std::vector<idx_t> physical(ne);for(int i=0;i<ne;++i)physical[i]=owner[labels[i]];
        failure.stage="owner_face_map";failure.task=-1;failure.owner=rank;failure.executor=-1;
        {scaling::StageScope stage("face_pipeline_total","algorithm");ExtractPartitionSurfaceMesh(coarse,physical.data(),facemap,nullptr);}
        profile.mark_elapsed("face_complete_elapsed");
        // 覆盖逐任务 PartFaceCreate 的最后一次局部计数，报告最终物理分区的面。
        std::uint64_t physical_faces=0,partition_faces=0;
        for(const auto &entry:facemap) {
            const auto &f=entry.second;
            if(f.procids[0]!=rank && f.procids[1]!=rank)continue;
            if(f.procids[1]<0)++physical_faces;
            else if(f.procids[0]!=f.procids[1])++partition_faces;
        }
        profile.set_metric("physical_boundary_faces",physical_faces);
        profile.set_metric("partition_boundary_faces",partition_faces);
        profile.set_metric("facemap_entries",facemap.size());
        {scaling::StageScope stage("task_merge","compute");
         std::sort(local.begin(),local.end(),[](const std::unique_ptr<TaskMesh> &a,const std::unique_ptr<TaskMesh> &b){return a->task<b->task;});
         for(auto &task:local){failure.stage="owner_merge";failure.task=task->task;failure.owner=rank;failure.executor=fixed?executor[task->task]:rank;
             append(*task,merged,parents,owner,rank,nglib::Ng_GetNFD(coarse),g2l,bary,faces);task.reset();}}
    } catch(const std::exception &e){failure.write(e.what());protocol_error(MPI_COMM_WORLD,e.what());}
      catch(...){failure.write("non-std exception");protocol_error(MPI_COMM_WORLD,"non-std exception in task pipeline");}
    return local_mesh_seconds;
}
