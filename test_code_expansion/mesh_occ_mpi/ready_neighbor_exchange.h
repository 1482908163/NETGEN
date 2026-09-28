#pragma once
#include "mpi_debug.h"
#include <cstdint>
#include <limits>
#include <map>
#include <set>
#include <vector>

namespace mesh_research {
struct ReadyExchangeStats {int counts=0,early_receives=0,payload_sends=0;};
// All sends are posted before waiting for counts. Each received count enables
// that peer's payload receive immediately; unrelated slow peers cannot gate it.
// Caller retains peer-indexed buffers and commits in rank order after return.
template<class Item>
ReadyExchangeStats exchange_ready_neighbors(MPI_Comm comm,MPI_Datatype type,
    const std::set<int> &peers,const std::map<int,std::vector<Item>> &send,
    const std::vector<int> &send_counts,std::vector<int> &recv_counts,
    std::vector<std::vector<Item>> &receive)
{
    const auto check=[&](int code,const char *where){netgen_mpi_check(comm,code,where);};
    const int n=static_cast<int>(peers.size());
    if(peers.size()>static_cast<std::size_t>(std::numeric_limits<int>::max()/2))
        check(MPI_ERR_COUNT,"ready neighbor request count overflow");
    std::vector<int> ranks(peers.begin(),peers.end());
    std::vector<MPI_Request> counts(n,MPI_REQUEST_NULL),count_sends(n,MPI_REQUEST_NULL),payload;
    payload.reserve(2*peers.size());
    ReadyExchangeStats stats;
    for(int i=0;i<n;++i) {
        const int peer=ranks[i];
        if(peer<0 || peer>=static_cast<int>(send_counts.size()) || recv_counts.size()!=send_counts.size() || receive.size()!=send_counts.size())
            check(MPI_ERR_RANK,"invalid ready neighbor");
        check(MPI_Irecv(&recv_counts[peer],1,MPI_INT,peer,4821,comm,&counts[i]),"ready count receive");
    }
    for(int i=0;i<n;++i) {
        const int peer=ranks[i];
        if(send_counts[peer]<0)check(MPI_ERR_COUNT,"negative ready send count");
        check(MPI_Isend(&send_counts[peer],1,MPI_INT,peer,4821,comm,&count_sends[i]),"ready count send");
        if(send_counts[peer]>0) {
            const auto found=send.find(peer);
            if(found==send.end() || found->second.size()!=static_cast<std::size_t>(send_counts[peer]))
                check(MPI_ERR_COUNT,"ready send count differs from buffer");
            payload.push_back(MPI_REQUEST_NULL);
            check(MPI_Isend(found->second.data(),send_counts[peer],type,peer,4822,comm,&payload.back()),"ready payload send");
            ++stats.payload_sends;
        }
    }
    std::int64_t total=0;
    for(int pending=n;pending>0;--pending) {
        int index=MPI_UNDEFINED;
        check(MPI_Waitany(n,counts.data(),&index,MPI_STATUS_IGNORE),"ready count completion");
        if(index<0 || index>=n)check(MPI_ERR_OTHER,"missing ready count completion");
        const int peer=ranks[index],count=recv_counts[peer];++stats.counts;
        if(count<0 || count>std::numeric_limits<int>::max()-total)
            check(MPI_ERR_COUNT,"ready receive count exceeds local capacity");
        total+=count;
        if(count) {
            receive[peer].resize(static_cast<std::size_t>(count));
            payload.push_back(MPI_REQUEST_NULL);
            check(MPI_Irecv(receive[peer].data(),count,type,peer,4822,comm,&payload.back()),"ready payload receive");
            stats.early_receives+=(pending>1);
        }
    }
    if(n)check(MPI_Waitall(n,count_sends.data(),MPI_STATUSES_IGNORE),"ready count sends complete");
    if(!payload.empty())check(MPI_Waitall(static_cast<int>(payload.size()),payload.data(),MPI_STATUSES_IGNORE),"ready payloads complete");
    return stats;
}
}
