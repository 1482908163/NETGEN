#ifndef NETGEN_SMOOTHING_DEPENDENCY_HPP
#define NETGEN_SMOOTHING_DEPENDENCY_HPP

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <exception>
#include <mutex>
#include <stdexcept>
#include <utility>
#include <vector>

namespace netgen
{
  using std::size_t;
  // Edges retain the old color order only where point updates share an element.
  // Repeated edges are intentional: every indegree increment has one release.
  // The queue mutex publishes point writes before a successor becomes runnable.
  class SmoothingDependencyQueue
  {
    std::vector<int> colors, successors, ready;
    std::vector<size_t> offsets, pending, color_remaining;
    size_t head=0, remaining=0, first_color=0;
    std::mutex mutex;
    std::condition_variable changed;
    std::exception_ptr failure;
    size_t completed=0, early=0;
    double wait_seconds=0;

  public:
    SmoothingDependencyQueue(std::vector<int> task_colors,
                             const std::vector<std::pair<int,int>> &edges)
      : colors(std::move(task_colors)), offsets(colors.size()+1,0),
        pending(colors.size(),0), remaining(colors.size())
    {
      int maxcolor=-1;
      for(int color:colors) {
        if(color<0) throw std::runtime_error("Invalid smoothing color");
        maxcolor=std::max(maxcolor,color);
      }
      color_remaining.resize(size_t(maxcolor+1),0);
      for(int color:colors) ++color_remaining[color];
      for(auto edge:edges) {
        int a=edge.first,b=edge.second;
        if(a<0 || b<0 || size_t(a)>=colors.size() || size_t(b)>=colors.size() || colors[a]>=colors[b])
          throw std::runtime_error("Non-increasing smoothing dependency");
        ++offsets[size_t(a)+1]; ++pending[b];
      }
      for(size_t i=1;i<offsets.size();++i) offsets[i]+=offsets[i-1];
      successors.resize(edges.size());
      auto cursor=offsets;
      for(auto edge:edges) successors[cursor[edge.first]++]=edge.second;
      ready.reserve(colors.size());
      for(size_t i=0;i<colors.size();++i) if(!pending[i]) ready.push_back(int(i));
      while(first_color<color_remaining.size() && !color_remaining[first_color]) ++first_color;
    }

    template<class Function> void RunWorker(Function process)
    {
      double waited=0;
      try {
        for(;;) {
          int task;
          {
            std::unique_lock<std::mutex> lock(mutex);
            if(!failure && remaining && head==ready.size()) {
              auto start=std::chrono::steady_clock::now();
              changed.wait(lock,[&]{return failure || !remaining || head<ready.size();});
              waited+=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
            }
            if(failure || !remaining) break;
            task=ready[head++];
            if(size_t(colors[task])>first_color) ++early;
          }
          process(task);
          {
            std::lock_guard<std::mutex> lock(mutex);
            ++completed; --remaining; --color_remaining[colors[task]];
            while(first_color<color_remaining.size() && !color_remaining[first_color]) ++first_color;
            for(size_t j=offsets[task];j<offsets[size_t(task)+1];++j) {
              int next=successors[j];
              if(--pending[next]==0) ready.push_back(next);
            }
          }
          changed.notify_all();
        }
      } catch(...) { Fail(std::current_exception()); }
      std::lock_guard<std::mutex> lock(mutex);
      wait_seconds+=waited;
    }

    // Also covers workspace construction before RunWorker, so waiting workers
    // cannot be stranded when another worker fails to initialize.
    void Fail(std::exception_ptr error)
    {
      { std::lock_guard<std::mutex> lock(mutex); if(!failure) failure=error; }
      changed.notify_all();
    }
    void Finish() const
    {
      if(failure) std::rethrow_exception(failure);
      if(remaining || completed!=colors.size()) throw std::runtime_error("Incomplete smoothing dependency queue");
    }
    size_t Completed() const { return completed; } // only after worker join
    size_t EarlyTasks() const { return early; }
    double WaitSeconds() const { return wait_seconds; }
  };
}
#endif
