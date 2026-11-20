package viewport

import "math"

type Camera struct {
	Target               Vec3
	Distance, Yaw, Pitch float64
	Perspective          bool
	Radius               float64
}

func NewCamera() Camera {
	return Camera{Distance: 5, Yaw: math.Pi / 4, Pitch: math.Atan(1 / math.Sqrt2), Perspective: true, Radius: 1}
}

func (c *Camera) Fit(min, max Vec3, aspect float64) {
	c.Target = min.Add(max).Mul(0.5)
	c.Radius = math.Max(max.Sub(min).Length()*0.5, 0.001)
	limiting := math.Atan(math.Tan(math.Pi/8) * math.Min(1, math.Max(aspect, 0.1)))
	c.Distance = c.Radius / math.Sin(limiting) * 1.232
}

func (c Camera) Eye() Vec3 {
	cosPitch := math.Cos(c.Pitch)
	return c.Target.Add(Vec3{cosPitch * math.Cos(c.Yaw), cosPitch * math.Sin(c.Yaw), math.Sin(c.Pitch)}.Mul(c.Distance))
}

func (c Camera) Basis() (right, up, forward Vec3) {
	forward = c.Target.Sub(c.Eye()).Unit()
	// The analytic horizontal vector stays defined at the top/bottom views.
	right = Vec3{-math.Sin(c.Yaw), math.Cos(c.Yaw), 0}
	up = right.Cross(forward).Unit()
	return
}

func (c *Camera) Orbit(dx, dy float64) {
	c.Yaw -= dx * 0.008
	c.Pitch = math.Max(-math.Pi/2+0.001, math.Min(math.Pi/2-0.001, c.Pitch+dy*0.008))
}

func (c *Camera) Pan(dx, dy, viewportHeight float64) {
	right, up, _ := c.Basis()
	units := 2 * c.Distance * math.Tan(math.Pi/8) / math.Max(1, viewportHeight)
	c.Target = c.Target.Add(right.Mul(-dx * units)).Add(up.Mul(dy * units))
}

func (c *Camera) Zoom(steps float64) {
	c.Distance *= math.Exp(-steps * 0.14)
	c.Distance = math.Max(c.Radius*0.025, math.Min(c.Radius*10000, c.Distance))
}

func (c *Camera) View(name string) bool {
	switch name {
	case "等轴", "等轴测", "iso", "isometric":
		c.Yaw, c.Pitch = math.Pi/4, math.Atan(1/math.Sqrt2)
	case "前", "front":
		c.Yaw, c.Pitch = -math.Pi/2, 0
	case "后", "back":
		c.Yaw, c.Pitch = math.Pi/2, 0
	case "左", "left":
		c.Yaw, c.Pitch = math.Pi, 0
	case "右", "right":
		c.Yaw, c.Pitch = 0, 0
	case "顶", "top":
		c.Yaw, c.Pitch = -math.Pi/2, math.Pi/2
	case "底", "bottom":
		c.Yaw, c.Pitch = -math.Pi/2, -math.Pi/2
	default:
		return false
	}
	return true
}

func mixCamera(from, to Camera, progress float64) Camera {
	t := progress * progress * (3 - 2*progress)
	delta := math.Remainder(to.Yaw-from.Yaw, 2*math.Pi)
	to.Yaw = from.Yaw + delta*t
	to.Pitch = from.Pitch + (to.Pitch-from.Pitch)*t
	to.Distance = from.Distance + (to.Distance-from.Distance)*t
	to.Target = from.Target.Add(to.Target.Sub(from.Target).Mul(t))
	return to
}
