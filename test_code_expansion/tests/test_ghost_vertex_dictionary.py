#!/usr/bin/env python3
"""Real record fields and codec, including malformed lengths and exact float bits."""
from pathlib import Path
import os,subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1]
s=(ROOT/'mesh_occ_mpi/parallelMeshData.h').read_text()
vertex=s[s.index('typedef struct xdVertex {'):s.index('}xdVertex;')+len('}xdVertex;')]
packet=s[s.index('typedef struct xdVElement {'):s.index('}xdVElement;')+len('}xdVElement;')]
cpp='''#include "ghost_vertex_dictionary.h"
#include <cassert>
#include <map>
#include <random>
#include <iostream>
using GlobalId=std::int64_t;
'''+vertex+packet+r'''
using namespace mesh_research::ghost_dictionary;
int main(){
 std::mt19937_64 random(20261012);std::size_t tested=0;
 for(int trial=0;trial<2000;++trial){
   std::vector<xdVElement> a(trial%127);std::vector<Key> points(1+trial%35);
   for(std::size_t v=0;v<points.size();++v){
     points[v][0]=(1ull<<50)+v;for(int d=1;d<=3;++d)points[v][d]=random();
   }
   if(points.size()>1){points[1][0]=points[0][0];points[1][1]=points[0][1]^0x8000000000000000ull;}
   for(std::size_t e=0;e<a.size();++e){
     a[e].gid=std::numeric_limits<std::int64_t>::max()-static_cast<std::int64_t>(e);
     a[e].domidx=e%2?std::numeric_limits<std::int32_t>::min():std::numeric_limits<std::int32_t>::max();
     for(int k=0;k<4;++k){const auto &key=points[random()%points.size()];std::memcpy(&a[e].Pindex[k],&key[0],8);for(int d=0;d<3;++d)std::memcpy(&a[e].Vertexs[k].xyz[d],&key[d+1],8);}
   }
   auto encoded=Encode(a);auto b=Decode<xdVElement>(encoded.bytes);assert(Equal(a,b));
   assert(encoded.bytes.size()==16+32ull*encoded.vertices+28ull*a.size());
   assert(encoded.bytes[0]==0x47 && encoded.bytes[1]==0x56 && encoded.bytes[2]==0x44 && encoded.bytes[3]==0x31);++tested;
   if(trial<8){
     for(std::size_t n=0;n<encoded.bytes.size();++n){auto bad=encoded.bytes;bad.resize(n);try{Decode<xdVElement>(bad);assert(false);}catch(const std::runtime_error&){} }
     for(int offset:{0,4,8,12}){auto bad=encoded.bytes;for(int k=0;k<4;++k)bad[offset+k]=255;try{Decode<xdVElement>(bad);assert(false);}catch(const std::runtime_error&){} }
     auto bad=encoded.bytes;bad.push_back(0);try{Decode<xdVElement>(bad);assert(false);}catch(const std::runtime_error&){}
     if(!a.empty()){bad=encoded.bytes;auto at=16+32ull*encoded.vertices+12;for(int k=0;k<4;++k)bad[at+k]=255;try{Decode<xdVElement>(bad);assert(false);}catch(const std::runtime_error&){} }
   }
 }
 xdVElement one{};one.gid=(1ll<<48)+19;one.domidx=7;
 for(int k=0;k<4;++k){one.Pindex[k]=(1ll<<42)+k;one.Vertexs[k].xyz[0]=k;one.Vertexs[k].xyz[1]=-0.;one.Vertexs[k].xyz[2]=std::numeric_limits<double>::infinity();}
 std::map<int,std::vector<xdVElement>> batches{{2,{one}},{5,std::vector<xdVElement>(10,one)}};
 Audit audit;audit.Run(batches,140);
 assert(audit.values[Audit::peers]==2 && audit.values[Audit::items]==11 && audit.values[Audit::unique_vertices]==8);
 assert(audit.values[Audit::raw_bytes]==1540 && audit.values[Audit::dictionary_bytes]==596 && audit.values[Audit::selected_bytes]==564);
 assert(audit.values[Audit::profitable_peers]==1 && audit.values[Audit::bypass_peers]==1);
 assert(audit.values[Audit::verified_items]==11 && audit.values[Audit::verified_peers]==2 && audit.values[Audit::mismatches]==0);
 try{audit.Run(batches,140);assert(false);}catch(const std::logic_error&){}
 Audit wrong;try{wrong.Run(batches,144);assert(false);}catch(const std::logic_error&){}
 Audit empty;empty.Run(std::map<int,std::vector<xdVElement>>{},140);assert(empty.values[Audit::calls]==1 && empty.values[Audit::peers]==0);
 std::cout<<"PASS: "<<tested<<" exact codec roundtrips, 64-bit IDs/domains, signed zero/NaN bits, repeated/conflicting vertex keys, hostile lengths/indices, peer bypass and full audit\n";
}
'''
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);p=tmp/'codec.cpp';p.write_text(cpp)
    for name,flags in [('checked',['-O2','-fsanitize=address,undefined','-fno-sanitize-recover=all']),('optimized',['-O3'])]:
        exe=tmp/name
        subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra','-Werror','-I',str(ROOT/'mesh_occ_mpi'),*flags,str(p),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'),check=True)
