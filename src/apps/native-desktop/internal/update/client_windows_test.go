package update

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func testAsset(address string, data []byte) Asset {
	hash := sha256.Sum256(data)
	return Asset{URL: address, Bytes: int64(len(data)), SHA256: hex.EncodeToString(hash[:])}
}

func testZIP(t *testing.T, files map[string][]byte, prefix string) []byte {
	t.Helper()
	var b bytes.Buffer
	z := zip.NewWriter(&b)
	for name, data := range files {
		entry, err := z.Create(prefix + name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write(data); err != nil {
			t.Fatal(err)
		}
	}
	if err := z.Close(); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}

func testSign(t *testing.T, m Manifest, key ed25519.PrivateKey) []byte {
	t.Helper()
	payload, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(map[string]string{
		"payload":   base64.RawURLEncoding.EncodeToString(payload),
		"signature": base64.RawURLEncoding.EncodeToString(ed25519.Sign(key, payload)),
	})
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

type mockTransport func(*http.Request) (*http.Response, error)

func (f mockTransport) RoundTrip(req *http.Request) (*http.Response, error) { return f(req) }

func TestCheckAndStageUsesDeltaOrVerifiesFullFallback(t *testing.T) {
	for _, modifyLocal := range []bool{false, true} {
		t.Run(map[bool]string{false: "delta", true: "damaged baseline"}[modifyLocal], func(t *testing.T) {
			const from, to = "0.3.0-alpha.1", "0.3.0-alpha.2"
			root := t.TempDir()
			content := map[string][]byte{
				"FormRecall.exe":                     []byte("new binary"),
				"formrecall-engine-host.exe":         []byte("same engine"),
				"occt-runtime/runtime-manifest.json": []byte("same OCCT"),
				"package-manifest.json":              []byte("new inventory"),
			}
			delta := testZIP(t, map[string][]byte{
				"FormRecall.exe": content["FormRecall.exe"], "package-manifest.json": content["package-manifest.json"],
			}, "")
			full := testZIP(t, content, "FormRecall-"+to+"-windows-x64/")
			m := Manifest{Schema: 2, Platform: "windows-x64", Channel: "alpha", Version: to, Commit: strings.Repeat("a", 40),
				Full:   testAsset(assetURL("form-recall", to, "FormRecall-"+to+"-windows-x64-portable.zip"), full),
				Deltas: []Delta{{From: from, Asset: testAsset(assetURL("form-recall-release", to, "FormRecall-OTA-v"+from+"_v"+to+"-win-x64.zip"), delta)}},
			}
			for name, data := range content {
				meta := testAsset("", data)
				m.Files = append(m.Files, File{Path: name, SHA256: meta.SHA256, Bytes: meta.Bytes})
				existing := data
				if modifyLocal && name == "formrecall-engine-host.exe" {
					existing = []byte("damaged")
				}
				local := filepath.Join(root, filepath.FromSlash(name))
				if err := os.MkdirAll(filepath.Dir(local), 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(local, existing, 0600); err != nil {
					t.Fatal(err)
				}
			}
			if err := os.WriteFile(filepath.Join(root, "obsolete.dll"), []byte("old"), 0600); err != nil {
				t.Fatal(err)
			}
			pub, private, err := ed25519.GenerateKey(rand.Reader)
			if err != nil {
				t.Fatal(err)
			}
			raw := testSign(t, m, private)
			c := NewClient(from, root)
			c.key = pub
			fullDownloads, deltaDownloads := 0, 0
			c.http.Transport = mockTransport(func(req *http.Request) (*http.Response, error) {
				var data []byte
				switch req.URL.String() {
				case c.freshFeed:
					if req.Header.Get("Cache-Control") != "no-cache" || req.Header.Get("Accept") != "application/vnd.github.raw+json" {
						t.Fatal("primary channel lookup did not request fresh raw API content")
					}
					data = raw
				case c.feed:
					t.Fatal("consulted the cached Raw feed despite a newer signed API response")
				case m.Deltas[0].URL:
					data = delta
					deltaDownloads++
				case m.Full.URL:
					data = full
					fullDownloads++
				default:
					t.Fatalf("unexpected URL: %s", req.URL)
				}
				return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(bytes.NewReader(data)), ContentLength: int64(len(data))}, nil
			})
			offer, err := c.Check(context.Background())
			if err != nil || offer == nil {
				t.Fatalf("check returned %v, %v", offer, err)
			}
			slot, err := c.Stage(context.Background(), *offer, nil)
			if err != nil {
				t.Fatal(err)
			}
			if deltaDownloads != 1 || (fullDownloads == 1) != modifyLocal {
				t.Fatalf("download counts: delta=%d full=%d", deltaDownloads, fullDownloads)
			}
			_, bundle, err := c.readSlot(slot)
			if err != nil {
				t.Fatal(err)
			}
			if err := verifyPayload(bundle, m.Files); err != nil {
				t.Fatal(err)
			}
			if _, err := os.Stat(filepath.Join(bundle, "obsolete.dll")); !os.IsNotExist(err) {
				t.Fatalf("obsolete file was carried forward: %v", err)
			}
			if got := c.ActiveExecutable(); got != "" {
				t.Fatalf("staging activated a version: %s", got)
			}
		})
	}
}

func TestManifestRejectsUnsafeWindowsPathsAndInvalidSignature(t *testing.T) {
	for _, name := range []string{"a/../b", "C:/file", "a:stream", "../x", "a\\b", "COM1.exe", "NUL.txt", "a/empty. ", "/root", "a//b"} {
		if safeName(name) {
			t.Fatalf("accepted unsafe name %q", name)
		}
	}
	pub, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	raw := testSign(t, Manifest{}, private)
	var wire map[string]string
	if err := json.Unmarshal(raw, &wire); err != nil {
		t.Fatal(err)
	}
	wire["payload"] = base64.RawURLEncoding.EncodeToString([]byte("{}"))
	modified, err := json.Marshal(wire)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := verifyEnvelope(modified, pub); err == nil || !strings.Contains(err.Error(), "签名") {
		t.Fatalf("tamper error = %v", err)
	}
}

func TestChannelOrderAndPersistedPreferences(t *testing.T) {
	versions := []string{"0.3.0-alpha.4", "0.3.0-beta.1", "0.3.0-rc.1", "0.3.0", "0.3.1-alpha.1"}
	for i := 1; i < len(versions); i++ {
		if !newer(versions[i], versions[i-1]) || newer(versions[i-1], versions[i]) {
			t.Fatalf("version order %v", versions)
		}
	}
	dir := t.TempDir()
	p := Preferences{Channel: "rc", Auto: false}
	if err := p.Save(dir); err != nil {
		t.Fatal(err)
	}
	if got := LoadPreferences(dir, "alpha"); got != p {
		t.Fatalf("reloaded preferences: %+v", got)
	}
	if err := (Preferences{Channel: "beta", Auto: true}).Save(dir); err != nil {
		t.Fatal(err)
	}
	if got := LoadPreferences(dir, "alpha"); got.Channel != "beta" || !got.Auto {
		t.Fatalf("updated preferences: %+v", got)
	}
}

func TestStagePublishedChannel(t *testing.T) {
	bundle := os.Getenv("FORMRECALL_OTA_TEST_BUNDLE")
	if bundle == "" {
		t.Skip("requires a fresh packaged bundle and the live update server")
	}
	client := NewClient(os.Getenv("FORMRECALL_OTA_TEST_VERSION"), bundle)
	if client == nil || !client.SupportsChannelSwitch() {
		t.Fatal("cross-channel test requires a compatible packaged launcher")
	}
	client = client.ForChannel(os.Getenv("FORMRECALL_OTA_TEST_CHANNEL"))
	offer, err := client.Check(context.Background())
	if err != nil || offer == nil {
		t.Fatalf("published offer lookup returned %v, %v", offer, err)
	}
	var progress Progress
	slot, err := client.Stage(context.Background(), *offer, func(p Progress) { progress = p })
	if err != nil {
		t.Fatal(err)
	}
	wantDelta := os.Getenv("FORMRECALL_OTA_TEST_DELTA") == "true"
	asset, _ := client.choose(*offer)
	if progress.Delta != wantDelta || progress.Received != asset.Bytes {
		t.Fatalf("unexpected cross-channel download: %+v", progress)
	}
	record, err := json.Marshal(map[string]any{
		"slot": slot, "version": offer.Version, "bytes": progress.Received, "delta": progress.Delta,
		"archive": filepath.Base(asset.URL),
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(os.Getenv("FORMRECALL_OTA_TEST_RESULT"), record, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestOnlySameSeriesCrossChannelDowngradesAreEligible(t *testing.T) {
	if !IsChannelDowngrade("0.3.0-beta.2", "0.3.0-alpha.4") {
		t.Fatal("expected same-series downgrade")
	}
	for _, target := range []string{"0.3.0-beta.1", "0.2.9-alpha.4", "0.3.0-rc.1", "invalid"} {
		if IsChannelDowngrade("0.3.0-beta.2", target) {
			t.Errorf("unexpected downgrade to %s", target)
		}
	}
}
