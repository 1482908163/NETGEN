#pragma once
// Exhaustive local tetrahedral audit, before ghost insertion. Warmups only.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

namespace mesh_audit {
using Point = std::array<long double,3>;
inline Point sub(const Point &a,const Point &b) { return {{a[0]-b[0],a[1]-b[1],a[2]-b[2]}}; }
inline Point cross(const Point &a,const Point &b) { return {{a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]}}; }
inline long double dot(const Point &a,const Point &b) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
inline std::uint64_t mix(std::uint64_t h,std::uint64_t value) {
    for(int i=0;i<8;++i) {h^=(value>>(8*i))&255;h*=1099511628211ULL;} return h;
}
struct Face {
    std::array<int,3> v;
    int orientation; // 0=declared surface; +/-1=outward tetrahedron face.
    bool operator<(const Face &other) const { return v<other.v; }
};
struct Result {
    std::uint64_t points=0,elements=0,surfaces=0,checked=0;
    std::uint64_t invalid_indices=0,unsupported=0,nonfinite_points=0;
    std::uint64_t repeated_vertices=0,zero_volume=0,positive=0,negative=0;
    std::uint64_t nonmanifold=0,same_side_faces=0,missing_surface=0,orphan_surface=0;
    std::uint64_t duplicate_surface=0,internal_marked=0,zero_surface_area=0;
    std::uint64_t surface_sum=0,surface_xor=0,volume_sum=0,volume_xor=0;
    std::array<std::uint64_t,10> shape_hist{};
    long double shape_min=1,shape_sum=0,angle_min=180,angle_max=0,abs_volume=0;
    std::uint64_t hard_errors() const {
        return invalid_indices+unsupported+nonfinite_points+repeated_vertices+zero_volume+
            nonmanifold+same_side_faces+missing_surface+orphan_surface+zero_surface_area+
            std::min(positive,negative);
    }
    template<class Profiler> void report(Profiler &p) const {
        const char *names[]={"points","elements","surfaces","checked","invalid_indices","unsupported",
            "nonfinite_points","repeated_vertices","zero_volume","positive","negative","nonmanifold",
            "same_side_faces","missing_surface","orphan_surface","duplicate_surface","internal_marked",
            "zero_surface_area","hard_errors"};
        const std::uint64_t values[]={points,elements,surfaces,checked,invalid_indices,unsupported,
            nonfinite_points,repeated_vertices,zero_volume,positive,negative,nonmanifold,same_side_faces,
            missing_surface,orphan_surface,duplicate_surface,internal_marked,zero_surface_area,hard_errors()};
        for(int i=0;i<19;++i) p.set_metric(std::string("quality_")+names[i],double(values[i]));
        p.set_metric("quality_shape_min",checked?double(shape_min):0);
        p.set_metric("quality_shape_sum",double(shape_sum));
        p.set_metric("quality_angle_min",checked?double(angle_min):0);
        p.set_metric("quality_angle_max",double(angle_max));
        p.set_metric("quality_abs_volume",double(abs_volume));
        for(int i=0;i<10;++i) p.set_metric("quality_shape_bin_"+std::to_string(i),double(shape_hist[i]));
        const char *hash_names[]={"surface_sum","surface_xor","volume_sum","volume_xor"};
        const std::uint64_t hashes[]={surface_sum,surface_xor,volume_sum,volume_xor};
        for(int i=0;i<4;++i) {
            p.set_metric(std::string("quality_")+hash_names[i]+"_hi",double(hashes[i]>>32));
            p.set_metric(std::string("quality_")+hash_names[i]+"_lo",double(hashes[i]&0xffffffffULL));
        }
    }
};

