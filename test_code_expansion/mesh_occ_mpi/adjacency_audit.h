#pragma once
#include "mesh_ids.h"
#include <array>
#include <cmath>
#include <cstring>
#include <set>
#include <string>

namespace mesh_audit {
// Order independent across receive order, but ties each coordinate/connectivity
// to its canonical global ID. Validation only; no production communication.
struct AdjacencyAudit {
    std::uint64_t point_sum=0,point_xor=0,element_sum=0,element_xor=0;
    GlobalCount points=0,elements=0,ghosts=0;
    std::set<GlobalId> point_ids,element_ids;
    static void mix(std::uint64_t &h,std::uint64_t x) {h=(h^x)*1099511628211ULL;}
    void point(GlobalId gid,const std::array<double,3> &xyz) {
        if(gid<=0 || !point_ids.insert(gid).second)
            throw std::runtime_error("invalid or duplicate adjacency point ID");
        std::uint64_t h=1469598103934665603ULL;mix(h,static_cast<std::uint64_t>(gid));
        for(double x:xyz) {
            if(!std::isfinite(x))throw std::runtime_error("nonfinite adjacency coordinate");
            if(x==0.)x=0.; // Canonicalize negative zero.
            std::uint64_t bits=0;static_assert(sizeof(bits)==sizeof(x),"64-bit double required");
            std::memcpy(&bits,&x,sizeof(bits));mix(h,bits);
        }
        point_sum+=h;point_xor^=h;++points;
    }
    void element(GlobalId gid,const std::array<GlobalId,4> &vertices,int domain,bool ghost) {
        if(gid<=0 || !element_ids.insert(gid).second)
            throw std::runtime_error("invalid or duplicate adjacency element ID");
        std::set<GlobalId> unique;
        std::uint64_t h=1469598103934665603ULL;mix(h,static_cast<std::uint64_t>(gid));
        mix(h,static_cast<std::uint64_t>(domain));mix(h,ghost);
        for(GlobalId v:vertices) {
            if(!point_ids.count(v) || !unique.insert(v).second)
                throw std::runtime_error("invalid adjacency element connectivity");
            mix(h,static_cast<std::uint64_t>(v));
        }
        element_sum+=h;element_xor^=h;++elements;ghosts+=ghost;
    }
    template<class Profiler> void report(Profiler &p) const {
        p.set_metric("quality_adjacency_points",points);
        p.set_metric("quality_adjacency_elements",elements);
        p.set_metric("quality_adjacency_ghosts",ghosts);
        const auto fingerprint=[&](const char *name,std::uint64_t value) {
            p.set_metric(std::string("quality_adjacency_")+name+"_hi",static_cast<double>(value>>32));
            p.set_metric(std::string("quality_adjacency_")+name+"_lo",static_cast<double>(value&0xffffffffULL));
        };
        fingerprint("point_sum",point_sum);fingerprint("point_xor",point_xor);
        fingerprint("element_sum",element_sum);fingerprint("element_xor",element_xor);
    }
};
}
