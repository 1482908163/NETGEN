#include "../../netgen/libsrc/meshing/front_face_index.hpp"
#include <cassert>
#include <iostream>
#include <random>
using Pair=std::pair<int,int>;
using Faces=std::vector<std::vector<int>>;

Pair exhaustive(const Faces &faces,int point,int arity,int position,Pair cursor) {
  for(int f=1;f<=int(faces.size());++f)
    for(int r=1;r<=arity;++r)
      if(Pair(f,r)>cursor && int(faces[f-1].size())==arity &&
         faces[f-1][(position+r-1)%arity]==point)return {f,r};
  return {int(faces.size())+1,1};
}
// Compare ordered candidates after ALL existing topological filters. Geometry
// visits must also have identical sequences.
std::vector<Pair> walk(const Faces &faces,const netgen::FrontFaceIndex &index,
    const std::vector<int> &mapped,const std::vector<bool> &used,
    const std::vector<bool> &near,Pair cursor,bool indexed) {
  const int arity=int(mapped.size());int anchor=-1;
  for(int j=0;j<arity;++j)if(mapped[j]>=0){anchor=j;break;}
  std::vector<Pair> visits;
  while(true) {
    Pair next;
    if(indexed && anchor>=0)next=index.Next(mapped[anchor],arity,anchor+1,cursor.first,cursor.second);
    else {
      next=cursor;
      if(++next.second==arity+1){next.second=1;++next.first;}
    }
    if(next.first>int(faces.size()))break;
    cursor=next;
    const int f=next.first-1,r=next.second;
    if(used[f] || !near[f] || int(faces[f].size())!=arity) {cursor.second=arity;continue;}
    bool ok=true;
    for(int j=0;j<arity;++j)
      if(mapped[j]>=0 && mapped[j]!=faces[f][(j+r)%arity]){ok=false;break;}
    if(ok)visits.push_back(next);
  }
  return visits;
}
int main() {
  std::mt19937 rng(7102026);long long queries=0,walks=0;
  for(int base:{0,1})for(int sample=0;sample<600;++sample) {
    const int np=1+rng()%18,nf=rng()%24;
    Faces faces;netgen::FrontFaceIndex index(np+base,nf);
    for(int f=1;f<=nf;++f) {
      std::vector<int> face(3+rng()%2);
      for(int j=1;j<=int(face.size());++j) {
        face[j-1]=base+rng()%np; // Includes repeated vertices and absent points.
        index.Add(face[j-1],f,j,int(face.size()));
      }
      faces.push_back(face);
    }
    for(int arity:{3,4})for(int pos=1;pos<=arity;++pos)
      for(int p=base;p<np+base;++p) {
        for(int c=0;c<=nf;++c)for(int r=1;r<=arity;++r) {
          Pair cursor=c?Pair(c,r):Pair(0,arity);
          assert(index.Next(p,arity,pos,cursor.first,cursor.second)==exhaustive(faces,p,arity,pos,cursor));
          ++queries;
        }
        // Complete iteration must preserve duplicates and rotation order.
        Pair c{0,arity};std::vector<Pair> got,expected;
        while(true) {
          c=index.Next(p,arity,pos,c.first,c.second);
          if(c.first>nf)break;
          got.push_back(c);
        }
        for(int f=1;f<=nf;++f)for(int r=1;r<=arity;++r)
          if(int(faces[f-1].size())==arity && faces[f-1][(pos+r-1)%arity]==p)expected.emplace_back(f,r);
        assert(got==expected);
      }
    for(int round=0;round<100;++round) {
      int arity=3+rng()%2;std::vector<int> mapped(arity,-1);
      for(auto &p:mapped)if(rng()%3)p=base+rng()%np;
      std::vector<bool> used(nf),near(nf);
      for(int f=0;f<nf;++f){used[f]=rng()%3==0;near[f]=rng()%3!=0;}
      // Random cursor and changing anchors mimic backtracking/re-entry.
      Pair c=nf?Pair(1+rng()%nf,1+rng()%arity):Pair(0,arity);
      assert(walk(faces,index,mapped,used,near,c,false)==walk(faces,index,mapped,used,near,c,true));
      ++walks;
    }
  }
  std::cout<<"PASS: "<<queries<<" exact cursor comparisons; "<<walks<<" ordered multi-anchor/filter walks\n";
}
