#include "occt_bridge.h"

#include <BRepBndLib.hxx>
#include <BRepBuilderAPI_GTransform.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeSolid.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <BRep_Builder.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepPrimAPI_MakeSphere.hxx>
#include <BRep_Tool.hxx>
#include <Bnd_Box.hxx>
#include <Standard_Failure.hxx>
#include <Standard_Version.hxx>
#include <GProp_GProps.hxx>
#include <Poly_Triangulation.hxx>
#include <TopAbs_Orientation.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS_Shell.hxx>
#include <TopoDS_Solid.hxx>
#include <TopTools_ListOfShape.hxx>
#include <TopLoc_Location.hxx>
#include <BOPAlgo_BOP.hxx>
#include <BOPAlgo_Operation.hxx>
#include <gp_Ax2.hxx>
#include <gp_Dir.hxx>
#include <gp_GTrsf.hxx>
#include <gp_Mat.hxx>
#include <gp_Pnt.hxx>
#include <gp_Trsf.hxx>
#include <gp_Vec.hxx>
#include <gp_XYZ.hxx>

#include <cmath>
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <new>
#include <string>
#include <stdexcept>
#include <utility>
#include <vector>

struct OcctShape { TopoDS_Shape value; };

namespace {
thread_local std::string last_error;

OcctStatus fail(OcctStatus status, const char *message) noexcept {
  try { last_error = message ? message : "unknown OCCT bridge error"; }
  catch (...) { last_error.clear(); }
  return status;
}

template<class F>
OcctStatus guard(F &&function) noexcept {
  try { last_error.clear(); function(); return OCCT_BRIDGE_OK; }
  catch (const Standard_Failure &e) { return fail(OCCT_BRIDGE_KERNEL_ERROR, e.GetMessageString()); }
  catch (const std::bad_alloc &) { return fail(OCCT_BRIDGE_OUT_OF_MEMORY, "out of memory"); }
  catch (const std::invalid_argument &e) { return fail(OCCT_BRIDGE_INVALID_ARGUMENT, e.what()); }
  catch (const std::exception &e) { return fail(OCCT_BRIDGE_KERNEL_ERROR, e.what()); }
  catch (...) { return fail(OCCT_BRIDGE_KERNEL_ERROR, "unknown C++ exception"); }
}

bool finite(double x) { return std::isfinite(x); }
bool valid(const OcctShape *s) { return s && !s->value.IsNull(); }
void check_out(OcctShape **out) {
  if (!out) throw std::invalid_argument("out_shape must not be null");
  *out = nullptr;
}
OcctShape *wrap(const TopoDS_Shape &shape) {
  if (shape.IsNull()) throw std::runtime_error("OCCT operation produced a null shape");
  return new OcctShape{shape};
}
TopoDS_Shape compound_of(const OcctShape *const *shapes, size_t count) {
  if (!shapes || count == 0) throw std::invalid_argument("shape list must not be empty");
  BRep_Builder builder;
  TopoDS_Compound compound;
  builder.MakeCompound(compound);
  for (size_t i = 0; i < count; ++i) {
    if (!valid(shapes[i])) throw std::invalid_argument("shape list contains null handle");
    builder.Add(compound, shapes[i]->value);
  }
  return compound;
}
bool is_closed_solid(const TopoDS_Solid &solid) {
  size_t shell_count=0;
  for(TopExp_Explorer ex(solid,TopAbs_SHELL);ex.More();ex.Next()) {
    ++shell_count;
    if(!BRep_Tool::IsClosed(TopoDS::Shell(ex.Current()))) return false;
  }
  return shell_count>0 && BRepCheck_Analyzer(solid,true).IsValid();
}
}

