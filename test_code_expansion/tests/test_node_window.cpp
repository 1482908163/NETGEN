#include "node_window_policy.h"
#include <cassert>
#include <limits>
int main(){
 using mesh_node::window_net_gain;
 assert(window_net_gain(4,4,10,.02,.001)>0);
 assert(window_net_gain(4,4,1,1.,0)==0);
 assert(window_net_gain(4,0,10,1.,0)==0);
 assert(window_net_gain(4,4,10,.02,.1)==0);
 assert(window_net_gain(4,4,10,std::numeric_limits<double>::quiet_NaN(),0)==0);
 // More mesh elements alone do not establish a longer recoverable window.
 assert(window_net_gain(4,4,10,.1,.002)>window_net_gain(4,4,20,.001,.002));
 assert(window_net_gain(4,4,10,.1,.002)>window_net_gain(4,2,10,.1,.002));
}
