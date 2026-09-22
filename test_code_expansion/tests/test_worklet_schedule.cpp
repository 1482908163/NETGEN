#include "worklet_schedule.h"
#include <cassert>
#include <iostream>
#include <random>
using namespace mesh_research;
int main() {
    for(int seed=0;seed<100;++seed) {
        std::mt19937 random(seed);const int n=100+seed,p=2+seed%9;
        std::vector<int> owners(n);std::vector<std::vector<int>> adj(n);
        for(int c=0;c<n;++c){owners[c]=c%p;if(c){adj[c].push_back(c-1);adj[c-1].push_back(c);}}
        auto split=split_worklets(owners,adj,p,1+seed%12);
        assert(split.labels==split_worklets(owners,adj,p,1+seed%12).labels);
        std::vector<double> weight(split.owners.size(),0.);std::vector<std::map<int,int>> edges(weight.size());
        for(int c=0;c<n;++c){assert(split.owners.at(split.labels[c])==owners[c]);weight[split.labels[c]]++;}
        for(std::size_t t=1;t<weight.size();++t){edges[t][t-1]=1;edges[t-1][t]=1;}
        for(const char *policy:{"static","dynamic","remaining","critical"}) {
            std::vector<int> node(p);for(int r=0;r<p;++r)node[r]=r/2;
            WorkletSchedule schedule(split.owners,node,edges,weight);
            std::vector<int> active;for(int r=1;r<p;++r)active.push_back(r);
            std::set<int> seen;double now=0.;
            while(!active.empty()) {
                const auto i=random()%active.size();const int worker=active[i];
                const int t=schedule.claim(worker,policy,now);now+=.1;
                if(t<0){active.erase(active.begin()+i);continue;}
                assert(seen.insert(t).second);
                assert(schedule.owners()==split.owners);
                if(std::string(policy)=="static")assert(worker==(split.owners[t]==0?1:split.owners[t]));
                schedule.complete(t,worker,.01*(1+random()%50));
            }
            assert(schedule.complete() && seen.size()==weight.size());
        }
    }
    // Completion feedback and in-flight work are included in remaining work.
    WorkletSchedule s({0,0,1,2},{0,0,0,0,0},{{},{},{},{}},{10,3,2,1});
    const int a=s.claim(3,"remaining",0);assert(a==0);
    const int b=s.claim(4,"remaining",.1);assert(b==1);
    s.complete(a,3,1);s.complete(b,4,.3);
    try{s.complete(a,3,1);assert(false);}catch(const std::runtime_error &){}
    try{s.claim(0,"dynamic",0);assert(false);}catch(const std::runtime_error &){}
    try{s.claim(1,"unknown",0);assert(false);}catch(const std::runtime_error &){}
    try{split_worklets({0},{{}},2,4);assert(false);}catch(const std::runtime_error &){}
    try{split_worklets({0,1},{{2},{}},2,4);assert(false);}catch(const std::runtime_error &){}
    // Same topology/weights: criticality changes the choice, not the split.
    std::vector<std::map<int,int>> edges={{},{ {2,1} },{ {1,1} },{}};
    WorkletSchedule remaining({0,1,2,3},{0,0,0,0,0},edges,{10,9,1,1});
    WorkletSchedule critical({0,1,2,3},{0,0,0,0,0},edges,{10,9,1,1});
    assert(remaining.claim(4,"remaining",0)==0);
    assert(critical.claim(4,"critical",0)==1);
    // Avoid returning a neighbor's task while an owned task is available.
    for(const char *policy:{"dynamic","remaining","critical"}) {
        WorkletSchedule own({0,1,2},{0,0,0},{{},{},{}},{100,1,10});
        assert(own.claim(1,policy,0)==1);
    }
    // A tiny boundary to a fast neighbor must not dominate weighted criticality.
    std::vector<std::map<int,int>> weighted_edges={{},{{2,99},{3,1}},{{1,99}},{{1,1}}};
    WorkletSchedule weighted({0,1,2,3},{0,0,0,0,0},weighted_edges,{10,9,8.9,.1});
    assert(weighted.claim(4,"critical",0)==0);
    // Completed owner samples affect its pending work, not every owner equally.
    WorkletSchedule learned({0,0,1,1},{0,0,0},{{},{},{},{}},{20,2,10,3});
    int owner1=learned.claim(1,"remaining",0);assert(owner1==2);
    int owner0=learned.claim(2,"remaining",0);assert(owner0==0);
    learned.complete(owner0,2,200);learned.complete(owner1,1,1);
    assert(learned.claim(2,"remaining",100)==1); // Smaller but empirically slower owner's task.
    std::cout<<"worklet partition/scheduler tests passed\n";
}
