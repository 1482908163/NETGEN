#include "../../netgen/libsrc/meshing/deterministic_tet_refine.hpp"
#include <cassert>
#include <map>
#include <random>
#include <thread>
#include <iostream>
using namespace netgen_refine;
using Map=std::map<std::pair<int,int>,int>;
struct Result {std::vector<Tet> cells;Map edges;int points;};
// Reference follows the original application: parent-major edge insertion;
// child 0 overwrites its parent; children 1..7 append in parent order.
Result reference(std::vector<Tet> input,Map edges,int points) {
  Result r{input,edges,points};
  const int ep[6][2]={{0,1},{0,2},{0,3},{1,2},{1,3},{2,3}};
  const int tab[8][4]={{0,4,5,6},{4,1,7,8},{5,7,2,9},{6,8,9,3},
                     {4,5,6,8},{4,5,8,7},{5,6,8,9},{5,7,9,8}};
  for(std::size_t i=0;i<input.size();++i) {
    int p[10];for(int k=0;k<4;++k)p[k]=input[i][k];
    for(int k=0;k<6;++k) {
      auto key=std::minmax(p[ep[k][0]],p[ep[k][1]]);
      auto it=r.edges.find(key);
      if(it==r.edges.end())it=r.edges.emplace(key,++r.points).first;
      p[k+4]=it->second;
    }
    for(int j=0;j<8;++j) {
      Tet t{};for(int k=0;k<4;++k)t[k]=p[tab[j][k]];
      if(j==0)r.cells[i]=t;else r.cells.push_back(t);
    }
  }
  return r;
}
Result planned(std::vector<Tet> input,Map edges,int points,int workers) {
  std::vector<Edge> known;for(auto [key,id]:edges)known.push_back({key.first,key.second,id});
  auto execute=[&](std::size_t n,auto fn) {
    std::vector<std::thread> pool;
    for(int w=0;w<workers;++w)pool.emplace_back([&,w] {
      for(std::size_t i=std::size_t(w);i<n;i+=workers) {if(i%7==0)std::this_thread::yield();fn(i);}
    });
    for(auto &t:pool)t.join();
  };
  auto p=build(input,points,known,workers,execute);
  Result r{{},edges,points+int(p.added.size())};r.cells.resize(8*input.size());
  for(auto e:p.added)assert(r.edges.emplace(std::make_pair(e.a,e.b),e.mid).second);
  execute(input.size(),[&](std::size_t i){for(int j=0;j<8;++j)r.cells[child_slot(input.size(),i,j)]=child(input[i],p,i,j);});
  return r;
}
int main() {
  std::mt19937 gen(4197);
  for(int trial=0;trial<30;++trial) {
    int np=20;Map edges;std::vector<Tet> cells;
    for(int i=0;i<trial;++i) {
      Tet t{};for(int k=0;k<4;++k) {
        do {t[k]=1+int(gen()%20);}while(std::find(t.begin(),t.begin()+k,t[k])!=t.begin()+k);
      }
      cells.push_back(t);
    }
    // Surface midpoint numbers precede volume-only midpoint numbers, and their
    // numbering need not be sorted by endpoint pair.
    for(auto t:cells)for(int k=0;k<2;++k) {
      std::pair<int,int> key=std::minmax(t[k],t[k+1]);
      if(!edges.count(key))edges[key]=++np;
    }
    for(int round=0;round<2;++round) {
      auto expected=reference(cells,edges,np);
      for(int workers:{1,2,4}) {
        auto actual=planned(cells,edges,np,workers);
        assert(actual.points==expected.points && actual.edges==expected.edges && actual.cells==expected.cells);
      }
      cells=expected.cells;edges=expected.edges;np=expected.points;
    }
  }
  auto seq=[](std::size_t n,auto fn){for(std::size_t i=0;i<n;++i)fn(i);};
  auto reject=[&](const std::vector<Tet>&t,int np,const std::vector<Edge>&e) {
    bool thrown=false;try {build(t,np,e,1,seq);}catch(const std::exception&) {thrown=true;}assert(thrown);
  };
  reject({Tet{1,1,2,3}},4,{});reject({Tet{1,2,3,5}},4,{});
  reject({},4,{{1,2,3},{1,2,4}});reject({},4,{{2,3,1},{1,2,4}});
  reject({Tet{1,2,3,4}},std::numeric_limits<int>::max(),{});
  std::cout<<"PASS: 180 serial/parallel plan comparisons, two refinements, shared/preexisting edges, exact child and point order, invalid input and overflow rejection\n";
}
