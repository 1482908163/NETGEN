#pragma once
#include <array>
#include <cstring>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <vector>
#include "mpi_debug.h"

namespace mesh_research {

// Geometry and recipient selection depend only on the fixed local mesh and
// shared-vertex adjacency. IDs are bound later, after their exchange completes.
template<class Payload> class GhostExchangePlan {
public:
    struct Reference {std::array<int,4> vertices;int element;};
    std::map<int,std::vector<Payload>> packets;
    std::map<int,std::vector<Reference>> references;
    int source_points=0,source_elements=0;
    std::size_t selected_elements=0,send_elements=0;
    bool built=false,bound=false;

    template<class Access,class Adjacency>
    void Build(const Access &mesh,const Adjacency &adj,int rank,int ranks) {
        if(built)throw std::logic_error("ghost plan built twice");
        source_points=mesh.np();source_elements=mesh.ne();
        if(source_points<0 || source_elements<0)throw std::logic_error("invalid local mesh size");
        for(int e=1;e<=source_elements;++e) {
            Reference ref;ref.element=e;int domain;
            mesh.tetrahedron(e,ref.vertices.data(),domain);
            std::map<int,int> recipients;
            for(int v:ref.vertices) {
                if(v<1 || v>source_points)throw std::logic_error("invalid ghost vertex reference");
                const auto found=adj.find(v);
                if(found!=adj.end())for(int peer:found->second)++recipients[peer];
            }
            bool selected=false;Payload packet{};
            for(const auto &entry:recipients)if(entry.second>=3) {
                const int peer=entry.first;
                if(peer<0 || peer>=ranks || peer==rank)throw std::logic_error("invalid ghost recipient");
                if(!selected) {
                    packet.domidx=domain;
                    for(int k=0;k<4;++k)mesh.point(ref.vertices[k],packet.Vertexs[k].xyz);
                    ++selected_elements;selected=true;
                }
                if(packets[peer].size()>=static_cast<std::size_t>(std::numeric_limits<int>::max()))
                    throw std::overflow_error("ghost MPI count overflow");
                packets[peer].push_back(packet);references[peer].push_back(ref);++send_elements;
            }
        }
        built=true;
    }

    template<class Access,class VertexIds,class ElementIds>
    void Bind(const Access &mesh,const VertexIds &vertices,const ElementIds &elements) {
        if(!built || bound || mesh.np()!=source_points || mesh.ne()!=source_elements)
            throw std::logic_error("ghost binding lifecycle or mesh changed");
        for(auto &entry:packets) {
            const auto &refs=references.at(entry.first);
            if(refs.size()!=entry.second.size())throw std::logic_error("ghost reference coverage");
            for(std::size_t i=0;i<refs.size();++i) {
                auto &packet=entry.second[i];const auto &ref=refs[i];
                packet.gid=elements[ref.element];
                if(packet.gid<=0)throw std::logic_error("unresolved ghost element ID");
                for(int k=0;k<4;++k) {
                    packet.Pindex[k]=vertices[ref.vertices[k]];
                    if(packet.Pindex[k]<=0)throw std::logic_error("unresolved ghost vertex ID");
                }
            }
        }
        bound=true;
    }

    // Original serial packing, independently reconstructed only for warmup.
    template<class Access,class Adjacency,class VertexIds,class ElementIds>
    std::size_t Verify(const Access &mesh,const Adjacency &adj,
        const VertexIds &vertices,const ElementIds &elements) const {
        if(!bound)throw std::logic_error("unbound ghost verification");
        std::map<int,std::vector<Payload>> original;
        for(int e=1;e<=mesh.ne();++e) {
            int entity[4],domain;mesh.tetrahedron(e,entity,domain);
            std::map<int,int> counts;std::set<int> peers;
            for(int k=0;k<4;++k) {
                auto found=adj.find(entity[k]);
                if(found!=adj.end())for(int peer:found->second)++counts[peer];
            }
            for(auto count:counts)if(count.second>=3)peers.insert(count.first);
            if(peers.empty())continue;
            Payload packet{};packet.domidx=domain;packet.gid=elements[e];
            for(int k=0;k<4;++k) {
                mesh.point(entity[k],packet.Vertexs[k].xyz);packet.Pindex[k]=vertices[entity[k]];
            }
            for(int peer:peers)original[peer].push_back(packet);
        }
        if(original.size()!=packets.size())throw std::logic_error("ghost oracle peer mismatch");
        std::size_t checked=0;
        for(const auto &entry:original) {
            const auto found=packets.find(entry.first);
            if(found==packets.end() || found->second.size()!=entry.second.size())
                throw std::logic_error("ghost oracle count mismatch");
            for(std::size_t i=0;i<entry.second.size();++i) {
                const auto &a=entry.second[i],&b=found->second[i];
                if(a.gid!=b.gid || a.domidx!=b.domidx)throw std::logic_error("ghost oracle element mismatch");
                for(int k=0;k<4;++k)
                    if(a.Pindex[k]!=b.Pindex[k] || std::memcmp(a.Vertexs[k].xyz,b.Vertexs[k].xyz,3*sizeof(double)))
                        throw std::logic_error("ghost oracle vertex mismatch");
                ++checked;
            }
        }
        return checked;
    }
    std::size_t ReferenceBytes() const {return send_elements*sizeof(Reference);}
};

// Owns the count buffers while MPI holds their addresses. Never move/copy this
// object between Begin and Wait. Zero-payload peers still exchange one count.
class GhostCountExchange {
    MPI_Comm comm=MPI_COMM_NULL;
    std::vector<MPI_Request> requests;
    bool active=false,started=false,finished=false;
public:
    std::vector<int> send,receive;
    std::size_t peers=0;
    GhostCountExchange()=default;
    GhostCountExchange(const GhostCountExchange&)=delete;
    GhostCountExchange&operator=(const GhostCountExchange&)=delete;
    ~GhostCountExchange() {
        if(active)netgen_mpi_check(comm,MPI_ERR_OTHER,"unfinished ghost count exchange");
    }
    void Begin(MPI_Comm communicator,int rank,int ranks,const std::set<int> &neighbors,
        const std::map<int,int> &counts) {
        if(started)throw std::logic_error("ghost counts posted twice");
        if(ranks<1 || rank<0 || rank>=ranks || neighbors.size()>static_cast<std::size_t>(std::numeric_limits<int>::max()/2))
            throw std::logic_error("ghost count communicator or request overflow");
        for(int peer:neighbors)if(peer<0 || peer>=ranks || peer==rank)
            throw std::logic_error("invalid count neighbor");
        for(auto entry:counts)if(!neighbors.count(entry.first) || entry.second<0)
            throw std::logic_error("ghost count outside neighbor graph");
        comm=communicator;peers=neighbors.size();send.assign(ranks,0);receive.assign(ranks,0);
        for(auto entry:counts)send[entry.first]=entry.second;
        requests.assign(2*peers,MPI_REQUEST_NULL);started=true;active=!requests.empty();
        std::size_t q=0;
        for(int peer:neighbors)
            netgen_mpi_check(comm,MPI_Irecv(&receive[peer],1,MPI_INT,peer,4821,comm,&requests[q++]),"ghost_count/Irecv");
        for(int peer:neighbors)
            netgen_mpi_check(comm,MPI_Isend(&send[peer],1,MPI_INT,peer,4821,comm,&requests[q++]),"ghost_count/Isend");
    }
    void Wait() {
        if(!started || finished)throw std::logic_error("ghost count wait lifecycle");
        if(active)netgen_mpi_check(comm,MPI_Waitall(static_cast<int>(requests.size()),requests.data(),MPI_STATUSES_IGNORE),"ghost_count/Waitall");
        active=false;finished=true;
        for(int count:receive)if(count<0)netgen_mpi_check(comm,MPI_ERR_COUNT,"negative ghost count");
    }
};

template<class Payload> struct GhostExchangeContext {
    GhostExchangePlan<Payload> plan;
    GhostCountExchange counts;
    const bool overlap,verify;
    bool prepared=false;
    std::set<int> neighbors;
    explicit GhostExchangeContext(bool overlap_,bool verify_):overlap(overlap_),verify(verify_){}
};

// A fixed vertex tag is disjoint from count tag 4821, even for rank 4821.
// Sender-rank tags cannot be retained once these protocols overlap.
template<class Payload>
std::vector<MPI_Request> PostKnownVertices(MPI_Comm comm,MPI_Datatype type,int /*rank*/,
    int sends,int receives,const int *dest,const int *src,const int *send_count,
    const int *receive_count,Payload *const *send_data,Payload *const *receive_data) {
    if(sends<0 || receives<0 || sends>std::numeric_limits<int>::max()-receives)
        throw std::logic_error("vertex request count overflow");
    std::vector<MPI_Request> requests(sends+receives,MPI_REQUEST_NULL);
    for(int i=0;i<receives;++i)
        netgen_mpi_check(comm,MPI_Irecv(receive_data[i],receive_count[i],type,src[i],3818,comm,&requests[i]),"ghost_vertex/Irecv");
    for(int i=0;i<sends;++i)
        netgen_mpi_check(comm,MPI_Isend(send_data[i],send_count[i],type,dest[i],3818,comm,&requests[receives+i]),"ghost_vertex/Isend");
    return requests;
}
inline void WaitKnownVertices(MPI_Comm comm,std::vector<MPI_Request> &requests) {
    if(!requests.empty())netgen_mpi_check(comm,MPI_Waitall(static_cast<int>(requests.size()),requests.data(),MPI_STATUSES_IGNORE),"ghost_vertex/Waitall");
}
} // namespace mesh_research
