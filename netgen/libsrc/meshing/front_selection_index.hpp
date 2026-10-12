#pragma once
#include <algorithm>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

namespace netgen {
// Face order is semantic: first qualifying suffix, otherwise LAST global
// minimum. Blocks only discard ranges that provably cannot contain that face.
class FrontSelectionIndex {
  static constexpr int width=64;
  std::vector<long long> minimum;
  std::vector<unsigned char> dirty;
  int count=0;
public:
  static constexpr long long inactive=std::numeric_limits<long long>::max();
  void Reset(int n) {if(n<0)throw std::invalid_argument("negative front size");count=n;minimum.assign(n?1+(n-1)/width:0,inactive);dirty.assign(minimum.size(),1);}
  void Grow(int n) {
    if(n<count) {Reset(n);return;}
    if(n==count)return;
    auto old=count;count=n;minimum.resize(n?1+(n-1)/width:0,inactive);dirty.resize(minimum.size(),1);
    if(old && old%width)dirty[(old-1)/width]=1;
  }
  void Changed(int face) {if(face>0 && face<=count)dirty[(face-1)/width]=1;}
  template<class Score> void Refresh(Score score,double *counters) {
    for(std::size_t b=0;b<dirty.size();++b)if(dirty[b]) {
      minimum[b]=inactive;
      int lo=static_cast<int>(b)*width,hi=lo+std::min(width,count-lo);
      for(long long f=static_cast<long long>(lo)+1;f<=hi;++f) {
        minimum[b]=std::min(minimum[b],score(f));if(counters)++counters[2];
      }
      dirty[b]=0;if(counters)++counters[3];
    }
  }
  template<class Score> int First(int from,long long limit,Score score,double *counters) {
    Refresh(score,counters);
    for(int b=std::max(0,(from-1)/width);b<static_cast<int>(minimum.size());++b) {
      if(counters)++counters[4];
      if(minimum[b]>limit)continue;
      int lo=b*width,hi=lo+std::min(width,count-lo);
      for(long long f=std::max(from,lo+1);f<=hi;++f)
        if(score(f)<=limit)return f;
    }
    return 0;
  }
  template<class Score> int LastMinimum(Score score,double *counters) {
    Refresh(score,counters);long long value=inactive;
    for(auto x:minimum)value=std::min(value,x);
    if(value==inactive)return 0;
    for(int b=static_cast<int>(minimum.size())-1;b>=0;--b) {
      if(counters)++counters[4];
      if(minimum[b]!=value)continue;
      int lo=b*width,hi=lo+std::min(width,count-lo);
      for(int f=hi;f>=lo+1;--f)if(score(f)==value)return f;
    }
    return 0;
  }
};
}
