package workbench

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"image/png"
	"math"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/engine"
	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/viewport"
	"github.com/egoist/mygo/ui"
)

// This opt-in test uses the production child process and GLB loader. All writable
// state lives in t.TempDir. FORMRECALL_TEST_OCCT names runtime-manifest.json.
// Set FORMRECALL_TEST_ENGINE, FORMRECALL_TEST_RESOURCES and FORMRECALL_TEST_OCCT,
// then run go test ./internal/workbench -run TestHostWorkbenchIntegration -count=1 -v.
func TestHostWorkbenchIntegration(t *testing.T) {
	if os.Getenv("FORMRECALL_TEST_ENGINE") == "" {
		t.Skip("set FORMRECALL_TEST_ENGINE, FORMRECALL_TEST_RESOURCES and FORMRECALL_TEST_OCCT to run the real host integration")
	}
	paths := make(map[string]string)
	for _, key := range []string{"FORMRECALL_TEST_ENGINE", "FORMRECALL_TEST_RESOURCES", "FORMRECALL_TEST_OCCT"} {
		value := os.Getenv(key)
		if value == "" {
			t.Fatalf("%s is required when host integration is enabled", key)
		}
		absolute, err := filepath.Abs(value)
		if err != nil {
			t.Fatal(err)
		}
		info, err := os.Stat(absolute)
		if err != nil {
			t.Fatalf("%s: %v", key, err)
		}
		if info.IsDir() != (key == "FORMRECALL_TEST_RESOURCES") {
			t.Fatalf("%s has the wrong file type: %s", key, absolute)
		}
		paths[key] = absolute
	}
	t.Setenv("AIMODELER_OCCT_MANIFEST", paths["FORMRECALL_TEST_OCCT"])
	t.Setenv("AIMODELER_RPC_TEST_ACP_FIXTURE", "")
	root := t.TempDir()
	options := engine.Options{Executable: paths["FORMRECALL_TEST_ENGINE"], Resources: paths["FORMRECALL_TEST_RESOURCES"],
		DataDir: filepath.Join(root, "data"), Config: filepath.Join(root, "config.json")}
	a, scene, tester, queue, closeHost := launchHostWorkbench(t, options)
	if object(a.runtime["occt"])["verified"] != true || len(a.parts) == 0 || len(a.samples) == 0 {
		t.Fatalf("production initialization did not load the runtime and catalogs: %v", a.runtime)
	}

	click(t, tester, "建模需求")
	tester.Type("创建 100×60×20 毫米长方体")
	click(t, tester, "生成模型")
	waitHostWorkbench(t, a, tester, queue)
	assertHostGeometry(t, a, scene, [3]float64{100, 60, 20})
	if !a.dirty || a.generation == 0 || !tester.HasText("模型已生成，可以在特征面板调整参数。") {
		t.Fatal("the Quick result did not reach the workbench")
	}
	initial := hostWorkbenchCall(t, a.service, "active_model_snapshot", Object{})
	assertHostCandidate(t, a, initial)
	if text(object(initial["document"])["mesh_path"]) != a.meshPath {
		t.Fatal("the workbench is displaying a different mesh from the active host snapshot")
	}
	initialFingerprint := text(initial["geometry_fingerprint"])
	t.Logf("Quick produced generation %d with %d real mesh triangles", a.generation, scene.Stats().Triangles)

	features := rows(a.document["features"])
	if len(features) != 1 || features[0]["kind"] != "box" {
		t.Fatalf("expected one editable box feature, got %v", features)
	}
	click(t, tester, text(features[0]["name"]))
	replaceHostInput(t, tester, "length", "120")
	click(t, tester, "应用参数")
	waitHostWorkbench(t, a, tester, queue)
	assertHostGeometry(t, a, scene, [3]float64{120, 60, 20})
	if text(a.document["geometry_fingerprint"]) == initialFingerprint || a.generation <= uint64(number(initial["generation"])) {
		t.Fatal("editing the scalar parameter did not publish a new geometry generation")
	}
	beforeArray := scene.Stats()
	oldCenter, ok := a.featureValues["center"].([]any)
	if !ok || len(oldCenter) != 3 {
		t.Fatalf("the real box did not expose its center array: %v", a.featureValues)
	}
	shift := viewport.Vec3{X: 12, Y: -7, Z: 5}
	newCenter := []float64{number(oldCenter[0]) + shift.X, number(oldCenter[1]) + shift.Y, number(oldCenter[2]) + shift.Z}
	encodedCenter, err := json.Marshal(newCenter)
	if err != nil {
		t.Fatal(err)
	}
	replaceHostInput(t, tester, "center", string(encodedCenter))
	click(t, tester, "应用参数")
	waitHostWorkbench(t, a, tester, queue)
	assertHostGeometry(t, a, scene, [3]float64{120, 60, 20})
	afterArray := scene.Stats()
	if afterArray.BoundsMin.Sub(beforeArray.BoundsMin).Sub(shift).Length() > 0.001 || afterArray.BoundsMax.Sub(beforeArray.BoundsMax).Sub(shift).Length() > 0.001 {
		t.Fatalf("array edit did not translate the real mesh by %v: before=%+v after=%+v", shift, beforeArray, afterArray)
	}
	assertHostCenter(t, a.document, newCenter)
	edited := hostWorkbenchCall(t, a.service, "active_model_snapshot", Object{})
	assertHostCandidate(t, a, edited)
	t.Logf("scalar and array inputs rebuilt generation %d with dimensions 120 × 60 × 20 mm and center %v", a.generation, newCenter)

	projectPath := filepath.Join(root, "edited.aimodel")
	a.savePath(projectPath, nil)
	waitHostWorkbench(t, a, tester, queue)
	if a.dirty || text(a.document["current_path"]) != projectPath {
		t.Fatalf("save did not update the workbench state: dirty=%v path=%v", a.dirty, a.document["current_path"])
	}
	projectBytes, err := os.ReadFile(projectPath)
	if err != nil {
		t.Fatal(err)
	}
	var project Object
	if err := json.Unmarshal(projectBytes, &project); err != nil {
		t.Fatal(err)
	}
	assertHostCenter(t, project, newCenter)
	if number(object(rows(project["features"])[0]["params"])["length"]) != 120 {
		t.Fatal("saved project lost the edited scalar parameter")
	}
	saved := hostWorkbenchCall(t, a.service, "active_model_snapshot", Object{})
	assertHostCandidate(t, a, saved)
	closeHost()
	a, scene, tester, queue, _ = launchHostWorkbench(t, options)
	assertHostGeometry(t, a, scene, [3]float64{120, 60, 20})
	assertHostCenter(t, a.document, newCenter)
	if a.generation != uint64(number(saved["generation"])) || text(a.document["geometry_fingerprint"]) != text(saved["geometry_fingerprint"]) || a.dirty {
		t.Fatal("restarting the real host did not restore the saved active model")
	}
	t.Logf("saved %d bytes and restored generation %d after a host restart", len(projectBytes), a.generation)

	a.newProject()
	waitHostWorkbench(t, a, tester, queue)
	if len(rows(a.document["features"])) != 0 || scene.Stats().Triangles != 0 {
		t.Fatal("new project did not clear the active model and viewport")
	}
	a.invoke("打开工程", "active_model_import_project", Object{"path": projectPath, "confirmed": true}, func(result any) {
		a.acceptSnapshot(result, false)
		a.refreshModel(false)
	})
	waitHostWorkbench(t, a, tester, queue)
	assertHostGeometry(t, a, scene, [3]float64{120, 60, 20})
	assertHostCenter(t, a.document, newCenter)
	if scene.Stats().BoundsMin.Sub(afterArray.BoundsMin).Length() > 0.001 || scene.Stats().BoundsMax.Sub(afterArray.BoundsMax).Length() > 0.001 {
		t.Fatal("reimporting the saved project changed its mesh bounds")
	}

	for _, kind := range []string{"stl", "obj", "report"} {
		path := filepath.Join(root, "export."+kind)
		a.invoke("导出模型", "active_model_export", Object{"kind": kind, "path": path}, nil)
		waitHostWorkbench(t, a, tester, queue)
		data, err := os.ReadFile(path)
		if err != nil || len(data) <= 80 {
			t.Fatalf("%s export is missing or empty: %v", kind, err)
		}
		switch kind {
		case "stl":
			assertHostSTL(t, data, scene.Stats())
		case "obj":
			if !bytes.Contains(data, []byte("\nv ")) || !bytes.Contains(data, []byte("\nf ")) {
				t.Fatal("OBJ export has no vertices or faces")
			}
		case "report":
			var report Object
			if err := json.Unmarshal(data, &report); err != nil || report["valid"] != true {
				t.Fatalf("geometry report is invalid: %v %s", err, data)
			}
		}
		t.Logf("verified %s export (%d bytes)", kind, len(data))
	}
	snapshotPath := filepath.Join(root, "four-views.png")
	a.invoke("导出模型", "active_model_export_snapshot", Object{"path": snapshotPath, "viewSet": "four"}, nil)
	waitHostWorkbench(t, a, tester, queue)
	snapshotBytes, err := os.ReadFile(snapshotPath)
	if err != nil {
		t.Fatal(err)
	}
	image, err := png.Decode(bytes.NewReader(snapshotBytes))
	if err != nil || image.Bounds().Dx() < 256 || image.Bounds().Dy() < 256 {
		t.Fatalf("four-view export is not a complete PNG: %v", err)
	}
	t.Logf("verified four-view PNG (%d × %d)", image.Bounds().Dx(), image.Bounds().Dy())

	// Saved files and direct document results cannot certify model acceptance.
	current := hostWorkbenchCall(t, a.service, "active_model_snapshot", Object{})
	forgedDocument := clone(object(current["document"]))
	finish := Object{"accepted": true, "finish_state": "accepted", "revision": forgedDocument["cad_revision"], "geometry_fingerprint": forgedDocument["geometry_fingerprint"]}
	forgedDocument["finish_summary"], forgedDocument["acceptance_confirmed"] = finish, true
	a.acceptSnapshot(forgedDocument, false)
	if acceptance(a.document) == "已验收" {
		t.Fatal("a document without the host projection forged UI acceptance")
	}
	claims := Object{"finish_summary": finish, "acceptance_confirmed": true,
		"acceptance_projection": Object{"active_generation": current["generation"], "revision": forgedDocument["cad_revision"],
			"geometry_fingerprint": forgedDocument["geometry_fingerprint"], "model": Object{"acceptance_confirmed": true, "status": "accepted", "finish_status": "accepted",
				"revision": forgedDocument["cad_revision"], "geometry_fingerprint": forgedDocument["geometry_fingerprint"], "finish_summary": finish}}}
	// Keep the saved CADPlan and feature numbers byte-for-byte. Re-encoding them
	// through float64 would change 120.0 to 120 and exercise a different contract.
	var forgedProject map[string]json.RawMessage
	if err := json.Unmarshal(projectBytes, &forgedProject); err != nil {
		t.Fatal(err)
	}
	for key, value := range claims {
		encoded, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		forgedProject[key] = encoded
	}
	forgedBytes, err := json.Marshal(forgedProject)
	if err != nil {
		t.Fatal(err)
	}
	forgedPath := filepath.Join(root, "claimed-accepted.aimodel")
	if err := os.WriteFile(forgedPath, forgedBytes, 0600); err != nil {
		t.Fatal(err)
	}
	a.invoke("打开工程", "active_model_import_project", Object{"path": forgedPath, "confirmed": true}, func(result any) {
		a.acceptSnapshot(result, false)
		a.refreshModel(false)
	})
	waitHostWorkbench(t, a, tester, queue)
	assertHostGeometry(t, a, scene, [3]float64{120, 60, 20})
	current = hostWorkbenchCall(t, a.service, "active_model_snapshot", Object{})
	assertHostCandidate(t, a, current)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := a.service.Call(ctx, "reviewed_case_save", Object{"generation": a.generation}, nil); err == nil {
		t.Fatal("the host accepted an unreviewed model into the reviewed case library")
	}
	cases := []Object{}
	if err := a.service.Call(ctx, "reviewed_cases_list", Object{}, &cases); err != nil || len(cases) != 0 {
		t.Fatalf("rejected acceptance polluted the reviewed case library: %v %v", err, cases)
	}
	t.Log("forged acceptance stayed candidate in both the host and UI, and reviewed-case insertion was rejected")
}

