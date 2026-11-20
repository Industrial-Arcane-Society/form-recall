//go:build windows

package workbench

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"image"
	"image/png"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
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
// shown above other windows without activation. Only its client rectangle is
// copied from the desktop compositor, so native presentation is observed too.
// The test HWND is disabled before showing to exclude external keyboard/mouse
// input while production callbacks continue to run on the application thread.
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
	DesktopMS       float64 `json:"desktop_ms"`
	WriteMS         float64 `json:"write_ms"`
	Stage           string  `json:"stage"`
	SHA256          string  `json:"sha256"`
	ChangedPixels   int     `json:"changed_pixels_from_previous_sample"`
	Significant     bool    `json:"significant_change"`
	StateStable     bool    `json:"state_stable"`
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
	Name               string        `json:"name"`
	Started            float64       `json:"started_seconds"`
	SignificantChanges int           `json:"significant_changes_first_450ms"`
	SettledChanges     int           `json:"significant_changes_after_450ms"`
	StateReached       bool          `json:"state_reached"`
	StateStable        bool          `json:"state_stable"`
	ExpectedState      motionUIState `json:"expected_state"`
	Animated           bool          `json:"animation_expected"`
}

type motionUIState struct {
	SettingsVisible bool `json:"settings_visible"`
	SettingsTab     int  `json:"settings_tab"`
	SelectedFeature int  `json:"selected_feature"`
	LogsVisible     bool `json:"logs_visible"`
	DetailsVisible  bool `json:"details_visible"`
}

