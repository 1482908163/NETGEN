#include "ordered_incidence.hpp"
#include <cassert>
#include <functional>
#include <random>
#include <thread>

// Independent sorted reference; production plan executes with actual OS threads.
int main() {
  std::mt19937_64 rng(20261010);
  std::size_t tested=0;
  auto parallel=[](std::size_t n,auto function) {
    const auto workers=std::min(n,std::size_t(4));
    std::vector<std::thread> team;
    for(std::size_t worker=0;worker<workers;++worker)
      team.emplace_back([&,worker] { for(std::size_t i=worker;i<n;i+=workers)function(i); });
    for(auto &t:team)t.join();
  };
  for(int trial=0;trial<800;++trial) {
    const std::size_t np=trial%29,ne=np?trial%131:0,shards=1+trial%9;
    std::vector<std::vector<std::size_t>> elements(ne),expected(np),actual(np);
    std::vector<bool> deleted(ne),mask(np);std::vector<int> domain(ne);
    const int selected=trial%4;
    for(std::size_t pi=0;pi<np;++pi)mask[pi]=trial%3 || rng()%2;
    for(std::size_t ei=0;ei<ne;++ei) {
      deleted[ei]=rng()%11==0;domain[ei]=1+rng()%3;
      for(std::size_t k=0;k<3+rng()%6;++k)elements[ei].push_back(rng()%np);
    }
    auto visit=[&](std::size_t shard,auto add) {
      const auto first=ne*shard/shards,last=ne*(shard+1)/shards;
      for(auto ei=first;ei<last;++ei) {
        if(deleted[ei] || (selected && selected!=domain[ei]))continue;
        for(auto pi:elements[ei])if(mask[pi])add(pi,ei);
      }
    };
    // Reference ignores shard traversal and builds from immutable element data.
    for(std::size_t ei=ne;ei-->0;)if(!deleted[ei] && (!selected || selected==domain[ei]))
      for(auto pi:elements[ei])if(mask[pi])expected[pi].push_back(ei);
    for(auto &row:expected)std::sort(row.begin(),row.end());
    netgen_incidence::OrderedPlan plan(np,shards);plan.Count(visit,parallel);
    for(std::size_t pi=0;pi<np;++pi)actual[pi].resize(plan.Sizes()[pi]);
    plan.Fill(visit,[&](std::size_t pi,std::size_t slot,std::size_t ei) {actual[pi][slot]=ei;},parallel);
    assert(actual==expected);++tested;
  }
  using P=netgen_incidence::OrderedPlan;
  assert(P::Fits(0,8) && !P::Fits(1,0) && !P::Fits(std::numeric_limits<std::size_t>::max(),8));
  const auto bound=P::scratch_limit/sizeof(std::size_t)/9;
  assert(P::Fits(bound,8) && !P::Fits(bound+1,8));
  bool rejected=false;try {P too_big(bound+1,8);}catch(const std::length_error&) {rejected=true;}
  assert(rejected && tested==800);
  auto serial=[](std::size_t n,auto f) {for(std::size_t i=0;i<n;++i)f(i);};
  for(std::size_t np:{std::size_t(0),std::size_t(3)}) {
    P plan(np,1);rejected=false;
    try {plan.Count([&](std::size_t,auto add) {add(np,std::size_t(0));},serial);}
    catch(const std::out_of_range&) {rejected=true;}
    assert(rejected);
  }
}
