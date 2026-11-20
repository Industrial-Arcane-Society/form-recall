package engine

import (
	"os/exec"
	"syscall"
)

func hideConsole(command *exec.Cmd) {
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
}
