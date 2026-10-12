#pragma once
#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <map>
#include <stdexcept>
#include <type_traits>
#include <unordered_map>
#include <vector>

namespace mesh_research {
// Dense adjacency lookup removes four tree probes and per-tet recipient maps.
// A >=3 recipient cannot be lost by the fewer-than-three-mentions filter.
// Count mentions, not distinct vertices: repeated adjacency entries retain
// their original multiplicity, even on degenerate input.
template<class Packet,class Access,class Adjacency,class Ids>
std::map<int,std::vector<Packet>> PackBoundary(const Access &mesh,const Adjacency &adj,
    const Ids &point_ids,const Ids &element_ids,int rank,int ranks,double *v) {
  if(mesh.np()<0 || mesh.ne()<0 || ranks<1 || rank<0 || rank>=ranks)throw std::logic_error("ghost boundary mesh range");
  std::vector<std::vector<int>> peers(static_cast<std::size_t>(mesh.np())+1);
  for(const auto &entry:adj) {
    if(entry.first<1 || entry.first>mesh.np())throw std::logic_error("ghost boundary vertex range");
    peers[entry.first].assign(entry.second.begin(),entry.second.end());
    for(int p:peers[entry.first])if(p<0 || p>=ranks || p==rank)throw std::logic_error("ghost boundary peer range");
  }
  std::map<int,std::vector<Packet>> result;
  std::vector<int> counts(ranks,0),touched;
  for(int e=1;e<=mesh.ne();++e) {
    int ids[4],domain;mesh.tetrahedron(e,ids,domain);++v[1];
    std::size_t boundary=0;for(int p:ids) {if(p<1 || p>mesh.np())throw std::logic_error("ghost pack vertex range");boundary+=peers[p].size();}
    if(boundary<3)continue;
    ++v[2];touched.clear();
    for(int p:ids)for(int peer:peers[p]) {if(!counts[peer])touched.push_back(peer);if(counts[peer]==std::numeric_limits<int>::max())throw std::overflow_error("ghost peer multiplicity");++counts[peer];}
    // Iterate the original peer order; packet/element order is never changed.
    std::sort(touched.begin(),touched.end());bool selected=false;Packet packet{};
    for(int peer:touched) {
      const int count=counts[peer];counts[peer]=0;
      if(count<3)continue;
      if(!selected) {
        packet.domidx=domain;packet.gid=element_ids[e];
        for(int k=0;k<4;++k){packet.Pindex[k]=point_ids[ids[k]];mesh.point(ids[k],packet.Vertexs[k].xyz);}
        ++v[3];selected=true;
      }
      auto &out=result[peer];if(out.size()>=static_cast<std::size_t>(std::numeric_limits<int>::max()))throw std::overflow_error("ghost pack count");
      out.push_back(packet);++v[4];
    }
  }
  return result;
}
template<class Packet> void SamePackets(const std::map<int,std::vector<Packet>>&a,
    const std::map<int,std::vector<Packet>>&b) {
  if(a.size()!=b.size())throw std::logic_error("ghost pack oracle peer mismatch");
  for(const auto &entry:a) {
    auto it=b.find(entry.first);if(it==b.end() || it->second.size()!=entry.second.size())throw std::logic_error("ghost pack oracle count mismatch");
    for(std::size_t i=0;i<entry.second.size();++i) {
      const auto &x=entry.second[i],&y=it->second[i];
      if(x.gid!=y.gid || x.domidx!=y.domidx)throw std::logic_error("ghost pack oracle element mismatch");
      for(int k=0;k<4;++k)if(x.Pindex[k]!=y.Pindex[k] || std::memcmp(x.Vertexs[k].xyz,y.Vertexs[k].xyz,3*sizeof(double)))throw std::logic_error("ghost pack oracle point mismatch");
    }
  }
}
template<class Id> struct GhostAssemblyPlan {
  struct Point {Id gid;std::array<double,3> xyz;};
  std::vector<Point> points;
  std::vector<std::array<int,4>> elements;
  // Existing duplicate IDs use the LAST old local index; new IDs use their
  // FIRST encounter's coordinates. This exactly matches the historical map.
  template<class Lookup,class Packet> static GhostAssemblyPlan Build(int oldnp,const Id *oldids,
      const std::vector<Packet*> &buffers,const std::vector<int>&counts) {
    if(oldnp<0 || (oldnp && !oldids) || buffers.size()!=counts.size())throw std::logic_error("ghost assembly input");
    Lookup index;GhostAssemblyPlan plan;
    if constexpr(std::is_same<Lookup,std::unordered_map<Id,int>>::value)index.reserve(static_cast<std::size_t>(oldnp));
    for(int i=1;i<=oldnp;++i)index[oldids[i]]=i;
    std::size_t total=0;for(int n:counts){if(n<0 || static_cast<std::size_t>(n)>static_cast<std::size_t>(std::numeric_limits<int>::max())-total)throw std::overflow_error("ghost assembly count");total+=n;}
    plan.elements.reserve(total);
    for(std::size_t peer=0;peer<buffers.size();++peer)for(int j=0;j<counts[peer];++j) {
      if(!buffers[peer])throw std::logic_error("ghost assembly null receive");
      const auto &packet=buffers[peer][j];std::array<int,4> pi;
      for(int k=0;k<4;++k) {
        auto it=index.find(packet.Pindex[k]);
        if(it==index.end()) {
          if(plan.points.size()>=static_cast<std::size_t>(std::numeric_limits<int>::max()-oldnp))throw std::overflow_error("ghost assembly point capacity");
          int local=oldnp+static_cast<int>(plan.points.size())+1;
          Point point;point.gid=packet.Pindex[k];std::memcpy(point.xyz.data(),packet.Vertexs[k].xyz,3*sizeof(double));
          plan.points.push_back(point);index[point.gid]=local;pi[k]=local;
        }else pi[k]=it->second;
      }
      plan.elements.push_back(pi);
    }
    return plan;
  }
  void Same(const GhostAssemblyPlan &other)const {
    if(elements!=other.elements || points.size()!=other.points.size())throw std::logic_error("ghost assembly oracle indices mismatch");
    for(std::size_t i=0;i<points.size();++i)if(points[i].gid!=other.points[i].gid || std::memcmp(points[i].xyz.data(),other.points[i].xyz.data(),3*sizeof(double)))throw std::logic_error("ghost assembly oracle points mismatch");
  }
};
}
