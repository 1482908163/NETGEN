#pragma once
#include <algorithm>
#include <array>
#include <cstring>
#include <cstdint>
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
// CSR vertex->peer mentions and a compact selected-element schedule. Scan the
// original tets once, count each destination, then materialize its exact-size
// packet vector. Repeated mentions retain their weight; each peer's packet
// order is the original element order, independent of touched-peer order.
template<class Packet,class Access,class Adjacency,class Ids>
std::map<int,std::vector<Packet>> PackBoundaryCompact(const Access &mesh,const Adjacency &adj,
    const Ids &point_ids,const Ids &element_ids,int rank,int ranks,double *v) {
  const int np=mesh.np(),ne=mesh.ne();
  if(np<0 || ne<0 || ranks<1 || rank<0 || rank>=ranks)throw std::logic_error("ghost boundary mesh range");
  std::vector<std::size_t> offsets(static_cast<std::size_t>(np)+2,0);
  std::vector<int> peers;
  for(const auto &entry:adj) {
    if(entry.first<1 || entry.first>np)throw std::logic_error("ghost boundary vertex range");
    offsets[static_cast<std::size_t>(entry.first)+1]=entry.second.size();
  }
  for(std::size_t i=1;i<offsets.size();++i) {
    if(offsets[i]>std::numeric_limits<std::size_t>::max()-offsets[i-1])throw std::overflow_error("ghost adjacency capacity");
    offsets[i]+=offsets[i-1];
  }
  peers.resize(offsets.back());
  for(const auto &entry:adj) {
    auto at=offsets[entry.first];
    for(int peer:entry.second) {
      if(peer<0 || peer>=ranks || peer==rank)throw std::logic_error("ghost boundary peer range");
      peers[at++]=peer;
    }
  }
  struct Selected {int element,domain;std::array<int,4> vertices;std::size_t end;};
  std::vector<Selected> selected;
  std::vector<int> recipients,counts(ranks,0),touched;
  std::vector<std::size_t> sizes(ranks,0);
  for(int ordinal=0;ordinal<ne;++ordinal) {
    const int e=ordinal+1;
    Selected item;item.element=e;mesh.tetrahedron(e,item.vertices.data(),item.domain);++v[1];
    // Saturation avoids size overflow; only the predicate >=3 is needed.
    std::size_t mentions=0;
    for(int p:item.vertices) {
      if(p<1 || p>np)throw std::logic_error("ghost pack vertex range");
      mentions+=std::min<std::size_t>(3,offsets[static_cast<std::size_t>(p)+1]-offsets[p]);
    }
    if(mentions<3)continue;
    ++v[2];touched.clear();
    for(int p:item.vertices)for(auto i=offsets[p];i<offsets[static_cast<std::size_t>(p)+1];++i) {
      const int peer=peers[i];if(!counts[peer])touched.push_back(peer);
      if(counts[peer]==std::numeric_limits<int>::max())throw std::overflow_error("ghost peer multiplicity");
      ++counts[peer];
    }
    const auto begin=recipients.size();
    for(int peer:touched) {
      const int count=counts[peer];counts[peer]=0;if(count<3)continue;
      if(sizes[peer]>=static_cast<std::size_t>(std::numeric_limits<int>::max()))throw std::overflow_error("ghost pack count");
      ++sizes[peer];recipients.push_back(peer);++v[4];
    }
    if(begin!=recipients.size()){item.end=recipients.size();selected.push_back(item);++v[3];}
  }
  std::map<int,std::vector<Packet>> result;
  std::vector<std::vector<Packet>*> output(ranks,nullptr);
  for(int peer=0;peer<ranks;++peer)if(sizes[peer]) {
    auto &packets=result[peer];packets.reserve(sizes[peer]);output[peer]=&packets;
  }
  std::size_t at=0;
  for(const auto &item:selected) {
    Packet packet{};packet.gid=element_ids[item.element];packet.domidx=item.domain;
    for(int k=0;k<4;++k){packet.Pindex[k]=point_ids[item.vertices[k]];mesh.point(item.vertices[k],packet.Vertexs[k].xyz);}
    for(;at<item.end;++at)output[recipients[at]]->push_back(packet);
  }
  return result;
}

