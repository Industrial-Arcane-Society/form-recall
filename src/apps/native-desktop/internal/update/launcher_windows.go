package update

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"
)

// The installed program keeps a single visible entry point and one runtime
// directory. Everything else lives under runtime so that a portable or
// installed copy never accumulates unrelated files in its root.
const (
	runtimeName  = "runtime"
	baseName     = "base"
	versionsName = "versions"
	launcherFile = "launcher.json"
)

var slotPattern = regexp.MustCompile(`^release-[0-9]+$`)

type activeVersion struct {
	Current     string `json:"current"`
	Previous    string `json:"previous,omitempty"`
	RootVersion string `json:"root_version,omitempty"`
}

// launcherRecord publishes the protocol abilities of the launcher that owns the
// program root. The launcher ships with a full package and an update never
// replaces it, so the desktop must not assume newer launcher abilities.
type launcherRecord struct {
	Schema        int    `json:"schema"`
	Version       string `json:"version"`
	ChannelSwitch bool   `json:"channel_switch"`
	Downgrade     bool   `json:"downgrade"`
}

func versionsDir(root string) string { return filepath.Join(root, runtimeName, versionsName) }

func baseDir(root string) string { return filepath.Join(root, runtimeName, baseName) }

// RootForBundle resolves the installed program root for the launcher, for a
// staged payload and for the base version shipped with the package. All three
// share the same runtime directory.
func RootForBundle(bundle string) string {
	if filepath.Base(bundle) == "payload" && slotPattern.MatchString(filepath.Base(filepath.Dir(bundle))) {
		versions := filepath.Dir(filepath.Dir(bundle))
		runtimeDirectory := filepath.Dir(versions)
		if filepath.Base(versions) == versionsName && filepath.Base(runtimeDirectory) == runtimeName {
			return filepath.Dir(runtimeDirectory)
		}
		return bundle
	}
	if filepath.Base(bundle) == baseName && filepath.Base(filepath.Dir(bundle)) == runtimeName {
		return filepath.Dir(filepath.Dir(bundle))
	}
	return bundle
}

// IsLauncher reports whether this process runs the entry point that selects and
// starts a version instead of being that version. A plain build output has no
// runtime directory and keeps running the desktop directly.
func (c *Client) IsLauncher() bool {
	if c == nil || c.Root != c.Bundle {
		return false
	}
	info, err := os.Lstat(filepath.Join(c.Root, runtimeName))
	return err == nil && info.IsDir() && info.Mode()&os.ModeSymlink == 0
}

func (c *Client) current() activeVersion {
	var state activeVersion
	raw, err := os.ReadFile(filepath.Join(versionsDir(c.Root), "active.json"))
	if err == nil && len(raw) < 4096 {
		_ = json.Unmarshal(raw, &state)
	}
	return state
}

func (c *Client) readSlot(slot string) (Offer, string, error) {
	if !slotPattern.MatchString(slot) {
		return Offer{}, "", errors.New("更新目录编号无效")
	}
	versions := versionsDir(c.Root)
	base := filepath.Join(versions, slot)
	file, err := openFile(versions, slot+"/manifest.json")
	if err != nil {
		return Offer{}, "", err
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, maxManifest+1))
	if err != nil {
		return Offer{}, "", err
	}
	offer, err := verifyEnvelope(raw, c.key)
	if err != nil {
		return Offer{}, "", err
	}
	return offer, filepath.Join(base, "payload"), nil
}

func (c *Client) launcher() launcherRecord {
	if c.IsLauncher() {
		return launcherRecord{Schema: 1, Version: c.Version, ChannelSwitch: true, Downgrade: true}
	}
	var record launcherRecord
	raw, err := os.ReadFile(filepath.Join(versionsDir(c.Root), launcherFile))
	if err != nil || len(raw) > 4096 || json.Unmarshal(raw, &record) != nil || record.Schema != 1 {
		return launcherRecord{}
	}
	return record
}

func (c *Client) SupportsChannelSwitch() bool { return c.launcher().ChannelSwitch }

func (c *Client) SupportsDowngrade() bool { return c.launcher().Downgrade }

// RecordLauncher publishes this launcher's abilities for the desktop that runs
// from a payload. Failure is reported so verification can detect a launcher that
// cannot describe itself.
func (c *Client) RecordLauncher() error {
	if !c.IsLauncher() {
		return nil
	}
	directory := versionsDir(c.Root)
	if err := makeDir(filepath.Join(c.Root, runtimeName)); err != nil {
		return err
	}
	if err := makeDir(directory); err != nil {
		return err
	}
	return writeUpdateState(directory, launcherFile,
		launcherRecord{Schema: 1, Version: c.Version, ChannelSwitch: true, Downgrade: true})
}

