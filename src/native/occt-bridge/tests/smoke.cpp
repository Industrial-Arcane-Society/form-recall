#include "occt_bridge.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
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

int main() {
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
  constexpr double pi = 3.141592653589793238462643383279502884;
  const double expected = 160000.0 - 9.0 * (pi * 4.0 * 4.0 * 20.0 +
    pi * (6.0 * 6.0 - 4.0 * 4.0) * 2.0);
  require(std::abs(volume - expected) < 1e-5, "9-hole volume must match analytic cutter volume");
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
