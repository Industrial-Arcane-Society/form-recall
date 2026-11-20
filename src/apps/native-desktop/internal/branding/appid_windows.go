package branding

import (
	"fmt"
	"syscall"
	"unsafe"
)

// Keep this ID in sync with the shortcuts in FormRecall.iss.
const appID = "FormRecall.Desktop"

func SetApplicationID() error {
	setID := syscall.NewLazyDLL("shell32.dll").NewProc("SetCurrentProcessExplicitAppUserModelID")
	id, err := syscall.UTF16PtrFromString(appID)
	if err != nil {
		return err
	}
	result, _, _ := setID.Call(uintptr(unsafe.Pointer(id)))
	if int32(result) < 0 {
		return fmt.Errorf("set Windows application ID: HRESULT 0x%08x", uint32(result))
	}
	return nil
}
