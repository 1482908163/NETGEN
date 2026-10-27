#include "../mesh_occ_mpi/volume_audit.h"
#include <map>
#include <iostream>
#include <stdexcept>
struct Fixture {
    std::vector<std::array<double,3>> p{{{0,0,0}},{{1,0,0}},{{0,1,0}},{{0,0,1}},{{0,0,-1}}};
    std::vector<std::array<int,4>> t{{{1,2,3,4}},{{1,3,2,5}}};
    std::vector<std::array<int,3>> s;
    Fixture() {
        std::map<std::array<int,3>,int> faces;
        for(auto v:t) for(int f=0;f<4;++f) {
            std::array<int,3> a{};int j=0;for(int k=0;k<4;++k) if(k!=f) a[j++]=v[k];
            std::sort(a.begin(),a.end());++faces[a];
        }
        for(auto &f:faces) if(f.second==1) s.push_back(f.first);
    }
    int np() const {return int(p.size());} int ne() const {return int(t.size());} int nse() const {return int(s.size());}
    auto point(int i) const {return p.at(i-1);}
    bool tetrahedron(int i,int *v) const {std::copy(t.at(i-1).begin(),t.at(i-1).end(),v);return true;}
    bool triangle(int i,int *v) const {std::copy(s.at(i-1).begin(),s.at(i-1).end(),v);return true;}
};
void require(bool ok) {if(!ok) throw std::runtime_error("volume audit regression");}
int main() {
    Fixture m;auto baseline=mesh_audit::inspect(m);require(baseline.hard_errors()==0);
    auto before=mesh_audit::connectivity(m);require(before.invalid==0 && before.unsupported==0);
    int permutations=0;
    do {
        auto r=mesh_audit::inspect(m);require(r.same_side_faces==0 && r.missing_surface==0);
        require(r.volume_sum==baseline.volume_sum && r.volume_xor==baseline.volume_xor);++permutations;
    } while(std::next_permutation(m.t[0].begin(),m.t[0].end()));
    require(permutations==24);
    std::swap(m.t[0][0],m.t[0][1]);require(!(before==mesh_audit::connectivity(m)));
    std::swap(m.t[0][0],m.t[0][1]);require(before==mesh_audit::connectivity(m));
    m.p[4][2]=2;auto bad=mesh_audit::inspect(m);require(bad.same_side_faces==1);
    int samples=0,incidents=0;
    mesh_audit::evidence(m,bad,[&](const std::string &s){
        ++samples;if(s.find("incident_tetrahedron")!=std::string::npos) ++incidents;
        std::cout<<'{'<<s<<"}\n";
    });
    require(samples==3 && incidents==2);require(!(before==mesh_audit::connectivity(m)));
    m.t[0][0]=0;m.t[1][3]=6;m.s[0][0]=-1;
    bad=mesh_audit::inspect(m);require(bad.invalid_indices==3);
    auto c=mesh_audit::connectivity(m);require(c.invalid==3 && c.samples.size()==3);
    m.t.resize(100,m.t[0]);c=mesh_audit::connectivity(m);require(c.invalid==101 && c.samples.size()==8);
    require(!mesh_audit::valid_indices(m.t[0].data(),4,m.np()));
    std::cout<<"通过：24种顶点排列、几何不变/编号变化、同侧面及邻接单元证据、坏索引与样本上限。\n";
}
