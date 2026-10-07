#include "ghost_exchange_plan.h"
#include <cassert>
#include <cstdint>
#include <iostream>
#include <random>
#include <algorithm>
struct Payload {std::int64_t Pindex[4],gid;struct {double xyz[3];}Vertexs[4];int domidx;};
struct Mesh {
 std::vector<std::array<double,3>> points;
 std::vector<std::array<int,4>> elements;
 int np()const{return static_cast<int>(points.size());}
 int ne()const{return static_cast<int>(elements.size());}
 void tetrahedron(int e,int *v,int &domain)const{std::copy(elements[e-1].begin(),elements[e-1].end(),v);domain=e%7;}
 void point(int v,double *p)const{std::copy(points[v-1].begin(),points[v-1].end(),p);}
};
using Plan=mesh_research::GhostExchangePlan<Payload>;
struct Request {void *receive;const void *send;int count,type,peer,tag;std::vector<char> snapshot;};
static std::map<int,Request> pending;
static int sequence=1,vertex_waits=0,count_waits=0;
static bool built_before_wait=false,expect_overlap=false;
static std::vector<std::pair<bool,int>> events;
int MPI_Comm_rank(MPI_Comm,int*r){*r=0;return 0;}
int MPI_Error_string(int,char*,int*n){*n=0;return 0;}
int MPI_Abort(MPI_Comm,int){std::abort();}
static int bytes(int t){return t==MPI_INT?sizeof(int):sizeof(std::int64_t);}
int MPI_Isend(const void *p,int count,MPI_Datatype type,int peer,int tag,MPI_Comm,MPI_Request*r){
 *r=sequence++;const char *c=static_cast<const char*>(p);
 pending[*r]={nullptr,p,count,type,peer,tag,std::vector<char>(c,c+count*bytes(type))};events.push_back({true,tag});return 0;
}
int MPI_Irecv(void *p,int count,MPI_Datatype type,int peer,int tag,MPI_Comm,MPI_Request*r){
 *r=sequence++;pending[*r]={p,nullptr,count,type,peer,tag,{}};events.push_back({false,tag});return 0;
}
int MPI_Waitall(int n,MPI_Request*r,MPI_Status*){
 if(n && pending.at(r[0]).tag==4821)++count_waits;
 else {assert(!expect_overlap || built_before_wait);++vertex_waits;}
 for(int i=0;i<n;++i){auto q=pending.at(r[i]);
  if(q.send)assert(std::memcmp(q.send,q.snapshot.data(),q.snapshot.size())==0);
  else if(q.tag==4821)*static_cast<int*>(q.receive)=q.peer%3;
  else for(int k=0;k<q.count;++k)static_cast<std::int64_t*>(q.receive)[k]=(1LL<<45)+q.peer*100+k;
  pending.erase(r[i]);r[i]=MPI_REQUEST_NULL;
 }return 0;
}
template<class F>void reject(F f){bool failed=false;try{f();}catch(const std::exception&){failed=true;}assert(failed);}
int main(){
 static_assert(sizeof(Plan::Reference)==20,"reference metric contract");
 std::mt19937 rng(43117);
 for(int trial=0;trial<5000;++trial){
  Mesh mesh;int np=4+rng()%12,ne=rng()%24,ranks=2+rng()%7;
  for(int v=0;v<np;++v)mesh.points.push_back({double(v),v%2?-0.0:0.0,double(v)/3});
  for(int e=0;e<ne;++e){std::array<int,4>v;for(int &k:v)k=1+rng()%np;mesh.elements.push_back(v);}
  std::map<int,std::vector<int>> adj;
  for(int v=1;v<=np;++v)for(int peer=1;peer<ranks;++peer)if(rng()%3==0){adj[v].push_back(peer);if(rng()%10==0)adj[v].push_back(peer);}
  Plan p;p.Build(mesh,adj,0,ranks);
  std::vector<std::int64_t> vids(np+1,-1),eids(ne+1,-1);
  for(int v=1;v<=np;++v)vids[v]=(1LL<<40)+v;
  for(int e=1;e<=ne;++e)eids[e]=(1LL<<41)+e;
  // Simulate canonical output compaction AFTER geometry packing.
  if(trial%2==0){for(int v=1;v<=np;++v)vids[v]=v+17;for(int e=1;e<=ne;++e)eids[e]=e+31;}
  p.Bind(mesh,vids,eids);assert(p.Verify(mesh,adj,vids,eids)==p.send_elements);
  assert(p.ReferenceBytes()==20*p.send_elements);
  reject([&]{p.Bind(mesh,vids,eids);});reject([&]{p.Build(mesh,adj,0,ranks);});
  if(!p.packets.empty()){
   auto &packet=p.packets.begin()->second[0];packet.Pindex[0]++;
   reject([&]{p.Verify(mesh,adj,vids,eids);});packet.Pindex[0]--;
   packet.Vertexs[0].xyz[1]=std::signbit(packet.Vertexs[0].xyz[1])?0.0:-0.0;
   reject([&]{p.Verify(mesh,adj,vids,eids);});
  }
 }
 Mesh mesh{{{0,0,0},{1,0,0},{0,1,0},{0,0,1}},{{1,2,3,4}}};
 std::map<int,std::vector<int>> adj{{1,{1}},{2,{1}},{3,{1}}};
 std::int64_t ids[]={0,1,2,3,4},eids[]={0,8};
 {Plan p;p.Build(mesh,adj,0,2);ids[1]=-1;reject([&]{p.Bind(mesh,ids,eids);});ids[1]=1;}
 {Plan p;p.Build(mesh,adj,0,2);Mesh changed=mesh;changed.points.push_back({0,0,0});reject([&]{p.Bind(changed,ids,eids);});}
 for(bool overlap:{false,true})for(int neighbors=0;neighbors<8;++neighbors){
  pending.clear();events.clear();vertex_waits=0;count_waits=0;expect_overlap=overlap;built_before_wait=false;
  std::vector<int> peers,lengths(neighbors,3);std::vector<std::vector<std::int64_t>> s(neighbors),r(neighbors);
  std::vector<std::int64_t*>sp,rp;std::set<int> graph;std::map<int,int>counts;
  for(int peer=1;peer<=neighbors;++peer){peers.push_back(peer);s[peer-1].assign(3,(1LL<<44)+peer);r[peer-1].resize(3);sp.push_back(s[peer-1].data());rp.push_back(r[peer-1].data());graph.insert(peer);counts[peer]=(peer+1)%3;}
  auto requests=mesh_research::PostKnownVertices(1,MPI_INT64_T,0,neighbors,neighbors,peers.data(),peers.data(),lengths.data(),lengths.data(),sp.data(),rp.data());
  for(int i=0;i<neighbors;++i)assert(!events[i].first);
  mesh_research::GhostCountExchange exchange;
  if(overlap){built_before_wait=true;exchange.Begin(1,0,neighbors+1,graph,counts);assert(vertex_waits==0);}
  mesh_research::WaitKnownVertices(1,requests);
  if(!overlap){built_before_wait=true;exchange.Begin(1,0,neighbors+1,graph,counts);}
  exchange.Wait();assert(pending.empty());assert(count_waits==int(neighbors>0));
  for(int peer:graph){assert(exchange.receive[peer]==peer%3);for(int k=0;k<3;++k)assert(r[peer-1][k]==(1LL<<45)+peer*100+k);}
  reject([&]{exchange.Wait();});reject([&]{exchange.Begin(1,0,neighbors+1,graph,counts);});
 }
 {
  std::int64_t sent=(1LL<<40),received=0;std::int64_t *sp=&sent,*rp=&received;
  int peer=4821,count=1;expect_overlap=false;
  auto requests=mesh_research::PostKnownVertices(1,MPI_INT64_T,4821,1,1,&peer,&peer,&count,&count,&sp,&rp);
  for(auto request:requests)assert(pending.at(request).tag==3818);
  mesh_research::WaitKnownVertices(1,requests);assert(pending.empty());
 }
 {mesh_research::GhostCountExchange c;reject([&]{c.Begin(1,0,2,{1},{{1,-1}});});reject([&]{c.Begin(1,0,2,{},{{1,0}});});}
 std::cout<<"PASS: 5000 production-plan exact packet tests, late 64-bit ID binding, compaction, coordinate bits, empty/asymmetric peers and request buffer lifecycle (scripted MPI)\n";
}
