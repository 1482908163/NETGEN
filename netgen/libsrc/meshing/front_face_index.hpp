#ifndef NETGEN_FRONT_FACE_INDEX_HPP
#define NETGEN_FRONT_FACE_INDEX_HPP
#include <algorithm>
#include <vector>
#include <utility>

namespace netgen {
// Exact incidence index of an immutable local front. Face and slot are 1-based.
// Next preserves the exhaustive face-major, rotation-minor traversal order.
class FrontFaceIndex {
  struct Occurrence { int face, slot, arity; };
  std::vector<std::vector<Occurrence>> points;
  int face_count;
public:
  FrontFaceIndex(int point_count, int faces):points(point_count),face_count(faces) {}
  void Add(int point,int face,int slot,int arity) {
    points.at(point).push_back({face,slot,arity}); // Add faces in increasing order.
  }
  std::pair<int,int> Next(int point,int arity,int position,int after_face,int after_rotation) const {
    std::pair<int,int> best{face_count+1,1};
    const auto & entries=points.at(point);
    auto it=std::lower_bound(entries.begin(),entries.end(),after_face,
        [](const Occurrence & e,int f){return e.face<f;});
    for(;it!=entries.end();++it) {
      if(it->face>best.first)break;
      if(it->arity!=arity)continue;
      int rotation=(it->slot-position)%arity;
      if(rotation<=0)rotation+=arity;
      std::pair<int,int> candidate{it->face,rotation};
      if(candidate>std::make_pair(after_face,after_rotation) && candidate<best)best=candidate;
    }
    return best;
  }
};
}
#endif
