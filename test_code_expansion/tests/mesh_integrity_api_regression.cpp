#include <meshing.hpp>
namespace nglib {
#include "../../netgen/nglib/nglib.h"
}
#include "../mesh_occ_mpi/ng_mesh_audit.h"
#include <stdexcept>
#include <iostream>
int main(int argc,char **argv) {
    MPI_Init(&argc,&argv);
    auto require=[](bool ok){if(!ok) throw std::runtime_error("bounded connectivity API");};
    netgen::Mesh mesh;
    mesh.AddFaceDescriptor(netgen::FaceDescriptor(1,1,0,1));
    mesh.AddPoint(netgen::Point<3>(0,0,0));mesh.AddPoint(netgen::Point<3>(1,0,0));
    mesh.AddPoint(netgen::Point<3>(0,1,0));mesh.AddPoint(netgen::Point<3>(0,0,1));
    netgen::Element tet(netgen::TET);tet.SetIndex(7);
    for(int i=0;i<4;++i) tet[i]=i+1;mesh.AddVolumeElement(tet);
    netgen::Element quadratic(netgen::TET10);mesh.AddVolumeElement(quadratic);
    netgen::Element hex(netgen::HEX);mesh.AddVolumeElement(hex);
    netgen::Element2d triangle(netgen::TRIG);triangle.SetIndex(1);for(int i=0;i<3;++i) triangle[i]=i+1;
    mesh.AddSurfaceElement(triangle);
    netgen::Element2d quad(netgen::QUAD);quad.SetIndex(1);mesh.AddSurfaceElement(quad);
    auto ngmesh=reinterpret_cast<nglib::Ng_Mesh*>(&mesh);
    int v[5]={0,0,0,0,76543},domain=-1;
    require(nglib::Ng_GetTetrahedronConnectivity(ngmesh,1,v,domain)==nglib::NG_OK);
    require(domain==7 && v[0]==1 && v[3]==4 && v[4]==76543);
    for(int n:{-1,0,2,3,4}) {
        require(nglib::Ng_GetTetrahedronConnectivity(ngmesh,n,v,domain)==nglib::NG_ERROR);
        require(v[4]==76543 && v[0]==0 && domain==0);
    }
    v[3]=23456;require(nglib::Ng_GetTriangleConnectivity(ngmesh,1,v)==nglib::NG_OK);
    require(v[2]==3 && v[3]==23456);
    for(int n:{-1,0,2,3}) {
        require(nglib::Ng_GetTriangleConnectivity(ngmesh,n,v)==nglib::NG_ERROR);require(v[3]==23456);
    }
    mesh.VolumeElements().SetSize(1);mesh.SurfaceElements().SetSize(1);
    auto baseline=mesh_audit::checkpoint({ngmesh},MPI_COMM_WORLD,"fixture_before");
    if(argc>1 && std::string(argv[1])=="invalid") mesh.VolumeElement(1)[0]=99;
    if(argc>1 && std::string(argv[1])=="changed") mesh.Point(1)(0)=0.25;
    mesh_audit::checkpoint({ngmesh},MPI_COMM_WORLD,"fixture_after",&baseline);
    MPI_Finalize();std::cout<<"通过：真实内核有界读取、拒绝高阶/非四面体、哨兵未越界及阶段指纹。\n";
}