func motionState(a *App) motionUIState {
	return motionUIState{a.showSettings, a.settingsTab, a.selectedFeature, a.showLogs, a.showDetails}
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
	var inputDisabled bool
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
		user := syscall.NewLazyDLL("user32.dll")
		user.NewProc("EnableWindow").Call(a.win.NativeHandle(), 0)
		enabled, _, _ := user.NewProc("IsWindowEnabled").Call(a.win.NativeHandle())
		inputDisabled = enabled == 0
		a.win.ShowInactive()
		a.initialize()
	})
	defer mygo.RunOnMain(func() { a.closed.Store(true); v.Close(); a.win.Destroy() })
	if attachErr != nil {
		t.Fatal(attachErr)
	}
	if !inputDisabled {
		t.Fatal("recording window still accepts external keyboard/mouse input")
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
	expectedState := motionUIState{DetailsVisible: true}
	var initialState motionUIState
	mygo.RunOnMain(func() { initialState = motionState(a) })
	if initialState != expectedState {
		t.Fatalf("unexpected initial workbench state: got %+v, want %+v", initialState, expectedState)
	}
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
	initial, err := capturer.Capture(&probe)
	if err != nil {
		t.Fatal(err)
	}
	width, height := initial.Bounds().Dx(), initial.Bounds().Dy()
	minimumChangedPixels := max(100, int(math.Ceil(float64(width*height)*0.0001)))
	previousPixels := append([]byte(nil), initial.Pix...)
	video := startMotionVideo(t, paths["FORMRECALL_TEST_FFMPEG"], output, width, height)
	defer video.Close()
	started := time.Now()
	var frames []motionFrame
	var stages []motionStage
	snapshots := make(map[string]*image.RGBA)
	for _, action := range actions {
		stageStart := time.Now()
		stage := motionStage{Name: action.name, Started: stageStart.Sub(started).Seconds(), Animated: action.animated, StateStable: true}
		var beforeState motionUIState
		mygo.RunOnMain(func() {
			beforeState = motionState(a)
			action.apply()
			stage.ExpectedState = motionState(a)
			a.win.Invalidate()
		})
		if beforeState != expectedState {
			t.Fatalf("%s began with unexpected state: got %+v, want %+v", action.name, beforeState, expectedState)
		}
		expectedState = stage.ExpectedState
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
				frame.StateStable = motionState(a) == stage.ExpectedState
				if frame.Seconds-stage.Started >= 0.45 && !action.check() {
					frame.StateStable = false
				}
			})
			captured, err := capturer.Capture(&frame)
			frame.CaptureMS = float64(time.Since(captureStart)) / float64(time.Millisecond)
			if err != nil {
				t.Fatal(err)
			}
			digest := sha256.Sum256(captured.Pix)
			frame.SHA256 = hex.EncodeToString(digest[:])
			frame.ChangedPixels = motionChangedPixels(captured.Pix, previousPixels)
			frame.Significant = frame.ChangedPixels >= minimumChangedPixels
			copy(previousPixels, captured.Pix)
			if frame.Significant {
				if frame.Seconds-stage.Started < 0.45 {
					stage.SignificantChanges++
				} else {
					stage.SettledChanges++
				}
			}
			if !frame.StateStable {
				stage.StateStable = false
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
		mygo.RunOnMain(func() {
			stage.StateReached = action.check()
			stage.StateStable = stage.StateStable && motionState(a) == stage.ExpectedState
		})
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
		"capture":   "BitBlt of desktop compositor pixels within the isolated native window client rectangle after DwmFlush",
		"actions":   "Production workbench callbacks and tab/log state changes dispatched on the native UI thread",
		"isolation": "Separate topmost nonactivated HWND disabled with EnableWindow(false) before showing, excluding physical keyboard/mouse input while production callbacks run; isolated engine data; client-only capture stops if an overlapping window is above it",
		"width":     width, "height": height, "target_interval_ms": interval.Milliseconds(),
		"elapsed_seconds": elapsed.Seconds(), "reduced_motion": motionDisabled,
		"engine": paths["FORMRECALL_TEST_ENGINE"], "sampled_fps": float64(len(frames)) / elapsed.Seconds(),
		"video_timing":    "Raw RGBA frames sent directly to FFmpeg, wall-clock timestamps, passthrough frame timing, no frame interpolation or duplication",
		"frame_digest":    "SHA-256 of the captured raw RGBA pixels",
		"input_disabled":  inputDisabled,
		"pixel_change":    map[string]any{"comparison": "Adjacent sampled desktop frames; a pixel changes when any RGB channel differs by more than 8; alpha is ignored", "minimum_changed_pixels": minimumChangedPixels, "minimum_fraction": 0.0001, "minimum_pixel_floor": 100, "animation_assertion": "At least two significant adjacent-frame changes within 450 ms; the static initial stage must show none; SHA-256 is retained for traceability only"},
		"state_checks":    "Known initial state, unchanged controls between planned actions, per-frame control-state stability, and requested target reached by 450 ms and retained to stage end",
		"model_triangles": stats.Triangles, "renderer": stats.Renderer, "stages": stages, "frames": frames,
		"limits": []string{"Frames contain the desktop compositor output in the test window's client rectangle; the capture rate does not measure monitor presentation FPS.", "Recording requires a visible, unobscured test window of constant size. It stops when the window is hidden, minimized, resized, outside its monitor, or overlapped by another window above it.", "The disabled test HWND excludes physical input. Actions invoke the same workbench callbacks and state transitions as controls, so this recording verifies their rendered response; physical input routing is covered separately."},
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
		if !stage.StateStable {
			t.Errorf("%s departed from the expected state during recording: %+v", stage.Name, stage.ExpectedState)
		}
		if stage.Animated && stage.SignificantChanges < 2 {
			t.Errorf("%s showed only %d significant adjacent-frame changes within 450 ms; expected at least 2", stage.Name, stage.SignificantChanges)
		}
		if !stage.Animated && stage.SignificantChanges+stage.SettledChanges > 0 {
			t.Errorf("static %s showed %d significant changes", stage.Name, stage.SignificantChanges+stage.SettledChanges)
		}
	}
	t.Logf("recorded %d real frames at %d × %d, %.2f samples/second in %s", len(frames), width, height, float64(len(frames))/elapsed.Seconds(), output)
}