extern "C" {
const char *occt_last_error(void) { return last_error.c_str(); }
const char *occt_version(void) { return OCC_VERSION_STRING_EXT; }
uint32_t occt_bridge_abi_version(void) { return 1; }
void occt_shape_release(OcctShape *shape) {
  try { delete shape; last_error.clear(); }
  catch (...) { fail(OCCT_BRIDGE_KERNEL_ERROR, "exception while releasing shape handle"); }
}

OcctStatus occt_make_box(double dx, double dy, double dz, double cx, double cy, double cz, OcctShape **out) {
  return guard([&] {
    check_out(out);
    if (!(finite(dx)&&finite(dy)&&finite(dz)&&finite(cx)&&finite(cy)&&finite(cz)) || dx<=0 || dy<=0 || dz<=0) throw std::invalid_argument("box dimensions must be finite and positive");
    gp_Pnt p(cx-dx/2, cy-dy/2, cz-dz/2);
    *out = wrap(BRepPrimAPI_MakeBox(p, dx, dy, dz).Shape());
  });
}

OcctStatus occt_make_cylinder(double r,double h,double bx,double by,double bz,double ax,double ay,double az,OcctShape **out) {
  return guard([&] {
    check_out(out);
    if (!(finite(r)&&finite(h)&&finite(bx)&&finite(by)&&finite(bz)&&finite(ax)&&finite(ay)&&finite(az)) || r<=0 || h<=0 || std::hypot(ax,std::hypot(ay,az))==0) throw std::invalid_argument("cylinder radius, height and axis must be finite; radius/height positive; axis nonzero");
    *out = wrap(BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(bx,by,bz), gp_Dir(ax,ay,az)), r, h).Shape());
  });
}

OcctStatus occt_make_sphere(double r,double cx,double cy,double cz,OcctShape **out) {
  return guard([&] {
    check_out(out);
    if (!(finite(r)&&finite(cx)&&finite(cy)&&finite(cz)) || r<=0) throw std::invalid_argument("sphere radius must be finite and positive");
    *out = wrap(BRepPrimAPI_MakeSphere(gp_Pnt(cx,cy,cz), r).Shape());
  });
}

OcctStatus occt_fuse(const OcctShape *const *shapes,size_t count,OcctShape **out) {
  return guard([&] {
    check_out(out);
    if(!shapes||count==0) throw std::invalid_argument("fuse requires at least one shape");
    if(count==1) {
      if(!valid(shapes[0])) throw std::invalid_argument("shape list contains null handle");
      *out=wrap(shapes[0]->value);
      return;
    }
    BOPAlgo_BOP bop; bop.SetOperation(BOPAlgo_FUSE);
    for(size_t i=0;i<count;++i) {
      if(!valid(shapes[i])) throw std::invalid_argument("shape list contains null handle");
      if(i==0) bop.AddArgument(shapes[i]->value);
      else bop.AddTool(shapes[i]->value);
    }
    bop.Perform();
    if (bop.HasErrors()) throw std::runtime_error("OCCT fuse reported errors");
    *out=wrap(bop.Shape());
  });
}

OcctStatus occt_cut(const OcctShape *base,const OcctShape *const *cutters,size_t count,OcctShape **out) {
  return guard([&] {
    check_out(out);
    if (!valid(base)) throw std::invalid_argument("base shape is null");
    if(!cutters||count==0) throw std::invalid_argument("cut requires at least one cutter");
    TopoDS_Shape result=base->value;
    for(size_t i=0;i<count;++i) {
      if(!valid(cutters[i])) throw std::invalid_argument("cutter list contains null handle");
      BRepAlgoAPI_Cut op(result,cutters[i]->value);
      op.Build();
      if(!op.IsDone()) throw std::runtime_error("OCCT cut operation did not complete");
      result=op.Shape();
    }
    *out=wrap(result);
  });
}