func (c *Client) activeSlot(state activeVersion, slot string) (Offer, string, error) {
	offer, bundle, err := c.readSlot(slot)
	if err == nil && state.RootVersion != c.Version && !newer(offer.Version, c.Version) {
		return Offer{}, "", errors.New("完整安装包已更换启动版本")
	}
	return offer, bundle, err
}

func (c *Client) ActiveExecutable() string {
	if !c.IsLauncher() {
		return ""
	}
	state := c.current()
	for _, slot := range []string{state.Current, state.Previous} {
		if offer, bundle, err := c.activeSlot(state, slot); err == nil {
			var executable File
			for _, file := range offer.Files {
				if strings.EqualFold(file.Path, "FormRecall.exe") {
					executable = file
					break
				}
			}
			binary, err := openFile(bundle, "FormRecall.exe")
			if err == nil {
				err = matchFile(binary, io.Discard, executable)
				_ = binary.Close()
				if err == nil {
					return filepath.Join(bundle, "FormRecall.exe")
				}
			}
		}
	}
	return ""
}

// BaseExecutable returns the version shipped with the full package while no
// update has been activated. It carries no signed update manifest, so the
// packaged inventory is the integrity reference.
func (c *Client) BaseExecutable() string {
	if !c.IsLauncher() {
		return ""
	}
	base := baseDir(c.Root)
	raw, err := os.ReadFile(filepath.Join(base, "package-manifest.json"))
	if err != nil || len(raw) > maxManifest {
		return ""
	}
	var record struct {
		Schema int    `json:"schema"`
		Files  []File `json:"files"`
	}
	if json.Unmarshal(raw, &record) != nil || record.Schema != 1 {
		return ""
	}
	var expected File
	for _, file := range record.Files {
		if strings.EqualFold(file.Path, "FormRecall.exe") {
			expected = file
			break
		}
	}
	if expected.Path == "" {
		return ""
	}
	binary, err := openFile(base, "FormRecall.exe")
	if err != nil {
		return ""
	}
	err = matchFile(binary, io.Discard, expected)
	_ = binary.Close()
	if err != nil {
		return ""
	}
	return filepath.Join(base, "FormRecall.exe")
}

