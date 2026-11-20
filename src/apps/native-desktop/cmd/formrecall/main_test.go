package main

import (
	"flag"
	"path/filepath"
	"testing"
)

func TestConfigEnvironmentAndFlagPrecedence(t *testing.T) {
	directory := t.TempDir()
	for _, test := range []struct {
		name, explicit, config, legacy, want string
	}{
		{name: "host default"},
		{name: "legacy data directory", legacy: directory, want: filepath.Join(directory, "config.json")},
		{name: "config environment wins", config: "environment.json", legacy: directory, want: "environment.json"},
		{name: "explicit flag wins", explicit: "flag.json", config: "environment.json", legacy: directory, want: "flag.json"},
	} {
		t.Run(test.name, func(t *testing.T) {
			t.Setenv("AIMODELER_CONFIG_PATH", test.config)
			t.Setenv("AIMODELER_DATA_DIR", test.legacy)
			flags := flag.NewFlagSet(test.name, flag.ContinueOnError)
			config := flags.String("config", configFromEnvironment(), "")
			var arguments []string
			if test.explicit != "" {
				arguments = []string{"--config", test.explicit}
			}
			if err := flags.Parse(arguments); err != nil {
				t.Fatal(err)
			}
			if *config != test.want {
				t.Fatalf("config path = %q, want %q", *config, test.want)
			}
		})
	}
}
