//go:build windows

package viewport

import (
	"bytes"
	"encoding/json"
	"fmt"
	"image/png"
	"math"
	"os"
	"path/filepath"
	"sort"
	"testing"
	"time"
	"unsafe"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

func TestMain(m *testing.M) {
	if os.Getenv("NATIVE_VIEWPORT_INTEGRATION") != "1" {
		os.Exit(m.Run())
	}
	mygo.App.OnWindowAllClosed(func() {})
	code := 1
	mygo.App.WhenReady(func() { go func() { code = m.Run(); mygo.App.Quit() }() })
	if err := mygo.App.Run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	os.Exit(code)
}

type frameTiming struct {
	Triangles int     `json:"triangles"`
	Parts     int     `json:"parts"`
	Frames    int     `json:"frames"`
	UploadMS  float64 `json:"initial_upload_ms"`
	MeanMS    float64 `json:"mean_ms"`
	P50MS     float64 `json:"p50_ms"`
	P95MS     float64 `json:"p95_ms"`
	MaxMS     float64 `json:"max_ms"`
}

func measureFrames(v *Viewport, mesh *Mesh) frameTiming {
	finish := opengl32.NewProc("glFinish")
	render := func() {
		v.native.renderFrame(true)
		wglMakeCurrent.Call(v.native.dc, v.native.context)
		finish.Call()
		wglMakeCurrent.Call(0, 0)
	}
	result := frameTiming{Triangles: mesh.Triangles, Parts: mesh.Parts, Frames: 120}
	v.SetMesh(mesh)
	started := time.Now()
	render()
	result.UploadMS = float64(time.Since(started)) / float64(time.Millisecond)
	for i := 0; i < 8; i++ {
		render()
	}
	times := make([]float64, result.Frames)
	for i := range times {
		v.Camera.Orbit(1.5, 0)
		started = time.Now()
		render()
		times[i] = float64(time.Since(started)) / float64(time.Millisecond)
		result.MeanMS += times[i] / float64(len(times))
	}
	sort.Float64s(times)
	result.P50MS = times[len(times)/2]
	result.P95MS = times[len(times)*95/100]
	result.MaxMS = times[len(times)-1]
	return result
}

func repeatMesh(source *Mesh, columns, rows int) *Mesh {
	result := &Mesh{Min: source.Min, Max: source.Max, Source: "repeated real GLB"}
	step := source.Max.Sub(source.Min).Mul(1.15)
	for row := 0; row < rows; row++ {
		for column := 0; column < columns; column++ {
			delta := Vec3{float64(column) * step.X, float64(row) * step.Y, 0}
			start := len(result.Vertices)
			result.Vertices = append(result.Vertices, source.Vertices...)
			for i := start; i < len(result.Vertices); i += 10 {
				result.Vertices[i] += float32(delta.X)
				result.Vertices[i+1] += float32(delta.Y)
			}
			start = len(result.Edges)
			result.Edges = append(result.Edges, source.Edges...)
			for i := start; i < len(result.Edges); i += 3 {
				result.Edges[i] += float32(delta.X)
				result.Edges[i+1] += float32(delta.Y)
			}
			result.Triangles += source.Triangles
			result.Parts += source.Parts
			result.Max = source.Max.Add(delta)
		}
	}
	return result
}

func processCPU() uint64 {
	var created, exited, kernel, user uint64
	process, _, _ := kernel32.NewProc("GetCurrentProcess").Call()
	kernel32.NewProc("GetProcessTimes").Call(process, uintptr(unsafe.Pointer(&created)), uintptr(unsafe.Pointer(&exited)), uintptr(unsafe.Pointer(&kernel)), uintptr(unsafe.Pointer(&user)))
	return kernel + user
}

func TestMyGoViewportIntegrationAndPerformance(t *testing.T) {
	if os.Getenv("NATIVE_VIEWPORT_INTEGRATION") != "1" {
		t.Skip("set NATIVE_VIEWPORT_INTEGRATION=1 for the isolated native integration process")
	}
	path := os.Getenv("NATIVE_VIEWPORT_TEST_GLB")
	if path == "" {
		t.Fatal("NATIVE_VIEWPORT_TEST_GLB must name a real CAD GLB")
	}
	loadStarted := time.Now()
	mesh, err := LoadGLB(path)
	if err != nil {
		t.Fatal(err)
	}
	loadMS := float64(time.Since(loadStarted)) / float64(time.Millisecond)
	large := repeatMesh(mesh, 20, 20)
	v := New()
	var window *mygo.Window
	var attachErr error
	mygo.RunOnMain(func() {
		window = mygo.NewWindow(mygo.WindowOptions{Title: "FormRecall isolated viewport verification", Width: 1200, Height: 800, Hidden: true, TitleBarStyle: mygo.TitleBarHidden, HideWindowButtons: true,
			Content: ui.View(func(c *ui.Context) {
				ui.Column(c).Fill().Children(func() {
					ui.Row(c).Height(48).Children(func() { ui.Text(c, "Viewport verification") })
					ui.Row(c).Grow(1).Children(func() { ui.Box(c).Width(180); v.View(c) })
					ui.Box(c).Height(24)
				})
			})})
		attachErr = v.SetWindow(window)
		v.SetReducedMotion(true)
		v.SetMesh(mesh)
		window.ShowInactive()
	})
	if attachErr != nil {
		t.Fatal(attachErr)
	}
	defer mygo.RunOnMain(func() { v.Close(); window.Destroy() })
	time.Sleep(150 * time.Millisecond)
	var combined []byte
	mygo.RunOnMain(func() { combined, err = v.CaptureWindow(window) })
	if err != nil {
		t.Fatal(err)
	}
	frame, err := png.Decode(bytes.NewReader(combined))
	if err != nil {
		t.Fatal(err)
	}
	var rect struct{ Left, Top, Right, Bottom int32 }
	var dpi uintptr
	var nativeBounds [4]int
	mygo.RunOnMain(func() {
		dpi, _, _ = getDPI.Call(window.NativeHandle())
		user32.NewProc("GetClientRect").Call(v.native.handle, uintptr(unsafe.Pointer(&rect)))
		nativeBounds = [4]int{v.native.x, v.native.y, v.native.width, v.native.height}
	})
	if v.native.parent == window.NativeHandle() {
		t.Fatal("viewport did not attach to MyGoSurface")
	}
	if output := os.Getenv("NATIVE_VIEWPORT_REPORT"); output != "" {
		_ = os.WriteFile(output+".png", combined, 0644)
	}
	t.Logf("capture %v, layout %+v", frame.Bounds(), v.layout)
	if int(rect.Right) != nativeBounds[2] || int(rect.Bottom) != nativeBounds[3] {
		t.Fatalf("actual HWND client dimensions %+v differ from viewport %v, DPI %d", rect, nativeBounds, dpi)
	}
	if nativeBounds[0] < 1 || nativeBounds[1] < 1 || nativeBounds[0]+nativeBounds[2] > frame.Bounds().Dx()+1 || nativeBounds[1]+nativeBounds[3] > frame.Bounds().Dy()+1 {
		t.Fatalf("DPI bounds outside capture, native %v screenshot %v", nativeBounds, frame.Bounds())
	}
	// Verify the final composite includes real model pixels within the native box.
	var raw []byte
	mygo.RunOnMain(func() { raw, err = window.CapturePage() })
	if err != nil {
		t.Fatal(err)
	}
	base, err := png.Decode(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	changed := 0
	for y := nativeBounds[1]; y < nativeBounds[1]+nativeBounds[3]; y++ {
		for x := nativeBounds[0]; x < nativeBounds[0]+nativeBounds[2]; x++ {
			if frame.At(x, y) != base.At(x, y) {
				changed++
			}
		}
	}
	if changed < 1000 {
		t.Fatalf("composite omitted native pixels, changed %d", changed)
	}
	var realTiming, largeTiming frameTiming
	mygo.RunOnMain(func() { realTiming = measureFrames(v, mesh); largeTiming = measureFrames(v, large) })
	// Resize through the real MyGo window and force its pending layout capture.
	var resized [4]int
	mygo.RunOnMain(func() { window.SetSize(920, 980) })
	window.Invalidate()
	time.Sleep(100 * time.Millisecond)
	mygo.RunOnMain(func() {
		_, err = window.CapturePage()
		resized = [4]int{v.native.x, v.native.y, v.native.width, v.native.height}
		v.SetMesh(mesh)
	})
	if err != nil {
		t.Fatal(err)
	}
	if resized == nativeBounds || resized[2] <= 0 || resized[3] <= 0 {
		t.Fatalf("native bounds did not follow MyGo resize %v", resized)
	}
	var hidden bool
	mygo.RunOnMain(func() { v.SetVisible(false); hidden = !v.native.shown; window.Update(func() {}) })
	if !hidden {
		t.Fatal("settings overlay failed to hide native surface")
	}
	mygo.RunOnMain(func() { v.SetVisible(true); v.SetReducedMotion(false); v.SetView("front") })
	time.Sleep(120 * time.Millisecond)
	var middle Camera
	mygo.RunOnMain(func() { middle = v.Camera })
	time.Sleep(220 * time.Millisecond)
	var settled bool
	mygo.RunOnMain(func() { settled = v.animationStarted.IsZero() && math.Abs(v.Camera.Yaw+math.Pi/2) < 1e-8 })
	if !settled || math.Abs(middle.Yaw+math.Pi/2) < 1e-8 {
		t.Fatal("native animation did not interpolate and settle")
	}
	mygo.RunOnMain(func() {
		v.SetReducedMotion(true)
		v.SetView("top")
		settled = v.animationStarted.IsZero() && v.Camera.Pitch == math.Pi/2
	})
	if !settled {
		t.Fatal("reduced motion did not apply target immediately")
	}
	time.Sleep(150 * time.Millisecond)
	var framesBefore, framesAfter uint64
	mygo.RunOnMain(func() { framesBefore = v.frames })
	cpuBefore := processCPU()
	idleStarted := time.Now()
	time.Sleep(2 * time.Second)
	cpuAfter := processCPU()
	idleDuration := time.Since(idleStarted)
	mygo.RunOnMain(func() { framesAfter = v.frames })
	if framesAfter-framesBefore > 2 {
		t.Fatalf("idle viewport rendered %d extra frames", framesAfter-framesBefore)
	}
	report := map[string]any{
		"timestamp": time.Now().Format(time.RFC3339), "status": "passed", "process_id": os.Getpid(),
		"renderer": v.Stats().Renderer, "model_path": path, "parse_ms": loadMS, "real_mesh": realTiming, "large_mesh": largeTiming,
		"large_mesh_source":      "20 by 20 translated copies of the real 604-triangle CAD model, retained as one GPU mesh",
		"visual_style":           []string{"neutral silver material and two directional studio lights", "subtle vertical background gradient", "low contrast major and minor ground grid", "screen-space XYZ orientation triad", "angle weighted smoothing at shallow triangle joins with sharp rims preserved"},
		"timing_method":          "120 rotating frames after 8 warmup frames. Wall time includes render, swap, WGL context switch and glFinish in a shown, nonactivated, isolated MyGo window. This measures synchronous render cost, not desktop presentation FPS.",
		"initial_surface_pixels": nativeBounds, "resized_surface_pixels": resized, "window_dpi": dpi,
		"capture_window_api":   "(*Viewport).CaptureWindow(*mygo.Window) ([]byte,error), UI thread",
		"capture_model_pixels": changed, "native_parent": "MyGoSurface",
		"idle_seconds": idleDuration.Seconds(), "idle_cpu_ms": float64(cpuAfter-cpuBefore) / 10000, "idle_cpu_one_core_percent": float64(cpuAfter-cpuBefore) / 1e7 / idleDuration.Seconds() * 100, "idle_extra_frames": framesAfter - framesBefore,
		"animation_interpolates_and_settles": true, "reduced_motion_immediate": true, "overlay_hides_surface": hidden,
		"limits": []string{"Multi-monitor physical DPI switching was not exercised. Current desktop DPI and actual HWND client sizing were verified.", "MenuButton uses native platform menus. Overlapping Select, ComboBox and PopoverMenuButton panels require viewport hiding.", "Timing is this GPU and isolated process workload only. Windows exposure may request occasional paint frames during idle."},
	}
	output := os.Getenv("NATIVE_VIEWPORT_REPORT")
	if output == "" {
		t.Fatal("NATIVE_VIEWPORT_REPORT must name the output JSON")
	}
	if err := os.MkdirAll(filepath.Dir(output), 0755); err != nil {
		t.Fatal(err)
	}
	encoded, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(output, encoded, 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(output+".png", combined, 0644); err != nil {
		t.Fatal(err)
	}
	t.Logf("real %d triangles p95 %.3f ms, large %d triangles p95 %.3f ms, idle %.4f ms CPU", realTiming.Triangles, realTiming.P95MS, largeTiming.Triangles, largeTiming.P95MS, float64(cpuAfter-cpuBefore)/10000)
}
