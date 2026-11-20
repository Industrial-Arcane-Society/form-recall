//go:build windows

package workbench

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"image"
	"image/draw"
	"image/png"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
	"unsafe"

	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/engine"
	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/viewport"
	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

// The opt-in recording uses a separate native process and isolated host data.
// Run only this test when FORMRECALL_UI_MOTION is enabled. The real window is
// shown without activation and captured through its own content and framebuffer.
func TestMain(m *testing.M) {
	if os.Getenv("FORMRECALL_UI_MOTION") != "1" {
		os.Exit(m.Run())
	}
	mygo.App.OnWindowAllClosed(func() {})
	code := 1
	mygo.App.WhenReady(func() {
		go func() {
			code = m.Run()
			mygo.App.Quit()
		}()
	})
	if err := mygo.App.Run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	os.Exit(code)
}

type motionFrame struct {
	File            string  `json:"file"`
	Seconds         float64 `json:"seconds"`
	CaptureMS       float64 `json:"capture_ms"`
	WindowMS        float64 `json:"window_ms"`
	ReadbackMS      float64 `json:"readback_ms"`
	WriteMS         float64 `json:"write_ms"`
	Stage           string  `json:"stage"`
	SHA256          string  `json:"sha256"`
	SettingsVisible bool    `json:"settings_visible"`
	SettingsTab     int     `json:"settings_tab"`
	SelectedFeature int     `json:"selected_feature"`
	LogsVisible     bool    `json:"logs_visible"`
	DetailsVisible  bool    `json:"details_visible"`
	CameraYaw       float64 `json:"camera_yaw"`
	CameraPitch     float64 `json:"camera_pitch"`
}

type motionAction struct {
	name     string
	apply    func()
	check    func() bool
	animated bool
}

type motionStage struct {
	Name         string  `json:"name"`
	Started      float64 `json:"started_seconds"`
	UniqueFrames int     `json:"unique_frames_first_450ms"`
	StateReached bool    `json:"state_reached"`
	Animated     bool    `json:"animation_expected"`
}

