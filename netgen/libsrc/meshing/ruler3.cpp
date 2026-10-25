#include <mystdlib.h>
#include "meshing.hpp"
#include <sstream>
#include <cstring>
#include <cstdint>


namespace netgen
{
extern double minother;
extern double minwithoutother;


  static double CalcElementBadness (const Array<Point3d, PointIndex> & points,
                                    const Element & elem)
{
  double vol, l, l4, l5, l6;
  if (elem.GetNP() != 4) 
    {
      if (elem.GetNP() == 5)
	{
	  double z = points[elem.PNum(5)].Z();
	  if (z > -1e-8) return 1e8;
	  return (-1 / z) - z; //  - 2;
	}
      return 0;
    }
  
  Vec3d v1 = points[elem.PNum(2)] - points[elem.PNum(1)];
  Vec3d v2 = points[elem.PNum(3)] - points[elem.PNum(1)];
  Vec3d v3 = points[elem.PNum(4)] - points[elem.PNum(1)];
  
  vol = - (Cross (v1, v2) * v3);
  l4 = Dist (points[elem.PNum(2)], points[elem.PNum(3)]);
  l5 = Dist (points[elem.PNum(2)], points[elem.PNum(4)]);
  l6 = Dist (points[elem.PNum(3)], points[elem.PNum(4)]);

  l = v1.Length() + v2.Length() + v3.Length() + l4 + l5 + l6;
  
  //  testout << "vol = " << vol << " l = " << l << endl;
  if (vol < 1e-8) return 1e10;
  //  (*testout) << "l^3/vol = " << (l*l*l / vol) << endl;
  
  double err = pow (l*l*l/vol, 1.0/3.0) / 12;
  return err;
}







// Each rule is claimed exactly once. Its mutable free-zone and diagnostic slots
// are exclusively owned until the team joins. Front and rotation order stay serial.
struct RuleSearchQueue {
  std::atomic<int> next{0};
  std::vector<int> order;
  std::vector<double> seconds;
  int face_begin=1, face_end=0; // Inclusive second-face prefix interval; zero means unsplit.
  bool measure;
  explicit RuleSearchQueue(int count,bool measured=false):order(count),seconds(count,0),measure(measured) {
    std::iota(order.begin(),order.end(),0);
  }
  int Claim(int count) { int i=next.fetch_add(1,std::memory_order_relaxed);
    return i<int(order.size())?order[i]:count; }
};
struct RuleSearchResult {
  float quality = 0, error = 0;
  int found = -1;
  double other = 0, withoutother = 0, seconds = 0;
  unsigned long long replay_face_trials=0,replay_point_trials=0;
  unsigned long long rules = 0, eligible = 0, face_trials = 0, point_trials = 0, mappings = 0;
  double prefix_min=1e100; // Valid candidates before the first diagnostic state write.
  std::string trace;
  Array<Point3d,PointIndex> points;
  Array<MiniElement2d> faces;
  NgArray<Element> elements;
  NgArray<INDEX> deleted;
};
namespace {
struct RuleLegacyTimer {
  int index; bool active;
  RuleLegacyTimer(int i, bool a):index(i),active(a) { if(active) NgProfiler::StartTimer(i); }
  ~RuleLegacyTimer() { if(active) NgProfiler::StopTimer(index); }
};
using RuleClock = std::chrono::steady_clock;
double RuleElapsed(RuleClock::time_point t) {
  return std::chrono::duration<double>(RuleClock::now()-t).count();
}
template<class T> bool RuleBitsEqual(T a, T b) { return std::memcmp(&a,&b,sizeof(T))==0; }
bool SameRuleOutput(const RuleSearchResult & a,
                    const Array<Point3d,PointIndex> & points,
                    const Array<MiniElement2d> & faces,
                    const NgArray<Element> & elements, const NgArray<INDEX> & deleted) {
  if(a.points.Size()!=points.Size() || a.faces.Size()!=faces.Size() ||
     a.elements.Size()!=elements.Size() || a.deleted.Size()!=deleted.Size()) return false;
  for(auto i:points.Range())
    for(int j=0;j<3;j++) if(!RuleBitsEqual(a.points[i].X(j+1),points[i].X(j+1))) return false;
  for(auto i:faces.Range()) {
    if(a.faces[i].GetNP()!=faces[i].GetNP()) return false;
    for(int j=1;j<=faces[i].GetNP();j++) if(a.faces[i].PNum(j)!=faces[i].PNum(j)) return false;
  }
  for(auto i:elements.Range()) {
    if(a.elements[i].GetType()!=elements[i].GetType() || a.elements[i].GetNP()!=elements[i].GetNP()) return false;
    for(int j=1;j<=elements[i].GetNP();j++) if(a.elements[i].PNum(j)!=elements[i].PNum(j)) return false;
  }
  for(auto i:deleted.Range()) if(a.deleted[i]!=deleted[i]) return false;
  return true;
}
void RecordRuleWork(VolumeKernelStats * s, const RuleSearchResult & r) {
  if(!s) return;
  s->AddRule(4,r.rules); s->AddRule(5,r.eligible);
  s->AddRule(6,r.face_trials); s->AddRule(7,r.point_trials); s->AddRule(8,r.mappings);
}
// Versioned native-endian packets are used only inside one homogeneous MPI job.
// No pointers or mesh ownership cross processes; count/length checks precede reads.
struct RulePacket {
  std::string data; size_t position=0;
  static constexpr size_t limit=64*1024*1024;
  RulePacket()=default;
  RulePacket(const char * p,int n) {
    if(n<0 || size_t(n)>limit) throw NgException("规则消息长度非法");
    data.assign(p,n);
  }
  template<class T> void Put(T value) {
    if(data.size()+sizeof(T)>limit) throw NgException("规则消息超过容量");
    data.append(reinterpret_cast<const char*>(&value),sizeof(T));
  }
  template<class T> T Get() {
    if(position+sizeof(T)>data.size()) throw NgException("规则消息截断");
    T value;std::memcpy(&value,data.data()+position,sizeof(T));position+=sizeof(T);return value;
  }
  int Count() {int n=Get<int>();if(n<0 || n>1000000) throw NgException("规则消息计数非法");return n;}
  void Text(const std::string & s) {Put(int(s.size()));for(char c:s) Put(c);}
  std::string Text() {int n=Count();if(position+size_t(n)>data.size()) throw NgException("规则消息文本截断");
    auto s=data.substr(position,n);position+=n;return s;}
  void End() {if(position!=data.size()) throw NgException("规则消息包含多余数据");}
};
void PutRuleGeometry(RulePacket & packet,const RuleSearchResult & r) {
  packet.Put(int(r.points.Size()));
  for(auto p:r.points) for(int j=1;j<=3;++j) packet.Put(p.X(j));
  packet.Put(int(r.faces.Size()));
  for(auto f:r.faces) {packet.Put(f.GetNP());for(int j=1;j<=f.GetNP();++j) packet.Put(int(f.PNum(j)));}
}
void GetRuleGeometry(RulePacket & packet,RuleSearchResult & r) {
  r.points.SetSize(packet.Count());
  for(auto & p:r.points) for(int j=1;j<=3;++j) p.X(j)=packet.Get<double>();
  r.faces.SetSize(packet.Count());
  for(auto & f:r.faces) {
    int n=packet.Get<int>();if(n!=3 && n!=4) throw NgException("规则消息面类型非法");
    f=MiniElement2d(n);
    for(int j=1;j<=n;++j) {int i=packet.Get<int>();if(i<1 || i>r.points.Size()) throw NgException("规则消息点索引非法");f.PNum(j)=i;}
  }
}
void PutRuleResult(RulePacket & packet,const RuleSearchResult & r) {
  packet.Put(r.found);packet.Put(r.quality);packet.Put(r.error);
  packet.Put(r.other);packet.Put(r.withoutother);packet.Put(r.seconds);
  for(auto x:{r.rules,r.eligible,r.face_trials,r.point_trials,r.mappings}) packet.Put(x);
  packet.Text(r.trace);PutRuleGeometry(packet,r);
  packet.Put(int(r.elements.Size()));
  for(auto & e:r.elements) {packet.Put(int(e.GetType()));packet.Put(e.GetNP());for(int j=1;j<=e.GetNP();++j) packet.Put(int(e.PNum(j)));}
  packet.Put(int(r.deleted.Size()));for(int d:r.deleted) packet.Put(d);
}
void GetRuleResult(RulePacket & packet,RuleSearchResult & r) {
  r.found=packet.Get<int>();r.quality=packet.Get<float>();r.error=packet.Get<float>();
  r.other=packet.Get<double>();r.withoutother=packet.Get<double>();r.seconds=packet.Get<double>();
  r.rules=packet.Get<unsigned long long>();r.eligible=packet.Get<unsigned long long>();
  r.face_trials=packet.Get<unsigned long long>();r.point_trials=packet.Get<unsigned long long>();r.mappings=packet.Get<unsigned long long>();
  r.trace=packet.Text();GetRuleGeometry(packet,r);
  r.elements.SetSize(0);int count=packet.Count();
  for(int i=0;i<count;++i) {
    int type=packet.Get<int>();if(type!=TET && type!=PYRAMID && type!=PRISM) throw NgException("规则消息单元类型非法");
    Element e(static_cast<ELEMENT_TYPE>(type));int n=packet.Count();
    if(n!=e.GetNP()) throw NgException("规则消息单元点数非法");
    for(int j=1;j<=n;++j) {int pi=packet.Get<int>();if(pi<1 || pi>r.points.Size()) throw NgException("规则消息单元索引非法");e.PNum(j)=pi;}
    r.elements.Append(e);
  }
  count=packet.Count();r.deleted.SetSize(count);for(auto & d:r.deleted) d=packet.Get<int>();
}
}

std::string Meshing3::ExecuteRuleSearchPacket(const char * data,int bytes) {
  RulePacket request(data,bytes),response;
  if(request.Get<unsigned>()!=0x52534e31) throw NgException("规则消息版本不匹配");
  static thread_local std::unique_ptr<Meshing3> helper;
  if(!helper) helper.reset(new Meshing3(static_cast<const char **>(nullptr)));
  auto & m=*helper;
  const int count=request.Count();if(count!=m.rules.Size()) throw NgException("远程规则集合不匹配");
  RuleSearchQueue queue(count,true);queue.order.clear();
  int tasks=request.Count();std::vector<bool> seen(count,false);
  for(int i=0;i<tasks;++i) {int id=request.Get<int>();
    if(id<0 || id>=count || seen[id]) throw NgException("远程规则任务重复或越界");
    seen[id]=true;queue.order.push_back(id);}
  int tol=request.Get<int>(),rot=request.Get<int>();double sloppy=request.Get<double>();
  if(tol<1 || tol>5 || rot<1 || rot>4 || !std::isfinite(sloppy)) throw NgException("远程规则参数非法");
  struct Restore { double other=minother,without=minwithoutother;
    ~Restore(){minother=other;minwithoutother=without;} } restore;
  minother=request.Get<double>();minwithoutother=request.Get<double>();
  RuleSearchResult result;GetRuleGeometry(request,result);
  if(result.faces.Size()==0) throw NgException("远程规则缺少基面");
  Array<int,PointIndex> allow(result.points.Size());for(auto & v:allow) v=request.Get<int>();
  int split=request.Count();if(split<1 || split>result.faces.Size()) throw NgException("远程规则面分割非法");
  int npairs=request.Count();INDEX_2_HASHTABLE<int> pairs(std::max(1,npairs*2));
  for(int i=0;i<npairs;++i) {int a=request.Get<int>(),b=request.Get<int>(),v=request.Get<int>();
    if(a<1 || b<1 || a>result.points.Size() || b>result.points.Size()) throw NgException("远程连接点非法");
    pairs.Set(INDEX_2(a,b),v);}
  request.End();m.foundmap=0;m.canuse=0;
  const auto start=RuleClock::now();
  result.found=m.ApplyRulesImpl(result.points,allow,result.faces,split,pairs,result.elements,result.deleted,
                               tol,sloppy,rot,result.error,&queue,&result);
  result.seconds=RuleElapsed(start);response.Put(unsigned(0x52534f31));PutRuleResult(response,result);
  response.Put(tasks);
  for(int id:queue.order) {response.Put(id);response.Put(m.foundmap[id]);response.Put(m.canuse[id]);
    response.Text(m.problems[id]);response.Put(queue.seconds[id]);}
  return std::move(response.data);
}

int Meshing3::ApplyRules(Array<Point3d,PointIndex> & p, Array<int,PointIndex> & a,
  Array<MiniElement2d> & f, INDEX split, INDEX_2_HASHTABLE<int> & pairs,
  NgArray<Element> & e, NgArray<INDEX> & d, int tol, double sloppy, int rot, float & err) {
  return ApplyRulesImpl(p,a,f,split,pairs,e,d,tol,sloppy,rot,err,nullptr,nullptr);
}

int Meshing3::ApplyRulesTasks(Array<Point3d,PointIndex> & p, Array<int,PointIndex> & a,
  Array<MiniElement2d> & f, INDEX split, INDEX_2_HASHTABLE<int> & pairs,
  NgArray<Element> & e, NgArray<INDEX> & d, int tol, double sloppy, int rot, float & err,
  int mode, VolumeKernelStats * stats, double remaining_faces) {
  if(mode<0 || (!mode && !stats)) return ApplyRules(p,a,f,split,pairs,e,d,tol,sloppy,rot,err);
  const auto begin=RuleClock::now();
  if(stats) stats->AddRule(0,1);
  struct CallTimer {
    VolumeKernelStats * stats; RuleClock::time_point begin;
    ~CallTimer() {
      if(!stats) return;
      double t=RuleElapsed(begin); stats->AddRule(9,t); stats->MaxRule(15,t);
      const double edges[]={32e-6,128e-6,512e-6,2e-3,8e-3};
      int b=0; while(b<5 && t>edges[b]) ++b;
      stats->AddRule(18+b,1);
    }
  } call_timer{stats,begin};
  // Verbose/test rules keep the original ordered diagnostics. Nested teams
  // also fall back: no extra workers or unaccounted resource oversubscription.
  bool debug=testmode || tol>5;
  for(auto & rule:rules) debug=debug || rule->TestFlag('t');
  bool parallel=mode && !debug && rules.Size()>1 && ngcore::task_manager &&
                ngcore::TaskManager::GetNumThreads()>1;
  if(!parallel) {
    RuleSearchResult work;
    int found=ApplyRulesImpl(p,a,f,split,pairs,e,d,tol,sloppy,rot,err,nullptr,stats?&work:nullptr);
    RecordRuleWork(stats,work);
    if(stats) { stats->AddRule(3,1); if(mode && debug) stats->AddRule(16,1); }
    return found;
  }
  const bool adaptive=mode>=3;
  const bool verify=mode==2 || mode==4 || mode==6;
  int point_bin=0,face_bin=0;
  for(int size=p.Size();size>1;size>>=1) ++point_bin;
  for(int size=f.Size();size>1;size>>=1) ++face_bin;
  const int bucket=(tol+8*f[0].GetNP())*1024+std::min(31,point_bin)*32+std::min(31,face_bin);
  SearchProfile * profile=adaptive?&search_profiles[bucket]:nullptr;
  const int available=std::min(int(rules.Size()),ngcore::TaskManager::GetNumThreads());
  int workers=available;
  bool sample=false;
  if(adaptive) {
    profile->rules.resize(rules.Size());sample=profile->Sample();
    // Exact structural concurrency, not a noisy serial-time counterfactual.
    // Ineligible rules still execute their original checks and diagnostics.
    int eligible=0;
    for(auto &rule:rules)
      eligible+=rule->GetNP(1)==f[0].GetNP() && rule->GetQuality()<=tol;
    workers=std::min(available,std::max(1,eligible));
  }
  int split_rule=-1,parts=0;
  if(adaptive && standard_rules && split>1 && available>1) {
    double largest=0;std::vector<double> whole_cost;
    for(int id=0;id<rules.Size();++id) {
      const double cost=profile->rules[id].Upper();whole_cost.push_back(cost);
      if(rules[id]->GetNP(1)==f[0].GetNP() && rules[id]->GetQuality()<=tol &&
         rules[id]->GetNOldF()>=2 && cost>largest) {largest=cost;split_rule=id;}
    }
    // Split only a predicted serial bottleneck; preprocessing is paid per slice.
    // Modes 5/6 force structural coverage for internal differential tests only.
    if(mode>=5) {
      for(int n=0;n<rules.Size();++n) {
        int id=(profile->calls+n)%rules.Size();
        if(rules[id]->GetNP(1)==f[0].GetNP() && rules[id]->GetQuality()<=tol && rules[id]->GetNOldF()>=2)
          {split_rule=id;break;}
      }
    } else if(split_rule>=0) {
      const int slices=std::min(available,int(split));
      auto sliced=whole_cost;
      const double per_slice=largest/slices+profile->preprocessing.Upper();
      sliced[split_rule]=per_slice;
      for(int i=1;i<slices;++i) sliced.push_back(per_slice);
      if(AssignSearch(whole_cost,available,false).local_seconds <=
         AssignSearch(sliced,available,false).local_seconds+profile->copy.Upper()) split_rule=-1;
    }
    if(mode<5 && (sample || (split_rule==profile->subtree_rule && profile->subtree.samples &&
        profile->subtree.Upper()>=profile->rules[split_rule].Lower()))) split_rule=-1;
    if(split_rule>=0) parts=std::min(available,int(split));
  }
  workers=std::max(workers,parts);
  if(adaptive && stats) {stats->AddAdaptive(0,1);stats->AddAdaptive(workers==1?1:2,1);}
  if(stats) stats->AddRule(workers>1?1:3,1);
  const double old_other=minother, old_without=minwithoutother;
  Array<int> old_found,old_can;
  if(verify) { old_found=foundmap;old_can=canuse; }
  std::vector<std::unique_ptr<RuleSearchResult>> results;
  auto stamp=RuleClock::now();
  for(int i=0;i<workers;i++) {
    results.emplace_back(new RuleSearchResult);
    results.back()->points=p;results.back()->faces=f;
  }
  const double copy_seconds=RuleElapsed(stamp);
  if(stats) stats->AddRule(10,copy_seconds);
  RuleSearchQueue queue(rules.Size(),sample);
  bool remote=false;
  double packet_seconds=0;
  RulePacket remote_request;
  if(adaptive && !profile->order.empty()) queue.order=profile->order;
  // Progress communication even when this call has no profitable remote job.
  if(adaptive && search_transport && search_transport->ready) search_transport->ready(search_transport->context);
  if(adaptive && search_transport && split_rule>=0)
    search_transport->offer(search_transport->context,0,0,0);
  if(adaptive && search_transport && split_rule<0) {
    if(sample || profile->plan_threads!=workers) {
      std::vector<double> cost,lower;
      for(int id=0;id<rules.Size();++id) {
        const bool eligible=rules[id]->GetNP(1)==f[0].GetNP() && rules[id]->GetQuality()<=tol;
        cost.push_back(eligible?profile->rules[id].Upper():0);
        lower.push_back(eligible?profile->rules[id].Lower():0);
      }
      auto local=AssignSearch(lower,workers,false);
      auto distributed=AssignSearch(cost,workers,true,profile->overhead.Upper());
      profile->remote_gain=local.local_seconds-std::max(distributed.local_seconds,distributed.remote_seconds);
      profile->remote_work=std::max(0.0,distributed.remote_seconds-profile->overhead.Upper());
      profile->remote_local=std::move(distributed.local);profile->remote_jobs=std::move(distributed.remote);
      profile->plan_threads=workers;
    }
    const double gain=profile->remote_gain;
    const bool candidate=workers>1 && standard_rules && !profile->remote_jobs.empty() && gain>0;
    // Consume/decline an arrived credit even on an unprofitable query, so a
    // completed helper can continue to another client instead of getting stuck.
    // Size estimate includes both snapshot and returned geometry, plus packet
    // metadata. This is a model input, not a hard bound on the returned packet.
    int transfer_bytes=1;
    if(candidate && search_transport->offer_sized) {
      size_t wire=128+4*profile->remote_jobs.size()+28*p.Size();
      for(auto face:f) wire+=4+4*face.GetNP();
      for(int bag=1;bag<=pairs.GetNBags();++bag) wire+=12*pairs.GetBagSize(bag);
      transfer_bytes=int(std::min(size_t(2*RulePacket::limit),2*wire+4096));
    }
    const double remaining=std::max(1.0,remaining_faces)*profile->parallel.mean;
    const bool accepted=search_transport->offer_sized ?
        search_transport->offer_sized(search_transport->context,candidate?gain:0,remaining,profile->remote_work,transfer_bytes) :
        search_transport->offer(search_transport->context,candidate?gain:0,remaining,profile->remote_work);
    if(accepted) {
      if(!candidate) throw NgException("远程协议接受了无有效任务的查询");
      remote=true;queue.order=profile->remote_local;
      const auto packing=RuleClock::now();
      remote_request.Put(unsigned(0x52534e31));remote_request.Put(int(rules.Size()));
      remote_request.Put(int(profile->remote_jobs.size()));for(int id:profile->remote_jobs) remote_request.Put(id);
      remote_request.Put(tol);remote_request.Put(rot);remote_request.Put(sloppy);
      remote_request.Put(old_other);remote_request.Put(old_without);
      PutRuleGeometry(remote_request,*results[0]);for(int v:a) remote_request.Put(v);
      remote_request.Put(int(split));int npairs=0;
      for(int b=1;b<=pairs.GetNBags();++b) npairs+=pairs.GetBagSize(b);
      remote_request.Put(npairs);
      for(int b=1;b<=pairs.GetNBags();++b) for(int j=1;j<=pairs.GetBagSize(b);++j) {
        INDEX_2 edge;int value;pairs.GetData(b,j,edge,value);
        remote_request.Put(edge.I1());remote_request.Put(edge.I2());remote_request.Put(value);
      }
      search_transport->start(search_transport->context,remote_request.data.data(),remote_request.data.size());
      packet_seconds=RuleElapsed(packing);
      if(stats) {stats->AddAdaptive(3,1);stats->AddAdaptive(4,profile->remote_jobs.size());stats->AddAdaptive(5,gain);}
    }
  }
  if(split_rule>=0) queue.order.erase(std::remove(queue.order.begin(),queue.order.end(),split_rule),queue.order.end());
  stamp=RuleClock::now();
  auto execute=[&](size_t i) {
    auto & r=*results[i];const auto t=RuleClock::now();
    r.found=ApplyRulesImpl(r.points,a,r.faces,split,pairs,r.elements,r.deleted,
                          tol,sloppy,rot,r.error,&queue,&r);
    r.seconds=RuleElapsed(t);
  };
  // No barrier between whole rules and subtrees: each worker searches its
  // disjoint slice first, then joins the ordinary-rule queue as soon as it is free.
  // Result slots and interval-order merging are unchanged.
  const int first=int(results.size());
  std::vector<double> times(parts,0),preprocessing(parts,0);
  if(split_rule>=0) {
    const auto setup=RuleClock::now();
    while(int(subtree_workers.size())<parts)
      subtree_workers.emplace_back(new Meshing3(static_cast<const char **>(nullptr)));
    for(int i=0;i<parts;++i) {
      results.emplace_back(new RuleSearchResult);results.back()->points=p;results.back()->faces=f;
    }
    if(stats) stats->AddAdaptive(10,RuleElapsed(setup));
  }
  const auto branches=RuleClock::now();
  auto unified=[&](size_t i) {
    if(int(i)<parts) {
      auto &m=*subtree_workers[i];auto &r=*results[first+i];
      m.foundmap=0;m.canuse=0;
      RuleSearchQueue sub(rules.Size(),true);sub.order={split_rule};
      sub.face_begin=1+int(i)*split/parts;sub.face_end=int(i+1)*split/parts;
      const auto t=RuleClock::now();
      r.found=m.ApplyRulesImpl(r.points,a,r.faces,split,pairs,r.elements,r.deleted,tol,sloppy,rot,r.error,&sub,&r);
      r.seconds=RuleElapsed(t);times[i]=sub.seconds[split_rule];
      preprocessing[i]=std::max(0.0,r.seconds-times[i]);
      if(i) {r.rules=0;r.eligible=0;}
    }
    execute(i);
  };
  if(workers==1) unified(0);else ngcore::ParallelFor(size_t(workers),unified,workers);
  if(split_rule>=0) {
    double elapsed=RuleElapsed(branches);
    if(profile->subtree_rule!=split_rule) {profile->subtree=SearchEstimate{};profile->subtree_rule=split_rule;}
    profile->subtree.Observe(*std::max_element(times.begin(),times.end())+
                             *std::max_element(preprocessing.begin(),preprocessing.end()));
    if(stats) {stats->AddAdaptive(8,1);stats->AddAdaptive(9,parts);stats->AddAdaptive(11,elapsed);}
    queue.seconds[split_rule]=std::accumulate(times.begin(),times.end(),0.0);
    profile->preprocessing.Observe(*std::max_element(preprocessing.begin(),preprocessing.end()));
    // Prefix intervals are disjoint and ordered. Diagnostics form a state
    // transducer: a slice inherits the preceding last nonempty problem string.
    // Its prefix minimum is classified only after that incoming state is known.
    problems[split_rule]="";
    for(int i=0;i<parts;++i) {
      auto &m=*subtree_workers[i];auto &r=*results[first+i];
      if(stats) {stats->AddAdaptive(12,r.replay_face_trials);stats->AddAdaptive(13,r.replay_point_trials);}
      foundmap[split_rule]+=m.foundmap[split_rule];canuse[split_rule]+=m.canuse[split_rule];
      if(problems[split_rule]=="other") r.other=std::min(r.other,r.prefix_min);
      else r.withoutother=std::min(r.withoutother,r.prefix_min);
      if(!m.problems[split_rule].empty()) problems[split_rule]=m.problems[split_rule];
    }
  }
  if(stats) stats->AddRule(11,RuleElapsed(stamp));
  if(remote) {
    const auto wait=RuleClock::now();int bytes=0;
    const char * data=search_transport->finish(search_transport->context,&bytes);
    if(stats) stats->AddAdaptive(6,RuleElapsed(wait));
    const auto unpacking=RuleClock::now();
    RulePacket response(data,bytes);
    if(response.Get<unsigned>()!=0x52534f31) throw NgException("远程规则响应版本不匹配");
    results.emplace_back(new RuleSearchResult);GetRuleResult(response,*results.back());
    std::vector<bool> covered(rules.Size(),false);for(int id:queue.order) covered[id]=true;
    int count=response.Count();
    for(int i=0;i<count;++i) {int id=response.Get<int>();
      if(id<0 || id>=rules.Size() || covered[id]) throw NgException("远程规则覆盖重复");
      covered[id]=true;foundmap[id]+=response.Get<int>();canuse[id]+=response.Get<int>();
      problems[id]=response.Text();queue.seconds[id]=response.Get<double>();}
    response.End();for(bool value:covered) if(!value) throw NgException("远程规则覆盖不完整");
    double remote_rule_seconds=0;
    for(int id=0;id<rules.Size();++id)
      if(std::find(queue.order.begin(),queue.order.end(),id)==queue.order.end()) remote_rule_seconds+=queue.seconds[id];
    const double helper_preprocessing=std::max(0.0,results.back()->seconds-remote_rule_seconds);
    profile->overhead.Observe(packet_seconds+RuleElapsed(unpacking)+helper_preprocessing);
    profile->plan_threads=0;
  }
  const double execution_seconds=RuleElapsed(stamp)+copy_seconds;
  if(adaptive) {
    if(sample) {
      std::vector<double> cost;
      for(int id=0;id<rules.Size();++id) {
        profile->rules[id].Observe(queue.seconds[id]);cost.push_back(profile->rules[id].Upper());
      }
      profile->order=AssignSearch(cost,workers,false).local;profile->plan_threads=0;
      if(stats) stats->AddAdaptive(7,1); // sampled calls, separate from rule work
    }
    profile->copy.Observe(copy_seconds);
    if(!remote) (workers==1?profile->serial:profile->parallel).Observe(execution_seconds);
  }
  int winner=-1, found=-1;
  double longest=0;
  for(int i=0;i<int(results.size());i++) {
    const auto & r=*results[i];RecordRuleWork(stats,r);
    if(stats) stats->AddRule(12,r.seconds);
    longest=std::max(longest,r.seconds);
    minother=std::min(minother,r.other);minwithoutother=std::min(minwithoutother,r.withoutother);
    if(!r.trace.empty()) (*testout)<<r.trace;
    // Strict original comparison: equal qualities keep the earliest rule.
    if(r.found>0 && (winner<0 || r.quality<results[winner]->quality ||
       (r.quality==results[winner]->quality && r.found<results[winner]->found))) winner=i;
    if(r.found==0) found=0;
  }
  if(stats) stats->AddRule(13,longest);
  if(winner>=0) found=results[winner]->found;
  err=results[0]->error;
  if(verify) {
    const auto verify_begin=RuleClock::now();
    Array<int> parallel_found(foundmap),parallel_can(canuse);
    Array<string> parallel_problems(problems);
    foundmap=old_found;canuse=old_can;
    const double parallel_other=minother,parallel_without=minwithoutother;
    minother=old_other;minwithoutother=old_without;
    float serial_error;
    RuleSearchResult reference;
    int serial_found=ApplyRulesImpl(p,a,f,split,pairs,e,d,tol,sloppy,rot,serial_error,nullptr,&reference);
    bool same=serial_found==found && RuleBitsEqual(serial_error,err) &&
      RuleBitsEqual(minother,parallel_other) && RuleBitsEqual(minwithoutother,parallel_without);
    int diagnostic_difference=-1;
    for(int i:foundmap.Range()) if(foundmap[i]!=parallel_found[i] || canuse[i]!=parallel_can[i] || problems[i]!=parallel_problems[i])
      {same=false;diagnostic_difference=i;}
    const bool same_output=SameRuleOutput(*results[winner>=0?winner:0],p,f,e,d);
    // No-winner output must also preserve the input arrays and empty outputs.
    same=same && same_output;
    unsigned long long face=0,point=0,maps=0;
    for(auto & r:results) {face+=r->face_trials;point+=r->point_trials;maps+=r->mappings;}
    same=same && face==reference.face_trials && point==reference.point_trials && maps==reference.mappings;
    if(stats) {stats->AddRule(2,workers>1?1:0);stats->AddRule(14,RuleElapsed(verify_begin));}
    if(!same) {
      if(stats) stats->AddRule(17,1);
      throw NgException("规则搜索等价检查失败: class="+ToString(tol)+" rotation="+ToString(rot)+
                        " serial="+ToString(serial_found)+" parallel="+ToString(found)+
                        " split="+ToString(split_rule)+" diagnostic="+ToString(diagnostic_difference)+
                        " output="+ToString(same_output)+" minima="+ToString(minother)+","+ToString(parallel_other)+
                        "/"+ToString(minwithoutother)+","+ToString(parallel_without)+
                        " trials="+ToString(face)+","+ToString(reference.face_trials)+
                        "/"+ToString(point)+","+ToString(reference.point_trials)+
                        "/"+ToString(maps)+","+ToString(reference.mappings));
    }
    // Serial verification has already materialized the exactly checked output.
  } else if(winner>=0) {
    p=results[winner]->points;f=results[winner]->faces;
    e=results[winner]->elements;d=results[winner]->deleted;
  } else {e.SetSize(0);d.SetSize(0);}
  // This includes verification when enabled; the separate verification metric
  // is explanatory only, and is never subtracted from natural core timings.
  return found;
}

int Meshing3 :: ApplyRulesImpl
(
 Array<Point3d, PointIndex> & lpoints,     // in: local points, out: old+new local points
 Array<int, PointIndex> & allowpoint,      // in: 2 .. it is allowed to use pointi, 1..will be allowed later, 0..no means
 Array<MiniElement2d> & lfaces,    // in: local faces, out: old+new local faces
 INDEX lfacesplit,	       // for local faces in outer radius
 INDEX_2_HASHTABLE<int> & connectedpairs,  // connected pairs for prism-meshing
 NgArray<Element> & elements,    // out: new elements
 NgArray<INDEX> & delfaces,      // out: face indices of faces to delete
 int tolerance,                // quality class: 1 best 
 double sloppy,                // quality strength
 int rotind1,                  // how to rotate base element
 float & retminerr,            // element error
 RuleSearchQueue * queue, RuleSearchResult * result
 )

{
  // static Timer t("ruler3 - all"); RegionTimer reg(t);
  // static Timer tstart("ruler3 - rule start");   
  // static Timer tloop("ruler3 - rule loop"); 

  // tstart.Start();
  float err, minerr, teterr, minteterr;
  char ok, found, hc;
  // vnetrule * rule;
  Vector oldu, newu, newu1, newu2, allp;
  Vec3d ui;
  Point3d np;
  const MiniElement2d * locface = NULL;
  int loktestmode;


  NgArray<int, PointIndex::BASE> pused;      // point is already mapped, number of uses
  NgArrayMem<char,100> fused;                       // face is already mapped
  NgArrayMem<PointIndex,100> pmap;                  // map of reference point to local point
  NgArrayMem<bool,100> pfixed;                      // point mapped by face-map
  NgArrayMem<int,100> fmapi;                        // face in reference is mapped to face nr ...
  NgArrayMem<int,100> fmapr;                        // face in reference is rotated to map 
  NgArrayMem<Point3d,100> transfreezone;            // transformed free-zone
  INDEX_2_CLOSED_HASHTABLE<int> ledges(100); // edges in local environment
  
  NgArrayMem<Point3d,100> tempnewpoints;
  NgArray<MiniElement2d> tempnewfaces;
  NgArrayMem<int,100> tempdelfaces;
  NgArray<Element> tempelements;
  NgArrayMem<Box3d,100> triboxes;         // bounding boxes of local faces

  NgArray<int, PointIndex::BASE> pnearness;
  NgArray<int> fnearness;

  static int cnt = 0;
  if (!queue) cnt++;
  std::unique_ptr<std::ostringstream> worker_trace;
  if(queue) worker_trace.reset(new std::ostringstream);
  std::ostream & rule_out = queue ? *worker_trace : *testout;
  double local_minother = minother, local_minwithoutother = minwithoutother;
  
  delfaces.SetSize (0);
  elements.SetSize (0);

  // determine topological distance of faces and points to
  // base element

  pnearness.SetSize (lpoints.Size());
  fnearness.SetSize (lfacesplit);

  pnearness = INT_MAX/10;

  for (PointIndex pi : lfaces[0].PNums())
    pnearness[pi] = 0;
  
  RuleLegacyTimer reg2(98, !queue);
  
  if (!queue) NgProfiler::StartTimer (90);

  for (int loop = 0; loop < 2; loop++)
    {

      for (int i = 0; i < lfacesplit; i++)
	{
	  const MiniElement2d & hface = lfaces[i];

	  int minn = INT_MAX-1;
	  for (PointIndex pi : hface.PNums())
	    {
	      int hi = pnearness[pi];
	      if (hi < minn) minn = hi;
	    }
	  if (minn < INT_MAX/10)
	    for (PointIndex pi : hface.PNums())
	      if (pnearness[pi] > minn+1)
		pnearness[pi] = minn+1;
	}

      for (int i = 1; i <= connectedpairs.GetNBags(); i++)
	for (int j = 1; j <= connectedpairs.GetBagSize(i); j++)
	  {
	    INDEX_2 edge;
	    int val;
	    connectedpairs.GetData (i, j, edge, val);

	    if (pnearness[edge.I1()] > pnearness[edge.I2()] + 1)
	      pnearness[edge.I1()] = pnearness[edge.I2()] + 1;

	    if (pnearness[edge.I2()] > pnearness[edge.I1()] + 1)
	      pnearness[edge.I2()] = pnearness[edge.I1()] + 1;
	  }
    }

  for (int i : fnearness.Range())
    {
      int sum = 0;
      for (PointIndex pi : lfaces[i].PNums())
        sum += pnearness[pi];
      fnearness[i] = sum;
    }

  
  if (!queue) NgProfiler::StopTimer (90);
  if (!queue) NgProfiler::StartTimer (91);

  // find bounding boxes of faces

  triboxes.SetSize (lfaces.Size());
  // for (int i = 0; i < lfaces.Size(); i++)
  for (auto i : lfaces.Range())
    {
      const MiniElement2d & face = lfaces[i];
      triboxes[i].SetPoint (lpoints[face[0]]);
      for (int j = 1; j < face.GetNP(); j++)
	triboxes[i].AddPoint (lpoints[face[j]]);
    }

  if (!queue) NgProfiler::StopTimer (91);
  if (!queue) NgProfiler::StartTimer (92);

  
  bool useedges = false;
  for (int ri = 0; ri < rules.Size(); ri++)
    if (rules[ri]->GetNEd()) useedges = true;

  if (useedges)
    {
      ledges.SetSize (5 * lfacesplit);
      
      for (int j = 0; j < lfacesplit; j++)
	// if (fnearness[j] <= 5) 
	  {
	    const MiniElement2d & face = lfaces[j];
	    int newp, oldp;
	    
	    newp = face[face.GetNP()-1];
	    for (int k = 0; k < face.GetNP(); k++)
	      {
		oldp = newp;
		newp = face[k];
		ledges.Set (INDEX_2::Sort(oldp, newp), 1);
	      }
	  }
    }

  if (!queue) NgProfiler::StopTimer (92);

  RuleLegacyTimer reg3(99, !queue);

  pused.SetSize (lpoints.Size());
  fused.SetSize (lfaces.Size());

  found = 0;
  minerr = tolfak * tolerance * tolerance;
  minteterr = sloppy * tolerance;

  if (testmode)
    rule_out << "cnt = " << cnt << " class = " << tolerance << endl;



  // impossible, if no rule can be applied at any tolerance class
  bool impossible = 1;


  // check each rule:
  // tstart.Stop();
  // tloop.Start();
  int serial_rule = 0;
  for (int rim = queue ? queue->Claim(rules.Size()) : serial_rule++;
       rim < rules.Size();
       rim = queue ? queue->Claim(rules.Size()) : serial_rule++)
    {
      struct RuleTaskTimer {
        RuleSearchQueue * queue; int id; RuleClock::time_point start;
        ~RuleTaskTimer() {if(queue && queue->measure) queue->seconds[id]=RuleElapsed(start);}
      } task_timer{queue,rim,queue && queue->measure?RuleClock::now():RuleClock::time_point{}};
      if (result) result->rules++;
      int base = (lfaces[0].GetNP() == 3) ? 100 : 200;
      RuleLegacyTimer regx1(base, !queue);
      RuleLegacyTimer regx(base+rim+1, !queue);

      // sprintf (problems.Elem(ri), "");
      // *problems.Elem(ri) = '\0';
      problems[rim] = "";

      vnetrule * rule = rules[rim].get();
      
      if (rule->GetNP(1) != lfaces[0].GetNP())
	continue;

      if (rule->GetQuality() > tolerance)
	{
	  if (rule->GetQuality() < 100) impossible = 0;

	  if (testmode)
	    problems[rim] = "Quality not ok";
	  continue;
	}
      
      if (testmode)
	problems[rim] = "no mapping found";
      
      if (result) result->eligible++;
      loktestmode = testmode || rule->TestFlag ('t') || tolerance > 5;

      if (loktestmode)
	rule_out << "Rule " << rim+1 << " = " << rule->Name() << endl;
      
      pmap.SetSize (rule->GetNP());
      fmapi.SetSize (rule->GetNF());
      fmapr.SetSize (rule->GetNF());
      
      fused = 0;
      pused = 0;
      for (auto & p : pmap) p.Invalidate();
      fmapi = 0;

      for (int i : fmapr.Range())
        fmapr[i] = rule->GetNP(i+1);
      
      fused[0] = 1;
      fmapi[0] = 1;
      fmapr[0] = rotind1;

      for (int j = 1; j <= lfaces[0].GetNP(); j++)
	{
	  PointIndex locpi = lfaces[0].PNumMod (j+rotind1);
	  pmap.Set (rule->GetPointNr (1, j), locpi);
	  pused[locpi]++;
	}

      /*
	map all faces
	nfok .. first nfok-1 faces are mapped properly
	*/

      const bool sliced=queue && queue->face_end>0;
      // Replay the prefix's mapping traversal (without geometry validation).
      // The original DFS retains fused-face state across siblings. Starting
      // from a clean slice would silently change its search, even with disjoint
      // face ranges. Prefix replay reconstructs exactly that traversal state.
      int nfok = 2;
      RuleLegacyTimer regfa(300, !queue);
      RuleLegacyTimer regx2(base+50+rim+1, !queue);
      while (nfok >= 2)
	{
	  
	  if (nfok <= rule->GetNOldF())
	    {
	      // not all faces mapped

	      ok = 0;
	      int locfi = fmapi.Get(nfok);
	      int locfr = fmapr.Get(nfok);

	      int actfnp = rule->GetNP(nfok);

	      while (!ok)
		{
		  locfr++;
		  if (locfr == actfnp + 1)
		    {
		      locfr = 1;
		      locfi++;
		      if (locfi > (sliced && nfok==2 ? queue->face_end : lfacesplit)) {
                              if(result && (!sliced || nfok!=2 || queue->face_end==lfacesplit)) {
                                if(!sliced || nfok==2 || fmapi.Get(2)>=queue->face_begin) ++result->face_trials;
                                else ++result->replay_face_trials;
                              } else if(result) ++result->replay_face_trials;
                              break;
                            }
		    }
		  
		  
                  if(result) {
                    if(!sliced || (nfok==2?locfi:fmapi.Get(2))>=queue->face_begin) ++result->face_trials;
                    else ++result->replay_face_trials;
                  }
		  if (fnearness.Get(locfi) > rule->GetFNearness (nfok) ||
		      fused.Get(locfi) ||
		      actfnp != lfaces[locfi-1].GetNP() )
		    {
		      // face not feasible in any rotation

		      locfr = actfnp;
		    }
		  else
		    {
		      
		      ok = 1;
		      
		      locface = &lfaces[locfi-1];

		      
		      // reference point already mapped differently ?
		      for (int j = 1; j <= actfnp && ok; j++)
			{
			  PointIndex locpi = pmap.Get(rule->GetPointNr (nfok, j));
			  if (locpi.IsValid() && locpi != locface->PNumMod(j+locfr))
			    ok = 0;
			}
		      
		      // local point already used or point outside tolerance ?
		      for (int j = 1; j <= actfnp && ok; j++)
			{
			  int refpi = rule->GetPointNr (nfok, j);
			  
			  if (!pmap.Get(refpi).IsValid())
			    {
			      PointIndex locpi = locface->PNumMod (j + locfr);

			      if (pused[locpi])
				ok = 0;
			      else
				{
				  const Point3d & lp = lpoints[locpi];
				  const Point3d & rp = rule->GetPoint(refpi);

				  if ( Dist2 (lp, rp) * rule->PointDistFactor(refpi) > minerr)
				    {
				      impossible = 0;
				      ok = 0;
				    }
				}
			    }
			}
		    }
		}
	      
	      
	      if (ok)
		{
		  // map face nfok

		  fmapi.Set (nfok, locfi);
		  fmapr.Set (nfok, locfr);
		  fused.Set (locfi, 1);
		  
		  for (int j = 1; j <= rule->GetNP (nfok); j++)
		    {
		      PointIndex locpi = locface->PNumMod(j+locfr);
		      
		      if (rule->GetPointNr (nfok, j) < IndexBASE<PointIndex>()+3 &&
			  pmap.Get(rule->GetPointNr(nfok, j)) != locpi)
			rule_out << "change face1 point, mark1" << endl;
		      
		      pmap.Set(rule->GetPointNr (nfok, j), locpi);
		      pused[locpi]++;
		    }
		  
		  nfok++;
		}
	      else
		{
		  // backtrack one face
		  fmapi.Set (nfok, 0);
		  fmapr.Set (nfok, rule->GetNP(nfok));
		  nfok--;
		  
		  fused.Set (fmapi.Get(nfok), 0);
		  for (int j = 1; j <= rule->GetNP (nfok); j++)
		    {
		      int refpi = rule->GetPointNr (nfok, j);
		      pused[pmap.Get(refpi)]--;
                      
		      if (pused[pmap.Get(refpi)] == 0)
			{
			  // pmap.Set(refpi, 0);
                          pmap.Elem(refpi).Invalidate();
			}
		    }
		}
	    }
	  
	  else
	    
	    { 
	      RuleLegacyTimer regfb(301, !queue);

	      // all faces are mapped
	      // now map all isolated points:
	      
	      if (loktestmode)
		{
		  rule_out << "Faces Ok" << endl;
		  problems[rim] = "Faces Ok";
		}

	      int npok = 1;
	      int incnpok = 1;
	      
	      pfixed.SetSize (pmap.Size());
              /*
	      for (int i = 1; i <= pmap.Size(); i++)
		pfixed.Set(i, (pmap.Get(i) != 0) );
              */
              for (int i : pmap.Range())
                pfixed[i] = pmap[i].IsValid();
                  
	      while (npok >= 1)
		{
		  
		  if (npok <= rule->GetNOldP())
		    {
		      
		      if (pfixed.Get(npok))
			
			{
			  if (incnpok)
			    npok++;
			  else
			    npok--;
			}
		      
		      else
			
			{
			  PointIndex locpi = pmap.Elem(npok);
			  ok = 0;
			  
			  if (locpi.IsValid())
			    pused[locpi]--;
			  
			  while (!ok && locpi < lpoints.Size()-1+IndexBASE<PointIndex>())
			    {
			      ok = 1;
			      if(result) {
                                if(!sliced || fmapi.Get(2)>=queue->face_begin) ++result->point_trials;
                                else ++result->replay_point_trials;
                              }
			      locpi++;
			      
			      if (pused[locpi] || 
				  pnearness[locpi] > rule->GetPNearness(npok))
				{
				  ok = 0;
				}
			      else if (allowpoint[locpi] != 2)
				{
				  ok = 0;
				  if (allowpoint[locpi] == 1)
				    impossible = 0;
				}
			      else
				{
				  const Point3d & lp = lpoints[locpi];
				  const Point3d & rp = rule->GetPoint(npok);

				  if ( Dist2 (lp, rp) * rule->PointDistFactor(npok) > minerr)
				    {
				      ok = 0;
				      impossible = 0;
				    }
				}
			    }
			  
			  
			  if (ok)
			    {
			      pmap.Set (npok, locpi);
			      
			      if (npok <= 3)
				rule_out << "set face1 point, mark3" << endl;
			      
			      pused[locpi]++;
			      npok++;
			      incnpok = 1;
			    }
			  
			  else
			    
			    {
			      // pmap.Set (npok, 0);
                              pmap.Elem(npok).Invalidate();
			      
			      if (npok <= 3)
				rule_out << "set face1 point, mark4" << endl;
			      
			      npok--;
			      incnpok = 0;
			    }
			}
		    }
		  
		  else
		    
		    {
		      RuleLegacyTimer regfa2(302, !queue);

                      if(sliced && fmapi.Get(2)<queue->face_begin) {
                        npok=rule->GetNOldP();incnpok=0;continue;
                      }
		      if (result) result->mappings++;
		      // all points are mapped
		      
		      if (loktestmode)
			{
			  rule_out << "Mapping found!!: Rule " << rule->Name() << endl;
			  for (auto pi : pmap)
			    rule_out << pi << " ";
			  rule_out << endl;
			  problems[rim] = "mapping found";
			  rule_out << rule->GetNP(1) << " = " << lfaces[0].GetNP() << endl;
			}
		      
		      ok = 1;
		      
		      
		      // check mapedges:
		      for (int i = 1; i <= rule->GetNEd(); i++)
			{
			  INDEX_2 in2(pmap.Get(rule->GetEdge(i).i1),
				      pmap.Get(rule->GetEdge(i).i2));
			  in2.Sort();
			  if (!ledges.Used (in2)) ok = 0;
			}


		      // check prism edges:
		      for (int i = 1; i <= rule->GetNE(); i++)
			{
			  const Element & el = rule->GetElement (i);
			  if (el.GetType() == PRISM) 
			    { 
			      for (int j = 1; j <= 3; j++)
				{
				  INDEX_2 in2(pmap.Get(el.PNum(j)),
					      pmap.Get(el.PNum(j+3)));      
				  in2.Sort();
				  if (!connectedpairs.Used (in2)) ok = 0;
				}
			    }
			  if (el.GetType() == PYRAMID) 
			    { 
			      if (loktestmode)
				rule_out << "map pyramid, rule = " << rule->Name() << endl;
			      for (int j = 1; j <= 2; j++)
				{
				  INDEX_2 in2;
				  if (j == 1)
				    {
				      in2.I1() = pmap.Get(el.PNum(2));
				      in2.I2() = pmap.Get(el.PNum(3));
				    }
				  else
				    {
				      in2.I1() = pmap.Get(el.PNum(1));
				      in2.I2() = pmap.Get(el.PNum(4));
				    }
				  in2.Sort();
				  if (!connectedpairs.Used (in2)) 
				    {
				      ok = 0;
				      if (loktestmode)
					rule_out << "no pair" << endl;
				    }
				}
			    }

			}
		      

		      
		      for (int i = rule->GetNOldF() + 1; i <= rule->GetNF(); i++)
			fmapi.Set(i, 0);
		      

		      if (ok)
                        foundmap[rim]++;
		      


		      // deviation of existing points

		      oldu.SetSize (3 * rule->GetNOldP());
		      newu.SetSize (3 * (rule->GetNP() - rule->GetNOldP()));
		      allp.SetSize (3 * rule->GetNP());
		      
		      for (int i = 1; i <= rule->GetNOldP(); i++)
			{
			  const Point3d & lp = lpoints[pmap.Get(i)];
			  const Point3d & rp = rule->GetPoint(i);
			  oldu (3*i-3) = lp.X()-rp.X();
                          oldu (3*i-2) = lp.Y()-rp.Y();
			  oldu (3*i-1) = lp.Z()-rp.Z();
			  
			  allp (3*i-3) = lp.X();
                          allp (3*i-2) = lp.Y();
                          allp (3*i-1) = lp.Z();
			}

		      if (rule->GetNP() > rule->GetNOldP())
			{
			  newu.SetSize (rule->GetOldUToNewU().Height());
			  rule->GetOldUToNewU().Mult (oldu, newu);
			}

		      //		      int idiff = 3 * (rule->GetNP()-rule->GetNOldP());
		      int idiff = 3 * rule->GetNOldP();
		      for (int i = rule->GetNOldP()+1; i <= rule->GetNP(); i++)
			{
			  const Point3d & rp = rule->GetPoint(i);
			  allp (3*i-3) = rp.X() + newu(3*i-3 - idiff);
                          allp (3*i-2) = rp.Y() + newu(3*i-2 - idiff);
                          allp (3*i-1) = rp.Z() + newu(3*i-1 - idiff);
			}
		      
		      rule->SetFreeZoneTransformation (allp, 
						       tolerance + int(sloppy));

		      if (!rule->ConvexFreeZone())
			{
			  ok = 0;
			  problems[rim] = "Freezone not convex";

			  if (loktestmode)
			    rule_out << "Freezone not convex" << endl;
			}

		      if (loktestmode)
			{
			  const NgArray<Point3d> & fz = rule->GetTransFreeZone();
			  rule_out << "Freezone: " << endl;
			  for (int i = 1; i <= fz.Size(); i++)
			    rule_out << fz.Get(i) << endl;
			}
		      

		      // check freezone:
		      
		      // for (int i = 1; i <= lpoints.Size(); i++)
                      for (auto i : lpoints.Range())
			{
			  if ( !pused[i] )
			    {
			      const Point3d & lp = lpoints[i];

			      if (rule->fzbox.IsIn (lp))
				{
				  if (rule->IsInFreeZone(lp))
				    {
				      if (loktestmode)
					{
					  rule_out << "Point " << i
						     << " in Freezone" << endl;
					  problems[rim] = "locpoint " + ToString(i) + " in Freezone";
					}
				      ok = 0;
				      break;
				    }
				}
			    }
			}

		      for (int i = 1; i <= lfaces.Size() && ok; i++)
			{
			  NgArrayMem<int, 10> lpi(4);

			  if (!fused.Get(i))
			    { 
			      int triin;
			      const MiniElement2d & lfacei = lfaces[i-1];

			      if (!triboxes.Elem(i).Intersect (rule->fzbox))
				triin = 0;
			      else
				{
				  int li, lj;
				  for (li = 1; li <= lfacei.GetNP(); li++)
				    {
				      int lpii = 0;
				      PointIndex pi = lfacei.PNum(li);
				      for (lj = 1; lj <= rule->GetNOldP(); lj++)
					if (pmap.Get(lj) == pi)
					  lpii = lj;
				      lpi.Elem(li) = lpii;
				    }


				  if (lfacei.GetNP() == 3)
				    {
				      triin = rule->IsTriangleInFreeZone 
					(
					 lpoints[lfacei.PNum(1)],
					 lpoints[lfacei.PNum(2)],
					 lpoints[lfacei.PNum(3)], lpi, 1
					 );
				    }
				  else
				    {
				      triin = rule->IsQuadInFreeZone 
					(
					 lpoints[lfacei.PNum(1)],
					 lpoints[lfacei.PNum(2)],
					 lpoints[lfacei.PNum(3)], 
					 lpoints[lfacei.PNum(4)], 
					 lpi, 1
					 );
				    }
				}


			      if (triin == -1)
				{
				  ok = 0;
				}
			      
			      if (triin == 1)
				{
#ifdef TEST_JS
				  ok = 0;

				  if (loktestmode)
				    {
				      rule_out << "El with " << lfaces[i-1].GetNP() << " points in freezone: "
						 << lfaces[i-1].PNum(1) << " - " 
						 << lfaces[i-1].PNum(2) << " - "
						 << lfaces[i-1].PNum(3) << " - "
						 << lfaces[i-1].PNum(4) << endl;
				      for (int lj = 1; lj <= lfaces[i-1].GetNP(); lj++)
					rule_out << lpoints[lfaces[i-1].PNum(lj)] << " ";

				      rule_out << endl;

				      sprintf (problems.Elem(ri), "triangle (%d, %d, %d) in Freezone",
					       lfaces[i-1].PNum(1), lfaces[i-1].PNum(2),
					       lfaces[i-1].PNum(3));
				    }
#else
				  if (loktestmode)
				    {
				      if (lfacei.GetNP() == 3)
					{
					  rule_out << "Triangle in freezone: "
						     << lfacei.PNum(1) << " - " 
						     << lfacei.PNum(2) << " - "
						     << lfacei.PNum(3) 
						     << ", or "
						     << lpoints[lfacei.PNum(1)] << " - " 
						     << lpoints[lfacei.PNum(2)] << " - "
						     << lpoints[lfacei.PNum(3)] 
						     << endl;
					  rule_out << "lpi = " << lpi.Get(1) << ", "
						     << lpi.Get(2) << ", " << lpi.Get(3) << endl;
					}
				      else
					  rule_out << "Quad in freezone: "
						     << lfacei.PNum(1) << " - " 
						     << lfacei.PNum(2) << " - "
						     << lfacei.PNum(3) << " - "
						     << lfacei.PNum(4) 
						     << ", or "
						     << lpoints[lfacei.PNum(1)] << " - " 
						     << lpoints[lfacei.PNum(2)] << " - "
						     << lpoints[lfacei.PNum(3)] << " - "
						     << lpoints[lfacei.PNum(4)] 
						     << endl;

				      problems[rim] = "triangle ("+ToString(lfaces[i-1].PNum(1))+", "
                                        + ToString(lfaces[i-1].PNum(2)) + ", "
                                        + ToString(lfaces[i-1].PNum(3)) + ") in Freezone";
				    }	

				  hc = 0;
				  for (int k = rule->GetNOldF() + 1; k <= rule->GetNF(); k++)
				    {
				      if (rule->GetPointNr(k, 1) < IndexBASE<PointIndex>()+rule->GetNOldP() &&
					  rule->GetPointNr(k, 2) < IndexBASE<PointIndex>()+rule->GetNOldP() &&
					  rule->GetPointNr(k, 3) < IndexBASE<PointIndex>()+rule->GetNOldP())
					{
					  for (int j = 1; j <= 3; j++)
					    if (lfaces[i-1].PNumMod(j  ) == pmap.Get(rule->GetPointNr(k, 1)) &&
						lfaces[i-1].PNumMod(j+1) == pmap.Get(rule->GetPointNr(k, 3)) &&
						lfaces[i-1].PNumMod(j+2) == pmap.Get(rule->GetPointNr(k, 2)))
					      {
						fmapi.Elem(k) = i;
						hc = 1;

						
 // 						rule_out << "found from other side: "
//  							   << rule->Name() 
//  							   << " ( " << pmap.Get (rule->GetPointNr(k, 1))
//  							   << " - " << pmap.Get (rule->GetPointNr(k, 2))
//  							   << " - " << pmap.Get (rule->GetPointNr(k, 3)) << " ) "
//  							   << endl;

						problems[rim] = "other";
					      }
					}
				    }
				  
				  if (!hc)
				    {
				      if (loktestmode)
					{
					  rule_out << "Triangle in freezone: "
						     << lfaces[i-1].PNum(1) << " - " 
						     << lfaces[i-1].PNum(2) << " - "
						     << lfaces[i-1].PNum(3) << endl;

                                          /*
					  snprintf (problems.Elem(ri), 255, "triangle (%d, %d, %d) in Freezone",
						   int (lfaces[i-1].PNum(1)), 
						   int (lfaces[i-1].PNum(2)),
						   int (lfaces[i-1].PNum(3)));
                                          */
                                          problems[rim] = "triangle ("
                                            + ToString(lfaces[i-1].PNum(1))+", "
                                            + ToString(lfaces[i-1].PNum(2)) + ", "
                                            + ToString(lfaces[i-1].PNum(3)) + ") in Freezone";
                                          
					}
				      ok = 0;
				    }
#endif
				}
			    }
			   
			}

		      
		      if (ok)
			{
			  err = 0;
			  for (int i = 1; i <= rule->GetNOldP(); i++)
			    {
			      double hf = rule->CalcPointDist (i, lpoints[pmap.Get(i)]);
			      if (hf > err) err = hf;
			    }
			  
			  
			  if (loktestmode)
			    {
			      rule_out << "Rule ok" << endl;
			      problems[rim] = "Rule ok, err = "+ToString(err);
			    }


			  //			  newu = rule->GetOldUToNewU() * oldu;

			  // set new points:
                          int oldnp = rule->GetNOldP();
			  int noldlp = lpoints.Size();
			  int noldlf = lfaces.Size();
			  
			  for (int i = oldnp + 1; i <= rule->GetNP(); i++)
			    {
			      np = rule->GetPoint(i);
			      np.X() += newu (3 * (i-oldnp) - 3);
			      np.Y() += newu (3 * (i-oldnp) - 2);
			      np.Z() += newu (3 * (i-oldnp) - 1);
			      lpoints.Append (np);
                              pmap.Elem(i) = lpoints.Size()-1+PointIndex::BASE;
			    }
			  
			  // Set new Faces:
			  
			  for (int i = rule->GetNOldF() + 1; i <= rule->GetNF(); i++)
			    if (!fmapi.Get(i))
			      {
				MiniElement2d nface(rule->GetNP(i));
				for (int j = 1; j <= nface.GetNP(); j++)
				  nface.PNum(j) = pmap.Get(rule->GetPointNr (i, j));
				
				lfaces.Append (nface);
			      }
			  
			  // Delete old Faces:

			  for (int i = 1; i <= rule->GetNDelF(); i++)
			    delfaces.Append (fmapi.Get(rule->GetDelFace(i)));
			  for (int i = rule->GetNOldF()+1; i <= rule->GetNF(); i++)
			    if (fmapi.Get(i))
			      {
				delfaces.Append (fmapi.Get(i));
				fmapi.Elem(i) = 0;
			      }
			  

			  // check orientation
			  for (int i = 1; i <= rule->GetNO() && ok; i++)
			    {
			      const fourint * fouri;
			      
			      fouri = &rule->GetOrientation(i);
			      Vec3d v1 (lpoints[pmap.Get(fouri->i1)], 
					lpoints[pmap.Get(fouri->i2)]);
			      Vec3d v2 (lpoints[pmap.Get(fouri->i1)], 
					lpoints[pmap.Get(fouri->i3)]);
			      Vec3d v3 (lpoints[pmap.Get(fouri->i1)], 
					lpoints[pmap.Get(fouri->i4)]);

			      Vec3d n;
			      Cross (v1, v2, n);
			      //if (n * v3 >= -1e-7*n.Length()*v3.Length()) // OR -1e-7???
			      if (n * v3 >= -1e-9)
				{
				  if (loktestmode)
				    {
				      problems[rim] = "Orientation wrong";
				      rule_out << "Orientation wrong ("<< n*v3 << ")" << endl;
				    }
				  ok = 0;
				}
			    }

			  

			  // new points in free-zone ?
			  for (int i = rule->GetNOldP() + 1; i <= rule->GetNP() && ok; i++)
			    if (!rule->IsInFreeZone (lpoints[pmap.Get(i)]))
			      {
				if (loktestmode)
				  {
				    rule_out << "Newpoint " << lpoints[pmap.Get(i)]
					       << " outside convex hull" << endl;
				    problems[rim] = "newpoint outside convex hull";
				  }
				ok = 0;
				
			      }
			  
			  // insert new elements
			  
			  for (int i = 1; i <= rule->GetNE(); i++)
			    {
			      elements.Append (rule->GetElement(i));
			      for (int j = 1; j <= elements.Get(i).NP(); j++)
				elements.Elem(i).PNum(j) = pmap.Get(elements.Get(i).PNum(j));
			    }
			  

			  // Calculate Element badness
			  
			  teterr = 0;
			  for (auto i : elements.Range())
			    {
			      double hf = CalcElementBadness (lpoints, elements[i]);
			      if (hf > teterr) teterr = hf;
			    }

			  /*
			    // keine gute Erfahrung am 25.1.2000, js
			  if (ok && teterr < 100 &&
			      (rule->TestFlag('b') || tolerance > 10) )
			    {
			      (*mycout) << "Reset teterr " 
				   << rule->Name() 
				   << " err = " << teterr 
				   << endl;
			      teterr = 1;
			    }
			  */

			  // compare edgelength
			  if (rule->TestFlag('l'))
			    {
			      double oldlen = 0;
			      double newlen = 0;

			      for (int i = 1; i <= rule->GetNDelF(); i++)
				{
				  const Element2d & face = 
				    rule->GetFace (rule->GetDelFace(i));
				  for (int j = 1; j <= 3; j++)
				    {
				      const Point3d & p1 =
					lpoints[pmap.Get(face.PNumMod(j))];
				      const Point3d & p2 =
					lpoints[pmap.Get(face.PNumMod(j+1))];
				      oldlen += Dist(p1, p2);
				    }
				}

			      for (int i = rule->GetNOldF()+1; i <= rule->GetNF(); i++)
				{
				  const Element2d & face = rule->GetFace (i);
				  for (int j = 1; j <= 3; j++)
				    {
				      const Point3d & p1 =
					lpoints[pmap.Get(face.PNumMod(j))];
				      const Point3d & p2 =
					lpoints[pmap.Get(face.PNumMod(j+1))];
				      newlen += Dist(p1, p2);
				    }
				}

			      if (oldlen < newlen) 
				{
				  ok = 0;
				  if (loktestmode)
				    problems[rim] = "oldlen < newlen";
				}
			    }
			  

			  if (loktestmode)
			    rule_out << "ok = " << int(ok)
				       << "teterr = " << teterr 
				       << "minteterr = " << minteterr << endl;


			  if (ok && teterr < tolerance)
			    {
			      canuse[rim] ++;
			      /*
			      rule_out << "can use rule " << rule->Name()
					 << ", err = " << teterr << endl;
			      for (i = 1; i <= pmap.Size(); i++)
				rule_out << pmap.Get(i) << " ";
			      rule_out << endl;
			      */

			      if (sliced && problems[rim].empty() && result)
                                result->prefix_min=std::min(result->prefix_min,double(teterr));
                              else if (problems[rim] == "other")
				{
				  if (teterr < local_minother)
				    local_minother = teterr;
				}
			      else
				{
				  if (teterr < local_minwithoutother)
				    local_minwithoutother = teterr;
				}
			    }


			  if (teterr > minteterr) impossible = 0;

			  if (ok && (teterr < minteterr ||
                             (queue && found && teterr==minteterr && rim+1<found)))
			    {

			      if (loktestmode)
				rule_out << "use rule" << endl;

			      found = rim+1;
			      minteterr = teterr;
			      
			      if (testmode)
				{
				  for (int i = 1; i <= rule->GetNOldP(); i++)
				    {
				      rule_out << "P" << i << ": Ref: "
						 << rule->GetPoint (i) << "  is: "
						 << lpoints[pmap.Get(i)] << endl;
				    }
				}
			      
			      tempnewpoints.SetSize (0);
			      // for (int i = noldlp+1; i <= lpoints.Size(); i++)
                              for (auto i : lpoints.Range().Modify(noldlp, 0))
				tempnewpoints.Append (lpoints[i]);
			      
			      tempnewfaces.SetSize (0);
			      // for (int i = noldlf+1; i <= lfaces.Size(); i++)
                              for (auto i : lfaces.Range().Modify(noldlf,0))
				tempnewfaces.Append (lfaces[i]);

			      tempdelfaces.SetSize (0);
			      // for (int i = 1; i <= delfaces.Size(); i++)
                              for (auto i : delfaces.Range())
				tempdelfaces.Append (delfaces[i]);
			      
			      tempelements.SetSize (0);
			      // for (int i = 1; i <= elements.Size(); i++)
                              for (auto i : elements.Range())
				tempelements.Append (elements[i]);
			    }
			  

			  lpoints.SetSize (noldlp);
			  lfaces.SetSize (noldlf);
			  delfaces.SetSize (0);
			  elements.SetSize (0);
			}
		      
		      npok = rule->GetNOldP();
		      incnpok = 0;
		    }
		}
	      
	      nfok = rule->GetNOldF();
	      
	      for (int j = 1; j <= rule->GetNP (nfok); j++)
		{
		  int refpi = rule->GetPointNr (nfok, j);
		  pused[pmap.Get(refpi)]--;
		  
		  if (pused[pmap.Get(refpi)] == 0)
                    pmap.Elem(refpi).Invalidate();
		}
	      
	    }
	}
      if (loktestmode)
	rule_out << "end rule" << endl;
    }
  // tloop.Stop();
  
  if (found)
    {
      /*
      for (i = 1; i <= tempnewpoints.Size(); i++)
	lpoints.Append (tempnewpoints.Get(i));
      */
      for (Point3d p : tempnewpoints)
        lpoints.Append(p);
      /*
      for (i = 1; i <= tempnewfaces.Size(); i++)
	if (tempnewfaces.Get(i).PNum(1))
	  lfaces.Append (tempnewfaces.Get(i));
      */
      for (int i : tempnewfaces.Range())
	if (tempnewfaces[i].PNum(1).IsValid())
	  lfaces.Append (tempnewfaces[i]);
      /*
      for (i = 1; i <= tempdelfaces.Size(); i++)
	delfaces.Append (tempdelfaces.Get(i));
      */
      for (int i : tempdelfaces.Range())
        delfaces.Append (tempdelfaces[i]);
      /*
      for (i = 1; i <= tempelements.Size(); i++)
	elements.Append (tempelements.Get(i));
      */
      for (int i : tempelements.Range())
        elements.Append (tempelements[i]);
    }
  
  retminerr = minerr;
  if (result) {
    result->quality = minteterr;
    result->other = local_minother;
    result->withoutother = local_minwithoutother;
    if(worker_trace) result->trace = worker_trace->str();
  }
  if (!queue) {
    minother = local_minother;
    minwithoutother = local_minwithoutother;
  }


  if (impossible && found == 0)
    return -1;

  return found;
}
}
