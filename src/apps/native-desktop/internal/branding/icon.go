package branding

import (
	"bytes"
	_ "embed"
	"image/color"
	"image/png"

	"github.com/egoist/mygo/ui"
)

//go:embed icon.svg
var source []byte

var Icon = ui.MustParseSVG(source)

func PNG(size int) ([]byte, error) {
	var buffer bytes.Buffer
	err := png.Encode(&buffer, Icon.Image(size, size, color.White))
	return buffer.Bytes(), err
}
