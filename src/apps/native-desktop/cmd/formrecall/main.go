package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime/debug"
	"time"

	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/engine"
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
	enginePath := flag.String("engine", envOr("FORMRECALL_ENGINE", filepath.Join(bundle, "formrecall-engine-host.exe")), "CAD host executable")
	resources := flag.String("resources", envOr("AIMODELER_RESOURCE_ROOT", envOr("AIMODELER_ROOT", filepath.Join(bundle, "app"))), "CAD resource root")
	data := flag.String("data", envOr("AIMODELER_DESKTOP_DATA_DIR", filepath.Join(os.Getenv("LOCALAPPDATA"), "com.aimodeler.desktop")), "Local data directory")
	config := flag.String("config", configFromEnvironment(), "Optional settings file")
	screenshot := flag.String("screenshot", "", "Capture the native window after loading")
	capturePanel := flag.String("capture-panel", "", "Optional capture panel: settings")
	demo := flag.String("demo-prompt", "", "Generate a model using the normal Quick workflow")
	exitAfter := flag.Duration("exit-after", 0*time.Second, "Close after capture for automated verification")
	width := flag.Int("width", 1440, "Initial window width")
	height := flag.Int("height", 900, "Initial window height")
	showVersion := flag.Bool("version", false, "Print the desktop version and exit")
	versionJSON := flag.Bool("version-json", false, "Print the desktop version and source commit as JSON and exit")
	flag.Parse()
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
	options := workbench.Options{Engine: engine.Options{Executable: absolute(*enginePath), Resources: absolute(*resources), DataDir: absolute(*data), Config: *config},
		Screenshot: *screenshot, CapturePanel: *capturePanel, DemoPrompt: *demo, ExitAfter: *exitAfter, Width: *width, Height: *height}
	if err := workbench.Run(options, viewport.New()); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
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
