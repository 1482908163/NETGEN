#include <mystdlib.h>
#include "meshing.hpp"
#include <sstream>
#include <cstring>


namespace netgen
{
extern double minother;
extern double minwithoutother;


  static double CalcElementBadness (const Array<Point3d, PointIndex> & points,
                                    const Element & elem)
{
  double vol, l, l4, l5, l6;
  if (elem.GetNP() != 4) 
    {
      if (elem.GetNP() == 5)
	{
	  double z = points[elem.PNum(5)].Z();
	  if (z > -1e-8) return 1e8;
	  return (-1 / z) - z; //  - 2;
	}
      return 0;
    }
  
  Vec3d v1 = points[elem.PNum(2)] - points[elem.PNum(1)];
  Vec3d v2 = points[elem.PNum(3)] - points[elem.PNum(1)];
  Vec3d v3 = points[elem.PNum(4)] - points[elem.PNum(1)];
  
  vol = - (Cross (v1, v2) * v3);
  l4 = Dist (points[elem.PNum(2)], points[elem.PNum(3)]);
  l5 = Dist (points[elem.PNum(2)], points[elem.PNum(4)]);
  l6 = Dist (points[elem.PNum(3)], points[elem.PNum(4)]);

  l = v1.Length() + v2.Length() + v3.Length() + l4 + l5 + l6;
  
  //  testout << "vol = " << vol << " l = " << l << endl;
  if (vol < 1e-8) return 1e10;
  //  (*testout) << "l^3/vol = " << (l*l*l / vol) << endl;
  
  double err = pow (l*l*l/vol, 1.0/3.0) / 12;
  return err;
}







// Each rule is claimed exactly once. Its mutable free-zone and diagnostic slots
// are exclusively owned until the team joins. Front and rotation order stay serial.
struct RuleSearchQueue { std::atomic<int> next{0}; };
struct RuleSearchResult {
  float quality = 0, error = 0;
  int found = -1;
  double other = 0, withoutother = 0, seconds = 0;
  unsigned long long rules = 0, eligible = 0, face_trials = 0, point_trials = 0, mappings = 0;
  std::string trace;
  Array<Point3d,PointIndex> points;
  Array<MiniElement2d> faces;
  NgArray<Element> elements;
  NgArray<INDEX> deleted;
};
namespace {
struct RuleLegacyTimer {
  int index; bool active;
  RuleLegacyTimer(int i, bool a):index(i),active(a) { if(active) NgProfiler::StartTimer(i); }
  ~RuleLegacyTimer() { if(active) NgProfiler::StopTimer(index); }
};
using RuleClock = std::chrono::steady_clock;
double RuleElapsed(RuleClock::time_point t) {
  return std::chrono::duration<double>(RuleClock::now()-t).count();
}
template<class T> bool RuleBitsEqual(T a, T b) { return std::memcmp(&a,&b,sizeof(T))==0; }
bool SameRuleOutput(const RuleSearchResult & a,
                    const Array<Point3d,PointIndex> & points,
                    const Array<MiniElement2d> & faces,
                    const NgArray<Element> & elements, const NgArray<INDEX> & deleted) {
  if(a.points.Size()!=points.Size() || a.faces.Size()!=faces.Size() ||
     a.elements.Size()!=elements.Size() || a.deleted.Size()!=deleted.Size()) return false;
  for(auto i:points.Range())
    for(int j=0;j<3;j++) if(!RuleBitsEqual(a.points[i].X(j+1),points[i].X(j+1))) return false;
  for(auto i:faces.Range()) {
    if(a.faces[i].GetNP()!=faces[i].GetNP()) return false;
    for(int j=1;j<=faces[i].GetNP();j++) if(a.faces[i].PNum(j)!=faces[i].PNum(j)) return false;
  }
  for(auto i:elements.Range()) {
    if(a.elements[i].GetType()!=elements[i].GetType() || a.elements[i].GetNP()!=elements[i].GetNP()) return false;
    for(int j=1;j<=elements[i].GetNP();j++) if(a.elements[i].PNum(j)!=elements[i].PNum(j)) return false;
  }
  for(auto i:deleted.Range()) if(a.deleted[i]!=deleted[i]) return false;
  return true;
}
void RecordRuleWork(VolumeKernelStats * s, const RuleSearchResult & r) {
  if(!s) return;
  s->AddRule(4,r.rules); s->AddRule(5,r.eligible);
  s->AddRule(6,r.face_trials); s->AddRule(7,r.point_trials); s->AddRule(8,r.mappings);
}
}

int Meshing3::ApplyRules(Array<Point3d,PointIndex> & p, Array<int,PointIndex> & a,
  Array<MiniElement2d> & f, INDEX split, INDEX_2_HASHTABLE<int> & pairs,
  NgArray<Element> & e, NgArray<INDEX> & d, int tol, double sloppy, int rot, float & err) {
  return ApplyRulesImpl(p,a,f,split,pairs,e,d,tol,sloppy,rot,err,nullptr,nullptr);
}

int Meshing3::ApplyRulesTasks(Array<Point3d,PointIndex> & p, Array<int,PointIndex> & a,
  Array<MiniElement2d> & f, INDEX split, INDEX_2_HASHTABLE<int> & pairs,
  NgArray<Element> & e, NgArray<INDEX> & d, int tol, double sloppy, int rot, float & err,
  int mode, VolumeKernelStats * stats) {
  if(mode<0 || (!mode && !stats)) return ApplyRules(p,a,f,split,pairs,e,d,tol,sloppy,rot,err);
  const auto begin=RuleClock::now();
  if(stats) stats->AddRule(0,1);
  struct CallTimer {
    VolumeKernelStats * stats; RuleClock::time_point begin;
    ~CallTimer() {
      if(!stats) return;
      double t=RuleElapsed(begin); stats->AddRule(9,t); stats->MaxRule(15,t);
      const double edges[]={32e-6,128e-6,512e-6,2e-3,8e-3};
      int b=0; while(b<5 && t>edges[b]) ++b;
      stats->AddRule(18+b,1);
    }
  } call_timer{stats,begin};
  // Verbose/test rules keep the original ordered diagnostics. Nested teams
  // also fall back: no extra workers or unaccounted resource oversubscription.
  bool debug=testmode || tol>5;
  for(auto & rule:rules) debug=debug || rule->TestFlag('t');
  bool parallel=mode && !debug && rules.Size()>1 && ngcore::task_manager &&
                ngcore::TaskManager::GetNumThreads()>1;
  if(!parallel) {
    RuleSearchResult work;
    int found=ApplyRulesImpl(p,a,f,split,pairs,e,d,tol,sloppy,rot,err,nullptr,stats?&work:nullptr);
    RecordRuleWork(stats,work);
    if(stats) { stats->AddRule(3,1); if(mode && debug) stats->AddRule(16,1); }
    return found;
  }
  if(stats) stats->AddRule(1,1);
  const bool verify=mode==2;
  const double old_other=minother, old_without=minwithoutother;
  Array<int> old_found,old_can;
  if(verify) { old_found=foundmap;old_can=canuse; }
  const int workers=std::min(int(rules.Size()),ngcore::TaskManager::GetNumThreads());
  std::vector<std::unique_ptr<RuleSearchResult>> results;
  auto stamp=RuleClock::now();
  for(int i=0;i<workers;i++) {
    results.emplace_back(new RuleSearchResult);
    results.back()->points=p;results.back()->faces=f;
  }
  if(stats) stats->AddRule(10,RuleElapsed(stamp));
  RuleSearchQueue queue;
  stamp=RuleClock::now();
  ngcore::ParallelFor(size_t(workers),[&](size_t i) {
    auto & r=*results[i];const auto t=RuleClock::now();
    r.found=ApplyRulesImpl(r.points,a,r.faces,split,pairs,r.elements,r.deleted,
                          tol,sloppy,rot,r.error,&queue,&r);
    r.seconds=RuleElapsed(t);
  },workers);
  if(stats) stats->AddRule(11,RuleElapsed(stamp));
  int winner=-1, found=-1;
  double longest=0;
  for(int i=0;i<workers;i++) {
    const auto & r=*results[i];RecordRuleWork(stats,r);
    if(stats) stats->AddRule(12,r.seconds);
    longest=std::max(longest,r.seconds);
    minother=std::min(minother,r.other);minwithoutother=std::min(minwithoutother,r.withoutother);
    if(!r.trace.empty()) (*testout)<<r.trace;
    // Strict original comparison: equal qualities keep the earliest rule.
    if(r.found>0 && (winner<0 || r.quality<results[winner]->quality ||
       (r.quality==results[winner]->quality && r.found<results[winner]->found))) winner=i;
    if(r.found==0) found=0;
  }
  if(stats) stats->AddRule(13,longest);
  if(winner>=0) found=results[winner]->found;
  err=results[0]->error;
  if(verify) {
    const auto verify_begin=RuleClock::now();
    Array<int> parallel_found(foundmap),parallel_can(canuse);
    Array<string> parallel_problems(problems);
    foundmap=old_found;canuse=old_can;
    const double parallel_other=minother,parallel_without=minwithoutother;
    minother=old_other;minwithoutother=old_without;
    float serial_error;
    RuleSearchResult reference;
    int serial_found=ApplyRulesImpl(p,a,f,split,pairs,e,d,tol,sloppy,rot,serial_error,nullptr,&reference);
    bool same=serial_found==found && RuleBitsEqual(serial_error,err) &&
      RuleBitsEqual(minother,parallel_other) && RuleBitsEqual(minwithoutother,parallel_without);
    for(int i:foundmap.Range()) same=same && foundmap[i]==parallel_found[i] &&
      canuse[i]==parallel_can[i] && problems[i]==parallel_problems[i];
    // No-winner output must also preserve the input arrays and empty outputs.
    same=same && SameRuleOutput(*results[winner>=0?winner:0],p,f,e,d);
    unsigned long long face=0,point=0,maps=0;
    for(auto & r:results) {face+=r->face_trials;point+=r->point_trials;maps+=r->mappings;}
    same=same && face==reference.face_trials && point==reference.point_trials && maps==reference.mappings;
    if(stats) {stats->AddRule(2,1);stats->AddRule(14,RuleElapsed(verify_begin));}
    if(!same) {
      if(stats) stats->AddRule(17,1);
      throw NgException("规则搜索等价检查失败: class="+ToString(tol)+" rotation="+ToString(rot)+
                        " serial="+ToString(serial_found)+" parallel="+ToString(found));
    }
    // Serial verification has already materialized the exactly checked output.
  } else if(winner>=0) {
    p=results[winner]->points;f=results[winner]->faces;
    e=results[winner]->elements;d=results[winner]->deleted;
  } else {e.SetSize(0);d.SetSize(0);}
  // This includes verification when enabled; the separate verification metric
  // is explanatory only, and is never subtracted from natural core timings.
  return found;
}

int Meshing3 :: ApplyRulesImpl
(
 Array<Point3d, PointIndex> & lpoints,     // in: local points, out: old+new local points
 Array<int, PointIndex> & allowpoint,      // in: 2 .. it is allowed to use pointi, 1..will be allowed later, 0..no means
 Array<MiniElement2d> & lfaces,    // in: local faces, out: old+new local faces
 INDEX lfacesplit,	       // for local faces in outer radius
 INDEX_2_HASHTABLE<int> & connectedpairs,  // connected pairs for prism-meshing
 NgArray<Element> & elements,    // out: new elements
 NgArray<INDEX> & delfaces,      // out: face indices of faces to delete
 int tolerance,                // quality class: 1 best 
 double sloppy,                // quality strength
 int rotind1,                  // how to rotate base element
 float & retminerr,            // element error
 RuleSearchQueue * queue, RuleSearchResult * result
 )

{
  // static Timer t("ruler3 - all"); RegionTimer reg(t);
  // static Timer tstart("ruler3 - rule start");   
  // static Timer tloop("ruler3 - rule loop"); 

  // tstart.Start();
  float err, minerr, teterr, minteterr;
  char ok, found, hc;
  // vnetrule * rule;
  Vector oldu, newu, newu1, newu2, allp;
  Vec3d ui;
  Point3d np;
  const MiniElement2d * locface = NULL;
  int loktestmode;


  NgArray<int, PointIndex::BASE> pused;      // point is already mapped, number of uses
  NgArrayMem<char,100> fused;                       // face is already mapped
  NgArrayMem<PointIndex,100> pmap;                  // map of reference point to local point
  NgArrayMem<bool,100> pfixed;                      // point mapped by face-map
  NgArrayMem<int,100> fmapi;                        // face in reference is mapped to face nr ...
  NgArrayMem<int,100> fmapr;                        // face in reference is rotated to map 
  NgArrayMem<Point3d,100> transfreezone;            // transformed free-zone
  INDEX_2_CLOSED_HASHTABLE<int> ledges(100); // edges in local environment
  
  NgArrayMem<Point3d,100> tempnewpoints;
  NgArray<MiniElement2d> tempnewfaces;
  NgArrayMem<int,100> tempdelfaces;
  NgArray<Element> tempelements;
  NgArrayMem<Box3d,100> triboxes;         // bounding boxes of local faces

  NgArray<int, PointIndex::BASE> pnearness;
  NgArray<int> fnearness;

  static int cnt = 0;
  if (!queue) cnt++;
  std::unique_ptr<std::ostringstream> worker_trace;
  if(queue) worker_trace.reset(new std::ostringstream);
  std::ostream & rule_out = queue ? *worker_trace : *testout;
  double local_minother = minother, local_minwithoutother = minwithoutother;
  
  delfaces.SetSize (0);
  elements.SetSize (0);

  // determine topological distance of faces and points to
  // base element

  pnearness.SetSize (lpoints.Size());
  fnearness.SetSize (lfacesplit);

  pnearness = INT_MAX/10;

  for (PointIndex pi : lfaces[0].PNums())
    pnearness[pi] = 0;
  
  RuleLegacyTimer reg2(98, !queue);
  
  if (!queue) NgProfiler::StartTimer (90);

  for (int loop = 0; loop < 2; loop++)
    {

      for (int i = 0; i < lfacesplit; i++)
	{
	  const MiniElement2d & hface = lfaces[i];

	  int minn = INT_MAX-1;
	  for (PointIndex pi : hface.PNums())
	    {
	      int hi = pnearness[pi];
	      if (hi < minn) minn = hi;
	    }
	  if (minn < INT_MAX/10)
	    for (PointIndex pi : hface.PNums())
	      if (pnearness[pi] > minn+1)
		pnearness[pi] = minn+1;
	}

      for (int i = 1; i <= connectedpairs.GetNBags(); i++)
	for (int j = 1; j <= connectedpairs.GetBagSize(i); j++)
	  {
	    INDEX_2 edge;
	    int val;
	    connectedpairs.GetData (i, j, edge, val);

	    if (pnearness[edge.I1()] > pnearness[edge.I2()] + 1)
	      pnearness[edge.I1()] = pnearness[edge.I2()] + 1;

	    if (pnearness[edge.I2()] > pnearness[edge.I1()] + 1)
	      pnearness[edge.I2()] = pnearness[edge.I1()] + 1;
	  }
    }

  for (int i : fnearness.Range())
    {
      int sum = 0;
      for (PointIndex pi : lfaces[i].PNums())
        sum += pnearness[pi];
      fnearness[i] = sum;
    }

  
  if (!queue) NgProfiler::StopTimer (90);
  if (!queue) NgProfiler::StartTimer (91);

  // find bounding boxes of faces

  triboxes.SetSize (lfaces.Size());
  // for (int i = 0; i < lfaces.Size(); i++)
  for (auto i : lfaces.Range())
    {
      const MiniElement2d & face = lfaces[i];
      triboxes[i].SetPoint (lpoints[face[0]]);
      for (int j = 1; j < face.GetNP(); j++)
	triboxes[i].AddPoint (lpoints[face[j]]);
    }

  if (!queue) NgProfiler::StopTimer (91);
  if (!queue) NgProfiler::StartTimer (92);

  
  bool useedges = false;
  for (int ri = 0; ri < rules.Size(); ri++)
    if (rules[ri]->GetNEd()) useedges = true;

  if (useedges)
    {
      ledges.SetSize (5 * lfacesplit);
      
      for (int j = 0; j < lfacesplit; j++)
	// if (fnearness[j] <= 5) 
	  {
	    const MiniElement2d & face = lfaces[j];
	    int newp, oldp;
	    
	    newp = face[face.GetNP()-1];
	    for (int k = 0; k < face.GetNP(); k++)
	      {
		oldp = newp;
		newp = face[k];
		ledges.Set (INDEX_2::Sort(oldp, newp), 1);
	      }
	  }
    }

  if (!queue) NgProfiler::StopTimer (92);

  RuleLegacyTimer reg3(99, !queue);

  pused.SetSize (lpoints.Size());
  fused.SetSize (lfaces.Size());

  found = 0;
  minerr = tolfak * tolerance * tolerance;
  minteterr = sloppy * tolerance;

  if (testmode)
    rule_out << "cnt = " << cnt << " class = " << tolerance << endl;



  // impossible, if no rule can be applied at any tolerance class
  bool impossible = 1;


  // check each rule:
  // tstart.Stop();
  // tloop.Start();
  int serial_rule = 0;
  for (int rim = queue ? queue->next.fetch_add(1, std::memory_order_relaxed) : serial_rule++;
       rim < rules.Size();
       rim = queue ? queue->next.fetch_add(1, std::memory_order_relaxed) : serial_rule++)
    {
      if (result) result->rules++;
      int base = (lfaces[0].GetNP() == 3) ? 100 : 200;
      RuleLegacyTimer regx1(base, !queue);
      RuleLegacyTimer regx(base+rim+1, !queue);

      // sprintf (problems.Elem(ri), "");
      // *problems.Elem(ri) = '\0';
      problems[rim] = "";

      vnetrule * rule = rules[rim].get();
      
      if (rule->GetNP(1) != lfaces[0].GetNP())
	continue;

      if (rule->GetQuality() > tolerance)
	{
	  if (rule->GetQuality() < 100) impossible = 0;

	  if (testmode)
	    problems[rim] = "Quality not ok";
	  continue;
	}
      
      if (testmode)
	problems[rim] = "no mapping found";
      
      if (result) result->eligible++;
      loktestmode = testmode || rule->TestFlag ('t') || tolerance > 5;

      if (loktestmode)
	rule_out << "Rule " << rim+1 << " = " << rule->Name() << endl;
      
      pmap.SetSize (rule->GetNP());
      fmapi.SetSize (rule->GetNF());
      fmapr.SetSize (rule->GetNF());
      
      fused = 0;
      pused = 0;
      for (auto & p : pmap) p.Invalidate();
      fmapi = 0;

      for (int i : fmapr.Range())
        fmapr[i] = rule->GetNP(i+1);
      
      fused[0] = 1;
      fmapi[0] = 1;
      fmapr[0] = rotind1;

      for (int j = 1; j <= lfaces[0].GetNP(); j++)
	{
	  PointIndex locpi = lfaces[0].PNumMod (j+rotind1);
	  pmap.Set (rule->GetPointNr (1, j), locpi);
	  pused[locpi]++;
	}

      /*
	map all faces
	nfok .. first nfok-1 faces are mapped properly
	*/

      int nfok = 2;
      RuleLegacyTimer regfa(300, !queue);
      RuleLegacyTimer regx2(base+50+rim+1, !queue);
      while (nfok >= 2)
	{
	  
	  if (nfok <= rule->GetNOldF())
	    {
	      // not all faces mapped

	      ok = 0;
	      int locfi = fmapi.Get(nfok);
	      int locfr = fmapr.Get(nfok);

	      int actfnp = rule->GetNP(nfok);

	      while (!ok)
		{
		  if (result) result->face_trials++;
		  locfr++;
		  if (locfr == actfnp + 1)
		    {
		      locfr = 1;
		      locfi++;
		      if (locfi > lfacesplit) break;
		    }
		  
		  
		  if (fnearness.Get(locfi) > rule->GetFNearness (nfok) ||
		      fused.Get(locfi) ||
		      actfnp != lfaces[locfi-1].GetNP() )
		    {
		      // face not feasible in any rotation

		      locfr = actfnp;
		    }
		  else
		    {
		      
		      ok = 1;
		      
		      locface = &lfaces[locfi-1];

		      
		      // reference point already mapped differently ?
		      for (int j = 1; j <= actfnp && ok; j++)
			{
			  PointIndex locpi = pmap.Get(rule->GetPointNr (nfok, j));
			  if (locpi.IsValid() && locpi != locface->PNumMod(j+locfr))
			    ok = 0;
			}
		      
		      // local point already used or point outside tolerance ?
		      for (int j = 1; j <= actfnp && ok; j++)
			{
			  int refpi = rule->GetPointNr (nfok, j);
			  
			  if (!pmap.Get(refpi).IsValid())
			    {
			      PointIndex locpi = locface->PNumMod (j + locfr);

			      if (pused[locpi])
				ok = 0;
			      else
				{
				  const Point3d & lp = lpoints[locpi];
				  const Point3d & rp = rule->GetPoint(refpi);

				  if ( Dist2 (lp, rp) * rule->PointDistFactor(refpi) > minerr)
				    {
				      impossible = 0;
				      ok = 0;
				    }
				}
			    }
			}
		    }
		}
	      
	      
	      if (ok)
		{
		  // map face nfok

		  fmapi.Set (nfok, locfi);
		  fmapr.Set (nfok, locfr);
		  fused.Set (locfi, 1);
		  
		  for (int j = 1; j <= rule->GetNP (nfok); j++)
		    {
		      PointIndex locpi = locface->PNumMod(j+locfr);
		      
		      if (rule->GetPointNr (nfok, j) < IndexBASE<PointIndex>()+3 &&
			  pmap.Get(rule->GetPointNr(nfok, j)) != locpi)
			rule_out << "change face1 point, mark1" << endl;
		      
		      pmap.Set(rule->GetPointNr (nfok, j), locpi);
		      pused[locpi]++;
		    }
		  
		  nfok++;
		}
	      else
		{
		  // backtrack one face
		  fmapi.Set (nfok, 0);
		  fmapr.Set (nfok, rule->GetNP(nfok));
		  nfok--;
		  
		  fused.Set (fmapi.Get(nfok), 0);
		  for (int j = 1; j <= rule->GetNP (nfok); j++)
		    {
		      int refpi = rule->GetPointNr (nfok, j);
		      pused[pmap.Get(refpi)]--;
                      
		      if (pused[pmap.Get(refpi)] == 0)
			{
			  // pmap.Set(refpi, 0);
                          pmap.Elem(refpi).Invalidate();
			}
		    }
		}
	    }
	  
	  else
	    
	    { 
	      RuleLegacyTimer regfb(301, !queue);

	      // all faces are mapped
	      // now map all isolated points:
	      
	      if (loktestmode)
		{
		  rule_out << "Faces Ok" << endl;
		  problems[rim] = "Faces Ok";
		}

	      int npok = 1;
	      int incnpok = 1;
	      
	      pfixed.SetSize (pmap.Size());
              /*
	      for (int i = 1; i <= pmap.Size(); i++)
		pfixed.Set(i, (pmap.Get(i) != 0) );
              */
              for (int i : pmap.Range())
                pfixed[i] = pmap[i].IsValid();
                  
	      while (npok >= 1)
		{
		  
		  if (npok <= rule->GetNOldP())
		    {
		      
		      if (pfixed.Get(npok))
			
			{
			  if (incnpok)
			    npok++;
			  else
			    npok--;
			}
		      
		      else
			
			{
			  PointIndex locpi = pmap.Elem(npok);
			  ok = 0;
			  
			  if (locpi.IsValid())
			    pused[locpi]--;
			  
			  while (!ok && locpi < lpoints.Size()-1+IndexBASE<PointIndex>())
			    {
			      ok = 1;
			      if (result) result->point_trials++;
			      locpi++;
			      
			      if (pused[locpi] || 
				  pnearness[locpi] > rule->GetPNearness(npok))
				{
				  ok = 0;
				}
			      else if (allowpoint[locpi] != 2)
				{
				  ok = 0;
				  if (allowpoint[locpi] == 1)
				    impossible = 0;
				}
			      else
				{
				  const Point3d & lp = lpoints[locpi];
				  const Point3d & rp = rule->GetPoint(npok);

				  if ( Dist2 (lp, rp) * rule->PointDistFactor(npok) > minerr)
				    {
				      ok = 0;
				      impossible = 0;
				    }
				}
			    }
			  
			  
			  if (ok)
			    {
			      pmap.Set (npok, locpi);
			      
			      if (npok <= 3)
				rule_out << "set face1 point, mark3" << endl;
			      
			      pused[locpi]++;
			      npok++;
			      incnpok = 1;
			    }
			  
			  else
			    
			    {
			      // pmap.Set (npok, 0);
                              pmap.Elem(npok).Invalidate();
			      
			      if (npok <= 3)
				rule_out << "set face1 point, mark4" << endl;
			      
			      npok--;
			      incnpok = 0;
			    }
			}
		    }
		  
		  else
		    
		    {
		      RuleLegacyTimer regfa2(302, !queue);

		      if (result) result->mappings++;
		      // all points are mapped
		      
		      if (loktestmode)
			{
			  rule_out << "Mapping found!!: Rule " << rule->Name() << endl;
			  for (auto pi : pmap)
			    rule_out << pi << " ";
			  rule_out << endl;
			  problems[rim] = "mapping found";
			  rule_out << rule->GetNP(1) << " = " << lfaces[0].GetNP() << endl;
			}
		      
		      ok = 1;
		      
		      
		      // check mapedges:
		      for (int i = 1; i <= rule->GetNEd(); i++)
			{
			  INDEX_2 in2(pmap.Get(rule->GetEdge(i).i1),
				      pmap.Get(rule->GetEdge(i).i2));
			  in2.Sort();
			  if (!ledges.Used (in2)) ok = 0;
			}


		      // check prism edges:
		      for (int i = 1; i <= rule->GetNE(); i++)
			{
			  const Element & el = rule->GetElement (i);
			  if (el.GetType() == PRISM) 
			    { 
			      for (int j = 1; j <= 3; j++)
				{
				  INDEX_2 in2(pmap.Get(el.PNum(j)),
					      pmap.Get(el.PNum(j+3)));      
				  in2.Sort();
				  if (!connectedpairs.Used (in2)) ok = 0;
				}
			    }
			  if (el.GetType() == PYRAMID) 
			    { 
			      if (loktestmode)
				rule_out << "map pyramid, rule = " << rule->Name() << endl;
			      for (int j = 1; j <= 2; j++)
				{
				  INDEX_2 in2;
				  if (j == 1)
				    {
				      in2.I1() = pmap.Get(el.PNum(2));
				      in2.I2() = pmap.Get(el.PNum(3));
				    }
				  else
				    {
				      in2.I1() = pmap.Get(el.PNum(1));
				      in2.I2() = pmap.Get(el.PNum(4));
				    }
				  in2.Sort();
				  if (!connectedpairs.Used (in2)) 
				    {
				      ok = 0;
				      if (loktestmode)
					rule_out << "no pair" << endl;
				    }
				}
			    }

			}
		      

		      
		      for (int i = rule->GetNOldF() + 1; i <= rule->GetNF(); i++)
			fmapi.Set(i, 0);
		      

		      if (ok)
                        foundmap[rim]++;
		      


		      // deviation of existing points

		      oldu.SetSize (3 * rule->GetNOldP());
		      newu.SetSize (3 * (rule->GetNP() - rule->GetNOldP()));
		      allp.SetSize (3 * rule->GetNP());
		      
		      for (int i = 1; i <= rule->GetNOldP(); i++)
			{
			  const Point3d & lp = lpoints[pmap.Get(i)];
			  const Point3d & rp = rule->GetPoint(i);
			  oldu (3*i-3) = lp.X()-rp.X();
                          oldu (3*i-2) = lp.Y()-rp.Y();
			  oldu (3*i-1) = lp.Z()-rp.Z();
			  
			  allp (3*i-3) = lp.X();
                          allp (3*i-2) = lp.Y();
                          allp (3*i-1) = lp.Z();
			}

		      if (rule->GetNP() > rule->GetNOldP())
			{
			  newu.SetSize (rule->GetOldUToNewU().Height());
			  rule->GetOldUToNewU().Mult (oldu, newu);
			}

		      //		      int idiff = 3 * (rule->GetNP()-rule->GetNOldP());
		      int idiff = 3 * rule->GetNOldP();
		      for (int i = rule->GetNOldP()+1; i <= rule->GetNP(); i++)
			{
			  const Point3d & rp = rule->GetPoint(i);
			  allp (3*i-3) = rp.X() + newu(3*i-3 - idiff);
                          allp (3*i-2) = rp.Y() + newu(3*i-2 - idiff);
                          allp (3*i-1) = rp.Z() + newu(3*i-1 - idiff);
			}
		      
		      rule->SetFreeZoneTransformation (allp, 
						       tolerance + int(sloppy));

		      if (!rule->ConvexFreeZone())
			{
			  ok = 0;
			  problems[rim] = "Freezone not convex";

			  if (loktestmode)
			    rule_out << "Freezone not convex" << endl;
			}

		      if (loktestmode)
			{
			  const NgArray<Point3d> & fz = rule->GetTransFreeZone();
			  rule_out << "Freezone: " << endl;
			  for (int i = 1; i <= fz.Size(); i++)
			    rule_out << fz.Get(i) << endl;
			}
		      

		      // check freezone:
		      
		      // for (int i = 1; i <= lpoints.Size(); i++)
                      for (auto i : lpoints.Range())
			{
			  if ( !pused[i] )
			    {
			      const Point3d & lp = lpoints[i];

			      if (rule->fzbox.IsIn (lp))
				{
				  if (rule->IsInFreeZone(lp))
				    {
				      if (loktestmode)
					{
					  rule_out << "Point " << i
						     << " in Freezone" << endl;
					  problems[rim] = "locpoint " + ToString(i) + " in Freezone";
					}
				      ok = 0;
				      break;
				    }
				}
			    }
			}

		      for (int i = 1; i <= lfaces.Size() && ok; i++)
			{
			  NgArrayMem<int, 10> lpi(4);

			  if (!fused.Get(i))
			    { 
			      int triin;
			      const MiniElement2d & lfacei = lfaces[i-1];

			      if (!triboxes.Elem(i).Intersect (rule->fzbox))
				triin = 0;
			      else
				{
				  int li, lj;
				  for (li = 1; li <= lfacei.GetNP(); li++)
				    {
				      int lpii = 0;
				      PointIndex pi = lfacei.PNum(li);
				      for (lj = 1; lj <= rule->GetNOldP(); lj++)
					if (pmap.Get(lj) == pi)
					  lpii = lj;
				      lpi.Elem(li) = lpii;
				    }


				  if (lfacei.GetNP() == 3)
				    {
				      triin = rule->IsTriangleInFreeZone 
					(
					 lpoints[lfacei.PNum(1)],
					 lpoints[lfacei.PNum(2)],
					 lpoints[lfacei.PNum(3)], lpi, 1
					 );
				    }
				  else
				    {
				      triin = rule->IsQuadInFreeZone 
					(
					 lpoints[lfacei.PNum(1)],
					 lpoints[lfacei.PNum(2)],
					 lpoints[lfacei.PNum(3)], 
					 lpoints[lfacei.PNum(4)], 
					 lpi, 1
					 );
				    }
				}


			      if (triin == -1)
				{
				  ok = 0;
				}
			      
			      if (triin == 1)
				{
#ifdef TEST_JS
				  ok = 0;

				  if (loktestmode)
				    {
				      rule_out << "El with " << lfaces[i-1].GetNP() << " points in freezone: "
						 << lfaces[i-1].PNum(1) << " - " 
						 << lfaces[i-1].PNum(2) << " - "
						 << lfaces[i-1].PNum(3) << " - "
						 << lfaces[i-1].PNum(4) << endl;
				      for (int lj = 1; lj <= lfaces[i-1].GetNP(); lj++)
					rule_out << lpoints[lfaces[i-1].PNum(lj)] << " ";

				      rule_out << endl;

				      sprintf (problems.Elem(ri), "triangle (%d, %d, %d) in Freezone",
					       lfaces[i-1].PNum(1), lfaces[i-1].PNum(2),
					       lfaces[i-1].PNum(3));
				    }
#else
				  if (loktestmode)
				    {
				      if (lfacei.GetNP() == 3)
					{
					  rule_out << "Triangle in freezone: "
						     << lfacei.PNum(1) << " - " 
						     << lfacei.PNum(2) << " - "
						     << lfacei.PNum(3) 
						     << ", or "
						     << lpoints[lfacei.PNum(1)] << " - " 
						     << lpoints[lfacei.PNum(2)] << " - "
						     << lpoints[lfacei.PNum(3)] 
						     << endl;
					  rule_out << "lpi = " << lpi.Get(1) << ", "
						     << lpi.Get(2) << ", " << lpi.Get(3) << endl;
					}
				      else
					  rule_out << "Quad in freezone: "
						     << lfacei.PNum(1) << " - " 
						     << lfacei.PNum(2) << " - "
						     << lfacei.PNum(3) << " - "
						     << lfacei.PNum(4) 
						     << ", or "
						     << lpoints[lfacei.PNum(1)] << " - " 
						     << lpoints[lfacei.PNum(2)] << " - "
						     << lpoints[lfacei.PNum(3)] << " - "
						     << lpoints[lfacei.PNum(4)] 
						     << endl;

				      problems[rim] = "triangle ("+ToString(lfaces[i-1].PNum(1))+", "
                                        + ToString(lfaces[i-1].PNum(2)) + ", "
                                        + ToString(lfaces[i-1].PNum(3)) + ") in Freezone";
				    }	

				  hc = 0;
				  for (int k = rule->GetNOldF() + 1; k <= rule->GetNF(); k++)
				    {
				      if (rule->GetPointNr(k, 1) < IndexBASE<PointIndex>()+rule->GetNOldP() &&
					  rule->GetPointNr(k, 2) < IndexBASE<PointIndex>()+rule->GetNOldP() &&
					  rule->GetPointNr(k, 3) < IndexBASE<PointIndex>()+rule->GetNOldP())
					{
					  for (int j = 1; j <= 3; j++)
					    if (lfaces[i-1].PNumMod(j  ) == pmap.Get(rule->GetPointNr(k, 1)) &&
						lfaces[i-1].PNumMod(j+1) == pmap.Get(rule->GetPointNr(k, 3)) &&
						lfaces[i-1].PNumMod(j+2) == pmap.Get(rule->GetPointNr(k, 2)))
					      {
						fmapi.Elem(k) = i;
						hc = 1;

						
 // 						rule_out << "found from other side: "
//  							   << rule->Name() 
//  							   << " ( " << pmap.Get (rule->GetPointNr(k, 1))
//  							   << " - " << pmap.Get (rule->GetPointNr(k, 2))
//  							   << " - " << pmap.Get (rule->GetPointNr(k, 3)) << " ) "
//  							   << endl;

						problems[rim] = "other";
					      }
					}
				    }
				  
				  if (!hc)
				    {
				      if (loktestmode)
					{
					  rule_out << "Triangle in freezone: "
						     << lfaces[i-1].PNum(1) << " - " 
						     << lfaces[i-1].PNum(2) << " - "
						     << lfaces[i-1].PNum(3) << endl;

                                          /*
					  snprintf (problems.Elem(ri), 255, "triangle (%d, %d, %d) in Freezone",
						   int (lfaces[i-1].PNum(1)), 
						   int (lfaces[i-1].PNum(2)),
						   int (lfaces[i-1].PNum(3)));
                                          */
                                          problems[rim] = "triangle ("
                                            + ToString(lfaces[i-1].PNum(1))+", "
                                            + ToString(lfaces[i-1].PNum(2)) + ", "
                                            + ToString(lfaces[i-1].PNum(3)) + ") in Freezone";
                                          
					}
				      ok = 0;
				    }
#endif
				}
			    }
			   
			}

		      
		      if (ok)
			{
			  err = 0;
			  for (int i = 1; i <= rule->GetNOldP(); i++)
			    {
			      double hf = rule->CalcPointDist (i, lpoints[pmap.Get(i)]);
			      if (hf > err) err = hf;
			    }
			  
			  
			  if (loktestmode)
			    {
			      rule_out << "Rule ok" << endl;
			      problems[rim] = "Rule ok, err = "+ToString(err);
			    }


			  //			  newu = rule->GetOldUToNewU() * oldu;

			  // set new points:
                          int oldnp = rule->GetNOldP();
			  int noldlp = lpoints.Size();
			  int noldlf = lfaces.Size();
			  
			  for (int i = oldnp + 1; i <= rule->GetNP(); i++)
			    {
			      np = rule->GetPoint(i);
			      np.X() += newu (3 * (i-oldnp) - 3);
			      np.Y() += newu (3 * (i-oldnp) - 2);
			      np.Z() += newu (3 * (i-oldnp) - 1);
			      lpoints.Append (np);
                              pmap.Elem(i) = lpoints.Size()-1+PointIndex::BASE;
			    }
			  
			  // Set new Faces:
			  
			  for (int i = rule->GetNOldF() + 1; i <= rule->GetNF(); i++)
			    if (!fmapi.Get(i))
			      {
				MiniElement2d nface(rule->GetNP(i));
				for (int j = 1; j <= nface.GetNP(); j++)
				  nface.PNum(j) = pmap.Get(rule->GetPointNr (i, j));
				
				lfaces.Append (nface);
			      }
			  
			  // Delete old Faces:

			  for (int i = 1; i <= rule->GetNDelF(); i++)
			    delfaces.Append (fmapi.Get(rule->GetDelFace(i)));
			  for (int i = rule->GetNOldF()+1; i <= rule->GetNF(); i++)
			    if (fmapi.Get(i))
			      {
				delfaces.Append (fmapi.Get(i));
				fmapi.Elem(i) = 0;
			      }
			  

			  // check orientation
			  for (int i = 1; i <= rule->GetNO() && ok; i++)
			    {
			      const fourint * fouri;
			      
			      fouri = &rule->GetOrientation(i);
			      Vec3d v1 (lpoints[pmap.Get(fouri->i1)], 
					lpoints[pmap.Get(fouri->i2)]);
			      Vec3d v2 (lpoints[pmap.Get(fouri->i1)], 
					lpoints[pmap.Get(fouri->i3)]);
			      Vec3d v3 (lpoints[pmap.Get(fouri->i1)], 
					lpoints[pmap.Get(fouri->i4)]);

			      Vec3d n;
			      Cross (v1, v2, n);
			      //if (n * v3 >= -1e-7*n.Length()*v3.Length()) // OR -1e-7???
			      if (n * v3 >= -1e-9)
				{
				  if (loktestmode)
				    {
				      problems[rim] = "Orientation wrong";
				      rule_out << "Orientation wrong ("<< n*v3 << ")" << endl;
				    }
				  ok = 0;
				}
			    }

			  

			  // new points in free-zone ?
			  for (int i = rule->GetNOldP() + 1; i <= rule->GetNP() && ok; i++)
			    if (!rule->IsInFreeZone (lpoints[pmap.Get(i)]))
			      {
				if (loktestmode)
				  {
				    rule_out << "Newpoint " << lpoints[pmap.Get(i)]
					       << " outside convex hull" << endl;
				    problems[rim] = "newpoint outside convex hull";
				  }
				ok = 0;
				
			      }
			  
			  // insert new elements
			  
			  for (int i = 1; i <= rule->GetNE(); i++)
			    {
			      elements.Append (rule->GetElement(i));
			      for (int j = 1; j <= elements.Get(i).NP(); j++)
				elements.Elem(i).PNum(j) = pmap.Get(elements.Get(i).PNum(j));
			    }
			  

			  // Calculate Element badness
			  
			  teterr = 0;
			  for (auto i : elements.Range())
			    {
			      double hf = CalcElementBadness (lpoints, elements[i]);
			      if (hf > teterr) teterr = hf;
			    }

			  /*
			    // keine gute Erfahrung am 25.1.2000, js
			  if (ok && teterr < 100 &&
			      (rule->TestFlag('b') || tolerance > 10) )
			    {
			      (*mycout) << "Reset teterr " 
				   << rule->Name() 
				   << " err = " << teterr 
				   << endl;
			      teterr = 1;
			    }
			  */

			  // compare edgelength
			  if (rule->TestFlag('l'))
			    {
			      double oldlen = 0;
			      double newlen = 0;

			      for (int i = 1; i <= rule->GetNDelF(); i++)
				{
				  const Element2d & face = 
				    rule->GetFace (rule->GetDelFace(i));
				  for (int j = 1; j <= 3; j++)
				    {
				      const Point3d & p1 =
					lpoints[pmap.Get(face.PNumMod(j))];
				      const Point3d & p2 =
					lpoints[pmap.Get(face.PNumMod(j+1))];
				      oldlen += Dist(p1, p2);
				    }
				}

			      for (int i = rule->GetNOldF()+1; i <= rule->GetNF(); i++)
				{
				  const Element2d & face = rule->GetFace (i);
				  for (int j = 1; j <= 3; j++)
				    {
				      const Point3d & p1 =
					lpoints[pmap.Get(face.PNumMod(j))];
				      const Point3d & p2 =
					lpoints[pmap.Get(face.PNumMod(j+1))];
				      newlen += Dist(p1, p2);
				    }
				}

			      if (oldlen < newlen) 
				{
				  ok = 0;
				  if (loktestmode)
				    problems[rim] = "oldlen < newlen";
				}
			    }
			  

			  if (loktestmode)
			    rule_out << "ok = " << int(ok)
				       << "teterr = " << teterr 
				       << "minteterr = " << minteterr << endl;


			  if (ok && teterr < tolerance)
			    {
			      canuse[rim] ++;
			      /*
			      rule_out << "can use rule " << rule->Name()
					 << ", err = " << teterr << endl;
			      for (i = 1; i <= pmap.Size(); i++)
				rule_out << pmap.Get(i) << " ";
			      rule_out << endl;
			      */

			      if (problems[rim] == "other")
				{
				  if (teterr < local_minother)
				    local_minother = teterr;
				}
			      else
				{
				  if (teterr < local_minwithoutother)
				    local_minwithoutother = teterr;
				}
			    }


			  if (teterr > minteterr) impossible = 0;

			  if (ok && teterr < minteterr)
			    {

			      if (loktestmode)
				rule_out << "use rule" << endl;

			      found = rim+1;
			      minteterr = teterr;
			      
			      if (testmode)
				{
				  for (int i = 1; i <= rule->GetNOldP(); i++)
				    {
				      rule_out << "P" << i << ": Ref: "
						 << rule->GetPoint (i) << "  is: "
						 << lpoints[pmap.Get(i)] << endl;
				    }
				}
			      
			      tempnewpoints.SetSize (0);
			      // for (int i = noldlp+1; i <= lpoints.Size(); i++)
                              for (auto i : lpoints.Range().Modify(noldlp, 0))
				tempnewpoints.Append (lpoints[i]);
			      
			      tempnewfaces.SetSize (0);
			      // for (int i = noldlf+1; i <= lfaces.Size(); i++)
                              for (auto i : lfaces.Range().Modify(noldlf,0))
				tempnewfaces.Append (lfaces[i]);

			      tempdelfaces.SetSize (0);
			      // for (int i = 1; i <= delfaces.Size(); i++)
                              for (auto i : delfaces.Range())
				tempdelfaces.Append (delfaces[i]);
			      
			      tempelements.SetSize (0);
			      // for (int i = 1; i <= elements.Size(); i++)
                              for (auto i : elements.Range())
				tempelements.Append (elements[i]);
			    }
			  

			  lpoints.SetSize (noldlp);
			  lfaces.SetSize (noldlf);
			  delfaces.SetSize (0);
			  elements.SetSize (0);
			}
		      
		      npok = rule->GetNOldP();
		      incnpok = 0;
		    }
		}
	      
	      nfok = rule->GetNOldF();
	      
	      for (int j = 1; j <= rule->GetNP (nfok); j++)
		{
		  int refpi = rule->GetPointNr (nfok, j);
		  pused[pmap.Get(refpi)]--;
		  
		  if (pused[pmap.Get(refpi)] == 0)
                    pmap.Elem(refpi).Invalidate();
		}
	      
	    }
	}
      if (loktestmode)
	rule_out << "end rule" << endl;
    }
  // tloop.Stop();
  
  if (found)
    {
      /*
      for (i = 1; i <= tempnewpoints.Size(); i++)
	lpoints.Append (tempnewpoints.Get(i));
      */
      for (Point3d p : tempnewpoints)
        lpoints.Append(p);
      /*
      for (i = 1; i <= tempnewfaces.Size(); i++)
	if (tempnewfaces.Get(i).PNum(1))
	  lfaces.Append (tempnewfaces.Get(i));
      */
      for (int i : tempnewfaces.Range())
	if (tempnewfaces[i].PNum(1).IsValid())
	  lfaces.Append (tempnewfaces[i]);
      /*
      for (i = 1; i <= tempdelfaces.Size(); i++)
	delfaces.Append (tempdelfaces.Get(i));
      */
      for (int i : tempdelfaces.Range())
        delfaces.Append (tempdelfaces[i]);
      /*
      for (i = 1; i <= tempelements.Size(); i++)
	elements.Append (tempelements.Get(i));
      */
      for (int i : tempelements.Range())
        elements.Append (tempelements[i]);
    }
  
  retminerr = minerr;
  if (result) {
    result->quality = minteterr;
    result->other = local_minother;
    result->withoutother = local_minwithoutother;
    if(worker_trace) result->trace = worker_trace->str();
  }
  if (!queue) {
    minother = local_minother;
    minwithoutother = local_minwithoutother;
  }


  if (impossible && found == 0)
    return -1;

  return found;
}
}
