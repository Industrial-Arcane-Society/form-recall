//go:build windows

package viewport

import (
	"errors"
	"fmt"
	"image"
	"math"
	"runtime"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"github.com/ebitengine/purego"
)

var (
	user32            = syscall.NewLazyDLL("user32.dll")
	gdi32             = syscall.NewLazyDLL("gdi32.dll")
	kernel32          = syscall.NewLazyDLL("kernel32.dll")
	opengl32          = syscall.NewLazyDLL("opengl32.dll")
	registerClass     = user32.NewProc("RegisterClassExW")
	createWindow      = user32.NewProc("CreateWindowExW")
	defaultWindowProc = user32.NewProc("DefWindowProcW")
	findWindow        = user32.NewProc("FindWindowExW")
	mapWindowPoints   = user32.NewProc("MapWindowPoints")
	setPosition       = user32.NewProc("SetWindowPos")
	showWindow        = user32.NewProc("ShowWindow")
	destroyWindow     = user32.NewProc("DestroyWindow")
	getDC             = user32.NewProc("GetDC")
	releaseDC         = user32.NewProc("ReleaseDC")
	getDPI            = user32.NewProc("GetDpiForWindow")
	invalidateRect    = user32.NewProc("InvalidateRect")
	beginPaint        = user32.NewProc("BeginPaint")
	endPaint          = user32.NewProc("EndPaint")
	setCapture        = user32.NewProc("SetCapture")
	releaseCapture    = user32.NewProc("ReleaseCapture")
	setFocus          = user32.NewProc("SetFocus")
	setTimer          = user32.NewProc("SetTimer")
	killTimer         = user32.NewProc("KillTimer")
	choosePixelFormat = gdi32.NewProc("ChoosePixelFormat")
	setPixelFormat    = gdi32.NewProc("SetPixelFormat")
	swapBuffers       = gdi32.NewProc("SwapBuffers")
	wglCreateContext  = opengl32.NewProc("wglCreateContext")
	wglMakeCurrent    = opengl32.NewProc("wglMakeCurrent")
	wglDeleteContext  = opengl32.NewProc("wglDeleteContext")
	className         = syscall.StringToUTF16Ptr("FormRecallNativeViewport")
	classOnce         sync.Once
	classError        error
	windowInstances   = map[uintptr]*nativeWindow{}
	windowCallback    = syscall.NewCallback(viewportWindowProc)
)

type windowClass struct {
	Size, Style                                                        uint32
	WindowProc                                                         uintptr
	ClassExtra, WindowExtra                                            int32
	Instance, Icon, Cursor, Background, MenuName, ClassName, SmallIcon uintptr
}

type pixelFormat struct {
	Size, Version                                                                                              uint16
	Flags                                                                                                      uint32
	PixelType, ColorBits, RedBits, RedShift, GreenBits, GreenShift, BlueBits, BlueShift, AlphaBits, AlphaShift byte
	AccumBits, AccumRedBits, AccumGreenBits, AccumBlueBits, AccumAlphaBits, DepthBits, StencilBits, AuxBuffers byte
	LayerType, Reserved                                                                                        byte
	LayerMask, VisibleMask, DamageMask                                                                         uint32
}

type nativeWindow struct {
	handle, dc, context uintptr
	parent              uintptr
	layoutSurface       uintptr
	owner               *Viewport
	renderer            string
	x, y, width, height int
	lastX, lastY        int
	button              uint32
	meshList            uint32
	edgeList            uint32
	meshDirty           bool
	shown               bool
	gl                  glFunctions
}

