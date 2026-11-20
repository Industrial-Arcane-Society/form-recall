//go:build ignore

// Run go generate ./cmd/formrecall from an x64 Visual Studio Developer Prompt.
package main

import (
	"bytes"
	"debug/pe"
	"encoding/binary"
	"flag"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/branding"
)

func main() {
	rc := flag.String("rc", "rc.exe", "Windows resource compiler")
	cvtres := flag.String("cvtres", "cvtres.exe", "MSVC resource converter")
	flag.Parse()
	if err := generate(*rc, *cvtres); err != nil {
		log.Fatal(err)
	}
}

func generate(rc, cvtres string) error {
	icon, err := makeICO()
	if err != nil {
		return err
	}
	temp, err := os.MkdirTemp("", "formrecall-icon-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(temp)

	if err := os.WriteFile(filepath.Join(temp, "formrecall.ico"), icon, 0644); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(temp, "icon.rc"), []byte("1 ICON \"formrecall.ico\"\r\n"), 0644); err != nil {
		return err
	}
	for _, command := range []*exec.Cmd{
		exec.Command(rc, "/nologo", "/fo", "icon.res", "icon.rc"),
		exec.Command(cvtres, "/nologo", "/machine:x64", "/out:icon.syso", "icon.res"),
	} {
		command.Dir = temp
		if output, err := command.CombinedOutput(); err != nil {
			return fmt.Errorf("%s: %w\n%s", command.Args[0], err, output)
		}
	}
	resource, err := os.ReadFile(filepath.Join(temp, "icon.syso"))
	if err != nil {
		return err
	}
	object, err := pe.NewFile(bytes.NewReader(resource))
	if err != nil {
		return err
	}
	// Mark MSVC's compiler ID as debug metadata so Go's internal linker skips it.
	for index, symbol := range object.COFFSymbols {
		if string(symbol.Name[:]) == "@comp.id" && symbol.SectionNumber == -1 {
			const sectionNumberOffset = 12
			offset := int(object.PointerToSymbolTable) + index*pe.COFFSymbolSize + sectionNumberOffset
			binary.LittleEndian.PutUint16(resource[offset:], 0xfffe) // IMAGE_SYM_DEBUG
		}
	}
	// The COFF timestamp is unnecessary and changes on every invocation.
	clear(resource[4:8])
	if err := os.WriteFile("icon_windows_amd64.syso", resource, 0644); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join("..", "..", "internal", "branding", "formrecall.ico"), icon, 0644)
}

func makeICO() ([]byte, error) {
	sizes := []int{16, 20, 24, 32, 40, 48, 64, 128, 256}
	// An ICO has a 6-byte header, followed by 16-byte entries and image payloads.
	icon := make([]byte, 6+16*len(sizes))
	binary.LittleEndian.PutUint16(icon[2:], 1) // icon, not cursor
	binary.LittleEndian.PutUint16(icon[4:], uint16(len(sizes)))
	var images bytes.Buffer
	for index, size := range sizes {
		image, err := branding.PNG(size)
		if err != nil {
			return nil, err
		}
		entry := icon[6+16*index:]
		entry[0], entry[1] = byte(size), byte(size)  // 256 is stored as zero
		binary.LittleEndian.PutUint16(entry[4:], 1)  // color planes
		binary.LittleEndian.PutUint16(entry[6:], 32) // bits per pixel
		binary.LittleEndian.PutUint32(entry[8:], uint32(len(image)))
		binary.LittleEndian.PutUint32(entry[12:], uint32(len(icon)+images.Len()))
		images.Write(image)
	}
	return append(icon, images.Bytes()...), nil
}
