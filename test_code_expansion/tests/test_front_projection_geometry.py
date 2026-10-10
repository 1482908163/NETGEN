#!/usr/bin/env python3
"""Run production intersection code and compare every floating iteration state.
Small geometry containers exercise arithmetic, not end-to-end mesh performance.
"""
import ast,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
tree=ast.parse((ROOT/'test_code_expansion/tests/test_front_bound_state.py').read_text())
prefix=next(n.value.value for n in tree.body if isinstance(n,ast.Assign) and n.targets[0].id=='prefix')
prefix=prefix[:prefix.index('struct vnetrule {')]
prefix=prefix.replace('#include <array>','#include <array>\n#include <random>\n#include <limits>\n#include <cfenv>')
prefix=prefix.replace('double X()const{return v[0];}double Y()const{return v[1];}double Z()const{return v[2];}double Length()',
 '''double X()const{return v[0];}double& X(){return v[0];}double Y()const{return v[1];}double& Y(){return v[1];}double Z()const{return v[2];}double& Z(){return v[2];}
 Vec3d(double x,double y,double z):v{x,y,z}{}
 Vec3d& operator/=(double x){for(double &a:v)a/=x;return *this;}
 Vec3d& operator*=(double x){for(double &a:v)a*=x;return *this;}
 double Length()''')
