package workbench

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

func (a *App) openSettings() {
	if a.busy || a.showSettings {
		return
	}
	// Preserve the current viewport choices, including toolbar changes made
	// since the previous save, as the baseline for cancelling this preview.
	a.settings = clone(a.settings)
	a.writeDisplaySettings(a.settings)
	a.settingsDraft = clone(a.settings)
	llm := object(a.settingsDraft["llm"])
	a.profile = text(llm["active_provider_id"])
	a.providerEnabled = llm["enabled"] == true
	a.advancedJSON = pretty(object(llm["agent_stage_overrides"]))
	a.readProfile()
	a.showSettings = true
}

func (a *App) cancelSettings() {
	a.applyViewerSettings()
	a.showSettings = false
	a.settingsDraft = nil
	a.modelChoices = nil
}

// Called after View builds its modal layer so Escape and backdrop dismissal
// restore the preview even when the settings content is no longer rendered.
func (a *App) finishSettingsFrame() {
	if !a.showSettings && a.settingsDraft != nil {
		if a.busy {
			a.showSettings = true
		} else {
			a.cancelSettings()
		}
	}
}

func (a *App) writeDisplaySettings(settings Object) {
	viewer := object(settings["viewer"])
	viewer["edges"], viewer["grid"], viewer["axes"], viewer["perspective"] = a.edges, a.grid, a.axes, a.perspective
	settings["viewer"] = viewer
	online := object(settings["online"])
	online["enabled"] = a.online
	settings["online"] = online
	native := object(settings["native_desktop"])
	native["theme"], native["show_thoughts"] = a.theme, a.showThoughts
	settings["native_desktop"] = native
}

func (a *App) readProfile() {
	llm := object(a.settingsDraft["llm"])
	p := llm
	for _, candidate := range rows(llm["providers"]) {
		if text(candidate["id"]) == a.profile {
			p = candidate
			break
		}
	}
	a.providerName = text(p["name"])
	a.providerURL = text(p["base_url"])
	a.providerKey = text(p["api_key"])
	a.providerModel = text(p["model"])
	a.effort = text(p["reasoning_effort"])
	if a.effort == "" {
		a.effort = "none"
	}
	a.modelChoices = nil
}

func (a *App) storeProfile() {
	llm := object(a.settingsDraft["llm"])
	providers := rows(llm["providers"])
	if a.profile == "" && len(providers) == 0 && a.providerURL == "" && a.providerModel == "" && a.providerKey == "" && a.providerName == "" {
		llm["providers"], llm["active_provider_id"], llm["enabled"] = []any{}, "", false
		a.settingsDraft["llm"] = llm
		return
	}
	if a.profile == "" {
		a.profile = fmt.Sprintf("native-%d", time.Now().UnixNano())
	}
	var current Object
	for _, p := range providers {
		if text(p["id"]) == a.profile {
			current = p
			break
		}
	}
	if current == nil {
		current = Object{"id": a.profile}
		providers = append(providers, current)
	}
	current["name"], current["base_url"], current["api_key"], current["model"], current["reasoning_effort"] = a.providerName, a.providerURL, a.providerKey, a.providerModel, a.effort
	values := make([]any, len(providers))
	for i, p := range providers {
		values[i] = p
	}
	llm["providers"], llm["active_provider_id"], llm["enabled"] = values, a.profile, a.providerEnabled
	for _, key := range []string{"base_url", "api_key", "model", "reasoning_effort"} {
		llm[key] = current[key]
	}
	a.settingsDraft["llm"] = llm
}

func (a *App) confirmRemoveProvider() {
	if a.busy || a.profile == "" {
		return
	}
	id, name := a.profile, a.providerName
	a.busy = true
	go func() {
		answer, err := mygo.Dialog.Message(mygo.MessageOptions{Parent: a.win, Type: mygo.MessageWarning,
			Title: "删除模型服务", Message: "删除此服务配置？", Detail: name,
			Buttons: []string{"取消", "删除服务"}, DefaultButton: 0, CancelButton: 0})
		a.update(func() {
			a.busy = false
			if err != nil {
				a.fail(err)
				return
			}
			if answer.Button == 1 && a.showSettings {
				if err := a.removeProviderDraft(id); err != nil {
					a.fail(err)
				}
			}
		})
	}()
}

