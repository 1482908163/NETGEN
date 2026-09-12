/**************************************************************************/
/* File:   nglib.cpp                                                      */
/* Author: Joachim Schoeberl                                              */
/* Date:   7. May. 2000                                                   */
/**************************************************************************/

/*
  
  Interface to the netgen meshing kernel
  
*/
#include <mystdlib.h>
#include <myadt.hpp>

#include <linalg.hpp>
#include <csg.hpp>
#include <stlgeom.hpp>
#include <geometry2d.hpp>
#include <meshing.hpp>
#include <../meshing/soldata.hpp>

#include <filesystem>
#include <chrono>

#include <nginterface.h>


namespace netgen {
   extern void MeshFromSpline2D (SplineGeometry2d & geometry,
                                 shared_ptr<Mesh> & mesh, 
                                 MeshingParameters & mp);
   extern MeshingParameters mparam;
   DLL_HEADER extern STLParameters stlparam;
DLL_HEADER extern shared_ptr<Mesh> mesh;
DLL_HEADER extern shared_ptr<NetgenGeometry> ng_geometry;
}



#ifdef PARALLEL
#include <mpi.h>

#endif


/*
namespace netgen
{
  int id = 0, ntasks = 1;
}
*/


/*
// should not be needed (occ currently requires it)
namespace netgen {
#include "../libsrc/visualization/vispar.hpp"
  VisualizationParameters vispar;
  VisualizationParameters :: VisualizationParameters() { ; }
}
*/


namespace nglib {
#include "nglib.h"
}

using namespace netgen;

// constants and types:

namespace nglib
{
  inline void NOOP_Deleter(void *) { ; }

  
   // initialize, deconstruct Netgen library:
   NGLIB_API void Ng_Init ()
   {
      mycout = &cout;
      myerr = &cerr;
      // netgen::testout->SetOutStream (new ofstream ("test.out"));
      // testout = new ofstream ("test.out");
   }




   // Clean-up functions before ending usage of nglib
   NGLIB_API void Ng_Exit ()
   {
      ;
   }




   // Create a new netgen mesh object
   NGLIB_API Ng_Mesh * Ng_NewMesh ()
   {
      Mesh * mesh = new Mesh;  
      mesh->AddFaceDescriptor (FaceDescriptor (1, 1, 0, 1));
      return (Ng_Mesh*)(void*)mesh;
   }




   // Delete an existing netgen mesh object
   NGLIB_API void Ng_DeleteMesh (Ng_Mesh * mesh)
   {
      if(mesh != NULL)
      {
         // Delete the Mesh structures
         ((Mesh*)mesh)->DeleteMesh();

         // Now delete the Mesh class itself
         delete (Mesh*)mesh;

         // Set the Ng_Mesh pointer to NULL
         mesh = NULL;
      }
   }




   // Save a netgen mesh in the native VOL format 
   NGLIB_API void Ng_SaveMesh(Ng_Mesh * mesh, const char* filename)
   {
      ((Mesh*)mesh)->Save(filename);
   }




   // Load a netgen native VOL mesh from a given file
   NGLIB_API Ng_Mesh * Ng_LoadMesh(const char* filename)
   {
      Mesh * mesh = new Mesh;
      mesh->Load(filename);
      return ( (Ng_Mesh*)mesh );
   }




   // Merge another mesh file into the currently loaded one
   NGLIB_API Ng_Result Ng_MergeMesh( Ng_Mesh* mesh, const char* filename)
   {
      Ng_Result status = NG_OK;

      ifstream infile(filename);
      Mesh * m = (Mesh*)mesh;

      if(!infile.good())
      {
         status = NG_FILE_NOT_FOUND;
      }

      if(!m)
      {
         status = NG_ERROR;
      }

      if(status == NG_OK)
      {
         const int num_pts = m->GetNP();
         const int face_offset = m->GetNFD();

         m->Merge(infile, face_offset);

         if(m->GetNP() > num_pts)
         {
            status = NG_OK;
         }
         else
         {
            status = NG_ERROR;
         }
      }

      return status;
   }




   // Merge another mesh file into the currently loaded one
   NGLIB_API Ng_Result Ng_MergeMesh( Ng_Mesh* mesh1, Ng_Mesh* mesh2)
   {
        Mesh *m1 = (Mesh*)mesh1;
        Mesh *m2 = (Mesh*)mesh2;
      int offset = Ng_GetNP(mesh1);
      int np2 = Ng_GetNP(mesh2);
      for(int i = 1; i <= np2; i++)
      {
        double x[3];
        Ng_GetPoint(mesh2,i,x);
        Ng_AddPoint(mesh1,x);
      }
      int nse = Ng_GetNSE(mesh2);
      for(int i = 1; i <= nse; i++)
      {
        
        Element2d & el = ((Mesh*)mesh2)->SurfaceElement(i);
        el.PNum(1) += offset;
        el.PNum(2) += offset;
        el.PNum(3) += offset;
        m1->AddSurfaceElement(el);
      }

      int ne = Ng_GetNE(mesh2);
      for(int i = 1; i <= ne; i++)
      {
        Element & el = ((Mesh*)mesh2)->VolumeElement(i);
        el.PNum(1) += offset;
        el.PNum(2) += offset;
	     el.PNum(3) += offset;
        el.PNum(4) += offset;
        m1->AddVolumeElement (el);
      }

      return NG_OK;
   }




   // Manually add a point to an existing mesh object
   NGLIB_API void Ng_AddPoint (Ng_Mesh * mesh, double * x)
   {
      Mesh * m = (Mesh*)mesh;
      m->AddPoint (Point3d (x[0], x[1], x[2]));
   }




   // Manually add a surface element of a given type to an existing mesh object
   NGLIB_API void Ng_AddSurfaceElement (Ng_Mesh * mesh, Ng_Surface_Element_Type et,
                                         int * pi)
   {
      Mesh * m = (Mesh*)mesh;
      Element2d el (3);
      el.SetIndex (1);
      el.PNum(1) = pi[0];
      el.PNum(2) = pi[1];
      el.PNum(3) = pi[2];
      m->AddSurfaceElement (el);
   }




   // Manually add a volume element of a given type to an existing mesh object
   NGLIB_API void Ng_AddVolumeElement (Ng_Mesh * mesh, Ng_Volume_Element_Type et,
                                        int * pi)
   {
      Mesh * m = (Mesh*)mesh;
      Element el (4);
      el.SetIndex (1);
      el.PNum(1) = pi[0];
      el.PNum(2) = pi[1];
      el.PNum(3) = pi[2];
      el.PNum(4) = pi[3];
      m->AddVolumeElement (el);
   }




   // Obtain the number of points in the mesh
   NGLIB_API int Ng_GetNP (Ng_Mesh * mesh)
   {
      return ((Mesh*)mesh) -> GetNP();
   }




   // Obtain the number of surface elements in the mesh
   NGLIB_API int Ng_GetNSE (Ng_Mesh * mesh)
   {
      return ((Mesh*)mesh) -> GetNSE();
   }




   // Obtain the number of volume elements in the mesh
   NGLIB_API int Ng_GetNE (Ng_Mesh * mesh)
   {
      return ((Mesh*)mesh) -> GetNE();
   }




