package update

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"path"
	"regexp"
	"strings"
)

const distribution = "https://raw.githubusercontent.com/Industrial-Arcane-Society/form-recall-release/main/channels/"
const distributionAPI = "https://api.github.com/repos/Industrial-Arcane-Society/form-recall-release/contents/channels/"
const downloads = "https://github.com/Industrial-Arcane-Society/"
const publicKey = "pADH694WxPtjJAK_Auo7qo88o3kv2oAdHzi9T4yFznQ"
const maxArchive = 1 << 30
const maxManifest = 1 << 20

var (
	versionPattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})(?:-(alpha|beta|rc)\.([1-9][0-9]{0,8}))?$`)
	hashPattern    = regexp.MustCompile(`^[a-f0-9]{64}$`)
	commitPattern  = regexp.MustCompile(`^[a-f0-9]{40}$`)
)

type File struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
	Bytes  int64  `json:"bytes"`
}

type Asset struct {
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
	Bytes  int64  `json:"bytes"`
}

type Delta struct {
	From string `json:"from"`
	Asset
}

type Manifest struct {
	Schema   int     `json:"schema"`
	Platform string  `json:"platform"`
	Channel  string  `json:"channel"`
	Version  string  `json:"version"`
	Commit   string  `json:"commit"`
	Files    []File  `json:"files"`
	Full     Asset   `json:"full"`
	Deltas   []Delta `json:"deltas"`
}

type Offer struct {
	Manifest
	signed []byte
}

func channelFor(version string) string {
	match := versionPattern.FindStringSubmatch(version)
	if match == nil {
		return ""
	}
	if match[4] != "" {
		return match[4]
	}
	return "stable"
}

func versionParts(v string) [5]int {
	m := versionPattern.FindStringSubmatch(v)
	if m == nil {
		return [5]int{}
	}
	var result [5]int
	for i, value := range []string{m[1], m[2], m[3], "", m[5]} {
		for _, digit := range value {
			result[i] = result[i]*10 + int(digit-'0')
		}
	}
	result[3] = map[string]int{"alpha": 0, "beta": 1, "rc": 2, "": 3}[m[4]]
	return result
}

func newer(candidate, current string) bool {
	a, b := versionParts(candidate), versionParts(current)
	for i := range a {
		if a[i] != b[i] {
			return a[i] > b[i]
		}
	}
	return false
}

// Lower versions are eligible only across channels in the same release
// series. Applying one additionally requires an explicit restart decision.
func IsChannelDowngrade(current, target string) bool {
	from, to := channelFor(current), channelFor(target)
	a, b := versionParts(current), versionParts(target)
	return from != "" && to != "" && from != to && newer(current, target) &&
		a[0] == b[0] && a[1] == b[1] && a[2] == b[2]
}

func safeName(name string) bool {
	if len(name) == 0 || len(name) > 240 || path.Clean(name) != name || strings.ContainsAny(name, "\\:*?\"<>|") {
		return false
	}
	for _, part := range strings.Split(name, "/") {
		if part == "" || part == "." || part == ".." || strings.TrimRight(part, " .") != part {
			return false
		}
		for _, ch := range part {
			if ch < 32 {
				return false
			}
		}
		stem, _, _ := strings.Cut(strings.ToUpper(part), ".")
		switch stem {
		case "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$":
			return false
		}
		if (strings.HasPrefix(stem, "COM") || strings.HasPrefix(stem, "LPT")) &&
			strings.Contains("123456789¹²³", strings.TrimPrefix(strings.TrimPrefix(stem, "COM"), "LPT")) {
			if len([]rune(stem)) == 4 {
				return false
			}
		}
	}
	return true
}

func assetURL(repo, version, name string) string {
	return downloads + repo + "/releases/download/v" + version + "/" + name
}

func validAsset(asset Asset, expected string) bool {
	u, err := url.Parse(asset.URL)
	return err == nil && u.String() == expected && hashPattern.MatchString(asset.SHA256) && asset.Bytes > 0 && asset.Bytes <= maxArchive
}

func (m Manifest) validate() error {
	limit := 5
	if m.Schema == 2 {
		limit = 8
	}
	if (m.Schema != 1 && m.Schema != 2) || m.Platform != "windows-x64" || !commitPattern.MatchString(m.Commit) ||
		channelFor(m.Version) == "" || m.Channel != channelFor(m.Version) || len(m.Files) > 4096 || len(m.Deltas) > limit {
		return errors.New("更新清单的版本、频道或平台无效")
	}
	full := "FormRecall-" + m.Version + "-windows-x64-portable.zip"
	if !validAsset(m.Full, assetURL("form-recall", m.Version, full)) {
		return errors.New("完整包信息无效")
	}
	from := map[string]bool{}
	for _, delta := range m.Deltas {
		file := "FormRecall-" + delta.From + "-to-" + m.Version + "-windows-x64-ota.zip"
		source := channelFor(delta.From)
		compatible := source == m.Channel
		if m.Schema == 2 {
			file = "ota-" + delta.SHA256 + ".zip"
			compatible = source != "" && ((m.Channel != "stable" && source != "stable") ||
				(m.Channel == "stable" && (source == "stable" || (source == "rc" && strings.HasPrefix(delta.From, m.Version+"-rc.")))))
		}
		if from[delta.From] || !compatible || !newer(m.Version, delta.From) ||
			!validAsset(delta.Asset, assetURL("form-recall-release", m.Version, file)) {
			return errors.New("增量包信息无效")
		}
		from[delta.From] = true
	}
	files := make(map[string]bool, len(m.Files))
	var total int64
	for _, f := range m.Files {
		key := strings.ToLower(f.Path)
		if !safeName(f.Path) || files[key] || !hashPattern.MatchString(f.SHA256) || f.Bytes < 0 || f.Bytes > maxArchive ||
			strings.HasPrefix(key, ".formrecall-updates") {
			return fmt.Errorf("更新文件清单无效：%s", f.Path)
		}
		files[key] = true
		total += f.Bytes
		if total > 2*maxArchive {
			return errors.New("更新清单的解压大小超出限制")
		}
	}
	for key := range files {
		for dir := path.Dir(key); dir != "."; dir = path.Dir(dir) {
			if files[dir] {
				return errors.New("更新清单的文件和目录路径冲突")
			}
		}
	}
	for _, key := range []string{"formrecall.exe", "formrecall-engine-host.exe", "package-manifest.json", "occt-runtime/runtime-manifest.json"} {
		if !files[key] {
			return fmt.Errorf("更新缺少必要文件：%s", key)
		}
	}
	return nil
}

func verifyEnvelope(raw []byte, key ed25519.PublicKey) (Offer, error) {
	var envelope struct {
		Payload   string `json:"payload"`
		Signature string `json:"signature"`
	}
	if len(raw) > maxManifest || json.Unmarshal(raw, &envelope) != nil {
		return Offer{}, errors.New("更新清单格式无效")
	}
	payload, err := base64.RawURLEncoding.DecodeString(envelope.Payload)
	signature, signatureErr := base64.RawURLEncoding.DecodeString(envelope.Signature)
	if err != nil || signatureErr != nil || len(key) != ed25519.PublicKeySize || !ed25519.Verify(key, payload, signature) {
		return Offer{}, errors.New("更新清单签名无效")
	}
	var m Manifest
	if err := json.Unmarshal(payload, &m); err != nil {
		return Offer{}, err
	}
	if err := m.validate(); err != nil {
		return Offer{}, err
	}
	return Offer{Manifest: m, signed: raw}, nil
}
