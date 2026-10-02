#include "occt_bridge.h"

#include <BRepBndLib.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepBuilderAPI_GTransform.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeSolid.hxx>
#include <BRepBuilderAPI_Sewing.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepClass3d_SolidClassifier.hxx>
#include <BRepGProp.hxx>
#include <BRep_Builder.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakeCone.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <BRepPrimAPI_MakeRevol.hxx>
#include <BRepPrimAPI_MakeSphere.hxx>
#include <BRep_Tool.hxx>
#include <BRepTools.hxx>
#include <Bnd_Box.hxx>
#include <Standard_Failure.hxx>
#include <Standard_Version.hxx>
#include <GProp_GProps.hxx>
#include <IntCurvesFace_ShapeIntersector.hxx>
#include <Poly_Triangulation.hxx>
#include <TopAbs_Orientation.hxx>
#include <TopAbs_State.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <GeomAbs_SurfaceType.hxx>
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
#include <gp_Ax1.hxx>
#include <gp_Dir.hxx>
#include <gp_GTrsf.hxx>
#include <gp_Mat.hxx>
#include <gp_Pnt.hxx>
#include <gp_Trsf.hxx>
#include <gp_Vec.hxx>
#include <gp_XYZ.hxx>

#include <cmath>
#include <algorithm>
#include <array>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <new>
#include <limits>
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
struct AxisFrame { gp_Dir axis; gp_Vec u; gp_Vec v; };
AxisFrame axis_frame(OcctAxis axis) {
  switch(axis) {
    case OCCT_AXIS_X: return {gp_Dir(1,0,0),gp_Vec(0,1,0),gp_Vec(0,0,1)};
    case OCCT_AXIS_Y: return {gp_Dir(0,1,0),gp_Vec(0,0,1),gp_Vec(1,0,0)};
    case OCCT_AXIS_Z: return {gp_Dir(0,0,1),gp_Vec(1,0,0),gp_Vec(0,1,0)};
    default: throw std::invalid_argument("axis must be OCCT_AXIS_X, Y, or Z");
  }
}
using Point2 = std::array<double,2>;
long double orient2(const Point2 &a,const Point2 &b,const Point2 &c) {
  return (static_cast<long double>(b[0])-a[0])*(static_cast<long double>(c[1])-a[1])
       - (static_cast<long double>(b[1])-a[1])*(static_cast<long double>(c[0])-a[0]);
}
bool on_segment(const Point2 &a,const Point2 &b,const Point2 &p) {
  return p[0]>=std::min(a[0],b[0]) && p[0]<=std::max(a[0],b[0])
      && p[1]>=std::min(a[1],b[1]) && p[1]<=std::max(a[1],b[1]);
}
bool segments_intersect(const Point2 &a,const Point2 &b,const Point2 &c,const Point2 &d) {
  const long double o1=orient2(a,b,c),o2=orient2(a,b,d),o3=orient2(c,d,a),o4=orient2(c,d,b);
  if(((o1<0 && o2>0)||(o1>0 && o2<0)) && ((o3<0 && o4>0)||(o3>0 && o4<0))) return true;
  return (o1==0 && on_segment(a,b,c)) || (o2==0 && on_segment(a,b,d))
      || (o3==0 && on_segment(c,d,a)) || (o4==0 && on_segment(c,d,b));
}
std::vector<Point2> checked_profile(const double *pairs,size_t count,bool radial) {
  if(!pairs || count<3) throw std::invalid_argument("profile requires at least three 2D points");
  if(count>100000) throw std::invalid_argument("profile exceeds the point limit");
  std::vector<Point2> points; points.reserve(count);
  for(size_t i=0;i<count;++i) {
    const double a=pairs[2*i],b=pairs[2*i+1];
    if(!finite(a)||!finite(b)) throw std::invalid_argument("profile coordinates must be finite");
    if(radial && a<0) throw std::invalid_argument("revolve profile radius must be nonnegative");
    points.push_back({a,b});
  }
  for(size_t i=0;i<count;++i) {
    const auto &a=points[i],&b=points[(i+1)%count];
    if(a[0]==b[0] && a[1]==b[1]) throw std::invalid_argument("profile has a repeated adjacent point or repeated closing point");
  }
  long double area2=0;
  for(size_t i=0;i<count;++i) {
    const auto &a=points[i],&b=points[(i+1)%count];
    area2+=static_cast<long double>(a[0])*b[1]-static_cast<long double>(b[0])*a[1];
  }
  if(!std::isfinite(area2)||area2==0) throw std::invalid_argument("profile area must be finite and nonzero");
  for(size_t i=0;i<count;++i) {
    const size_t in=(i+1)%count;
    for(size_t j=i+1;j<count;++j) {
      const size_t jn=(j+1)%count;
      if(j==in || i==jn) continue;
      if(segments_intersect(points[i],points[in],points[j],points[jn]))
        throw std::invalid_argument("profile is self-intersecting");
    }
  }
  return points;
}
TopoDS_Face profile_face(const std::vector<Point2> &profile,const gp_Pnt &origin,
                         const gp_Vec &basis_u,const gp_Vec &basis_v) {
  BRepBuilderAPI_MakePolygon wire_builder;
  for(const auto &p:profile) {
    const gp_Pnt point=origin.Translated(basis_u*p[0]+basis_v*p[1]);
    if(!finite(point.X())||!finite(point.Y())||!finite(point.Z()))
      throw std::invalid_argument("mapped profile coordinates must be finite");
    wire_builder.Add(point);
  }
  wire_builder.Close();
  if(!wire_builder.IsDone()) throw std::runtime_error("OCCT could not close the profile wire");
  BRepBuilderAPI_MakeFace face_builder(wire_builder.Wire(),true);
  if(!face_builder.IsDone()) throw std::invalid_argument("profile does not form a planar face");
  return face_builder.Face();
}
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

