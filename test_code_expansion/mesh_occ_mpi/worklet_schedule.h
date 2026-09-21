#pragma once
#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace mesh_research {
struct WorkletPartition {
    std::vector<int> labels, owners;
};
// Constrained, deterministic BFS chunks: never move a coarse cell out of its
// original rank partition. Disconnected pieces are allowed, as in METIS.
inline WorkletPartition split_worklets(const std::vector<int> &owners,
    const std::vector<std::vector<int>> &adj, int ranks, int factor) {
    if(ranks<2 || factor<1 || owners.size()!=adj.size())
        throw std::runtime_error("invalid worklet partition input");
    WorkletPartition out;out.labels.assign(owners.size(),-1);
    std::vector<std::vector<int>> cells(ranks);
    for(std::size_t c=0;c<owners.size();++c) {
        if(owners[c]<0 || owners[c]>=ranks)throw std::runtime_error("invalid coarse owner");
        cells[owners[c]].push_back(static_cast<int>(c));
        for(int n:adj[c])if(n<0 || n>=static_cast<int>(owners.size()))
            throw std::runtime_error("invalid coarse adjacency");
    }
    std::vector<char> queued(owners.size(),false);
    for(int r=0;r<ranks;++r) {
        if(cells[r].empty())throw std::runtime_error("empty original partition");
        const int k=std::min(factor,static_cast<int>(cells[r].size()));
        std::size_t seed=0;int remaining=static_cast<int>(cells[r].size());
        for(int j=0;j<k;++j) {
            const int t=static_cast<int>(out.owners.size());out.owners.push_back(r);
            const int count=(remaining+k-j-1)/(k-j);remaining-=count;
            std::vector<int> queue;std::size_t pos=0;
            for(int picked=0;picked<count;++picked) {
                if(pos==queue.size()) {
                    while(seed<cells[r].size() && out.labels[cells[r][seed]]>=0)++seed;
                    if(seed==cells[r].size())throw std::runtime_error("worklet coverage failure");
                    queue.push_back(cells[r][seed]);queued[cells[r][seed]]=true;
                }
                const int c=queue[pos++];out.labels[c]=t;
                for(int n:adj[c])if(owners[n]==r && out.labels[n]<0 && !queued[n]) {
                    queue.push_back(n);queued[n]=true;
                }
            }
            for(int c:queue)queued[c]=false;
        }
    }
    return out;
}

// A1/B1 reference dispatcher. Ownership is immutable; only execution moves.
// Tier order is common to all dynamic policies: node, adjacent owner, global.
// Rank zero is a dispatcher; static execution of owner zero uses worker one.
class WorkletSchedule {
    std::vector<int> owner_,node_,executor_,state_;
    std::vector<double> weight_,start_;
    std::vector<std::set<int>> neighbors_;
    double seconds_per_weight_=1.;bool observed_=false;
public:
    WorkletSchedule(std::vector<int> owner,std::vector<int> node,
        const std::vector<std::map<int,int>> &edges,std::vector<double> weight)
        :owner_(std::move(owner)),node_(std::move(node)),executor_(owner_.size(),-1),
         state_(owner_.size(),0),weight_(std::move(weight)),start_(owner_.size(),0),neighbors_(node_.size()) {
        if(node_.size()<2 || owner_.empty() || edges.size()!=owner_.size() || weight_.size()!=owner_.size())
            throw std::runtime_error("invalid worklet schedule");
        for(std::size_t t=0;t<owner_.size();++t) {
            if(owner_[t]<0 || owner_[t]>=static_cast<int>(node_.size()) || !(weight_[t]>0) || !std::isfinite(weight_[t]))
                throw std::runtime_error("invalid worklet owner/weight");
        }
        for(std::size_t t=0;t<owner_.size();++t)for(const auto &e:edges[t]) {
            if(e.first<0 || e.first>=static_cast<int>(owner_.size()) || e.second<=0)
                throw std::runtime_error("invalid worklet edge");
            if(owner_[t]!=owner_[e.first])neighbors_[owner_[t]].insert(owner_[e.first]);
        }
    }
    void complete(int t,int worker,double seconds) {
        if(t<0 || t>=static_cast<int>(owner_.size()) || state_[t]!=1 || executor_[t]!=worker ||
           seconds<0 || !std::isfinite(seconds))throw std::runtime_error("invalid worklet completion");
        state_[t]=2;
        const double sample=std::max(seconds,1e-9)/weight_[t];
        seconds_per_weight_=observed_?.8*seconds_per_weight_+.2*sample:sample;observed_=true;
    }
    int claim(int worker,const std::string &policy,double now) {
        if(worker<=0 || worker>=static_cast<int>(node_.size()) || !std::isfinite(now) ||
           (policy!="static" && policy!="dynamic" && policy!="remaining" && policy!="critical"))
            throw std::runtime_error("invalid worklet claim");
        std::vector<double> remaining(node_.size(),0.);
        for(std::size_t t=0;t<owner_.size();++t)if(state_[t]!=2) {
            const double estimate=weight_[t]*seconds_per_weight_;
            remaining[owner_[t]]+=state_[t]==0?estimate:std::max(.05*estimate,estimate-std::max(0.,now-start_[t]));
        }
        int best=-1,best_tier=4;double best_score=-1.;
        for(int t=0;t<static_cast<int>(owner_.size());++t)if(state_[t]==0) {
            const int home=owner_[t];
            if(policy=="static" && (home==0?1:home)!=worker)continue;
            const int tier=policy=="static"?0:node_[home]==node_[worker]?0:neighbors_[worker].count(home)?1:2;
            double score=weight_[t];
            if(policy=="remaining" || policy=="critical")score=remaining[home];
            if(policy=="critical" && !neighbors_[home].empty()) {
                double next=std::numeric_limits<double>::max();
                for(int n:neighbors_[home])next=std::min(next,remaining[n]);
                // Negative neighbor slack estimates the next synchronization's
                // blocker. This is a proxy, not a measured pipeline deadline.
                score+=std::max(0.,remaining[home]-next);
            }
            if(tier<best_tier || (tier==best_tier && (score>best_score ||
               (score==best_score && (best<0 || weight_[t]>weight_[best]))))) {
                best=t;best_tier=tier;best_score=score;
            }
        }
        if(best>=0){state_[best]=1;executor_[best]=worker;start_[best]=now;}
        return best;
    }
    bool complete() const {return std::all_of(state_.begin(),state_.end(),[](int s){return s==2;});}
    const std::vector<int> &executors() const {return executor_;}
    const std::vector<int> &owners() const {return owner_;}
};
}
