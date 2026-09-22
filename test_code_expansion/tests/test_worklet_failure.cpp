#include "worklet_failure.h"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <string>
#include <sys/wait.h>
int main() {
    char directory[]="/tmp/netgen-worklet-failure-XXXXXX";
    assert(mkdtemp(directory));assert(setenv("MESH_FAILURE_DIR",directory,1)==0);
    for(int rank=0;rank<8;++rank) {
        const pid_t pid=fork();assert(pid>=0);
        if(pid==0) {
            mesh_research::WorkletFailure f;f.rank=rank;f.task=42;f.owner=3;f.executor=rank;
            f.stage="volume_generation";f.status=0;f.final_illegal=7;
            f.write("original exception: closed mesh failure");_exit(0);
        }
    }
    for(int i=0;i<8;++i){int status=0;assert(wait(&status)>0 && WIFEXITED(status) && WEXITSTATUS(status)==0);}
    auto read=[](const std::filesystem::path &p){std::ifstream f(p);return std::string(std::istreambuf_iterator<char>(f),{});};
    const auto root=std::filesystem::path(directory),summary=root/"failure_reason.txt";
    const auto first=read(summary);
    assert(first.find("stage=volume_generation")!=std::string::npos);
    assert(first.find("final_illegal=7")!=std::string::npos);
    assert(first.find("reason=original exception: closed mesh failure\n")!=std::string::npos);
    for(int i=0;i<8;++i)assert(read(root/("failure_rank_"+std::to_string(i)+".txt")).find("rank="+std::to_string(i)+"\n")!=std::string::npos);
    mesh_research::WorkletFailure later;later.write("secondary failure must not replace original");
    assert(read(summary)==first);
    std::filesystem::remove_all(root);
}