OcctStatus occt_make_cone(double rb,double rt,double h,double bx,double by,double bz,OcctAxis axis,OcctShape **out) {
  return guard([&] {
    check_out(out);
    const AxisFrame frame=axis_frame(axis);
    if(!(finite(rb)&&finite(rt)&&finite(h)&&finite(bx)&&finite(by)&&finite(bz))
       || rb<0 || rt<0 || h<=0 || (rb==0 && rt==0))
      throw std::invalid_argument("cone radii must be finite and nonnegative, height positive, and at least one radius nonzero");
    BRepPrimAPI_MakeCone cone(gp_Ax2(gp_Pnt(bx,by,bz),frame.axis),rb,rt,h);
    cone.Build();
    if(!cone.IsDone()) throw std::runtime_error("OCCT cone construction did not complete");
    *out=wrap(cone.Shape());
  });
}

OcctStatus occt_make_prism(const double *pairs,size_t count,double h,double ox,double oy,double oz,OcctAxis axis,OcctShape **out) {
  return guard([&] {
    check_out(out);
    const AxisFrame frame=axis_frame(axis);
    if(!finite(h)||h<=0||!finite(ox)||!finite(oy)||!finite(oz))
      throw std::invalid_argument("prism height and origin must be finite; height must be positive");
    const auto profile=checked_profile(pairs,count,false);
    const TopoDS_Face face=profile_face(profile,gp_Pnt(ox,oy,oz),frame.u,frame.v);
    const gp_Vec translation(frame.axis.X()*h,frame.axis.Y()*h,frame.axis.Z()*h);
    BRepPrimAPI_MakePrism prism(face,translation);
    prism.Build();
    if(!prism.IsDone()) throw std::runtime_error("OCCT prism construction did not complete");
    *out=wrap(prism.Shape());
  });
}

OcctStatus occt_make_revolve(const double *pairs,size_t count,double degrees,double ox,double oy,double oz,OcctAxis axis,OcctShape **out) {
  return guard([&] {
    check_out(out);
    const AxisFrame frame=axis_frame(axis);
    if(!finite(degrees)||std::abs(degrees)<=1e-9||std::abs(degrees)>360.0+1e-8||!finite(ox)||!finite(oy)||!finite(oz))
      throw std::invalid_argument("revolve angle must be nonzero within [-360,360] degrees and origin must be finite");
    const auto profile=checked_profile(pairs,count,true);
    const gp_Pnt origin(ox,oy,oz);
    const TopoDS_Face face=profile_face(profile,origin,frame.u,gp_Vec(frame.axis));
    constexpr double pi=3.141592653589793238462643383279502884;
    BRepPrimAPI_MakeRevol revolve(face,gp_Ax1(origin,frame.axis),degrees*pi/180.0,true);
    revolve.Build();
    if(!revolve.IsDone()) throw std::runtime_error("OCCT revolve construction did not complete");
    *out=wrap(revolve.Shape());
  });
}