// Flat open addressing: zero local index is the empty marker, so every 64-bit
// ID (including 0, negative values and INT64_MIN/MAX) remains representable.
// No deletion; grow below 50% occupancy. Existing duplicate IDs overwrite the
// value in insertion order, exactly like std::map/unordered_map.
template<class Id> class GhostFlatIndex {
  static_assert(std::is_integral<Id>::value && sizeof(Id)<=8,"integral ghost ID required");
  std::vector<Id> keys;
  std::vector<int> values;
  std::size_t used=0;
  static std::uint64_t Hash(Id id) {
    std::uint64_t x=static_cast<std::uint64_t>(id)+UINT64_C(0x9e3779b97f4a7c15);
    x=(x^(x>>30))*UINT64_C(0xbf58476d1ce4e5b9);
    x=(x^(x>>27))*UINT64_C(0x94d049bb133111eb);return x^(x>>31);
  }
  std::size_t Slot(Id id)const {
    std::size_t i=Hash(id)&(values.size()-1);
    while(values[i] && keys[i]!=id)i=(i+1)&(values.size()-1);
    return i;
  }
public:
  void reserve(std::size_t n) {
    if(n<=values.size()/2)return;
    std::size_t capacity=8;
    while(capacity/2<n) {
      if(capacity>std::min(keys.max_size(),values.max_size())/2)throw std::overflow_error("ghost flat index capacity");
      capacity*=2;
    }
    std::vector<Id> next_keys(capacity);
    std::vector<int> next_values(capacity,0);
    keys.swap(next_keys);values.swap(next_values);
    for(std::size_t i=0;i<next_values.size();++i)if(next_values[i]) {
      const auto at=Slot(next_keys[i]);keys[at]=next_keys[i];values[at]=next_values[i];
    }
  }
  int Find(Id id)const {return values.empty()?0:values[Slot(id)];}
  void Set(Id id,int local) {
    if(local<=0)throw std::logic_error("ghost flat local index");
    if(values.empty())reserve(1);
    auto at=Slot(id);
    if(!values[at]) {
      if(used==values.size()/2){reserve(used+1);at=Slot(id);}
      keys[at]=id;++used;
    }
    values[at]=local;
  }
};
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
    constexpr bool flat=std::is_same<Lookup,GhostFlatIndex<Id>>::value;
    if constexpr(flat || std::is_same<Lookup,std::unordered_map<Id,int>>::value)index.reserve(static_cast<std::size_t>(oldnp));
    for(int i=1;i<=oldnp;++i) {
      if constexpr(flat)index.Set(oldids[i],i);else index[oldids[i]]=i;
    }
    std::size_t total=0;for(int n:counts){if(n<0 || static_cast<std::size_t>(n)>static_cast<std::size_t>(std::numeric_limits<int>::max())-total)throw std::overflow_error("ghost assembly count");total+=n;}
    plan.elements.reserve(total);
    for(std::size_t peer=0;peer<buffers.size();++peer)for(int j=0;j<counts[peer];++j) {
      if(!buffers[peer])throw std::logic_error("ghost assembly null receive");
      const auto &packet=buffers[peer][j];std::array<int,4> pi;
      for(int k=0;k<4;++k) {
        int found=0;
        if constexpr(flat)found=index.Find(packet.Pindex[k]);
        else {auto it=index.find(packet.Pindex[k]);if(it!=index.end())found=it->second;}
        if(!found) {
          if(plan.points.size()>=static_cast<std::size_t>(std::numeric_limits<int>::max()-oldnp))throw std::overflow_error("ghost assembly point capacity");
          int local=oldnp+static_cast<int>(plan.points.size())+1;
          Point point;point.gid=packet.Pindex[k];std::memcpy(point.xyz.data(),packet.Vertexs[k].xyz,3*sizeof(double));
          plan.points.push_back(point);
          if constexpr(flat)index.Set(point.gid,local);else index[point.gid]=local;
          pi[k]=local;
        }else pi[k]=found;
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
