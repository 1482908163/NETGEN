// Single-process lease state regression, not a distributed affinity/runtime test.
#include <sched.h>
#include <cassert>
#include <stdexcept>
static cpu_set_t test_mask;
static int test_setaffinity(pid_t,size_t,const cpu_set_t *mask){test_mask=*mask;return 0;}
static int test_getaffinity(pid_t,size_t,cpu_set_t *mask){*mask=test_mask;return 0;}
#define sched_setaffinity test_setaffinity
#define sched_getaffinity test_getaffinity
#define NETGEN_NODE_RESOURCES_TEST
#include "node_resources.h"
#undef sched_setaffinity
#undef sched_getaffinity
#ifdef NETGEN_TEST_MPI_STUB
int MPI_Abort(MPI_Comm,int){throw std::runtime_error("unexpected MPI abort");}
#endif
namespace mesh_node {
struct NodeResourcesTest {
 static void run() {
  NodeResources n;
  n.shared=new NodeResources::Shared{};auto &s=*n.shared;
  n.me=0;n.size=4;n.base=4;n.mode=16;s.size=4;s.cpus=16;
  s.origin=s.clock=NodeResources::now();
  assert(pthread_mutex_init(&s.mutex,nullptr)==0);
  assert(pthread_cond_init(&s.changed,nullptr)==0);
  for(int c=0;c<16;++c){s.cpu[c]=c;s.home[c]=s.owner[c]=c/4;}
  for(int r=0;r<4;++r){s.anchor[r]=4*r;s.rank[r].prepared=1;}
  auto done=[&](int rank){
   s.rank[rank].done=1;++s.completion_generation;
   for(int c=4*rank+1;c<4*rank+4;++c)s.owner[c]=-1;
  };
  assert(n.acquire(6,100,0)==4);
  assert(n.poll_work(100,10,.1,.001)==0); // no donors
  done(1);
  assert(n.poll_work(100,10,.1,.001)==1);
  n.release(6,100,4,.01);assert(n.acquire(6,100,0)==7);
  assert(n.poll_work(100,9,.1,.001)==0); // same completion generation
  done(2);
  assert(n.poll_work(100,8,.1,.001)==1);
  // Both previous and new borrowed cores are reserved before workers stop.
  for(int c:{5,6,7,9,10,11})assert(s.reserved[c]==1);
  for(int c:{0,4,8,12})assert(s.reserved[c]==0);
  n.release(6,100,7,.01);
  n.me=3;
  for(int c:{5,6,7,9,10,11})assert(!n.can_take(c));
  n.me=0;assert(n.acquire(6,100,0)==10);
  done(3);assert(n.poll_work(100,7,.1,.001)==0); // hard two-grant bound
  assert(n.work_grants==2 && n.work_repeat_grants==1 && n.work_reserved==6);
  assert(n.checkpoint_grants==2 && n.borrow_epochs==2);
  n.release(6,100,10,.01);n.finish();
  for(int c=0;c<16;++c)assert(s.reserved[c]==0);
  for(int c:{5,6,7,9,10,11})assert(s.owner[c]==-1);
  pthread_mutex_destroy(&s.mutex);pthread_cond_destroy(&s.changed);
  delete n.shared;n.shared=nullptr;
 }
};
}
int main(){mesh_node::NodeResourcesTest::run();}