func (a *App) removeProviderDraft(id string) error {
	var stages Object
	if err := json.Unmarshal([]byte(a.advancedJSON), &stages); err != nil || stages == nil {
		return fmt.Errorf("阶段配置需要有效的 JSON 对象。")
	}
	a.storeProfile()
	llm := object(a.settingsDraft["llm"])
	remaining := []any{}
	for _, provider := range rows(llm["providers"]) {
		if text(provider["id"]) != id {
			remaining = append(remaining, provider)
		}
	}
	llm["providers"] = remaining
	for _, stage := range stages {
		if entry := object(stage); text(entry["provider_id"]) == id {
			entry["provider_id"] = ""
		}
	}
	a.advancedJSON = pretty(stages)
	llm["agent_stage_overrides"] = stages
	a.profile = ""
	if len(remaining) > 0 {
		a.profile = text(object(remaining[0])["id"])
	}
	llm["active_provider_id"] = a.profile
	if a.profile == "" {
		for _, key := range []string{"name", "base_url", "api_key", "model", "reasoning_effort"} {
			llm[key] = ""
		}
		a.providerEnabled = false
		llm["enabled"] = false
	}
	a.readProfile()
	return nil
}

func (a *App) settingsView(c *ui.Context) {
	width, height := c.Size()
	// Keep the overlay parent alive while the panel's exit copy is fading.
	// View calls this on every frame, including after showSettings becomes false.
	ui.Overlay(c, func() {
		backdrop := ui.Box(c).Key("settings-overlay").Absolute().Left(0).Top(0).Right(0).Bottom(0).Center()
		target := float32(0)
		if a.showSettings {
			target = 1
			backdrop.Modal()
			if (backdrop.Clicked() || backdrop.OverlayShortcut(0, ui.KeyEscape)) && !a.busy {
				a.cancelSettings()
				target = 0
			}
		} else {
			backdrop.PassThrough()
		}
		opacity := backdrop.Animate("dimming", target, 160*time.Millisecond)
		backdrop.Background(ui.RGBA(0, 0, 0, 0.4*opacity))
		backdrop.Children(func() {
			if !a.showSettings {
				return
			}
			transition := ui.ElementTransition{Duration: 160 * time.Millisecond, ExitDuration: 120 * time.Millisecond,
				Colors: true, Enter: &ui.Motion{Y: 8}, Exit: &ui.Motion{Y: 4}}
			if c.Preferences().ReduceMotion {
				transition.Duration, transition.ExitDuration = -1, -1
			}
			panel := ui.Column(c).Key("settings-panel").Role(ui.RoleDialog).Label("设置窗口").FontWeight(500).
				Padding(0).Gap(0).Width(min(float32(800), width-48)).Height(min(float32(600), height-48)).
				Radius(4).Border(1, c.Theme().Border).Background(c.Theme().Background).
				Shadow(0, 10, 30, 0, ui.RGBA(0, 0, 0, 0.3)).Transition(transition)
			panel.Clicked() // Consume clicks inside the panel before the backdrop.
			panel.Children(func() { a.settingsPanel(c) })
		})
	})
	a.finishSettingsFrame()
}

