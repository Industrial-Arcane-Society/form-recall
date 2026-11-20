package workbench

import (
	"context"
	"errors"
	"fmt"

	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/update"
	"github.com/egoist/mygo/ui"
)

const (
	updateCheckIcon    = "refresh-cw"
	updateDownloadIcon = "download"
	updateRestartIcon  = "rotate-cw"
)

type softwareUpdate struct {
	ctx         context.Context
	offer       *update.Offer
	slot        string
	status      string
	failure     string
	working     bool
	progress    update.Progress
	preferences update.Preferences
}

func (a *App) checkForUpdate() {
	s := &a.softwareUpdate
	if s.working || s.slot != "" || a.options.Updater == nil {
		return
	}
	s.working, s.status, s.failure = true, "正在检查更新…", ""
	client := a.options.Updater.ForChannel(s.preferences.Channel)
	go func() {
		offer, err := client.Check(s.ctx)
		a.update(func() {
			s.working = false
			s.offer = offer
			if errors.Is(err, update.ErrNoChannelRelease) {
				s.status = err.Error()
			} else if err != nil {
				s.status, s.failure = "检查更新失败", err.Error()
			} else if offer == nil {
				s.status = "当前已是最新版本"
			} else {
				s.status = "发现新版本"
			}
		})
	}()
}

func (a *App) downloadUpdate() {
	s := &a.softwareUpdate
	if s.working || s.offer == nil || s.slot != "" {
		return
	}
	offer := *s.offer
	client := a.options.Updater.ForChannel(s.preferences.Channel)
	s.working, s.status, s.failure = true, "正在准备下载…", ""
	s.progress = update.Progress{}
	go func() {
		slot, err := client.Stage(s.ctx, offer, func(progress update.Progress) {
			a.update(func() {
				s.status = "正在下载完整更新"
				if progress.Delta {
					s.status = "正在下载增量更新"
				}
				if progress.Received == progress.Total {
					s.status = "正在校验并组装新版…"
				}
				s.progress = progress
			})
		})
		a.update(func() {
			s.working = false
			if err != nil {
				s.status, s.failure = "下载或校验失败", err.Error()
				return
			}
			s.slot, s.status = slot, "更新已就绪"
		})
	}()
}

func (a *App) restartToUpdate() {
	if a.softwareUpdate.slot == "" || a.options.RestartUpdate == nil {
		return
	}
	a.confirmReplace(func() {
		a.options.RestartUpdate(a.softwareUpdate.slot)
		a.closing = true
		a.win.Close()
	})
}

func (a *App) updateSettings(c *ui.Context) {
	s := &a.softwareUpdate
	u := a.options.Updater.ForChannel(s.preferences.Channel)
	channelNames := []string{"Alpha", "Beta", "RC", "Release"}
	channelKeys := []string{"alpha", "beta", "rc", "stable"}
	selected := "Release"
	for i, key := range channelKeys {
		if key == s.preferences.Channel {
			selected = channelNames[i]
		}
	}
	next := s.preferences
	previous := selected
	settingsRow(c, "更新渠道", func() {
		ui.Select(c, &selected, channelNames).Width(180).Height(30).Disabled(s.working || s.slot != "").Label("更新渠道")
	})
	if selected != previous {
		for i, name := range channelNames {
			if selected == name {
				next.Channel = channelKeys[i]
			}
		}
	}
	ui.Checkbox(c, &next.Auto, "自动检测更新")
	if next != s.preferences {
		if err := next.Save(a.options.Engine.DataDir); err != nil {
			s.failure = err.Error()
		} else {
			channelChanged := next.Channel != s.preferences.Channel
			autoEnabled := next.Auto && !s.preferences.Auto
			s.preferences = next
			if channelChanged {
				s.offer, s.status, s.failure, s.progress = nil, "", "", update.Progress{}
			}
			if channelChanged || autoEnabled {
				a.checkForUpdate()
			}
		}
	}
	settingsRow(c, "当前版本", func() {
		ui.Text(c, "v"+u.Version).FontSize(13)
	})
	if s.offer != nil {
		settingsRow(c, "可用版本", func() {
			ui.Text(c, fmt.Sprintf("v%s · 约 %.1f MB", s.offer.Version, float64(u.DownloadSize(*s.offer))/1e6)).FontSize(13)
		})
	}
	if s.status != "" {
		ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
			if s.working {
				ui.Spinner(c).Size(14, 14)
			}
			label := s.status
			if s.working && s.progress.Total > 0 {
				label = fmt.Sprintf("%s  %d%%", label, 100*s.progress.Received/s.progress.Total)
			}
			ui.Text(c, label).FontSize(13).TextColor(c.Theme().TextMuted)
		})
	}
	if s.failure != "" {
		ui.Text(c, s.failure).FontSize(12).TextColor(c.Theme().Danger)
	}
	if failure := a.updateFailure; failure != "" {
		ui.Text(c, "上次更新未应用，当前版本仍可使用。"+failure).FontSize(12).TextColor(c.Theme().Danger)
	}
	ui.Row(c).Gap(8).Children(func() {
		if updateAction(c, "检查更新", updateCheckIcon, false).Disabled(s.working || s.slot != "").Clicked() {
			a.checkForUpdate()
		}
		if s.slot != "" {
			if updateAction(c, "重启并更新", updateRestartIcon, true).Clicked() {
				a.restartToUpdate()
			}
		} else if s.offer != nil && (s.offer.Channel == a.options.Updater.Channel() || u.SupportsChannelSwitch()) {
			if updateAction(c, "下载更新", updateDownloadIcon, true).Disabled(s.working).Clicked() {
				a.downloadUpdate()
			}
		}
	})
	if s.offer != nil && s.offer.Channel != a.options.Updater.Channel() && !u.SupportsChannelSwitch() {
		ui.Text(c, "这个启动器需要用新版完整包升级一次，才能应用跨渠道更新。").FontSize(12).TextColor(c.Theme().TextMuted)
	}
}

func updateAction(c *ui.Context, label, icon string, primary bool) ui.Element {
	var button ui.Element
	color := c.Theme().Text
	if primary {
		button = ui.PrimaryButton(c, "")
		color = c.Theme().AccentText
	} else {
		button = ui.Button(c, "")
	}
	return button.Key(label).Label(label).Height(32).Gap(7).Children(func() {
		glyph(c, icon, color).Size(15, 15)
		ui.Text(c, label).FontSize(12).TextColor(color)
	})
}
