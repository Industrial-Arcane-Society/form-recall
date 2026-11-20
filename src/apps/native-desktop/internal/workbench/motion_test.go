package workbench

import (
	"math"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"
)

func viewportBounds(t *testing.T, tt *ui.Tester) ui.Rect {
	t.Helper()
	r, ok := tt.Find("模型视口")
	if !ok || r.W <= 0 || r.H <= 0 {
		t.Fatal("model viewport lost its usable layout")
	}
	return r
}

func TestPanelMotionReflowsViewportAndReverses(t *testing.T) {
	a, tt, _ := newUITest(t)
	tt.Advance(time.Second)
	start := viewportBounds(t, tt)
	if err := tt.Click("属性"); err != nil {
		t.Fatal(err)
	}
	tt.Advance(60 * time.Millisecond)
	middle := viewportBounds(t, tt)
	if middle.X >= start.X || middle.X <= 2 || middle.W <= start.W {
		t.Fatalf("viewport did not follow the closing panel: start=%+v middle=%+v", start, middle)
	}
	if err := tt.Click("属性"); err != nil {
		t.Fatal(err)
	}
	tt.Advance(300 * time.Millisecond)
	restored := viewportBounds(t, tt)
	if math.Abs(float64(restored.X-start.X)) > 1 || a.leftWidth != 286 {
		t.Fatalf("reversing the animation changed the saved panel width: %+v width=%v", restored, a.leftWidth)
	}
	if err := tt.Click("日志"); err != nil {
		t.Fatal(err)
	}
	tt.Advance(60 * time.Millisecond)
	middle = viewportBounds(t, tt)
	if middle.H >= restored.H || middle.H <= restored.H-180 {
		t.Fatalf("viewport did not follow the opening log panel: start=%+v middle=%+v", restored, middle)
	}
	tt.Advance(250 * time.Millisecond)
	finished := viewportBounds(t, tt)
	if math.Abs(float64(restored.H-finished.H-180)) > 1 {
		t.Fatalf("log panel did not settle at its intended size: %+v", finished)
	}
	click(t, tt, "收起")
	if end := viewportBounds(t, tt); math.Abs(float64(end.H-start.H)) > 1 {
		t.Fatalf("closing logs did not restore the viewport: %+v", end)
	}
}

func TestReducedMotionChangesPanelLayoutImmediately(t *testing.T) {
	_, tt, _ := newUITest(t)
	tt.SetPreferences(ui.Preferences{ReduceMotion: true, TextScale: 1})
	start := viewportBounds(t, tt)
	if err := tt.Click("属性"); err != nil {
		t.Fatal(err)
	}
	closed := viewportBounds(t, tt)
	if closed.X > 2 || closed.W <= start.W {
		t.Fatalf("reduced motion left the sidebar in transition: %+v", closed)
	}
	if err := tt.Click("日志"); err != nil {
		t.Fatal(err)
	}
	if logs := viewportBounds(t, tt); math.Abs(float64(closed.H-logs.H-180)) > 1 {
		t.Fatalf("reduced motion left the logs in transition: %+v", logs)
	}
}

type overlayScene struct {
	testScene
	visible bool
}

func (s *overlayScene) SetVisible(visible bool) { s.visible = visible }
func (s *overlayScene) SetReducedMotion(bool)   {}

func TestModalExitKeepsNativeViewportBehindOverlay(t *testing.T) {
	for _, reduced := range []bool{false, true} {
		a, tt, _ := newUITest(t)
		scene := &overlayScene{}
		a.scene = scene
		tt.SetPreferences(ui.Preferences{ReduceMotion: reduced, TextScale: 1})
		click(t, tt, "设置")
		if scene.visible {
			t.Fatal("native child window covers the settings dialog")
		}
		if err := tt.Click("取消"); err != nil {
			t.Fatal(err)
		}
		if scene.visible != reduced {
			t.Fatalf("native viewport visibility=%t during dismissal with reduced motion=%t", scene.visible, reduced)
		}
		tt.Advance(180 * time.Millisecond)
		if !scene.visible {
			t.Fatal("native viewport did not return after the settings exit")
		}
	}
}
