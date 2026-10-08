#pragma once
#include <array>
#include <atomic>
#include <cstring>
#include <type_traits>
#include <vector>

namespace netgen {
struct VolumeRepairFixedPointStats {
  static constexpr int width=10,count=3*width;
  enum {calls,rounds,checks,stable_rounds,potential_skipped,skipped,
        verified,mismatches,snapshot_seconds,reference_rounds};
  std::array<std::atomic<double>,count> values{};
  VolumeRepairFixedPointStats() {for(auto &v:values)v.store(0);}
  void Add(int phase,int field,double value=1) {
    auto &target=values[phase*width+field];double old=target.load();
    while(!target.compare_exchange_weak(old,old+value)) {}
  }
};

// Exact object representations, not hashes. Padding can cause a conservative
// false miss, never a false match. The repair operators rebuild incidence and
// boundary caches from these ordered primary records; timestamps are not state.
// Captures flags/badness, point attributes, boundary geometry, open and locked
// records as well as connectivity. Requires fixed-size value records.
class RepairMeshState {
  std::vector<unsigned char> bytes;
  template<class T> void Append(const T &value) {
    static_assert(std::is_trivially_copyable_v<T>,"repair state needs value records");
    const auto *p=reinterpret_cast<const unsigned char*>(&value);
    bytes.insert(bytes.end(),p,p+sizeof(T));
  }
  template<class R> void AppendRange(const R &range) {
    const size_t size=range.Size();Append(size);
    for(const auto &value:range)Append(value);
  }
public:
  template<class M> explicit RepairMeshState(const M &mesh) {
    AppendRange(mesh.Points());AppendRange(mesh.VolumeElements());
    AppendRange(mesh.SurfaceElements());AppendRange(mesh.LineSegments());
    AppendRange(mesh.OpenElements());AppendRange(mesh.LockedPoints());
    const size_t point_elements=mesh.pointelements.Size();Append(point_elements);
    for(const auto &value:mesh.pointelements) {
      if constexpr(std::is_trivially_copyable_v<std::decay_t<decltype(value)>>)Append(value);
      else {
        Append(value.pnum);Append(value.index);
        const size_t length=value.name.size();Append(length);
        bytes.insert(bytes.end(),value.name.begin(),value.name.end());
      }
    }
  }
  template<class M> bool Equal(const M &mesh) const {
    return bytes==RepairMeshState(mesh).bytes;
  }
};
}
