// Original candidate checks, extracted from ruler3.cpp at 5e4dd611.
// Enumeration and SetFreeZoneTransformation stay on the canonical rule.
// This helper only uses a worker-private rule and candidate workspace.
static void EvaluateOrderedMapping(vnetrule *rule,MappingCandidate &out,
    const NgArrayMem<Box3d,100> &face_boxes,int tolerance,bool initial_ok) {
  auto &lpoints=out.points;auto &lfaces=out.faces;
  auto &pused=out.used;auto &fused=out.fused;auto &pmap=out.pmap;
  auto &fmapi=out.fmapi;
  const auto &newu=out.newu;
  if(newu.size()!=3*size_t(rule->GetNP()-rule->GetNOldP()))
    throw NgException("映射候选的新点位移数量不符");
  auto &elements=out.elements;auto &delfaces=out.deleted;
  int loktestmode=tolerance>5;
  char ok=initial_ok,hc=0;
  float err=0,teterr=0,minteterr=0;
  Point3d np;
  char unused_diagnostic_buffer[256]={};
  std::ostream rule_out(nullptr);
  rule->RestoreFreeZoneState(out.zone);
if (!rule->ConvexFreeZone())
			{
			  ok = 0;
			  out.problem = "Freezone not convex";

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
					  out.problem = "locpoint " + ToString(i) + " in Freezone";
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

			      if (!face_boxes.Get(i).Intersect (rule->fzbox))
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

				      sprintf (unused_diagnostic_buffer, "triangle (%d, %d, %d) in Freezone",
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

				      out.problem = "triangle ("+ToString(lfaces[i-1].PNum(1))+", "
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

						out.problem = "other";
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
					  snprintf (unused_diagnostic_buffer, 255, "triangle (%d, %d, %d) in Freezone",
						   int (lfaces[i-1].PNum(1)), 
						   int (lfaces[i-1].PNum(2)),
						   int (lfaces[i-1].PNum(3)));
                                          */
                                          out.problem = "triangle ("
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
			  out.evaluated=true;
                          err = 0;
			  for (int i = 1; i <= rule->GetNOldP(); i++)
			    {
			      double hf = rule->CalcPointDist (i, lpoints[pmap.Get(i)]);
			      if (hf > err) err = hf;
			    }
			  
			  
			  if (loktestmode)
			    {
			      rule_out << "Rule ok" << endl;
			      out.problem = "Rule ok, err = "+ToString(err);
			    }


			  //			  newu = rule->GetOldUToNewU() * oldu;

			  // set new points:
                          int oldnp = rule->GetNOldP();
			  int noldlp = lpoints.Size();
			  int noldlf = lfaces.Size();
			  
			  for (int i = oldnp + 1; i <= rule->GetNP(); i++)
			    {
			      np = rule->GetPoint(i);
			      np.X() += newu[3 * (i-oldnp) - 3];
			      np.Y() += newu[3 * (i-oldnp) - 2];
			      np.Z() += newu[3 * (i-oldnp) - 1];
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
				      out.problem = "Orientation wrong";
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
				    out.problem = "newpoint outside convex hull";
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
				    out.problem = "oldlen < newlen";
				}
			    }
			  

			  if (loktestmode)
			    rule_out << "ok = " << int(ok)
				       << "teterr = " << teterr 
				       << "minteterr = " << minteterr << endl;


			  
  out.quality=teterr;
  } // original if(ok): candidate geometry/quality was evaluated
  out.ok=ok;
}
