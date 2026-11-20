package update

import (
	"encoding/json"
	"os"
	"path/filepath"
)

type Preferences struct {
	Channel string `json:"channel"`
	Auto    bool   `json:"auto"`
}

func LoadPreferences(data, defaultChannel string) Preferences {
	p := Preferences{Channel: defaultChannel, Auto: true}
	raw, err := os.ReadFile(filepath.Join(data, "update-preferences.json"))
	if err != nil || len(raw) > 4096 || json.Unmarshal(raw, &p) != nil {
		return Preferences{Channel: defaultChannel, Auto: true}
	}
	switch p.Channel {
	case "alpha", "beta", "rc", "stable":
	default:
		p.Channel = defaultChannel
	}
	return p
}

func (p Preferences) Save(data string) error {
	if err := os.MkdirAll(data, 0700); err != nil {
		return err
	}
	return writeUpdateState(data, "update-preferences.json", p)
}
