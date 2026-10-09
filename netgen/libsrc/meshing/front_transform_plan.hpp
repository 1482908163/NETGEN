#pragma once
#include <cmath>
#include <cfenv>
#include <cstring>
#include <cstddef>

namespace netgen {
// Common-subexpression plan for immutable paired rule operators. The original
// DenseMatrix::Mult and vector blend kernels remain the only arithmetic path.
class FrontTransformPlan {
  int width=0,height=0;
  bool identical=true,finite=true;
public:
  template<class First,class Second>
  FrontTransformPlan(int rows,int columns,First first,Second second)
    :width(columns),height(rows) {
    for(int i=0;i<height;++i)for(int j=0;j<width;++j) {
      const double a=first(i,j),b=second(i,j);
      finite=finite && std::isfinite(a) && std::isfinite(b);
      identical=identical && std::memcmp(&a,&b,sizeof(double))==0;
    }
  }
  bool Identical()const{return identical;}
  std::size_t DenseTerms()const{return std::size_t(height)*width*6;}
  std::size_t EvaluatedTerms()const{return DenseTerms()/(identical?2:1);}
  template<class Coordinate>
  bool CanApply(Coordinate coordinate)const {
#ifdef __FAST_MATH__
    (void)coordinate;
    return false;
#else
    if(!finite || std::fegetround()!=FE_TONEAREST)return false;
    for(int j=0;j<width;++j)
      for(int c=0;c<3;++c)if(!std::isfinite(coordinate(j,c)))return false;
    return true;
#endif
  }
};
}
