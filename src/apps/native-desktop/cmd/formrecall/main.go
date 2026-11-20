package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime/debug"
	"strings"
	"time"

	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/engine"
	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/update"
	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/viewport"
	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/workbench"
)

var version = "dev"

func main() {
	executable, err := os.Executable()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	bundle := filepath.Dir(executable)
	updater := update.NewClient(version, bundle)
	enginePath := flag.String("engine", envOr("FORMRECALL_ENGINE", filepath.Join(bundle, "formrecall-engine-host.exe")), "CAD host executable")
	resources := flag.String("resources", envOr("AIMODELER_RESOURCE_ROOT", envOr("AIMODELER_ROOT", filepath.Join(bundle, "app"))), "CAD resource root")
	data := flag.String("data", envOr("AIMODELER_DESKTOP_DATA_DIR", filepath.Join(os.Getenv("LOCALAPPDATA"), "com.aimodeler.desktop")), "Local data directory")
	config := flag.String("config", configFromEnvironment(), "Optional settings file")
	screenshot := flag.String("screenshot", "", "Capture the native window after loading")
	capturePanel := flag.String("capture-panel", "", "Optional capture panel: settings or updates")
	demo := flag.String("demo-prompt", "", "Generate a model using the normal Quick workflow")
	exitAfter := flag.Duration("exit-after", 0*time.Second, "Close after capture for automated verification")
	width := flag.Int("width", 1440, "Initial window width")
	height := flag.Int("height", 900, "Initial window height")
	showVersion := flag.Bool("version", false, "Print the desktop version and exit")
	versionJSON := flag.Bool("version-json", false, "Print the desktop version and source commit as JSON and exit")
	applyUpdate := flag.String("apply-update", "", "Activate a verified staged update")
	updateHealth := flag.String("update-health", "", "Report startup to the launcher")
	stageUpdate := flag.Bool("stage-update", false, "Download and verify the available update without activating it")
	flag.Parse()
	if *applyUpdate != "" && updater != nil {
		args := withoutUpdateArguments(os.Args[1:])
		if err := updater.Apply(*applyUpdate, args); err != nil {
			fmt.Fprintln(os.Stderr, err)
			_ = startDesktop(executable, args, false)
			os.Exit(1)
		}
		return
	}
	if active := updater.ActiveExecutable(); active != "" {
		if err := startDesktop(active, os.Args[1:], true); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if *stageUpdate {
		if updater == nil {
			os.Exit(1)
		}
		offer, err := updater.Check(context.Background())
		if err == nil && offer != nil {
			var slot string
			var received update.Progress
			slot, err = updater.Stage(context.Background(), *offer, func(progress update.Progress) {
				received = progress
			})
			if err == nil {
				_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"slot": slot, "version": offer.Version, "bytes": received.Received, "delta": received.Delta})
				return
			}
		}
		fmt.Fprintln(os.Stderr, "更新不可用：", err)
		os.Exit(1)
	}
	if *versionJSON {
		commit := ""
		if build, ok := debug.ReadBuildInfo(); ok {
			for _, setting := range build.Settings {
				if setting.Key == "vcs.revision" {
					commit = setting.Value
				}
			}
		}
		if err := json.NewEncoder(os.Stdout).Encode(map[string]string{"version": version, "commit": commit}); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if *showVersion {
		fmt.Println("FormRecall", version)
		return
	}
	var pending string
	options := workbench.Options{Engine: engine.Options{Executable: absolute(*enginePath), Resources: absolute(*resources), DataDir: absolute(*data), Config: *config},
		Screenshot: *screenshot, CapturePanel: *capturePanel, DemoPrompt: *demo, ExitAfter: *exitAfter, Width: *width, Height: *height}
	if updater != nil {
		if (*screenshot == "" && *exitAfter == 0 && *demo == "") || *capturePanel == "updates" {
			options.Updater = updater
			options.RestartUpdate = func(slot string) { pending = slot }
		}
		if *updateHealth != "" && updater.Root != updater.Bundle {
			options.UpdateReady = func() { update.ReportReady(updater.Root, *updateHealth, version) }
		}
	}
	if err := workbench.Run(options, viewport.New()); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if pending != "" {
		if err := updater.Queue(pending, withoutUpdateArguments(os.Args[1:])); err != nil {
			fmt.Fprintln(os.Stderr, err)
			_ = startDesktop(executable, withoutUpdateArguments(os.Args[1:]), false)
			os.Exit(1)
		}
	}
}

func startDesktop(executable string, args []string, wait bool) error {
	cmd := exec.Command(executable, args...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	if wait {
		return cmd.Run()
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	return cmd.Process.Release()
}

func withoutUpdateArguments(args []string) []string {
	result := make([]string, 0, len(args))
	for i := 0; i < len(args); i++ {
		arg := args[i]
		key, _, inline := strings.Cut(strings.TrimLeft(arg, "-"), "=")
		if key == "apply-update" || key == "update-health" {
			if !inline {
				i++
			}
			continue
		}
		result = append(result, arg)
	}
	return result
}

func envOr(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func configFromEnvironment() string {
	if path := os.Getenv("AIMODELER_CONFIG_PATH"); path != "" {
		return path
	}
	if directory := os.Getenv("AIMODELER_DATA_DIR"); directory != "" {
		return filepath.Join(directory, "config.json")
	}
	return ""
}

func absolute(path string) string {
	value, err := filepath.Abs(path)
	if err != nil {
		return path
	}
	return value
}