   //  Return point coordinates of a given point index in the mesh
   NGLIB_API void Ng_GetPoint (Ng_Mesh * mesh, int num, double * x)
   {
      const Point3d & p = ((Mesh*)mesh)->Point(num);
      x[0] = p.X();
      x[1] = p.Y();
      x[2] = p.Z();
   }




   // Return the surface element at a given index "pi"
   NGLIB_API Ng_Surface_Element_Type 
      Ng_GetSurfaceElement (Ng_Mesh * mesh, int num, int * pi)
   {
      const Element2d & el = ((Mesh*)mesh)->SurfaceElement(num);
      for (int i = 1; i <= el.GetNP(); i++)
         pi[i-1] = el.PNum(i);
      Ng_Surface_Element_Type et;
      switch (el.GetNP())
      {
      case 3: et = NG_TRIG; break;
      case 4: et = NG_QUAD; break;
      case 6: 
         switch (el.GetNV())
         {
         case 3: et = NG_TRIG6; break;
         case 4: et = NG_QUAD6; break;
         default:
            et = NG_TRIG6; break;
         }
         break;
      case 8: et = NG_QUAD8; break;
      default:
         et = NG_TRIG; break; // for the compiler
      }
      return et;
   }




   // Return the volume element at a given index "pi"
   NGLIB_API Ng_Volume_Element_Type
      Ng_GetVolumeElement (Ng_Mesh * mesh, int num, int * pi)
   {
      const Element & el = ((Mesh*)mesh)->VolumeElement(num);
      for (int i = 1; i <= el.GetNP(); i++)
         pi[i-1] = el.PNum(i);
      Ng_Volume_Element_Type et;
      switch (el.GetNP())
      {
      case 4: et = NG_TET; break;
      case 5: et = NG_PYRAMID; break;
      case 6: et = NG_PRISM; break;
      case 10: et = NG_TET10; break;
      default:
         et = NG_TET; break; // for the compiler
      }
      return et;
   }




   // Set a global limit on the maximum mesh size allowed
   NGLIB_API void Ng_RestrictMeshSizeGlobal (Ng_Mesh * mesh, double h)
   {
      ((Mesh*)mesh) -> SetGlobalH (h);
   }




   // Set a local limit on the maximum mesh size allowed around the given point
   NGLIB_API void Ng_RestrictMeshSizePoint (Ng_Mesh * mesh, double * p, double h)
   {
      ((Mesh*)mesh) -> RestrictLocalH (Point3d (p[0], p[1], p[2]), h);
   }




   // Set a local limit on the maximum mesh size allowed within a given box region
   NGLIB_API void Ng_RestrictMeshSizeBox (Ng_Mesh * mesh, double * pmin, double * pmax, double h)
   {
      for (double x = pmin[0]; x < pmax[0]; x += h)
         for (double y = pmin[1]; y < pmax[1]; y += h)
            for (double z = pmin[2]; z < pmax[2]; z += h)
               ((Mesh*)mesh) -> RestrictLocalH (Point3d (x, y, z), h);
   }




   // Generates volume mesh from an existing surface mesh
   NGLIB_API Ng_Result Ng_GenerateVolumeMesh (Ng_Mesh * mesh, Ng_Meshing_Parameters * mp)
   {
      Mesh * m = (Mesh*)mesh;

      // Philippose - 30/08/2009
      // Do not locally re-define "mparam" here... "mparam" is a global 
      // object 
      //MeshingParameters mparam;
      mp->Transfer_Parameters();

      m->CalcLocalH(mparam.grading);

      MeshVolume (mparam, *m);
      RemoveIllegalElements (*m);
      OptimizeVolume (mparam, *m);

      return NG_OK;
   }




   static Ng_Result GenerateVolumeKernelImpl (Ng_Mesh * mesh,
       Ng_Meshing_Parameters * mp, int threads, int schedule, double * seconds, double * details,
       const VolumeResources * resources=nullptr)
   {
      if (!mesh || !mp || !seconds || threads<1 || (schedule<0 || schedule>3))
         return NG_ERROR;
      std::fill_n(seconds,3,0.0);
      VolumeKernelStats stats;
      if(details) std::fill_n(details,VolumeKernelStats::count,0.0);
      try {
         // The public parameter transfer uses a global; restore it on exit.
         // Like the existing nglib API, this entry must be called by one host
         // thread per process. Worker tasks are created inside the kernel.
         struct Restore {
            MeshingParameters saved = mparam;
            ~Restore() { mparam = std::move(saved); }
         } restore;
         mp->Transfer_Parameters();
         MeshingParameters local = mparam;
         local.parallel_meshing = threads>1;
         local.nthreads = threads;
         local.volume_candidate_schedule = schedule==1;
         local.volume_parallel_repair = schedule>=2;
         local.volume_repair_frontier = schedule==3;
         local.volume_kernel_stats = details ? &stats : nullptr;
         local.volume_resources = resources;
         Mesh & m = *reinterpret_cast<Mesh*>(mesh);
         m.CalcLocalH(local.grading);
         auto measure = [&](int phase, auto fn) {
            auto start=std::chrono::steady_clock::now();
            auto result=fn();
            seconds[phase]=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
            return result;
         };
         if (measure(0,[&] { return MeshVolume(local,m); })!=MESHING3_OK)
            return NG_VOLUME_FAILURE;
         measure(1,[&] { RemoveIllegalElements(m,local); return 0; });
         if (measure(2,[&] { return OptimizeVolume(local,m); })!=MESHING3_OK)
            return NG_VOLUME_FAILURE;
         if(details) {
            stats.Add(11,m.MarkIllegalElements());
            for(int i=0;i<VolumeKernelStats::count;++i) details[i]=stats.values[i].load();
         }
         return NG_OK;
      } catch (const std::exception & error) {
         std::cerr << "Volume kernel failed: " << error.what() << std::endl;
         return NG_VOLUME_FAILURE;
      }
   }

   NGLIB_API Ng_Result Ng_GenerateVolumeMeshKernel(Ng_Mesh * mesh,
       Ng_Meshing_Parameters * mp, int threads, int schedule, double * seconds)
   {
     if(schedule<0 || schedule>1) return NG_ERROR;
     return GenerateVolumeKernelImpl(mesh,mp,threads,schedule,seconds,nullptr);
   }

   NGLIB_API Ng_Result Ng_GenerateVolumeMeshRepair(Ng_Mesh * mesh,
       Ng_Meshing_Parameters * mp, int threads, int schedule, double * seconds, double * details)
   {
     if(!details) return NG_ERROR;
     return GenerateVolumeKernelImpl(mesh,mp,threads,schedule,seconds,details);
   }

   NGLIB_API Ng_Result Ng_GenerateVolumeMeshCooperative(Ng_Mesh * mesh,
       Ng_Meshing_Parameters * mp, int threads, const Ng_VolumeResources * resources,
       double * seconds, double * details)
   {
     if(!resources || !resources->acquire || !resources->release || !details) return NG_ERROR;
     VolumeResources adapter{resources->context,resources->acquire,resources->release};
     return GenerateVolumeKernelImpl(mesh,mp,threads,2,seconds,details,&adapter);
   }