OcctStatus occt_transform(const OcctShape *shape,const double m[16],OcctShape **out) {
  return guard([&] {
    check_out(out);
    if (!valid(shape)||!m) throw std::invalid_argument("shape and matrix are required");
    for (int i=0;i<16;++i) if(!finite(m[i])) throw std::invalid_argument("matrix values must be finite");
    if (std::abs(m[12])>1e-12 || std::abs(m[13])>1e-12 || std::abs(m[14])>1e-12 || std::abs(m[15]-1)>1e-12) throw std::invalid_argument("matrix must be affine row-major homogeneous");
    // Preserve analytic planes/cylinders for rigid and uniform-scale transforms.
    // GTransform converts even translations to spline surfaces; subsequent
    // exact bounds/volume checks on perforated parts can become very expensive.
    const gp_Mat matrix(m[0],m[1],m[2],m[4],m[5],m[6],m[8],m[9],m[10]);
    const double lengths[3] = {
      m[0]*m[0]+m[4]*m[4]+m[8]*m[8],
      m[1]*m[1]+m[5]*m[5]+m[9]*m[9],
      m[2]*m[2]+m[6]*m[6]+m[10]*m[10]
    };
    const double scale2 = std::max({lengths[0],lengths[1],lengths[2]});
    if(!finite(scale2) || scale2<=0 || std::abs(matrix.Determinant())<=1e-15)
      throw std::invalid_argument("matrix linear part must be nonsingular");
    const double tolerance=1e-12*scale2;
    const bool similarity = std::abs(lengths[0]-lengths[1])<=tolerance
      && std::abs(lengths[0]-lengths[2])<=tolerance
      && std::abs(m[0]*m[1]+m[4]*m[5]+m[8]*m[9])<=tolerance
      && std::abs(m[0]*m[2]+m[4]*m[6]+m[8]*m[10])<=tolerance
      && std::abs(m[1]*m[2]+m[5]*m[6]+m[9]*m[10])<=tolerance;
    if(similarity) {
      gp_Trsf trsf;
      trsf.SetValues(m[0],m[1],m[2],m[3],m[4],m[5],m[6],m[7],m[8],m[9],m[10],m[11]);
      BRepBuilderAPI_Transform op(shape->value,trsf,true);
      if(!op.IsDone()) throw std::runtime_error("OCCT similarity transform did not complete");
      *out=wrap(op.Shape());
      return;
    }
    gp_GTrsf trsf(matrix, gp_XYZ(m[3],m[7],m[11]));
    BRepBuilderAPI_GTransform op(shape->value,trsf,true);
    if(!op.IsDone()) throw std::runtime_error("OCCT affine transform did not complete");
    *out=wrap(op.Shape());
  });
}

OcctStatus occt_shape_is_valid(const OcctShape *shape,int32_t *out) {
  return guard([&] { if(!valid(shape)||!out) throw std::invalid_argument("shape and output are required"); *out=BRepCheck_Analyzer(shape->value,true).IsValid()?1:0; });
}
OcctStatus occt_shape_is_closed_solid(const OcctShape *shape,int32_t *out) {
  return guard([&] {
    if(!valid(shape)||!out) throw std::invalid_argument("shape and output are required");
    int solids=0; bool closed=true;
    for(TopExp_Explorer ex(shape->value,TopAbs_SOLID); ex.More(); ex.Next()) { ++solids; const TopoDS_Solid s=TopoDS::Solid(ex.Current()); if(!is_closed_solid(s)) closed=false; }
    *out=(solids==1 && closed && BRepCheck_Analyzer(shape->value,true).IsValid())?1:0;
  });
}
OcctStatus occt_shape_analyze(const OcctShape *shape,OcctShapeAnalysis *out) {
  return guard([&] {
    if(!valid(shape)||!out) throw std::invalid_argument("shape and output are required");
    out->valid=BRepCheck_Analyzer(shape->value,true).IsValid()?1:0;
    out->solid_count=0; out->closed_solid_count=0;
    for(TopExp_Explorer ex(shape->value,TopAbs_SOLID);ex.More();ex.Next()) {
      ++out->solid_count;
      if(is_closed_solid(TopoDS::Solid(ex.Current()))) ++out->closed_solid_count;
    }
  });
}
OcctStatus occt_shape_bounds(const OcctShape *shape,OcctBounds *out) {
  return guard([&] {
    if(!valid(shape)||!out) throw std::invalid_argument("shape and output are required");
    Bnd_Box box; BRepBndLib::AddOptimal(shape->value,box,false,false);
    if(box.IsVoid()) throw std::runtime_error("shape has empty bounds");
    box.Get(out->xmin,out->ymin,out->zmin,out->xmax,out->ymax,out->zmax);
  });
}
OcctStatus occt_shape_volume(const OcctShape *shape,double *out) {
  return guard([&] { if(!valid(shape)||!out) throw std::invalid_argument("shape and output are required"); GProp_GProps props; BRepGProp::VolumeProperties(shape->value,props); *out=props.Mass(); });
}