func (a *App) settingsPanel(c *ui.Context) {
	closeRequested := false
	categories := []struct{ label, icon string }{
		{"模型服务", "settings-2"},
		{"显示与行为", "sliders-horizontal"},
		{"几何计算", "box"},
		{"运行环境", "logs"},
	}
	if a.options.Updater != nil {
		categories = append(categories, struct{ label, icon string }{"更新", "export"})
	}
	ui.Column(c).Fill().Gap(0).Disabled(a.busy).Children(func() {
		ui.Row(c).Height(50).Padding(0, 16, 0, 20).AlignItems(ui.Center).Children(func() {
			ui.Text(c, "设置").FontSize(16).FontWeight(600).Grow(1)
			if iconButton(c, "关闭设置", "x", false).Clicked() {
				closeRequested = true
			}
		})
		sectionRule(c)
		ui.Row(c).FillWidth().Grow(1).MinHeight(0).Gap(0).Children(func() {
			ui.Column(c).Width(160).FillHeight().Padding(12, 8).Gap(4).Children(func() {
				for index, category := range categories {
					selected := a.settingsTab == index
					transition := controlTransition
					if c.Preferences().ReduceMotion {
						transition.Duration = -1
					}
					button := ui.ButtonBase(c).Key(category.label).Label(category.label).
						FillWidth().Height(36).Padding(0, 12).Gap(9).Radius(4).Justify(ui.Start).Transition(transition)
					color := c.Theme().TextMuted
					if selected {
						button.Background(c.Theme().Selection)
						color = c.Theme().Text
					} else if button.Hovered() {
						button.Background(c.Theme().SurfaceHover)
						color = c.Theme().Text
					}
					button.Children(func() {
						glyph(c, category.icon, color).Size(16, 16)
						ui.Text(c, category.label).FontSize(12).TextColor(color)
					})
					if button.Clicked() {
						a.settingsTab = index
					}
				}
			})
			ui.Box(c).Width(1).FillHeight().Background(c.Theme().Border)
			transition := ui.ElementTransition{Duration: 140 * time.Millisecond, Enter: &ui.Motion{X: 6}}
			if c.Preferences().ReduceMotion {
				transition.Duration = -1
			}
			ui.Scroll(c).Key(a.settingsTab).Grow(1).MinWidth(0).FillHeight().Label("设置内容").Background(c.Theme().Surface).Transition(transition).Children(func() {
				ui.Column(c).FillWidth().Padding(20, 24).Gap(14).Children(func() {
					ui.Text(c, categories[a.settingsTab].label).FontSize(16).FontWeight(600).Margin(0, 0, 4, 0)
					switch a.settingsTab {
					case 1:
						a.displaySettings(c)
					case 2:
						a.geometrySettings(c)
					case 3:
						a.runtimeSettings(c)
					case 4:
						a.updateSettings(c)
					default:
						a.providerSettings(c)
					}
				})
			})
		})
		if a.failure != "" {
			ui.Text(c, a.failure).Padding(6, 20).FontSize(12).TextColor(c.Theme().Danger).MaxHeight(48)
		}
		sectionRule(c)
		ui.Row(c).Height(54).Padding(0, 20).AlignItems(ui.Center).Justify(ui.End).Gap(8).Children(func() {
			if a.busy {
				ui.Spinner(c).Size(14, 14)
				ui.Text(c, a.operation).FontSize(12).TextColor(c.Theme().TextMuted)
			}
			ui.Spacer(c)
			if ui.Button(c, "取消").Width(76).Height(30).Clicked() {
				a.cancelSettings()
			}
			if a.settingsTab != 4 && ui.PrimaryButton(c, "保存设置").Width(92).Height(30).Disabled(a.busy).Clicked() {
				a.saveSettings()
			}
		})
	})
	if closeRequested {
		a.cancelSettings()
	}
}

