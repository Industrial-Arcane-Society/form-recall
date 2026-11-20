//go:build windows

package viewport

import "math"

func (n *nativeWindow) drawBackground() {
	g := &n.gl
	g.Disable(0x0B71)
	g.Disable(0x0B50)
	g.MatrixMode(0x1701)
	g.LoadIdentity()
	g.MatrixMode(0x1700)
	g.LoadIdentity()
	g.Begin(0x0007)
	if n.owner.dark {
		g.Color(30.0/255, 31.0/255, 34.0/255, 1)
	} else {
		g.Color(0.89, 0.90, 0.91, 1)
	}
	g.Vertex(-1, -1, 0)
	g.Vertex(1, -1, 0)
	if n.owner.dark {
		g.Color(35.0/255, 36.0/255, 40.0/255, 1)
	} else {
		g.Color(0.97, 0.975, 0.98, 1)
	}
	g.Vertex(1, 1, 0)
	g.Vertex(-1, 1, 0)
	g.End()
}

// The orientation triad stays in screen space while its three directions follow
// the camera. It never draws an axis through the engineering model.
func (n *nativeWindow) drawAxes() {
	g := &n.gl
	dpi, _, _ := getDPI.Call(n.parent)
	scale := math.Max(1, float64(dpi)/96)
	centerX, centerY, length := 50*scale, 48*scale, 27*scale
	right, up, _ := n.owner.Camera.Basis()
	g.Disable(0x0B71)
	g.Disable(0x0B50)
	g.Enable(0x0BE2)
	g.BlendFunc(0x0302, 0x0303)
	g.Enable(0x0B20)
	g.MatrixMode(0x1701)
	g.LoadIdentity()
	g.Ortho(0, float64(n.width), 0, float64(n.height), -1, 1)
	g.MatrixMode(0x1700)
	g.LoadIdentity()
	g.LineWidth(float32(1.5 * scale))
	directions := []struct {
		vector Vec3
		color  [3]float32
		letter byte
	}{
		{Vec3{1, 0, 0}, [3]float32{0.66, 0.29, 0.28}, 'X'},
		{Vec3{0, 1, 0}, [3]float32{0.28, 0.52, 0.38}, 'Y'},
		{Vec3{0, 0, 1}, [3]float32{0.28, 0.43, 0.64}, 'Z'},
	}
	for _, axis := range directions {
		x, y := axis.vector.Dot(right), axis.vector.Dot(up)
		endX, endY := centerX+x*length, centerY+y*length
		g.Color(axis.color[0], axis.color[1], axis.color[2], 1)
		g.Begin(0x0001)
		g.Vertex(centerX, centerY, 0)
		g.Vertex(endX, endY, 0)
		g.End()
		magnitude := math.Hypot(x, y)
		if magnitude < 0.1 {
			x, y = 0, -1
			magnitude = 1
		}
		labelX, labelY := endX+x/magnitude*9*scale, endY+y/magnitude*9*scale
		n.drawAxisLetter(axis.letter, labelX, labelY, scale)
	}
	g.Disable(0x0B20)
	g.Disable(0x0BE2)
	g.LineWidth(1)
}

func (n *nativeWindow) drawAxisLetter(letter byte, x, y, scale float64) {
	var lines [][4]float64
	switch letter {
	case 'X':
		lines = [][4]float64{{-3, -4, 3, 4}, {-3, 4, 3, -4}}
	case 'Y':
		lines = [][4]float64{{-3, 4, 0, 0}, {3, 4, 0, 0}, {0, 0, 0, -4}}
	case 'Z':
		lines = [][4]float64{{-3, 4, 3, 4}, {3, 4, -3, -4}, {-3, -4, 3, -4}}
	}
	g := &n.gl
	g.Begin(0x0001)
	for _, line := range lines {
		g.Vertex(x+line[0]*scale, y+line[1]*scale, 0)
		g.Vertex(x+line[2]*scale, y+line[3]*scale, 0)
	}
	g.End()
}