func launchHostWorkbench(t *testing.T, options engine.Options) (*App, *viewport.Viewport, *ui.Tester, chan func(), func()) {
	t.Helper()
	options.OnLog = func(line string) { t.Logf("host: %s", line) }
	client, err := engine.Start(options)
	if err != nil {
		t.Fatal(err)
	}
	scene := viewport.New()
	a := New(client, scene)
	queue := make(chan func(), 128)
	stopped := make(chan struct{})
	a.dispatch = func(fn func()) {
		select {
		case queue <- fn:
		case <-stopped:
		}
	}
	var once sync.Once
	closeHost := func() {
		once.Do(func() {
			a.closed.Store(true)
			close(stopped)
			client.Close()
			scene.Close()
		})
	}
	t.Cleanup(closeHost)
	tester := ui.NewTester(a.View, 1440, 1100)
	a.initialize()
	waitHostWorkbench(t, a, tester, queue)
	if !a.ready {
		t.Fatal("the workbench never became ready")
	}
	return a, scene, tester, queue, closeHost
}

func waitHostWorkbench(t *testing.T, a *App, tester *ui.Tester, queue chan func()) {
	t.Helper()
	deadline := time.NewTimer(90 * time.Second)
	defer deadline.Stop()
	for a.busy {
		select {
		case fn := <-queue:
			fn()
			tester.Frame()
		case <-deadline.C:
			t.Fatalf("real host operation timed out: %s\n%s", a.operation, strings.Join(a.logs, "\n"))
		}
	}
	tester.Advance(250 * time.Millisecond)
	if a.failure != "" {
		t.Fatalf("workbench operation failed: %s\n%s", a.failure, strings.Join(a.logs, "\n"))
	}
}