   NGLIB_API Ng_Result Ng_GenerateVolumeMeshCooperativeGrouped(Ng_Mesh * mesh,
       Ng_Meshing_Parameters * mp, int threads, const Ng_VolumeResources * resources,
       double * seconds, double * details)
   {
     if(!resources || !resources->acquire || !resources->release || !details) return NG_ERROR;
     VolumeResources adapter{resources->context,resources->acquire,resources->release};
     adapter.grouped_repair=true;
     return GenerateVolumeKernelImpl(mesh,mp,threads,2,seconds,details,&adapter);
   }

   NGLIB_API void Ng_GetVolumeTaskManagerStats(double * values)
   {
     if(!values) return;
     const auto stats=ngcore::GetTaskManagerLifecycleStats();
     values[0]=stats.starts;values[1]=stats.startup_seconds;values[2]=stats.shutdown_seconds;
   }

   /* ------------------ 2D Meshing Functions ------------------------- */
   NGLIB_API void Ng_AddPoint_2D (Ng_Mesh * mesh, double * x)
   {
      Mesh * m = (Mesh*)mesh;

      m->AddPoint (Point3d (x[0], x[1], 0));
   }




   NGLIB_API void Ng_AddBoundarySeg_2D (Ng_Mesh * mesh, int pi1, int pi2)
   {
      Mesh * m = (Mesh*)mesh;

      Segment seg;
      seg[0] = pi1;
      seg[1] = pi2;
      m->AddSegment (seg);
   }




   NGLIB_API int Ng_GetNP_2D (Ng_Mesh * mesh)
   {
      Mesh * m = (Mesh*)mesh;
      return m->GetNP();
   }




   NGLIB_API int Ng_GetNE_2D (Ng_Mesh * mesh)
   {
      Mesh * m = (Mesh*)mesh;
      return m->GetNSE();
   }




   NGLIB_API int Ng_GetNSeg_2D (Ng_Mesh * mesh)
   {
      Mesh * m = (Mesh*)mesh;
      return m->GetNSeg();
   }




   NGLIB_API void Ng_GetPoint_2D (Ng_Mesh * mesh, int num, double * x)
   {
      Mesh * m = (Mesh*)mesh;

      Point<3> & p = m->Point(num);
      x[0] = p(0);
      x[1] = p(1);
   }




   NGLIB_API Ng_Surface_Element_Type
      Ng_GetElement_2D (Ng_Mesh * mesh, int num, int * pi, int * matnum)
   {
      const Element2d & el = ((Mesh*)mesh)->SurfaceElement(num);
      for (int i = 1; i <= el.GetNP(); i++)
         pi[i-1] = el.PNum(i);

      Ng_Surface_Element_Type et;
      switch (el.GetNP())
      {
      case 3: et = NG_TRIG; break;
      case 4: et = NG_QUAD; break;
      case 6: 
         switch (el.GetNV())
         {
         case 3: et = NG_TRIG6; break;
         case 4: et = NG_QUAD6; break;
         default:
            et = NG_TRIG6; break;
         }
         break;
      case 8: et = NG_QUAD8; break;
      default:
         et = NG_TRIG; break; // for the compiler
      }

      if (matnum)
         *matnum = el.GetIndex();

      return et;
   }




   NGLIB_API void Ng_GetSegment_2D (Ng_Mesh * mesh, int num, int * pi, int * matnum)
   {
      const Segment & seg = ((Mesh*)mesh)->LineSegment(num);
      pi[0] = seg[0];
      pi[1] = seg[1];

      if (matnum)
         *matnum = seg.edgenr;
   }




   NGLIB_API Ng_Geometry_2D * Ng_LoadGeometry_2D (const char * filename)
   {
      SplineGeometry2d * geom = new SplineGeometry2d();
      geom -> Load (filename);
      return (Ng_Geometry_2D *)geom;
   }


   NGLIB_API Ng_Result Ng_GenerateMesh_2D (Ng_Geometry_2D * geom,
                                            Ng_Mesh ** mesh,
                                            Ng_Meshing_Parameters * mp)
   {
      // use global variable mparam
      //  MeshingParameters mparam;  
      mp->Transfer_Parameters();

      shared_ptr<Mesh> m(new Mesh, &NOOP_Deleter);
      MeshFromSpline2D (*(SplineGeometry2d*)geom, m, mparam);
      // new shared_ptr<Mesh> (m);  // hack to keep mesh m alive 
      
      cout << m->GetNSE() << " elements, " << m->GetNP() << " points" << endl;

      *mesh = (Ng_Mesh*)m.get();
      return NG_OK;
   }




   NGLIB_API void Ng_HP_Refinement (Ng_Geometry_2D * geom,
      Ng_Mesh * mesh,
      int levels)
   {
      Refinement ref(*(SplineGeometry2d*)geom);
      HPRefinement (*(Mesh*)mesh, &ref, levels);
   }




   NGLIB_API void Ng_HP_Refinement (Ng_Geometry_2D * geom,
      Ng_Mesh * mesh,
      int levels, double parameter)
   {
      Refinement ref(*(SplineGeometry2d*)geom);
      HPRefinement (*(Mesh*)mesh, &ref, levels, parameter);
   }




   NgArray<STLReadTriangle> readtrias; //only before initstlgeometry
   NgArray<Point<3> > readedges; //only before init stlgeometry

   // loads geometry from STL file
   NGLIB_API Ng_STL_Geometry * Ng_STL_LoadGeometry (const char * filename, int binary)
   {
      int i;
      STLGeometry geom;
      STLGeometry* geo;
      ifstream ist(filename);

      if (binary)
      {
         geo = geom.LoadBinary(ist);
      }
      else
      {
         geo = geom.Load(ist);
      }

      readtrias.SetSize(0);
      readedges.SetSize(0);

      Point3d p;
      Vec3d normal;
      double p1[3];
      double p2[3];
      double p3[3];
      double n[3];

      Ng_STL_Geometry * geo2 = Ng_STL_NewGeometry();

      for (i = 1; i <= geo->GetNT(); i++)
      {
         const STLTriangle& t = geo->GetTriangle(i);
         p = geo->GetPoint(t.PNum(1));
         p1[0] = p.X(); p1[1] = p.Y(); p1[2] = p.Z(); 
         p = geo->GetPoint(t.PNum(2));
         p2[0] = p.X(); p2[1] = p.Y(); p2[2] = p.Z(); 
         p = geo->GetPoint(t.PNum(3));
         p3[0] = p.X(); p3[1] = p.Y(); p3[2] = p.Z();
         normal = t.Normal();
         n[0] = normal.X(); n[1] = normal.Y(); n[2] = normal.Z();

         Ng_STL_AddTriangle(geo2, p1, p2, p3, n);
      }

      return geo2;
   }




   // generate new STL Geometry
   NGLIB_API Ng_STL_Geometry * Ng_STL_NewGeometry ()
   {
      return (Ng_STL_Geometry*)(void*)new STLGeometry;
   } 




