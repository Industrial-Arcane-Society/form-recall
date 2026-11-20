//go:build windows

package viewport

import (
	"encoding/json"
	"fmt"
	"image"
	"image/png"
	"os"
	"path/filepath"
	"testing"
	"time"
	"unsafe"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

// This opt-in test observes the desktop compositor's pixels. It never asks
// Capture, CaptureWindow, render, or renderFrame to draw a verification frame.
func TestNativeViewportDesktopPresentation(t *testing.T) {
	if os.Getenv("NATIVE_VIEWPORT_INTEGRATION") != "1" {
		t.Skip("set NATIVE_VIEWPORT_INTEGRATION=1 for native desktop presentation verification")
	}
	path := os.Getenv("NATIVE_VIEWPORT_TEST_GLB")
	output := os.Getenv("NATIVE_VIEWPORT_PRESENTATION_OUTPUT")
	if path == "" || output == "" {
		t.Fatal("NATIVE_VIEWPORT_TEST_GLB and NATIVE_VIEWPORT_PRESENTATION_OUTPUT are required")
	}
	if err := os.MkdirAll(output, 0755); err != nil {
		t.Fatal(err)
	}
	mesh, err := LoadGLB(path)
	if err != nil {
		t.Fatal(err)
	}
	v := New()
	var window *mygo.Window
	var attachErr error
	mygo.RunOnMain(func() {
		window = mygo.NewWindow(mygo.WindowOptions{
			Title: "FormRecall isolated desktop presentation verification", Width: 1080, Height: 700,
			Hidden: true, TitleBarStyle: mygo.TitleBarHidden, HideWindowButtons: true,
			Content: ui.View(func(c *ui.Context) {
				c.SetTheme(ui.DarkTheme())
				ui.Column(c).Fill().Background(c.Theme().Background).Children(func() {
					ui.Row(c).Height(44).Padding(12).Children(func() { ui.Text(c, "Isolated native presentation verification") })
					ui.Row(c).Grow(1).MinHeight(0).Children(func() { ui.Box(c).Width(180); v.View(c) })
					ui.Box(c).Height(24)
				})
			}),
		})
		attachErr = v.SetWindow(window)
		v.SetOptions(true, true, true, true, true)
		v.SetMesh(mesh)
		// Disable only this test-owned window so external mouse and keyboard
		// input cannot change the camera between presentation observations.
		user32.NewProc("EnableWindow").Call(window.NativeHandle(), 0)
		window.ShowInactive()
		// This test owns the new window. Keep it unobscured without activation.
		setPosition.Call(window.NativeHandle(), ^uintptr(0), 24, 24, 0, 0, 0x0011)
	})
	defer mygo.RunOnMain(func() {
		setPosition.Call(window.NativeHandle(), ^uintptr(1), 0, 0, 0, 0, 0x0013)
		v.Close()
		window.Destroy()
	})
	if attachErr != nil {
		t.Fatal(attachErr)
	}
	time.Sleep(700 * time.Millisecond)
	var results []presentationObservation
	observe := func(name string, modelExpected bool) {
		t.Helper()
		var item presentationObservation
		item.Stage, item.ModelExpected = name, modelExpected
		var child uintptr
		var pixels, front *image.RGBA
		var captureErr, frontErr error
		mygo.RunOnMain(func() {
			child = v.native.handle
			item.Shown, item.Visible, item.RenderFrames = v.native.shown, v.visible, v.frames
			enabled, _, _ := user32.NewProc("IsWindowEnabled").Call(window.NativeHandle())
			item.InputDisabled = enabled == 0
			hwndVisible, _, _ := user32.NewProc("IsWindowVisible").Call(child)
			item.NativeVisible = hwndVisible != 0
			item.Triangles, item.Width, item.Height = v.Stats().Triangles, v.native.width, v.native.height
			item.Loading, item.Error = v.Loading(), v.Error()
			item.ParentStyle, _, _ = user32.NewProc("GetWindowLongPtrW").Call(v.native.parent, ^uintptr(15))
			item.TopLevelExStyle, _, _ = user32.NewProc("GetWindowLongPtrW").Call(window.NativeHandle(), ^uintptr(19))
			// Read the desktop and the already-presented front buffer together.
			// No UI callback or PNG encoding runs between the two observations.
			pixels, captureErr = desktopClientPixels(child)
			front, frontErr = presentedFrontPixels(v)
			item.RenderFramesAfterCapture = v.frames
		})
		if captureErr != nil {
			t.Fatal(captureErr)
		}
		item.BrightPixels, item.TotalPixels = brightModelPixels(pixels)
		item.BrightFraction = float64(item.BrightPixels) / float64(item.TotalPixels)
		writePresentationPNG(t, filepath.Join(output, name+"-desktop.png"), pixels)
		whole, captureErr := desktopClientPixels(window.NativeHandle())
		if captureErr != nil {
			t.Fatal(captureErr)
		}
		writePresentationPNG(t, filepath.Join(output, name+"-window.png"), whole)
		// The front buffer provides no substitute for the independently
		// captured desktop pixels. Both captures precede all PNG encoding.
		if frontErr == nil {
			item.FrontBrightPixels, _ = brightModelPixels(front)
			item.DesktopFrontMatch = presentedModelMatch(pixels, front)
			writePresentationPNG(t, filepath.Join(output, name+"-front.png"), front)
		} else {
			item.FrontError = frontErr.Error()
		}
		results = append(results, item)
		t.Logf("%s shown=%v visible=%v WM_PAINT frames=%d desktop model pixels=%d/%d front model pixels=%d", name, item.Shown, item.Visible, item.RenderFrames, item.BrightPixels, item.TotalPixels, item.FrontBrightPixels)
		if !item.InputDisabled {
			t.Errorf("%s test window accepted external input, presentation sample was not isolated", name)
		}
		if item.RenderFramesAfterCapture != item.RenderFrames {
			t.Errorf("%s changed frames during paired capture (%d to %d), presentation sample was unstable", name, item.RenderFrames, item.RenderFramesAfterCapture)
		}
		if modelExpected && (item.BrightPixels < 1000 || item.BrightFraction < 0.02) {
			t.Errorf("%s did not present the model on the actual desktop, bright fraction %.6f", name, item.BrightFraction)
		}
		// Bright pixels alone could belong to a covering window or to the
		// cached settings bitmap. Require the visible native HWND and matching
		// model colors at the same positions in its already-presented buffer.
		if modelExpected && (!item.NativeVisible || item.FrontError != "" || item.DesktopFrontMatch < 0.95) {
			t.Errorf("%s desktop pixels do not match native presentation: visible=%v match=%.6f front error=%q", name, item.NativeVisible, item.DesktopFrontMatch, item.FrontError)
		}
		if !modelExpected && item.NativeVisible {
			t.Errorf("%s left the native HWND visible during the settings overlay", name)
		}
	}
	observe("initial", true)
	mygo.RunOnMain(func() { window.SetSize(1000, 660) })
	time.Sleep(500 * time.Millisecond)
	observe("resized", true)
	mygo.RunOnMain(func() { window.Maximize() })
	time.Sleep(600 * time.Millisecond)
	observe("maximized", true)
	mygo.RunOnMain(func() { window.Unmaximize() })
	time.Sleep(600 * time.Millisecond)
	observe("restored", true)
	mygo.RunOnMain(func() { v.SetVisible(false); window.Invalidate() })
	time.Sleep(220 * time.Millisecond)
	observe("settings-hidden", false)
	mygo.RunOnMain(func() { v.SetVisible(true); window.Invalidate() })
	time.Sleep(450 * time.Millisecond)
	observe("settings-restored", true)
	mygo.RunOnMain(func() { v.SetMesh(nil); window.Invalidate() })
	time.Sleep(120 * time.Millisecond)
	mygo.RunOnMain(func() { err = v.Load(path) })
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(700 * time.Millisecond)
	observe("reloaded", true)
	mygo.RunOnMain(func() { v.SetView("top") })
	time.Sleep(500 * time.Millisecond)
	observe("camera-top", true)
	encoded, err := json.MarshalIndent(map[string]any{
		"timestamp": time.Now().Format(time.RFC3339), "glb": path,
		"capture":          "BitBlt from the actual Windows desktop, cropped to the isolated test window and its native client rectangle",
		"front_diagnostic": "GL_FRONT read only, without rendering or swapping",
		"observations":     results,
	}, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(output, "presentation.json"), encoded, 0644); err != nil {
		t.Fatal(err)
	}
}

type presentationObservation struct {
	Stage, Error, FrontError                              string
	ModelExpected, Shown, Visible, Loading, NativeVisible bool
	InputDisabled                                         bool
	RenderFrames                                          uint64
	RenderFramesAfterCapture                              uint64
	Triangles, Width, Height                              int
	BrightPixels, TotalPixels, FrontBrightPixels          int
	BrightFraction                                        float64
	DesktopFrontMatch                                     float64
	ParentStyle, TopLevelExStyle                          uintptr
}

func presentedModelMatch(desktop, front *image.RGBA) float64 {
	if desktop.Bounds() != front.Bounds() {
		return 0
	}
	matched, total := 0, 0
	for y := 8; y < front.Bounds().Dy()-8; y++ {
		for x := 8; x < front.Bounds().Dx()-8; x++ {
			i := y*front.Stride + x*4
			if front.Pix[i] <= 100 || front.Pix[i+1] <= 100 || front.Pix[i+2] <= 100 {
				continue
			}
			total++
			agrees := true
			for channel := 0; channel < 3; channel++ {
				difference := int(desktop.Pix[i+channel]) - int(front.Pix[i+channel])
				if difference < -12 || difference > 12 {
					agrees = false
				}
			}
			if agrees {
				matched++
			}
		}
	}
	if total == 0 {
		return 0
	}
	return float64(matched) / float64(total)
}

func brightModelPixels(frame *image.RGBA) (bright, total int) {
	for y := 8; y < frame.Bounds().Dy()-8; y++ {
		for x := 8; x < frame.Bounds().Dx()-8; x++ {
			i := y*frame.Stride + x*4
			if frame.Pix[i] > 100 && frame.Pix[i+1] > 100 && frame.Pix[i+2] > 100 {
				bright++
			}
			total++
		}
	}
	return bright, total
}

func writePresentationPNG(t *testing.T, path string, pixels image.Image) {
	t.Helper()
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if err := png.Encode(file, pixels); err != nil {
		t.Fatal(err)
	}
}

func desktopClientPixels(hwnd uintptr) (*image.RGBA, error) {
	var rect struct{ Left, Top, Right, Bottom int32 }
	if ok, _, err := user32.NewProc("GetClientRect").Call(hwnd, uintptr(unsafe.Pointer(&rect))); ok == 0 {
		return nil, fmt.Errorf("desktop capture client bounds: %w", err)
	}
	var origin struct{ X, Y int32 }
	user32.NewProc("ClientToScreen").Call(hwnd, uintptr(unsafe.Pointer(&origin)))
	width, height := int(rect.Right), int(rect.Bottom)
	if width < 16 || height < 16 {
		return nil, fmt.Errorf("desktop capture invalid size %d x %d", width, height)
	}
	dc, _, _ := getDC.Call(0)
	defer releaseDC.Call(0, dc)
	memory, _, _ := gdi32.NewProc("CreateCompatibleDC").Call(dc)
	defer gdi32.NewProc("DeleteDC").Call(memory)
	info := struct {
		Size                         uint32
		Width, Height                int32
		Planes, BitCount             uint16
		Compression, SizeImage       uint32
		XPelsPerMeter, YPelsPerMeter int32
		ColorsUsed, ColorsImportant  uint32
	}{Size: 40, Width: int32(width), Height: -int32(height), Planes: 1, BitCount: 32}
	var pixels unsafe.Pointer
	bitmap, _, err := gdi32.NewProc("CreateDIBSection").Call(memory, uintptr(unsafe.Pointer(&info)), 0, uintptr(unsafe.Pointer(&pixels)), 0, 0)
	if bitmap == 0 {
		return nil, fmt.Errorf("desktop capture bitmap: %w", err)
	}
	defer gdi32.NewProc("DeleteObject").Call(bitmap)
	previous, _, _ := gdi32.NewProc("SelectObject").Call(memory, bitmap)
	defer gdi32.NewProc("SelectObject").Call(memory, previous)
	if ok, _, err := gdi32.NewProc("BitBlt").Call(memory, 0, 0, uintptr(width), uintptr(height), dc, uintptr(origin.X), uintptr(origin.Y), 0x40CC0020); ok == 0 {
		return nil, fmt.Errorf("desktop capture BitBlt: %w", err)
	}
	bgra := unsafe.Slice((*byte)(pixels), width*height*4)
	frame := image.NewRGBA(image.Rect(0, 0, width, height))
	for i := 0; i < len(bgra); i += 4 {
		frame.Pix[i], frame.Pix[i+1], frame.Pix[i+2], frame.Pix[i+3] = bgra[i+2], bgra[i+1], bgra[i], 255
	}
	return frame, nil
}

func presentedFrontPixels(v *Viewport) (*image.RGBA, error) {
	n := &v.native
	if ok, _, err := wglMakeCurrent.Call(n.dc, n.context); ok == 0 {
		return nil, fmt.Errorf("front buffer context: %w", err)
	}
	defer wglMakeCurrent.Call(0, 0)
	frame := image.NewRGBA(image.Rect(0, 0, n.width, n.height))
	n.gl.ReadBuffer(0x0404) // GL_FRONT, the result of normal WM_PAINT and SwapBuffers.
	n.gl.ReadPixels(0, 0, int32(n.width), int32(n.height), 0x1908, 0x1401, &frame.Pix[0])
	if code := n.gl.GetError(); code != 0 {
		return nil, fmt.Errorf("front buffer OpenGL error 0x%x", code)
	}
	row := make([]byte, frame.Stride)
	for y := 0; y < n.height/2; y++ {
		top := frame.Pix[y*frame.Stride : (y+1)*frame.Stride]
		bottom := frame.Pix[(n.height-y-1)*frame.Stride : (n.height-y)*frame.Stride]
		copy(row, top)
		copy(top, bottom)
		copy(bottom, row)
	}
	return frame, nil
}