func (a *App) providerSettings(c *ui.Context) {
	ui.Checkbox(c, &a.providerEnabled, "启用模型服务")
	providers := rows(object(a.settingsDraft["llm"])["providers"])
	ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
		labels := make([]string, len(providers))
		selected := ""
		for i, p := range providers {
			labels[i] = fmt.Sprintf("%s (%d)", text(p["name"]), i+1)
			if text(p["id"]) == a.profile {
				selected = labels[i]
			}
		}
		before := selected
		ui.Select(c, &selected, labels).Grow(1).MinWidth(0).Height(30).Label("服务配置")
		if selected != before {
			a.storeProfile()
			for i, label := range labels {
				if label == selected {
					a.profile = text(providers[i]["id"])
					a.readProfile()
				}
			}
		}
		iconButton(c, "添加服务", "plus", false).Size(30, 30).Menu(func(m *ui.Menu) {
			for _, entry := range []struct{ id, label string }{{"openai", "OpenAI"}, {"deepseek", "DeepSeek"}, {"siliconflow", "SiliconFlow"}, {"local", "本地 llama.cpp"}, {"custom", "自定义服务"}} {
				if m.Item(entry.label).Chosen() {
					a.storeProfile()
					a.invoke("添加服务", "native_provider_template", Object{"id": entry.id}, func(result any) {
						if !a.showSettings {
							return
						}
						p := object(result)
						a.profile = fmt.Sprintf("native-%d", time.Now().UnixNano())
						a.providerName = text(p["name"])
						a.providerURL = text(p["base_url"])
						a.providerKey = text(p["api_key"])
						a.providerModel = text(p["model"])
						a.effort = text(p["reasoning_effort"])
						a.modelChoices = nil
						a.storeProfile()
					})
				}
			}
		})
		if iconButton(c, "删除服务", "trash", false).Size(30, 30).Disabled(a.busy || a.profile == "").Clicked() {
			a.confirmRemoveProvider()
		}
	})
	ui.Grid(c).Columns(2).Gap(16).FillWidth().Children(func() {
		field(c, "配置名称", func() { ui.TextInput(c, &a.providerName).FillWidth().Height(30).Label("配置名称") })
		field(c, "推理强度", func() {
			ui.Select(c, &a.effort, []string{"none", "low", "high", "max"}).FillWidth().Height(30).Label("推理强度")
		})
	})
	settingsRow(c, "接口地址", func() {
		ui.TextInput(c, &a.providerURL).FillWidth().Height(30).Label("接口地址").Placeholder("https://…/v1")
	})
	settingsRow(c, "API Key", func() { ui.TextInput(c, &a.providerKey).Password().FillWidth().Height(30).Label("API Key") })
	settingsRow(c, "模型", func() {
		ui.Row(c).Gap(8).Children(func() {
			ui.TextInput(c, &a.providerModel).Grow(1).MinWidth(0).Height(30).Label("模型名称")
			if ui.Button(c, "获取模型").Height(30).Disabled(a.busy || a.providerURL == "").Clicked() {
				a.invoke("获取模型", "native_provider_fetch_models", Object{"baseUrl": a.providerURL, "apiKey": a.providerKey}, func(result any) {
					a.modelChoices = nil
					if values, ok := object(result)["models"].([]any); ok {
						for _, v := range values {
							a.modelChoices = append(a.modelChoices, text(v))
						}
					}
				})
			}
		})
	})
	if len(a.modelChoices) > 0 {
		ui.Select(c, &a.providerModel, a.modelChoices).FillWidth().Height(30).Label("可用模型")
	}
	ui.Collapsible(c, "分阶段模型配置", &a.stageExpanded, func() {
		a.stageSettings(c)
	})
}

func (a *App) stageSettings(c *ui.Context) {
	var stages Object
	if err := json.Unmarshal([]byte(a.advancedJSON), &stages); err != nil || stages == nil {
		ui.Text(c, "阶段配置无法读取，请重新打开设置。").TextColor(c.Theme().Danger)
		return
	}
	providers := rows(object(a.settingsDraft["llm"])["providers"])
	labels, ids := []string{"继承当前服务"}, []string{""}
	for index, provider := range providers {
		labels = append(labels, fmt.Sprintf("%s (%d)", text(provider["name"]), index+1))
		ids = append(ids, text(provider["id"]))
	}
	ui.Column(c).FillWidth().Gap(18).Padding(12, 0).Children(func() {
		for _, stage := range []struct{ id, label string }{{"perception", "识图"}, {"modeling", "建模"}, {"review", "复核"}} {
			entry := object(stages[stage.id])
			providerID, model, effort := text(entry["provider_id"]), text(entry["model"]), text(entry["reasoning_effort"])
			selected := "继承当前服务"
			choices := append([]string{}, labels...)
			for i, id := range ids {
				if id == providerID {
					selected = labels[i]
				}
			}
			if providerID != "" && selected == "继承当前服务" {
				selected = "未找到服务 (" + providerID + ")"
				choices = append(choices, selected)
			}
			before := selected
			ui.Column(c).Key(stage.id).FillWidth().Gap(10).Children(func() {
				ui.Text(c, stage.label).FontSize(12).Bold()
				ui.Grid(c).Columns(2).Gap(16).FillWidth().Children(func() {
					field(c, "服务", func() {
						ui.Select(c, &selected, choices).FillWidth().Height(30).Label(stage.label + "服务")
					})
					field(c, "推理强度", func() {
						ui.Select(c, &effort, []string{"", "none", "low", "high", "max"}).FillWidth().Height(30).Placeholder("继承服务设置").Label(stage.label + "推理强度")
					})
				})
				field(c, "模型", func() {
					ui.TextInput(c, &model).FillWidth().Height(30).Placeholder("继承服务模型").Label(stage.label + "模型")
				})
			})
			if selected != before {
				for i, label := range labels {
					if selected == label {
						entry["provider_id"] = ids[i]
					}
				}
			}
			if model != text(entry["model"]) {
				entry["model"] = model
			}
			if effort != text(entry["reasoning_effort"]) {
				entry["reasoning_effort"] = effort
			}
			if len(entry) > 0 {
				stages[stage.id] = entry
			}
		}
	})
	a.advancedJSON = pretty(stages)
}

