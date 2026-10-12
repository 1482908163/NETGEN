#pragma once
#include "ghost_vertex_dictionary.h"
#include "mpi_debug.h"
#include <map>
#include <climits>

namespace mesh_research { namespace ghost_transport {
using Clock=std::chrono::steady_clock;
inline double Elapsed(Clock::time_point start){return std::chrono::duration<double>(Clock::now()-start).count();}
struct NullScope {NullScope(const char*,const char*){}};
struct Stats {
    enum Field {calls,send_items,receive_items,raw_send_bytes,raw_receive_bytes,payload_send_bytes,payload_receive_bytes,
        metadata_send_bytes,metadata_receive_bytes,dictionary_send_peers,dictionary_receive_peers,literal_send_peers,literal_receive_peers,
        payload_send_messages,payload_receive_messages,metadata_send_messages,metadata_receive_messages,
        verify_calls,verified_items,verified_peers,mismatches,retained_wire_bytes_peak,
        prepare_seconds,metadata_seconds,exchange_seconds,decode_seconds,release_seconds,total_seconds,reference_seconds,full_seconds,count};
    std::array<double,count> values{};
};
struct Chunk {std::size_t offset;int count;};
inline std::vector<Chunk> Chunks(std::uint64_t total,int limit=INT_MAX){
    if(limit<1 || total>std::numeric_limits<std::size_t>::max())throw std::overflow_error("ghost transport chunk limit");
    const auto n=total/std::uint64_t(limit)+(total%std::uint64_t(limit)!=0);
    if(n>INT_MAX)throw std::overflow_error("too many ghost payload requests");
    std::vector<Chunk> result;result.reserve(static_cast<std::size_t>(n));
    for(std::uint64_t off=0;off<total;){const int count=static_cast<int>(std::min<std::uint64_t>(limit,total-off));result.push_back({static_cast<std::size_t>(off),count});off+=count;}
    return result;
}
using Header=std::array<std::int64_t,3>; // format, original record count, actual payload bytes
inline void CheckHeader(const Header &h,int expected){
    if(expected<=0 || h[1]!=expected || (h[0]!=0 && h[0]!=1) || h[2]<0)throw std::runtime_error("invalid ghost transport header");
    const std::uint64_t raw=140ull*expected,bytes=static_cast<std::uint64_t>(h[2]);
    if((h[0]==0 && bytes!=raw) || (h[0]==1 && (bytes>=raw || bytes<16ull+28ull*expected+32 || bytes>16ull+156ull*expected)))
        throw std::runtime_error("ghost transport payload length mismatch");
    if(bytes>std::numeric_limits<std::size_t>::max())throw std::overflow_error("ghost transport host size overflow");
}
inline void Wait(MPI_Comm comm,std::vector<MPI_Request> &requests,const std::vector<std::pair<MPI_Datatype,int>> &expected){
    if(requests.size()!=expected.size() || requests.size()>INT_MAX)throw std::overflow_error("ghost transport request count");
    if(requests.empty())return;
    std::vector<MPI_Status> statuses;
    try{statuses.resize(requests.size());}catch(const std::bad_alloc &){netgen_mpi_check(comm,MPI_ERR_OTHER,"ghost transport status allocation");}
    netgen_mpi_check(comm,MPI_Waitall(static_cast<int>(requests.size()),requests.data(),statuses.data()),"ghost transport/Waitall");
    for(std::size_t i=0;i<requests.size();++i)if(expected[i].second>=0){
        int count=0;netgen_mpi_check(comm,MPI_Get_count(&statuses[i],expected[i].first,&count),"ghost transport/Get_count");
        if(count!=expected[i].second)throw std::runtime_error("ghost transport truncated MPI message");
    }
}
struct Pending {bool sending;void *data;int count;MPI_Datatype type;int peer,tag;};
inline void Execute(MPI_Comm comm,const std::vector<Pending> &plans){
    if(plans.size()>INT_MAX)throw std::overflow_error("ghost transport request count");
    // Allocate every descriptor before posting: allocation failure must not
    // unwind storage still referenced by an outstanding MPI request.
    std::vector<MPI_Request> requests(plans.size(),MPI_REQUEST_NULL);
    std::vector<std::pair<MPI_Datatype,int>> expected;expected.reserve(plans.size());
    for(const auto &p:plans)expected.push_back({p.type,p.sending?-1:p.count});
    for(std::size_t i=0;i<plans.size();++i){const auto &p=plans[i];
        const auto rc=p.sending?MPI_Isend(p.data,p.count,p.type,p.peer,p.tag,comm,&requests[i]):MPI_Irecv(p.data,p.count,p.type,p.peer,p.tag,comm,&requests[i]);
        netgen_mpi_check(comm,rc,p.sending?"ghost transport/Isend":"ghost transport/Irecv");
    }
    Wait(comm,requests,expected);
}
template<class Packet,class Scope=NullScope> Stats Exchange(MPI_Comm comm,MPI_Datatype record_type,
    const std::map<int,std::vector<Packet>> &send,const std::vector<int> &send_counts,
    const std::vector<int> &receive_counts,std::vector<std::vector<Packet>> &receive,int chunk_limit=INT_MAX){
    Stats stats;auto &v=stats.values;v[Stats::calls]=1;const auto total_start=Clock::now();
    const auto n=send_counts.size();int size=0,rank=0,record_bytes=0;
    netgen_mpi_check(comm,MPI_Comm_size(comm,&size),"ghost transport/size");
    netgen_mpi_check(comm,MPI_Comm_rank(comm,&rank),"ghost transport/rank");
    netgen_mpi_check(comm,MPI_Type_size(record_type,&record_bytes),"ghost transport/type size");
    if(n!=static_cast<std::size_t>(size) || receive_counts.size()!=n || receive.size()!=n || record_bytes!=140 || chunk_limit<1)
        throw std::logic_error("ghost transport input schema mismatch");
    for(const auto &entry:send)if(entry.first<0 || entry.first>=size || entry.first==rank || entry.second.size()!=static_cast<std::size_t>(send_counts[entry.first]))
        throw std::logic_error("ghost transport send map mismatch");
    std::vector<Header> sh(n),rh(n);std::vector<ghost_dictionary::Bytes> sb(n),rb(n);
    std::vector<Pending> plans;
    auto post=[&](bool sending,void *data,int count,MPI_Datatype type,int peer,int tag){
        if(plans.size()>=INT_MAX)throw std::overflow_error("ghost transport request count");
        plans.push_back({sending,data,count,type,peer,tag});
    };
    auto start=Clock::now();
    {Scope scope("ghost_dictionary_prepare","compute");
        for(int peer=0;peer<size;++peer){
            if(send_counts[peer]<0 || receive_counts[peer]<0 || (peer==rank && (send_counts[peer] || receive_counts[peer])))throw std::logic_error("invalid ghost transport counts");
            v[Stats::send_items]+=send_counts[peer];v[Stats::receive_items]+=receive_counts[peer];
            if(send_counts[peer]){
                const auto it=send.find(peer);if(it==send.end())throw std::logic_error("missing ghost transport send buffer");
                ghost_dictionary::Encoding encoded;bool representable=true;
                try{encoded=ghost_dictionary::Encode(it->second);}
                catch(const std::overflow_error &){representable=false;}
                catch(const std::bad_alloc &){representable=false;}
                const auto raw=140ull*send_counts[peer];const bool dictionary=representable && encoded.bytes.size()<raw;
                sh[peer]={{dictionary?1:0,send_counts[peer],static_cast<std::int64_t>(dictionary?encoded.bytes.size():raw)}};
                if(dictionary){sb[peer]=std::move(encoded.bytes);++v[Stats::dictionary_send_peers];}
                else ++v[Stats::literal_send_peers];
                v[Stats::payload_send_bytes]+=sh[peer][2];v[Stats::metadata_send_bytes]+=24;++v[Stats::metadata_send_messages];
            }
        }
        v[Stats::raw_send_bytes]=140*v[Stats::send_items];v[Stats::raw_receive_bytes]=140*v[Stats::receive_items];
    }
    v[Stats::prepare_seconds]=Elapsed(start);start=Clock::now();
    {Scope scope("ghost_dictionary_metadata","communication");
        for(int peer=0;peer<size;++peer)if(receive_counts[peer])post(false,rh[peer].data(),3,MPI_INT64_T,peer,4921);
        for(int peer=0;peer<size;++peer)if(send_counts[peer])post(true,sh[peer].data(),3,MPI_INT64_T,peer,4921);
        Execute(comm,plans);plans.clear();
    }
    v[Stats::metadata_seconds]=Elapsed(start);start=Clock::now();
    {Scope scope("volume_payload_exchange","communication");
        for(int peer=0;peer<size;++peer)if(receive_counts[peer]){
            CheckHeader(rh[peer],receive_counts[peer]);const bool dictionary=rh[peer][0]==1;
            v[Stats::payload_receive_bytes]+=rh[peer][2];v[Stats::metadata_receive_bytes]+=24;++v[Stats::metadata_receive_messages];
            ++v[dictionary?Stats::dictionary_receive_peers:Stats::literal_receive_peers];
            if(dictionary)rb[peer].resize(static_cast<std::size_t>(rh[peer][2]));else receive[peer].resize(receive_counts[peer]);
            for(const auto c:Chunks(dictionary?rh[peer][2]:receive_counts[peer],chunk_limit)){
                post(false,dictionary?static_cast<void*>(rb[peer].data()+c.offset):static_cast<void*>(receive[peer].data()+c.offset),c.count,dictionary?MPI_BYTE:record_type,peer,4922);
                ++v[Stats::payload_receive_messages];
            }
        }
        for(int peer=0;peer<size;++peer)if(send_counts[peer]){
            const bool dictionary=sh[peer][0]==1;
            for(const auto c:Chunks(dictionary?sh[peer][2]:send_counts[peer],chunk_limit)){
                post(true,dictionary?static_cast<void*>(sb[peer].data()+c.offset):static_cast<void*>(const_cast<Packet*>(send.at(peer).data()+c.offset)),c.count,dictionary?MPI_BYTE:record_type,peer,4922);
                ++v[Stats::payload_send_messages];
            }
        }
        Execute(comm,plans);
    }
    v[Stats::exchange_seconds]=Elapsed(start);start=Clock::now();
    {Scope scope("ghost_dictionary_decode","compute");
        std::uint64_t retained=0;for(const auto &b:sb)retained+=b.capacity();for(const auto &b:rb)retained+=b.capacity();v[Stats::retained_wire_bytes_peak]=retained;
        for(int peer=0;peer<size;++peer)if(receive_counts[peer] && rh[peer][0]==1){
            auto decoded=ghost_dictionary::Decode<Packet>(rb[peer]);
            if(decoded.size()!=static_cast<std::size_t>(receive_counts[peer]))throw std::runtime_error("ghost dictionary decoded record count");
            receive[peer]=std::move(decoded);
        }
    }
    v[Stats::decode_seconds]=Elapsed(start);start=Clock::now();
    {Scope scope("ghost_dictionary_release","compute");
        decltype(sb)().swap(sb);decltype(rb)().swap(rb);decltype(sh)().swap(sh);decltype(rh)().swap(rh);
        decltype(plans)().swap(plans);
    }
    v[Stats::release_seconds]=Elapsed(start);v[Stats::total_seconds]=Elapsed(total_start);return stats;
}
template<class Packet,class Scope=NullScope> void Verify(MPI_Comm comm,MPI_Datatype type,
    const std::map<int,std::vector<Packet>> &send,const std::vector<int> &send_counts,const std::vector<int> &receive_counts,
    const std::vector<std::vector<Packet>> &receive,Stats &stats,int chunk_limit=INT_MAX){
    const auto start=Clock::now();
    {Scope scope("ghost_dictionary_reference","validation");
        std::vector<std::vector<Packet>> reference(receive_counts.size());std::vector<Pending> plans;
        for(std::size_t peer=0;peer<receive_counts.size();++peer)if(receive_counts[peer]){
            reference[peer].resize(receive_counts[peer]);
            for(auto c:Chunks(receive_counts[peer],chunk_limit))plans.push_back({false,reference[peer].data()+c.offset,c.count,type,static_cast<int>(peer),4923});
        }
        for(std::size_t peer=0;peer<send_counts.size();++peer)if(send_counts[peer]){
            for(auto c:Chunks(send_counts[peer],chunk_limit))plans.push_back({true,const_cast<Packet*>(send.at(static_cast<int>(peer)).data()+c.offset),c.count,type,static_cast<int>(peer),4923});
        }
        Execute(comm,plans);++stats.values[Stats::verify_calls];
        for(std::size_t peer=0;peer<reference.size();++peer){
            if(!ghost_dictionary::Equal(reference[peer],receive[peer])){++stats.values[Stats::mismatches];throw std::runtime_error("ghost transport reference mismatch");}
            stats.values[Stats::verified_items]+=reference[peer].size();if(!reference[peer].empty())++stats.values[Stats::verified_peers];
        }
    }
    stats.values[Stats::reference_seconds]=Elapsed(start);
}
} }
