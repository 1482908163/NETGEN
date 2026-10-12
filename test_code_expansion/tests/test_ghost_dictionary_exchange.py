#!/usr/bin/env python3
"""Execute real transport under a concurrent deterministic MPI message model.
This checks matching/counts/ordering, not a native MPI implementation or speed.
"""
from pathlib import Path
import os,subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'mesh_occ_mpi/parallelMeshData.h').read_text()
records=source[source.index('typedef struct xdVertex {'):source.index('}xdVertex;')+len('}xdVertex;')]+source[source.index('typedef struct xdVElement {'):source.index('}xdVElement;')+len('}xdVElement;')]
mpi=r'''#pragma once
#include <cstdint>
using MPI_Comm=int;using MPI_Datatype=int;using MPI_Request=int;
struct MPI_Status{int count;};
constexpr int MPI_SUCCESS=0,MPI_ERR_OTHER=5,MPI_REQUEST_NULL=-1,MPI_INT64_T=1,MPI_BYTE=2,PACKET=3,MPI_MAX_ERROR_STRING=256;
int MPI_Comm_size(MPI_Comm,int*);int MPI_Comm_rank(MPI_Comm,int*);int MPI_Type_size(MPI_Datatype,int*);
int MPI_Isend(const void*,int,MPI_Datatype,int,int,MPI_Comm,MPI_Request*);
int MPI_Irecv(void*,int,MPI_Datatype,int,int,MPI_Comm,MPI_Request*);
int MPI_Waitall(int,MPI_Request*,MPI_Status*);int MPI_Get_count(const MPI_Status*,MPI_Datatype,int*);
int MPI_Error_string(int,char*,int*);int MPI_Abort(MPI_Comm,int);double MPI_Wtime();
'''
cpp=r'''#include <cstdint>
#include <cstring>
#include <cassert>
#include <mutex>
#include <condition_variable>
#include <deque>
#include <thread>
#include <random>
#include <iostream>
using GlobalId=std::int64_t;
'''+records+r'''
#include "ghost_dictionary_exchange.h"
using namespace mesh_research::ghost_transport;
struct Message{bool send,done=false;int source,dest,tag,count,type;const void *input;void *output;int actual=0;};
std::deque<Message> queue;std::mutex mutex;std::condition_variable changed;
thread_local int rank=0;int size=1;bool wrong_count=false;
int MPI_Comm_rank(int,int *out){*out=rank;return 0;}int MPI_Comm_size(int,int *out){*out=size;return 0;}
int MPI_Type_size(int type,int *out){*out=type==PACKET?140:type==MPI_INT64_T?8:1;return 0;}
int Post(bool send,const void *input,void *output,int count,int type,int peer,int tag,int *request){
 std::lock_guard<std::mutex> lock(mutex);*request=static_cast<int>(queue.size());
 queue.push_back({send,false,send?rank:peer,send?peer:rank,tag,count,type,input,output,0});
 auto &now=queue.back();
 for(std::size_t i=0;i+1<queue.size();++i){auto &old=queue[i];if(old.done || old.send==send || old.source!=now.source || old.dest!=now.dest || old.tag!=tag)continue;
  auto &s=send?now:old;auto &r=send?old:now;assert(s.type==r.type && s.count==r.count);
  const std::size_t stride=type==PACKET?sizeof(xdVElement):type==MPI_INT64_T?8:1;
  std::memcpy(r.output,s.input,stride*s.count);s.done=r.done=true;s.actual=r.actual=s.count;break;
 }
 changed.notify_all();return 0;
}
int MPI_Isend(const void *p,int n,int t,int peer,int tag,int,MPI_Request *r){return Post(true,p,nullptr,n,t,peer,tag,r);}
int MPI_Irecv(void *p,int n,int t,int peer,int tag,int,MPI_Request *r){return Post(false,nullptr,p,n,t,peer,tag,r);}
int MPI_Waitall(int n,MPI_Request *r,MPI_Status *s){std::unique_lock<std::mutex> lock(mutex);
 bool ready=changed.wait_for(lock,std::chrono::seconds(5),[&]{for(int i=0;i<n;++i)if(!queue[r[i]].done)return false;return true;});assert(ready);
 for(int i=0;i<n;++i){s[i].count=queue[r[i]].actual;r[i]=MPI_REQUEST_NULL;}return 0;
}
int MPI_Get_count(const MPI_Status *s,int,int *n){*n=s->count-(wrong_count?1:0);return 0;}
int MPI_Error_string(int,char*,int *n){*n=0;return 0;}int MPI_Abort(int,int){std::abort();}double MPI_Wtime(){return 0;}
int main(){
 assert(Chunks(std::uint64_t(INT_MAX)+1).size()==2);assert(Chunks(19,7).back().count==5);
 for(int limit:{0,-1}){try{Chunks(4,limit);assert(false);}catch(const std::overflow_error&){} }
 for(Header h: {Header{{2,1,140}},Header{{0,1,139}},Header{{1,1,140}},Header{{1,1,20}},Header{{1,2,280}},Header{{1,2,-1}},Header{{0,2,140}}}){try{CheckHeader(h,1);assert(false);}catch(const std::runtime_error&){} }
 std::uint64_t messages=0;
 for(int n:{1,2,3,4,8})for(int limit:{INT_MAX,17})for(bool corrupt:{false,true}){
  size=n;queue.clear();std::vector<std::map<int,std::vector<xdVElement>>> outgoing(n);
  std::vector<std::vector<int>> sc(n,std::vector<int>(n)),rc(n,std::vector<int>(n));
  for(int r=0;r<n;++r)for(int peer=0;peer<n;++peer)if(peer!=r && ((r+peer)%3!=1 || r==0 || peer==0)){
   const int count=(r+peer)%2?1:20;auto &packets=outgoing[r][peer];packets.resize(count);
   for(int i=0;i<count;++i){auto &p=packets[i];p.gid=(1ll<<50)+r*100+i;p.domidx=-42;
    for(int k=0;k<4;++k){p.Pindex[k]=(1ll<<48)+k+(((r+peer)%4==0)?i*4:0);for(int d=0;d<3;++d){std::uint64_t bits=0x7ff8000000000001ull+k+d;std::memcpy(&p.Vertexs[k].xyz[d],&bits,8);}}
   }
   sc[r][peer]=count;rc[peer][r]=count;
  }
  std::vector<Stats> stats(n);std::vector<int> failures(n);std::vector<std::thread> threads;
  for(int r=0;r<n;++r)threads.emplace_back([&,r]{rank=r;std::vector<std::vector<xdVElement>> receive(n);
   stats[r]=Exchange<xdVElement>(0,PACKET,outgoing[r],sc[r],rc[r],receive,limit);
   for(int peer=0;peer<n;++peer){const auto it=outgoing[peer].find(r);assert(mesh_research::ghost_dictionary::Equal(it==outgoing[peer].end()?std::vector<xdVElement>{}:it->second,receive[peer]));}
   if(corrupt && r==0)for(auto &v:receive)if(!v.empty()){v[0].Pindex[0]^=1;break;}
   try{Verify<xdVElement>(0,PACKET,outgoing[r],sc[r],rc[r],receive,stats[r],limit);}catch(const std::runtime_error&){failures[r]=1;}
  });
  for(auto &t:threads)t.join();
  assert(std::accumulate(failures.begin(),failures.end(),0)==int(corrupt && n>1));
  double sent=0,received=0,dict=0,literal=0;
  for(auto &s:stats){sent+=s.values[Stats::payload_send_bytes];received+=s.values[Stats::payload_receive_bytes];dict+=s.values[Stats::dictionary_send_peers];literal+=s.values[Stats::literal_send_peers];}
  assert(sent==received);if(n>=3)assert(dict>0 && literal>0);
  for(auto &m:queue){assert(m.done);}messages+=queue.size();
 }
 // Short native-message receipt must be rejected independently of decoding.
 rank=0;size=1;queue.clear();queue.push_back({false,true,0,0,1,3,MPI_INT64_T,nullptr,nullptr,3});
 std::vector<MPI_Request> requests{0};wrong_count=true;
 try{Wait(0,requests,{{MPI_INT64_T,3}});assert(false);}catch(const std::runtime_error&){}wrong_count=false;
 std::cout<<"PASS: real transport with concurrent 1/2/3/4/8-rank message model; dictionary/literal bypass, asymmetry, chunks, NaN bits, oracle corruption and short message rejection; messages="<<messages<<"\n";
}
'''
with tempfile.TemporaryDirectory() as directory:
    d=Path(directory);(d/'mpi.h').write_text(mpi);(d/'test.cpp').write_text(cpp)
    for flags in (['-O2','-fsanitize=address,undefined','-fno-omit-frame-pointer'],['-O3']):
        subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra','-Werror','-pthread',*flags,'-I',str(d),'-I',str(ROOT/'mesh_occ_mpi'),str(d/'test.cpp'),'-o',str(d/'test')],check=True)
        subprocess.run([str(d/'test')],check=True,timeout=30,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