func (a *App) geometrySettings(c *ui.Context) {
	ui.Grid(c).Columns(2).Gap(18).FillWidth().Children(func() {
		for _, input := range []struct {
			section, key, label      string
			fallback, min, max, step float64
		}{
			{"build", "time_budget", "几何计算时限（秒）", 30, 1, 600, 1},
			{"build", "max_faces", "最大面片数", 60000, 1000, 10000000, 1000},
			{"viewer", "max_faces", "预览面片上限", 6000, 1000, 10000000, 1000},
			{"online", "timeout", "联网搜索超时（秒）", 8, 1, 120, 1},
		} {
			section := object(a.settingsDraft[input.section])
			value := number(section[input.key])
			if _, exists := section[input.key]; !exists {
				value = input.fallback
			}
			field(c, input.label, func() {
				ui.NumberInput(c, &value, input.min, input.max, input.step).FillWidth().Height(30).Label(input.label)
			})
			section[input.key] = value
			a.settingsDraft[input.section] = section
		}
	})
}

func field(c *ui.Context, label string, body func()) {
	ui.Column(c).FillWidth().MinWidth(0).Gap(6).Children(func() {
		ui.Text(c, label).FontSize(12).TextColor(c.Theme().TextMuted)
		body()
	})
}

func settingsRow(c *ui.Context, label string, body func()) {
	ui.Row(c).FillWidth().Gap(12).AlignItems(ui.Center).Children(func() {
		ui.Text(c, label).Width(76).FontSize(12).TextColor(c.Theme().TextMuted)
		ui.Column(c).Grow(1).MinWidth(0).Children(body)
	})
}

func (a *App) displaySettings(c *ui.Context) {
	settingsRow(c, "外观", func() {
		ui.Select(c, &a.theme, []string{"跟随系统", "浅色", "深色"}).Width(220).Height(30).Label("外观")
	})
	sectionRule(c)
	sectionTitle(c, "模型显示")
	ui.Grid(c).Columns(2).Gap(14).FillWidth().Children(func() {
		ui.Checkbox(c, &a.edges, "模型边线")
		ui.Checkbox(c, &a.grid, "基准网格")
		ui.Checkbox(c, &a.axes, "坐标轴")
		ui.Checkbox(c, &a.perspective, "透视投影")
	})
	sectionRule(c)
	sectionTitle(c, "Agent 行为")
	ui.Grid(c).Columns(2).Gap(14).FillWidth().Children(func() {
		ui.Checkbox(c, &a.online, "允许在线查询")
		ui.Checkbox(c, &a.showThoughts, "显示推理过程")
	})
	ui.Text(c, "界面过渡与相机动画遵循系统动画设置。").FontSize(12).TextColor(c.Theme().TextMuted)
}

