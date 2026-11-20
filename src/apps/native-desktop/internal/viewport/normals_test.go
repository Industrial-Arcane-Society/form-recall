package viewport

import (
	"math"
	"testing"
)

func TestSmoothFacetedNormalsKeepsSharpRims(t *testing.T) {
	for _, degrees := range []float64{10, 90} {
		angle := degrees * math.Pi / 180
		mesh := &Mesh{Min: Vec3{-1, -1, -1}, Max: Vec3{1, 1, 1}}
		triangles := [][3]Vec3{
			{{0, 0, 0}, {1, 0, 0}, {0, 1, 0}},
			{{1, 0, 0}, {0, 0, 0}, {0, -math.Cos(angle), math.Sin(angle)}},
		}
		for _, points := range triangles {
			normal := points[1].Sub(points[0]).Cross(points[2].Sub(points[0])).Unit()
			for _, p := range points {
				mesh.Vertices = append(mesh.Vertices, float32(p.X), float32(p.Y), float32(p.Z), float32(normal.X), float32(normal.Y), float32(normal.Z), 1, 1, 1, 1)
			}
		}
		mesh.smoothFacetedNormals(0)
		a := Vec3{float64(mesh.Vertices[3]), float64(mesh.Vertices[4]), float64(mesh.Vertices[5])}
		b := Vec3{float64(mesh.Vertices[43]), float64(mesh.Vertices[44]), float64(mesh.Vertices[45])}
		if degrees == 10 && a.Sub(b).Length() > 1e-6 {
			t.Fatalf("shallow curved join still faceted %v %v", a, b)
		}
		if degrees == 90 && math.Abs(a.Dot(b)) > 1e-6 {
			t.Fatalf("sharp edge was smoothed %v %v", a, b)
		}
	}
}
