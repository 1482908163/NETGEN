#include "ready_neighbor_exchange.h"
#include <cassert>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <thread>
static int count(int from,int to,int round) {return (from+to)%3==0?0:(round?8192:1)+(from*7+to)%4;}
static std::int64_t value(int from,int to,int i) {return (std::int64_t{1}<<40)+1000000LL*from+10000LL*to+i;}
int main(int argc,char **argv) {
  int provided=0;MPI_Init_thread(&argc,&argv,MPI_THREAD_FUNNELED,&provided);
  int rank=0,p=0;MPI_Comm_rank(MPI_COMM_WORLD,&rank);MPI_Comm_size(MPI_COMM_WORLD,&p);
  for(int round=0;round<2;++round) {
    std::set<int> peers;std::map<int,std::vector<std::int64_t>> send;
    std::vector<int> outgoing(p),incoming(p);std::vector<std::vector<std::int64_t>> receive(p);
    for(int peer=0;peer<p;++peer)if(peer!=rank) {
      peers.insert(peer);outgoing[peer]=count(rank,peer,round);
      for(int i=0;i<outgoing[peer];++i)send[peer].push_back(value(rank,peer,i));
    }
    MPI_Barrier(MPI_COMM_WORLD);
    if(rank==p-1)std::this_thread::sleep_for(std::chrono::milliseconds(30));
    const auto stats=mesh_research::exchange_ready_neighbors(MPI_COMM_WORLD,MPI_INT64_T,peers,send,outgoing,incoming,receive);
    assert(stats.counts==p-1);
    for(int peer:peers) {
      assert(incoming[peer]==count(peer,rank,round));
      assert(receive[peer].size()==static_cast<std::size_t>(incoming[peer]));
      for(int i=0;i<incoming[peer];++i)assert(receive[peer][i]==value(peer,rank,i));
    }
  }
  if(rank==0)std::cout<<"PASS: ready neighbor payloads, zero/asymmetric counts, delayed peer and 64-bit values\n";
  MPI_Finalize();
}