func TestNativeWorkbenchMotionRecording(t *testing.T) {
	if os.Getenv("FORMRECALL_UI_MOTION") != "1" {
		t.Skip("set FORMRECALL_UI_MOTION=1 to record the isolated native workbench")
	}
	paths := map[string]string{}
	for _, key := range []string{"FORMRECALL_TEST_ENGINE", "FORMRECALL_TEST_RESOURCES", "FORMRECALL_TEST_OCCT", "FORMRECALL_TEST_FFMPEG", "FORMRECALL_UI_MOTION_OUTPUT"} {
		value := os.Getenv(key)
		if value == "" {
			t.Fatalf("%s is required for native motion recording", key)
		}
		absolute, err := filepath.Abs(value)
		if err != nil {
			t.Fatal(err)
		}
		paths[key] = absolute
	}
	output := paths["FORMRECALL_UI_MOTION_OUTPUT"]
	if err := os.MkdirAll(output, 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIMODELER_OCCT_MANIFEST", paths["FORMRECALL_TEST_OCCT"])
	t.Setenv("AIMODELER_RPC_TEST_ACP_FIXTURE", "")
	data := t.TempDir()
	client, err := engine.Start(engine.Options{Executable: paths["FORMRECALL_TEST_ENGINE"], Resources: paths["FORMRECALL_TEST_RESOURCES"], DataDir: filepath.Join(data, "data"), Config: filepath.Join(data, "config.json")})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	v := viewport.New()
	a := New(client, v)
	a.options.DemoPrompt = "底板 长100 宽80 厚10，四角通孔 直径8 边距10"
	var attachErr error
	var reducedMotion bool
	mygo.RunOnMain(func() {
		a.win = mygo.NewWindow(mygo.WindowOptions{
			Title: "FormRecall isolated UI motion verification", Width: 1080, Height: 700,
			Hidden: true, TitleBarStyle: mygo.TitleBarHidden, HideWindowButtons: true,
			Content: ui.View(func(c *ui.Context) {
				reducedMotion = c.Preferences().ReduceMotion
				a.View(c)
			}),
		})
		attachErr = v.SetWindow(a.win)
		a.win.ShowInactive()
		a.initialize()
	})
	defer mygo.RunOnMain(func() { a.closed.Store(true); v.Close(); a.win.Destroy() })
	if attachErr != nil {
		t.Fatal(attachErr)
	}
	deadline := time.Now().Add(90 * time.Second)
	ready := false
	for time.Now().Before(deadline) {
		var failure string
		mygo.RunOnMain(func() {
			ready = a.ready && !a.busy && len(rows(a.document["features"])) >= 2 && v.Stats().Triangles > 0
			failure = a.failure
		})
		if failure != "" {
			t.Fatal(failure)
		}
		if ready {
			break
		}
		time.Sleep(40 * time.Millisecond)
	}
	if !ready {
		t.Fatal("real host Quick model did not become ready")
	}
	var motionDisabled bool
	mygo.RunOnMain(func() { motionDisabled = reducedMotion })
	if motionDisabled {
		t.Fatal("Windows reduced motion is enabled; recording preserves that preference")
	}
	mygo.RunOnMain(func() { a.theme = "深色"; a.win.Invalidate() })
	time.Sleep(300 * time.Millisecond)
	actions := []motionAction{
		{"initial-model", func() {}, func() bool { return len(rows(a.document["features"])) >= 2 }, false},
		{"select-mounting-holes", func() { a.selectFeature(1) }, func() bool { return a.selectedFeature == 1 }, true},
		{"select-base", func() { a.selectFeature(0) }, func() bool { return a.selectedFeature == 0 }, true},
		{"open-log", func() { a.showLogs = true }, func() bool { return a.showLogs }, true},
		{"close-log", func() { a.showLogs = false }, func() bool { return !a.showLogs }, true},
		{"hide-details", func() { a.showDetails = false }, func() bool { return !a.showDetails }, true},
		{"show-details", func() { a.showDetails = true }, func() bool { return a.showDetails }, true},
		{"open-settings", a.openSettings, func() bool { return a.showSettings }, true},
		{"settings-display", func() { a.settingsTab = 1 }, func() bool { return a.settingsTab == 1 }, true},
		{"settings-geometry", func() { a.settingsTab = 2 }, func() bool { return a.settingsTab == 2 }, true},
		{"settings-runtime", func() { a.settingsTab = 3 }, func() bool { return a.settingsTab == 3 }, true},
		{"settings-provider", func() { a.settingsTab = 0 }, func() bool { return a.settingsTab == 0 }, true},
		{"close-settings", a.cancelSettings, func() bool { return !a.showSettings }, true},
		{"camera-front", func() { a.scene.SetView("front") }, func() bool { return cameraViewReached(v, "front") }, true},
		{"camera-top", func() { a.scene.SetView("top") }, func() bool { return cameraViewReached(v, "top") }, true},
		{"camera-right", func() { a.scene.SetView("right") }, func() bool { return cameraViewReached(v, "right") }, true},
		{"camera-iso", func() { a.scene.SetView("iso") }, func() bool { return cameraViewReached(v, "iso") }, true},
	}
	if os.Getenv("FORMRECALL_UI_MOTION_STAGES") == "main" {
		mainActions := make([]motionAction, 0, len(actions))
		for _, action := range actions {
			if !strings.Contains(action.name, "settings") {
				mainActions = append(mainActions, action)
			}
		}
		actions = mainActions
	}
	const interval = time.Second / 30
	const stageDuration = 1100 * time.Millisecond
	capturer, err := newMotionCapture(a.win.NativeHandle())
	if err != nil {
		t.Fatal(err)
	}
	defer capturer.Close()
	probe := motionFrame{}
	initial, err := capturer.Capture(v, &probe)
	if err != nil {
		t.Fatal(err)
	}
	width, height := initial.Bounds().Dx(), initial.Bounds().Dy()
	video := startMotionVideo(t, paths["FORMRECALL_TEST_FFMPEG"], output, width, height)
	defer video.Close()
	started := time.Now()
	var frames []motionFrame
	var stages []motionStage
	snapshots := make(map[string]*image.RGBA)
	for _, action := range actions {
		stageStart := time.Now()
		stage := motionStage{Name: action.name, Started: stageStart.Sub(started).Seconds(), Animated: action.animated}
		mygo.RunOnMain(func() {
			action.apply()
			a.win.Invalidate()
		})
		unique := map[string]bool{}
		var lastCapture *image.RGBA
		for next := stageStart; time.Since(stageStart) < stageDuration; next = next.Add(interval) {
			if delay := time.Until(next); delay > 0 {
				time.Sleep(delay)
			}
			frame := motionFrame{File: fmt.Sprintf("native-ui-motion.mp4#frame=%d", len(frames)), Stage: action.name}
			captureStart := time.Now()
			mygo.RunOnMain(func() {
				frame.Seconds = time.Since(started).Seconds()
				frame.SettingsVisible, frame.SettingsTab = a.showSettings, a.settingsTab
				frame.SelectedFeature, frame.LogsVisible = a.selectedFeature, a.showLogs
				frame.DetailsVisible = a.showDetails
				frame.CameraYaw, frame.CameraPitch = v.Camera.Yaw, v.Camera.Pitch
			})
			captured, err := capturer.Capture(v, &frame)
			frame.CaptureMS = float64(time.Since(captureStart)) / float64(time.Millisecond)
			if err != nil {
				t.Fatal(err)
			}
			digest := sha256.Sum256(captured.Pix)
			frame.SHA256 = hex.EncodeToString(digest[:])
			if frame.Seconds-stage.Started < 0.45 {
				unique[frame.SHA256] = true
			}
			writeStart := time.Now()
			if _, err := video.input.Write(captured.Pix); err != nil {
				t.Fatalf("write raw recording frame: %v\n%v", err, video.Close())
			}
			frame.WriteMS = float64(time.Since(writeStart)) / float64(time.Millisecond)
			frames = append(frames, frame)
			lastCapture = captured
			// Let the window message loop deliver native WM_TIMER frames even
			// when image capture takes longer than the nominal sample interval.
			if time.Now().After(next.Add(interval)) {
				time.Sleep(time.Millisecond)
			}
		}
		snapshot := image.NewRGBA(lastCapture.Bounds())
		copy(snapshot.Pix, lastCapture.Pix)
		snapshots[action.name] = snapshot
		mygo.RunOnMain(func() { stage.StateReached = action.check() })
		stage.UniqueFrames = len(unique)
		stages = append(stages, stage)
	}
	elapsed := time.Since(started)
	if err := video.Close(); err != nil {
		t.Fatal(err)
	}
	for name, snapshot := range snapshots {
		file, err := os.Create(filepath.Join(output, name+".png"))
		if err != nil {
			t.Fatal(err)
		}
		err = png.Encode(file, snapshot)
		file.Close()
		if err != nil {
			t.Fatal(err)
		}
	}
	var stats viewport.Stats
	mygo.RunOnMain(func() { stats = v.Stats() })
	report := map[string]any{
		"timestamp": time.Now().Format(time.RFC3339), "process_id": os.Getpid(),
		"capture":   "Windows PrintWindow of the isolated native client area, plus its actual OpenGL framebuffer at the native child-window bounds",
		"actions":   "Production workbench callbacks and tab/log state changes dispatched on the native UI thread",
		"isolation": "Separate nonactivated native window, isolated engine data, no desktop or other window capture",
		"width":     width, "height": height, "target_interval_ms": interval.Milliseconds(),
		"elapsed_seconds": elapsed.Seconds(), "reduced_motion": motionDisabled,
		"engine": paths["FORMRECALL_TEST_ENGINE"], "sampled_fps": float64(len(frames)) / elapsed.Seconds(),
		"video_timing":    "Raw RGBA frames sent directly to FFmpeg, wall-clock timestamps, passthrough frame timing, no frame interpolation or duplication",
		"frame_digest":    "SHA-256 of the captured raw RGBA pixels",
		"model_triangles": stats.Triangles, "renderer": stats.Renderer, "stages": stages, "frames": frames,
		"limits": []string{"This records the app scene and GPU model framebuffer at their actual sampled times; it does not measure monitor presentation FPS.", "Actions invoke the same workbench callbacks and state transitions as controls, without moving the desktop pointer."},
	}
	encoded, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(output, "recording.json"), encoded, 0644); err != nil {
		t.Fatal(err)
	}
	for _, stage := range stages {
		if !stage.StateReached {
			t.Errorf("%s did not reach the requested workbench state", stage.Name)
		}
		if stage.Animated && stage.UniqueFrames < 3 {
			t.Errorf("%s showed only %d sampled frames within 450 ms; expected a visible transition", stage.Name, stage.UniqueFrames)
		}
	}
	t.Logf("recorded %d real frames at %d × %d, %.2f samples/second in %s", len(frames), width, height, float64(len(frames))/elapsed.Seconds(), output)
}

