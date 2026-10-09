#ifndef NETGEN_SPLIT_ACTIVE_QUEUE_HPP
#define NETGEN_SPLIT_ACTIVE_QUEUE_HPP
#include <atomic>
#include <cstring>
#include <vector>
namespace netgen {
// Stable compaction: candidate identities never depend on worker completion.
inline std::vector<size_t> ActiveSplitIndices(const std::vector<unsigned char>& mask) {
  std::vector<size_t> active;
  for(size_t i=0;i<mask.size();++i)if(mask[i])active.push_back(i);
  return active;
}
template<class F> void ClaimActiveSplitIndices(std::atomic<size_t>& next,
    const std::vector<size_t>& active,F evaluate) {
  while(true) {
    size_t k=next.fetch_add(1,std::memory_order_relaxed);
    if(k>=active.size())return;
    evaluate(active[k]);
  }
}
inline bool ExactSplitValues(const std::vector<double>& a,const std::vector<double>& b) {
  return a.size()==b.size() && (a.empty() || std::memcmp(a.data(),b.data(),a.size()*sizeof(double))==0);
}
}
#endif
