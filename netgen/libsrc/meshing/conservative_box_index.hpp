#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <numeric>
#include <vector>

namespace netgen {
// Broad phase only: exact predicates still decide acceptance. IDs are returned
// in input order so first-hit diagnostics and rule selection remain unchanged.
class ConservativeBoxIndex {
public:
  struct Box {std::array<double,3> lo,hi;};
private:
  struct Node {Box box;int begin,end,left=-1,right=-1;};
  std::vector<Box> boxes;
  std::vector<int> order;
  std::vector<Node> nodes;
  bool valid=true;
  static bool finite(const Box &b) {
    for(int k=0;k<3;++k)if(!std::isfinite(b.lo[k]) || !std::isfinite(b.hi[k]) || b.lo[k]>b.hi[k])return false;
    return true;
  }
  static bool overlaps(const Box &a,const Box &b) {
    for(int k=0;k<3;++k)if(a.hi[k]<b.lo[k] || b.hi[k]<a.lo[k])return false;
    return true;
  }
  int build(int begin,int end) {
    Box box=boxes[order[begin]];
    for(int i=begin+1;i<end;++i)for(int k=0;k<3;++k) {
      box.lo[k]=std::min(box.lo[k],boxes[order[i]].lo[k]);
      box.hi[k]=std::max(box.hi[k],boxes[order[i]].hi[k]);
    }
    const int node=static_cast<int>(nodes.size());nodes.push_back({box,begin,end});
    if(end-begin>8) {
      int axis=0;for(int k=1;k<3;++k)if(box.hi[k]-box.lo[k]>box.hi[axis]-box.lo[axis])axis=k;
      const int mid=begin+(end-begin)/2;
      std::nth_element(order.begin()+begin,order.begin()+mid,order.begin()+end,[&](int a,int b) {
        // Halve before addition to avoid overflow for finite coordinates.
        const double x=.5*boxes[a].lo[axis]+.5*boxes[a].hi[axis];
        const double y=.5*boxes[b].lo[axis]+.5*boxes[b].hi[axis];
        return x<y || (x==y && a<b);
      });
      const int left=build(begin,mid),right=build(mid,end);
      nodes[node].left=left;nodes[node].right=right;
    }
    return node;
  }
  void collect(int n,const Box &query,std::vector<int> &out) const {
    const auto &node=nodes[n];if(!overlaps(node.box,query))return;
    if(node.left<0) {
      for(int i=node.begin;i<node.end;++i)if(overlaps(boxes[order[i]],query))out.push_back(order[i]);
    } else {collect(node.left,query,out);collect(node.right,query,out);}
  }
public:
  ConservativeBoxIndex()=default;
  explicit ConservativeBoxIndex(std::vector<Box> input):boxes(std::move(input)),order(boxes.size()) {
    std::iota(order.begin(),order.end(),0);
    for(const auto &b:boxes)valid=valid && finite(b);
    if(valid && !boxes.empty())build(0,static_cast<int>(boxes.size()));
  }
  std::vector<int> query(const Box &box) const {
    std::vector<int> out;
    if(!valid || !finite(box)) {out.resize(boxes.size());std::iota(out.begin(),out.end(),0);return out;}
    if(!nodes.empty())collect(0,box,out);
    std::sort(out.begin(),out.end());return out;
  }
};
}