func hostWorkbenchCall(t *testing.T, service Service, method string, params Object) Object {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	var result Object
	if err := service.Call(ctx, method, params, &result); err != nil {
		t.Fatalf("%s: %v", method, err)
	}
	return result
}

func replaceHostInput(t *testing.T, tester *ui.Tester, label, value string) {
	t.Helper()
	rect, ok := tester.Find(label)
	if !ok {
		t.Fatalf("input %q is absent", label)
	}
	// NumberInput labels its whole row, including the increment buttons. The
	// editable text is at the leading edge of both numeric and array inputs.
	tester.ClickAt(rect.X+8, rect.Y+rect.H/2)
	tester.Key(ui.Ctrl, ui.KeyA)
	tester.Type(value)
	tester.Key(0, ui.KeyEnter)
}

func assertHostGeometry(t *testing.T, a *App, scene *viewport.Viewport, expected [3]float64) {
	t.Helper()
	stats := object(a.document["stats"])
	size, ok := stats["size"].([]any)
	mesh := scene.Stats()
	dimensions := mesh.BoundsMax.Sub(mesh.BoundsMin)
	actual := [3]float64{dimensions.X, dimensions.Y, dimensions.Z}
	if !ok || len(size) != 3 || mesh.Triangles == 0 || number(stats["triangles"]) != float64(mesh.Triangles) {
		t.Fatalf("host stats and decoded GLB do not describe a real mesh: stats=%v mesh=%+v", stats, mesh)
	}
	for axis, want := range expected {
		if math.Abs(number(size[axis])-want) > 0.001 || math.Abs(actual[axis]-want) > 0.001 {
			t.Fatalf("axis %d expected %g mm, host=%v GLB=%v", axis, want, size, actual)
		}
	}
	if math.Abs(number(stats["volume"])-expected[0]*expected[1]*expected[2]) > 0.01 {
		t.Fatalf("unexpected solid volume: %v", stats["volume"])
	}
}