type glFunctions struct {
	Viewport                              func(int32, int32, int32, int32)
	ClearColor                            func(float32, float32, float32, float32)
	Clear                                 func(uint32)
	Enable, Disable                       func(uint32)
	DepthFunc                             func(uint32)
	MatrixMode                            func(uint32)
	LoadIdentity                          func()
	LoadMatrix                            func(*float64)
	Ortho                                 func(float64, float64, float64, float64, float64, float64)
	Frustum                               func(float64, float64, float64, float64, float64, float64)
	Begin                                 func(uint32)
	End                                   func()
	Vertex                                func(float64, float64, float64)
	Normal                                func(float64, float64, float64)
	Color                                 func(float32, float32, float32, float32)
	LineWidth                             func(float32)
	PolygonOffset                         func(float32, float32)
	Light                                 func(uint32, uint32, *float32)
	LightModel                            func(uint32, *float32)
	ColorMaterial                         func(uint32, uint32)
	GenLists                              func(int32) uint32
	NewList                               func(uint32, uint32)
	EndList                               func()
	CallList                              func(uint32)
	DeleteLists                           func(uint32, int32)
	GetString                             func(uint32) *byte
	GetError                              func() uint32
	EnableClientState, DisableClientState func(uint32)
	VertexPointer                         func(int32, uint32, int32, *float32)
	NormalPointer                         func(uint32, int32, *float32)
	ColorPointer                          func(int32, uint32, int32, *float32)
	DrawArrays                            func(uint32, int32, int32)
	ReadPixels                            func(int32, int32, int32, int32, uint32, uint32, *byte)
	ReadBuffer                            func(uint32)
	Material                              func(uint32, uint32, *float32)
	MaterialFloat                         func(uint32, uint32, float32)
	BlendFunc                             func(uint32, uint32)
}

func (g *glFunctions) load() error {
	functions := []struct {
		name   string
		target any
	}{
		{"glViewport", &g.Viewport}, {"glClearColor", &g.ClearColor}, {"glClear", &g.Clear},
		{"glEnable", &g.Enable}, {"glDisable", &g.Disable}, {"glDepthFunc", &g.DepthFunc},
		{"glMatrixMode", &g.MatrixMode}, {"glLoadIdentity", &g.LoadIdentity}, {"glLoadMatrixd", &g.LoadMatrix},
		{"glOrtho", &g.Ortho}, {"glFrustum", &g.Frustum}, {"glBegin", &g.Begin}, {"glEnd", &g.End},
		{"glVertex3d", &g.Vertex}, {"glNormal3d", &g.Normal}, {"glColor4f", &g.Color},
		{"glLineWidth", &g.LineWidth}, {"glPolygonOffset", &g.PolygonOffset}, {"glLightfv", &g.Light},
		{"glLightModelfv", &g.LightModel}, {"glColorMaterial", &g.ColorMaterial},
		{"glGenLists", &g.GenLists}, {"glNewList", &g.NewList}, {"glEndList", &g.EndList},
		{"glCallList", &g.CallList}, {"glDeleteLists", &g.DeleteLists}, {"glGetString", &g.GetString}, {"glGetError", &g.GetError},
		{"glEnableClientState", &g.EnableClientState}, {"glDisableClientState", &g.DisableClientState},
		{"glVertexPointer", &g.VertexPointer}, {"glNormalPointer", &g.NormalPointer}, {"glColorPointer", &g.ColorPointer},
		{"glDrawArrays", &g.DrawArrays}, {"glReadPixels", &g.ReadPixels}, {"glReadBuffer", &g.ReadBuffer},
		{"glMaterialfv", &g.Material}, {"glMaterialf", &g.MaterialFloat}, {"glBlendFunc", &g.BlendFunc},
	}
	for _, function := range functions {
		proc := opengl32.NewProc(function.name)
		if err := proc.Find(); err != nil {
			return err
		}
		purego.RegisterFunc(function.target, proc.Addr())
	}
	return nil
}

func registerViewportClass() {
	instance, _, _ := kernel32.NewProc("GetModuleHandleW").Call(0)
	cursor, _, _ := user32.NewProc("LoadCursorW").Call(0, 32512)
	class := windowClass{Style: 0x0020 | 0x0003 | 0x0008, WindowProc: windowCallback, Instance: instance, Cursor: cursor, ClassName: uintptr(unsafe.Pointer(className))}
	class.Size = uint32(unsafe.Sizeof(class))
	if result, _, err := registerClass.Call(uintptr(unsafe.Pointer(&class))); result == 0 {
		classError = fmt.Errorf("注册三维窗口失败 %w", err)
	}
}