func cameraViewReached(v *viewport.Viewport, name string) bool {
	expected := v.Camera
	expected.View(name)
	return math.Abs(v.Camera.Yaw-expected.Yaw) < 0.00001 && math.Abs(v.Camera.Pitch-expected.Pitch) < 0.00001
}

// PrintWindow targets only this test window. Its OpenGL child requires a separate
// framebuffer read, mapped into the actual child-window client rectangle.
// Image conversion and compression happen off the application's UI thread.
type motionCapture struct {
	hwnd, memory, bitmap, previous uintptr
	pixels                         unsafe.Pointer
	frame                          *image.RGBA
}

func newMotionCapture(hwnd uintptr) (*motionCapture, error) {
	user := syscall.NewLazyDLL("user32.dll")
	gdi := syscall.NewLazyDLL("gdi32.dll")
	var rect struct{ Left, Top, Right, Bottom int32 }
	if ok, _, err := user.NewProc("GetClientRect").Call(hwnd, uintptr(unsafe.Pointer(&rect))); ok == 0 {
		return nil, fmt.Errorf("get recording client bounds: %w", err)
	}
	width, height := int(rect.Right), int(rect.Bottom)
	dc, _, err := user.NewProc("GetDC").Call(hwnd)
	if dc == 0 {
		return nil, fmt.Errorf("get recording device context: %w", err)
	}
	defer user.NewProc("ReleaseDC").Call(hwnd, dc)
	memory, _, err := gdi.NewProc("CreateCompatibleDC").Call(dc)
	if memory == 0 {
		return nil, fmt.Errorf("create recording device context: %w", err)
	}
	capture := &motionCapture{hwnd: hwnd, memory: memory}
	info := struct {
		Size                         uint32
		Width, Height                int32
		Planes, BitCount             uint16
		Compression, SizeImage       uint32
		XPelsPerMeter, YPelsPerMeter int32
		ColorsUsed, ColorsImportant  uint32
	}{Size: 40, Width: int32(width), Height: -int32(height), Planes: 1, BitCount: 32}
	var pixels unsafe.Pointer
	bitmap, _, err := gdi.NewProc("CreateDIBSection").Call(memory, uintptr(unsafe.Pointer(&info)), 0, uintptr(unsafe.Pointer(&pixels)), 0, 0)
	if bitmap == 0 {
		capture.Close()
		return nil, fmt.Errorf("create recording bitmap: %w", err)
	}
	previous, _, _ := gdi.NewProc("SelectObject").Call(memory, bitmap)
	capture.bitmap, capture.previous, capture.pixels = bitmap, previous, pixels
	capture.frame = image.NewRGBA(image.Rect(0, 0, width, height))
	return capture, nil
}