OcctStatus occt_shape_tessellate(const OcctShape *shape,double lin,double ang,OcctMesh *out) {
  if(out) { out->xyz=nullptr; out->normals=nullptr; out->vertex_count=0; }
  return guard([&] {
    if(!valid(shape)||!out||!finite(lin)||!finite(ang)||lin<=0||ang<=0) throw std::invalid_argument("shape, output and positive finite deflections are required");
    // A dynamically loaded bridge must not leave OCCT's global worker pool
    // alive during FreeLibrary (Windows loader-lock deadlock). The Rust host
    // schedules geometry jobs; keep each shape's meshing on that job thread.
    BRepMesh_IncrementalMesh mesher(shape->value,lin,false,ang,false);
    std::vector<double> xyz,normals;
    for(TopExp_Explorer ex(shape->value,TopAbs_FACE);ex.More();ex.Next()) {
      TopLoc_Location loc; const TopoDS_Face face=TopoDS::Face(ex.Current());
      Handle(Poly_Triangulation) tri=BRep_Tool::Triangulation(face,loc);
      if(tri.IsNull()) continue;
      const gp_Trsf placement=loc.Transformation();
      for(int i=1;i<=tri->NbTriangles();++i) {
        int a,b,c; tri->Triangle(i).Get(a,b,c); if(face.Orientation()==TopAbs_REVERSED) std::swap(b,c);
        const gp_Pnt pa=tri->Node(a).Transformed(placement), pb=tri->Node(b).Transformed(placement), pc=tri->Node(c).Transformed(placement);
        gp_Vec n=gp_Vec(pa,pb).Crossed(gp_Vec(pa,pc)); if(n.SquareMagnitude()>0) n.Normalize();
        for(const gp_Pnt *p:{&pa,&pb,&pc}) { xyz.insert(xyz.end(),{p->X(),p->Y(),p->Z()}); normals.insert(normals.end(),{n.X(),n.Y(),n.Z()}); }
      }
    }
    if(xyz.empty()) throw std::runtime_error("meshing produced no triangles");
    out->vertex_count=xyz.size()/3;
    out->xyz=static_cast<double*>(std::malloc(xyz.size()*sizeof(double)));
    out->normals=static_cast<double*>(std::malloc(normals.size()*sizeof(double)));
    if(!out->xyz||!out->normals) { std::free(out->xyz); std::free(out->normals); out->xyz=nullptr; out->normals=nullptr; out->vertex_count=0; throw std::bad_alloc(); }
    std::memcpy(out->xyz,xyz.data(),xyz.size()*sizeof(double)); std::memcpy(out->normals,normals.data(),normals.size()*sizeof(double));
  });
}
void occt_mesh_release(OcctMesh *mesh) {
  try { if(mesh) { std::free(mesh->xyz); std::free(mesh->normals); mesh->xyz=nullptr; mesh->normals=nullptr; mesh->vertex_count=0; } last_error.clear(); }
  catch (...) { fail(OCCT_BRIDGE_KERNEL_ERROR, "exception while releasing mesh allocation"); }
}
}
