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
  struct SmoothingDependencyGraph
  {
    std::vector<size_t> offsets;
    std::vector<int> successors;
    size_t raw_edges=0;
  };

  // Enumerate the old chain's immediate successor in each incident tet. A
  // point-sized stamp array removes repeated source/target pairs without sorting
  // all tet edges or storing an intermediate pair list.
  template<class Enumerate>
  SmoothingDependencyGraph BuildUniqueSmoothingGraph(const std::vector<int> &colors,
                                                      Enumerate enumerate)
  {
    SmoothingDependencyGraph graph;
    graph.offsets.reserve(colors.size()+1); graph.offsets.push_back(0);
    std::vector<int> seen(colors.size(),-1);
    for(size_t source=0;source<colors.size();++source) {
      if(colors[source]<0) throw std::runtime_error("Invalid smoothing color");
      enumerate(int(source),[&](int next) {
        if(next<0 || size_t(next)>=colors.size() || colors[source]>=colors[next])
          throw std::runtime_error("Non-increasing smoothing dependency");
        ++graph.raw_edges;
        if(seen[next]!=int(source)) {
          seen[next]=int(source); graph.successors.push_back(next);
        }
      });
      graph.offsets.push_back(graph.successors.size());
    }
    return graph;
  }

  // Warmup only: compare exact edge sets against the independent per-tet builder.
  inline bool SameSmoothingDependencies(std::vector<std::pair<int,int>> reference,
                                        const SmoothingDependencyGraph &graph)
  {
    if(graph.offsets.empty() || graph.offsets.front()!=0 || graph.offsets.back()!=graph.successors.size()) return false;
    for(size_t offset:graph.offsets) if(offset>graph.successors.size()) return false;
    std::vector<std::pair<int,int>> actual;
    actual.reserve(graph.successors.size());
    for(size_t source=0;source+1<graph.offsets.size();++source) {
      if(graph.offsets[source]>graph.offsets[source+1]) return false;
      for(size_t j=graph.offsets[source];j<graph.offsets[source+1];++j)
        actual.emplace_back(int(source),graph.successors[j]);
    }
    std::sort(reference.begin(),reference.end());
    reference.erase(std::unique(reference.begin(),reference.end()),reference.end());
    std::sort(actual.begin(),actual.end());
    return reference==actual; // also rejects duplicates in the compact graph
  }

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
    SmoothingDependencyQueue(std::vector<int> task_colors, std::vector<size_t> row_offsets,
                             std::vector<int> next_tasks)
      : colors(std::move(task_colors)), successors(std::move(next_tasks)),
        offsets(std::move(row_offsets)), pending(colors.size(),0), remaining(colors.size())
    {
      if(offsets.size()!=colors.size()+1 || offsets.front()!=0 || offsets.back()!=successors.size())
        throw std::runtime_error("Invalid smoothing CSR offsets");
      for(size_t offset:offsets) if(offset>successors.size())
        throw std::runtime_error("Invalid smoothing CSR bounds");
      int maxcolor=-1;
      for(int color:colors) {
        if(color<0) throw std::runtime_error("Invalid smoothing color");
        maxcolor=std::max(maxcolor,color);
      }
      color_remaining.resize(size_t(maxcolor+1),0);
      for(int color:colors) ++color_remaining[color];
      for(size_t source=0;source<colors.size();++source) {
        if(offsets[source]>offsets[source+1]) throw std::runtime_error("Invalid smoothing CSR order");
        for(size_t j=offsets[source];j<offsets[source+1];++j) {
          int next=successors[j];
          if(next<0 || size_t(next)>=colors.size() || colors[source]>=colors[next])
            throw std::runtime_error("Non-increasing smoothing dependency");
          ++pending[next];
        }
      }
      ready.reserve(colors.size());
      for(size_t i=0;i<colors.size();++i) if(!pending[i]) ready.push_back(int(i));
      while(first_color<color_remaining.size() && !color_remaining[first_color]) ++first_color;
    }

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
