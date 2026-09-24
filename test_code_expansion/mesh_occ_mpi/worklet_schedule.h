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
    int split_owners=0,max_factor=1;
};
// Constrained, deterministic BFS chunks: never move a coarse cell out of its
// original rank partition. Disconnected pieces are allowed, as in METIS.
inline WorkletPartition split_worklets(const std::vector<int> &owners,
    const std::vector<std::vector<int>> &adj, int ranks, int factor, bool adaptive=false) {
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
    std::vector<int> populations;
    populations.reserve(ranks);
    for(int r=0;r<ranks;++r) {
        if(cells[r].empty())throw std::runtime_error("empty original partition");
        populations.push_back(static_cast<int>(cells[r].size()));
    }
    std::sort(populations.begin(),populations.end());
    const int q75=populations[std::min<std::size_t>(populations.size()-1,3*populations.size()/4)];
    const int q90=populations[std::min<std::size_t>(populations.size()-1,9*populations.size()/10)];
    std::vector<char> queued(owners.size(),false);
    for(int r=0;r<ranks;++r) {
        int requested=factor;
        if(adaptive && factor>1) {
            requested=1;
            if(static_cast<int>(cells[r].size())>=q75) requested=std::min(2,factor);
            if(factor>=4 && static_cast<int>(cells[r].size())>=q90) requested=std::min(4,factor);
        }
        const int k=std::min(requested,static_cast<int>(cells[r].size()));
        if(k>1)++out.split_owners;
        out.max_factor=std::max(out.max_factor,k);
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
// Tier order is common to all dynamic policies: owner, node, adjacent owner, global.
// Rank zero is a dispatcher; static execution of owner zero uses worker one.
class WorkletSchedule {
    std::vector<int> owner_,node_,executor_,state_;
    std::vector<double> weight_,start_;
    std::vector<std::set<int>> neighbors_;
    std::vector<std::map<int,double>> boundary_;
    std::vector<double> owner_seconds_,owner_weight_;
    double seconds_per_weight_=1.;bool observed_=false;
    int remote_claims_=0,remote_budget_=1;
public:
    WorkletSchedule(std::vector<int> owner,std::vector<int> node,
        const std::vector<std::map<int,int>> &edges,std::vector<double> weight)
        :owner_(std::move(owner)),node_(std::move(node)),executor_(owner_.size(),-1),
         state_(owner_.size(),0),weight_(std::move(weight)),start_(owner_.size(),0),neighbors_(node_.size()),
         boundary_(node_.size()),owner_seconds_(node_.size(),0.),owner_weight_(node_.size(),0.) {
        if(node_.size()<2 || owner_.empty() || edges.size()!=owner_.size() || weight_.size()!=owner_.size())
            throw std::runtime_error("invalid worklet schedule");
        remote_budget_=std::max(1,static_cast<int>(owner_.size()/8));
        for(std::size_t t=0;t<owner_.size();++t) {
            if(owner_[t]<0 || owner_[t]>=static_cast<int>(node_.size()) || !(weight_[t]>0) || !std::isfinite(weight_[t]))
                throw std::runtime_error("invalid worklet owner/weight");
        }
        for(std::size_t t=0;t<owner_.size();++t)for(const auto &e:edges[t]) {
            if(e.first<0 || e.first>=static_cast<int>(owner_.size()) || e.second<=0)
                throw std::runtime_error("invalid worklet edge");
            if(owner_[t]!=owner_[e.first]) {
                neighbors_[owner_[t]].insert(owner_[e.first]);
                boundary_[owner_[t]][owner_[e.first]]+=e.second;
            }
        }
    }
    void complete(int t,int worker,double seconds) {
        if(t<0 || t>=static_cast<int>(owner_.size()) || state_[t]!=1 || executor_[t]!=worker ||
           seconds<0 || !std::isfinite(seconds))throw std::runtime_error("invalid worklet completion");
        state_[t]=2;
        owner_seconds_[owner_[t]]+=std::max(seconds,1e-9);
        owner_weight_[owner_[t]]+=weight_[t];
        const double sample=std::max(seconds,1e-9)/weight_[t];
        seconds_per_weight_=observed_?.8*seconds_per_weight_+.2*sample:sample;observed_=true;
    }
    int claim(int worker,const std::string &policy,double now) {
        if(worker<=0 || worker>=static_cast<int>(node_.size()) || !std::isfinite(now) ||
           (policy!="static" && policy!="dynamic" && policy!="remaining" && policy!="critical"))
            throw std::runtime_error("invalid worklet claim");
        std::vector<double> remaining(node_.size(),0.);
        if(policy=="remaining" || policy=="critical")
        for(std::size_t t=0;t<owner_.size();++t)if(state_[t]!=2) {
            // One task-weight of global prior regularizes sparse owner observations.
            const int home=owner_[t];
            const double unit=(owner_seconds_[home]+weight_[t]*seconds_per_weight_)/
                              (owner_weight_[home]+weight_[t]);
            const double estimate=weight_[t]*unit;
            remaining[owner_[t]]+=state_[t]==0?estimate:std::max(.05*estimate,estimate-std::max(0.,now-start_[t]));
        }
        double median_remaining=0.;
        if(policy=="critical") {
            std::vector<double> active;
            for(double x:remaining)if(x>0)active.push_back(x);
            if(!active.empty()) {
                const auto mid=active.begin()+active.size()/2;
                std::nth_element(active.begin(),mid,active.end());
                median_remaining=*mid;
            }
        }
        int best=-1,best_tier=5;double best_score=-1.;
        for(int t=0;t<static_cast<int>(owner_.size());++t)if(state_[t]==0) {
            const int home=owner_[t];
            if(policy=="static" && (home==0?1:home)!=worker)continue;
            const int tier=policy=="static"?0:home==worker?0:node_[home]==node_[worker]?1:neighbors_[worker].count(home)?2:3;
            const bool remote=node_[home]!=node_[worker];
            // B3: never spray critical work globally; cap neighbor-node steals.
            if(policy=="critical" && remote && (tier>=3 || remote_claims_>=remote_budget_))continue;
            double score=weight_[t];
            if(policy=="remaining" || policy=="critical")score=remaining[home];
            if(policy=="critical") {
                double neighbor_wait=0.,total=0.;
                for(const auto &edge:boundary_[home]) {
                    neighbor_wait+=edge.second*std::max(0.,remaining[home]-remaining[edge.first]);
                    total+=edge.second;
                }
                if(total>0)neighbor_wait/=total;
                const double critical_slack=std::max(0.,remaining[home]-median_remaining);
                const double task_seconds=std::max(1e-9,weight_[t]*seconds_per_weight_);
                const double transfer_cost=remote?(.002+.15*task_seconds):0.;
                const double locality=tier==0?1.0:tier==1?.96:.45;
                score=(critical_slack+neighbor_wait+.05*remaining[home]-transfer_cost)*locality;
            }
            const bool better = policy=="critical"
                ? (score>best_score || (score==best_score && (tier<best_tier ||
                   (tier==best_tier && (best<0 || weight_[t]>weight_[best])))))
                : (tier<best_tier || (tier==best_tier && (score>best_score ||
                   (score==best_score && (best<0 || weight_[t]>weight_[best])))));
            if(better) {best=t;best_tier=tier;best_score=score;}
        }
        if(best>=0){
            if(policy=="critical" && node_[owner_[best]]!=node_[worker])++remote_claims_;
            state_[best]=1;executor_[best]=worker;start_[best]=now;
        }
        return best;
    }
    bool complete() const {return std::all_of(state_.begin(),state_.end(),[](int s){return s==2;});}
    const std::vector<int> &executors() const {return executor_;}
    const std::vector<int> &owners() const {return owner_;}
    int remote_claims() const {return remote_claims_;}
    int remote_budget() const {return remote_budget_;}
};
}