   // after adding triangles (and edges) initialize
   NGLIB_API Ng_Result Ng_STL_InitSTLGeometry (Ng_STL_Geometry * geom)
   {
      STLGeometry* geo = (STLGeometry*)geom;
      geo->InitSTLGeometry(readtrias);
      readtrias.SetSize(0);

      if (readedges.Size() != 0)
      {
         /*
         for (int i = 1; i <= readedges.Size(); i+=2)
         {
         cout << "e(" << readedges.Get(i) << "," << readedges.Get(i+1) << ")" << endl;
         }
         */
         geo->AddEdges(readedges);
      }

      if (geo->GetStatus() == STLTopology::STL_GOOD || geo->GetStatus() == STLTopology::STL_WARNING) return NG_OK;
      return NG_SURFACE_INPUT_ERROR;
   }




   // automatically generates edges:
   NGLIB_API Ng_Result Ng_STL_MakeEdges (Ng_STL_Geometry * geom,
                                          Ng_Mesh* mesh,
                                          Ng_Meshing_Parameters * mp)
   {
      STLGeometry* stlgeometry = (STLGeometry*)geom;
      Mesh* me = (Mesh*)mesh;
      me->SetGeometry( shared_ptr<NetgenGeometry>(stlgeometry, &NOOP_Deleter) );

      // Philippose - 27/07/2009
      // Do not locally re-define "mparam" here... "mparam" is a global 
      // object 
      //MeshingParameters mparam;
      mp->Transfer_Parameters();

      me -> SetGlobalH (mparam.maxh);
      me -> SetLocalH (stlgeometry->GetBoundingBox().PMin() - Vec3d(10, 10, 10),
                       stlgeometry->GetBoundingBox().PMax() + Vec3d(10, 10, 10),
                       0.3);

      // cout << "meshsize = " << mp->meshsize_filename << endl;
      if (mp->meshsize_filename)
        me -> LoadLocalMeshSize (mp->meshsize_filename);

      /*
      if (mp->meshsize_filename)
        {
          ifstream infile (mp->meshsize_filename);
          if (!infile.good()) return NG_FILE_NOT_FOUND;
          me -> LoadLocalMeshSize (infile);
        }
      */

      STLMeshing (*stlgeometry, *me, mparam, stlparam);

      stlgeometry->edgesfound = 1;
      stlgeometry->surfacemeshed = 0;
      stlgeometry->surfaceoptimized = 0;
      stlgeometry->volumemeshed = 0;

      return NG_OK;
   }




   // generates mesh, empty mesh be already created.
   NGLIB_API Ng_Result Ng_STL_GenerateSurfaceMesh (Ng_STL_Geometry * geom,
                                                    Ng_Mesh* mesh,
                                                    Ng_Meshing_Parameters * mp)
   {
      STLGeometry* stlgeometry = (STLGeometry*)geom;
      Mesh* me = (Mesh*)mesh;
      me->SetGeometry( shared_ptr<NetgenGeometry>(stlgeometry, &NOOP_Deleter) );

      // Philippose - 27/07/2009
      // Do not locally re-define "mparam" here... "mparam" is a global 
      // object
      //MeshingParameters mparam;
      mp->Transfer_Parameters();


      /*
      me -> SetGlobalH (mparam.maxh);
      me -> SetLocalH (stlgeometry->GetBoundingBox().PMin() - Vec3d(10, 10, 10),
      stlgeometry->GetBoundingBox().PMax() + Vec3d(10, 10, 10),
      0.3);
      */
      /*
      STLMeshing (*stlgeometry, *me);

      stlgeometry->edgesfound = 1;
      stlgeometry->surfacemeshed = 0;
      stlgeometry->surfaceoptimized = 0;
      stlgeometry->volumemeshed = 0;
      */  
      int retval = STLSurfaceMeshing (*stlgeometry, *me, mparam, stlparam);
      if (retval == MESHING3_OK)
      {
         (*mycout) << "Success !!!!" << endl;
         stlgeometry->surfacemeshed = 1;
         stlgeometry->surfaceoptimized = 0;
         stlgeometry->volumemeshed = 0;
      } 
      else if (retval == MESHING3_OUTERSTEPSEXCEEDED)
      {
         (*mycout) << "ERROR: Give up because of too many trials. Meshing aborted!" << endl;
      }
      else if (retval == MESHING3_TERMINATE)
      {
         (*mycout) << "Meshing Stopped!" << endl;
      }
      else
      {
         (*mycout) << "ERROR: Surface meshing not successful. Meshing aborted!" << endl;
      }


      STLSurfaceOptimization (*stlgeometry, *me, mparam);

      return NG_OK;
   }




   // fills STL Geometry
   // positive orientation
   // normal vector may be null-pointer
   NGLIB_API void Ng_STL_AddTriangle (Ng_STL_Geometry * geom, 
                                       double * p1, double * p2, double * p3, 
                                       double * nv)
   {
      Point<3> apts[3];
      apts[0] = Point<3>(p1[0],p1[1],p1[2]);
      apts[1] = Point<3>(p2[0],p2[1],p2[2]);
      apts[2] = Point<3>(p3[0],p3[1],p3[2]);

      Vec<3> n;
      if (!nv)
         n = Cross (apts[0]-apts[1], apts[0]-apts[2]);
      else
         n = Vec<3>(nv[0],nv[1],nv[2]);

      readtrias.Append(STLReadTriangle(apts,n));
   }

   // add (optional) edges:
   NGLIB_API void Ng_STL_AddEdge (Ng_STL_Geometry * geom, 
      double * p1, double * p2)
   {
      readedges.Append(Point3d(p1[0],p1[1],p1[2]));
      readedges.Append(Point3d(p2[0],p2[1],p2[2]));
   }








   // ------------------ Begin - Meshing Parameters related functions ------------------
   // Constructor for the local nglib meshing parameters class
   NGLIB_API Ng_Meshing_Parameters :: Ng_Meshing_Parameters()
   {
      uselocalh = 1;

      maxh = 1000;
      minh = 0.0;

      fineness = 0.5;
      grading = 0.3;

      elementsperedge = 2.0;
      elementspercurve = 2.0;

      closeedgeenable = 0;
      closeedgefact = 2.0;

	  minedgelenenable = 0;
	  minedgelen = 1e-4;

      second_order = 0;
      quad_dominated = 0;

      meshsize_filename = 0;

      optsurfmeshenable = 1;
      optvolmeshenable = 1;

      optsteps_2d = 3;
      optsteps_3d = 3;

      invert_tets = 0;
      invert_trigs = 0;

      check_overlap = 1;
      check_overlapping_boundary = 1;
   }




   // Reset the local meshing parameters to the default values
   NGLIB_API void Ng_Meshing_Parameters :: Reset_Parameters()
   {
      uselocalh = 1;

      maxh = 1000;
      minh = 0;

      fineness = 0.5;
      grading = 0.3;

      elementsperedge = 2.0;
      elementspercurve = 2.0;

      closeedgeenable = 0;
      closeedgefact = 2.0;

  	  minedgelenenable = 0;
	  minedgelen = 1e-4;

      second_order = 0;
      quad_dominated = 0;

      meshsize_filename = 0;

      optsurfmeshenable = 1;
      optvolmeshenable = 1;

      optsteps_2d = 3;
      optsteps_3d = 3;

      invert_tets = 0;
      invert_trigs = 0;

      check_overlap = 1;
      check_overlapping_boundary = 1;
   }




