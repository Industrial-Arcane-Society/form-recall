package viewport

import (
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"
)

func TestCameraFitsEveryViewAndAspect(t *testing.T) {
	min, max := Vec3{-10, -5, -2}, Vec3{10, 5, 2}
	for _, view := range []string{"front", "back", "left", "right", "top", "bottom", "iso"} {
		for _, aspect := range []float64{0.25, 1, 1258.0 / 1082, 3} {
			c := NewCamera()
			if !c.View(view) {
				t.Fatal(view)
			}
			c.Fit(min, max, aspect)
			right, up, forward := c.Basis()
			if math.Abs(right.Dot(up)) > 1e-9 || math.Abs(right.Dot(forward)) > 1e-9 || math.Abs(up.Dot(forward)) > 1e-9 {
				t.Fatal("camera basis is not orthogonal")
			}
			coverage := [2]float64{}
			for _, x := range []float64{min.X, max.X} {
				for _, y := range []float64{min.Y, max.Y} {
					for _, z := range []float64{min.Z, max.Z} {
						delta := (Vec3{x, y, z}).Sub(c.Eye())
						depth := delta.Dot(forward)
						for projection, distance := range []float64{depth, c.Distance} {
							half := distance * math.Tan(math.Pi/8)
							extent := math.Max(math.Abs(delta.Dot(up))/half, math.Abs(delta.Dot(right))/(half*aspect))
							if depth <= 0 || extent > 0.9 {
								t.Fatalf("%s aspect %.2f projection %d lacks framing margin", view, aspect, projection)
							}
							coverage[projection] = math.Max(coverage[projection], extent)
						}
					}
				}
			}
			if coverage[0] < 0.08 || coverage[1] < 0.08 {
				t.Fatalf("%s aspect %.2f fits model too small: %v", view, aspect, coverage)
			}
		}
	}
}

func TestAsyncLoadLatestRequestWins(t *testing.T) {
	doc, data := fixture()
	encoded := encodeFixture(t, doc, data)
	dir := t.TempDir()
	first := filepath.Join(dir, "first.glb")
	second := filepath.Join(dir, "second.glb")
	for _, path := range []string{first, second} {
		if err := os.WriteFile(path, encoded, 0600); err != nil {
			t.Fatal(err)
		}
	}
	pending := make(chan func(), 2)
	v := New()
	v.post = func(fn func()) { pending <- fn }
	if err := v.Load(first); err != nil {
		t.Fatal(err)
	}
	if err := v.Load(second); err != nil {
		t.Fatal(err)
	}
	if !v.Loading() {
		t.Fatal("pending GLB is reported as ready for capture")
	}
	for i := 0; i < 2; i++ {
		select {
		case apply := <-pending:
			apply()
		case <-time.After(5 * time.Second):
			t.Fatal("loading worker did not finish")
		}
	}
	if v.mesh == nil || v.mesh.Source != second {
		t.Fatal("older load replaced latest model")
	}
	if v.Loading() {
		t.Fatal("applied GLB is still reported as loading")
	}
	if err := v.Load(first); err != nil {
		t.Fatal(err)
	}
	if err := v.Load(""); err != nil {
		t.Fatal(err)
	}
	select {
	case apply := <-pending:
		apply()
	case <-time.After(5 * time.Second):
		t.Fatal("loading worker did not finish")
	}
	if v.mesh != nil {
		t.Fatal("pending load replaced a cleared model")
	}
	if v.Loading() {
		t.Fatal("cleared model is still reported as loading")
	}
	if err := v.Load(first); err != nil {
		t.Fatal(err)
	}
	if err := v.Load(filepath.Join(dir, "missing.glb")); err == nil || v.Loading() {
		t.Fatal("failed replacement retained the previous loading state")
	}
	select {
	case apply := <-pending:
		apply()
	case <-time.After(5 * time.Second):
		t.Fatal("replaced loading worker did not finish")
	}
	if v.mesh != nil || v.Error() == "" {
		t.Fatal("superseded load replaced a failed current request")
	}
	v.Close()
	if v.Loading() {
		t.Fatal("closed viewport is still reported as loading")
	}
}

func TestCameraNavigationAndAnimation(t *testing.T) {
	c := NewCamera()
	original := c
	c.Orbit(30, 15)
	if c.Yaw == original.Yaw || c.Pitch == original.Pitch {
		t.Fatal("orbit had no effect")
	}
	c.Pan(15, 20, 600)
	if c.Target == original.Target {
		t.Fatal("pan had no effect")
	}
	c.Zoom(2)
	if c.Distance >= original.Distance {
		t.Fatal("zoom did not approach model")
	}
	c.Zoom(1e9)
	if c.Distance <= 0 || math.IsInf(c.Distance, 0) {
		t.Fatal("zoom became invalid")
	}
	from, to := NewCamera(), NewCamera()
	from.Yaw = math.Pi - 0.1
	to.Yaw = -math.Pi + 0.1
	middle := mixCamera(from, to, 0.5)
	if math.Abs(middle.Yaw-math.Pi) > 1e-9 {
		t.Fatal("animation took the long turn")
	}
	v := New()
	v.SetView("front")
	v.animationStarted = time.Now().Add(-time.Second)
	if v.tick() || v.Camera.Yaw != -math.Pi/2 {
		t.Fatal("animation failed to settle")
	}
}

func TestViewportHeadlessLayoutAndError(t *testing.T) {
	v := New()
	tester := ui.NewTester(func(c *ui.Context) { ui.Column(c).Fill().Children(func() { ui.Text(c, "模型视口"); v.View(c) }) }, 640, 480)
	if v.layout.W < 600 || v.layout.H < 300 {
		t.Fatalf("invalid viewport layout %+v", v.layout)
	}
	if err := v.Load("does-not-exist.glb"); err == nil {
		t.Fatal("missing file accepted")
	}
	tester.Frame()
	if !tester.HasText("三维视口加载失败") {
		t.Fatal("loading failure is not visible")
	}
	if err := v.Load(""); err != nil || v.Error() != "" {
		t.Fatal("clear did not reset model error")
	}
}