func (n *nativeWindow) attach(parent uintptr, owner *Viewport) error {
	if parent == 0 {
		return errors.New("三维视口缺少父窗口句柄")
	}
	classOnce.Do(registerViewportClass)
	if classError != nil {
		return classError
	}
	// MyGo presents its surface through a swap chain. A WGL child of that
	// surface is obscured by its presentation even after SwapBuffers succeeds.
	// Keep the viewport above it as a sibling under the application's window.
	n.layoutSurface, _, _ = findWindow.Call(parent, 0, uintptr(unsafe.Pointer(syscall.StringToUTF16Ptr("MyGoSurface"))), 0)
	if n.layoutSurface == 0 {
		n.layoutSurface = parent
	}
	n.parent, n.owner = parent, owner
	instance, _, _ := kernel32.NewProc("GetModuleHandleW").Call(0)
	handle, _, err := createWindow.Call(0, uintptr(unsafe.Pointer(className)), 0, 0x40000000|0x04000000|0x02000000, 0, 0, 1, 1, parent, 0, instance, 0)
	if handle == 0 {
		return fmt.Errorf("创建三维子窗口失败 %w", err)
	}
	n.handle = handle
	windowInstances[handle] = n
	n.dc, _, _ = getDC.Call(handle)
	pfd := pixelFormat{Version: 1, Flags: 0x00000004 | 0x00000020 | 0x00000001, ColorBits: 32, DepthBits: 24, StencilBits: 8}
	pfd.Size = uint16(unsafe.Sizeof(pfd))
	format, _, _ := choosePixelFormat.Call(n.dc, uintptr(unsafe.Pointer(&pfd)))
	if format == 0 {
		n.close()
		return errors.New("没有可用的 OpenGL 深度缓冲像素格式")
	}
	if result, _, _ := setPixelFormat.Call(n.dc, format, uintptr(unsafe.Pointer(&pfd))); result == 0 {
		n.close()
		return errors.New("设置 OpenGL 像素格式失败")
	}
	n.context, _, _ = wglCreateContext.Call(n.dc)
	if n.context == 0 {
		n.close()
		return errors.New("创建 OpenGL 上下文失败")
	}
	if err := n.gl.load(); err != nil {
		n.close()
		return err
	}
	if result, _, err := wglMakeCurrent.Call(n.dc, n.context); result == 0 {
		n.close()
		return fmt.Errorf("激活 OpenGL 上下文失败 %w", err)
	}
	n.renderer = readCString(n.gl.GetString(0x1F01))
	if n.renderer == "" {
		n.renderer = "OpenGL"
	}
	wglMakeCurrent.Call(0, 0)
	n.meshDirty = true
	return nil
}

func readCString(pointer *byte) string {
	if pointer == nil {
		return ""
	}
	b := make([]byte, 0, 128)
	for i := 0; i < 1024; i++ {
		ch := *(*byte)(unsafe.Add(unsafe.Pointer(pointer), i))
		if ch == 0 {
			break
		}
		b = append(b, ch)
	}
	return string(b)
}

func (n *nativeWindow) bounds(x, y, width, height float32) {
	if n.handle == 0 {
		return
	}
	dpi, _, _ := getDPI.Call(n.layoutSurface)
	scale := float64(dpi) / 96
	if scale <= 0 {
		scale = 1
	}
	px, py := int(math.Round(float64(x)*scale)), int(math.Round(float64(y)*scale))
	w, h := int(math.Round(float64(width)*scale)), int(math.Round(float64(height)*scale))
	// Layout coordinates belong to MyGoSurface, while the viewport is now a
	// direct child of the application window. Map the physical client origin.
	point := struct{ X, Y int32 }{int32(px), int32(py)}
	mapWindowPoints.Call(n.layoutSurface, n.parent, uintptr(unsafe.Pointer(&point)), 1)
	px, py = int(point.X), int(point.Y)
	if w < 1 || h < 1 {
		n.show(false)
		return
	}
	if n.x != px || n.y != py || n.width != w || n.height != h {
		n.x, n.y, n.width, n.height = px, py, w, h
		n.owner.resize(w, h)
		setPosition.Call(n.handle, 0, uintptr(px), uintptr(py), uintptr(w), uintptr(h), 0x0010)
		n.invalidate()
	}
	n.show(n.owner.visible && n.owner.Error() == "")
}

