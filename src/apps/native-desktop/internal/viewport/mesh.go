package viewport

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
)

type Vec3 struct{ X, Y, Z float64 }

func (a Vec3) Add(b Vec3) Vec3    { return Vec3{a.X + b.X, a.Y + b.Y, a.Z + b.Z} }
func (a Vec3) Sub(b Vec3) Vec3    { return Vec3{a.X - b.X, a.Y - b.Y, a.Z - b.Z} }
func (a Vec3) Mul(s float64) Vec3 { return Vec3{a.X * s, a.Y * s, a.Z * s} }
func (a Vec3) Dot(b Vec3) float64 { return a.X*b.X + a.Y*b.Y + a.Z*b.Z }
func (a Vec3) Cross(b Vec3) Vec3 {
	return Vec3{a.Y*b.Z - a.Z*b.Y, a.Z*b.X - a.X*b.Z, a.X*b.Y - a.Y*b.X}
}
func (a Vec3) Length() float64 { return math.Sqrt(a.Dot(a)) }
func (a Vec3) Unit() Vec3 {
	if n := a.Length(); n > 1e-12 {
		return a.Mul(1 / n)
	}
	return Vec3{0, 0, 1}
}

// Mesh is immutable after loading. Vertices contain position, normal and RGBA.
type Mesh struct {
	Vertices  []float32
	Edges     []float32
	Min, Max  Vec3
	Triangles int
	Parts     int
	Source    string
}

type gltfDocument struct {
	Asset   struct{ Version string }
	Buffers []struct {
		URI        string
		ByteLength int
	}
	BufferViews []struct{ Buffer, ByteOffset, ByteLength, ByteStride int }
	Accessors   []struct {
		BufferView                       *int
		ByteOffset, ComponentType, Count int
		Type                             string
		Normalized                       bool
		Sparse                           json.RawMessage
	}
	Meshes []struct {
		Primitives []struct {
			Attributes map[string]int
			Indices    *int
			Material   *int
			Mode       *int
			Extensions map[string]json.RawMessage
		}
	}
	Materials []struct {
		PBR struct {
			BaseColorFactor []float64 `json:"baseColorFactor"`
		} `json:"pbrMetallicRoughness"`
	}
	Nodes []struct {
		Mesh                                 *int
		Children                             []int
		Matrix, Translation, Rotation, Scale []float64
	}
	Scenes []struct{ Nodes []int }
	Scene  *int
}

type matrix [16]float64

func identity() matrix { return matrix{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1} }
func multiply(a, b matrix) (out matrix) {
	for col := 0; col < 4; col++ {
		for row := 0; row < 4; row++ {
			for k := 0; k < 4; k++ {
				out[col*4+row] += a[k*4+row] * b[col*4+k]
			}
		}
	}
	return
}
func (m matrix) position(p Vec3) Vec3 {
	return Vec3{m[0]*p.X + m[4]*p.Y + m[8]*p.Z + m[12], m[1]*p.X + m[5]*p.Y + m[9]*p.Z + m[13], m[2]*p.X + m[6]*p.Y + m[10]*p.Z + m[14]}
}

// Normals use the inverse transpose, preserving lighting under nonuniform scale.
func (m matrix) normal(n Vec3) Vec3 {
	x, y, z := Vec3{m[0], m[1], m[2]}, Vec3{m[4], m[5], m[6]}, Vec3{m[8], m[9], m[10]}
	determinant := x.Dot(y.Cross(z))
	if math.Abs(determinant) < 1e-15 {
		return Vec3{}
	}
	return y.Cross(z).Mul(n.X).Add(z.Cross(x).Mul(n.Y)).Add(x.Cross(y).Mul(n.Z)).Mul(1 / determinant).Unit()
}