OcctStatus occt_make_solid_from_triangles(const double *xyz,size_t vertex_count,double tolerance,OcctShape **out) {
  return guard([&] {
    check_out(out);
    constexpr size_t kMaxTriangles=100000;
    if(!xyz || vertex_count<12 || vertex_count%3!=0 || vertex_count/3>kMaxTriangles
       || !finite(tolerance) || tolerance<=0 || tolerance>0.1)
      throw std::invalid_argument("triangle mesh must have 4..100000 triangles and positive sewing tolerance <= 0.1");
    BRepBuilderAPI_Sewing sewing(tolerance,true,true,true,false);
    for(size_t i=0;i<vertex_count;i+=3) {
      gp_Pnt p[3];
      for(size_t j=0;j<3;++j) {
        const size_t coordinate=(i+j)*3;
        const double x=xyz[coordinate],y=xyz[coordinate+1],z=xyz[coordinate+2];
        if(!finite(x)||!finite(y)||!finite(z)) throw std::invalid_argument("triangle mesh coordinates must be finite");
        p[j]=gp_Pnt(x,y,z);
      }
      const double area2=(p[1].XYZ()-p[0].XYZ()).Crossed(p[2].XYZ()-p[0].XYZ()).SquareModulus();
      if(!finite(area2)||area2<=tolerance*tolerance*tolerance*tolerance)
        throw std::invalid_argument("triangle mesh contains a degenerate face");
      BRepBuilderAPI_MakePolygon polygon;
      polygon.Add(p[0]);polygon.Add(p[1]);polygon.Add(p[2]);polygon.Close();
      if(!polygon.IsDone()) throw std::invalid_argument("OCCT could not construct a triangle wire");
      BRepBuilderAPI_MakeFace face(polygon.Wire(),true);
      if(!face.IsDone()) throw std::invalid_argument("OCCT could not construct a triangle face");
      sewing.Add(face.Face());
    }
    sewing.Perform();
    const TopoDS_Shape sewn=sewing.SewedShape();
    if(sewn.IsNull()) throw std::invalid_argument("mesh sewing produced a null shape");
    std::vector<TopoDS_Shell> shells;
    for(TopExp_Explorer ex(sewn,TopAbs_SHELL);ex.More();ex.Next()) shells.push_back(TopoDS::Shell(ex.Current()));
    if(shells.empty() && sewn.ShapeType()==TopAbs_SHELL) shells.push_back(TopoDS::Shell(sewn));
    if(shells.size()!=1 || !BRep_Tool::IsClosed(shells.front()))
      throw std::invalid_argument("triangle mesh must sew to exactly one closed shell (shells="+std::to_string(shells.size())+", free_edges="+std::to_string(sewing.NbFreeEdges())+")");
    BRepBuilderAPI_MakeSolid solid_builder(shells.front());
    if(!solid_builder.IsDone()) throw std::invalid_argument("OCCT could not make a solid from the closed shell");
    TopoDS_Solid solid=solid_builder.Solid();
    if(!BRepCheck_Analyzer(solid,true).IsValid()) throw std::invalid_argument("sewn mesh solid failed BRepCheck");
    GProp_GProps props;BRepGProp::VolumeProperties(solid,props);
    if(!finite(props.Mass()) || std::abs(props.Mass())<=std::numeric_limits<double>::epsilon())
      throw std::invalid_argument("mesh solid must have nonzero finite volume");
    if(props.Mass()<0) solid.Reverse();
    *out=wrap(solid);
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

OcctStatus occt_shape_line_material_intervals(const OcctShape *shape,
    double ox,double oy,double oz,double ax,double ay,double az,
    double tmin,double tmax,double tolerance,
    OcctMaterialInterval **out_intervals,size_t *out_count) {
  if(out_intervals) *out_intervals=nullptr;
  if(out_count) *out_count=0;
  return guard([&] {
    if(!out_intervals||!out_count) throw std::invalid_argument("interval output pointers must not be null");
    if(!valid(shape)) throw std::invalid_argument("shape handle must not be null");
    if(!(finite(ox)&&finite(oy)&&finite(oz)&&finite(ax)&&finite(ay)&&finite(az)
         &&finite(tmin)&&finite(tmax)&&finite(tolerance)) || !(tmin<tmax) || tolerance<=0)
      throw std::invalid_argument("line coordinates, range, and tolerance must be finite; range must increase and tolerance must be positive");
    const double axis_length=std::hypot(ax,std::hypot(ay,az));
    if(!finite(axis_length)||axis_length==0) throw std::invalid_argument("line axis must be nonzero and finite");
    const gp_Dir direction(ax,ay,az);
    const gp_Lin line(gp_Pnt(ox,oy,oz),direction);
    IntCurvesFace_ShapeIntersector intersector;
    intersector.Load(shape->value,tolerance);
    intersector.Perform(line,tmin,tmax);
    if(!intersector.IsDone()) throw std::runtime_error("OCCT line/face intersection did not complete");

    std::vector<double> cuts{tmin,tmax};
    cuts.reserve(static_cast<size_t>(intersector.NbPnt())+2);
    for(Standard_Integer i=1;i<=intersector.NbPnt();++i) {
      const double t=intersector.WParameter(i);
      if(finite(t)&&t>=tmin-tolerance&&t<=tmax+tolerance)
        cuts.push_back(std::clamp(t,tmin,tmax));
    }
    std::sort(cuts.begin(),cuts.end());
    std::vector<double> unique;
    unique.reserve(cuts.size());
    for(size_t begin=0;begin<cuts.size();) {
      size_t end=begin+1;
      long double sum=cuts[begin];
      while(end<cuts.size()&&cuts[end]-cuts[end-1]<=tolerance) { sum+=cuts[end]; ++end; }
      double value=static_cast<double>(sum/static_cast<long double>(end-begin));
      if(begin==0) value=tmin;
      if(end==cuts.size()) value=tmax;
      unique.push_back(value);
      begin=end;
    }
    if(unique.front()!=tmin) unique.insert(unique.begin(),tmin);
    if(unique.back()!=tmax) unique.push_back(tmax);

    std::vector<OcctMaterialInterval> material;
    for(size_t i=0;i+1<unique.size();++i) {
      const double lo=unique[i],hi=unique[i+1];
      if(!(hi>lo)) continue;
      const gp_Pnt point=line.Location().Translated(gp_Vec(direction)*(lo+(hi-lo)*0.5));
      bool inside=false;
      for(TopExp_Explorer it(shape->value,TopAbs_SOLID);it.More();it.Next()) {
        BRepClass3d_SolidClassifier classifier(TopoDS::Solid(it.Current()),point,tolerance);
        if(classifier.State()==TopAbs_IN) { inside=true; break; }
      }
      if(inside) {
        if(!material.empty()&&lo-material.back().t_end<=tolerance) material.back().t_end=hi;
        else material.push_back({lo,hi});
      }
    }
    if(material.empty()) return;
    if(material.size()>std::numeric_limits<size_t>::max()/sizeof(OcctMaterialInterval)) throw std::bad_alloc();
    auto *buffer=static_cast<OcctMaterialInterval*>(std::malloc(material.size()*sizeof(OcctMaterialInterval)));
    if(!buffer) throw std::bad_alloc();
    std::memcpy(buffer,material.data(),material.size()*sizeof(OcctMaterialInterval));
    *out_intervals=buffer;
    *out_count=material.size();
  });
}

void occt_material_intervals_release(OcctMaterialInterval *intervals) { std::free(intervals); }

OcctStatus occt_shape_cylindrical_faces(const OcctShape *shape,
    OcctCylinderFace **out_faces,size_t *out_count) {
  if(out_faces) *out_faces=nullptr;
  if(out_count) *out_count=0;
  return guard([&] {
    if(!valid(shape)||!out_faces||!out_count)
      throw std::invalid_argument("shape and cylinder face output pointers are required");
    std::vector<OcctCylinderFace> faces;
    for(TopExp_Explorer ex(shape->value,TopAbs_FACE);ex.More();ex.Next()) {
      const TopoDS_Face face=TopoDS::Face(ex.Current());
      BRepAdaptor_Surface surface(face,Standard_True);
      if(surface.GetType()!=GeomAbs_Cylinder) continue;
      const gp_Cylinder cylinder=surface.Cylinder();
      double umin,umax,vmin,vmax;
      BRepTools::UVBounds(face,umin,umax,vmin,vmax);
      if(!(finite(umin)&&finite(umax)&&finite(vmin)&&finite(vmax)) || umax<umin || vmax<vmin)
        throw std::runtime_error("cylindrical face has invalid trimmed UV bounds");
      const double umid=umin+(umax-umin)*0.5;
      const double vmid=vmin+(vmax-vmin)*0.5;
      gp_Pnt point; gp_Vec du,dv;
      surface.D1(umid,vmid,point,du,dv);
      gp_Vec normal=du.Crossed(dv);
      if(normal.SquareMagnitude()<=std::numeric_limits<double>::epsilon())
        throw std::runtime_error("cylindrical face has degenerate midpoint normal");
      normal.Normalize();
      if(face.Orientation()==TopAbs_REVERSED) normal.Reverse();
      const gp_Ax1 axis=cylinder.Axis();
      const gp_Pnt origin=axis.Location(); const gp_Dir direction=axis.Direction();
      faces.push_back({cylinder.Radius(),
        {origin.X(),origin.Y(),origin.Z()},
        {direction.X(),direction.Y(),direction.Z()},
        vmin,vmax,umin,umax,{point.X(),point.Y(),point.Z()},
        {normal.X(),normal.Y(),normal.Z()}});
    }
    if(faces.empty()) return;
    if(faces.size()>std::numeric_limits<size_t>::max()/sizeof(OcctCylinderFace)) throw std::bad_alloc();
    auto *buffer=static_cast<OcctCylinderFace*>(std::malloc(faces.size()*sizeof(OcctCylinderFace)));
    if(!buffer) throw std::bad_alloc();
    std::memcpy(buffer,faces.data(),faces.size()*sizeof(OcctCylinderFace));
    *out_faces=buffer; *out_count=faces.size();
  });
}
void occt_cylindrical_faces_release(OcctCylinderFace *faces) { std::free(faces); }

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
