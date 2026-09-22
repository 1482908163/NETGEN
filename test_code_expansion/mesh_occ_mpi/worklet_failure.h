#pragma once
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <fcntl.h>
#include <unistd.h>

namespace mesh_research {
// No MPI/collective in the failure path: peers may already be aborting.
// O_EXCL elects a writer for the small run-level report. Each reporting rank
// also keeps its own record; the elected writer need not be the causal first.
struct WorkletFailure {
    const char *stage="entry";
    int rank=-1,task=-1,owner=-1,executor=-1;
    int status=-1;double final_illegal=-1;
    void write(const char *reason) const noexcept {
        char report[4096];
        const int length=std::snprintf(report,sizeof(report),
            "worklet_failure_v1\nrank=%d\ntask=%d\nowner=%d\nexecutor=%d\nstage=%s\nng_status=%d\nfinal_illegal=%.17g\nreason=%.2800s\n",
            rank,task,owner,executor,stage,status,final_illegal,reason?reason:"unknown");
        const std::size_t size=length<0?0:static_cast<std::size_t>(length)<sizeof(report)?static_cast<std::size_t>(length):sizeof(report)-1;
        std::fwrite(report,1,size,stderr);std::fflush(stderr);
        const char *directory=std::getenv("MESH_FAILURE_DIR");if(!directory || !*directory)return;
        auto save=[&](const char *name) {
            char path[4096];int n=std::snprintf(path,sizeof(path),"%s/%s",directory,name);
            if(n<0 || static_cast<std::size_t>(n)>=sizeof(path))return;
            int fd=::open(path,O_WRONLY|O_CREAT|O_EXCL,0600);
            if(fd<0)return;
            std::size_t done=0;
            while(done<size){ssize_t wrote=::write(fd,report+done,size-done);if(wrote<0 && errno==EINTR)continue;if(wrote<=0)break;done+=static_cast<std::size_t>(wrote);}
            ::fsync(fd);::close(fd);
        };
        char name[80];std::snprintf(name,sizeof(name),"failure_rank_%d.txt",rank);
        save(name);save("failure_reason.txt");
    }
};
}