func (a *App) runtimeSettings(c *ui.Context) {
	occt, dsh, catia := object(a.runtime["occt"]), object(a.runtime["dsh"]), object(a.runtime["catia"])
	occtState, occtDetail := "未就绪", "请检查 OCCT 运行库。"
	if occt["available"] == true && occt["verified"] == true {
		occtState, occtDetail = "已校验", "用于 Quick 建模、几何重建和模型导出。"
	}
	dshState, dshDetail := "待安装", "安装后可使用 Agent 建模。"
	if dsh["installed"] == true && dsh["verified"] == true {
		dshState, dshDetail = "已安装", "DSH "+text(dsh["dshVersion"])
	} else if dsh["bundleRequired"] == true && dsh["bundled"] != true {
		dshState, dshDetail = "缺少安装资源", "需要包含 DSH 安装资源的发行包。"
	}
	catiaState, catiaDetail := "未检测", "推送模型时检测 CATIA 连接。"
	if catia["registered"] == true {
		catiaState = "COM 已注册"
	} else if catia["registered"] == false {
		catiaState, catiaDetail = "COM 未注册", "需要本机安装并注册 CATIA V5。"
	}
	if catia["connected"] == true {
		catiaState, catiaDetail = "已连接", "可通过 COM 推送和导出零件。"
	}
	for index, row := range []struct {
		name, state, detail string
		source              Object
	}{
		{"OCCT 几何内核", occtState, occtDetail, occt},
		{"Agent 运行时", dshState, dshDetail, dsh},
		{"CATIA V5", catiaState, catiaDetail, catia},
	} {
		if index > 0 {
			sectionRule(c)
		}
		ui.Column(c).Key(row.name).Gap(6).FillWidth().Children(func() {
			ui.Row(c).Gap(12).Children(func() {
				ui.Text(c, row.name).FontSize(12).FontWeight(600).Grow(1)
				ui.Text(c, row.state).FontSize(12).TextColor(c.Theme().TextMuted)
			})
			ui.Text(c, row.detail).FontSize(12).TextColor(c.Theme().TextMuted)
			if reason := text(row.source["reason"]); reason != "" {
				ui.Text(c, reason).FontSize(12).TextColor(c.Theme().Danger).Selectable()
			}
		})
	}
	ui.Row(c).Gap(8).Children(func() {
		if ui.Button(c, "重新检测").Height(30).Disabled(a.busy).Clicked() {
			a.invoke("检测环境", "native_runtime_status", Object{}, func(result any) { a.runtime = object(result) })
		}
		if ui.Button(c, "核心自检").Height(30).Disabled(a.busy).Clicked() {
			a.invoke("核心自检", "native_selftest", Object{}, func(result any) { a.addLog(pretty(result)); a.showLogs = true })
		}
		if ui.Button(c, "安装 Agent 运行时").Height(30).Disabled(a.busy || dsh["installed"] == true || (dsh["bundleRequired"] == true && dsh["bundled"] != true)).Clicked() {
			a.invoke("安装 Agent 运行时", "install_dsh_runtime", Object{}, func(result any) {
				a.addLog(pretty(result))
				a.invoke("检测环境", "native_runtime_status", Object{}, func(status any) { a.runtime = object(status) })
			})
		}
	})
	details := ui.Column(c).FillWidth()
	expanded := ui.Local(details, "runtime-details", func() bool { return false })
	details.Children(func() {
		ui.Collapsible(c, "诊断详情", expanded, func() { ui.Text(c, pretty(a.runtime)).FontSize(11).Selectable() })
	})
}

func (a *App) saveSettings() {
	if a.busy || !a.showSettings {
		return
	}
	var stages Object
	if err := json.Unmarshal([]byte(a.advancedJSON), &stages); err != nil || stages == nil {
		a.failure = "阶段配置需要有效的 JSON 对象。"
		return
	}
	a.storeProfile()
	object(a.settingsDraft["llm"])["agent_stage_overrides"] = stages
	a.writeDisplaySettings(a.settingsDraft)
	a.invoke("保存设置", "native_settings_save", Object{"settings": clone(a.settingsDraft)}, func(result any) {
		a.settings = object(result)
		a.cancelSettings()
	})
}
