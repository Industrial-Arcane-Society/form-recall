#ifndef OCCT_BRIDGE_H
#define OCCT_BRIDGE_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#  if defined(OCCT_BRIDGE_BUILD)
#    define OCCT_BRIDGE_API __declspec(dllexport)
#  else
#    define OCCT_BRIDGE_API __declspec(dllimport)
#  endif
#else
#  define OCCT_BRIDGE_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct OcctShape OcctShape;
typedef int32_t OcctStatus;
typedef int32_t OcctAxis;

enum {
  OCCT_AXIS_X = 0,
  OCCT_AXIS_Y = 1,
  OCCT_AXIS_Z = 2
};

enum {
  OCCT_BRIDGE_OK = 0,
  OCCT_BRIDGE_INVALID_ARGUMENT = 1,
  OCCT_BRIDGE_KERNEL_ERROR = 2,
  OCCT_BRIDGE_OUT_OF_MEMORY = 3
};

typedef struct OcctBounds {
  double xmin, ymin, zmin;
  double xmax, ymax, zmax;
} OcctBounds;

typedef struct OcctShapeAnalysis {
  int32_t valid;
  size_t solid_count;
  size_t closed_solid_count;
} OcctShapeAnalysis;

/* Triangle soup: xyz and normal are tightly packed triples; vertex_count is
   the number of triples. Release with occt_mesh_release. */
typedef struct OcctMesh {
  double *xyz;
  double *normals;
  size_t vertex_count;
} OcctMesh;

typedef struct OcctMaterialInterval {
  double t_start;
  double t_end;
} OcctMaterialInterval;

/* One trimmed cylindrical B-Rep face. v_min/v_max are axial parameters in
   model units; u_min/u_max are angular parameters in radians. normal is the
   oriented face normal sampled at the UV midpoint. */
typedef struct OcctCylinderFace {
  double radius;
  double axis_origin[3];
  double axis_direction[3];
  double v_min, v_max;
  double u_min, u_max;
  double surface_point[3];
  double normal[3];
} OcctCylinderFace;

/* Error text remains valid until the next bridge call on the same thread. */
OCCT_BRIDGE_API const char *occt_last_error(void);
OCCT_BRIDGE_API const char *occt_version(void);
OCCT_BRIDGE_API uint32_t occt_bridge_abi_version(void);
OCCT_BRIDGE_API void occt_shape_release(OcctShape *shape);

OCCT_BRIDGE_API OcctStatus occt_make_box(double dx, double dy, double dz,
  double cx, double cy, double cz, OcctShape **out_shape);
OCCT_BRIDGE_API OcctStatus occt_make_cylinder(double radius, double height,
  double bx, double by, double bz, double ax, double ay, double az,
  OcctShape **out_shape);
OCCT_BRIDGE_API OcctStatus occt_make_sphere(double radius,
  double cx, double cy, double cz, OcctShape **out_shape);
/* Axes are positive global X/Y/Z and profile coordinates follow the
   right-handed bases X=(Y,Z), Y=(Z,X), Z=(X,Y). */
OCCT_BRIDGE_API OcctStatus occt_make_cone(double bottom_radius, double top_radius,
  double height, double bx, double by, double bz, OcctAxis axis,
  OcctShape **out_shape);
/* xy_pairs is [u0,v0,u1,v1,...]; omit a repeated final copy of point zero. */
OCCT_BRIDGE_API OcctStatus occt_make_prism(const double *uv_pairs,
  size_t point_count, double height, double ox, double oy, double oz,
  OcctAxis axis, OcctShape **out_shape);
/* radius_axial_pairs is [r0,z0,r1,z1,...]; angle is in (0,360] degrees. */
OCCT_BRIDGE_API OcctStatus occt_make_revolve(const double *radius_axial_pairs,
  size_t point_count, double angle_degrees, double ox, double oy, double oz,
  OcctAxis axis, OcctShape **out_shape);
/* Build one closed solid from triangle soup. xyz is tightly packed xyz triples;
   vertex_count is the number of vertices and must be a multiple of three. */
OCCT_BRIDGE_API OcctStatus occt_make_solid_from_triangles(const double *xyz,
  size_t vertex_count, double sewing_tolerance, OcctShape **out_shape);
OCCT_BRIDGE_API OcctStatus occt_fuse(const OcctShape *const *shapes,
  size_t count, OcctShape **out_shape);
OCCT_BRIDGE_API OcctStatus occt_cut(const OcctShape *base,
  const OcctShape *const *cutters, size_t count, OcctShape **out_shape);
/* Matrix is row-major 4x4 and transforms homogeneous points; output is new. */
OCCT_BRIDGE_API OcctStatus occt_transform(const OcctShape *shape,
  const double matrix4x4[16], OcctShape **out_shape);
OCCT_BRIDGE_API OcctStatus occt_shape_is_valid(const OcctShape *shape,
  int32_t *out_valid);
OCCT_BRIDGE_API OcctStatus occt_shape_is_closed_solid(const OcctShape *shape,
  int32_t *out_closed_solid);
OCCT_BRIDGE_API OcctStatus occt_shape_analyze(const OcctShape *shape,
  OcctShapeAnalysis *out_analysis);
OCCT_BRIDGE_API OcctStatus occt_shape_bounds(const OcctShape *shape,
  OcctBounds *out_bounds);
OCCT_BRIDGE_API OcctStatus occt_shape_volume(const OcctShape *shape,
  double *out_volume);
/* Exact line/face events clipped to [tmin,tmax], then midpoint-classified.
   axis is normalized internally; t is measured in the shape's length unit.
   Release returned storage with occt_material_intervals_release. */
OCCT_BRIDGE_API OcctStatus occt_shape_line_material_intervals(
  const OcctShape *shape, double ox, double oy, double oz,
  double ax, double ay, double az, double tmin, double tmax, double tolerance,
  OcctMaterialInterval **out_intervals, size_t *out_count);
OCCT_BRIDGE_API void occt_material_intervals_release(OcctMaterialInterval *intervals);
/* Returns one record per trimmed cylindrical face (partial cylinders may
   therefore yield several records). Release with the matching function. */
OCCT_BRIDGE_API OcctStatus occt_shape_cylindrical_faces(
  const OcctShape *shape, OcctCylinderFace **out_faces, size_t *out_count);
OCCT_BRIDGE_API void occt_cylindrical_faces_release(OcctCylinderFace *faces);
OCCT_BRIDGE_API OcctStatus occt_shape_tessellate(const OcctShape *shape,
  double linear_deflection, double angular_deflection, OcctMesh *out_mesh);
OCCT_BRIDGE_API void occt_mesh_release(OcctMesh *mesh);

#ifdef __cplusplus
}
#endif
#endif
