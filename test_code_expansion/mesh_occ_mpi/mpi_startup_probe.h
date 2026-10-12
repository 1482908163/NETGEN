#pragma once
#include "mpi_debug.h"
#include <cstdint>
namespace mesh_research {
inline int MPIStartupProbe(MPI_Comm comm,int provided){
    int rank=0,size=0;netgen_mpi_check(comm,MPI_Comm_rank(comm,&rank),"startup/rank");
    netgen_mpi_check(comm,MPI_Comm_size(comm,&size),"startup/size");
    const std::int64_t local=std::int64_t(rank)+1;std::int64_t sum=0;
    netgen_mpi_check(comm,MPI_Allreduce(&local,&sum,1,MPI_INT64_T,MPI_SUM,comm),"startup/Allreduce");
    if(sum!=std::int64_t(size)*(std::int64_t(size)+1)/2 || provided<MPI_THREAD_FUNNELED)netgen_mpi_check(comm,MPI_ERR_OTHER,"startup/result");
    netgen_mpi_check(comm,MPI_Barrier(comm),"startup/Barrier");
    if(rank==0){std::printf("[MPI_PREFLIGHT_OK] ranks=%d thread_support=%d\n",size,provided);std::fflush(stdout);}
    return MPI_Finalize()==MPI_SUCCESS?0:2;
}
}