   // 
   NGLIB_API void Ng_Meshing_Parameters :: Transfer_Parameters()
   {
      mparam.uselocalh = uselocalh;
      
      mparam.maxh = maxh;
      mparam.minh = minh;

      mparam.grading = grading;
      mparam.curvaturesafety = elementspercurve;
      mparam.segmentsperedge = elementsperedge;

      mparam.secondorder = second_order;
      mparam.quad = quad_dominated;

      if (meshsize_filename)
        mparam.meshsizefilename = meshsize_filename;
      else
        mparam.meshsizefilename = "";
      mparam.optsteps2d = optsteps_2d;
      mparam.optsteps3d = optsteps_3d;

      mparam.inverttets = invert_tets;
      mparam.inverttrigs = invert_trigs;

      mparam.checkoverlap = check_overlap;
      mparam.checkoverlappingboundary = check_overlapping_boundary;
   }
   // ------------------ End - Meshing Parameters related functions --------------------




   // ------------------ Begin - Second Order Mesh generation functions ----------------
   NGLIB_API void Ng_Generate_SecondOrder(Ng_Mesh * mesh)
   {
     Refinement ref(*((Mesh*) mesh)->GetGeometry());
      ref.MakeSecondOrder(*(Mesh*) mesh);
   }




   NGLIB_API void Ng_2D_Generate_SecondOrder(Ng_Geometry_2D * geom,
					  Ng_Mesh * mesh)
   {
      ( (SplineGeometry2d*)geom ) -> GetRefinement().MakeSecondOrder( * (Mesh*) mesh );
   }




   NGLIB_API void Ng_STL_Generate_SecondOrder(Ng_STL_Geometry * geom,
					   Ng_Mesh * mesh)
   {
      ((STLGeometry*)geom)->GetRefinement().MakeSecondOrder(*(Mesh*) mesh);
   }




   NGLIB_API void Ng_CSG_Generate_SecondOrder (Ng_CSG_Geometry * geom,
					   Ng_Mesh * mesh)
   {
      ((CSGeometry*)geom)->GetRefinement().MakeSecondOrder(*(Mesh*) mesh);
   }




   // ------------------ End - Second Order Mesh generation functions ------------------




   // ------------------ Begin - Uniform Mesh Refinement functions ---------------------
   NGLIB_API void Ng_Uniform_Refinement (Ng_Mesh * mesh)
   {
     Refinement ref(*((Mesh*)mesh)->GetGeometry());
     ref.Refine ( * (Mesh*) mesh );
   }




   NGLIB_API void Ng_2D_Uniform_Refinement (Ng_Geometry_2D * geom,
      Ng_Mesh * mesh)
   {
      ( (SplineGeometry2d*)geom ) -> GetRefinement().Refine ( * (Mesh*) mesh );
   }




   NGLIB_API void Ng_STL_Uniform_Refinement (Ng_STL_Geometry * geom,
      Ng_Mesh * mesh)
   {
      ( (STLGeometry*)geom ) -> GetRefinement().Refine ( * (Mesh*) mesh );
   }




   NGLIB_API void Ng_CSG_Uniform_Refinement (Ng_CSG_Geometry * geom,
      Ng_Mesh * mesh)
   {
      ( (CSGeometry*)geom ) -> GetRefinement().Refine ( * (Mesh*) mesh );
   }




