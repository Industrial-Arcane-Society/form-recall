package viewport

import (
	"bytes"
	"errors"
	"fmt"
	"image"
	"image/draw"
	"image/png"
	"math"
	"os"
	"sync/atomic"
	"time"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

// Viewport owns an interactive depth-buffered WGL child surface on Windows.
// Construct it once. UI-facing methods run on the MyGo main thread.
type Viewport struct {
	Camera            Camera
	mesh              *Mesh
	native            nativeWindow
	err               error
	loadErr           error
	visible           bool
	edges, grid, axes bool
	dark              bool
	width, height     int
	from, to          Camera
	animationStarted  time.Time
	reducedMotion     bool
	frames            uint64
	post              func(func())
	closed            atomic.Bool
	loadSequence      uint64
	loading           bool
	layout            ui.Rect
	overlayFrame      *ui.Bitmap
}

type Stats struct {
	Triangles            int
	Parts                int
	BoundsMin, BoundsMax Vec3
	Renderer             string
	Frames               uint64
	Width, Height        int
}

func New() *Viewport {
	return &Viewport{Camera: NewCamera(), visible: true, edges: true, grid: true, axes: true}
}

func (v *Viewport) Attach(parentHWND uintptr) error {
	if v.native.handle != 0 {
		return nil
	}
	v.err = v.native.attach(parentHWND, v)
	if v.err == nil {
		v.native.bounds(v.layout.X, v.layout.Y, v.layout.W, v.layout.H)
	}
	return v.err
}

// SetWindow binds native resources and the dispatcher used for asynchronous Load.
// Call it on the MyGo main thread after creating the containing window.
func (v *Viewport) SetWindow(window *mygo.Window) error {
	v.post = window.Update
	return v.Attach(window.NativeHandle())
}

// Draw places the native surface in this layout box. Hide it while a UI popup
// overlaps the box, since child HWNDs are composited above the MyGo surface.
func (v *Viewport) Draw(c *ui.Context) ui.Element {
	background := ui.Hex("#edf1f4")
	if v.dark {
		background = ui.Hex("#1E1F22")
	}
	e := ui.Box(c).Key("cad-viewport").Grow(1).AlignSelf(ui.Stretch).Clip().Background(background)
	e.Draw(func(p *ui.Painter, r ui.Rect) {
		v.SetBounds(r.X, r.Y, r.W, r.H)
		if !v.visible && v.overlayFrame != nil {
			p.Image(v.overlayFrame, r, ui.FillBox)
		}
	})
	if message := v.Error(); message != "" {
		e.Children(func() { ui.Text(c, "三维视口加载失败").Bold(); ui.Text(c, message).Wrap() })
	}
	return e
}

// View participates in the native workbench's flex layout.
func (v *Viewport) View(c *ui.Context) { v.Draw(c) }

func (v *Viewport) SetOptions(edges, grid, axes, perspective, dark bool) {
	changed := v.edges != edges || v.grid != grid || v.axes != axes || v.Camera.Perspective != perspective || v.dark != dark
	v.edges, v.grid, v.axes, v.dark = edges, grid, axes, dark
	v.Camera.Perspective, v.to.Perspective = perspective, perspective
	if changed {
		v.native.invalidate()
	}
}

func (v *Viewport) SetBounds(x, y, width, height float32) {
	v.layout = ui.Rect{X: x, Y: y, W: width, H: height}
	if v.native.handle == 0 {
		v.resize(int(width), int(height))
	}
	v.native.bounds(x, y, width, height)
}
func (v *Viewport) SetVisible(visible bool) {
	if v.visible && !visible {
		// Keep the actual model under the modal's dimming layer while the
		// native child window gives way to the overlay and its exit animation.
		if frame, err := v.Capture(); err == nil {
			v.overlayFrame = ui.NewBitmap(frame)
		}
	}
	if visible {
		v.overlayFrame = nil
	}
	v.visible = visible
	v.native.show(visible && v.Error() == "")
}
func (v *Viewport) Close() {
	v.closed.Store(true)
	v.loadSequence++
	v.loading = false
	v.overlayFrame = nil
	v.native.close()
}

// Loading reports whether the latest GLB request is awaiting its UI update.
// Read it on the UI thread, like the other viewport state methods.
func (v *Viewport) Loading() bool { return v.loading }

// Capture returns the actual OpenGL framebuffer on the UI thread.
func (v *Viewport) Capture() (*image.RGBA, error) { return v.native.capture() }

// CaptureWindow includes both MyGo content and the native framebuffer, which
// MyGo's software CapturePage does not include. Run it on the UI thread after
// the requested layout has been presented. Native bounds are physical pixels.
func (v *Viewport) CaptureWindow(window *mygo.Window) ([]byte, error) {
	content, err := window.CapturePage()
	if err != nil {
		return nil, err
	}
	if !v.native.shown {
		return content, nil
	}
	background, err := png.Decode(bytes.NewReader(content))
	if err != nil {
		return nil, err
	}
	frame, err := v.Capture()
	if err != nil {
		return nil, err
	}
	combined := image.NewRGBA(background.Bounds())
	draw.Draw(combined, combined.Bounds(), background, image.Point{}, draw.Src)
	position := image.Rect(v.native.x, v.native.y, v.native.x+v.native.width, v.native.y+v.native.height)
	draw.Draw(combined, position, frame, image.Point{}, draw.Src)
	var output bytes.Buffer
	err = png.Encode(&output, combined)
	return output.Bytes(), err
}
func (v *Viewport) Error() string {
	if v.err != nil {
		return v.err.Error()
	}
	if v.loadErr != nil {
		return v.loadErr.Error()
	}
	return ""
}
func (v *Viewport) SetReducedMotion(enabled bool) {
	v.reducedMotion = enabled
	if enabled && !v.animationStarted.IsZero() {
		v.Camera = v.to
		v.animationStarted = time.Time{}
		v.native.invalidate()
	}
}
func (v *Viewport) SetEdges(enabled bool) { v.edges = enabled; v.native.invalidate() }
func (v *Viewport) SetGrid(enabled bool)  { v.grid = enabled; v.native.invalidate() }
func (v *Viewport) SetAxes(enabled bool)  { v.axes = enabled; v.native.invalidate() }
func (v *Viewport) SetPerspective(enabled bool) {
	v.Camera.Perspective = enabled
	v.to.Perspective = enabled
	v.native.invalidate()
}
func (v *Viewport) SetMesh(mesh *Mesh) {
	v.loadSequence++
	v.mesh = mesh
	v.loadErr = nil
	v.loading = false
	if mesh != nil {
		v.Camera.Fit(mesh.Min, mesh.Max, v.aspect())
	}
	v.animationStarted = time.Time{}
	v.native.meshChanged()
	v.native.invalidate()
}

// Load reads and prepares a GLB in the background after SetWindow. Newer loads
// supersede earlier results, and closing the window discards pending results.
// Decode failures are available through Error and displayed by View.
func (v *Viewport) Load(path string) error {
	if v.closed.Load() {
		return errors.New("三维视口已关闭")
	}
	v.loadSequence++
	sequence := v.loadSequence
	v.loading = false
	if path == "" {
		v.SetMesh(nil)
		return nil
	}
	if _, err := os.Stat(path); err != nil {
		v.loadErr = err
		return err
	}
	if v.post == nil {
		return v.LoadGLB(path)
	}
	v.loading, v.loadErr = true, nil
	post := v.post
	go func() {
		mesh, err := LoadGLB(path)
		if v.closed.Load() {
			return
		}
		post(func() {
			if v.closed.Load() || sequence != v.loadSequence {
				return
			}
			v.loading, v.loadErr = false, err
			if err == nil {
				v.SetMesh(mesh)
			}
			v.native.show(v.visible && err == nil)
		})
	}()
	return nil
}
func (v *Viewport) LoadGLB(path string) error {
	v.loadSequence++
	v.loading = false
	mesh, err := LoadGLB(path)
	if err != nil {
		v.loadErr = err
		return err
	}
	v.SetMesh(mesh)
	return nil
}
func (v *Viewport) Fit() {
	if v.mesh == nil {
		return
	}
	next := v.Camera
	next.Fit(v.mesh.Min, v.mesh.Max, v.aspect())
	v.animate(next)
}
func (v *Viewport) SetView(name string) {
	next := v.Camera
	if next.View(name) {
		v.animate(next)
	}
}
func (v *Viewport) Zoom(steps float64) {
	next := v.Camera
	if !v.animationStarted.IsZero() {
		next = v.to
	}
	next.Zoom(steps)
	v.animate(next)
}

func (v *Viewport) resize(width, height int) {
	if width < 1 || height < 1 {
		return
	}
	previous := v.aspect()
	v.width, v.height = width, height
	limiting := func(aspect float64) float64 {
		return math.Sin(math.Atan(math.Tan(math.Pi/8) * math.Min(1, math.Max(aspect, 0.1))))
	}
	factor := limiting(previous) / limiting(v.aspect())
	v.Camera.Distance *= factor
	v.from.Distance *= factor
	v.to.Distance *= factor
}
func (v *Viewport) aspect() float64 {
	if v.height < 1 {
		return 1.4
	}
	return float64(v.width) / float64(v.height)
}
func (v *Viewport) animate(next Camera) {
	if v.reducedMotion {
		v.Camera = next
		v.animationStarted = time.Time{}
		v.native.invalidate()
		return
	}
	v.from, v.to = v.Camera, next
	v.animationStarted = time.Now()
	v.native.animate()
}
func (v *Viewport) tick() bool {
	if v.animationStarted.IsZero() {
		return false
	}
	t := float64(time.Since(v.animationStarted)) / float64(240*time.Millisecond)
	if t >= 1 {
		v.Camera = v.to
		v.animationStarted = time.Time{}
		return false
	}
	v.Camera = mixCamera(v.from, v.to, t)
	return true
}
func (v *Viewport) Stats() Stats {
	s := Stats{Renderer: v.native.renderer, Frames: v.frames, Width: v.width, Height: v.height}
	if v.mesh != nil {
		s.Triangles = v.mesh.Triangles
		s.Parts = v.mesh.Parts
		s.BoundsMin = v.mesh.Min
		s.BoundsMax = v.mesh.Max
	}
	return s
}
func (v *Viewport) Description() string {
	if err := v.Error(); err != "" {
		return err
	}
	if v.loading {
		return "正在读取模型"
	}
	if v.mesh == nil {
		return "等待模型"
	}
	return fmt.Sprintf("%d 个三角形 · %d 个网格", v.mesh.Triangles, v.mesh.Parts)
}
