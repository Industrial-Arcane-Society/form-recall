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

const storageName = ".formrecall-updates"

var slotPattern = regexp.MustCompile(`^release-[0-9]+$`)

type activeVersion struct {
	Current  string `json:"current"`
	Previous string `json:"previous,omitempty"`
}

func RootForBundle(bundle string) string {
	slot := filepath.Dir(bundle)
	versions := filepath.Dir(slot)
	store := filepath.Dir(versions)
	if filepath.Base(bundle) == "payload" && slotPattern.MatchString(filepath.Base(slot)) &&
		filepath.Base(versions) == "versions" && filepath.Base(store) == storageName {
		return filepath.Dir(store)
	}
	return bundle
}

func (c *Client) current() activeVersion {
	var state activeVersion
	raw, err := os.ReadFile(filepath.Join(c.Root, storageName, "active.json"))
	if err == nil && len(raw) < 4096 {
		_ = json.Unmarshal(raw, &state)
	}
	return state
}

func (c *Client) readSlot(slot string) (Offer, string, error) {
	if !slotPattern.MatchString(slot) {
		return Offer{}, "", errors.New("更新目录编号无效")
	}
	base := filepath.Join(c.Root, storageName, "versions", slot)
	file, err := openFile(c.Root, storageName+"/versions/"+slot+"/manifest.json")
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
	if offer.Channel != c.Channel() || !newer(offer.Version, c.Version) {
		return Offer{}, "", errors.New("暂存版本与引导程序不兼容")
	}
	return offer, filepath.Join(base, "payload"), nil
}

func (c *Client) ActiveExecutable() string {
	if c == nil || c.Root != c.Bundle {
		return ""
	}
	state := c.current()
	for _, slot := range []string{state.Current, state.Previous} {
		if _, bundle, err := c.readSlot(slot); err == nil {
			binary, err := openFile(bundle, "FormRecall.exe")
			if err == nil {
				_ = binary.Close()
				return filepath.Join(bundle, "FormRecall.exe")
			}
		}
	}
	return ""
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

func writeActive(store string, state activeVersion) error {
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
	return os.Rename(f.Name(), filepath.Join(store, "active.json"))
}

func hiddenCommand(executable string, args ...string) *exec.Cmd {
	cmd := exec.Command(executable, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x08000000} // CREATE_NO_WINDOW
	return cmd
}

// Queue starts the fixed launcher after the caller closes its engine and window.
func (c *Client) Queue(slot string, args []string) error {
	if !slotPattern.MatchString(slot) {
		return errors.New("更新目录编号无效")
	}
	cmd := hiddenCommand(filepath.Join(c.Root, "FormRecall.exe"), append(args, "--apply-update="+slot)...)
	if err := cmd.Start(); err != nil {
		return err
	}
	return cmd.Process.Release()
}

// Apply runs in the fixed launcher. The existing pointer remains valid until
// the new desktop reports engine readiness, including during power loss.
func (c *Client) Apply(slot string, args []string) (result error) {
	if c.Root != c.Bundle {
		return errors.New("必须由安装目录的启动器应用更新")
	}
	store := filepath.Join(c.Root, storageName)
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
	if current, _, err := c.readSlot(state.Current); err == nil && !newer(offer.Version, current.Version) {
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
			if err := writeActive(store, activeVersion{Current: slot, Previous: state.Current}); err != nil {
				_ = cmd.Process.Kill()
				<-exited
				return err
			}
			_ = os.Remove(filepath.Join(store, "last-error.txt"))
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

func ReportReady(root, marker, version string) {
	dir := filepath.Dir(marker)
	if filepath.Base(marker) == "ready" &&
		strings.EqualFold(filepath.Dir(dir), filepath.Join(root, storageName)) &&
		strings.HasPrefix(filepath.Base(dir), "startup-") {
		_ = os.WriteFile(marker, []byte(version), 0600)
	}
}

func (c *Client) LastError() string {
	raw, err := os.ReadFile(filepath.Join(c.Root, storageName, "last-error.txt"))
	if err != nil || len(raw) > 4096 {
		return ""
	}
	return string(raw)
}
