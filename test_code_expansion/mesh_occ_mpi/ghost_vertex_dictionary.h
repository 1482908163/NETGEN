#pragma once
#include <array>
#include <algorithm>
#include <type_traits>
#include <utility>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <unordered_map>
#include <vector>

namespace mesh_research {
// A local, transport-independent codec. All IDs and coordinate bit patterns
// are retained. The MPI path is deliberately untouched by this feasibility audit.
namespace ghost_dictionary {
constexpr std::uint32_t magic=0x31445647u,version=1;
constexpr std::uint64_t header_bytes=16,vertex_bytes=32,element_bytes=28,literal_bytes=140;
using Bytes=std::vector<unsigned char>;
using Key=std::array<std::uint64_t,4>;
struct Hash {
    std::size_t operator()(const Key &key)const noexcept {
        std::uint64_t h=0xcbf29ce484222325ull;
        for(auto value:key){h^=value;h*=0x100000001b3ull;h^=h>>32;}
        return static_cast<std::size_t>(h);
    }
};
template<class T> std::uint64_t Bits64(const T &value){
    static_assert(sizeof(T)==8,"dictionary field must have 64 bits");
    std::uint64_t bits;std::memcpy(&bits,&value,8);return bits;
}
inline void Put(Bytes &out,std::uint64_t value,unsigned width){
    for(unsigned k=0;k<width;++k){out.push_back(static_cast<unsigned char>(value&255));value>>=8;}
}
struct Reader {
    const Bytes &bytes;std::size_t pos=0;
    std::uint64_t Get(unsigned width){
        if(width>bytes.size()-pos)throw std::runtime_error("truncated ghost dictionary");
        std::uint64_t value=0;for(unsigned k=0;k<width;++k)value|=std::uint64_t(bytes[pos++])<<(8*k);
        return value;
    }
};
struct Encoding {Bytes bytes;std::uint32_t vertices=0,elements=0;};
template<class Packet> Encoding Encode(const std::vector<Packet> &packets){
    static_assert(sizeof(double)==8 && std::numeric_limits<double>::is_iec559,"IEEE binary64 required");
    if(packets.size()>std::numeric_limits<std::uint32_t>::max())throw std::overflow_error("ghost dictionary element count");
    std::unordered_map<Key,std::uint32_t,Hash> lookup;
    std::vector<Key> vertices;std::vector<std::array<std::uint32_t,4>> connectivity;
    connectivity.reserve(packets.size());
    for(const auto &packet:packets){
        std::array<std::uint32_t,4> indices{};
        for(int k=0;k<4;++k){
            const Key key{{Bits64(packet.Pindex[k]),Bits64(packet.Vertexs[k].xyz[0]),Bits64(packet.Vertexs[k].xyz[1]),Bits64(packet.Vertexs[k].xyz[2])}};
            auto found=lookup.find(key);
            if(found==lookup.end()){
                if(vertices.size()>=std::numeric_limits<std::uint32_t>::max())throw std::overflow_error("ghost dictionary vertex count");
                const auto id=static_cast<std::uint32_t>(vertices.size());vertices.push_back(key);lookup.emplace(key,id);indices[k]=id;
            }else indices[k]=found->second;
        }
        connectivity.push_back(indices);
    }
    Encoding result;result.vertices=static_cast<std::uint32_t>(vertices.size());result.elements=static_cast<std::uint32_t>(packets.size());
    const std::uint64_t size=header_bytes+vertex_bytes*result.vertices+element_bytes*result.elements;
    if(size>std::numeric_limits<std::size_t>::max())throw std::overflow_error("ghost dictionary byte count");
    result.bytes.reserve(static_cast<std::size_t>(size));
    Put(result.bytes,magic,4);Put(result.bytes,version,4);Put(result.bytes,result.vertices,4);Put(result.bytes,result.elements,4);
    for(const auto &key:vertices)for(auto value:key)Put(result.bytes,value,8);
    for(std::size_t i=0;i<packets.size();++i){
        const auto &packet=packets[i];static_assert(sizeof(packet.domidx)==4,"32-bit domains required");
        Put(result.bytes,Bits64(packet.gid),8);std::uint32_t domain;std::memcpy(&domain,&packet.domidx,4);Put(result.bytes,domain,4);
        for(auto index:connectivity[i])Put(result.bytes,index,4);
    }
    return result;
}
template<class Packet> std::vector<Packet> Decode(const Bytes &bytes){
    Reader input{bytes};
    if(input.Get(4)!=magic || input.Get(4)!=version)throw std::runtime_error("unknown ghost dictionary format");
    const auto vertices=static_cast<std::uint32_t>(input.Get(4)),elements=static_cast<std::uint32_t>(input.Get(4));
    const std::uint64_t required=header_bytes+vertex_bytes*vertices+element_bytes*elements;
    if(std::uint64_t(vertices)>4ull*elements || required!=bytes.size())throw std::runtime_error("ghost dictionary size mismatch");
    std::vector<Key> dictionary(vertices);
    for(auto &key:dictionary)for(auto &value:key)value=input.Get(8);
    std::vector<Packet> packets(elements);
    for(auto &packet:packets){
        static_assert(sizeof(packet.gid)==8 && sizeof(packet.domidx)==4 && sizeof(packet.Pindex[0])==8,"dictionary integer widths");
        const auto gid=input.Get(8);const auto domain=static_cast<std::uint32_t>(input.Get(4));
        std::memcpy(&packet.gid,&gid,8);std::memcpy(&packet.domidx,&domain,4);
        for(int k=0;k<4;++k){
            const auto index=static_cast<std::uint32_t>(input.Get(4));
            if(index>=vertices)throw std::runtime_error("ghost dictionary index out of range");
            const auto &key=dictionary[index];std::memcpy(&packet.Pindex[k],&key[0],8);
            for(int d=0;d<3;++d)std::memcpy(&packet.Vertexs[k].xyz[d],&key[d+1],8);
        }
    }
    return packets;
}
template<class Packet> bool Equal(const std::vector<Packet> &a,const std::vector<Packet> &b){
    if(a.size()!=b.size())return false;
    for(std::size_t i=0;i<a.size();++i){
        if(a[i].gid!=b[i].gid || a[i].domidx!=b[i].domidx)return false;
        for(int k=0;k<4;++k)if(a[i].Pindex[k]!=b[i].Pindex[k] ||
            std::memcmp(a[i].Vertexs[k].xyz,b[i].Vertexs[k].xyz,3*sizeof(double)))return false;
    }
    return true;
}
struct Audit {
    enum Field {calls,peers,items,unique_vertices,raw_bytes,dictionary_bytes,selected_bytes,
        profitable_peers,bypass_peers,verified_items,verified_peers,mismatches,
        encode_seconds,decode_seconds,max_peer_raw_bytes,max_peer_dictionary_bytes,
        max_peer_selected_bytes,max_peer_encode_seconds,max_peer_decode_seconds,lifecycle_seconds,max_peer_lifecycle_seconds,count};
    std::array<double,count> values{};
    template<class Packets> void Run(const Packets &packets,int mpi_record_bytes){
        if(values[calls])throw std::logic_error("ghost codec audit repeated");
        if(mpi_record_bytes!=int(literal_bytes))throw std::logic_error("ghost codec MPI record schema mismatch");
        values[calls]=1;
        for(const auto &entry:packets){
            const auto &original=entry.second;if(original.empty())continue;
            ++values[peers];values[items]+=original.size();
            const auto peer_start=std::chrono::steady_clock::now();
            {
            auto start=std::chrono::steady_clock::now();auto encoded=Encode(original);
            const double encode=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
            start=std::chrono::steady_clock::now();
            using Packet=typename std::decay<decltype(original)>::type::value_type;
            auto decoded=Decode<Packet>(encoded.bytes);
            const double decode=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
            if(!Equal(original,decoded)){++values[mismatches];throw std::runtime_error("ghost codec roundtrip mismatch");}
            values[verified_items]+=original.size();++values[verified_peers];values[unique_vertices]+=encoded.vertices;
            const double raw=double(original.size())*literal_bytes,dict=encoded.bytes.size(),selected=std::min(raw,dict);
            values[raw_bytes]+=raw;values[dictionary_bytes]+=dict;values[selected_bytes]+=selected;
            ++values[dict<raw?profitable_peers:bypass_peers];values[encode_seconds]+=encode;values[decode_seconds]+=decode;
            for(auto pair:{std::pair<int,double>{max_peer_raw_bytes,raw},{max_peer_dictionary_bytes,dict},
                {max_peer_selected_bytes,selected},{max_peer_encode_seconds,encode},{max_peer_decode_seconds,decode}})
                values[pair.first]=std::max(values[pair.first],pair.second);
            } // release encoded/decoded buffers before recording full peer cost
            const double total=std::chrono::duration<double>(std::chrono::steady_clock::now()-peer_start).count();
            values[lifecycle_seconds]+=total;values[max_peer_lifecycle_seconds]=std::max(values[max_peer_lifecycle_seconds],total);
        }
    }
};
} // namespace ghost_dictionary
} // namespace mesh_research
