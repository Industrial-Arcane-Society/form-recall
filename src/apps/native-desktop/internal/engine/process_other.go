//go:build !windows

package engine

import "os/exec"

func hideConsole(command *exec.Cmd) {}
