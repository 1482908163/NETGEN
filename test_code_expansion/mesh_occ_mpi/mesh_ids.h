#pragma once
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

// Final distributed mesh identifiers/counts. Netgen-local and coarse-mesh
// indices remain int and must never receive a final GlobalId by narrowing.
using GlobalId = std::int64_t;
using GlobalCount = std::int64_t;
static_assert(sizeof(GlobalId) == 8, "64-bit global mesh IDs required");

constexpr unsigned OWNER_LOCAL_ID_BITS = 40;
constexpr std::uint64_t OWNER_LOCAL_ID_MASK = (std::uint64_t{1} << OWNER_LOCAL_ID_BITS) - 1;

inline GlobalId make_owner_local_id(int owner, GlobalCount local)
{
    if (owner < 0 || local <= 0 ||
        static_cast<std::uint64_t>(local) > OWNER_LOCAL_ID_MASK)
        throw std::overflow_error("owner-local mesh ID exceeds encoding capacity");
    const std::uint64_t owner_code = static_cast<std::uint64_t>(owner) + 1;
    if (owner_code > (static_cast<std::uint64_t>(std::numeric_limits<GlobalId>::max()) >> OWNER_LOCAL_ID_BITS))
        throw std::overflow_error("owner rank exceeds owner-local ID capacity");
    return static_cast<GlobalId>((owner_code << OWNER_LOCAL_ID_BITS) |
                                 static_cast<std::uint64_t>(local));
}

inline int owner_local_id_owner(GlobalId id)
{
    if (id <= 0) throw std::runtime_error("invalid owner-local mesh ID");
    const std::uint64_t raw = static_cast<std::uint64_t>(id);
    const std::uint64_t code = raw >> OWNER_LOCAL_ID_BITS;
    if (code == 0 || code - 1 > static_cast<std::uint64_t>(std::numeric_limits<int>::max()))
        throw std::runtime_error("invalid owner-local mesh owner");
    return static_cast<int>(code - 1);
}

inline GlobalCount owner_local_id_local(GlobalId id)
{
    if (id <= 0) throw std::runtime_error("invalid owner-local mesh ID");
    const auto local = static_cast<GlobalCount>(static_cast<std::uint64_t>(id) & OWNER_LOCAL_ID_MASK);
    if (local <= 0) throw std::runtime_error("invalid owner-local mesh local ID");
    return local;
}

// Associative, commutative sum on nonnegative counts with -1 absorbing errors.
// A user-defined MPI reduction avoids signed overflow inside MPI_SUM itself.
inline GlobalCount checked_count_sum(GlobalCount a,GlobalCount b) {
    if(a<0 || b<0 || b>std::numeric_limits<GlobalCount>::max()-a) return -1;
    return a+b;
}

// Exclusive offsets; rank r owns (offset[r], offset[r+1]]. Empty ranks
// are valid. Checking before addition avoids signed overflow at INT64_MAX.
inline std::vector<GlobalId> make_id_offsets(const std::vector<GlobalCount> &counts)
{
    std::vector<GlobalId> offsets(counts.size() + 1, 0);
    for (std::size_t r = 0; r < counts.size(); ++r) {
        if (counts[r] < 0 || counts[r] > std::numeric_limits<GlobalId>::max() - offsets[r])
            throw std::overflow_error("global mesh count exceeds signed 64-bit capacity");
        offsets[r + 1] = offsets[r] + counts[r];
    }
    return offsets;
}

// Keep room for one-based indexing and the increment after the last element.
inline bool fits_local_mesh(GlobalCount count)
{
    return count >= 0 && count < std::numeric_limits<int>::max();
}
