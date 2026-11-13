#ifndef NETGEN_MPI_REFINE_EDGE_CACHE_H
#define NETGEN_MPI_REFINE_EDGE_CACHE_H
#include <cstddef>
#include <cstdint>
#include <vector>
namespace mesh_refine {
// Bounded per-call cache; the ordered map remains authoritative.
// Full positive endpoint IDs identify edges; zero is an empty key.
class EdgeCache {
public:
    struct Entry { std::uint64_t key = 0; int midpoint = 0; };
    static constexpr std::size_t capacity = 65536;
    explicit EdgeCache(bool enabled) : entries_(enabled ? capacity : 0) {}
    static std::uint64_t key(int a, int b) {
        return (std::uint64_t(std::uint32_t(a)) << 32) | std::uint32_t(b);
    }
    Entry &slot(std::uint64_t key) {
        key ^= key >> 30; key *= UINT64_C(0xbf58476d1ce4e5b9);
        key ^= key >> 27; key *= UINT64_C(0x94d049bb133111eb);
        key ^= key >> 31;
        return entries_[key & (capacity - 1)];
    }
    std::size_t size() const { return entries_.size(); }
    std::size_t bytes() const { return entries_.size() * sizeof(Entry); }
private:
    std::vector<Entry> entries_;
};
}
#endif
