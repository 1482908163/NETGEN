#ifndef NETGEN_DETERMINISTIC_TET_REFINE_HPP
#define NETGEN_DETERMINISTIC_TET_REFINE_HPP
#include <algorithm>
#include <array>
#include <cstddef>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <vector>

namespace netgen_refine {
using Tet=std::array<int,4>;
struct Edge { int a,b,mid; };
struct Occurrence { int a,b; std::size_t slot; };
struct NewEdge { int a,b,mid; std::size_t first; };
struct Plan {
  std::vector<int> midpoints; // six entries per input tet
  std::vector<NewEdge> added; // sorted by endpoint pair
  std::vector<std::size_t> point_order; // original first-encounter order
  std::size_t buffer_bytes=0;
};
inline constexpr int endpoints[6][2]={{0,1},{0,2},{0,3},{1,2},{1,3},{2,3}};
inline constexpr int children[8][4]={{0,4,5,6},{4,1,7,8},{5,7,2,9},{6,8,9,3},
                                  {4,5,6,8},{4,5,8,7},{5,6,8,9},{5,7,9,8}};
inline bool less_edge(int a,int b,int c,int d) { return a<c || (a==c && b<d); }

// Execute(size, fn) must join all workers before returning. Planning reads a
// fixed input snapshot and writes disjoint occurrence slots; no mesh writes.
template<class Execute>
Plan build(const std::vector<Tet> &tets,int points,const std::vector<Edge> &known,
           int workers,Execute execute) {
  if(points<0 || workers<1 || tets.size()>std::size_t(std::numeric_limits<int>::max()/8))
    throw std::invalid_argument("invalid refinement size or worker count");
  for(std::size_t i=0;i<known.size();++i) {
    const auto &e=known[i];
    if(e.a<1 || e.b<=e.a || e.b>points || e.mid<1 || e.mid>points ||
       (i && !less_edge(known[i-1].a,known[i-1].b,e.a,e.b)))
      throw std::invalid_argument("invalid or unsorted known edge map");
  }
  for(const auto &t:tets)for(int j=0;j<4;++j) {
    if(t[j]<1 || t[j]>points)throw std::invalid_argument("invalid tetrahedron point");
    for(int k=0;k<j;++k)if(t[k]==t[j])throw std::invalid_argument("degenerate tetrahedron connectivity");
  }
  std::vector<Occurrence> edges(6*tets.size());
  execute(tets.size(),[&](std::size_t i) {
    for(int k=0;k<6;++k) {
      int a=tets[i][endpoints[k][0]],b=tets[i][endpoints[k][1]];
      if(a>b)std::swap(a,b);
      edges[6*i+k]={a,b,6*i+std::size_t(k)};
    }
  });
  auto order=[](const Occurrence &a,const Occurrence &b) {
    return a.a!=b.a ? a.a<b.a : a.b!=b.b ? a.b<b.b : a.slot<b.slot;
  };
  const std::size_t chunks=std::max<std::size_t>(1,std::min<std::size_t>(workers,edges.size()));
  auto cut=[&](std::size_t i){return edges.size()*i/chunks;};
  execute(chunks,[&](std::size_t i){std::sort(edges.begin()+cut(i),edges.begin()+cut(i+1),order);});
  for(std::size_t width=1;width<chunks;width*=2) {
    const std::size_t groups=(chunks+2*width-1)/(2*width);
    execute(groups,[&](std::size_t i) {
      std::size_t a=i*2*width,b=std::min(a+width,chunks),c=std::min(a+2*width,chunks);
      std::inplace_merge(edges.begin()+cut(a),edges.begin()+cut(b),edges.begin()+cut(c),order);
    });
  }
  Plan plan;plan.midpoints.resize(edges.size());
  std::size_t cursor=0;
  for(std::size_t i=0;i<edges.size();) {
    std::size_t end=i+1;
    while(end<edges.size() && edges[end].a==edges[i].a && edges[end].b==edges[i].b)++end;
    const auto &e=edges[i];
    while(cursor<known.size() && less_edge(known[cursor].a,known[cursor].b,e.a,e.b))++cursor;
    int id;
    if(cursor<known.size() && known[cursor].a==e.a && known[cursor].b==e.b)id=known[cursor].mid;
    else {
      if(plan.added.size()>=std::size_t(std::numeric_limits<int>::max()-points))
        throw std::overflow_error("refined point count exceeds local index capacity");
      plan.added.push_back({e.a,e.b,0,e.slot});
      id=-int(plan.added.size());
    }
    for(std::size_t j=i;j<end;++j)plan.midpoints[edges[j].slot]=id;
    i=end;
  }
  plan.point_order.resize(plan.added.size());
  std::iota(plan.point_order.begin(),plan.point_order.end(),0);
  std::sort(plan.point_order.begin(),plan.point_order.end(),[&](std::size_t a,std::size_t b){return plan.added[a].first<plan.added[b].first;});
  for(std::size_t i=0;i<plan.point_order.size();++i)plan.added[plan.point_order[i]].mid=points+int(i)+1;
  execute(plan.midpoints.size(),[&](std::size_t i){int id=plan.midpoints[i];if(id<0)plan.midpoints[i]=plan.added[std::size_t(-id-1)].mid;});
  // Explicit plan buffers only; excludes merge scratch, mesh and caller buffers.
  plan.buffer_bytes=edges.capacity()*sizeof(Occurrence)+plan.midpoints.capacity()*sizeof(int)+
                    plan.added.capacity()*sizeof(NewEdge)+plan.point_order.capacity()*sizeof(std::size_t);
  return plan;
}
inline Tet child(const Tet &tet,const Plan &plan,std::size_t parent,int number) {
  int p[10]={tet[0],tet[1],tet[2],tet[3]};
  for(int k=0;k<6;++k)p[k+4]=plan.midpoints[6*parent+k];
  Tet out{};for(int k=0;k<4;++k)out[k]=p[children[number][k]];
  return out;
}
inline std::size_t child_slot(std::size_t parents,std::size_t parent,int number) {
  return number==0 ? parent : parents+7*parent+std::size_t(number-1);
}
}
#endif
