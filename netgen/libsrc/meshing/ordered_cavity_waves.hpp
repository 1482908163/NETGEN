#pragma once
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

namespace netgen_cavity {
// Form only a consecutive prefix of the remaining ordered operations. The
// caller must join/commit this wave before asking for another. A rejected
// footprint is deliberately rebuilt against the newly committed mesh.
class OrderedWaves {
    std::vector<std::uint64_t> marks;
    std::uint64_t epoch=0;
public:
    explicit OrderedWaves(std::size_t resources):marks(resources,0) {}
    template<class Footprint>
    std::size_t Next(std::size_t first,std::size_t end,std::size_t capacity,
                     Footprint footprint,std::vector<int> & resources) {
        if(first>=end || !capacity)throw std::invalid_argument("empty cavity wave");
        if(epoch==std::numeric_limits<std::uint64_t>::max()) {
            std::fill(marks.begin(),marks.end(),0);epoch=0;
        }
        ++epoch;resources.clear();
        std::size_t next=first;
        while(next<end && next-first<capacity) {
            auto ids=footprint(next);
            bool conflict=false;
            for(auto id:ids) {
                if(id<0 || std::size_t(id)>=marks.size())
                    throw std::out_of_range("invalid cavity point");
                if(marks[id]==epoch)conflict=true;
            }
            if(conflict)break;
            for(auto id:ids)if(marks[id]!=epoch) {
                marks[id]=epoch;resources.push_back(id);
            }
            ++next;
        }
        if(next==first)throw std::logic_error("cavity wave made no progress");
        return next;
    }
};
}