   // ------------------ End - Uniform Mesh Refinement functions -----------------------

// Return the volume element at a given index "pi"

NGLIB_API void My_Ng_GetFaceDescriptor(Ng_Mesh * mesh, int num, int* x) {
    const FaceDescriptor & fd = ((Mesh*)mesh)->GetFaceDescriptor(num);
    //cout << fd.SurfNr() << ";" << fd.DomainIn() << ";" << fd.DomainOut() << ";" << fd.TLOSurface() << endl;
    x[0] = fd.SurfNr();
    x[1] = fd.DomainIn();
    x[2] = fd.DomainOut();
    x[3] = fd.TLOSurface();
}

NGLIB_API int My_Ng_AddFaceDescriptor(Ng_Mesh * mesh, int surfnr, int domin, int domout, int tlosurf) {
    return ((Mesh*)mesh)->AddFaceDescriptor(FaceDescriptor(surfnr, domin, domout, tlosurf));
}

NGLIB_API Ng_Volume_Element_Type
Ng_GetVolumeElement (Ng_Mesh * mesh, int num, int * pi, int &domainidx) {
    const Element & el = ((Mesh*)mesh)->VolumeElement(num);
    domainidx = el.GetIndex();
    for (int i = 1; i <= el.GetNP(); i++)
        pi[i - 1] = el.PNum(i);
    Ng_Volume_Element_Type et;
    switch (el.GetNP()) {
    case 4:
        et = NG_TET;
        break;
    case 5:
        et = NG_PYRAMID;
        break;
    case 6:
        et = NG_PRISM;
        break;
    case 10:
        et = NG_TET10;
        break;
    default:
        et = NG_TET;
        break; // for the compiler
    }
    return et;
}

NGLIB_API void Ng_AddSurfaceElementwithIndex(Ng_Mesh * mesh, Ng_Surface_Element_Type et, int * pi, int index) {
    Mesh * m = (Mesh*)mesh;
    Element2d el(3);
    el.SetIndex(index);
    el.PNum(1) = pi[0];
    el.PNum(2) = pi[1];
    el.PNum(3) = pi[2];
    m->AddSurfaceElement(el);
}

// Return the surface element at a given index "pi"
NGLIB_API Ng_Surface_Element_Type
Ng_GetSurfaceElement (Ng_Mesh * mesh, int num, int * pi, int &surfidx) {
    const Element2d & el = ((Mesh*)mesh)->SurfaceElement(num);
    surfidx = el.GetIndex();
    for (int i = 1; i <= el.GetNP(); i++)
        pi[i - 1] = el.PNum(i);
    Ng_Surface_Element_Type et;
    switch (el.GetNP()) {
    case 3:
        et = NG_TRIG;
        break;
    case 4:
        et = NG_QUAD;
        break;
    case 6:
        switch (el.GetNV()) {
        case 3:
            et = NG_TRIG6;
            break;
        case 4:
            et = NG_QUAD6;
            break;
        default:
            et = NG_TRIG6;
            break;
        }
        break;
    case 8:
        et = NG_QUAD8;
        break;
    default:
        et = NG_TRIG;
        break; // for the compiler
    }
    return et;
}

// Manually add a volume element of a given type to an existing mesh object
NGLIB_API void Ng_AddVolumeElement (Ng_Mesh * mesh, Ng_Volume_Element_Type et,
                                    int * pi, int index) {
    Mesh * m = (Mesh*)mesh;
    Element el (4);
    el.SetIndex (index);
    el.PNum(1) = pi[0];
    el.PNum(2) = pi[1];
    el.PNum(3) = pi[2];
    el.PNum(4) = pi[3];
    m->AddVolumeElement (el);
}

NGLIB_API void Ng_AddPoint (Ng_Mesh * mesh, double * x, int &idx) {
    Mesh * m = (Mesh*)mesh;
    PointIndex pIdx = m->AddPoint (Point3d (x[0], x[1], x[2]));
    idx = pIdx;
}

NGLIB_API int My_Ng_GetElement_Faces(Ng_Mesh *mesh_, int elnr, int * faces, int * orient, bool update) {
    Mesh* mesh = (Mesh*)mesh_;
    if (update)
        mesh->UpdateTopology();
    const MeshTopology & topology = mesh->GetTopology();
    if (mesh->GetDimension() == 3)
        return topology.GetElementFaces(elnr, faces, orient);
    else {
        faces[0] = elnr;
        if (orient) orient[0] = 0;
        return 1;
    }
}

NGLIB_API void My_Ng_GetSurface2VolumeElements(Ng_Mesh *mesh_, int selnr, int & elnr1, int & elnr2, int update) {
    Mesh*mesh = (Mesh*)mesh_;
    if (update)
        mesh->UpdateTopology();
    const MeshTopology &topology1 = mesh->GetTopology();
    if (mesh->GetDimension() == 3)
        topology1.GetSurface2VolumeElement(selnr, elnr1, elnr2);
}


NGLIB_API int My_Ng_GetFace_Vertices(Ng_Mesh *mesh_, int fnr, int * vert) {
    Mesh* mesh = (Mesh*)mesh_;
    MeshTopology & topology = mesh->GetTopology();
    
    NgArray<int>ia;
    //ArrayMem<int, 4> ia;
    //topology.GetPartitionFaceVertices(fnr, ia);
    topology.GetFaceVertices(fnr, ia);
    for (int i = 0; i < ia.Size(); i++)
    {
         if(i==3){
           break;
         }
         vert[i] = ia[i];

    }
        
    return ia.Size();
}
NGLIB_API void My_Ng_SetVolumeElement(Ng_Mesh * mesh, int index, int *pi, int ind) {
    Mesh * m = (Mesh*)mesh;
    Element nel(TET);
    for (int k = 1; k <= 4; k++)
        nel.PNum(k) = pi[k - 1];
    nel.SetIndex(ind);
    m->VolumeElement(index) = nel;
}

NGLIB_API void My_Ng_SetVolumeElementTet10(Ng_Mesh * mesh, int index, int *pi, int ind) {
    Mesh * m = (Mesh*)mesh;
    Element nel(TET10);
    for (int k = 1; k <= 10; k++)
        nel.PNum(k) = pi[k - 1];
    nel.SetIndex(ind);
    m->VolumeElement(index) = nel;
}
NGLIB_API void My_Ng_SetSurElement(Ng_Mesh * mesh, int index, int *pi, int ind) {
    Mesh * m = (Mesh*)mesh;
    Element2d nel(TRIG);
    for (int k = 1; k <= 3; k++)
        nel.PNum(k) = pi[k - 1];
    nel.SetIndex(ind);
    //m->SurfaceElement(index) = nel;
    m->SetSurfaceElement(index, nel);
}

NGLIB_API void ClearSurfaceElements(Ng_Mesh * mesh) {
    Mesh * m = (Mesh*)mesh;
    m->ClearSurfaceElements();
}
NGLIB_API void My_Ng_DelSurElement(Ng_Mesh * mesh, int index) {
    Mesh * m = (Mesh*)mesh;
    m->Delete(index);
}
NGLIB_API void My_Ng_orientation(Ng_Mesh *mesh_) {
    Mesh*mesh = (Mesh*)mesh_;
    mesh->SurfaceMeshOrientation();
}


NGLIB_API int GetBoundaryID(Ng_Mesh *mesh, int id) {
    const Element2d & el = ((Mesh *)mesh)->SurfaceElement(id);
    return ((Mesh *)mesh)->GetFaceDescriptor(el.GetIndex()).SurfNr();
}

NGLIB_API int Ng_GetNFD(Ng_Mesh * mesh) {
    return ((Mesh*)mesh)->GetNFD();
}

/* 
 * brief  按照边界删除面元素
 * param  mesh_ : 输入的网格
 * param  BoundaryID : 边界ID
 * return 无
 */
NGLIB_API void My_Delete_SurfaceMesh(Ng_Mesh *mesh_, int BoundaryID) {
    Mesh *mesh = (Mesh*)mesh_;
    int nse = mesh->GetNSE();
    cout << nse << endl;
    for(SurfaceElementIndex sei = 0;sei < nse; sei++) {
        // Element2d  surface = mesh->SurfaceElement(i);
        if(mesh->GetFaceDescriptor(mesh->SurfaceElement(sei).GetIndex ()).BCProperty() == BoundaryID) {
            // cout << "sei:" << sei << endl;
            // cout << "surnf:" << mesh->GetFaceDescriptor(mesh->SurfaceElement(sei).GetIndex ()).SurfNr() << endl;
            mesh->Delete(sei);
        }
        // if()
        // cout << surface.GetIndex()<<endl;
    }
    // cout << nse << endl;
}
/* 
 * brief  删除最后一个面描述
 * param  mesh_ : 输入的网格
 * return 无
 */
NGLIB_API void My_Delete_Last_SurfaceDescriptor(Ng_Mesh *mesh_)
{
    Mesh *mesh = (Mesh*)mesh_;
    int nse = mesh->GetNSE();
    int nfd = mesh->GetNFD();
cout << nfd << endl;
    for(SurfaceElementIndex sei = 0;sei < nse; sei++) {
        // Element2d  surface = mesh->SurfaceElement(i);
        if(mesh->GetFaceDescriptor(mesh->SurfaceElement(sei).GetIndex ()).BCProperty() == nfd) {
            // cout << "sei:" << sei << endl;
            // cout << "surnf:" << mesh->GetFaceDescriptor(mesh->SurfaceElement(sei).GetIndex ()).SurfNr() << endl;
            mesh->Delete(sei);
        }
        // if()
        // cout << surface.GetIndex()<<endl;
    }
}

bool ispatbound(Ng_Mesh * mesh_,int j){
    Mesh *mesh = (Mesh*)mesh_;
    int nfd = mesh->GetNFD();
    SurfaceElementIndex sei = j;
    if(mesh->GetFaceDescriptor(mesh->SurfaceElement(sei).GetIndex ()).BCProperty() == nfd){
      return true;
    }else{
      return false;
    }
}
/*
    NGLIB_API Ng_Result My_MergeMesh( Ng_Mesh* mesh, const char* filename)
   {
      Ng_Result status = NG_OK;

      ifstream infile(filename);
      Mesh * m = (Mesh*)mesh;

      if(!infile.good())
      {
         status = NG_FILE_NOT_FOUND;
      }

      if(!m)
      {
         status = NG_ERROR;
      }

      if(status == NG_OK)
      {
         const int num_pts = m->GetNP();
         const int face_offset = m->GetNFD();

         m->Merge(infile, face_offset);

         if(m->GetNP() > num_pts)
         {
            status = NG_OK;
         }
         else
         {
            status = NG_ERROR;
         }
      }

      return status;
   }

*/




} // End of namespace nglib




// compatibility functions:
namespace netgen 
{
   char geomfilename[255];

   NGLIB_API void MyError2 (const char * ch)
   {
      cerr << ch;
   }




