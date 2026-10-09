#include "split_active_queue.hpp"
#include <cassert>
#include <cmath>
#include <limits>
#include <thread>
#include <algorithm>
using namespace netgen;
int main() {
  for(size_t count:{size_t(0),size_t(1),size_t(257),size_t(65537)}) {
    std::vector<unsigned char> mask(count,0);
    for(size_t i=0;i<count;++i)mask[i]=(i%7==0 || i%13==0);
    auto active=ActiveSplitIndices(mask);
    assert(std::is_sorted(active.begin(),active.end()));
    for(int workers:{1,2,4,11}) {
      std::vector<std::atomic<int>> visits(count);
      for(auto& v:visits)v=0;
      std::atomic<size_t> next{0};std::vector<std::thread> threads;
      for(int w=0;w<workers;++w)threads.emplace_back([&]{
        ClaimActiveSplitIndices(next,active,[&](size_t i){++visits[i];});
      });
      for(auto& t:threads)t.join();
      for(size_t i=0;i<count;++i)assert(visits[i]==int(mask[i]));
    }
  }
  std::vector<double> a{0.0,-1.0,1.0},b=a;
  assert(ExactSplitValues(a,b));b[0]=-0.0;assert(!ExactSplitValues(a,b));
  b=a;b[2]=std::nextafter(b[2],2.0);assert(!ExactSplitValues(a,b));
  b=a;b.pop_back();assert(!ExactSplitValues(a,b));
  assert(ExactSplitValues({},{}));
}
