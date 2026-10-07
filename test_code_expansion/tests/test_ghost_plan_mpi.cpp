// Real MPI stress: large ID messages, delayed peer, zero/asymmetric ghost counts.
#include "ghost_exchange_plan.h"
#include <cassert>
#include <cstdint>
#include <algorithm>
#include <chrono>
#include <thread>
#include <iostream>
struct Payload {std::int64_t Pindex[4],gid;struct {double xyz[3];}Vertexs[4];int domidx;};
struct Mesh {
 int rank;int np()const{return 4;}int ne()const{return 1;}
 void tetrahedron(int,int *v,int &domain)const{for(int k=0;k<4;++k)v[k]=k+1;domain=rank+1;}
 void point(int v,double *p)const{p[0]=rank;p[1]=v;p[2]=v%2?-0.0:0.0;}
};
int main(int argc,char **argv){
 MPI_Init(&argc,&argv);int rank,ranks;MPI_Comm_rank(MPI_COMM_WORLD,&rank);MPI_Comm_size(MPI_COMM_WORLD,&ranks);
 std::set<int>neighbors;for(int p:{(rank+1)%ranks,(rank+ranks-1)%ranks})if(p!=rank)neighbors.insert(p);
 for(bool overlap:{false,true}){
  Mesh mesh{rank};std::map<int,std::vector<int>>adj;
  for(int v=1;v<=(rank%3==0?2:4);++v)adj[v]=std::vector<int>(neighbors.begin(),neighbors.end());
  mesh_research::GhostExchangePlan<Payload>plan;
  std::vector<int>peers(neighbors.begin(),neighbors.end()),lengths(peers.size(),32768);
  std::vector<std::vector<std::int64_t>>send(peers.size()),receive(peers.size());
  std::vector<std::int64_t*>sp,rp;
  for(std::size_t i=0;i<peers.size();++i){send[i].resize(lengths[i]);receive[i].resize(lengths[i]);
   for(int j=0;j<lengths[i];++j)send[i][j]=(1LL<<40)+rank*100000+j;
   sp.push_back(send[i].data());rp.push_back(receive[i].data());}
  if(rank==ranks-1)std::this_thread::sleep_for(std::chrono::milliseconds(20));
  auto requests=mesh_research::PostKnownVertices(MPI_COMM_WORLD,MPI_INT64_T,rank,
    peers.size(),peers.size(),peers.data(),peers.data(),lengths.data(),lengths.data(),sp.data(),rp.data());
  mesh_research::GhostCountExchange exchange;
  auto prepare=[&]{plan.Build(mesh,adj,rank,ranks);std::map<int,int>counts;
    for(const auto &entry:plan.packets)counts[entry.first]=entry.second.size();
    exchange.Begin(MPI_COMM_WORLD,rank,ranks,neighbors,counts);};
  if(overlap)prepare();
  mesh_research::WaitKnownVertices(MPI_COMM_WORLD,requests);
  for(std::size_t i=0;i<peers.size();++i)for(int j=0;j<lengths[i];++j)assert(receive[i][j]==(1LL<<40)+peers[i]*100000+j);
  if(!overlap)prepare();
  std::int64_t vids[5]={0},eids[2]={0,(1LL<<42)+rank};
  for(int v=1;v<=4;++v)vids[v]=(1LL<<41)+rank*10+v;
  plan.Bind(mesh,vids,eids);assert(plan.Verify(mesh,adj,vids,eids)==plan.send_elements);
  exchange.Wait();
  std::map<int,std::vector<Payload>>received;requests.clear();
  for(int peer:neighbors){assert(exchange.receive[peer]==(peer%3==0?0:1));
   if(exchange.receive[peer]){received[peer].resize(exchange.receive[peer]);requests.push_back(MPI_REQUEST_NULL);
    MPI_Irecv(received[peer].data(),sizeof(Payload),MPI_BYTE,peer,4822,MPI_COMM_WORLD,&requests.back());}}
  for(auto &entry:plan.packets){requests.push_back(MPI_REQUEST_NULL);MPI_Isend(entry.second.data(),sizeof(Payload),MPI_BYTE,entry.first,4822,MPI_COMM_WORLD,&requests.back());}
  mesh_research::WaitKnownVertices(MPI_COMM_WORLD,requests);
  for(const auto &entry:received){int peer=entry.first;const auto &p=entry.second[0];assert(p.gid==(1LL<<42)+peer && p.domidx==peer+1);
   for(int k=0;k<4;++k){assert(p.Pindex[k]==(1LL<<41)+peer*10+k+1);double xyz[3];Mesh{peer}.point(k+1,xyz);assert(std::memcmp(xyz,p.Vertexs[k].xyz,sizeof(xyz))==0);}}
 }
 if(rank==0)std::cout<<"PASS: real MPI ghost plan, large vertex IDs, delayed peer, empty/asymmetric ghosts, count lifetime and exact payloads ranks="<<ranks<<"\n";
 MPI_Finalize();
}
