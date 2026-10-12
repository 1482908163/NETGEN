#include "mesh_mpi_types.h"
#include "ghost_dictionary_exchange.h"
#include <cassert>
#include <thread>
#include <chrono>
#include <iostream>
int main(int argc,char **argv){
    MPI_Init(&argc,&argv);int rank=0,size=0;MPI_Comm_rank(MPI_COMM_WORLD,&rank);MPI_Comm_size(MPI_COMM_WORLD,&size);
    auto type=volume_element_mpi_type(MPI_COMM_WORLD);
    for(int limit:{INT_MAX,17}){
        std::map<int,std::vector<xdVElement>> outgoing;std::vector<int> send(size),receive_counts(size);
        for(int peer=0;peer<size;++peer)if(peer!=rank && ((rank+peer)%3!=1 || rank==0)){
            auto &records=outgoing[peer];records.resize((rank+peer)%2?1:128);
            for(std::size_t i=0;i<records.size();++i){auto &p=records[i];p.gid=(1ll<<50)+1000*rank+i;p.domidx=-31;
                for(int k=0;k<4;++k){p.Pindex[k]=(1ll<<48)+k+((rank+peer)%4==0?4*i:0);for(int d=0;d<3;++d){std::uint64_t bits=0x7ff8000000000001ull+k+d;std::memcpy(&p.Vertexs[k].xyz[d],&bits,8);}}}
            send[peer]=static_cast<int>(records.size());
        }
        MPI_Alltoall(send.data(),1,MPI_INT,receive_counts.data(),1,MPI_INT,MPI_COMM_WORLD);
        if(rank==size-1)std::this_thread::sleep_for(std::chrono::milliseconds(20));
        std::vector<std::vector<xdVElement>> receive(size);
        auto stats=mesh_research::ghost_transport::Exchange<xdVElement>(MPI_COMM_WORLD,type,outgoing,send,receive_counts,receive,limit);
        mesh_research::ghost_transport::Verify<xdVElement>(MPI_COMM_WORLD,type,outgoing,send,receive_counts,receive,stats,limit);
        assert(stats.values[mesh_research::ghost_transport::Stats::mismatches]==0);
    }
    MPI_Type_free(&type);if(rank==0)std::cout<<"PASS: native MPI dictionary/literal messages, asymmetric peers, empty ranks, small chunks and complete receive reference ranks="<<size<<"\n";
    MPI_Finalize();
}