func (n *nativeWindow) show(visible bool) {
	if n.handle == 0 || n.shown == visible {
		return
	}
	n.shown = visible
	command := uintptr(0)
	if visible {
		command = 5
	}
	showWindow.Call(n.handle, command)
	if visible {
		// Settings hide the HWND while their overlay is visible. Restore it
		// above the MyGo sibling without moving focus when the overlay closes.
		setPosition.Call(n.handle, 0, 0, 0, 0, 0, 0x0001|0x0002|0x0010)
		n.owner.tick()
		n.invalidate()
		if !n.owner.animationStarted.IsZero() {
			n.animate()
		}
	} else {
		killTimer.Call(n.handle, 1)
	}
}
func (n *nativeWindow) invalidate() {
	if n.handle != 0 {
		invalidateRect.Call(n.handle, 0, 0)
	}
}
func (n *nativeWindow) animate() {
	if n.handle != 0 && n.shown {
		setTimer.Call(n.handle, 1, 16, 0)
		n.invalidate()
	}
}
func (n *nativeWindow) meshChanged() { n.meshDirty = true }
func (n *nativeWindow) close() {
	if n.handle == 0 {
		return
	}
	if n.context != 0 {
		wglMakeCurrent.Call(n.dc, n.context)
		if n.meshList != 0 {
			n.gl.DeleteLists(n.meshList, 1)
		}
		if n.edgeList != 0 {
			n.gl.DeleteLists(n.edgeList, 1)
		}
		wglMakeCurrent.Call(0, 0)
		wglDeleteContext.Call(n.context)
		n.context = 0
	}
	if n.dc != 0 {
		releaseDC.Call(n.handle, n.dc)
		n.dc = 0
	}
	killTimer.Call(n.handle, 1)
	destroyWindow.Call(n.handle)
	delete(windowInstances, n.handle)
	n.handle = 0
	n.meshList, n.edgeList = 0, 0
	n.shown = false
}

func viewportWindowProc(handle uintptr, message uint32, wparam, lparam uintptr) uintptr {
	n := windowInstances[handle]
	if n == nil {
		result, _, _ := defaultWindowProc.Call(handle, uintptr(message), wparam, lparam)
		return result
	}
	switch message {
	case 0x000F:
		var paint [80]byte
		beginPaint.Call(handle, uintptr(unsafe.Pointer(&paint[0])))
		n.render()
		endPaint.Call(handle, uintptr(unsafe.Pointer(&paint[0])))
		return 0
	case 0x0014:
		return 1
	case 0x0113:
		if !n.owner.tick() {
			killTimer.Call(handle, 1)
		}
		n.invalidate()
		return 0
	case 0x0201, 0x0204, 0x0207:
		n.button = message
		n.lastX, n.lastY = int(int16(lparam&0xffff)), int(int16((lparam>>16)&0xffff))
		n.owner.animationStarted = time.Time{}
		killTimer.Call(handle, 1)
		setCapture.Call(handle)
		setFocus.Call(handle)
		return 0
	case 0x0202, 0x0205, 0x0208:
		n.button = 0
		releaseCapture.Call()
		return 0
	case 0x0215:
		n.button = 0
		return 0
	case 0x0200:
		x, y := int(int16(lparam&0xffff)), int(int16((lparam>>16)&0xffff))
		if n.button != 0 {
			dx, dy := float64(x-n.lastX), float64(y-n.lastY)
			if n.button == 0x0201 && wparam&0x0004 == 0 {
				n.owner.Camera.Orbit(dx, dy)
			} else {
				n.owner.Camera.Pan(dx, dy, float64(n.height))
			}
			n.invalidate()
		}
		n.lastX, n.lastY = x, y
		return 0
	case 0x020A:
		n.owner.Zoom(float64(int16((wparam>>16)&0xffff)) / 120)
		return 0
	case 0x0203:
		n.owner.Fit()
		return 0
	case 0x0100:
		switch wparam {
		case 'F':
			n.owner.Fit()
		case '1':
			n.owner.SetView("front")
		case '2':
			n.owner.SetView("back")
		case '3':
			n.owner.SetView("left")
		case '4':
			n.owner.SetView("right")
		case '5':
			n.owner.SetView("top")
		case '6':
			n.owner.SetView("bottom")
		case '7':
			n.owner.SetView("iso")
		default:
			result, _, _ := defaultWindowProc.Call(handle, uintptr(message), wparam, lparam)
			return result
		}
		return 0
	}
	result, _, _ := defaultWindowProc.Call(handle, uintptr(message), wparam, lparam)
	return result
}

