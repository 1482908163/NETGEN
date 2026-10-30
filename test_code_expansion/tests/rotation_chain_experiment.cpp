// Isolated research executable: replaces ruler3/meshing3 objects in the test link.
// TestRules is an existing Meshing3 friend; the production code is unchanged.
#include "../../netgen/libsrc/meshing/ruler3.cpp"
#include <random>
#include <iomanip>
#include <sstream>
using namespace netgen;
namespace {
int threads=4, fixture_variant=0, limit=7, repeats=1, route=0;
bool verify=true;
int front_fixture=-1;
unsigned long long front_batches=0;
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
  if(route!=1 || mode!=1 || testmode || !ngcore::task_manager ||
     ngcore::TaskManager::GetNumThreads()<2 || testout->rdbuf()!=nullptr ||
     testout->tie()!=nullptr || testout->exceptions()!=std::ios::goodbit)return nullptr;
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
    auto output=std::make_unique<Frame>();output->rotation=rotation;output->points=f->points;
    output->faces=f->faces;output->allow=f->allow;state->output.push_back(std::move(output));
    b.frames.push_back(std::move(f));
  }
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
      int workers=std::min(threads,int(mesher.rules.Size()));
      struct Slot {std::vector<std::unique_ptr<RuleSearchResult>> result;};
      std::vector<Slot> slots(workers);
      std::vector<std::vector<std::string>> diagnostic;
      if(verify)diagnostic.resize(rotations,std::vector<std::string>(mesher.rules.Size()));
      for(auto &slot:slots)for(auto &f:b.frames) {
        auto r=std::make_unique<RuleSearchResult>();r->points=f->points;r->faces=f->faces;
        slot.result.push_back(std::move(r));
      }
      if(b.tolerance>5)RuleDiagnosticCounter()+=rotations;
      ngcore::ParallelFor(size_t(workers),[&](size_t worker) {
        RuleSearchQueue queue(0);queue.discard_trace=true;
        // A rule stays on one worker for all rotations: its mutable geometric
        // history is consumed in original order, without a barrier per rotation.
        for(int id=worker;id<mesher.rules.Size();id+=workers)queue.order.push_back(id);
        for(int rotation=0;rotation<rotations;++rotation) {
          queue.next.store(0,std::memory_order_relaxed);
          auto &r=*slots[worker].result[rotation];auto &input=*b.frames[rotation];
          r.found=mesher.ApplyRulesImpl(r.points,input.allow,r.faces,b.split,b.pairs,r.elements,r.deleted,
            b.tolerance,b.sloppy,input.rotation,r.error,&queue,&r);
          if(verify)for(int id:queue.order) {
            auto &s=diagnostic[rotation][id];auto append=[&](auto v){s.append(reinterpret_cast<char*>(&v),sizeof(v));};
            append(mesher.foundmap[id]);append(mesher.canuse[id]);
            auto &problem=mesher.problems[id];append(int(problem.size()));s+=problem;
          }
        }
      },workers);
      ++dispatches;
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
      <<",\"fingerprint\":\""<<std::hex<<fingerprint<<std::dec<<"\"}\n";
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
    <<",\"dispatches\":"<<dispatches<<",\"fingerprint\":\""<<std::hex<<fingerprint<<std::dec<<"\"}\n";
}
}
int main(int argc,char **argv) {
  if(argc>1)threads=std::atoi(argv[1]);if(argc>2)route=std::atoi(argv[2]);
  if(argc>3)fixture_variant=std::atoi(argv[3]);if(argc>4)limit=std::atoi(argv[4]);
  if(argc>5)verify=std::atoi(argv[5]);if(argc>6)repeats=std::atoi(argv[6]);
  if(argc>7)front_fixture=std::atoi(argv[7]);
  if(threads<1 || threads>16 || route < -1 || route>1 || fixture_variant<0 || fixture_variant>2 || limit<1 || limit>45 || repeats<1 || front_fixture>5)
    return 2;
  TestRules();
}
