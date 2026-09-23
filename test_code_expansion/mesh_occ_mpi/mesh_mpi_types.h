#pragma once
#include "parallelMeshData.h"
#include "mpi_debug.h"
#include <type_traits>
#include <array>

inline void mesh_count_sum(void *input,void *output,int *length,MPI_Datatype *) {
    const auto *a=static_cast<const GlobalCount*>(input);
    auto *b=static_cast<GlobalCount*>(output);
    for(int i=0;i<*length;++i) b[i]=checked_count_sum(a[i],b[i]);
}

// Inclusive scan gives rank zero a defined result, unlike Exscan. The last
// rank also checks global overflow; Abort prevents a partially valid mesh.
inline std::array<GlobalId,2> prefix_id_pair(const GlobalCount (&local)[2],MPI_Comm comm) {
    std::array<GlobalCount,2> inclusive{};
    MPI_Op sum;
    netgen_mpi_check(comm,MPI_Op_create(mesh_count_sum,1,&sum),"id_prefix/op_create");
    netgen_mpi_check(comm,MPI_Scan(local,inclusive.data(),2,MPI_INT64_T,sum,comm),"id_prefix/Scan");
    netgen_mpi_check(comm,MPI_Op_free(&sum),"id_prefix/op_free");
    for(int i=0;i<2;++i) {
        if(local[i]<0 || inclusive[i]<local[i])
            netgen_mpi_check(comm,MPI_ERR_COUNT,"id_prefix/overflow");
        inclusive[i]-=local[i];
    }
    return inclusive;
}

// Set the array stride explicitly: mixed 32/64-bit fields introduce padding.
template<class Record, std::size_t N>
inline MPI_Datatype mesh_record_type(Record &record, const int (&lengths)[N],
                                    const MPI_Datatype (&types)[N],
                                    MPI_Aint (&addresses)[N], MPI_Comm comm)
{
    static_assert(std::is_standard_layout<Record>::value, "MPI record must have standard layout");
    MPI_Aint base;
    netgen_mpi_check(comm, MPI_Get_address(&record, &base), "mesh_record_type/address");
    for (auto &address : addresses) address -= base;
    MPI_Datatype raw, resized;
    netgen_mpi_check(comm, MPI_Type_create_struct(static_cast<int>(N), lengths, addresses, types, &raw),
                    "mesh_record_type/struct");
    netgen_mpi_check(comm, MPI_Type_create_resized(raw, 0, sizeof(Record), &resized),
                    "mesh_record_type/resize");
    netgen_mpi_check(comm, MPI_Type_free(&raw), "mesh_record_type/free");
    netgen_mpi_check(comm, MPI_Type_commit(&resized), "mesh_record_type/commit");
    return resized;
}

inline MPI_Datatype barycentric_mpi_type(MPI_Comm comm)
{
    Barycentric record{};
    const int lengths[] = {3, 3, 1};
    const MPI_Datatype types[] = {MPI_INT, MPI_SHORT, MPI_INT64_T};
    MPI_Aint addresses[3];
    netgen_mpi_check(comm, MPI_Get_address(record.gvrtx, addresses), "barycentric/address");
    netgen_mpi_check(comm, MPI_Get_address(record.coord, addresses + 1), "barycentric/address");
    netgen_mpi_check(comm, MPI_Get_address(&record.newgid, addresses + 2), "barycentric/address");
    return mesh_record_type(record, lengths, types, addresses, comm);
}

inline MPI_Datatype volume_element_mpi_type(MPI_Comm comm)
{
    xdVElement record{};
    const int lengths[] = {4, 12, 1, 1};
    const MPI_Datatype types[] = {MPI_INT64_T, MPI_DOUBLE, MPI_INT64_T, MPI_INT};
    MPI_Aint addresses[4];
    netgen_mpi_check(comm, MPI_Get_address(record.Pindex, addresses), "volume_element/address");
    netgen_mpi_check(comm, MPI_Get_address(record.Vertexs, addresses + 1), "volume_element/address");
    netgen_mpi_check(comm, MPI_Get_address(&record.gid, addresses + 2), "volume_element/address");
    netgen_mpi_check(comm, MPI_Get_address(&record.domidx, addresses + 3), "volume_element/address");
    return mesh_record_type(record, lengths, types, addresses, comm);
}

inline std::vector<GlobalId> checked_id_offsets(const std::vector<GlobalCount> &counts, MPI_Comm comm)
{
    try {
        return make_id_offsets(counts);
    } catch (const std::exception &e) {
        std::fprintf(stderr, "%s\n", e.what());
        MPI_Abort(comm, MPI_ERR_COUNT);
        std::abort();
    }
}

inline std::vector<GlobalId> gather_id_offsets(GlobalCount local_count, MPI_Comm comm)
{
    int size;
    netgen_mpi_check(comm, MPI_Comm_size(comm, &size), "gather_id_offsets/size");
    std::vector<GlobalCount> counts(size);
    netgen_mpi_check(comm, MPI_Allgather(&local_count, 1, MPI_INT64_T,
                                      counts.data(), 1, MPI_INT64_T, comm),
                    "gather_id_offsets/Allgather");
    return checked_id_offsets(counts, comm);
}

inline void require_local_mesh_capacity(GlobalCount count, MPI_Comm comm)
{
    if (fits_local_mesh(count)) return;
    std::fprintf(stderr, "Rank-local mesh exceeds Netgen's int index capacity; use more partitions.\n");
    MPI_Abort(comm, MPI_ERR_COUNT);
    std::abort();
}