func (n *nativeWindow) compileMesh() {
	if !n.meshDirty {
		return
	}
	n.meshDirty = false
	g := &n.gl
	if n.meshList != 0 {
		g.DeleteLists(n.meshList, 1)
		n.meshList = 0
	}
	if n.edgeList != 0 {
		g.DeleteLists(n.edgeList, 1)
		n.edgeList = 0
	}
	m := n.owner.mesh
	if m == nil || len(m.Vertices) < 30 {
		return
	}
	g.EnableClientState(0x8074)
	g.EnableClientState(0x8075)
	g.EnableClientState(0x8076)
	g.VertexPointer(3, 0x1406, 40, &m.Vertices[0])
	g.NormalPointer(0x1406, 40, &m.Vertices[3])
	g.ColorPointer(4, 0x1406, 40, &m.Vertices[6])
	n.meshList = g.GenLists(1)
	g.NewList(n.meshList, 0x1300)
	g.DrawArrays(0x0004, 0, int32(len(m.Vertices)/10))
	g.EndList()
	g.DisableClientState(0x8075)
	g.DisableClientState(0x8076)
	if len(m.Edges) > 0 {
		g.VertexPointer(3, 0x1406, 12, &m.Edges[0])
		n.edgeList = g.GenLists(1)
		g.NewList(n.edgeList, 0x1300)
		g.DrawArrays(0x0001, 0, int32(len(m.Edges)/3))
		g.EndList()
	}
	g.DisableClientState(0x8074)
	runtime.KeepAlive(m)
}

func (n *nativeWindow) render() { n.renderFrame(true) }

func (n *nativeWindow) renderFrame(present bool) {
	if n.context == 0 || n.width < 1 || n.height < 1 {
		return
	}
	if result, _, _ := wglMakeCurrent.Call(n.dc, n.context); result == 0 {
		return
	}
	defer wglMakeCurrent.Call(0, 0)
	g := &n.gl
	v := n.owner
	c := v.Camera
	n.compileMesh()
	g.Viewport(0, 0, int32(n.width), int32(n.height))
	if v.dark {
		g.ClearColor(30.0/255, 31.0/255, 34.0/255, 1)
	} else {
		g.ClearColor(0.925, 0.941, 0.956, 1)
	}
	g.Clear(0x00004000 | 0x00000100)
	n.drawBackground()
	g.Enable(0x0B71)
	g.DepthFunc(0x0203)
	g.MatrixMode(0x1701)
	g.LoadIdentity()
	aspect := float64(n.width) / float64(n.height)
	near := math.Max(c.Radius*0.001, c.Distance-c.Radius*2.2)
	far := math.Max(c.Distance+c.Radius*50, near+1)
	if c.Perspective {
		half := near * math.Tan(math.Pi/8)
		g.Frustum(-half*aspect, half*aspect, -half, half, near, far)
	} else {
		half := c.Distance * math.Tan(math.Pi/8)
		g.Ortho(-half*aspect, half*aspect, -half, half, near, far)
	}
	g.MatrixMode(0x1700)
	g.LoadIdentity()
	ambient := [4]float32{0.30, 0.31, 0.33, 1}
	light := [4]float32{-0.4, 0.8, 1, 0}
	diffuse := [4]float32{0.66, 0.66, 0.64, 1}
	fill := [4]float32{0.8, 0.1, 0.4, 0}
	fillDiffuse := [4]float32{0.22, 0.23, 0.25, 1}
	specular := [4]float32{0.18, 0.18, 0.18, 1}
	g.LightModel(0x0B53, &ambient[0])
	g.Light(0x4000, 0x1203, &light[0])
	g.Light(0x4000, 0x1201, &diffuse[0])
	g.Light(0x4000, 0x1202, &specular[0])
	g.Light(0x4001, 0x1203, &fill[0])
	g.Light(0x4001, 0x1201, &fillDiffuse[0])
	g.Material(0x0408, 0x1202, &specular[0])
	g.MaterialFloat(0x0408, 0x1601, 36)
	right, up, forward := c.Basis()
	eye := c.Eye()
	m := matrix{right.X, up.X, -forward.X, 0, right.Y, up.Y, -forward.Y, 0, right.Z, up.Z, -forward.Z, 0, -right.Dot(eye), -up.Dot(eye), forward.Dot(eye), 1}
	g.LoadMatrix(&m[0])
	if v.grid {
		n.drawGrid()
	}
	if n.meshList != 0 {
		g.Enable(0x0B50)
		g.Enable(0x4000)
		g.Enable(0x4001)
		g.Enable(0x0B57)
		g.Enable(0x0BA1)
		g.ColorMaterial(0x0408, 0x1602)
		g.Enable(0x8037)
		g.PolygonOffset(1, 1)
		g.CallList(n.meshList)
		g.Disable(0x8037)
		g.Disable(0x0B50)
		if v.edges {
			if v.dark {
				g.Color(0.24, 0.27, 0.30, 1)
			} else {
				g.Color(0.31, 0.34, 0.37, 1)
			}
			g.LineWidth(1)
			g.CallList(n.edgeList)
		}
	}
	if v.axes {
		n.drawAxes()
	}
	if present {
		swapBuffers.Call(n.dc)
	}
	v.frames++
}

