package update

import (
	"archive/zip"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type Client struct {
	Version   string
	Root      string
	Bundle    string
	http      *http.Client
	key       ed25519.PublicKey
	feed      string
	freshFeed string
	channel   string
}

func NewClient(version, bundle string) *Client {
	channel := channelFor(version)
	if channel == "" {
		return nil
	}
	key, _ := base64.RawURLEncoding.DecodeString(publicKey)
	return &Client{
		Version: version, Root: RootForBundle(bundle), Bundle: bundle, key: key,
		channel: channel, feed: distribution + channel + ".json",
		freshFeed: distributionAPI + channel + ".json?ref=main",
		http: &http.Client{CheckRedirect: func(req *http.Request, via []*http.Request) error {
			host := req.URL.Host
			if len(via) >= 6 || req.URL.Scheme != "https" || (host != "github.com" &&
				host != "release-assets.githubusercontent.com" && host != "objects.githubusercontent.com" &&
				host != "raw.githubusercontent.com") {
				return errors.New("更新下载重定向无效")
			}
			return nil
		}},
	}
}

func (c *Client) Channel() string { return c.channel }

func (c *Client) ForChannel(channel string) *Client {
	selected := *c
	selected.channel = channel
	selected.feed = distribution + channel + ".json"
	selected.freshFeed = distributionAPI + channel + ".json?ref=main"
	return &selected
}

func (c *Client) get(ctx context.Context, address string) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "FormRecall/"+c.Version)
	req.Header.Set("Accept-Encoding", "identity")
	return c.http.Do(req)
}

var ErrNoChannelRelease = errors.New("此更新渠道尚无可用版本")

func (c *Client) Check(ctx context.Context) (*Offer, error) {
	var lastErr error
	var valid bool
	for _, address := range []string{c.freshFeed, c.feed} {
		if address == "" {
			continue
		}
		offer, err := c.readFeed(ctx, address)
		if err != nil {
			if lastErr == nil || errors.Is(lastErr, ErrNoChannelRelease) {
				lastErr = err
			}
			continue
		}
		valid = true
		if newer(offer.Version, c.Version) || IsChannelDowngrade(c.Version, offer.Version) {
			return &offer, nil
		}
	}
	if valid {
		return nil, nil
	}
	return nil, lastErr
}

func (c *Client) readFeed(ctx context.Context, address string) (Offer, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
	if err != nil {
		return Offer{}, err
	}
	req.Header.Set("User-Agent", "FormRecall/"+c.Version)
	req.Header.Set("Cache-Control", "no-cache")
	if strings.HasPrefix(address, distributionAPI) {
		req.Header.Set("Accept", "application/vnd.github.raw+json")
	}
	res, err := c.http.Do(req)
	if err != nil {
		return Offer{}, err
	}
	defer res.Body.Close()
	if res.StatusCode == http.StatusNotFound {
		return Offer{}, ErrNoChannelRelease
	}
	if res.StatusCode != http.StatusOK {
		return Offer{}, fmt.Errorf("更新服务返回 HTTP %d", res.StatusCode)
	}
	raw, err := io.ReadAll(io.LimitReader(res.Body, maxManifest+1))
	if err != nil {
		return Offer{}, err
	}
	offer, err := verifyEnvelope(raw, c.key)
	if err != nil {
		return Offer{}, err
	}
	if offer.Channel != c.Channel() {
		return Offer{}, errors.New("更新清单与所选渠道不匹配")
	}
	return offer, nil
}

func (c *Client) choose(offer Offer) (Asset, bool) {
	for _, delta := range offer.Deltas {
		if delta.From == c.Version && delta.Bytes*10 < offer.Full.Bytes*8 {
			return delta.Asset, true
		}
	}
	return offer.Full, false
}

func (c *Client) DownloadSize(offer Offer) int64 {
	asset, _ := c.choose(offer)
	return asset.Bytes
}

type Progress struct {
	Received, Total int64
	Delta           bool
}

type progressWriter struct {
	Progress
	last time.Time
	send func(Progress)
}

func (p *progressWriter) Write(data []byte) (int, error) {
	p.Received += int64(len(data))
	if p.send != nil && (time.Since(p.last) >= 150*time.Millisecond || p.Received == p.Total) {
		p.send(p.Progress)
		p.last = time.Now()
	}
	return len(data), nil
}

func (c *Client) download(ctx context.Context, asset Asset, delta bool, dest string, progress func(Progress)) error {
	res, err := c.get(ctx, asset.URL)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK || (res.ContentLength >= 0 && res.ContentLength != asset.Bytes) {
		return fmt.Errorf("更新文件下载失败或大小不符（HTTP %d）", res.StatusCode)
	}
	file, err := os.OpenFile(dest, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0600)
	if err != nil {
		return err
	}
	report := &progressWriter{Progress: Progress{Total: asset.Bytes, Delta: delta}, send: progress}
	if progress != nil {
		progress(report.Progress)
	}
	hash := sha256.New()
	n, err := io.Copy(io.MultiWriter(file, hash, report), io.LimitReader(res.Body, asset.Bytes+1))
	closeErr := file.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if n != asset.Bytes || hex.EncodeToString(hash.Sum(nil)) != asset.SHA256 {
		return errors.New("更新文件 SHA-256 校验失败")
	}
	return nil
}

func makeDir(directory string) error {
	if err := os.Mkdir(directory, 0700); err != nil && !errors.Is(err, os.ErrExist) {
		return err
	}
	info, err := os.Lstat(directory)
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("更新目录不可用：%s", directory)
	}
	return nil
}