prefix=prefix.replace('void SetSize(int n){this->resize(n);}', 'void SetSize(int n){this->resize(n);}void Append(T value){this->push_back(value);}')
prefix+='''
Vec3d operator-(const Point3d&a,const Point3d&b){return Vec3d(a.X()-b.X(),a.Y()-b.Y(),a.Z()-b.Z());}
double operator*(const Vec3d&a,const Vec3d&b){return a.X()*b.X()+a.Y()*b.Y()+a.Z()*b.Z();}
Vec3d Cross(const Vec3d&a,const Vec3d&b){Vec3d n;Cross(a,b,n);return n;}
std::ostream& operator<<(std::ostream&o,const Vec3d&v){return o<<v.X()<<v.Y()<<v.Z();}
template<class T,int N>using NgArrayMem=NgArray<T>;
double sqr(double x){return x*x;}
using std::endl;
std::ostream *testout=&std::cerr;
std::vector<unsigned char> trace;
void record(std::initializer_list<double> values){for(double x:values){const auto *p=reinterpret_cast<const unsigned char*>(&x);trace.insert(trace.end(),p,p+sizeof(x));}}
struct vnetrule {
 NgArray<Point3d> transfreezone;NgArray<int> freezonepi;
 NgArray<NgArray<int>*> freesets;
 NgArray<NgArray<threeint>*> freefaces;NgArray<DenseMatrix*> freefaceinequ;
 int IsTriangleInFreeZone(const Point3d&,const Point3d&,const Point3d&,const NgArray<int>&,int,int=0,double * =nullptr);
 int IsQuadInFreeZone(const Point3d&,const Point3d&,const Point3d&,const Point3d&,const NgArray<int>&,int,int=0,double * =nullptr);
 int IsQuadInFreeSet(const Point3d&,const Point3d&,const Point3d&,const Point3d&,int,const NgArray<int>&,int,int=0,double * =nullptr);
 int IsTriangleInFreeSet(const Point3d&,const Point3d&,const Point3d&,int,const NgArray<int>&,int,int=0,double*=nullptr);
};
'''.replace('double*=','double * =')
source=(ROOT/'netgen/libsrc/meshing/netrule3.cpp').read_text()
method=source[source.index('int vnetrule :: IsTriangleInFreeZone'):source.index('float vnetrule :: CalcPointDist')]
method=method.replace('      if (isin) return 1;', '      record({double(it),lam1,lam2,hpx,hpy,hpz,f,h11,h12,h22,dflam1,dflam2,double(cntout),double(isin)});\n      if (isin) return 1;')
main=r'''
int main(){
 std::mt19937_64 rng(20261010);size_t solved=0,steps=0,common=0,limits=0;
 NgArray<threeint> faces{{1,4,2},{2,4,3},{5,6,8},{6,7,8},{1,2,5},{2,6,5},
                       {4,8,3},{3,8,7},{1,5,4},{4,5,8},{2,3,6},{3,7,6}};
 DenseMatrix planes(12,4);vnetrule rule;rule.freefaces={&faces};rule.freefaceinequ={&planes};
 rule.freezonepi={1,2,3,4,5,6,7,8};
 rule.transfreezone={{-1,-1,-1},{1,-1,-1},{1,1,-1},{-1,1,-1},
                     {-1,-1,1},{1,-1,1},{1,1,1},{-1,1,1}};
 for(int j=0;j<12;++j){int axis=j/4;double sign=(j%4<2?-1:1);planes(j,axis)=sign;planes(j,3)=-1.;}
 for(int trial=0;trial<30000;++trial){
  Point3d p[3];NgArray<int> pi{0,0,0};
  for(int k=0;k<3;++k)for(int a=0;a<3;++a)p[k].v[a]=double(int(rng()%6001)-3000)/1000.;
  if(trial%5==0){for(int k=0;k<trial%4;++k){int index=1+int(rng()%8);pi.Elem(k+1)=index;p[k]=rule.transfreezone.Get(index);}}
  if(trial%11==0)p[2]=p[1]; // collapsed triangle
  if(trial%13==0){p[0]=Point3d(-2,0,0);p[1]=Point3d(0,2,0);p[2]=Point3d(0,0,2);}
  if(trial%17==0){for(auto &a:p)a.v[0]=1.+double(int(trial%3)-1)*1e-8;}
  if(trial%19==0)for(auto &a:p)a.v[2]=-0.;
  if(trial%23==0)planes(0,0)=std::numeric_limits<double>::quiet_NaN();else planes(0,0)=-1.;
  if(trial%29==0)p[0].v[0]=std::numeric_limits<double>::infinity();
  if(trial%31==0)for(auto &a:p)for(auto &v:a.v)v*=std::numeric_limits<double>::denorm_min();
  if(trial%41==0){planes(0,0)=std::numeric_limits<double>::infinity();p[0]=Point3d(-2,-2,0);p[1]=Point3d(2,-2,0);p[2]=Point3d(2,2,0);pi={0,0,0};}
  // Includes non-default rounding; the same original dot expression is used.
  if(trial%37==0)std::fesetround(FE_DOWNWARD);
  double old[16]={},candidate[16]={};trace.clear();
  int a=rule.IsTriangleInFreeSet(p[0],p[1],p[2],1,pi,trial%7!=0,0,old);auto expected=trace;
  trace.clear();int b=rule.IsTriangleInFreeSet(p[0],p[1],p[2],1,pi,trial%7!=0,1,candidate);
  if(a!=b || trace!=expected){std::cerr<<"mismatch trial "<<trial<<" results "<<a<<" "<<b<<"\n";return 1;}
  std::fesetround(FE_TONEAREST);
  for(int k:{1,2,3,4,7,14,15})assert(old[k]==candidate[k]);
  assert(old[5]==old[7] && old[6]==0 && candidate[5]==candidate[6]);
  assert(candidate[6]<=candidate[4]);
  solved+=old[2];steps+=old[3];common+=old[14];limits+=old[15];
 }
 assert(solved>1000 && steps>solved && common>1000 && limits>0);
 NgArray<int> indices{1,2,3,4,5,6,7,8};rule.freesets={&indices};
 for(int trial=0;trial<3000;++trial){
  Point3d p[4];NgArray<int> pi{0,0,0,0};
  for(int k=0;k<4;++k)for(int a=0;a<3;++a)p[k].v[a]=double(int(rng()%6001)-3000)/1000.;
  if(trial%3==0)for(int k=0;k<trial%5;++k){int index=1+int(rng()%8);pi.Elem(k+1)=index;p[k]=rule.transfreezone.Get(index);}
  double old[16]={},candidate[16]={};trace.clear();
  int a=rule.IsQuadInFreeZone(p[0],p[1],p[2],p[3],pi,1,0,old);auto expected=trace;
  trace.clear();int b=rule.IsQuadInFreeZone(p[0],p[1],p[2],p[3],pi,1,1,candidate);
  assert(a==b && trace==expected);
  for(int k:{1,2,3,4,7,14,15})assert(old[k]==candidate[k]);
 }
 std::cout<<"PASS: 3000 production quad/free-set dispatches; 30000 production triangle solves, exact iteration bytes; iterative="<<solved<<" steps="<<steps<<" common="<<common<<" limits="<<limits<<"\n";
}
'''
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);cpp=tmp/'geometry.cpp';cpp.write_text(prefix+method+main)
    for name,flags in [('checked',['-O2','-fsanitize=undefined','-fno-sanitize-recover=all']),('native',['-O3','-march=native'])]:
        exe=tmp/name
        subprocess.run(['g++','-std=c++17','-I',str(ROOT/'netgen/libsrc/meshing'),*flags,str(cpp),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