func LoadGLB(path string) (*Mesh, error) {
	info, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if info.Size() > 256<<20 {
		return nil, errors.New("模型超过 256 MiB 加载上限")
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	b, err := io.ReadAll(io.LimitReader(file, (256<<20)+1))
	if err != nil {
		return nil, err
	}
	m, err := DecodeGLB(b)
	if err == nil {
		m.Source = path
	}
	return m, err
}

// DecodeGLB accepts glTF 2 binary meshes, including indexed primitives and node transforms.
func DecodeGLB(b []byte) (*Mesh, error) {
	if len(b) > 256<<20 {
		return nil, errors.New("模型超过 256 MiB 加载上限")
	}
	if len(b) < 20 || string(b[:4]) != "glTF" || binary.LittleEndian.Uint32(b[4:8]) != 2 || uint64(binary.LittleEndian.Uint32(b[8:12])) != uint64(len(b)) {
		return nil, errors.New("无效的 glTF 2 GLB 文件头")
	}
	var doc gltfDocument
	var data []byte
	jsonFound := false
	for offset := 12; offset < len(b); {
		if len(b)-offset < 8 {
			return nil, errors.New("GLB 分块头被截断")
		}
		n := int(binary.LittleEndian.Uint32(b[offset : offset+4]))
		kind := binary.LittleEndian.Uint32(b[offset+4 : offset+8])
		offset += 8
		if n < 0 || n > len(b)-offset {
			return nil, errors.New("GLB 分块超出文件长度")
		}
		switch kind {
		case 0x4e4f534a:
			if jsonFound {
				return nil, errors.New("GLB 包含重复 JSON 块")
			}
			if err := json.Unmarshal(b[offset:offset+n], &doc); err != nil {
				return nil, fmt.Errorf("GLB JSON %w", err)
			}
			jsonFound = true
		case 0x004e4942:
			if data != nil {
				return nil, errors.New("GLB 包含重复二进制块")
			}
			data = b[offset : offset+n]
		}
		offset += n
	}
	if !jsonFound || doc.Asset.Version != "2.0" || len(data) == 0 {
		return nil, errors.New("GLB 缺少 glTF 2.0 描述或二进制网格")
	}
	for _, buffer := range doc.Buffers {
		if buffer.URI != "" {
			return nil, errors.New("当前视口要求自包含 GLB，发现外部缓冲区")
		}
	}
	if len(doc.Buffers) != 1 || doc.Buffers[0].ByteLength < 1 || doc.Buffers[0].ByteLength > len(data) {
		return nil, errors.New("GLB 二进制缓冲区长度无效")
	}
	data = data[:doc.Buffers[0].ByteLength]
	mesh := &Mesh{Min: Vec3{math.Inf(1), math.Inf(1), math.Inf(1)}, Max: Vec3{math.Inf(-1), math.Inf(-1), math.Inf(-1)}}
	var walk func(int, matrix, map[int]bool) error
	walk = func(index int, parent matrix, ancestors map[int]bool) error {
		if len(ancestors) >= 256 {
			return errors.New("GLB 节点层级超过 256 层")
		}
		if index < 0 || index >= len(doc.Nodes) || ancestors[index] {
			return errors.New("GLB 节点引用无效或存在循环")
		}
		ancestors[index] = true
		defer delete(ancestors, index)
		n := doc.Nodes[index]
		if len(n.Matrix) != 0 && len(n.Matrix) != 16 || len(n.Translation) != 0 && len(n.Translation) != 3 || len(n.Scale) != 0 && len(n.Scale) != 3 || len(n.Rotation) != 0 && len(n.Rotation) != 4 {
			return errors.New("GLB 节点变换维度无效")
		}
		local := identity()
		if len(n.Matrix) == 16 {
			copy(local[:], n.Matrix)
		} else {
			s := Vec3{1, 1, 1}
			if len(n.Scale) == 3 {
				s = Vec3{n.Scale[0], n.Scale[1], n.Scale[2]}
			}
			if len(n.Rotation) == 4 {
				x, y, z, w := n.Rotation[0], n.Rotation[1], n.Rotation[2], n.Rotation[3]
				length := math.Sqrt(x*x + y*y + z*z + w*w)
				if length < 1e-12 || math.IsInf(length, 0) {
					return errors.New("GLB 节点旋转无效")
				}
				x, y, z, w = x/length, y/length, z/length, w/length
				local = matrix{1 - 2*y*y - 2*z*z, 2*x*y + 2*z*w, 2*x*z - 2*y*w, 0, 2*x*y - 2*z*w, 1 - 2*x*x - 2*z*z, 2*y*z + 2*x*w, 0, 2*x*z + 2*y*w, 2*y*z - 2*x*w, 1 - 2*x*x - 2*y*y, 0, 0, 0, 0, 1}
			}
			for i := 0; i < 3; i++ {
				local[i] *= s.X
				local[4+i] *= s.Y
				local[8+i] *= s.Z
			}
			if len(n.Translation) == 3 {
				copy(local[12:15], n.Translation)
			}
		}
		world := multiply(parent, local)
		for _, value := range world {
			if math.IsNaN(value) || math.IsInf(value, 0) {
				return errors.New("GLB 节点变换包含非有限数值")
			}
		}
		if n.Mesh != nil {
			if err := appendMesh(mesh, &doc, data, *n.Mesh, world); err != nil {
				return err
			}
		}
		for _, child := range n.Children {
			if err := walk(child, world, ancestors); err != nil {
				return err
			}
		}
		return nil
	}
	if len(doc.Nodes) > 0 {
		roots := []int{}
		if len(doc.Scenes) > 0 {
			scene := 0
			if doc.Scene != nil {
				scene = *doc.Scene
			}
			if scene < 0 || scene >= len(doc.Scenes) {
				return nil, errors.New("GLB 场景索引无效")
			}
			roots = doc.Scenes[scene].Nodes
		} else {
			children := map[int]bool{}
			for _, n := range doc.Nodes {
				for _, child := range n.Children {
					children[child] = true
				}
			}
			for i := range doc.Nodes {
				if !children[i] {
					roots = append(roots, i)
				}
			}
		}
		for _, root := range roots {
			if err := walk(root, identity(), map[int]bool{}); err != nil {
				return nil, err
			}
		}
	} else {
		for i := range doc.Meshes {
			if err := appendMesh(mesh, &doc, data, i, identity()); err != nil {
				return nil, err
			}
		}
	}
	if mesh.Triangles == 0 {
		return nil, errors.New("GLB 中没有可显示的三角形")
	}
	mesh.buildEdges()
	return mesh, nil
}

func accessor(doc *gltfDocument, data []byte, index, components int) ([]float64, error) {
	if index < 0 || index >= len(doc.Accessors) {
		return nil, errors.New("GLB accessor 索引无效")
	}
	a := doc.Accessors[index]
	types := map[string]int{"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
	if types[a.Type] != components || a.Count < 0 || a.Count > 6_000_000 || a.BufferView == nil || len(a.Sparse) > 0 {
		return nil, errors.New("GLB accessor 结构不受支持")
	}
	if *a.BufferView < 0 || *a.BufferView >= len(doc.BufferViews) {
		return nil, errors.New("GLB bufferView 索引无效")
	}
	view := doc.BufferViews[*a.BufferView]
	width := map[int]int{5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}[a.ComponentType]
	if width == 0 || view.Buffer != 0 || view.ByteOffset < 0 || view.ByteLength < 0 || a.ByteOffset < 0 {
		return nil, errors.New("GLB 缓冲区类型无效")
	}
	stride := view.ByteStride
	if stride == 0 {
		stride = width * components
	}
	if stride < width*components {
		return nil, errors.New("GLB 步长无效")
	}
	if view.ByteOffset > len(data) || view.ByteLength > len(data)-view.ByteOffset || a.ByteOffset > view.ByteLength {
		return nil, errors.New("GLB accessor 超出缓冲区")
	}
	available := view.ByteLength - a.ByteOffset
	if a.Count > 0 && (available < width*components || a.Count-1 > (available-width*components)/stride) {
		return nil, errors.New("GLB accessor 超出缓冲区")
	}
	start := view.ByteOffset + a.ByteOffset
	out := make([]float64, a.Count*components)
	for i := 0; i < a.Count; i++ {
		for c := 0; c < components; c++ {
			at := start + i*stride + c*width
			var v float64
			switch a.ComponentType {
			case 5120:
				v = float64(int8(data[at]))
				if a.Normalized {
					v = math.Max(-1, v/127)
				}
			case 5121:
				v = float64(data[at])
				if a.Normalized {
					v /= 255
				}
			case 5122:
				v = float64(int16(binary.LittleEndian.Uint16(data[at:])))
				if a.Normalized {
					v = math.Max(-1, v/32767)
				}
			case 5123:
				v = float64(binary.LittleEndian.Uint16(data[at:]))
				if a.Normalized {
					v /= 65535
				}
			case 5125:
				v = float64(binary.LittleEndian.Uint32(data[at:]))
			case 5126:
				v = float64(math.Float32frombits(binary.LittleEndian.Uint32(data[at:])))
			}
			if math.IsNaN(v) || math.IsInf(v, 0) {
				return nil, errors.New("GLB 包含非有限数值")
			}
			out[i*components+c] = v
		}
	}
	return out, nil
}

func appendMesh(out *Mesh, doc *gltfDocument, data []byte, index int, transform matrix) error {
	if index < 0 || index >= len(doc.Meshes) {
		return errors.New("GLB mesh 索引无效")
	}
	for _, primitive := range doc.Meshes[index].Primitives {
		primitiveStart := len(out.Vertices)
		if primitive.Mode != nil && *primitive.Mode != 4 {
			continue
		}
		if _, ok := primitive.Extensions["KHR_draco_mesh_compression"]; ok {
			return errors.New("当前视口不支持 Draco 压缩网格")
		}
		position, ok := primitive.Attributes["POSITION"]
		if !ok {
			return errors.New("GLB 三角形缺少 POSITION")
		}
		positions, err := accessor(doc, data, position, 3)
		if err != nil {
			return err
		}
		var normals []float64
		if normalIndex, ok := primitive.Attributes["NORMAL"]; ok {
			normals, err = accessor(doc, data, normalIndex, 3)
			if err != nil {
				return err
			}
			if len(normals) != len(positions) {
				return errors.New("GLB 顶点与法线数量不一致")
			}
		}
		count := len(positions) / 3
		indices := make([]float64, count)
		for i := range indices {
			indices[i] = float64(i)
		}
		if primitive.Indices != nil {
			indices, err = accessor(doc, data, *primitive.Indices, 1)
			if err != nil {
				return err
			}
		}
		if len(indices)%3 != 0 {
			return errors.New("GLB 三角形索引数量无效")
		}
		if out.Triangles+len(indices)/3 > 2_000_000 {
			return errors.New("模型超过 200 万三角形加载上限")
		}
		color := [4]float64{0.68, 0.70, 0.72, 1}
		if primitive.Material != nil && *primitive.Material >= 0 && *primitive.Material < len(doc.Materials) {
			c := doc.Materials[*primitive.Material].PBR.BaseColorFactor
			if len(c) == 4 {
				copy(color[:], c)
			}
		}
		for i := 0; i < len(indices); i += 3 {
			var points [3]Vec3
			var vertexNormals [3]Vec3
			for k := 0; k < 3; k++ {
				idx := int(indices[i+k])
				if float64(idx) != indices[i+k] || idx < 0 || idx >= count {
					return errors.New("GLB 三角形引用越界")
				}
				points[k] = transform.position(Vec3{positions[idx*3], positions[idx*3+1], positions[idx*3+2]})
				p := points[k]
				if math.IsNaN(p.X) || math.IsNaN(p.Y) || math.IsNaN(p.Z) || math.Abs(p.X) > math.MaxFloat32 || math.Abs(p.Y) > math.MaxFloat32 || math.Abs(p.Z) > math.MaxFloat32 {
					return errors.New("GLB 变换后的坐标超出 float32 范围")
				}
				if normals != nil {
					vertexNormals[k] = transform.normal(Vec3{normals[idx*3], normals[idx*3+1], normals[idx*3+2]})
				}
			}
			faceNormal := points[1].Sub(points[0]).Cross(points[2].Sub(points[0])).Unit()
			for k, p := range points {
				normal := vertexNormals[k]
				if normal.Length() < 0.5 {
					normal = faceNormal
				}
				out.Min = Vec3{math.Min(out.Min.X, p.X), math.Min(out.Min.Y, p.Y), math.Min(out.Min.Z, p.Z)}
				out.Max = Vec3{math.Max(out.Max.X, p.X), math.Max(out.Max.Y, p.Y), math.Max(out.Max.Z, p.Z)}
				out.Vertices = append(out.Vertices, float32(p.X), float32(p.Y), float32(p.Z), float32(normal.X), float32(normal.Y), float32(normal.Z), float32(color[0]), float32(color[1]), float32(color[2]), float32(color[3]))
			}
			out.Triangles++
		}
		out.smoothFacetedNormals(primitiveStart)
		out.Parts++
	}
	return nil
}

func (m *Mesh) buildEdges() {
	type key [6]int64
	type edge struct {
		a, b, normal Vec3
		sharp        bool
		count        int
	}
	edges := map[key]edge{}
	tolerance := math.Max(m.Max.Sub(m.Min).Length()*1e-7, 1e-8)
	for i := 0; i < len(m.Vertices); i += 30 {
		var p [3]Vec3
		for k := 0; k < 3; k++ {
			o := i + k*10
			p[k] = Vec3{float64(m.Vertices[o]), float64(m.Vertices[o+1]), float64(m.Vertices[o+2])}
		}
		n := p[1].Sub(p[0]).Cross(p[2].Sub(p[0])).Unit()
		for k := 0; k < 3; k++ {
			a, b := p[k], p[(k+1)%3]
			ra, rb := a.Sub(m.Min), b.Sub(m.Min)
			qa := [3]int64{int64(math.Round(ra.X / tolerance)), int64(math.Round(ra.Y / tolerance)), int64(math.Round(ra.Z / tolerance))}
			qb := [3]int64{int64(math.Round(rb.X / tolerance)), int64(math.Round(rb.Y / tolerance)), int64(math.Round(rb.Z / tolerance))}
			swap := false
			for j := 0; j < 3; j++ {
				if qa[j] != qb[j] {
					swap = qa[j] > qb[j]
					break
				}
			}
			if swap {
				qa, qb = qb, qa
				a, b = b, a
			}
			id := key{qa[0], qa[1], qa[2], qb[0], qb[1], qb[2]}
			old, found := edges[id]
			if found {
				old.count++
				old.sharp = old.sharp || old.normal.Dot(n) < 0.94
				edges[id] = old
			} else {
				edges[id] = edge{a: a, b: b, normal: n, count: 1}
			}
		}
	}
	for _, e := range edges {
		if e.count == 1 || e.sharp {
			m.Edges = append(m.Edges, float32(e.a.X), float32(e.a.Y), float32(e.a.Z), float32(e.b.X), float32(e.b.Y), float32(e.b.Z))
		}
	}
}
