#!/usr/bin/env python3
"""Execute actual legacy transformation and snapshot methods, including rows
retained by degenerate normals. Does not execute real intersection routines.
"""
from pathlib import Path
import subprocess,tempfile
ROOT=Path(__file__).resolve().parents[2]
s=(ROOT/'netgen/libsrc/meshing/netrule3.cpp').read_text()
a=s.index('void vnetrule :: SetFreeZoneTransformation');b=s.index('\nint vnetrule :: ConvexFreeZone',a);transform=s[a:b]
h=(ROOT/'netgen/libsrc/meshing/ruler3.hpp').read_text()
a=h.index('  using FreeZoneReplayState');b=h.index('  void SetFreeZoneTransformation',a);snapshot=h[a:b]
prefix=r'''
#include <array>
#include <memory>
#include <chrono>
#include "front_transform_plan.hpp"
using netgen::FrontTransformPlan;
struct VolumeKernelStats {double values[12]={};void Add(int i,double value){values[i]+=value;}};
#include <vector>
#include <cstring>
#include <algorithm>
#include <cmath>
#include <cassert>
#include <iostream>
struct Point3d {double v[3]={};Point3d()=default;Point3d(double x,double y,double z):v{x,y,z}{}
 double X()const{return v[0];}double Y()const{return v[1];}double Z()const{return v[2];}double& X(int k){return v[k-1];}};
struct Vec3d {double v[3]={};Vec3d()=default;Vec3d(const Point3d&a,const Point3d&b):v{b.X()-a.X(),b.Y()-a.Y(),b.Z()-a.Z()}{}
 double X()const{return v[0];}double Y()const{return v[1];}double Z()const{return v[2];}double Length()const{return std::sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2]);}};
void Cross(const Vec3d&a,const Vec3d&b,Vec3d&n){n.v[0]=a.v[1]*b.v[2]-a.v[2]*b.v[1];n.v[1]=a.v[2]*b.v[0]-a.v[0]*b.v[2];n.v[2]=a.v[0]*b.v[1]-a.v[1]*b.v[0];}
template<class T>struct NgArray:std::vector<T> {using std::vector<T>::vector;int Size()const{return int(this->size());}void SetSize(int n){this->resize(n);}T& Elem(int i){return this->at(i-1);}const T& Get(int i)const{return this->at(i-1);}};
struct Vector:std::vector<double> {using std::vector<double>::vector;double& operator()(int i){return this->at(i);}double operator()(int i)const{return this->at(i);}void operator*=(double x){for(auto&v:*this)v*=x;}void Add(double x,const Vector&o){for(size_t i=0;i<size();++i)(*this)[i]+=x*o[i];}};
struct DenseMatrix {int h,w;std::vector<double> v;DenseMatrix(int a,int b):h(a),w(b),v(a*b){}
 int Height()const{return h;}int Width()const{return w;}const double& Get(int i,int j)const{return v.at((i-1)*w+j-1);}void Set(int i,int j,double x){v.at((i-1)*w+j-1)=x;}double& operator()(int i,int j){return v.at(i*w+j);}void Mult(const Vector&x,Vector&y)const{for(int i=0;i<h;++i){double sum=0;for(int j=0;j<w;++j)sum+=v[i*w+j]*x[j];y[i]=sum;}}};
struct Box3d {Point3d lo,hi;Box3d()=default;Box3d(double a,double b,double c,double d,double e,double f):lo(a,c,e),hi(b,d,f){}double Mini(int i)const{return lo.v[i-1];}double Maxi(int i)const{return hi.v[i-1];}void SetPoint(Point3d p){lo=hi=p;}void AddPoint(Point3d p){for(int k=0;k<3;++k){lo.v[k]=std::min(lo.v[k],p.v[k]);hi.v[k]=std::max(hi.v[k],p.v[k]);}}void IncreaseRel(double r){for(int k=0;k<3;++k){double d=(hi.v[k]-lo.v[k])*r;lo.v[k]-=d;hi.v[k]+=d;}}};
struct threeint{int i1,i2,i3;};
struct vnetrule {
 NgArray<Point3d> points,freezone,transfreezone;NgArray<int> freesets;
 NgArray<NgArray<threeint>*> freefaces;NgArray<DenseMatrix*> freefaceinequ;
 DenseMatrix *oldutofreezone,*oldutofreezonelimit;Box3d fzbox;
 std::unique_ptr<FrontTransformPlan> front_transform_plan;
'''
suffix=r'''
 void SetFreeZoneTransformation(const Vector&,int);
 void SetFreeZoneTransformationPlanned(const Vector&,int,int,double*);
};
'''
main=r'''
int main(){
 DenseMatrix weights(4,4),inequalities(4,4),empty(0,4);
 for(int i=1;i<=4;++i)weights.Set(i,i,1);
 NgArray<threeint> faces{{1,2,3},{1,3,4},{1,4,2},{2,4,3}},empty_faces;
 vnetrule rule;rule.points.SetSize(4);rule.freezone.SetSize(4);rule.freesets={1,2};
 rule.freefaces={&faces,&empty_faces};rule.freefaceinequ={&inequalities,&empty};rule.oldutofreezone=rule.oldutofreezonelimit=&weights;
 Vector a{0,0,0,1,0,0,0,1,0,0,0,1};
 Vector b{0,0,0,1,0,0,0,1,0,0,0,0}; // row 2 degenerates and retains old coefficients
 for(int trial=0;trial<2000;++trial) {
  for(size_t i=0;i<inequalities.v.size();++i)inequalities.v[i]=double(trial+1)+double(i)*.125;
  auto initial=rule.SaveFrontReplayState();
  rule.SetFreeZoneTransformation(a,2);rule.SetFreeZoneTransformation(b,2);
  auto correct=rule.SaveFrontReplayState();
  rule.RestoreFrontReplayState(initial);rule.SetFreeZoneTransformation(b,2);
  auto wrong=rule.SaveFrontReplayState();assert(wrong!=correct); // omitting a is NOT safe
  rule.RestoreFrontReplayState(initial);rule.SetFreeZoneTransformation(a,2);rule.SetFreeZoneTransformation(b,2);
  assert(rule.SaveFrontReplayState()==correct); // new route preserves every transformation
  rule.RestoreFrontReplayState(initial);rule.SetFreeZoneTransformation(b,2);
  auto first=rule.SaveFrontReplayState();rule.SetFreeZoneTransformation(a,2);
  auto optimized_end=rule.SaveFrontReplayState();
  rule.transfreezone.Elem(1).X(1)+=1;assert(rule.SaveFrontReplayState()!=optimized_end);
  rule.RestoreFrontReplayState(optimized_end);
  rule.fzbox.lo.X(1)-=1;assert(rule.SaveFrontReplayState()!=optimized_end);
  rule.RestoreFrontReplayState(optimized_end);
  rule.SetFreeZoneTransformation(b,2);assert(rule.SaveFrontReplayState()!=first); // wrong replay start
  rule.RestoreFrontReplayState(initial);rule.SetFreeZoneTransformation(b,2);
  assert(rule.SaveFrontReplayState()==first); // oracle starts with EXACT original state
  rule.RestoreFrontReplayState(optimized_end);assert(rule.SaveFrontReplayState()==optimized_end);
 }
 std::cout<<"PASS: 2000 legacy degenerate-state sequences, exact replay restore, empty matrices\n";
}
'''
with tempfile.TemporaryDirectory() as directory:
    tmp=Path(directory);cpp=tmp/'state.cpp';exe=tmp/'state'
    cpp.write_text(prefix+snapshot+suffix+transform+main)
    subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-I',str(ROOT/'netgen/libsrc/meshing'),str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