   //Destination for messages, errors, ...
   NGLIB_API void Ng_PrintDest2(const char * s)
   {
#ifdef PARALLEL
     int id = 0;
     MPI_Comm_rank(MPI_COMM_WORLD, &id);
     if (id != 0) return;
#endif
     (*mycout) << s << flush;
   }


  /*
   NGLIB_API double GetTime ()
   {
      return 0;
   }
  */

  /*
#ifndef WIN32
   void ResetTime ()
   {
      ;
   }
#endif
  */


   void MyBeep (int i)
   {
      ;
   }



  //void Render() { ; }

} // End of namespace netgen


/*

#ifndef WIN32
void Ng_Redraw () { ; }
void Ng_ClearSolutionData() { ; }
#endif
void Ng_SetSolutionData (Ng_SolutionData * soldata) 
{ 
  delete soldata->solclass;
}
void Ng_InitSolutionData (Ng_SolutionData * soldata) { ; }
*/

// Force linking libinterface to libnglib
#include <../interface/writeuser.hpp>
void MyDummyToForceLinkingLibInterface(Mesh &mesh, NetgenGeometry &geom)
{
  netgen::WriteUserFormat("", mesh, /* geom, */ "");
}

/* 
 *  brief 写入网格文件为elmer格式
 *  param write_mesh : 写入网格
 *  param filename   : 写入文件名
 *  return 无
 */
void nglib::My_WriteElmerFormat(Ng_Mesh *write_mesh, const filesystem::path & filename) {
    
    mesh = make_shared<Mesh>();
    *mesh = *(Mesh*)write_mesh;
    SetGlobalMesh(mesh);
    if(mesh->GetGeometry())
        ng_geometry = mesh->GetGeometry();


	// const Mesh & m = (Mesh&)mesh;
   const filesystem::path &format = "Elmer Format";
   netgen::WriteUserFormat(format, *mesh, filename);
    cout << "done" << endl;
}

void nglib::My_WriteOpenFOAMFormat(Ng_Mesh *write_mesh, const filesystem::path & filename) {
    
    mesh = make_shared<Mesh>();
    *mesh = *(Mesh*)write_mesh;
    SetGlobalMesh(mesh);
    if(mesh->GetGeometry())
        ng_geometry = mesh->GetGeometry();


	// const Mesh & m = (Mesh&)mesh;
   const filesystem::path &format = "OpenFOAM 1.5+ Format";
   netgen::WriteUserFormat(format, *mesh, filename);
    cout << "done" << endl;
}

void nglib::My_WriteElmerBound(void *submesh, const std::string boundaryfile) {
    cout << "write elmer mesh files" << endl;
    mesh = make_shared<Mesh>();
    *mesh = *(Mesh*)submesh;
    std::map<ELEMENT_TYPE, int> tmap;
    tmap[TRIG] = 303;
    tmap[TRIG6] = 306;
    tmap[QUAD] = 404;
    tmap[QUAD8] = 408;
    tmap[TET] = 504;
    tmap[TET10] = 510;
    tmap[PYRAMID] = 605;
    tmap[PYRAMID13] = 613;
    tmap[PRISM] = 706;
    tmap[PRISM15] = 715;
    tmap[HEX] = 808;
    tmap[HEX20] = 820;

    std::map<int, Array<int,int>> pmap;
    pmap[TRIG]  = {1,2,3};
    pmap[TRIG6] = {1,2,3, 6,4,5};
    pmap[QUAD]  = {1,2,3,4};
    pmap[QUAD8] = {1,2,3,4, 5,8,6,7};
    pmap[TET]   = {1,2,3,4};
    pmap[TET10] = {1,2,3,4, 5,8,6,7,9,10};
    pmap[PYRAMID]={1,2,3,4,5};
    pmap[PYRAMID13]= {1,2,3,4,5,6,7,8,9,10,11,12,13};
    pmap[PRISM] = {1,2,3,4,5,6};
    pmap[PRISM15] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15};
    pmap[HEX]   = {1,2,3,4,5,6,7,8};
    pmap[HEX20] = {1,2,3,4,5,8,6,7,8, 9,12,10,11, 17,20,19,18, 13,16,14,15};

    int np = mesh->GetNP();
    int ne = mesh->GetNE();
    int nse = mesh->GetNSE();
    int i, j;
    // char str[200];

    int inverttets = mparam.inverttets;
    int invertsurf = mparam.inverttrigs;



  //  ofstream outfile_h(get_name("mesh.header"));
  //  ofstream outfile_n(get_name("mesh.nodes"));
   // ofstream outfile_e(get_name("mesh.elements"));
    ofstream outfile_b(boundaryfile);
   // ofstream outfile_names(get_name("mesh.names"));


    auto get3FacePoints = [](const Element2d & el)
    {
        INDEX_3 i3;
        INDEX_4 i4;
        auto eltype = el.GetType();
        switch (eltype)
        {
            case TRIG:
            case TRIG6:
                i3 = {el[0], el[1], el[2]};
                i3.Sort();
                break;
            case QUAD:
            case QUAD8:
                i4 = {el[0], el[1], el[2], el[3]};
                i4.Sort();
                i3 = {i4[0], i4[1], i4[2]};
                break;
            default:
                throw Exception("Got invalid type (no face)");
        }
        return i3;
    };

    // fill hashtable

    // use lowest three point numbers of lowest-order face to index faces
    INDEX_3_HASHTABLE<int> face2volelement(ne);

    for (int i = 1; i <= ne; i++)
    {
        const Element & el = mesh->VolumeElement(i);

        // getface not working for second order elements -> reconstruct linear element here
        Element linear_el = el;
        linear_el.SetNP(el.GetNV()); // GetNV returns 8 for HEX20 for instance

        for (auto j : Range(1,el.GetNFaces()+1))
        {
            Element2d face;
            linear_el.GetFace(j, face);
            face2volelement.Set (get3FacePoints(face), i);
            cout << "set " << get3FacePoints(face) << "\tto " << i << endl;
        }
    }

//  outfile.precision(6);
//  outfile.setf (ios::fixed, ios::floatfield);
//  outfile.setf (ios::showpoint);

    std::map<ELEMENT_TYPE, size_t> elcount;

    for (i = 1; i <= np; i++)
    {
        const Point3d & p = mesh->Point(i);

       // outfile_n << i << " -1 ";
       // outfile_n << p.X() << " ";
        //outfile_n << p.Y() << " ";
       // outfile_n << p.Z() << "\n";
    }

    for (i = 1; i <= ne; i++)
    {
        Element el = mesh->VolumeElement(i);
        if (inverttets) el.Invert();
        auto eltype = el.GetType();
        elcount[eltype]++;
       // outfile_e << i << " " << el.GetIndex() << " " << tmap[eltype] <<  "  ";

        auto & map = pmap[eltype];
        for (j = 1; j <= el.GetNP(); j++)
        {
           // outfile_e << " ";
           // outfile_e << el.PNum(map[j-1]);
        }
        //outfile_e << "\n";
    }



    for (int i = 1; i <= ne; i++)
    {
        const Element & el = mesh->VolumeElement(i);

        // getface not working for second order elements -> reconstruct linear element here
        Element linear_el = el;
        linear_el.SetNP(el.GetNV()); // GetNV returns 8 for HEX20 for instance

        for (auto j : Range(1,el.GetNFaces()+1))
        {
            Element2d face;
            linear_el.GetFace(j, face);
            face2volelement.Set (get3FacePoints(face), i);
            cout << "set " << get3FacePoints(face) << "\tto " << i << endl;
        }
    }

    for (i = 1; i <= nse; i++)
    {
        Element2d el = mesh->SurfaceElement(i);
        if (invertsurf) el.Invert();
        auto eltype = el.GetType();
        elcount[eltype]++;

        int elind = face2volelement.Get(get3FacePoints(el));
        cout << "get " << get3FacePoints(el) << "\t " << elind << endl;

        outfile_b << i << " " << mesh->GetFaceDescriptor(el.GetIndex()).BCProperty() <<
                  " " << elind << " 0 "  << tmap[eltype] << "    ";

        auto & map = pmap[el.GetType()];
        for (j = 1; j <= el.GetNP(); j++)
        {
            outfile_b << " ";
            outfile_b << el.PNum(map[j-1]);
        }
        outfile_b << "\n";
    }
    outfile_b.close();
