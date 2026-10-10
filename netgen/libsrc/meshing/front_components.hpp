#ifndef NETGEN_FRONT_COMPONENTS_HPP
#define NETGEN_FRONT_COMPONENTS_HPP
#include <algorithm>
#include <numeric>
#include <vector>

namespace netgen {
// Connectivity only. The legacy front connects exactly the first THREE
// vertices, even for quads; geometry and connected-pair constraints stay out.
// Union by size bounds tree depth; root identity is separate from minimum ID.
class FrontComponents {
  std::vector<int> parent, size, minimum;
  int Root(int p) {
    while(parent[p]!=p) {parent[p]=parent[parent[p]];p=parent[p];}
    return p;
  }
public:
  explicit FrontComponents(int count):parent(count),size(count,1),minimum(count) {
    std::iota(parent.begin(),parent.end(),0);
    std::iota(minimum.begin(),minimum.end(),0);
  }
  bool Join(int a,int b) {
    a=Root(a);b=Root(b);if(a==b)return false;
    if(size[a]<size[b])std::swap(a,b);
    parent[b]=a;size[a]+=size[b];minimum[a]=std::min(minimum[a],minimum[b]);
    return true;
  }
  int Label(int p) {return minimum[Root(p)];}
};
}
#endif
