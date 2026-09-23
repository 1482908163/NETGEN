#include "node_window_policy.h"
#include <cassert>
#include <limits>
#include <functional>
#include <numeric>
#include <random>

static double predicted_tail(const std::vector<mesh_node::WindowDemand>& peers,
                             const std::vector<int>& allocation) {
 double tail=0;
 for(size_t i=0;i<peers.size();++i)
  tail=std::max(tail,peers[i].seconds()-peers[i].gain(allocation[i]));
 return tail;
}

static void test_tail_allocation() {
 using mesh_node::WindowDemand;
 using mesh_node::window_tail_allocation;
 const WindowDemand same{4,10,.1,.001,0};
 auto split=window_tail_allocation({same,same},6);
 assert(split==std::vector<int>({3,3}));
 // Taking all six for one peer cannot reduce the last completion here.
 assert(predicted_tail({same,same},split)<predicted_tail({same,same},{6,0}));
 assert(window_tail_allocation({same},6)==std::vector<int>({6}));
 auto stale=same;stale.age=1;
 assert(window_tail_allocation({same,stale},6)==std::vector<int>({6,0}));
 auto recent=same;recent.age=.2;
 assert(recent.seconds()<same.seconds() && recent.gain(2)<same.gain(2));
 auto costly=same;costly.restart=10;
 assert(window_tail_allocation({costly},6)==std::vector<int>({0}));
 assert(window_tail_allocation({same},0)==std::vector<int>({0}));
 assert(window_tail_allocation({},6).empty());
 auto invalid=same;invalid.age=std::numeric_limits<double>::quiet_NaN();
 assert(window_tail_allocation({invalid},6)==std::vector<int>({0}));
 // Independent exhaustive enumeration checks the DP optimum and resource budget.
 std::mt19937 random(731);
 for(int trial=0;trial<200;++trial) {
  const int budget=random()%9;
  std::vector<WindowDemand> peers;
  for(int i=0;i<3;++i)
   peers.push_back({int(1+random()%8),int(2+random()%20),.005*(1+random()%40),.001*(random()%80),0});
  const auto got=window_tail_allocation(peers,budget);
  int total=std::accumulate(got.begin(),got.end(),0);
  assert(total<=budget);
  for(size_t i=0;i<got.size();++i) assert(got[i]>=0 && (!got[i] || peers[i].gain(got[i])>0));
  double best=std::numeric_limits<double>::infinity();int least=budget+1;
  std::vector<int> option(3);
  std::function<void(int,int)> enumerate=[&](int i,int used) {
   if(i==3) {
    double value=predicted_tail(peers,option);
    if(value<best || (value==best && used<least)) {best=value;least=used;}
    return;
   }
   for(int k=0;k<=budget-used;++k) if(!k || peers[i].gain(k)>0) {
    option[i]=k;enumerate(i+1,used+k);
   }
  };
  enumerate(0,0);
  assert(std::abs(predicted_tail(peers,got)-best)<1e-12 && total==least);
 }
}
int main(){
 test_tail_allocation();
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
