#include "occt_bridge.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <vector>

static void require(bool condition, const char *what) {
  if (!condition) {
    std::fprintf(stderr, "FAIL: %s: %s\n", what, occt_last_error());
    std::exit(1);
  }
}

static void require_ok(OcctStatus status, const char *what) {
  require(status == OCCT_BRIDGE_OK, what);
}

static void require_intervals(const OcctShape *shape, double ox,double oy,double oz,
    double ax,double ay,double az,double tmin,double tmax,double tolerance,
    const std::vector<OcctMaterialInterval> &expected,const char *what) {
  OcctMaterialInterval *actual=nullptr;
  size_t count=0;
  require_ok(occt_shape_line_material_intervals(shape,ox,oy,oz,ax,ay,az,tmin,tmax,tolerance,
                                                &actual,&count),what);
  require(count==expected.size(),what);
  for(size_t i=0;i<count;++i) {
    require(std::abs(actual[i].t_start-expected[i].t_start)<=tolerance*2,what);
    require(std::abs(actual[i].t_end-expected[i].t_end)<=tolerance*2,what);
  }
  occt_material_intervals_release(actual);
}

int main() {
  constexpr double pi = 3.141592653589793238462643383279502884;
  OcctShape *invalid = nullptr;
  require(occt_make_box(0, 2, 3, 0, 0, 0, &invalid) == OCCT_BRIDGE_INVALID_ARGUMENT,
          "invalid input must return an error status without throwing across C ABI");
  require(invalid == nullptr, "failed output handle must remain null");
  OcctShape *box = nullptr;
  require_ok(occt_make_box(100, 80, 20, 0, 0, 10, &box), "make box");
  int32_t closed = 0;
  OcctShapeAnalysis analysis{};
  require_ok(occt_shape_analyze(box, &analysis), "box analysis");
  require(analysis.valid == 1 && analysis.solid_count == 1 && analysis.closed_solid_count == 1,
          "box analysis must report valid and one closed solid");
  require_ok(occt_shape_is_closed_solid(box, &closed), "box closed check");
  require(closed == 1, "box must be one valid closed solid");
  double volume = 0;
  require_ok(occt_shape_volume(box, &volume), "box volume");
  require(std::abs(volume - 160000.0) < 1e-7, "box volume must equal 160000 mm^3");
  OcctBounds bounds{};
  require_ok(occt_shape_bounds(box, &bounds), "box bounds");
  require(std::abs(bounds.xmin + 50) < 1e-8 && std::abs(bounds.xmax - 50) < 1e-8 &&
          std::abs(bounds.ymin + 40) < 1e-8 && std::abs(bounds.ymax - 40) < 1e-8 &&
          std::abs(bounds.zmin) < 1e-8 && std::abs(bounds.zmax - 20) < 1e-8,
          "box bounds must be [-50,-40,0]..[50,40,20]");

  // Shell face inventory is exact to this B-Rep. Model-facing selectors are
  // minted by Rust; this C ABI only exposes internal ordinals to its caller.
  OcctFaceInfo *faces=nullptr;
  size_t face_count=0;
  require_ok(occt_shape_faces(box,&faces,&face_count),"box face inventory");
  require(face_count==6,"box must enumerate six faces");
  size_t top_face=face_count;
  for(size_t i=0;i<face_count;++i) {
    require(faces[i].surface_type==0,"box face inventory must classify all six as planes");
    if(faces[i].center[2]>19.999 && faces[i].oriented_normal[2]>0.999) top_face=faces[i].ordinal;
  }
  require(top_face<face_count,"box inventory must identify its oriented top face");
  occt_faces_release(faces);
  OcctShape *shell_in=nullptr;
  require_ok(occt_make_thick_solid(box,&top_face,1,2.0,0,&shell_in),"make inward shell");
  require_ok(occt_shape_analyze(shell_in,&analysis),"inward shell analysis");
  require(analysis.valid==1&&analysis.solid_count==1&&analysis.closed_solid_count==1,
          "inward shell must remain one valid closed solid");
  require_ok(occt_shape_volume(shell_in,&volume),"inward shell volume");
  require(std::abs(volume-28672.0)<1e-5,"inward shell must remove the cavity volume with an open top");
  OcctShape *shell_out=nullptr;
  require_ok(occt_make_thick_solid(box,&top_face,1,2.0,1,&shell_out),"make outward shell");
  require_ok(occt_shape_analyze(shell_out,&analysis),"outward shell analysis");
  require(analysis.valid==1&&analysis.solid_count==1&&analysis.closed_solid_count==1,
          "outward shell must remain one valid closed solid");
  require_ok(occt_shape_bounds(shell_out,&bounds),"outward shell bounds");
  require(std::abs(bounds.xmin+52)<1e-6&&std::abs(bounds.xmax-52)<1e-6&&
          std::abs(bounds.ymin+42)<1e-6&&std::abs(bounds.ymax-42)<1e-6&&
          std::abs(bounds.zmin+2)<1e-6&&std::abs(bounds.zmax-20)<1e-6,
          "outward shell must offset remaining planar faces without direction fallback");
  occt_shape_release(shell_in);
  occt_shape_release(shell_out);

  // Analytic cone constructor.
  OcctShape *cone = nullptr;
  require_ok(occt_make_cone(10, 0, 30, 0, 0, 0, OCCT_AXIS_Z, &cone), "make analytic cone");
  require_ok(occt_shape_analyze(cone, &analysis), "cone analysis");
  require(analysis.valid == 1 && analysis.solid_count == 1 && analysis.closed_solid_count == 1,
          "cone must be one valid closed solid");
  require_ok(occt_shape_volume(cone, &volume), "cone volume");
  require(std::abs(volume - 1000.0 * pi) < 1e-5, "cone volume must equal 1000*pi mm^3");
  require_ok(occt_shape_bounds(cone, &bounds), "cone bounds");
  require(std::abs(bounds.xmin + 10) < 1e-7 && std::abs(bounds.xmax - 10) < 1e-7 &&
          std::abs(bounds.ymin + 10) < 1e-7 && std::abs(bounds.ymax - 10) < 1e-7 &&
          std::abs(bounds.zmin) < 1e-7 && std::abs(bounds.zmax - 30) < 1e-7,
          "cone bounds must be [-10,-10,0]..[10,10,30]");
  occt_shape_release(cone);

  // Prism basis is explicit, right-handed, and tied to the positive axis.
  const double rect[] = {0,0, 4,0, 4,6, 0,6};
  OcctShape *prism_x = nullptr;
  require_ok(occt_make_prism(rect, 4, 5, 1, 2, 3, OCCT_AXIS_X, &prism_x), "make X-axis prism");
  require_ok(occt_shape_analyze(prism_x, &analysis), "X-axis prism analysis");
  require(analysis.valid == 1 && analysis.solid_count == 1 && analysis.closed_solid_count == 1,
          "X-axis prism must be one valid closed solid");
  require_ok(occt_shape_volume(prism_x, &volume), "X-axis prism volume");
  require(std::abs(volume - 120.0) < 1e-8, "X-axis prism volume must equal 120 mm^3");
  require_ok(occt_shape_bounds(prism_x, &bounds), "X-axis prism bounds");
  require(std::abs(bounds.xmin-1)<1e-8 && std::abs(bounds.xmax-6)<1e-8 &&
          std::abs(bounds.ymin-2)<1e-8 && std::abs(bounds.ymax-6)<1e-8 &&
          std::abs(bounds.zmin-3)<1e-8 && std::abs(bounds.zmax-9)<1e-8,
          "X profile basis must map (u,v) to (Y,Z)");
  occt_shape_release(prism_x);
  OcctShape *prism_y = nullptr;
  require_ok(occt_make_prism(rect, 4, 4, 1, 2, 3, OCCT_AXIS_Y, &prism_y), "make Y-axis prism");
  require_ok(occt_shape_volume(prism_y, &volume), "Y-axis prism volume");
  require(std::abs(volume - 96.0) < 1e-8, "Y-axis prism volume must equal 96 mm^3");
  require_ok(occt_shape_bounds(prism_y, &bounds), "Y-axis prism bounds");
  require(std::abs(bounds.xmin-1)<1e-8 && std::abs(bounds.xmax-7)<1e-8 &&
          std::abs(bounds.ymin-2)<1e-8 && std::abs(bounds.ymax-6)<1e-8 &&
          std::abs(bounds.zmin-3)<1e-8 && std::abs(bounds.zmax-7)<1e-8,
          "Y profile basis must map (u,v) to (Z,X)");
  occt_shape_release(prism_y);

  // Analytic revolution: the profile is a hollow cylinder in (radius,axial).
  const double tube_profile[] = {2,0, 5,0, 5,10, 2,10};
  OcctShape *tube = nullptr;
  require_ok(occt_make_revolve(tube_profile, 4, 360, 0, 0, 0, OCCT_AXIS_Z, &tube),
             "make full analytic revolve");
  require_ok(occt_shape_analyze(tube, &analysis), "revolve analysis");
  require(analysis.valid == 1 && analysis.solid_count == 1 && analysis.closed_solid_count == 1,
          "full revolve must be one valid closed solid");
  require_ok(occt_shape_volume(tube, &volume), "revolve volume");
  require(std::abs(volume - 210.0 * pi) < 1e-5, "revolve volume must equal 210*pi mm^3");
  require_ok(occt_shape_bounds(tube, &bounds), "revolve bounds");
  require(std::abs(bounds.xmin+5)<1e-7 && std::abs(bounds.xmax-5)<1e-7 &&
          std::abs(bounds.ymin+5)<1e-7 && std::abs(bounds.ymax-5)<1e-7 &&
          std::abs(bounds.zmin)<1e-7 && std::abs(bounds.zmax-10)<1e-7,
          "revolve bounds must be [-5,-5,0]..[5,5,10]");
  occt_shape_release(tube);
  OcctShape *half_tube = nullptr;
  require_ok(occt_make_revolve(tube_profile, 4, 180, 0, 0, 0, OCCT_AXIS_Z, &half_tube),
             "make half analytic revolve");
  require_ok(occt_shape_volume(half_tube, &volume), "half revolve volume");
  require(std::abs(volume - 105.0 * pi) < 1e-5, "180-degree revolve volume must equal 105*pi mm^3");
  require_ok(occt_shape_bounds(half_tube, &bounds), "half revolve bounds");
  require(std::abs(bounds.xmin+5)<1e-7 && std::abs(bounds.xmax-5)<1e-7 &&
          std::abs(bounds.ymin)<1e-7 && std::abs(bounds.ymax-5)<1e-7 &&
          std::abs(bounds.zmin)<1e-7 && std::abs(bounds.zmax-10)<1e-7,
          "positive half revolve must occupy the +Y half-space");
  occt_shape_release(half_tube);

  // Malformed profiles fail at the C ABI boundary without repair or handles.
  const double zero_area[] = {0,0, 1,1, 2,2};
  const double self_crossing[] = {0,0, 4,4, 0,4, 3,0};
  const double repeated_close[] = {0,0, 4,0, 4,4, 0,0};
  const double not_finite[] = {0,0, 1,0, std::numeric_limits<double>::quiet_NaN(),1};
  const double two_points[] = {0,0, 1,0};
  struct InvalidProfile { const double *points; size_t count; const char *label; };
  for (const auto &sample : std::vector<InvalidProfile>{{zero_area,3,"zero-area profile"},
       {self_crossing,4,"self-intersecting profile"}, {repeated_close,4,"repeated closing point"},
       {not_finite,3,"non-finite profile"}, {two_points,2,"too few profile points"}}) {
    OcctShape *rejected = nullptr;
    require(occt_make_prism(sample.points,sample.count,2,0,0,0,OCCT_AXIS_Z,&rejected)
            == OCCT_BRIDGE_INVALID_ARGUMENT, sample.label);
    require(rejected == nullptr, "rejected prism profile must not return a handle");
  }
  OcctShape *negative_tube = nullptr;
  require_ok(occt_make_revolve(tube_profile,4,-90,0,0,0,OCCT_AXIS_Z,&negative_tube),"negative quarter revolve");
  require_ok(occt_shape_volume(negative_tube,&volume),"negative quarter revolve volume");
  require(std::abs(volume-52.5*pi)<1e-5,"negative quarter revolve must have positive quarter volume");
  occt_shape_release(negative_tube);
  for (const double bad_angle : {0.0,-360.1,360.1}) {
    OcctShape *rejected = nullptr;
    require(occt_make_revolve(tube_profile,4,bad_angle,0,0,0,OCCT_AXIS_Z,&rejected)
            == OCCT_BRIDGE_INVALID_ARGUMENT, "zero or magnitude above 360 revolve angle must be rejected");
    require(rejected == nullptr, "rejected revolve must not return a handle");
  }
  const double negative_radius[] = {-1,0, 2,0, 2,2, -1,2};
  OcctShape *rejected = nullptr;
  require(occt_make_revolve(negative_radius,4,360,0,0,0,OCCT_AXIS_Z,&rejected)
          == OCCT_BRIDGE_INVALID_ARGUMENT, "negative revolve radius must be rejected");
  require(rejected == nullptr, "negative-radius revolve must not return a handle");

  std::vector<OcctShape *> cutter_storage;
  std::vector<const OcctShape *> cutters;
  for (double x : {-30.0, 0.0, 30.0}) {
    for (double y : {-20.0, 0.0, 20.0}) {
      OcctShape *through = nullptr, *recess = nullptr;
      require_ok(occt_make_cylinder(4, 20, x, y, 0, 0, 0, 1, &through), "make through-hole cutter");
      require_ok(occt_make_cylinder(6, 2, x, y, 18, 0, 0, 1, &recess), "make recess cutter");
      cutter_storage.push_back(through); cutter_storage.push_back(recess);
      cutters.push_back(through); cutters.push_back(recess);
    }
  }
  OcctShape *part = nullptr;
  require_ok(occt_cut(box, cutters.data(), cutters.size(), &part), "cut 9-hole recessed solid");
  require_ok(occt_shape_is_closed_solid(part, &closed), "nine-hole closed check");
  require(closed == 1, "9-hole recessed part must be one valid closed solid");
  require_ok(occt_shape_is_valid(part, &closed), "nine-hole valid check");
  require(closed == 1, "9-hole recessed part must pass BRepCheck_Analyzer");
  require_ok(occt_shape_bounds(part, &bounds), "nine-hole bounds");
  require(std::abs(bounds.xmin + 50) < 1e-7 && std::abs(bounds.xmax - 50) < 1e-7 &&
          std::abs(bounds.ymin + 40) < 1e-7 && std::abs(bounds.ymax - 40) < 1e-7 &&
          std::abs(bounds.zmin) < 1e-7 && std::abs(bounds.zmax - 20) < 1e-7,
          "9-hole bounds must match the original stock");
  require_ok(occt_shape_volume(part, &volume), "nine-hole volume");
  const double expected = 160000.0 - 9.0 * (pi * 4.0 * 4.0 * 20.0 +
    pi * (6.0 * 6.0 - 4.0 * 4.0) * 2.0);
  require(std::abs(volume - expected) < 1e-5, "9-hole volume must match analytic cutter volume");

  // Exact line material ranges. These report only material occupancy on each
  // tested ray; they do not independently certify feature/hole dimensions.
  require_intervals(part,15,0,-5,0,0,3,0,30,1e-7,{{5,25}},
                    "solid front-center ray must return its material interval");
  require_intervals(part,0,0,-5,0,0,1,0,30,1e-7,{},
                    "through-hole center ray must be empty");
  require_intervals(part,100,0,-5,0,0,1,0,30,1e-7,{},
                    "external ray must be empty");
  require_intervals(box,-60,0,10,1,0,0,0,20,1e-7,{{10,20}},
                    "line interval must clip material span to requested range");

  // A blind bore leaves material behind its bottom.
  OcctShape *blind_tool=nullptr,*blind_part=nullptr;
  require_ok(occt_make_cylinder(4,10,15,0,10,0,0,1,&blind_tool),"make blind-hole cutter");
  const OcctShape *blind_tools[]={blind_tool};
  require_ok(occt_cut(box,blind_tools,1,&blind_part),"cut blind hole");
  require_intervals(blind_part,15,0,-5,0,0,1,0,30,1e-7,{{5,15}},
                    "blind-hole ray must retain material beneath its bottom");
  occt_shape_release(blind_part);
  occt_shape_release(blind_tool);

  // Coincident/tangent events from adjacent faces must not create a false span.
  require_intervals(box,-60,40,10,2,0,0,0,120,1e-6,{},
                    "edge-tangent line must not produce a spurious material span");

  OcctShape *small_a=nullptr,*small_b=nullptr,*disjoint=nullptr;
  require_ok(occt_make_box(4,4,4,-10,0,0,&small_a),"make first disjoint solid");
  require_ok(occt_make_box(4,4,4,10,0,0,&small_b),"make second disjoint solid");
  const OcctShape *disjoint_parts[]={small_a,small_b};
  require_ok(occt_fuse(disjoint_parts,2,&disjoint),"combine disjoint solids");
  require_intervals(disjoint,-20,0,0,5,0,0,0,40,1e-7,{{8,12},{28,32}},
                    "line query must return intervals for each disjoint solid");
  occt_shape_release(disjoint);
  occt_shape_release(small_a);
  occt_shape_release(small_b);

  OcctMaterialInterval *bad_intervals=reinterpret_cast<OcctMaterialInterval*>(1);
  size_t bad_count=99;
  require(occt_shape_line_material_intervals(box,0,0,0,0,0,0,0,10,1e-7,&bad_intervals,&bad_count)
          ==OCCT_BRIDGE_INVALID_ARGUMENT && bad_intervals==nullptr && bad_count==0,
          "zero direction must be rejected and reset outputs");
  bad_intervals=reinterpret_cast<OcctMaterialInterval*>(1); bad_count=99;
  require(occt_shape_line_material_intervals(box,0,0,0,0,0,1,10,0,1e-7,&bad_intervals,&bad_count)
          ==OCCT_BRIDGE_INVALID_ARGUMENT && bad_intervals==nullptr && bad_count==0,
          "invalid line range must be rejected and reset outputs");
  bad_intervals=reinterpret_cast<OcctMaterialInterval*>(1); bad_count=99;
  require(occt_shape_line_material_intervals(box,std::numeric_limits<double>::infinity(),0,0,
          0,0,1,0,10,1e-7,&bad_intervals,&bad_count)==OCCT_BRIDGE_INVALID_ARGUMENT &&
          bad_intervals==nullptr && bad_count==0,
          "non-finite line input must be rejected and reset outputs");
  bad_intervals=reinterpret_cast<OcctMaterialInterval*>(1); bad_count=99;
  require(occt_shape_line_material_intervals(box,0,0,0,0,0,1,0,10,0,&bad_intervals,&bad_count)
          ==OCCT_BRIDGE_INVALID_ARGUMENT && bad_intervals==nullptr && bad_count==0,
          "non-positive tolerance must be rejected and reset outputs");
  OcctMesh mesh{};
  require_ok(occt_shape_tessellate(part, 0.1, 0.5, &mesh), "nine-hole tessellation");
  require(mesh.vertex_count >= 100, "9-hole tessellation must return triangles");

  std::printf("OCCT %s | box: closed=1 volume=%.9f bounds=(%.9f,%.9f,%.9f)-(%.9f,%.9f,%.9f) | 9-hole-recess: closed=1 volume=%.9f expected=%.9f triangles=%zu\n",
    occt_version(), 160000.0, -50.0, -40.0, 0.0, 50.0, 40.0, 20.0, volume, expected, mesh.vertex_count / 3);
  occt_mesh_release(&mesh);
  occt_shape_release(part);
  for (auto *cutter : cutter_storage) occt_shape_release(cutter);

  // Topology fixture requested for F4: one recessed rectangular channel and
  // nine through holes, with large/small/large diameters across each row.
  OcctShape *channel = nullptr;
  require_ok(occt_make_box(80, 12, 4, 0, 0, 18, &channel), "make rectangular recess cutter");
  std::vector<OcctShape *> topology_cutters{channel};
  std::vector<const OcctShape *> topology_tools{channel};
  const double xs[] = {-30.0, 0.0, 30.0};
  const double ys[] = {-25.0, 0.0, 25.0};
  const double radii[] = {5.0, 3.0, 5.0};
  for (double y : ys) {
    for (size_t col = 0; col < 3; ++col) {
      OcctShape *hole = nullptr;
      require_ok(occt_make_cylinder(radii[col], 20, xs[col], y, 0, 0, 0, 1, &hole),
                 "make large/small/large through-hole cutter");
      topology_cutters.push_back(hole);
      topology_tools.push_back(hole);
    }
  }
  OcctShape *recessed = nullptr;
  require_ok(occt_cut(box, topology_tools.data(), topology_tools.size(), &recessed),
             "cut rectangular recess and 9 through holes");
  require_ok(occt_shape_is_closed_solid(recessed, &closed), "recessed 9-hole closed check");
  require(closed == 1, "recessed 9-hole part must be one valid closed solid");
  require_ok(occt_shape_analyze(recessed, &analysis), "recessed 9-hole analysis");
  require(analysis.valid == 1 && analysis.solid_count == 1 && analysis.closed_solid_count == 1,
          "recessed 9-hole analysis must report one valid closed solid");
  require_ok(occt_shape_bounds(recessed, &bounds), "recessed 9-hole bounds");
  require(std::abs(bounds.xmin + 50) < 1e-7 && std::abs(bounds.xmax - 50) < 1e-7 &&
          std::abs(bounds.ymin + 40) < 1e-7 && std::abs(bounds.ymax - 40) < 1e-7 &&
          std::abs(bounds.zmin) < 1e-7 && std::abs(bounds.zmax - 20) < 1e-7,
          "recessed 9-hole bounds must match the stock");
  require_ok(occt_shape_volume(recessed, &volume), "recessed 9-hole volume");
  const double recess_expected = 160000.0 - 80.0 * 12.0 * 4.0 -
    3.0 * pi * (5.0 * 5.0 + 3.0 * 3.0 + 5.0 * 5.0) * 20.0 +
    4.0 * pi * (5.0 * 5.0 + 3.0 * 3.0 + 5.0 * 5.0);
  require(std::abs(volume - recess_expected) < 1e-5,
          "recessed 9-hole volume must match analytic cutter union");
  require_intervals(recessed,15,0,-5,0,0,1,0,30,1e-7,{{5,21}},
                    "channel center ray must stop at recess floor");
  require_intervals(recessed,15,10,-5,0,0,2,0,30,1e-7,{{5,25}},
                    "ray outside channel must retain full stock depth");
  require_intervals(recessed,0,0,-5,0,0,1,0,30,1e-7,{},
                    "hole ray through channel floor must remain empty");
  require_ok(occt_shape_tessellate(recessed, 0.1, 0.5, &mesh), "recessed 9-hole tessellation");
  require(mesh.vertex_count >= 100, "recessed 9-hole tessellation must return triangles");
  std::printf("rectangular-recess + 9-hole: closed=1 volume=%.9f expected=%.9f triangles=%zu\n",
              volume, recess_expected, mesh.vertex_count / 3);
  occt_mesh_release(&mesh);
  occt_shape_release(recessed);
  for (auto *cutter : topology_cutters) occt_shape_release(cutter);
  occt_shape_release(box);
  return 0;
}
