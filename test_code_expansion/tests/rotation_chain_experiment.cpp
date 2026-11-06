// Isolated research executable: replaces ruler3/meshing3 objects in the test link.
// TestRules is an existing Meshing3 friend; the production code is unchanged.
#include "rotation_chain_probe.hpp"
#include "rotation_chain_ruler3.inc"
#include <random>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <exception>
using namespace netgen;
namespace {
int threads=4, fixture_variant=0, limit=7, repeats=1, route=0;
bool verify=true;
int front_fixture=-1, prepare_fault=-1;
unsigned long long front_batches=0;
unsigned long long experiment_dispatches=0;
void PrintProfile(const VolumeKernelStats *stats=nullptr) {
  using namespace rotation_probe;
  std::cout<<",\"profile\":{\"enabled\":"<<(enabled?"true":"false")
    <<",\"input_wall\":"<<input_wall<<",\"copy_wall\":"<<copy_wall
    <<",\"queue_wall\":"<<queue_wall<<",\"dispatch_wall\":"<<dispatch_wall
    <<",\"merge_wall\":"<<merge_wall<<",\"prepare_elapsed_sum\":"<<prepare_sum.load()
    <<",\"prepare_calls\":"<<prepare_calls.load()<<",\"worker_elapsed_sum\":"<<worker_sum
    <<",\"group_max_sum\":"<<group_max_sum<<",\"group_idle_proxy\":"<<group_idle_proxy
    <<",\"groups\":"<<groups
    <<",\"shared_prepare_wall\":"<<shared_prepare_wall<<",\"worker_copy_sum\":"<<worker_copy_sum
    <<",\"chain_claim_sum\":"<<chain_claim_sum<<",\"shared_preparations\":"<<shared_preparations
    <<",\"chain_rules\":"<<chain_rules<<",\"chain_rule_rotations\":"<<chain_rule_rotations
    <<",\"input_copies\":"<<input_copies<<",\"prefix_checks\":"<<prefix_checks
    <<",\"worker_init_sum\":"<<worker_init_sum<<",\"workspace_builds\":"<<workspace_builds
    <<",\"workspace_calls\":"<<workspace_calls<<",\"worker_input_copies\":"<<worker_input_copies
    <<",\"prepare_dispatches\":"<<prepare_dispatches
    <<",\"worker_preparations\":"<<worker_preparations<<",\"prepare_wait_events\":"<<prepare_wait_events
    <<",\"worker_prepare_sum\":"<<worker_prepare_sum<<",\"prepare_wait_sum\":"<<prepare_wait_sum
    <<",\"prepare_span_wall\":"<<prepare_span_wall
    <<",\"claim_elapsed_sum\":"<<claim_sum.load()<<",\"claim_attempts\":"<<claim_attempts.load()
    <<",\"claim_successes\":"<<claim_successes.load()
    <<",\"group_dispatch_wall\":"<<group_dispatch_wall<<",\"launch_delay_sum\":"<<launch_delay_sum
    <<",\"completion_to_join_sum\":"<<completion_to_join_sum<<",\"join_tail_wall\":"<<join_tail_wall;
  if(stats)std::cout<<",\"baseline_rule_wall\":"<<stats->rule_values[9].load()
    <<",\"baseline_copy_wall\":"<<stats->rule_values[10].load()
    <<",\"baseline_dispatch_wall\":"<<stats->rule_values[11].load()
    <<",\"baseline_worker_elapsed_sum\":"<<stats->rule_values[12].load()
    <<",\"baseline_group_max_sum\":"<<stats->rule_values[13].load();
  std::cout<<"}";
}
struct Frame {
  Array<Point3d,PointIndex> points;
  Array<int,PointIndex> allow;
  Array<MiniElement2d> faces;
  NgArray<Element> elements;
  NgArray<INDEX> deleted;
  int found=-1, rotation=1;
  float error=0;
  double other=1e10, without=1e10;
  std::string diagnostics;
  unsigned long long work[5]={0,0,0,0,0};
};
struct Batch {
  std::vector<std::unique_ptr<Frame>> frames;
  INDEX_2_HASHTABLE<int> pairs{10};
  int split,tolerance;
  double sloppy;
};
std::function<void(Meshing3 &,Batch &,std::vector<std::unique_ptr<Frame>> &)> front_chain_runner;
struct FrontChain {
  std::unique_ptr<Batch> input;
  std::vector<std::unique_ptr<Frame>> output;
};
std::unique_ptr<FrontChain> PrepareFrontChain(Meshing3 &mesher,
    const Array<Point3d,PointIndex> &points, const Array<int,PointIndex> &initial_allow,
    const Array<MiniElement2d> &faces,int split,INDEX_2_HASHTABLE<int> &pairs,
    int tolerance,double sloppy,double h,int mode) {
  if(route<1 || mode!=1 || testmode || !ngcore::task_manager ||
     ngcore::TaskManager::GetNumThreads()<2 || testout->rdbuf()!=nullptr ||
     testout->tie()!=nullptr || testout->exceptions()!=std::ios::goodbit)return nullptr;
  const auto prepare_start=rotation_probe::enabled?RuleClock::now():RuleClock::time_point{};
  auto state=std::make_unique<FrontChain>();state->input=std::make_unique<Batch>();
  auto &b=*state->input;b.split=split;b.tolerance=tolerance;b.sloppy=sloppy;
  for(int i=1;i<=pairs.GetNBags();++i)for(int j=1;j<=pairs.GetBagSize(i);++j) {
    INDEX_2 pair;int value;pairs.GetData(i,j,pair,value);b.pairs.Set(pair,value);
  }
  Array<int,PointIndex> allow(initial_allow);referencetransform trans;
  for(int rotation=1;rotation<=faces[0].GetNP();++rotation) {
    auto f=std::make_unique<Frame>();f->rotation=rotation;f->faces=faces;f->points.SetSize(points.Size());
    trans.Set(points[faces[0].PNumMod(1+rotation)],points[faces[0].PNumMod(2+rotation)],
              points[faces[0].PNumMod((faces[0].GetNP()==3?3:4)+rotation)],h);
    for(auto i:points.Range())trans.ToPlain(points[i],f->points[i]);
    for(auto i:allow.Range())if(f->points[i].Z()>0)allow[i]=0;
    f->allow=allow;
    auto output=std::make_unique<Frame>();output->rotation=rotation;
    if(route<4){output->points=f->points;output->faces=f->faces;output->allow=f->allow;}
    state->output.push_back(std::move(output));
    b.frames.push_back(std::move(f));
  }
  if constexpr(rotation_probe::enabled)rotation_probe::input_wall+=RuleElapsed(prepare_start);
  front_chain_runner(mesher,b,state->output);++front_batches;
  minother=minwithoutother=1e10;
  return state;
}
uint64_t fingerprint=1469598103934665603ULL;
void Bytes(const void *p,size_t n) {
  for(size_t i=0;i<n;++i) {fingerprint^=static_cast<const unsigned char*>(p)[i];fingerprint*=1099511628211ULL;}
}
template<class T> void FingerprintAdd(T v) {Bytes(&v,sizeof(v));}
void HashFrame(const Frame &f) {
  FingerprintAdd(f.found);FingerprintAdd(f.error);FingerprintAdd(f.other);FingerprintAdd(f.without);
  FingerprintAdd(int(f.points.Size()));FingerprintAdd(int(f.faces.Size()));FingerprintAdd(int(f.elements.Size()));FingerprintAdd(int(f.deleted.Size()));
  for(auto &p:f.points)for(int j=1;j<=3;++j)FingerprintAdd(p.X(j));
  for(auto &face:f.faces){FingerprintAdd(face.GetNP());for(auto pi:face.PNums())FingerprintAdd(int(pi));}
  for(auto &e:f.elements){FingerprintAdd(e.GetType());for(auto pi:e.PNums())FingerprintAdd(int(pi));}
  for(auto i:f.deleted)FingerprintAdd(i);
  for(auto n:f.work)FingerprintAdd(n);
  Bytes(f.diagnostics.data(),f.diagnostics.size());
}
std::vector<std::unique_ptr<Batch>> Corpus() {
  std::vector<std::unique_ptr<Batch>> corpus;
  std::mt19937 random(928173);
  for(int sample=0;sample<160;++sample) {
    Array<Point3d,PointIndex> p;
    for(auto v:{Point3d(0,0,0),Point3d(1,0,0),Point3d(.5,.8660254037844386,0),
                Point3d(.5,.2886751345948129,-.816496580927726)})p.Append(v);
    if(sample>=140)p[PointIndex(4)]=Point3d(0,1,0);
    for(int j=0;j<sample%9;++j) {
      auto x=[&](){return double(random()%30001)/10000.-1;};
      // Explicit coordinate order makes fixture generation compiler-independent.
      double a=x(),b=x(),c=x();p.Append(Point3d(a,b,-std::abs(c)));
    }
    Array<MiniElement2d> faces;
    int tris[4][3]={{1,2,3},{1,4,2},{2,4,3},{3,4,1}};
    for(int k=0;k<=sample%4;++k) {
      MiniElement2d f(3);for(int j=1;j<=3;++j)f.PNum(j)=tris[k][j-1];faces.Append(f);
    }
    if(sample>=140) {
      faces.SetSize(0);MiniElement2d f(4);for(int j=1;j<=4;++j)f.PNum(j)=j;faces.Append(f);
    }
    for(int tol=1;tol<=limit;++tol) {
      auto batch=std::make_unique<Batch>();batch->tolerance=tol;batch->sloppy=sample%2?5.:1.;
      batch->split=fixture_variant==2?1+sample%faces.Size():faces.Size();
      if(fixture_variant==1){batch->pairs.Set(INDEX_2(1,4),1);batch->pairs.Set(INDEX_2(2,3),1);}
      Array<int,PointIndex> allow(p.Size());allow=2;
      for(auto i:allow.Range())if(int(i)>4)allow[i]=(sample+int(i))%3;
      referencetransform transform;
      for(int rotation=1;rotation<=faces[0].GetNP();++rotation) {
        auto f=std::make_unique<Frame>();f->rotation=rotation;f->faces=faces;f->points.SetSize(p.Size());
        transform.Set(p[faces[0].PNumMod(1+rotation)],p[faces[0].PNumMod(2+rotation)],
          p[faces[0].PNumMod((faces[0].GetNP()==3?3:4)+rotation)],1.);
        for(auto i:p.Range())transform.ToPlain(p[i],f->points[i]);
        // The real front carries this mask across rotations, rather than resetting it.
        for(auto i:allow.Range())if(f->points[i].Z()>0)allow[i]=0;
        f->allow=allow;batch->frames.push_back(std::move(f));
      }
      corpus.push_back(std::move(batch));
    }
  }
  return corpus;
}
}
#include "rotation_chain_meshing3.inc"
namespace netgen {
void TestRules() {
  std::ostream disabled(nullptr);testout=&disabled;testmode=0;printmessage_importance=0;
  auto team=std::make_unique<ngcore::RegionTaskManager>(threads);
  Meshing3 mesher(static_cast<const char **>(nullptr));mesher.SetQuietRuleParallelism(true);
  auto corpus=Corpus();unsigned long long calls=0,dispatches=0;
  double measured=0;
  front_chain_runner=[&](Meshing3 &mesher,Batch &b,std::vector<std::unique_ptr<Frame>> &outputs) {
    int rotations=b.frames.size();
    if(route>=4) {
      const int workers=std::min(threads,int(mesher.rules.Size()));
      const int rule_count=mesher.rules.Size();
      const bool workspace_route=route>=7;
      const bool reuse_input=route==5 || workspace_route;
      const bool fused_prepare=route==9;
      // Every rule retains exclusive ownership for its entire ordered chain.
      // Shared geometry is scoped to this front batch, never a later front.
      std::vector<std::unique_ptr<RuleSearchPreparation>> prepared(rotations);
      std::unique_ptr<std::atomic<bool>[]> ready;
      std::atomic<int> next_prepare{0};
      std::atomic<bool> preparation_failed{false};
      if(fused_prepare) {
        ready.reset(new std::atomic<bool>[rotations]);
        for(int rotation=0;rotation<rotations;++rotation)ready[rotation].store(false,std::memory_order_relaxed);
      }
      bool useedges=false;for(int id=0;id<rule_count;++id)if(mesher.rules[id]->GetNEd())useedges=true;
      auto prepare=[&](size_t rotation) {
        if(fused_prepare && int(rotation)==prepare_fault)throw std::runtime_error("ROTATION_PREPARATION_FAULT");
        auto &input=*b.frames[rotation];
        prepared[rotation]=std::make_unique<RuleSearchPreparation>(
            input.points,input.faces,b.split,b.pairs,useedges);
      };
      if(!fused_prepare) {
        rotation_probe::WallTimer timer(rotation_probe::shared_prepare_wall);
        if(route==8) {
          // Independent rotations, one writer per slot. ParallelFor joins
          // before any search reads preparation; its extra dispatch is counted.
          ngcore::ParallelFor(size_t(rotations),prepare,workers);
          ++dispatches;++experiment_dispatches;++rotation_probe::prepare_dispatches;
        } else for(int rotation=0;rotation<rotations;++rotation)prepare(rotation);
      }
      rotation_probe::shared_preparations+=rotations;
      struct ChainWorker {
        std::vector<std::unique_ptr<RuleSearchResult>> winners,scratch;
        RuleSearchQueue queue{0};
        unsigned long long chains=0,visits=0,copies=0,prefix_checks=0;
        unsigned long long workspace_calls=0,preparations=0,prepare_waits=0;
        double copy_seconds=0,claim_seconds=0,init_seconds=0,prepare_seconds=0,wait_seconds=0;
        rotation_probe::Clock::time_point start{},finish{},prepare_start{},prepare_finish{};
        std::exception_ptr preparation_error;
      };
      std::vector<std::unique_ptr<ChainWorker>> slots;
      {
        rotation_probe::WallTimer timer(rotation_probe::copy_wall);
        for(int w=0;w<workers;++w) {
          auto slot=std::make_unique<ChainWorker>();slot->queue.discard_trace=true;
          slot->queue.order.resize(1);
          for(auto &input:b.frames) {
            auto result=std::make_unique<RuleSearchResult>();
            result->other=minother;result->withoutother=minwithoutother;
            slot->winners.push_back(std::move(result));
            if(route==5) {
              auto scratch=std::make_unique<RuleSearchResult>();
              scratch->points=input->points;scratch->faces=input->faces;++slot->copies;
              slot->scratch.push_back(std::move(scratch));
            }
          }
          slots.push_back(std::move(slot));
        }
      }
      std::vector<std::vector<std::string>> diagnostic;
      if(verify)diagnostic.resize(rotations,std::vector<std::string>(rule_count));
      std::atomic<int> next_rule{0};
      std::atomic<bool> prefix_ok{true};
      if(b.tolerance>5)RuleDiagnosticCounter()+=rotations;
      const auto begin=rotation_probe::enabled?rotation_probe::Clock::now():rotation_probe::Clock::time_point{};
      ngcore::ParallelFor(size_t(workers),[&](size_t worker) {
        auto &slot=*slots[worker];
        if constexpr(rotation_probe::enabled)slot.start=rotation_probe::Clock::now();
        std::unique_ptr<RotationChainWorkspace> workspace;
        if(workspace_route) {
          const auto init_start=rotation_probe::enabled?rotation_probe::Clock::now():rotation_probe::Clock::time_point{};
          workspace=std::make_unique<RotationChainWorkspace>();
          slot.scratch.reserve(rotations);
          for(auto &input:b.frames) {
            auto scratch=std::make_unique<RuleSearchResult>();
            scratch->points=input->points;scratch->faces=input->faces;++slot.copies;
            slot.scratch.push_back(std::move(scratch));
          }
          if constexpr(rotation_probe::enabled)slot.init_seconds=rotation_probe::Since(init_start);
        }
        if(fused_prepare) {
          // Preparation is claimed only by running tasks. A producer never
          // waits on another producer; no queued task owns an awaited slot.
          for(int rotation=next_prepare.fetch_add(1,std::memory_order_relaxed);
              rotation<rotations && !preparation_failed.load(std::memory_order_acquire);
              rotation=next_prepare.fetch_add(1,std::memory_order_relaxed)) {
            const auto prep_start=rotation_probe::enabled?rotation_probe::Clock::now():rotation_probe::Clock::time_point{};
            try {
              prepare(rotation);
              ready[rotation].store(true,std::memory_order_release);
              if constexpr(rotation_probe::enabled) {
                const auto end=rotation_probe::Clock::now();
                if(slot.preparations==0)slot.prepare_start=prep_start;
                slot.prepare_finish=end;
                slot.prepare_seconds+=std::chrono::duration<double>(end-prep_start).count();
              }
              ++slot.preparations;
            } catch(...) {
              slot.preparation_error=std::current_exception();
              preparation_failed.store(true,std::memory_order_release);
              break;
            }
          }
        }
        bool all_prepared=!fused_prepare;
        int static_id=int(worker);
        while(!fused_prepare || !preparation_failed.load(std::memory_order_acquire)) {
          const auto claim_start=rotation_probe::enabled?rotation_probe::Clock::now():rotation_probe::Clock::time_point{};
          int id;
          if(route==6){id=static_id;static_id+=workers;}
          else id=next_rule.fetch_add(1,std::memory_order_relaxed);
          if constexpr(rotation_probe::enabled)slot.claim_seconds+=rotation_probe::Since(claim_start);
          if(id>=rule_count)break;
          ++slot.chains;slot.queue.order[0]=id;
          for(int rotation=0;rotation<rotations;++rotation) {
            if(!all_prepared) {
              if(!ready[rotation].load(std::memory_order_acquire)) {
                ++slot.prepare_waits;
                const auto wait_start=rotation_probe::enabled?rotation_probe::Clock::now():rotation_probe::Clock::time_point{};
                while(!ready[rotation].load(std::memory_order_acquire) &&
                      !preparation_failed.load(std::memory_order_acquire))std::this_thread::yield();
                if constexpr(rotation_probe::enabled)slot.wait_seconds+=rotation_probe::Since(wait_start);
              }
              if(preparation_failed.load(std::memory_order_acquire))break;
              // After the first chain all input slots are published. Avoid
              // readiness loads on every subsequent rule/rotation.
              if(rotation+1==rotations)all_prepared=true;
            }
            auto &input=*b.frames[rotation];auto &winner=*slot.winners[rotation];
            std::unique_ptr<RuleSearchResult> fresh;
            const auto copy_start=rotation_probe::enabled?rotation_probe::Clock::now():rotation_probe::Clock::time_point{};
            if(!reuse_input) {
              fresh=std::make_unique<RuleSearchResult>();
              fresh->points=input.points;fresh->faces=input.faces;++slot.copies;
            }
            auto &r=reuse_input?*slot.scratch[rotation]:*fresh;
            // ApplyRulesImpl only appends geometry and truncates to the original
            // prefix. Reuse that prefix; all output/scalar state is reset.
            r.points.SetSize(input.points.Size());r.faces.SetSize(input.faces.Size());
            r.elements.SetSize(0);r.deleted.SetSize(0);r.trace.clear();
            r.rules=r.eligible=r.face_trials=r.point_trials=r.mappings=0;
            r.replay_face_trials=r.replay_point_trials=0;r.prefix_min=1e100;
            r.found=-1;r.error=r.quality=0;r.other=minother;r.withoutother=minwithoutother;
            if constexpr(rotation_probe::enabled)slot.copy_seconds+=rotation_probe::Since(copy_start);
            if(!workspace_route)slot.queue.next.store(0,std::memory_order_relaxed);
            slot.queue.prepared=prepared[rotation].get();
            if(workspace_route)
              r.found=mesher.ApplyRulesWorkspace(r.points,input.allow,r.faces,b.split,b.pairs,r.elements,r.deleted,
                  b.tolerance,b.sloppy,input.rotation,r.error,&slot.queue,&r,*workspace);
            else
              r.found=mesher.ApplyRulesImpl(r.points,input.allow,r.faces,b.split,b.pairs,r.elements,r.deleted,
                  b.tolerance,b.sloppy,input.rotation,r.error,&slot.queue,&r);
            ++slot.visits;
            if(verify) {
              if(reuse_input) {
                bool same=r.points.Size()>=input.points.Size() && r.faces.Size()>=input.faces.Size();
                if(same) {
                  for(auto pi:input.points.Range())for(int c=1;c<=3;++c)
                    if(!RuleBitsEqual(r.points[pi].X(c),input.points[pi].X(c)))same=false;
                  for(auto fi:input.faces.Range()) {
                    if(r.faces[fi].GetNP()!=input.faces[fi].GetNP()){same=false;continue;}
                    for(int j=1;j<=input.faces[fi].GetNP();++j)
                      if(r.faces[fi].PNum(j)!=input.faces[fi].PNum(j))same=false;
                  }
                }
                if(!same)prefix_ok.store(false,std::memory_order_relaxed);
                ++slot.prefix_checks;
              }
              auto &s=diagnostic[rotation][id];auto append=[&](auto v){s.append(reinterpret_cast<char*>(&v),sizeof(v));};
              append(mesher.foundmap[id]);append(mesher.canuse[id]);
              auto &problem=mesher.problems[id];append(int(problem.size()));s+=problem;
            }
            winner.error=r.error;
            winner.other=std::min(winner.other,r.other);winner.withoutother=std::min(winner.withoutother,r.withoutother);
            winner.rules+=r.rules;winner.eligible+=r.eligible;winner.face_trials+=r.face_trials;
            winner.point_trials+=r.point_trials;winner.mappings+=r.mappings;
            if(r.found==0 && winner.found<0)winner.found=0;
            if(r.found>0 && (winner.found<=0 || r.quality<winner.quality ||
                (r.quality==winner.quality && r.found<winner.found))) {
              winner.found=r.found;winner.quality=r.quality;
              winner.points=r.points;winner.faces=r.faces;winner.elements=r.elements;winner.deleted=r.deleted;
            }
          }
        }
        // Release task-owned storage on the owning worker before recording its
        // finish. Winner geometry survives in slots until the host merges it.
        if(workspace_route){slot.workspace_calls=workspace->calls;slot.scratch.clear();workspace.reset();}
        if constexpr(rotation_probe::enabled)slot.finish=rotation_probe::Clock::now();
      },workers);
      const auto joined=rotation_probe::enabled?rotation_probe::Clock::now():rotation_probe::Clock::time_point{};
      ++dispatches;++experiment_dispatches;
      for(auto &slot:slots)if(slot->preparation_error)std::rethrow_exception(slot->preparation_error);
      if(!prefix_ok.load(std::memory_order_relaxed))throw std::runtime_error("复用缓冲的原始几何前缀发生变化");
      if constexpr(rotation_probe::enabled) {
        const auto seconds=[](auto duration){return std::chrono::duration<double>(duration).count();};
        auto last=slots[0]->finish;double total=0,largest=0;
        for(auto &slot:slots) {
          double elapsed=seconds(slot->finish-slot->start);total+=elapsed;largest=std::max(largest,elapsed);
          last=std::max(last,slot->finish);
          rotation_probe::launch_delay_sum+=seconds(slot->start-begin);
          rotation_probe::completion_to_join_sum+=seconds(joined-slot->finish);
          rotation_probe::worker_copy_sum+=slot->copy_seconds;
          rotation_probe::worker_init_sum+=slot->init_seconds;
          rotation_probe::worker_prepare_sum+=slot->prepare_seconds;
          rotation_probe::prepare_wait_sum+=slot->wait_seconds;
          rotation_probe::chain_claim_sum+=slot->claim_seconds;
        }
        rotation_probe::dispatch_wall+=seconds(joined-begin);rotation_probe::group_dispatch_wall+=seconds(joined-begin);
        rotation_probe::join_tail_wall+=seconds(joined-last);
        rotation_probe::worker_sum+=total;rotation_probe::group_max_sum+=largest;
        rotation_probe::group_idle_proxy+=workers*largest-total;++rotation_probe::groups;
        if(fused_prepare) {
          auto first=joined,last_prepared=begin;
          for(auto &slot:slots)if(slot->preparations) {
            first=std::min(first,slot->prepare_start);
            last_prepared=std::max(last_prepared,slot->prepare_finish);
          }
          rotation_probe::prepare_span_wall+=seconds(last_prepared-first);
        }
      }
      for(auto &slot:slots) {
        rotation_probe::worker_preparations+=slot->preparations;
        rotation_probe::prepare_wait_events+=slot->prepare_waits;
        rotation_probe::chain_rules+=slot->chains;rotation_probe::chain_rule_rotations+=slot->visits;
        rotation_probe::input_copies+=slot->copies;rotation_probe::prefix_checks+=slot->prefix_checks;
        if(workspace_route) {
          ++rotation_probe::workspace_builds;
          rotation_probe::workspace_calls+=slot->workspace_calls;
          rotation_probe::worker_input_copies+=slot->copies;
        }
      }
      {
        rotation_probe::WallTimer timer(rotation_probe::merge_wall);
        for(int rotation=0;rotation<rotations;++rotation) {
          auto &o=*outputs[rotation];RuleSearchResult *winner=nullptr;int found=-1;
          for(auto &slot:slots) {
            auto &r=*slot->winners[rotation];
            minother=std::min(minother,r.other);minwithoutother=std::min(minwithoutother,r.withoutother);
            if(r.found==0)found=0;
            if(r.found>0 && (!winner || r.quality<winner->quality ||
                (r.quality==winner->quality && r.found<winner->found)))winner=&r;
            unsigned long long work[]={r.rules,r.eligible,r.face_trials,r.point_trials,r.mappings};
            for(int k=0;k<5;++k)o.work[k]+=work[k];
          }
          o.found=winner?winner->found:found;
          for(auto &slot:slots)if(slot->chains){o.error=slot->winners[rotation]->error;break;}
          o.other=minother;o.without=minwithoutother;
          if(winner){o.points=winner->points;o.faces=winner->faces;o.elements=winner->elements;o.deleted=winner->deleted;}
          else if(o.points.Size()==0){o.points=b.frames[rotation]->points;o.faces=b.frames[rotation]->faces;}
          if(verify)for(auto &d:diagnostic[rotation])o.diagnostics+=d;
        }
      }
      return;
    }
      int workers=std::min(threads,int(mesher.rules.Size()));
      struct Slot {std::vector<std::unique_ptr<RuleSearchResult>> result;};
      std::vector<Slot> slots(workers);
      std::vector<std::vector<std::string>> diagnostic;
      std::vector<std::vector<double>> worker_times;
      std::vector<rotation_probe::Clock::time_point> group_starts,group_finishes;
      if constexpr(rotation_probe::enabled) {
        worker_times.resize(workers,std::vector<double>(rotations,0));
        group_starts.resize(workers);group_finishes.resize(workers);
      }
      {
      rotation_probe::WallTimer timer(rotation_probe::copy_wall);
      if(verify)diagnostic.resize(rotations,std::vector<std::string>(mesher.rules.Size()));
      for(auto &slot:slots)for(auto &f:b.frames) {
        auto r=std::make_unique<RuleSearchResult>();r->points=f->points;r->faces=f->faces;
        slot.result.push_back(std::move(r));
      }
      }
      std::vector<std::unique_ptr<RuleSearchQueue>> queues;
      {
        rotation_probe::WallTimer timer(rotation_probe::queue_wall);
        if(route==3) {
          queues.emplace_back(new RuleSearchQueue(mesher.rules.Size()));
          queues[0]->discard_trace=true;
        } else for(int worker=0;worker<workers;++worker) {
          queues.emplace_back(new RuleSearchQueue(0));queues.back()->discard_trace=true;
          for(int id=worker;id<mesher.rules.Size();id+=workers)queues.back()->order.push_back(id);
        }
      }
      if(b.tolerance>5)RuleDiagnosticCounter()+=rotations;
      auto record_diagnostic=[&](int rotation,int id) {
        auto &s=diagnostic[rotation][id];auto append=[&](auto v){s.append(reinterpret_cast<char*>(&v),sizeof(v));};
        append(mesher.foundmap[id]);append(mesher.canuse[id]);
        auto &problem=mesher.problems[id];append(int(problem.size()));s+=problem;
      };
      auto search=[&](int worker,int rotation) {
          auto &queue=*queues[route==3?0:worker];
          if(route!=3)queue.next.store(0,std::memory_order_relaxed);
          auto &r=*slots[worker].result[rotation];auto &input=*b.frames[rotation];
          const auto start=rotation_probe::enabled?RuleClock::now():RuleClock::time_point{};
          r.found=mesher.ApplyRulesImpl(r.points,input.allow,r.faces,b.split,b.pairs,r.elements,r.deleted,
            b.tolerance,b.sloppy,input.rotation,r.error,&queue,&r);
          if constexpr(rotation_probe::enabled)worker_times[worker][rotation]=RuleElapsed(start);
          // Static ownership permits per-worker capture. A shared dynamic queue
          // is captured only after its rotation joins, avoiding diagnostic races.
          if(verify && route!=3)for(int id:queue.order)record_diagnostic(rotation,id);
      };
      auto record_group=[&](int first,int end) {
        if constexpr(rotation_probe::enabled) {
          double total=0,largest=0;
          for(int w=0;w<workers;++w) {
            double elapsed=0;for(int r=first;r<end;++r)elapsed+=worker_times[w][r];
            total+=elapsed;largest=std::max(largest,elapsed);
          }
          rotation_probe::worker_sum+=total;rotation_probe::group_max_sum+=largest;
          rotation_probe::group_idle_proxy+=workers*largest-total;++rotation_probe::groups;
        }
      };
      // Observe existing joins without adding a new barrier. The interval from
      // task completion to host return is elapsed time, not CPU idle time.
      auto dispatch_group=[&](int first,int end) {
        rotation_probe::Clock::time_point begin{},joined{};
        if constexpr(rotation_probe::enabled)begin=rotation_probe::Clock::now();
        ngcore::ParallelFor(size_t(workers),[&](size_t worker) {
          if constexpr(rotation_probe::enabled)group_starts[worker]=rotation_probe::Clock::now();
          for(int rotation=first;rotation<end;++rotation)search(worker,rotation);
          if constexpr(rotation_probe::enabled)group_finishes[worker]=rotation_probe::Clock::now();
        },workers);
        if constexpr(rotation_probe::enabled) {
          joined=rotation_probe::Clock::now();
          const auto seconds=[](auto duration){return std::chrono::duration<double>(duration).count();};
          auto last=group_finishes[0];
          for(int w=0;w<workers;++w) {
            rotation_probe::launch_delay_sum+=seconds(group_starts[w]-begin);
            rotation_probe::completion_to_join_sum+=seconds(joined-group_finishes[w]);
            last=std::max(last,group_finishes[w]);
          }
          rotation_probe::group_dispatch_wall+=seconds(joined-begin);
          rotation_probe::join_tail_wall+=seconds(joined-last);
        }
      };
      {
        rotation_probe::WallTimer timer(rotation_probe::dispatch_wall);
        if(route==1) {
          dispatch_group(0,rotations);
          ++dispatches;++experiment_dispatches;record_group(0,rotations);
        } else for(int rotation=0;rotation<rotations;++rotation) {
          if(route==3)queues[0]->next.store(0,std::memory_order_relaxed);
          dispatch_group(rotation,rotation+1);
          ++dispatches;++experiment_dispatches;record_group(rotation,rotation+1);
          if(verify && route==3)for(int id=0;id<mesher.rules.Size();++id)record_diagnostic(rotation,id);
        }
      }
      rotation_probe::WallTimer merge_timer(rotation_probe::merge_wall);
      for(int rotation=0;rotation<rotations;++rotation) {
        auto &o=*outputs[rotation];RuleSearchResult *winner=nullptr;int found=-1;
        for(auto &slot:slots) {
          auto &r=*slot.result[rotation];
          minother=std::min(minother,r.other);minwithoutother=std::min(minwithoutother,r.withoutother);
          if(r.found==0)found=0;
          if(r.found>0 && (!winner || r.quality<winner->quality ||
            (r.quality==winner->quality && r.found<winner->found)))winner=&r;
          unsigned long long work[]={r.rules,r.eligible,r.face_trials,r.point_trials,r.mappings};
          for(int k=0;k<5;++k)o.work[k]+=work[k];
        }
        o.found=winner?winner->found:found;o.error=slots[0].result[rotation]->error;
        o.other=minother;o.without=minwithoutother;
        if(winner){o.points=winner->points;o.faces=winner->faces;o.elements=winner->elements;o.deleted=winner->deleted;}
        if(verify)for(auto &d:diagnostic[rotation])o.diagnostics+=d;
      }
  };
  if(front_fixture>=0) {
    team.reset(); // Let GenerateMesh own the team; nested teams deliberately fall back.
    const int refinement=front_fixture==3?0:front_fixture>=4?front_fixture-1:front_fixture;
    const double mesh_h=front_fixture==3?2.:std::ldexp(1.,-refinement);
    Mesh mesh;mesh.SetGlobalH(mesh_h);mesh.SetLocalH(Point<3>(-2,-2,-2),Point<3>(2,2,2),.3);
    std::vector<Point3d> points={{0,0,0},{1,0,0},{.5,.8660254037844386,0},
      {.5,.2886751345948129,-.816496580927726}};
    auto face=[](int a,int b,int c) {MiniElement2d f(3);f.PNum(1)=a;f.PNum(2)=b;f.PNum(3)=c;return f;};
    std::vector<MiniElement2d> faces={face(1,2,3),face(1,4,2),face(2,4,3),face(3,4,1)};
    for(int level=0;level<refinement;++level) {
      std::map<std::pair<int,int>,int> mids;
      auto mid=[&](int a,int b) {auto key=std::minmax(a,b);auto it=mids.find(key);
        if(it!=mids.end())return it->second;points.push_back(Center(points[a-1],points[b-1]));return mids[key]=points.size();};
      std::vector<MiniElement2d> next;
      for(auto &f:faces) {int a=f.PNum(1),b=f.PNum(2),c=f.PNum(3),ab=mid(a,b),bc=mid(b,c),ca=mid(c,a);
        for(auto child:{face(a,ab,ca),face(ab,b,bc),face(ca,bc,c),face(ab,bc,ca)})next.push_back(child);}
      faces=std::move(next);
    }
    for(auto p:points){auto pi=mesh.AddPoint(p);mesher.AddPoint(p,pi);}
    for(auto f:faces)mesher.AddBoundaryElement(f);
    MeshingParameters mp;VolumeKernelStats stats;
    mp.maxh=mesh_h;mp.uselocalh=false;mp.sloppy=front_fixture==3?.2:5.;
    mp.giveuptol=front_fixture==3?50:10;mp.starshapeclass=front_fixture==3?100:5;mp.nthreads=threads;
    mp.volume_rule_search=route<0?0:1;mp.volume_quiet_rule_parallelism=true;mp.volume_kernel_stats=&stats;
    const auto start=RuleClock::now();int status=mesher.GenerateMesh(mesh,mp);double seconds=RuleElapsed(start);
    FingerprintAdd(status);FingerprintAdd(mesh.GetNP());FingerprintAdd(mesh.GetNE());
    for(auto &p:mesh.Points())for(int j=0;j<3;++j)FingerprintAdd(p(j));
    for(auto &e:mesh.VolumeElements()){FingerprintAdd(e.GetType());for(auto pi:e.PNums())FingerprintAdd(int(pi));}
    for(int k=4;k<=8;++k)FingerprintAdd(stats.rule_values[k].load());
    for(int k=8;k<20;++k)FingerprintAdd(stats.front_values[k].load());
    std::cout<<std::setprecision(17)<<"{\"seconds\":"<<seconds<<",\"status\":"<<status
      <<",\"elements\":"<<mesh.GetNE()<<",\"front_batches\":"<<front_batches
      <<",\"dispatches\":"<<experiment_dispatches
      <<",\"fingerprint\":\""<<std::hex<<fingerprint<<std::dec<<"\"";
    PrintProfile(&stats);std::cout<<"}\n";
    return;
  }
  for(int repeat=0;repeat<repeats;++repeat)for(auto &bp:corpus) {
    auto &b=*bp;int rotations=b.frames.size();minother=minwithoutother=1e10;
    std::vector<std::unique_ptr<Frame>> outputs;
    for(auto &input:b.frames) {
      auto o=std::make_unique<Frame>();o->points=input->points;o->faces=input->faces;
      o->allow=input->allow;o->rotation=input->rotation;outputs.push_back(std::move(o));
    }
    // Both paths include dispatch, worker copies, preparation, search and merge.
    // Fixture transformation and output hashing are excluded from both paths.
    const auto start=RuleClock::now();
    if(route<=0 || threads==1) {
      for(auto &op:outputs) {
        auto &o=*op;std::unique_ptr<VolumeKernelStats> stats;
        if(verify)stats=std::make_unique<VolumeKernelStats>();
        o.found=mesher.ApplyRulesTasks(o.points,o.allow,o.faces,b.split,b.pairs,o.elements,o.deleted,
          b.tolerance,b.sloppy,o.rotation,o.error,route<0?0:1,stats.get());
        o.other=minother;o.without=minwithoutother;
        if(verify) {
          for(int k=0;k<5;++k)o.work[k]=stats->rule_values[4+k].load();
          for(int id:mesher.foundmap.Range()) {
            auto append=[&](auto v){o.diagnostics.append(reinterpret_cast<char*>(&v),sizeof(v));};
            append(mesher.foundmap[id]);append(mesher.canuse[id]);
            auto &s=mesher.problems[id];append(int(s.size()));o.diagnostics+=s;
          }
        }
      }
      dispatches+=route<0?0:rotations;
    } else {
      front_chain_runner(mesher,b,outputs);
    }
    measured+=RuleElapsed(start);calls+=rotations;
    if(verify)for(auto &f:outputs)HashFrame(*f);
  }
  std::cout<<std::setprecision(17)<<"{\"seconds\":"<<measured<<",\"calls\":"<<calls
    <<",\"dispatches\":"<<dispatches<<",\"fingerprint\":\""<<std::hex<<fingerprint<<std::dec<<"\"";
  PrintProfile();std::cout<<"}\n";
}
}
int main(int argc,char **argv) {
  if(argc>1)threads=std::atoi(argv[1]);if(argc>2)route=std::atoi(argv[2]);
  if(argc>3)fixture_variant=std::atoi(argv[3]);if(argc>4)limit=std::atoi(argv[4]);
  if(argc>5)verify=std::atoi(argv[5]);if(argc>6)repeats=std::atoi(argv[6]);
  if(argc>7)front_fixture=std::atoi(argv[7]);
  if(argc>8)prepare_fault=std::atoi(argv[8]);
  if(threads<1 || threads>16 || route < -1 || route>9 || fixture_variant<0 || fixture_variant>2 || limit<1 || limit>45 || repeats<1 || front_fixture < -1 || front_fixture>5)
    return 2;
  if(prepare_fault < -1 || prepare_fault>3 || (prepare_fault>=0 && (route!=9 || !verify)))return 2;
  try {TestRules();}
  catch(const std::exception &error){std::cerr<<error.what()<<"\n";return 1;}
}