cout << "done" << endl;
}



void WriteElmerFormat (const Mesh &mesh,
                       const filesystem::path &dirname)
{
    cout << "write elmer mesh files" << endl;

    std::map<ELEMENT_TYPE, int> tmap;
    tmap[TRIG] = 303;
    tmap[TRIG6] = 306;
    tmap[QUAD] = 404;
    tmap[QUAD8] = 408;
    tmap[TET] = 504;
    tmap[TET10] = 510;
    tmap[PYRAMID] = 605;
    tmap[PYRAMID13] = 613;
    tmap[PRISM] = 706;
    tmap[PRISM15] = 715;
    tmap[HEX] = 808;
    tmap[HEX20] = 820;

    std::map<int, Array<int,int>> pmap;
    pmap[TRIG]  = {1,2,3};
    pmap[TRIG6] = {1,2,3, 6,4,5};
    pmap[QUAD]  = {1,2,3,4};
    pmap[QUAD8] = {1,2,3,4, 5,8,6,7};
    pmap[TET]   = {1,2,3,4};
    pmap[TET10] = {1,2,3,4, 5,8,6,7,9,10};
    pmap[PYRAMID]={1,2,3,4,5};
    pmap[PYRAMID13]= {1,2,3,4,5,6,7,8,9,10,11,12,13};
    pmap[PRISM] = {1,2,3,4,5,6};
    pmap[PRISM15] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15};
    pmap[HEX]   = {1,2,3,4,5,6,7,8};
    pmap[HEX20] = {1,2,3,4,5,8,6,7,8, 9,12,10,11, 17,20,19,18, 13,16,14,15};

    int np = mesh.GetNP();
    int ne = mesh.GetNE();
    int nse = mesh.GetNSE();
    int i, j;
    // char str[200];

    int inverttets = mparam.inverttets;
    int invertsurf = mparam.inverttrigs;

    filesystem::create_directories(dirname);

    auto get_name = [&dirname]( string s ) {
        return filesystem::path(dirname).append(s);
    };

    ofstream outfile_h(get_name("mesh.header"));
    ofstream outfile_n(get_name("mesh.nodes"));
    ofstream outfile_e(get_name("mesh.elements"));
    ofstream outfile_b(get_name("mesh.boundary"));
    ofstream outfile_names(get_name("mesh.names"));

    for( auto codim : IntRange(0, mesh.GetDimension()-1) )
    {
        auto & names = const_cast<Mesh&>(mesh).GetRegionNamesCD(codim);

        for (auto i0 : Range(names) )
        {
            if(names[i0] == nullptr)
                continue;
            string name = *names[i0];
            if(name == "" || name == "default")
                continue;
            outfile_names << "$" << name << "=" << i0+1 << "\n";
        }
    }

    auto get3FacePoints = [](const Element2d & el)
    {
        INDEX_3 i3;
        INDEX_4 i4;
        auto eltype = el.GetType();
        switch (eltype)
        {
            case TRIG:
            case TRIG6:
                i3 = {el[0], el[1], el[2]};
                i3.Sort();
                break;
            case QUAD:
            case QUAD8:
                i4 = {el[0], el[1], el[2], el[3]};
                i4.Sort();
                i3 = {i4[0], i4[1], i4[2]};
                break;
            default:
                throw Exception("Got invalid type (no face)");
        }
        return i3;
    };

    // fill hashtable

    // use lowest three point numbers of lowest-order face to index faces
    INDEX_3_HASHTABLE<int> face2volelement(ne);

    for (int i = 1; i <= ne; i++)
    {
        const Element & el = mesh.VolumeElement(i);

        // getface not working for second order elements -> reconstruct linear element here
        Element linear_el = el;
        linear_el.SetNP(el.GetNV()); // GetNV returns 8 for HEX20 for instance

        for (auto j : Range(1,el.GetNFaces()+1))
        {
            Element2d face;
            linear_el.GetFace(j, face);
            face2volelement.Set (get3FacePoints(face), i);
            cout << "set " << get3FacePoints(face) << "\tto " << i << endl;
        }
    }

//  outfile.precision(6);
//  outfile.setf (ios::fixed, ios::floatfield);
//  outfile.setf (ios::showpoint);

    std::map<ELEMENT_TYPE, size_t> elcount;

    for (i = 1; i <= np; i++)
    {
        const Point3d & p = mesh.Point(i);

        outfile_n << i << " -1 ";
        outfile_n << p.X() << " ";
        outfile_n << p.Y() << " ";
        outfile_n << p.Z() << "\n";
    }

    for (i = 1; i <= ne; i++)
    {
        Element el = mesh.VolumeElement(i);
        if (inverttets) el.Invert();
        auto eltype = el.GetType();
        elcount[eltype]++;
        outfile_e << i << " " << el.GetIndex() << " " << tmap[eltype] <<  "  ";

        auto & map = pmap[eltype];
        for (j = 1; j <= el.GetNP(); j++)
        {
            outfile_e << " ";
            outfile_e << el.PNum(map[j-1]);
        }
        outfile_e << "\n";
    }

    for (i = 1; i <= nse; i++)
    {
        Element2d el = mesh.SurfaceElement(i);
        if (invertsurf) el.Invert();
        auto eltype = el.GetType();
        elcount[eltype]++;

        int elind = face2volelement.Get(get3FacePoints(el));
        cout << "get " << get3FacePoints(el) << "\t " << elind << endl;

        outfile_b << i << " " << mesh.GetFaceDescriptor(el.GetIndex()).BCProperty() <<
                  " " << elind << " 0 "  << tmap[eltype] << "    ";

        auto & map = pmap[el.GetType()];
        for (j = 1; j <= el.GetNP(); j++)
        {
            outfile_b << " ";
            outfile_b << el.PNum(map[j-1]);
        }
        outfile_b << "\n";
    }

    outfile_h << np << " " << ne << " " << nse << "\n";
    outfile_h << "2"     << "\n";

    for( auto & [eltype,count] : elcount )
        outfile_h << tmap[eltype] << " " << count << "\n";
}