// MeshAccess supplies np/ne/nse, point(i), tetrahedron(i,ids), triangle(i,ids).
// Separating traversal from the public API makes geometric fixtures independent of MPI/OCC.
template<class MeshAccess> Result inspect(const MeshAccess &mesh) {
    Result r;r.points=mesh.np();r.elements=mesh.ne();r.surfaces=mesh.nse();
    std::vector<Point> points(r.points+1);
    std::vector<std::uint64_t> point_hash(r.points+1);
    std::vector<unsigned char> finite(r.points+1,1);
    for(std::uint64_t i=1;i<=r.points;++i) {
        const auto p=mesh.point(int(i));std::uint64_t h=14695981039346656037ULL;
        for(int k=0;k<3;++k) {
            double x=p[k];if(!std::isfinite(x)) finite[i]=0;
            if(x==0) x=0; // Canonicalize negative zero for geometry fingerprints.
            points[i][k]=x;std::uint64_t bits;std::memcpy(&bits,&x,sizeof(bits));h=mix(h,bits);
        }
        if(!finite[i]) ++r.nonfinite_points;
        point_hash[i]=h;
    }
    std::vector<Face> faces;
    faces.reserve(std::size_t(r.elements)*4+std::size_t(r.surfaces));
    auto valid=[&](const int *v,int n) {
        bool ok=true;for(int j=0;j<n;++j) if(v[j]<1 || std::uint64_t(v[j])>r.points) ok=false;
        if(!ok) ++r.invalid_indices;
        return ok;
    };
    auto hash_element=[&](const int *v,int n) {
        std::array<std::uint64_t,4> h{};for(int j=0;j<n;++j) h[j]=point_hash[v[j]];
        std::sort(h.begin(),h.begin()+n);std::uint64_t out=14695981039346656037ULL;
        for(int j=0;j<n;++j) out=mix(out,h[j]);
        return out;
    };
    const int fv[4][3]={{1,2,3},{0,3,2},{0,1,3},{0,2,1}};
    for(std::uint64_t i=1;i<=r.elements;++i) {
        int v[10]{};if(!mesh.tetrahedron(int(i),v)) {++r.unsupported;continue;}
        if(!valid(v,4)) continue;
        auto ids=std::array<int,4>{{v[0],v[1],v[2],v[3]}};std::sort(ids.begin(),ids.end());
        if(std::adjacent_find(ids.begin(),ids.end())!=ids.end()) {++r.repeated_vertices;continue;}
        bool ok=true;for(int j=0;j<4;++j) if(!finite[v[j]]) ok=false;if(!ok) continue;
        std::array<Point,4> x;for(int j=0;j<4;++j) x[j]=sub(points[v[j]],points[v[0]]);
        long double scale=0;for(int a=0;a<4;++a) for(int b=a+1;b<4;++b) scale=std::max(scale,std::sqrt(dot(sub(x[a],x[b]),sub(x[a],x[b]))));
        if(scale==0) {++r.zero_volume;continue;}
        for(auto &p:x) for(auto &z:p) z/=scale;
        const long double det=dot(x[1],cross(x[2],x[3]));
        if(det==0) ++r.zero_volume;else if(det>0) ++r.positive;else ++r.negative;
        for(int f=0;f<4;++f) {
            Face face{{{v[fv[f][0]],v[fv[f][1]],v[fv[f][2]]}},det<0?-1:1};
            for(int a=0;a<3;++a) for(int b=a+1;b<3;++b) if(face.v[a]>face.v[b]) face.orientation=-face.orientation;
            std::sort(face.v.begin(),face.v.end());faces.push_back(face);
        }
        if(det==0) continue;
        ++r.checked;const auto hash=hash_element(v,4);r.volume_sum+=hash;r.volume_xor^=hash;
        long double edge_sum=0;for(int a=0;a<4;++a) for(int b=a+1;b<4;++b) {auto e=sub(x[a],x[b]);edge_sum+=dot(e,e);}
        // Mean ratio: one for a regular tetrahedron, tends to zero for slivers.
        const long double q=std::min(1.L,12*std::cbrt(det*det/4)/edge_sum);
        r.shape_min=std::min(r.shape_min,q);r.shape_sum+=q;
        ++r.shape_hist[std::min(9,int(q*10))];r.abs_volume+=std::abs(det)*scale*scale*scale/6;
        std::array<Point,4> normals;
        for(int f=0;f<4;++f) {
            auto n=cross(sub(x[fv[f][1]],x[fv[f][0]]),sub(x[fv[f][2]],x[fv[f][0]]));
            const long double length=std::sqrt(dot(n,n));
            for(auto &z:n) z=(det<0?-z:z)/length;
            normals[f]=n;
        }
        for(int a=0;a<4;++a) for(int b=a+1;b<4;++b) {
            long double cosine=std::max(-1.L,std::min(1.L,-dot(normals[a],normals[b])));
            const long double angle=std::acos(cosine)*180/std::acos(-1.L);
            r.angle_min=std::min(r.angle_min,angle);r.angle_max=std::max(r.angle_max,angle);
        }
    }
    for(std::uint64_t i=1;i<=r.surfaces;++i) {
        int v[10]{};if(!mesh.triangle(int(i),v)) {++r.unsupported;continue;}
        if(!valid(v,3)) continue;
        if(!finite[v[0]]||!finite[v[1]]||!finite[v[2]]) continue;
        const auto n=cross(sub(points[v[1]],points[v[0]]),sub(points[v[2]],points[v[0]]));
        if(dot(n,n)==0) ++r.zero_surface_area;
        Face face{{{v[0],v[1],v[2]}},0};std::sort(face.v.begin(),face.v.end());faces.push_back(face);
        const auto hash=hash_element(v,3);r.surface_sum+=hash;r.surface_xor^=hash;
    }
    std::sort(faces.begin(),faces.end());
    for(std::size_t i=0;i<faces.size();) {
        std::size_t j=i;int volume=0,surface=0,orientation=0;
        while(j<faces.size()&&faces[j].v==faces[i].v) {
            if(faces[j].orientation) {++volume;orientation+=faces[j].orientation;}else ++surface;++j;
        }
        if(volume>2) ++r.nonmanifold;
        if(volume==2&&orientation) ++r.same_side_faces;
        if(volume==1&&!surface) ++r.missing_surface;
        if(!volume&&surface) ++r.orphan_surface;
        if(surface>1) r.duplicate_surface+=surface-1; // May be a two-sided material interface: compare with control.
        if(volume==2&&surface) ++r.internal_marked;
        i=j;
    }
    return r;
}
} // namespace mesh_audit
