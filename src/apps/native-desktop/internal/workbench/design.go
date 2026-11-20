package workbench

import (
	"embed"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/egoist/mygo/ui"
)

func workbenchTheme(dark bool) *ui.Theme {
	t := ui.LightTheme()
	if dark {
		t = ui.DarkTheme()
	}
	t.Font = "Noto Sans SC, Segoe UI, Microsoft YaHei UI, Microsoft YaHei, sans-serif"
	t.FontSize = 13
	t.Radius = 4
	t.Spacing = 4
	t.Accent = ui.Hex("#3574F0")
	t.AccentHover = ui.Hex("#467FF2")
	t.AccentPressed = ui.Hex("#366ACE")
	t.AccentText = ui.Hex("#FFFFFF")
	if dark {
		t.Background = ui.Hex("#1E1F22")
		t.Surface = ui.Hex("#2B2D30")
		t.SurfaceHover = ui.Hex("#393B40")
		t.SurfacePressed = ui.Hex("#43454A")
		t.Border = ui.Hex("#393B40")
		t.Text = ui.Hex("#DFE1E5")
		t.TextMuted = ui.Hex("#9DA0A8")
		t.Selection = ui.Hex("#2E436E")
	} else {
		t.Background = ui.Hex("#EEF0F4")
		t.Surface = ui.Hex("#FAFBFD")
		t.SurfaceHover = ui.Hex("#E9EDF4")
		t.SurfacePressed = ui.Hex("#DFE5EF")
		t.Border = ui.Hex("#DDE2EA")
		t.Text = ui.Hex("#273344")
		t.TextMuted = ui.Hex("#748094")
		t.Selection = ui.Hex("#E1E8FC")
	}
	return t
}

//go:embed icons/*.svg
var iconFiles embed.FS

var icons = sync.OnceValue(func() map[string]*ui.SVG {
	entries, err := iconFiles.ReadDir("icons")
	if err != nil {
		panic(err)
	}
	result := make(map[string]*ui.SVG, len(entries))
	for _, entry := range entries {
		data, err := iconFiles.ReadFile("icons/" + entry.Name())
		if err != nil {
			panic(err)
		}
		result[strings.TrimSuffix(entry.Name(), filepath.Ext(entry.Name()))] = ui.MustParseSVG(data)
	}
	return result
})

func glyph(c *ui.Context, name string, color ui.Color) ui.Element {
	return ui.Icon(c, icons()[name]).Size(18, 18).TextColor(color)
}

func iconButton(c *ui.Context, label, icon string, active bool) ui.Element {
	t := c.Theme()
	button := ui.ButtonBase(c).Key(label).Size(32, 32).Radius(4).Label(label).Tooltip(label).Transition(controlTransition)
	color := t.TextMuted
	if active {
		button.Background(t.Selection)
		color = t.Accent
	} else if button.Hovered() {
		button.Background(t.SurfaceHover)
		color = t.Text
	}
	if button.Pressed() {
		button.Background(t.SurfacePressed)
		color = t.Text
	}
	color = button.AnimateColor("foreground", color, ui.Animation{Duration: 120 * time.Millisecond})
	button.Children(func() { glyph(c, icon, color) })
	return button
}

func toolbarDivider(c *ui.Context) {
	ui.Box(c).Width(1).Height(20).Margin(0, 8).Background(c.Theme().Border)
}
func toolButton(c *ui.Context, label, icon string, active bool) ui.Element {
	t := c.Theme()
	button := ui.ButtonBase(c).Key(label).Height(30).Padding(0, 9).Gap(7).Radius(4).Label(label).Tooltip(label).Transition(controlTransition)
	color := t.TextMuted
	if active {
		button.Background(t.Selection)
		color = t.Accent
	} else if button.Hovered() {
		button.Background(t.SurfaceHover)
		color = t.Text
	}
	if button.Pressed() {
		button.Background(t.SurfacePressed)
		color = t.Text
	}
	color = button.AnimateColor("foreground", color, ui.Animation{Duration: 120 * time.Millisecond})
	button.Children(func() {
		if icon != "" {
			glyph(c, icon, color)
		}
		ui.Text(c, label).FontSize(12).TextColor(color)
	})
	return button
}

var controlTransition = ui.ElementTransition{Duration: 120 * time.Millisecond, Colors: true}

func sectionRule(c *ui.Context) { ui.Box(c).FillWidth().Height(1).Background(c.Theme().Border) }

func sectionTitle(c *ui.Context, label string) {
	ui.Text(c, label).FontSize(11).FontWeight(600).TextColor(c.Theme().TextMuted)
}
