package viewport

import "math"

// The OCCT bridge currently exports each triangle's face normal. Blend only
// those faceted corners across shallow joins. Authored smooth normals survive,
// and the same crease threshold used by the edge renderer preserves sharp rims.
func (m *Mesh) smoothFacetedNormals(start int) {
	type positionKey [3]int64
	type contribution struct {
		normal Vec3
		angle  float64
	}
	adjacent := make(map[positionKey][]contribution)
	tolerance := math.Max(m.Max.Sub(m.Min).Length()*1e-7, 1e-8)
	position := func(offset int) Vec3 {
		return Vec3{float64(m.Vertices[offset]), float64(m.Vertices[offset+1]), float64(m.Vertices[offset+2])}
	}
	key := func(p Vec3) positionKey {
		p = p.Sub(m.Min)
		return positionKey{int64(math.Round(p.X / tolerance)), int64(math.Round(p.Y / tolerance)), int64(math.Round(p.Z / tolerance))}
	}
	faces := make([]Vec3, (len(m.Vertices)-start)/30)
	for offset := start; offset < len(m.Vertices); offset += 30 {
		points := [3]Vec3{position(offset), position(offset + 10), position(offset + 20)}
		normal := points[1].Sub(points[0]).Cross(points[2].Sub(points[0])).Unit()
		faces[(offset-start)/30] = normal
		for corner, p := range points {
			a := points[(corner+1)%3].Sub(p).Unit()
			b := points[(corner+2)%3].Sub(p).Unit()
			angle := math.Acos(math.Max(-1, math.Min(1, a.Dot(b))))
			id := key(p)
			adjacent[id] = append(adjacent[id], contribution{normal, angle})
		}
	}
	for offset := start; offset < len(m.Vertices); offset += 10 {
		face := faces[(offset-start)/30]
		normal := Vec3{float64(m.Vertices[offset+3]), float64(m.Vertices[offset+4]), float64(m.Vertices[offset+5])}
		if normal.Dot(face) < 0.99999 {
			continue
		}
		var sum Vec3
		for _, candidate := range adjacent[key(position(offset))] {
			if face.Dot(candidate.normal) >= 0.94 {
				sum = sum.Add(candidate.normal.Mul(candidate.angle))
			}
		}
		normal = sum.Unit()
		m.Vertices[offset+3], m.Vertices[offset+4], m.Vertices[offset+5] = float32(normal.X), float32(normal.Y), float32(normal.Z)
	}
}