// Stage prepares a complete new version beside the running version. Only
// activation writes the pointer used by the installed launcher.
func (c *Client) Stage(ctx context.Context, offer Offer, progress func(Progress)) (string, error) {
	verified, err := verifyEnvelope(offer.signed, c.key)
	if err != nil {
		return "", err
	}
	if verified.Channel != c.Channel() || (!newer(verified.Version, c.Version) &&
		!(c.SupportsDowngrade() && IsChannelDowngrade(c.Version, verified.Version))) {
		return "", errors.New("目标版本不适用于当前安装")
	}
	if verified.Channel != channelFor(c.Version) && !c.SupportsChannelSwitch() {
		return "", errors.New("请先用完整安装包或便携包换用支持跨渠道更新的启动器")
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Minute)
	defer cancel()
	versions := versionsDir(c.Root)
	if err := makeDir(filepath.Join(c.Root, runtimeName)); err != nil {
		return "", err
	}
	if err := makeDir(versions); err != nil {
		return "", err
	}
	c.pruneIncompleteSlots()
	asset, delta := c.choose(verified)
	slot, err := c.prepare(ctx, versions, verified, asset, delta, progress)
	if err != nil && delta && ctx.Err() == nil {
		return c.prepare(ctx, versions, verified, verified.Full, false, progress)
	}
	return slot, err
}

func (c *Client) prepare(ctx context.Context, versions string, offer Offer, asset Asset, delta bool, progress func(Progress)) (string, error) {
	stage, err := os.MkdirTemp(versions, "release-")
	if err != nil {
		return "", err
	}
	complete := false
	defer func() {
		if !complete {
			_ = os.RemoveAll(stage)
		}
	}()
	archive := filepath.Join(stage, "download.zip")
	if err := c.download(ctx, asset, delta, archive, progress); err != nil {
		return "", err
	}
	payload := filepath.Join(stage, "payload")
	if err := extract(ctx, archive, payload, c.Bundle, offer.Manifest, delta); err != nil {
		return "", err
	}
	if err := os.Remove(archive); err != nil {
		return "", err
	}
	if err := os.WriteFile(filepath.Join(stage, "manifest.json"), offer.signed, 0600); err != nil {
		return "", err
	}
	complete = true
	return filepath.Base(stage), nil
}

func extract(ctx context.Context, archive, dest, installed string, m Manifest, delta bool) error {
	reader, err := zip.OpenReader(archive)
	if err != nil {
		return err
	}
	defer reader.Close()
	entries := make(map[string]*zip.File, len(reader.File))
	// The portable package keeps the bundled version under runtime/base; only
	// that tree becomes the staged payload. Delta archives already contain
	// payload-relative entries.
	prefix := "FormRecall-" + m.Version + "-windows-x64/" + runtimeName + "/" + baseName + "/"
	if delta {
		prefix = ""
	}
	expected := make(map[string]File, len(m.Files))
	for _, f := range m.Files {
		expected[f.Path] = f
	}
	if len(reader.File) > 2*len(expected)+16 {
		return errors.New("更新包条目过多")
	}
	for _, file := range reader.File {
		if !strings.HasPrefix(file.Name, prefix) {
			if delta {
				return errors.New("更新包目录不匹配")
			}
			// A full package also ships the entry point and the packaged
			// inventory; the staged payload only needs the bundled version.
			continue
		}
		name := strings.TrimPrefix(file.Name, prefix)
		if file.FileInfo().IsDir() && (name == "" || safeName(strings.TrimSuffix(name, "/"))) {
			continue
		}
		meta, ok := expected[name]
		if !ok || !file.Mode().IsRegular() || entries[name] != nil || file.UncompressedSize64 != uint64(meta.Bytes) {
			return fmt.Errorf("更新包含无效文件：%s", name)
		}
		entries[name] = file
	}
	for _, f := range m.Files {
		if err := ctx.Err(); err != nil {
			return err
		}
		var src io.ReadCloser
		switch {
		case entries[f.Path] != nil:
			src, err = entries[f.Path].Open()
		case delta:
			src, err = openFile(installed, f.Path)
		default:
			err = fmt.Errorf("完整更新包缺少 %s", f.Path)
		}
		if err != nil {
			return err
		}
		err = writeFile(src, dest, f)
		_ = src.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func openFile(root, name string) (*os.File, error) {
	parts := strings.Split(name, "/")
	for i, part := range parts {
		root = filepath.Join(root, part)
		info, err := os.Lstat(root)
		if err != nil {
			return nil, err
		}
		if info.Mode()&os.ModeSymlink != 0 || (i == len(parts)-1 && !info.Mode().IsRegular()) ||
			(i < len(parts)-1 && !info.IsDir()) {
			return nil, fmt.Errorf("更新文件类型无效：%s", name)
		}
	}
	return os.Open(root)
}

func writeFile(src io.Reader, dest string, f File) error {
	name := filepath.Join(dest, filepath.FromSlash(f.Path))
	if err := os.MkdirAll(filepath.Dir(name), 0700); err != nil {
		return err
	}
	output, err := os.OpenFile(name, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	err = matchFile(src, output, f)
	closeErr := output.Close()
	return errors.Join(err, closeErr)
}

func matchFile(src io.Reader, target io.Writer, f File) error {
	hash := sha256.New()
	n, err := io.Copy(io.MultiWriter(hash, target), io.LimitReader(src, f.Bytes+1))
	if err != nil {
		return err
	}
	if n != f.Bytes || hex.EncodeToString(hash.Sum(nil)) != f.SHA256 {
		return fmt.Errorf("文件校验失败：%s", f.Path)
	}
	return nil
}