func (n *nativeWindow) capture() (*image.RGBA, error) {
	if n.context == 0 || n.width < 1 || n.height < 1 {
		return nil, errors.New("三维视口尚未连接窗口或没有有效尺寸")
	}
	n.renderFrame(false)
	if result, _, err := wglMakeCurrent.Call(n.dc, n.context); result == 0 {
		return nil, fmt.Errorf("读取三维帧缓冲失败 %w", err)
	}
	defer wglMakeCurrent.Call(0, 0)
	frame := image.NewRGBA(image.Rect(0, 0, n.width, n.height))
	n.gl.ReadBuffer(0x0405)
	n.gl.ReadPixels(0, 0, int32(n.width), int32(n.height), 0x1908, 0x1401, &frame.Pix[0])
	if code := n.gl.GetError(); code != 0 {
		return nil, fmt.Errorf("OpenGL 读取失败 0x%x", code)
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

func (n *nativeWindow) drawGrid() {
	g := &n.gl
	c := n.owner.Camera
	step := math.Pow(10, math.Floor(math.Log10(math.Max(c.Radius, 0.001))))
	if c.Radius/step < 2 {
		step /= 5
	}
	span := step * 10
	z := 0.0
	if n.owner.mesh != nil {
		z = n.owner.mesh.Min.Z - c.Radius*0.001
	}
	cx := math.Round(c.Target.X/step) * step
	cy := math.Round(c.Target.Y/step) * step
	g.Disable(0x0B50)
	g.Enable(0x0BE2)
	g.BlendFunc(0x0302, 0x0303)
	g.LineWidth(1)
	g.Begin(0x0001)
	for i := -10; i <= 10; i++ {
		alpha := float32(0.10)
		if i%5 == 0 {
			alpha = 0.22
		}
		alpha *= float32(1 - math.Abs(float64(i))/14)
		if n.owner.dark {
			g.Color(0.42, 0.42, 0.42, alpha*0.75)
		} else {
			g.Color(0.40, 0.44, 0.48, alpha)
		}
		d := float64(i) * step
		g.Vertex(cx+d, cy-span, z)
		g.Vertex(cx+d, cy+span, z)
		g.Vertex(cx-span, cy+d, z)
		g.Vertex(cx+span, cy+d, z)
	}
	g.End()
	g.Disable(0x0BE2)
}
