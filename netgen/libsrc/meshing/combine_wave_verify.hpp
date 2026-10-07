#pragma once
#include <algorithm>
#include <cstring>
#include <vector>

namespace netgen {
template<class T> inline bool CavitySameBits(const T &a,const T &b) {
    return std::memcmp(&a,&b,sizeof(T))==0;
}
// Warmup-only state of the actual production function's complete read/write
// footprint. Boundary hash tables were built before the transaction and are
// read-only. No allocation, compression or incidence update occurs in a wave.
struct CombineWaveState {
    struct PointState {PointIndex id;MeshPoint value;bool removed;};
    struct CellState {ElementIndex id;Element value;};
    std::vector<PointState> points;
    std::vector<CellState> cells;
    CombineWaveState(Mesh &mesh,FlatArray<bool,PointIndex> removed,
                     const std::vector<int> &ids,std::vector<ElementIndex> elements) {
        for(auto id:ids) {
            PointIndex pi(id);points.push_back({pi,mesh[pi],bool(removed[pi])});
        }
        std::sort(elements.begin(),elements.end());
        elements.erase(std::unique(elements.begin(),elements.end()),elements.end());
        for(auto ei:elements)cells.push_back({ei,mesh[ei]});
    }
    void Restore(Mesh &mesh,FlatArray<bool,PointIndex> removed) const {
        for(auto &p:points) {mesh[p.id]=p.value;removed[p.id]=p.removed;}
        for(auto &e:cells)mesh[e.id]=e.value;
    }
    bool Equal(Mesh &mesh,FlatArray<bool,PointIndex> removed) {
        for(auto &p:points) {
            auto &v=mesh[p.id];auto &w=p.value;
            if(removed[p.id]!=p.removed || v.Type()!=w.Type() ||
               v.GetLayer()!=w.GetLayer() || !CavitySameBits(v.Singularity(),w.Singularity()))return false;
            for(int j=0;j<3;++j)if(!CavitySameBits(v(j),w(j)))return false;
        }
        for(auto &e:cells) {
            auto &v=mesh[e.id];auto &w=e.value;
            if(v.GetType()!=w.GetType() || v.GetNP()!=w.GetNP() || v.GetIndex()!=w.GetIndex())return false;
            for(int j=0;j<v.GetNP();++j)if(v[j]!=w[j])return false;
            const auto &a=v.Flags();const auto &b=w.Flags();
            if(a.refflag!=b.refflag || a.marked!=b.marked || a.badel!=b.badel ||
               a.reverse!=b.reverse || a.illegal!=b.illegal || a.illegal_valid!=b.illegal_valid ||
               a.badness_valid!=b.badness_valid || a.strongrefflag!=b.strongrefflag ||
               a.deleted!=b.deleted || a.fixed!=b.fixed)return false;
            if(v.BadnessValid() && !CavitySameBits(v.GetBadness(),w.GetBadness()))return false;
        }
        return true;
    }
};
}
