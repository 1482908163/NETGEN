#pragma once
// Include after the nglib declarations. No additional collectives/barriers.
#include "volume_audit.h"
#include "mpi_debug.h"
namespace mesh_audit {
struct NgAccess {
    nglib::Ng_Mesh *mesh;
    int np() const {return nglib::Ng_GetNP(mesh);}
    int ne() const {return nglib::Ng_GetNE(mesh);}
    int nse() const {return nglib::Ng_GetNSE(mesh);}
    std::array<double,3> point(int i) const {
        std::array<double,3> p{};nglib::Ng_GetPoint(mesh,i,p.data());return p;
    }
    bool tetrahedron(int i,int *v) const {
        int domain=0;return nglib::Ng_GetTetrahedronConnectivity(mesh,i,v,domain)==nglib::NG_OK;
    }
    bool triangle(int i,int *v) const {return nglib::Ng_GetTriangleConnectivity(mesh,i,v)==nglib::NG_OK;}
};
inline void emit(MPI_Comm comm,const char *stage,const std::string &fields) {
    int rank=-1;MPI_Comm_rank(comm,&rank);
    char host[MPI_MAX_PROCESSOR_NAME+1]{};int length=0;MPI_Get_processor_name(host,&length);
    // MPI processor names are opaque: JSON-escape them (including controls).
    std::ostringstream name;
    for(int i=0;i<length;++i) {
        unsigned char c=host[i];
        if(c=='"'||c=='\\') name<<'\\'<<char(c);
        else if(c<32) name<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<int(c);
        else name<<char(c);
    }
    const std::string line="[MESH_EVIDENCE] {\"rank\":"+std::to_string(rank)+
        ",\"host\":\""+name.str()+"\",\"stage\":\""+stage+"\","+fields+"}\n";
    std::fwrite(line.data(),1,line.size(),stderr);std::fflush(stderr);
}
[[noreturn]] inline void reject(MPI_Comm comm,const char *stage,int entity,const int *v,int n,int np) {
    std::ostringstream out;out<<"\"kind\":\"invalid_connectivity\",\"entity\":"<<entity
        <<",\"points\":"<<np<<",\"ids\":[";
    for(int k=0;k<n;++k) out<<(k?",":"")<<v[k];
    out<<']';
    emit(comm,stage,out.str());MPI_Abort(comm,86);std::abort();
}
inline Connectivity checkpoint(NgAccess mesh,MPI_Comm comm,const char *stage,
                               const Connectivity *expected=nullptr) {
    const auto c=connectivity(mesh);
    std::ostringstream out;out<<"\"kind\":\"checkpoint\",\"points\":"<<c.points
        <<",\"elements\":"<<c.elements<<",\"surfaces\":"<<c.surfaces
        <<",\"invalid\":"<<c.invalid<<",\"unsupported\":"<<c.unsupported
        <<",\"point_hash\":\""<<std::hex<<c.point_hash<<"\",\"index_hash\":\""<<c.index_hash<<"\"";
    if(expected) out<<",\"unchanged\":"<<((*expected==c)?"true":"false");
    emit(comm,stage,out.str());
    for(const auto &s:c.samples) {
        std::ostringstream sample;sample<<"\"kind\":\""<<s.kind<<"\",\"entity\":"<<s.entity
            <<",\"ids\":["<<s.ids[0]<<','<<s.ids[1]<<','<<s.ids[2]<<','<<s.ids[3]<<']';
        emit(comm,stage,sample.str());
    }
    if(c.invalid || c.unsupported || (expected && !(*expected==c))) {
        MPI_Abort(comm,86);std::abort();
    }
    return c;
}
inline void report_evidence(NgAccess mesh,MPI_Comm comm,const Result &r) {
    if(!r.hard_errors()) return;
    std::ostringstream out;out<<"\"kind\":\"quality_failure\",\"hard_errors\":"<<r.hard_errors()
        <<",\"invalid_indices\":"<<r.invalid_indices<<",\"same_side_faces\":"<<r.same_side_faces
        <<",\"missing_surface\":"<<r.missing_surface<<",\"volume_sum\":\""<<std::hex<<r.volume_sum
        <<"\",\"volume_xor\":\""<<r.volume_xor<<"\"";
    emit(comm,"volume_audit",out.str());
    evidence(mesh,r,[&](const std::string &s){emit(comm,"volume_audit",s);});
}
} // namespace mesh_audit
