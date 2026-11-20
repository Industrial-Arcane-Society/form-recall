package update

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const layoutVersion = "0.3.1-alpha.1"

func shaTest(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func writeTestFile(t *testing.T, root, name string, data []byte) string {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(name))
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

// writeBaseVersion creates the bundled version the way a full package ships it.
func writeBaseVersion(t *testing.T, root string) map[string][]byte {
	t.Helper()
	contents := map[string][]byte{
		"FormRecall.exe":                     []byte("packaged desktop binary"),
		"formrecall-engine-host.exe":         []byte("packaged engine"),
		"occt-runtime/runtime-manifest.json": []byte(`{"schema":1}`),
	}
	files := make([]File, 0, len(contents))
	for name, data := range contents {
		writeTestFile(t, baseDir(root), name, data)
		files = append(files, File{Path: name, SHA256: shaTest(data), Bytes: int64(len(data))})
	}
	record, err := json.Marshal(map[string]any{
		"schema": 1, "version": layoutVersion, "source_commit": strings.Repeat("b", 40), "files": files,
	})
	if err != nil {
		t.Fatal(err)
	}
	writeTestFile(t, baseDir(root), "package-manifest.json", record)
	return contents
}

func zipEntries(t *testing.T, entries map[string][]byte) []byte {
	t.Helper()
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	for name, data := range entries {
		entry, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write(data); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return buffer.Bytes()
}

func TestRootForBundleResolvesContainerLayouts(t *testing.T) {
	root := filepath.Join(t.TempDir(), "FormRecall")
	if got := RootForBundle(baseDir(root)); got != root {
		t.Fatalf("base bundle resolved to %s", got)
	}
	payload := filepath.Join(versionsDir(root), "release-12", "payload")
	if got := RootForBundle(payload); got != root {
		t.Fatalf("payload resolved to %s", got)
	}
	plain := filepath.Join(t.TempDir(), "loose")
	if got := RootForBundle(plain); got != plain {
		t.Fatalf("loose bundle resolved to %s", got)
	}
	lookalike := filepath.Join(t.TempDir(), "release-3", "payload")
	if got := RootForBundle(lookalike); got != lookalike {
		t.Fatalf("lookalike payload resolved to %s", got)
	}
}

func TestLauncherSelectsBundledVersionBeforeAnyUpdate(t *testing.T) {
	root := t.TempDir()
	writeBaseVersion(t, root)
	launcher := NewClient(layoutVersion, root)
	if launcher == nil || !launcher.IsLauncher() {
		t.Fatal("program root must act as the launcher")
	}
	if active := launcher.ActiveExecutable(); active != "" {
		t.Fatalf("fresh package selected %s", active)
	}
	base := launcher.BaseExecutable()
	if base != filepath.Join(baseDir(root), "FormRecall.exe") {
		t.Fatalf("bundled version not selected: %q", base)
	}
	if err := launcher.RecordLauncher(); err != nil {
		t.Fatal(err)
	}
	desktop := NewClient(layoutVersion, baseDir(root))
	if desktop == nil || desktop.IsLauncher() || desktop.Root != root {
		t.Fatalf("bundled version did not resolve the program root: %+v", desktop)
	}
	if !desktop.SupportsChannelSwitch() || !desktop.SupportsDowngrade() {
		t.Fatal("launcher abilities were not published for the bundled version")
	}
	writeTestFile(t, baseDir(root), "FormRecall.exe", []byte("tampered"))
	if got := launcher.BaseExecutable(); got != "" {
		t.Fatalf("tampered bundled version was accepted: %s", got)
	}
}

func TestCommitDropsBaseAndKeepsOneRollbackVersion(t *testing.T) {
	root := t.TempDir()
	writeBaseVersion(t, root)
	versions := versionsDir(root)
	for _, slot := range []string{"release-2", "release-5", "release-7"} {
		writeTestFile(t, filepath.Join(versions, slot), "manifest.json", []byte(`{}`))
		writeTestFile(t, filepath.Join(versions, slot), "payload/FormRecall.exe", []byte("payload"))
	}
	writeTestFile(t, versions, "startup-abc/ready", []byte(""))
	launcher := NewClient(layoutVersion, root)
	if err := launcher.RecordLauncher(); err != nil {
		t.Fatal(err)
	}
	if err := launcher.commit("release-7", "release-5"); err != nil {
		t.Fatal(err)
	}
	var state activeVersion
	raw, err := os.ReadFile(filepath.Join(versions, "active.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &state); err != nil {
		t.Fatal(err)
	}
	if state.Current != "release-7" || state.Previous != "release-5" || state.RootVersion != layoutVersion {
		t.Fatalf("unexpected active state: %+v", state)
	}
	if _, err := os.Stat(baseDir(root)); !os.IsNotExist(err) {
		t.Fatalf("bundled version survived activation: %v", err)
	}
	if _, err := os.Stat(filepath.Join(versions, "release-2")); !os.IsNotExist(err) {
		t.Fatalf("older version survived activation: %v", err)
	}
	for _, keep := range []string{"release-5", "release-7", "launcher.json", "startup-abc"} {
		if _, err := os.Stat(filepath.Join(versions, keep)); err != nil {
			t.Fatalf("%s was removed: %v", keep, err)
		}
	}
}

func TestFirstActivationKeepsBaseAsRollback(t *testing.T) {
	root := t.TempDir()
	writeBaseVersion(t, root)
	versions := versionsDir(root)
	writeTestFile(t, filepath.Join(versions, "release-1"), "manifest.json", []byte(`{}`))
	launcher := NewClient(layoutVersion, root)
	if err := launcher.commit("release-1", ""); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(baseDir(root), "FormRecall.exe")); err != nil {
		t.Fatalf("the packaged version must stay as the first rollback source: %v", err)
	}
	if _, err := os.Stat(filepath.Join(versions, "release-1", "manifest.json")); err != nil {
		t.Fatalf("the activated version was removed: %v", err)
	}
	if err := launcher.commit("release-2", "release-1"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(baseDir(root)); !os.IsNotExist(err) {
		t.Fatalf("the packaged version survived the second activation: %v", err)
	}
}

func TestPruneIncompleteSlotsKeepsStagedVersion(t *testing.T) {
	root := t.TempDir()
	versions := versionsDir(root)
	writeTestFile(t, filepath.Join(versions, "release-1"), "payload/download.zip", []byte("partial"))
	writeTestFile(t, filepath.Join(versions, "release-2"), "manifest.json", []byte(`{}`))
	launcher := NewClient(layoutVersion, root)
	launcher.pruneIncompleteSlots()
	if _, err := os.Stat(filepath.Join(versions, "release-1")); !os.IsNotExist(err) {
		t.Fatalf("interrupted download survived: %v", err)
	}
	if _, err := os.Stat(filepath.Join(versions, "release-2", "manifest.json")); err != nil {
		t.Fatalf("staged version was removed: %v", err)
	}
}

func TestExtractReadsOnlyTheBundledVersionTree(t *testing.T) {
	contents := map[string][]byte{
		"FormRecall.exe":                     []byte("payload desktop"),
		"formrecall-engine-host.exe":         []byte("payload engine"),
		"package-manifest.json":              []byte(`{"schema":1}`),
		"occt-runtime/runtime-manifest.json": []byte(`{"schema":1}`),
	}
	manifest := Manifest{Version: layoutVersion}
	for name, data := range contents {
		manifest.Files = append(manifest.Files, File{Path: name, SHA256: shaTest(data), Bytes: int64(len(data))})
	}
	prefix := "FormRecall-" + layoutVersion + "-windows-x64/runtime/base/"
	entries := map[string][]byte{
		"FormRecall-" + layoutVersion + "-windows-x64/FormRecall.exe": []byte("launcher copy"),
	}
	for name, data := range contents {
		entries[prefix+name] = data
	}
	archive := filepath.Join(t.TempDir(), "portable.zip")
	if err := os.WriteFile(archive, zipEntries(t, entries), 0600); err != nil {
		t.Fatal(err)
	}
	dest := filepath.Join(t.TempDir(), "payload")
	if err := os.MkdirAll(dest, 0700); err != nil {
		t.Fatal(err)
	}
	if err := extract(context.Background(), archive, dest, "", manifest, false); err != nil {
		t.Fatalf("packaged container was rejected: %v", err)
	}
	for name := range contents {
		if _, err := os.Stat(filepath.Join(dest, filepath.FromSlash(name))); err != nil {
			t.Fatalf("payload file missing: %s", name)
		}
	}
	if _, err := os.Stat(filepath.Join(dest, "FormRecall.exe.dist")); !os.IsNotExist(err) {
		t.Fatal("launcher copy leaked into the payload")
	}
	legacyEntries := map[string][]byte{}
	for name, data := range contents {
		legacyEntries["FormRecall-"+layoutVersion+"-windows-x64/"+name] = data
	}
	legacy := filepath.Join(t.TempDir(), "legacy.zip")
	if err := os.WriteFile(legacy, zipEntries(t, legacyEntries), 0600); err != nil {
		t.Fatal(err)
	}
	if err := extract(context.Background(), legacy, filepath.Join(t.TempDir(), "legacy"),
		"", manifest, false); err == nil {
		t.Fatal("a flat package without the bundled version tree was accepted")
	}
}