func (c *motionCapture) Close() {
	gdi := syscall.NewLazyDLL("gdi32.dll")
	if c.previous != 0 {
		gdi.NewProc("SelectObject").Call(c.memory, c.previous)
	}
	if c.bitmap != 0 {
		gdi.NewProc("DeleteObject").Call(c.bitmap)
	}
	if c.memory != 0 {
		gdi.NewProc("DeleteDC").Call(c.memory)
	}
}

func (c *motionCapture) Capture(v *viewport.Viewport, metrics *motionFrame) (*image.RGBA, error) {
	user := syscall.NewLazyDLL("user32.dll")
	windowStart := time.Now()
	if ok, _, err := user.NewProc("PrintWindow").Call(c.hwnd, c.memory, 3); ok == 0 {
		return nil, fmt.Errorf("capture native recording window: %w", err)
	}
	metrics.WindowMS = float64(time.Since(windowStart)) / float64(time.Millisecond)
	frame := c.frame
	bgra := unsafe.Slice((*byte)(c.pixels), len(frame.Pix))
	for i := 0; i < len(bgra); i += 4 {
		frame.Pix[i], frame.Pix[i+1], frame.Pix[i+2], frame.Pix[i+3] = bgra[i+2], bgra[i+1], bgra[i], 255
	}
	var model *image.RGBA
	var modelRect struct{ Left, Top, Right, Bottom int32 }
	var captureErr error
	readbackStart := time.Now()
	mygo.RunOnMain(func() {
		find := user.NewProc("FindWindowExW")
		surface, _, _ := find.Call(c.hwnd, 0, uintptr(unsafe.Pointer(syscall.StringToUTF16Ptr("MyGoSurface"))), 0)
		if surface == 0 {
			surface = c.hwnd
		}
		child, _, _ := find.Call(surface, 0, uintptr(unsafe.Pointer(syscall.StringToUTF16Ptr("FormRecallNativeViewport"))), 0)
		visible, _, _ := user.NewProc("IsWindowVisible").Call(child)
		if child == 0 || visible == 0 {
			return
		}
		user.NewProc("GetClientRect").Call(child, uintptr(unsafe.Pointer(&modelRect)))
		user.NewProc("MapWindowPoints").Call(child, c.hwnd, uintptr(unsafe.Pointer(&modelRect)), 2)
		model, captureErr = v.Capture()
	})
	metrics.ReadbackMS = float64(time.Since(readbackStart)) / float64(time.Millisecond)
	if captureErr != nil {
		return nil, captureErr
	}
	if model != nil {
		draw.Draw(frame, image.Rect(int(modelRect.Left), int(modelRect.Top), int(modelRect.Right), int(modelRect.Bottom)), model, image.Point{}, draw.Src)
	}
	return frame, nil
}

type motionVideo struct {
	command *exec.Cmd
	input   io.WriteCloser
	log     bytes.Buffer
	closed  bool
	err     error
}

func startMotionVideo(t *testing.T, ffmpeg, output string, width, height int) *motionVideo {
	t.Helper()
	command := exec.Command(ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
		"-use_wallclock_as_timestamps", "1", "-f", "rawvideo", "-pixel_format", "rgba",
		"-video_size", fmt.Sprintf("%dx%d", width, height), "-framerate", "1000", "-i", "pipe:0",
		"-fps_mode", "passthrough", "-enc_time_base", "1:1000",
		"-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast",
		"-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "native-ui-motion.mp4")
	command.Dir = output
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	video := &motionVideo{command: command}
	var err error
	video.input, err = command.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	command.Stderr = &video.log
	if err := command.Start(); err != nil {
		video.input.Close()
		t.Fatal(err)
	}
	return video
}

func (v *motionVideo) Close() error {
	if v.closed {
		return v.err
	}
	v.closed = true
	v.input.Close()
	if err := v.command.Wait(); err != nil {
		v.err = fmt.Errorf("encode native recording: %w\n%s", err, v.log.String())
	}
	return v.err
}
