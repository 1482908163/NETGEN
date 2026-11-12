#include "smoothing_dependency.hpp"
#include <array>
#include <atomic>
#include <iostream>
#include <memory>
#include <thread>

using netgen::SmoothingDependencyQueue;

static void require(bool value,const char *message)
{
  if(!value) throw std::runtime_error(message);
}

static void ordered_updates(int workers,int compact=0)
{
  const int n=67;
  std::vector<int> colors(n);
  for(int i=0;i<n;++i) colors[i]=i%4;
  std::vector<std::pair<int,int>> edges;
  std::vector<std::vector<int>> reads(n),incident(n);
  std::vector<std::array<int,4>> elements;
  for(int i=0;i+3<n;++i) {
    std::array<int,4> tet{{i,i+1,i+2,i+3}};
    for(int a:tet) incident[a].push_back(int(elements.size()));
    elements.push_back(tet);
    for(int a:tet) for(int b:tet) if(a!=b) reads[a].push_back(b);
    std::sort(tet.begin(),tet.end(),[&](int a,int b){return colors[a]<colors[b];});
    for(int j=1;j<4;++j) edges.emplace_back(tet[j-1],tet[j]);
  }
  std::vector<double> reference(n),actual(n);
  for(int i=0;i<n;++i) reference[i]=actual[i]=i+1;
  auto update=[&](std::vector<double>&values,int i) {
    double x=values[i];for(int j:reads[i]) x+=0.01*values[j];values[i]=x;
  };
  for(int c=0;c<4;++c) for(int i=0;i<n;++i) if(colors[i]==c) update(reference,i);
  std::unique_ptr<SmoothingDependencyQueue> owner;
  if(compact) {
    auto graph=compact==2 ? netgen::BuildEdgeStreamSmoothingGraph(colors,edges)
                         : netgen::BuildUniqueSmoothingGraph(colors,[&](int source,auto emit) {
      for(int ei:incident[source]) {
        int next=-1;
        for(int id:elements[ei]) if(colors[id]>colors[source] && (next<0 || colors[id]<colors[next])) next=id;
        if(next>=0) emit(next);
      }
    });
    require(graph.raw_edges==edges.size(),"raw dependency coverage changed");
    require(graph.successors.size()<graph.raw_edges,"duplicate-edge fixture was not exercised");
    require(netgen::SameSmoothingDependencies(edges,graph),"compact dependency edge set changed");
    owner.reset(new SmoothingDependencyQueue(colors,std::move(graph.offsets),std::move(graph.successors)));
  } else owner.reset(new SmoothingDependencyQueue(colors,edges));
  auto &queue=*owner;
  std::vector<std::atomic<int>> seen(n);for(auto &v:seen) v.store(0);
  std::vector<std::thread> threads;
  for(int w=0;w<workers;++w) threads.emplace_back([&]{
    queue.RunWorker([&](int i){
      require(seen[i].fetch_add(1)==0,"duplicate task");update(actual,i);
    });
  });
  for(auto &t:threads)t.join();queue.Finish();
  require(reference==actual,"dependency order changed values");
  require(queue.Completed()==n,"incomplete point coverage");
  for(auto &v:seen)require(v.load()==1,"missing point");
}

static void failure_release(bool initialization)
{
  SmoothingDependencyQueue queue({0,1,2,3},{{0,1},{1,2},{2,3}});
  std::vector<std::thread> threads;
  for(int w=0;w<4;++w) threads.emplace_back([&,w]{
    if(initialization && w==0) queue.Fail(std::make_exception_ptr(std::runtime_error("initialization")));
    else queue.RunWorker([&](int task){if(!initialization && task==1) throw std::runtime_error("point");});
  });
  for(auto &t:threads)t.join();
  bool failed=false;try {queue.Finish();} catch(const std::runtime_error&){failed=true;}
  require(failed,"worker failure was swallowed");
}

int main()
{
  try {
    for(int workers:{1,2,4}) ordered_updates(workers);
    failure_release(false);failure_release(true);
    SmoothingDependencyQueue empty({},{});empty.RunWorker([](int){throw std::runtime_error("empty task");});empty.Finish();
    SmoothingDependencyQueue isolated({0,2,1},{});isolated.RunWorker([](int){});isolated.Finish();
    bool rejected=false;
    try {SmoothingDependencyQueue invalid({0,1},{{1,0}});} catch(const std::runtime_error&){rejected=true;}
    require(rejected,"backwards dependency accepted");
    rejected=false;
    try {SmoothingDependencyQueue invalid({0,0},{{0,1}});} catch(const std::runtime_error&){rejected=true;}
    require(rejected,"same-color dependency accepted");
    for(int workers:{1,2,4}) ordered_updates(workers,true);
    rejected=false;
    // Interior offset exceeds storage even though the final offset is correct.
    try {SmoothingDependencyQueue invalid({0,1},{0,2,1},{1});} catch(const std::runtime_error&){rejected=true;}
    require(rejected,"out-of-bounds CSR offset accepted");
    netgen::SmoothingDependencyGraph missing;
    missing.offsets={0,0,0};
    require(!netgen::SameSmoothingDependencies({{0,1}},missing),"missing dependency accepted");
    for(int workers:{1,2,4}) ordered_updates(workers,2);
    auto stream_empty=netgen::BuildEdgeStreamSmoothingGraph({},{});
    require(stream_empty.offsets==std::vector<std::size_t>{0} && stream_empty.successors.empty(),"empty stream graph changed");
    SmoothingDependencyQueue empty_stream({},std::move(stream_empty.offsets),std::move(stream_empty.successors));
    empty_stream.RunWorker([](int){throw std::runtime_error("empty stream task");});empty_stream.Finish();
    auto stream_isolated=netgen::BuildEdgeStreamSmoothingGraph({0,2,1},{});
    require(stream_isolated.offsets==std::vector<std::size_t>({0,0,0,0}),"isolated stream offsets changed");
    SmoothingDependencyQueue isolated_stream({0,2,1},std::move(stream_isolated.offsets),std::move(stream_isolated.successors));
    isolated_stream.RunWorker([](int){});isolated_stream.Finish();
    rejected=false;
    try {netgen::BuildEdgeStreamSmoothingGraph({0,1},{{-1,1}});} catch(const std::runtime_error&){rejected=true;}
    require(rejected,"invalid stream edge accepted");
    std::cout<<"RECOVERY_DAG_PROBE_PASS 20\n";
  } catch(const std::exception &error) {std::cerr<<error.what()<<'\n';return 1;}
}
