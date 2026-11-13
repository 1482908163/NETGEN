#pragma once
#include "parallelMeshData.h"
#include "refine_edge_cache.h"
#include <unordered_map>
#include <stdexcept>

namespace mesh_refine {
// Complete persistent dictionary, not an eviction cache. Never iterate it to
// construct mesh entities: original traversal determines every output index.
class EdgeIndex {
    using Ordered = std::map<IntPair,int,IntPairCompare>;
    struct Hash {
        std::size_t operator()(std::uint64_t key) const noexcept {
            key ^= key >> 30; key *= UINT64_C(0xbf58476d1ce4e5b9);
            key ^= key >> 27; key *= UINT64_C(0x94d049bb133111eb);
            key ^= key >> 31;
            return static_cast<std::size_t>(key);
        }
    };
    std::unordered_map<std::uint64_t,int,Hash> entries_;
    Ordered *reference_ = nullptr;
    bool active_ = false;
public:
    std::uint64_t migrations=0, seed_entries=0, queries=0, hits=0, misses=0;
    std::uint64_t inserts=0, verified=0, mismatches=0, rehashes=0;
    bool activate(Ordered &old, bool verify) {
        if(active_) return false;
        entries_.max_load_factor(0.75f);
        entries_.reserve(old.size());
        for(const auto &item : old)
            entries_.emplace(EdgeCache::key(item.first.x,item.first.y),item.second);
        if(entries_.size()!=old.size()) fail("refine index seed mismatch");
        seed_entries=old.size();
        // During warmup the untouched ordered dictionary is an independent
        // oracle for every lookup, including misses, and receives every write.
        if(verify) reference_=&old;
        else old.clear();
        active_=true; ++migrations;
        return true;
    }
    bool find(const IntPair &edge, int &midpoint) {
        ++queries;
        const auto it=entries_.find(EdgeCache::key(edge.x,edge.y));
        const bool found=it!=entries_.end();
        if(found) { midpoint=it->second; ++hits; } else ++misses;
        if(reference_) {
            const auto ref=reference_->find(edge);
            if((ref!=reference_->end())!=found || (found && ref->second!=midpoint))
                fail("refine index lookup differs from ordered reference");
            ++verified;
        }
        return found;
    }
    void insert(const IntPair &edge, int midpoint) {
        const auto buckets=entries_.bucket_count();
        if(!entries_.emplace(EdgeCache::key(edge.x,edge.y),midpoint).second)
            fail("refine index duplicate insertion");
        ++inserts;
        if(entries_.bucket_count()!=buckets) ++rehashes;
        if(reference_ && !reference_->emplace(edge,midpoint).second)
            fail("refine reference duplicate insertion");
    }
    std::size_t size() const { return entries_.size(); }
    std::size_t buckets() const { return active_?entries_.bucket_count():0; }
private:
    [[noreturn]] void fail(const char *reason) { ++mismatches; throw std::runtime_error(reason); }
};
}