func verifyPayload(bundle string, files []File) error {
	for _, meta := range files {
		file, err := openFile(bundle, meta.Path)
		if err != nil {
			return err
		}
		err = matchFile(file, io.Discard, meta)
		_ = file.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func writeUpdateState(store, name string, state any) error {
	raw, err := json.Marshal(state)
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(store, "active-")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	_, err = f.Write(raw)
	if err == nil {
		err = f.Sync()
	}
	if err = errors.Join(err, f.Close()); err != nil {
		return err
	}
	return os.Rename(f.Name(), filepath.Join(store, name))
}

func hiddenCommand(executable string, args ...string) *exec.Cmd {
	cmd := exec.Command(executable, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x08000000} // CREATE_NO_WINDOW
	return cmd
}

// pruneIncompleteSlots removes interrupted downloads. A staged slot keeps its
// signed manifest, so this never discards a version that is waiting for the
// restart that activates it.
func (c *Client) pruneIncompleteSlots() {
	directory := versionsDir(c.Root)
	entries, err := os.ReadDir(directory)
	if err != nil {
		return
	}
	for _, entry := range entries {
		name := entry.Name()
		if !slotPattern.MatchString(name) || !entry.IsDir() {
			continue
		}
		path := filepath.Join(directory, name)
		if _, err := os.Stat(filepath.Join(path, "manifest.json")); err == nil {
			continue
		}
		_ = os.RemoveAll(path)
	}
}

// pruneSlots keeps the active version and the single rollback version. Older
// copies are deleted so an installation never grows with every update.
func (c *Client) pruneSlots(keep ...string) error {
	directory := versionsDir(c.Root)
	entries, err := os.ReadDir(directory)
	if err != nil {
		return err
	}
	kept := make(map[string]bool, len(keep))
	for _, name := range keep {
		if slotPattern.MatchString(name) {
			kept[strings.ToLower(name)] = true
		}
	}
	for _, entry := range entries {
		name := entry.Name()
		if !slotPattern.MatchString(name) || kept[strings.ToLower(name)] || !entry.IsDir() {
			continue
		}
		_ = os.RemoveAll(filepath.Join(directory, name))
	}
	return nil
}

// Queue starts the fixed launcher after the caller closes its engine and window.
func (c *Client) Queue(slot string, args []string) error {
	if !slotPattern.MatchString(slot) {
		return errors.New("更新目录编号无效")
	}
	cmd := hiddenCommand(filepath.Join(c.Root, "FormRecall.exe"), append(args, "--apply-update="+slot)...)
	if offer, _, err := c.readSlot(slot); err == nil && IsChannelDowngrade(c.Version, offer.Version) {
		cmd.Args = append(cmd.Args, "--channel-downgrade")
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	return cmd.Process.Release()
}

// Apply runs in the fixed launcher. The existing pointer remains valid until
// the new desktop reports engine readiness, including during power loss.
func (c *Client) Apply(slot string, args []string, allowDowngrade bool) (result error) {
	if !c.IsLauncher() {
		return errors.New("必须由安装目录的启动器应用更新")
	}
	store := versionsDir(c.Root)
	if err := makeDir(store); err != nil {
		return err
	}
	lockName, err := syscall.UTF16PtrFromString(filepath.Join(store, "activation.lock"))
	if err != nil {
		return err
	}
	lock, err := syscall.CreateFile(lockName, syscall.GENERIC_WRITE, 0, nil, syscall.OPEN_ALWAYS, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		return fmt.Errorf("另一个更新正在执行：%w", err)
	}
	defer syscall.CloseHandle(lock)
	defer func() {
		if result != nil {
			_ = os.WriteFile(filepath.Join(store, "last-error.txt"), []byte(result.Error()), 0600)
		}
	}()
	offer, bundle, err := c.readSlot(slot)
	if err != nil {
		return err
	}
	state := c.current()
	currentVersion := c.Version
	if current, _, err := c.activeSlot(state, state.Current); err == nil {
		currentVersion = current.Version
	}
	if !newer(offer.Version, currentVersion) &&
		!(allowDowngrade && c.SupportsDowngrade() && IsChannelDowngrade(currentVersion, offer.Version)) {
		return errors.New("目标更新已应用或已过时")
	}
	if err := verifyPayload(bundle, offer.Files); err != nil {
		return err
	}
	health, err := os.MkdirTemp(store, "startup-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(health)
	marker := filepath.Join(health, "ready")
	finalArgs := append(append([]string{}, args...), "--update-health="+marker)
	cmd := hiddenCommand(filepath.Join(bundle, "FormRecall.exe"), finalArgs...)
	if err := cmd.Start(); err != nil {
		return err
	}
	exited := make(chan error, 1)
	go func() { exited <- cmd.Wait() }()
	deadline := time.NewTimer(90 * time.Second)
	defer deadline.Stop()
	tick := time.NewTicker(50 * time.Millisecond)
	defer tick.Stop()
	for {
		if ready, _ := os.ReadFile(marker); string(ready) == offer.Version {
			if err := c.commit(slot, state.Current); err != nil {
				_ = cmd.Process.Kill()
				<-exited
				return err
			}
			return nil
		}
		select {
		case err := <-exited:
			if err != nil {
				return fmt.Errorf("新版在启动验证前退出：%w", err)
			}
			return errors.New("新版在启动验证前退出")
		case <-deadline.C:
			_ = cmd.Process.Kill()
			<-exited
			return errors.New("新版建模引擎启动超时")
		case <-tick.C:
		}
	}
}

// commit records the activated version and removes everything the program no
// longer needs. The packaged base doubles as the rollback source for the first
// update and is dropped as soon as a second version is activated, so an
// installation never holds more than two full copies.
func (c *Client) commit(slot, previous string) error {
	store := versionsDir(c.Root)
	if err := writeUpdateState(store, "active.json", activeVersion{Current: slot, Previous: previous, RootVersion: c.Version}); err != nil {
		return err
	}
	_ = os.Remove(filepath.Join(store, "last-error.txt"))
	if previous != "" {
		_ = os.RemoveAll(baseDir(c.Root))
	}
	_ = c.pruneSlots(slot, previous)
	return nil
}

func ReportReady(root, marker, version string) {
	dir := filepath.Dir(marker)
	if filepath.Base(marker) == "ready" &&
		strings.EqualFold(filepath.Dir(dir), versionsDir(root)) &&
		strings.HasPrefix(filepath.Base(dir), "startup-") {
		_ = os.WriteFile(marker, []byte(version), 0600)
	}
}

func (c *Client) LastError() string {
	raw, err := os.ReadFile(filepath.Join(versionsDir(c.Root), "last-error.txt"))
	if err != nil || len(raw) > 4096 {
		return ""
	}
	return string(raw)
}