func assertHostCenter(t *testing.T, document Object, expected []float64) {
	t.Helper()
	features := rows(document["features"])
	if len(features) != 1 {
		t.Fatalf("expected one saved feature, got %d", len(features))
	}
	center, ok := object(features[0]["params"])["center"].([]any)
	if !ok || len(center) != len(expected) {
		t.Fatalf("center parameter lost its JSON array shape: %v", features[0])
	}
	for axis, value := range expected {
		if math.Abs(number(center[axis])-value) > 0.001 {
			t.Fatalf("center axis %d expected %g, got %v", axis, value, center)
		}
	}
}

func assertHostCandidate(t *testing.T, a *App, record Object) {
	t.Helper()
	projection := object(record["acceptance_projection"])
	if number(record["generation"]) != float64(a.generation) || text(record["geometry_fingerprint"]) != text(a.document["geometry_fingerprint"]) {
		t.Fatalf("workbench and host snapshot diverged: generation %d, record %v", a.generation, record)
	}
	if object(projection["model"])["acceptance_confirmed"] != false || acceptance(a.document) != "候选模型" {
		t.Fatalf("unreviewed geometry was certified: projection=%v status=%s", projection, acceptance(a.document))
	}
}

func assertHostSTL(t *testing.T, data []byte, mesh viewport.Stats) {
	t.Helper()
	if len(data) < 84 {
		t.Fatal("STL has no binary header")
	}
	count := int(binary.LittleEndian.Uint32(data[80:84]))
	if count != mesh.Triangles || len(data) != 84+50*count {
		t.Fatalf("STL triangle count or byte length differs from the displayed GLB: count=%d bytes=%d mesh=%d", count, len(data), mesh.Triangles)
	}
	minimum := [3]float64{math.Inf(1), math.Inf(1), math.Inf(1)}
	maximum := [3]float64{math.Inf(-1), math.Inf(-1), math.Inf(-1)}
	for face := 0; face < count; face++ {
		for vertex := 0; vertex < 3; vertex++ {
			for axis := 0; axis < 3; axis++ {
				offset := 84 + face*50 + 12 + vertex*12 + axis*4
				value := float64(math.Float32frombits(binary.LittleEndian.Uint32(data[offset : offset+4])))
				if math.IsNaN(value) || math.IsInf(value, 0) {
					t.Fatal("STL contains a non-finite vertex")
				}
				minimum[axis] = math.Min(minimum[axis], value)
				maximum[axis] = math.Max(maximum[axis], value)
			}
		}
	}
	minPoint := viewport.Vec3{X: minimum[0], Y: minimum[1], Z: minimum[2]}
	maxPoint := viewport.Vec3{X: maximum[0], Y: maximum[1], Z: maximum[2]}
	if minPoint.Sub(mesh.BoundsMin).Length() > 0.001 || maxPoint.Sub(mesh.BoundsMax).Length() > 0.001 {
		t.Fatalf("exported STL and displayed GLB bounds differ: STL %v %v, GLB %v %v", minPoint, maxPoint, mesh.BoundsMin, mesh.BoundsMax)
	}
}
