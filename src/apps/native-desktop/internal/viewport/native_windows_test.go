//go:build windows

package viewport

import (
	"bytes"
	"image/png"
	"os"
	"runtime"
	"testing"
	"time"
	"unsafe"
)

// This test creates a hidden Win32 parent and exercises the real GPU and HWND
// input path. It runs explicitly on Windows desktop hosts, without Computer Use.
func TestNativeOpenGLFramebufferAndInput(t *testing.T) {
	if os.Getenv("NATIVE_VIEWPORT_GPU_TESTS") != "1" {
		t.Skip("set NATIVE_VIEWPORT_GPU_TESTS=1 to exercise the desktop OpenGL driver")
	}
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	classOnce.Do(registerViewportClass)
	if classError != nil {
		t.Fatal(classError)
	}
	instance, _, _ := kernel32.NewProc("GetModuleHandleW").Call(0)
	parent, _, err := createWindow.Call(0, uintptr(unsafe.Pointer(className)), 0, 0x00CF0000, 0, 0, 640, 480, 0, 0, instance, 0)
	if parent == 0 {
		t.Fatal(err)
	}
	defer destroyWindow.Call(parent)
	v := New()
	if err := v.Attach(parent); err != nil {
		t.Fatal(err)
	}
	defer v.Close()
	v.SetBounds(0, 0, 320, 240)
	v.SetOptions(false, false, false, true, false)
	v.SetReducedMotion(true)
	empty, err := v.Capture()
	if err != nil {
		t.Fatal(err)
	}
	v.SetOptions(false, false, false, true, true)
	dark, err := v.Capture()
	if err != nil {
		t.Fatal(err)
	}
	for _, sample := range []struct {
		y   int
		rgb [3]uint8
	}{
		{0, [3]uint8{35, 36, 40}},
		{dark.Bounds().Dy() - 1, [3]uint8{30, 31, 34}},
	} {
		pixel := dark.RGBAAt(dark.Bounds().Dx()/2, sample.y)
		for channel, actual := range []uint8{pixel.R, pixel.G, pixel.B} {
			expected := sample.rgb[channel]
			if actual < expected-1 || actual > expected+1 {
				t.Fatalf("dark gradient y=%d channel=%d got %d want %d", sample.y, channel, actual, expected)
			}
		}
	}
	v.SetOptions(false, false, false, true, false)
	doc, data := fixture()
	mesh, err := DecodeGLB(encodeFixture(t, doc, data))
	if err != nil {
		t.Fatal(err)
	}
	v.SetMesh(mesh)
	first, err := v.Capture()
	if err != nil {
		t.Fatal(err)
	}
	colored := 0
	for y := 0; y < first.Bounds().Dy(); y++ {
		for x := 0; x < first.Bounds().Dx(); x++ {
			if first.RGBAAt(x, y) != empty.RGBAAt(x, y) {
				colored++
			}
		}
	}
	if colored < 500 {
		t.Fatalf("mesh did not appear in framebuffer, %d nonbackground pixels", colored)
	}
	t.Logf("renderer %s, framebuffer %dx%d, model pixels %d", v.Stats().Renderer, first.Bounds().Dx(), first.Bounds().Dy(), colored)
	send := user32.NewProc("SendMessageW")
	point := func(x, y uint32) uintptr { return uintptr(x | (y << 16)) }
	initial := v.Camera
	send.Call(v.native.handle, 0x0201, 1, point(80, 80))
	send.Call(v.native.handle, 0x0200, 1, point(125, 100))
	send.Call(v.native.handle, 0x0202, 0, point(125, 100))
	if v.Camera.Yaw == initial.Yaw || v.Camera.Pitch == initial.Pitch {
		t.Fatal("native orbit did not update camera")
	}
	rotated, err := v.Capture()
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Equal(first.Pix, rotated.Pix) {
		t.Fatal("orbit did not change actual framebuffer")
	}
	initial = v.Camera
	send.Call(v.native.handle, 0x0204, 2, point(80, 80))
	send.Call(v.native.handle, 0x0200, 2, point(95, 110))
	send.Call(v.native.handle, 0x0205, 0, point(95, 110))
	if v.Camera.Target == initial.Target {
		t.Fatal("native pan did not update camera")
	}
	send.Call(v.native.handle, 0x020A, 120<<16, 0)
	if v.Camera.Distance >= initial.Distance {
		t.Fatal("native wheel did not zoom")
	}
	for _, view := range []string{"front", "back", "left", "right", "top", "bottom", "iso"} {
		v.SetView(view)
		if _, err := v.Capture(); err != nil {
			t.Fatalf("%s %v", view, err)
		}
	}
	v.SetOptions(true, true, true, false, true)
	if _, err := v.Capture(); err != nil {
		t.Fatal(err)
	}
	v.SetBounds(0, 0, 480, 200)
	resized, err := v.Capture()
	if err != nil {
		t.Fatal(err)
	}
	if resized.Bounds() == first.Bounds() {
		t.Fatal("framebuffer failed to resize")
	}
	v.SetReducedMotion(false)
	v.SetView("front")
	v.animationStarted = time.Now().Add(-time.Second)
	var message struct {
		Handle         uintptr
		Message        uint32
		WParam, LParam uintptr
		Time           uint32
		X, Y           int32
		Private        uint32
	}
	peek := user32.NewProc("PeekMessageW")
	deadline := time.Now().Add(time.Second)
	for {
		if found, _, _ := peek.Call(uintptr(unsafe.Pointer(&message)), v.native.handle, 0x0113, 0x0113, 1); found != 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("camera animation timer was not armed")
		}
		time.Sleep(5 * time.Millisecond)
	}
	user32.NewProc("DispatchMessageW").Call(uintptr(unsafe.Pointer(&message)))
	if !v.animationStarted.IsZero() {
		t.Fatal("camera animation did not settle")
	}
	time.Sleep(30 * time.Millisecond)
	if found, _, _ := peek.Call(uintptr(unsafe.Pointer(&message)), v.native.handle, 0x0113, 0x0113, 1); found != 0 {
		t.Fatal("idle viewport kept its animation timer")
	}
	if path := os.Getenv("NATIVE_VIEWPORT_TEST_GLB"); path != "" {
		productionMesh, err := LoadGLB(path)
		if err != nil {
			t.Fatal(err)
		}
		v.SetMesh(productionMesh)
		v.SetReducedMotion(true)
		v.SetView("iso")
		v.SetBounds(0, 0, 960, 640)
		v.SetOptions(true, true, true, true, os.Getenv("NATIVE_VIEWPORT_DARK") == "1")
		productionFrame, err := v.Capture()
		if err != nil {
			t.Fatal(err)
		}
		t.Logf("production GLB %d triangles, %d parts, bounds %v to %v", productionMesh.Triangles, productionMesh.Parts, productionMesh.Min, productionMesh.Max)
		if output := os.Getenv("NATIVE_VIEWPORT_CAPTURE"); output != "" {
			file, err := os.Create(output)
			if err != nil {
				t.Fatal(err)
			}
			err = png.Encode(file, productionFrame)
			closeErr := file.Close()
			if err != nil {
				t.Fatal(err)
			}
			if closeErr != nil {
				t.Fatal(closeErr)
			}
		}
	}
	v.Close()
	if len(windowInstances) != 0 {
		t.Fatal("native window retained after close")
	}
}