func motionChangedPixels(current, previous []byte) int {
	changed := 0
	for i := 0; i < len(current); i += 4 {
		red := int(current[i]) - int(previous[i])
		green := int(current[i+1]) - int(previous[i+1])
		blue := int(current[i+2]) - int(previous[i+2])
		if red < -8 || red > 8 || green < -8 || green > 8 || blue < -8 || blue > 8 {
			changed++
		}
	}
	return changed
}

func TestMotionChangedPixelsIgnoresNoise(t *testing.T) {
	previous := []byte{50, 50, 50, 255, 50, 50, 50, 255, 50, 50, 50, 255, 50, 50, 50, 255}
	current := []byte{58, 42, 58, 0, 59, 50, 50, 255, 50, 41, 50, 255, 50, 50, 59, 255}
	if got := motionChangedPixels(current, previous); got != 3 {
		t.Fatalf("significantly changed pixels = %d, want 3", got)
	}
	if got := motionChangedPixels(previous, current); got != 3 {
		t.Fatalf("reverse changed pixels = %d, want 3", got)
	}
}

func cameraViewReached(v *viewport.Viewport, name string) bool {
	expected := v.Camera
	expected.View(name)
	return math.Abs(v.Camera.Yaw-expected.Yaw) < 0.00001 && math.Abs(v.Camera.Pitch-expected.Pitch) < 0.00001
}

// The reusable DIB receives only the desktop pixels inside this test window's
// client rectangle. Image conversion and compression stay off the UI thread.
type motionCapture struct {
	hwnd, memory, bitmap, previous uintptr
	pixels                         unsafe.Pointer
	frame                          *image.RGBA
}

func newMotionCapture(hwnd uintptr) (*motionCapture, error) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	user := syscall.NewLazyDLL("user32.dll")
	gdi := syscall.NewLazyDLL("gdi32.dll")
	bounds, err := motionClientBounds(hwnd)
	if err != nil {
		return nil, err
	}
	width, height := bounds.Dx(), bounds.Dy()
	dc, _, err := user.NewProc("GetDC").Call(0)
	if dc == 0 {
		return nil, fmt.Errorf("get recording device context: %w", err)
	}
	defer user.NewProc("ReleaseDC").Call(0, dc)
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
	// HWND_TOPMOST, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE.
	if ok, _, err := user.NewProc("SetWindowPos").Call(hwnd, ^uintptr(0), 0, 0, 0, 0, 0x0013); ok == 0 {
		capture.Close()
		return nil, fmt.Errorf("keep recording window unobscured: %w", err)
	}
	return capture, nil
}

