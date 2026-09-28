#include "ready_neighbor_exchange.h"
#include <cassert>
#include <cstring>
#include <iostream>
#include <stdexcept>
struct Request {void *ptr;const void *sendptr;int count,type,peer,tag;bool send;std::vector<char> snapshot;};
static std::map<int,Request> requests;
static std::map<int,int> counts_by_peer;
static int next_request=1,count_steps=0,expected_sends=0,last_peer=-1;
static bool negative=false;
static int bytes(int type){return type==MPI_INT?sizeof(int):sizeof(std::int64_t);}
int MPI_Comm_rank(MPI_Comm,int*r){*r=0;return MPI_SUCCESS;}
int MPI_Error_string(int,char*,int*n){*n=0;return MPI_SUCCESS;}
int MPI_Abort(MPI_Comm,int){throw std::runtime_error("MPI abort");}
int MPI_Isend(const void*ptr,int count,MPI_Datatype type,int peer,int tag,MPI_Comm,MPI_Request*r) {
 *r=next_request++;Request q{nullptr,ptr,count,type,peer,tag,true,{}};
 const auto *p=static_cast<const char*>(ptr);q.snapshot.assign(p,p+count*bytes(type));requests.emplace(*r,q);return 0;
}
int MPI_Irecv(void*ptr,int count,MPI_Datatype type,int peer,int tag,MPI_Comm,MPI_Request*r) {
 *r=next_request++;requests.emplace(*r,Request{ptr,nullptr,count,type,peer,tag,false,{}});return 0;
}
int MPI_Waitany(int n,MPI_Request*r,int *index,MPI_Status*) {
 int sends=0;bool last_received=false;
 for(const auto &entry:requests) {
  const auto &q=entry.second;sends+=q.send && q.tag==4822;
  if(!q.send && q.tag==4822 && q.peer==last_peer)last_received=true;
 }
 assert(sends==expected_sends); // Payloads launched before any count wait.
 if(last_peer>=0 && counts_by_peer[last_peer]>0)assert(last_received);
 *index=-1;
 for(int i=n-1;i>=0;--i)if(r[i]!=MPI_REQUEST_NULL){*index=i;break;}
 assert(*index>=0);auto &q=requests.at(r[*index]);assert(!q.send && q.tag==4821);
 *static_cast<int*>(q.ptr)=negative?-1:counts_by_peer[q.peer];last_peer=q.peer;
 r[*index]=MPI_REQUEST_NULL;++count_steps;return 0;
}
int MPI_Waitall(int n,MPI_Request*r,MPI_Status*) {
 for(int i=0;i<n;++i) {
  if(!r[i])continue;
  const auto &q=requests.at(r[i]);
  if(q.send)assert(std::memcmp(q.snapshot.data(),q.sendptr,q.snapshot.size())==0);
  else {assert(q.tag==4822);auto *out=static_cast<std::int64_t*>(q.ptr);for(int j=0;j<q.count;++j)out[j]=(1LL<<40)+q.peer*100+j;}
  r[i]=MPI_REQUEST_NULL;
 }
 return 0;
}
int main() {
 for(int n=0;n<8;++n) {
  requests.clear();count_steps=0;expected_sends=0;last_peer=-1;negative=false;
  std::set<int> peers;std::map<int,std::vector<std::int64_t>> send;
  std::vector<int> outgoing(n+1),incoming(n+1);std::vector<std::vector<std::int64_t>> receive(n+1);
  for(int peer=1;peer<=n;++peer) {
   peers.insert(peer);counts_by_peer[peer]=peer%3;outgoing[peer]=(peer+1)%3;
   if(outgoing[peer]){send[peer].assign(outgoing[peer],1000+peer);++expected_sends;}
  }
  const auto stats=mesh_research::exchange_ready_neighbors(1,MPI_INT64_T,peers,send,outgoing,incoming,receive);
  assert(stats.counts==n && stats.payload_sends==expected_sends);
  for(int peer:peers){assert(incoming[peer]==counts_by_peer[peer]);for(int j=0;j<incoming[peer];++j)assert(receive[peer][j]==(1LL<<40)+peer*100+j);}
 }
 negative=true;requests.clear();last_peer=-1;expected_sends=0;
 std::vector<int> outgoing(2),incoming(2);std::vector<std::vector<std::int64_t>> receive(2);
 bool failed=false;try{mesh_research::exchange_ready_neighbors(1,MPI_INT64_T,{1},std::map<int,std::vector<std::int64_t>>{},outgoing,incoming,receive);}catch(const std::runtime_error&){failed=true;}
 assert(failed);std::cout<<"PASS: production ready-neighbor helper, delayed count order, early payload posting, zero/asymmetric counts, buffer lifetimes and negative-count rejection (scripted MPI)\n";
}
