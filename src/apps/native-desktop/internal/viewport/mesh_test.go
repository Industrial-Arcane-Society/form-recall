package viewport

import (
	"encoding/binary"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"
)

// The fixture has an indexed square, smooth vertex normals and a scene node.
func fixture() (map[string]any, []byte) {
	positions := []float32{0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0}
	data := make([]byte, 108)
	for i, value := range positions {
		binary.LittleEndian.PutUint32(data[i*4:], math.Float32bits(value))
	}
	for i := 0; i < 4; i++ {
		binary.LittleEndian.PutUint32(data[48+i*12+8:], math.Float32bits(1))
	}
	for i, value := range []uint16{0, 1, 2, 0, 2, 3} {
		binary.LittleEndian.PutUint16(data[96+i*2:], value)
	}
	doc := map[string]any{
		"asset":   map[string]any{"version": "2.0"},
		"buffers": []any{map[string]any{"byteLength": len(data)}},
		"bufferViews": []any{
			map[string]any{"buffer": 0, "byteLength": 48},
			map[string]any{"buffer": 0, "byteOffset": 48, "byteLength": 48},
			map[string]any{"buffer": 0, "byteOffset": 96, "byteLength": 12},
		},
		"accessors": []any{
			map[string]any{"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
			map[string]any{"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC3"},
			map[string]any{"bufferView": 2, "componentType": 5123, "count": 6, "type": "SCALAR"},
		},
		"meshes":    []any{map[string]any{"primitives": []any{map[string]any{"attributes": map[string]any{"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}}}},
		"materials": []any{map[string]any{"pbrMetallicRoughness": map[string]any{"baseColorFactor": []float64{0.4, 0.6, 0.8, 1}}}},
		"nodes":     []any{map[string]any{"mesh": 0}},
		"scenes":    []any{map[string]any{"nodes": []int{0}}}, "scene": 0,
	}
	return doc, data
}

func encodeFixture(t testing.TB, doc map[string]any, data []byte) []byte {
	t.Helper()
	encoded, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	for len(encoded)%4 != 0 {
		encoded = append(encoded, ' ')
	}
	data = append([]byte(nil), data...)
	for len(data)%4 != 0 {
		data = append(data, 0)
	}
	result := make([]byte, 28+len(encoded)+len(data))
	copy(result, "glTF")
	binary.LittleEndian.PutUint32(result[4:], 2)
	binary.LittleEndian.PutUint32(result[8:], uint32(len(result)))
	binary.LittleEndian.PutUint32(result[12:], uint32(len(encoded)))
	binary.LittleEndian.PutUint32(result[16:], 0x4e4f534a)
	copy(result[20:], encoded)
	offset := 20 + len(encoded)
	binary.LittleEndian.PutUint32(result[offset:], uint32(len(data)))
	binary.LittleEndian.PutUint32(result[offset+4:], 0x004e4942)
	copy(result[offset+8:], data)
	return result
}

func TestGLBIndexedSceneAndEdges(t *testing.T) {
	doc, data := fixture()
	doc["nodes"].([]any)[0].(map[string]any)["translation"] = []float64{10, 20, 30}
	doc["nodes"].([]any)[0].(map[string]any)["scale"] = []float64{2, 3, 4}
	mesh, err := DecodeGLB(encodeFixture(t, doc, data))
	if err != nil {
		t.Fatal(err)
	}
	if mesh.Triangles != 2 || mesh.Parts != 1 || mesh.Min != (Vec3{10, 20, 30}) || mesh.Max != (Vec3{12, 23, 30}) {
		t.Fatalf("unexpected scene %+v", mesh)
	}
	if len(mesh.Edges) != 24 {
		t.Fatalf("coplanar diagonal should be excluded, got %d edges", len(mesh.Edges)/6)
	}
	if mesh.Vertices[6] != 0.4 || mesh.Vertices[8] != 0.8 || mesh.Vertices[5] != 1 {
		t.Fatal("material or normals lost")
	}
}

func TestGLBPreservesSmoothNormalsUnderScale(t *testing.T) {
	doc, data := fixture()
	for i := 0; i < 12; i++ {
		binary.LittleEndian.PutUint32(data[48+i*4:], math.Float32bits(float32(1/math.Sqrt(3))))
	}
	doc["nodes"].([]any)[0].(map[string]any)["scale"] = []float64{2, 3, 4}
	mesh, err := DecodeGLB(encodeFixture(t, doc, data))
	if err != nil {
		t.Fatal(err)
	}
	expected := (Vec3{0.5, 1.0 / 3, 0.25}).Unit()
	actual := Vec3{float64(mesh.Vertices[3]), float64(mesh.Vertices[4]), float64(mesh.Vertices[5])}
	if actual.Sub(expected).Length() > 1e-6 {
		t.Fatalf("normal %v, want %v", actual, expected)
	}
}

func TestGLBRejectsMalformedGeometry(t *testing.T) {
	cases := map[string]func(map[string]any, []byte){
		"out of range index": func(_ map[string]any, b []byte) { binary.LittleEndian.PutUint16(b[96:], 999) },
		"infinite vertex": func(_ map[string]any, b []byte) {
			binary.LittleEndian.PutUint32(b, math.Float32bits(float32(math.Inf(1))))
		},
		"overflow offset": func(d map[string]any, _ []byte) {
			d["bufferViews"].([]any)[0].(map[string]any)["byteOffset"] = int64(math.MaxInt64)
		},
		"overflow stride": func(d map[string]any, _ []byte) {
			d["bufferViews"].([]any)[0].(map[string]any)["byteStride"] = int64(math.MaxInt64)
		},
		"short binary":   func(d map[string]any, _ []byte) { d["buffers"].([]any)[0].(map[string]any)["byteLength"] = 200 },
		"cycle":          func(d map[string]any, _ []byte) { d["nodes"].([]any)[0].(map[string]any)["children"] = []int{0} },
		"invalid matrix": func(d map[string]any, _ []byte) { d["nodes"].([]any)[0].(map[string]any)["matrix"] = []int{1, 2} },
		"overflow scale": func(d map[string]any, _ []byte) {
			d["nodes"].([]any)[0].(map[string]any)["scale"] = []float64{1e100, 1e100, 1e100}
		},
		"external buffer": func(d map[string]any, _ []byte) { d["buffers"].([]any)[0].(map[string]any)["uri"] = "external.bin" },
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			doc, data := fixture()
			change(doc, data)
			if _, err := DecodeGLB(encodeFixture(t, doc, data)); err == nil {
				t.Fatal("malformed geometry accepted")
			}
		})
	}
}

func TestGLBLoadFileAndTruncation(t *testing.T) {
	doc, data := fixture()
	encoded := encodeFixture(t, doc, data)
	path := filepath.Join(t.TempDir(), "fixture.glb")
	if err := os.WriteFile(path, encoded, 0600); err != nil {
		t.Fatal(err)
	}
	mesh, err := LoadGLB(path)
	if err != nil || mesh.Source != path {
		t.Fatalf("load file %v", err)
	}
	for end := 0; end < len(encoded); end++ {
		if _, err := DecodeGLB(encoded[:end]); err == nil {
			t.Fatalf("truncation at %d accepted", end)
		}
	}
}

func BenchmarkDecodeGLB(b *testing.B) {
	doc, data := fixture()
	encoded := encodeFixture(b, doc, data)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, err := DecodeGLB(encoded); err != nil {
			b.Fatal(err)
		}
	}
}