func (c *motionCapture) Close() {
	// Restore only this test window's z-order before the test destroys it.
	syscall.NewLazyDLL("user32.dll").NewProc("SetWindowPos").Call(c.hwnd, ^uintptr(1), 0, 0, 0, 0, 0x0013)
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

func motionClientBounds(hwnd uintptr) (image.Rectangle, error) {
	user := syscall.NewLazyDLL("user32.dll")
	var rect struct{ Left, Top, Right, Bottom int32 }
	if ok, _, err := user.NewProc("GetClientRect").Call(hwnd, uintptr(unsafe.Pointer(&rect))); ok == 0 {
		return image.Rectangle{}, fmt.Errorf("get recording client bounds: %w", err)
	}
	var origin struct{ X, Y int32 }
	if ok, _, err := user.NewProc("ClientToScreen").Call(hwnd, uintptr(unsafe.Pointer(&origin))); ok == 0 {
		return image.Rectangle{}, fmt.Errorf("map recording client bounds to desktop: %w", err)
	}
	bounds := image.Rect(int(origin.X), int(origin.Y), int(origin.X+rect.Right), int(origin.Y+rect.Bottom))
	if bounds.Empty() {
		return image.Rectangle{}, fmt.Errorf("invalid recording client bounds %v", bounds)
	}
	return bounds, nil
}

func (c *motionCapture) Capture(metrics *motionFrame) (*image.RGBA, error) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	user := syscall.NewLazyDLL("user32.dll")
	gdi := syscall.NewLazyDLL("gdi32.dll")
	desktopStart := time.Now()
	visible, _, _ := user.NewProc("IsWindowVisible").Call(c.hwnd)
	minimized, _, _ := user.NewProc("IsIconic").Call(c.hwnd)
	if visible == 0 || minimized != 0 {
		return nil, fmt.Errorf("recording window is hidden or minimized")
	}
	bounds, err := motionClientBounds(c.hwnd)
	if err != nil {
		return nil, err
	}
	if bounds.Size() != c.frame.Bounds().Size() {
		return nil, fmt.Errorf("recording client size changed from %v to %v", c.frame.Bounds().Size(), bounds.Size())
	}
	var monitor struct {
		Size         uint32
		Bounds, Work struct{ Left, Top, Right, Bottom int32 }
		Flags        uint32
	}
	monitor.Size = uint32(unsafe.Sizeof(monitor))
	handle, _, _ := user.NewProc("MonitorFromWindow").Call(c.hwnd, 2)
	if ok, _, err := user.NewProc("GetMonitorInfoW").Call(handle, uintptr(unsafe.Pointer(&monitor))); ok == 0 {
		return nil, fmt.Errorf("get recording monitor bounds: %w", err)
	}
	monitorBounds := image.Rect(int(monitor.Bounds.Left), int(monitor.Bounds.Top), int(monitor.Bounds.Right), int(monitor.Bounds.Bottom))
	if !bounds.In(monitorBounds) {
		return nil, fmt.Errorf("recording client is outside its monitor")
	}
	if result, _, _ := syscall.NewLazyDLL("dwmapi.dll").NewProc("DwmFlush").Call(); int32(result) < 0 {
		return nil, fmt.Errorf("wait for recording desktop composition: 0x%x", uint32(result))
	}
	// Reject overlap before reading desktop pixels, including other topmost
	// windows. Read rectangles only, never their content or titles.
	above, _, _ := user.NewProc("GetWindow").Call(c.hwnd, 3) // GW_HWNDPREV.
	for above != 0 {
		shown, _, _ := user.NewProc("IsWindowVisible").Call(above)
		var rect struct{ Left, Top, Right, Bottom int32 }
		if shown != 0 {
			if ok, _, err := user.NewProc("GetWindowRect").Call(above, uintptr(unsafe.Pointer(&rect))); ok == 0 {
				return nil, fmt.Errorf("check recording window overlap: %w", err)
			}
			if bounds.Overlaps(image.Rect(int(rect.Left), int(rect.Top), int(rect.Right), int(rect.Bottom))) {
				return nil, fmt.Errorf("another window overlaps the recording client")
			}
		}
		above, _, _ = user.NewProc("GetWindow").Call(above, 3)
	}
	dc, _, err := user.NewProc("GetDC").Call(0)
	if dc == 0 {
		return nil, fmt.Errorf("get recording desktop device context: %w", err)
	}
	defer user.NewProc("ReleaseDC").Call(0, dc)
	// SRCCOPY | CAPTUREBLT includes the actual composed native child surface.
	if ok, _, err := gdi.NewProc("BitBlt").Call(c.memory, 0, 0, uintptr(bounds.Dx()), uintptr(bounds.Dy()), dc, uintptr(bounds.Min.X), uintptr(bounds.Min.Y), 0x40CC0020); ok == 0 {
		return nil, fmt.Errorf("capture recording desktop client: %w", err)
	}
	if ok, _, err := gdi.NewProc("GdiFlush").Call(); ok == 0 {
		return nil, fmt.Errorf("flush recording desktop pixels: %w", err)
	}
	metrics.DesktopMS = float64(time.Since(desktopStart)) / float64(time.Millisecond)
	frame := c.frame
	bgra := unsafe.Slice((*byte)(c.pixels), len(frame.Pix))
	for i := 0; i < len(bgra); i += 4 {
		frame.Pix[i], frame.Pix[i+1], frame.Pix[i+2], frame.Pix[i+3] = bgra[i+2], bgra[i+1], bgra[i], 255
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
