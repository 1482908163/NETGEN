#ifndef NETGEN_ORDERED_MAPPING_HPP
#define NETGEN_ORDERED_MAPPING_HPP
#include <atomic>
#include <algorithm>
#include <array>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <exception>
#include <functional>
#include <memory>
#include <mutex>
#include <thread>
#include <string>
#include <vector>

namespace netgen {

// Only idle members of the existing rule team execute these jobs. No nested
// task manager, private OS threads, mesh mutation or resource callbacks here.
struct OrderedMappingJob {
  std::function<void(int)> run;
  std::function<void()> commit;
  std::exception_ptr error;
  std::atomic<bool> ready{false};
  size_t bytes=0;
};

class OrderedMappingPool {
  std::mutex mutex;
  std::condition_variable changed;
  std::deque<std::shared_ptr<OrderedMappingJob>> queue;
  size_t used=0;
  const size_t budget;
  const int slots;
  const int batch;
  int occupied=0;
public:
  std::atomic<int> producers, helpers{0}, started{0};
  std::vector<double> validation,waiting,largest;
  VolumeKernelStats *stats;
  explicit OrderedMappingPool(int workers,int window,int grain,size_t memory,VolumeKernelStats *s)
    :budget(memory),slots(window),batch(grain),producers(workers),validation(workers,0),waiting(workers,0),largest(workers,0),stats(s) {}
  bool Reserve(size_t bytes) {
    std::lock_guard<std::mutex> lock(mutex);
    if(bytes>budget || used>budget-bytes || occupied>=slots) {
      if(stats) stats->AddMapping(5,1);
      return false;
    }
    used+=bytes;++occupied;
    if(stats) stats->MaxMapping(6,double(used));
    return true;
  }
  void Release(size_t bytes) {
    std::lock_guard<std::mutex> lock(mutex);
    used-=bytes;--occupied;
  }
  void Publish(const std::shared_ptr<OrderedMappingJob> &job) {
    {std::lock_guard<std::mutex> lock(mutex);queue.push_back(job);}
    changed.notify_all();
  }
  bool Help(int worker) {
    const auto claim=std::chrono::steady_clock::now();
    std::array<std::shared_ptr<OrderedMappingJob>,4> jobs;
    int count=0;
    {std::lock_guard<std::mutex> lock(mutex);
      if(queue.empty()) return false;
      count=std::min(4,std::min(batch,int(queue.size())));
      for(int j=0;j<count;++j) {jobs[j]=queue.front();queue.pop_front();}}
    if(stats) stats->AddMapping(16,
      std::chrono::duration<double>(std::chrono::steady_clock::now()-claim).count());
    for(int j=0;j<count;++j) {
      auto &job=jobs[j];
      auto begin=std::chrono::steady_clock::now();
      try {job->run(worker);} catch(...) {job->error=std::current_exception();}
      double seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-begin).count();
      validation[worker]+=seconds;largest[worker]=std::max(largest[worker],seconds);
      job->run=nullptr;
      job->ready.store(true,std::memory_order_release);changed.notify_all();
    }
    return true;
  }
  void Wait(int worker) {
    auto begin=std::chrono::steady_clock::now();
    std::unique_lock<std::mutex> lock(mutex);
    changed.wait_for(lock,std::chrono::microseconds(100));
    waiting[worker]+=std::chrono::duration<double>(std::chrono::steady_clock::now()-begin).count();
  }
  void ProducerDone() {--producers;changed.notify_all();}
};

// One producer owns each lane. Jobs may finish in any order; only this lane's
// owner changes diagnostic/winner state. The destructor drains without commit
// on an exception, keeping captured input arrays alive until all readers exit.
class OrderedMappingLane {
  OrderedMappingPool &pool;
  int worker;
  std::deque<std::shared_ptr<OrderedMappingJob>> pending;
public:
  OrderedMappingLane(OrderedMappingPool &p,int w):pool(p),worker(w) {}
  ~OrderedMappingLane() {
    while(!pending.empty()) {
      auto job=pending.front();pending.pop_front();
      while(!job->ready.load(std::memory_order_acquire))
        if(!pool.Help(worker)) pool.Wait(worker);
      if(job->bytes) pool.Release(job->bytes);
    }
  }
  void Push(const std::shared_ptr<OrderedMappingJob> &job) {
    // Track before publishing: allocation failure cannot leave an unowned job.
    pending.push_back(job);
    try {pool.Publish(job);} catch(...) {pending.pop_back();throw;}
  }
  void Diagnostic(std::function<void()> action) {
    if(pending.empty()) {action();return;}
    // No candidate observes intermediate diagnostic assignments. Coalesce
    // consecutive markers so unsuccessful enumeration cannot grow the queue.
    if(pending.back()->bytes==0) {pending.back()->commit=std::move(action);return;}
    auto job=std::make_shared<OrderedMappingJob>();job->commit=std::move(action);
    job->ready.store(true,std::memory_order_release);pending.push_back(job);
  }
  void Drain(bool all=true) {
    do {
      if(pending.empty()) return;
      auto job=pending.front();
      auto begin=std::chrono::steady_clock::now();
      while(!job->ready.load(std::memory_order_acquire))
        if(!pool.Help(worker)) pool.Wait(worker);
      if(pool.stats) pool.stats->AddMapping(4,
        std::chrono::duration<double>(std::chrono::steady_clock::now()-begin).count());
      pending.pop_front();
      // Release accounting even if ordered commit allocates and throws.
      struct Release {OrderedMappingPool &p;size_t n;~Release(){if(n)p.Release(n);}} release{pool,job->bytes};
      if(job->error) std::rethrow_exception(job->error);
      job->commit();
    } while(all);
  }
  size_t Size() const {return pending.size();}
};

struct MappingProblem {
  bool changed=false;
  std::string value;
  MappingProblem &operator=(const std::string &s) {value=s;changed=true;return *this;}
};
struct MappingCandidate {
  NgArray<int,PointIndex::BASE> used;
  NgArray<char> fused;
  NgArray<PointIndex> pmap;
  NgArray<int> fmapi;
  std::vector<double> newu;
  vnetrule::FreeZoneState zone;
  Array<Point3d,PointIndex> points;
  Array<MiniElement2d> faces;
  NgArray<Element> elements;
  NgArray<INDEX> deleted;
  MappingProblem problem;
  bool evaluated=false,ok=false;
  float quality=0;
};
}
#endif
