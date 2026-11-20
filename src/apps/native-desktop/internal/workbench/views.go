package workbench

import (
	"fmt"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"
)

var appear = ui.ElementTransition{Duration: 160 * time.Millisecond, Colors: true, Enter: &ui.Motion{Y: 4}}

func (a *App) View(c *ui.Context) {
	width, _ := c.Size()
	a.leftWidth = max(240, min(a.leftWidth, width-600))
	theme := workbenchTheme(a.theme == "深色" || (a.theme == "跟随系统" && c.Theme().Dark))
	c.SetTheme(theme)
	a.scene.SetOptions(a.edges, a.grid, a.axes, a.perspective, theme.Dark)
	root := ui.Column(c).Fill().Gap(0).FontWeight(500)
	detailsTarget, logsTarget, settingsTarget := float32(0), float32(0), float32(0)
	if a.showDetails {
		detailsTarget = 1
	}
	if a.showLogs {
		logsTarget = 180
	}
	if a.showSettings {
		settingsTarget = 1
	}
	settingsProgress := root.Animate("settings", settingsTarget, 160*time.Millisecond)
	if native, ok := a.scene.(interface {
		SetVisible(bool)
		SetReducedMotion(bool)
	}); ok {
		native.SetVisible(!a.showSettings && settingsProgress == 0)
		native.SetReducedMotion(c.Preferences().ReduceMotion)
	}
	// Animate layout dimensions so the native child viewport follows every frame.
	detailsProgress := root.Animate("details", detailsTarget, 220*time.Millisecond)
	logsHeight := root.Animate("logs", logsTarget, 200*time.Millisecond)
	root.Children(func() {
		a.titlebar(c)
		sectionRule(c)
		displayedWidth := a.leftWidth * detailsProgress
		split := ui.Split(c, &displayedWidth, func() {
			if detailsProgress > 0 {
				ui.Column(c).Width(a.leftWidth).FillHeight().Background(theme.Surface).Children(func() { a.inspector(c) })
			}
		}, func() { a.modelingArea(c) }).Grow(1).MinHeight(0).SplitReset(286)
		if split.Changed() && detailsProgress == 1 {
			a.leftWidth = displayedWidth
		}
		ui.Box(c).Key("logs").Height(logsHeight).Clip().Children(func() {
			if logsHeight > 0 {
				a.logPane(c)
			}
		})
		a.statusbar(c)
	})
	a.settingsView(c)
	if c.Shortcut(ui.Cmd, ui.KeyS) {
		a.save(false, nil)
	}
	if c.Shortcut(ui.Cmd, ui.KeyO) {
		a.openProject()
	}
	if c.Shortcut(ui.Cmd, ui.KeyN) {
		a.newProject()
	}
}

func (a *App) modelingArea(c *ui.Context) {
	ui.Row(c).Fill().Gap(0).Children(func() {
		a.viewportPane(c)
		ui.Box(c).Width(1).FillHeight().Background(c.Theme().Border)
		ui.Box(c).Width(300).FillHeight().Children(func() { a.leftPane(c) })
	})
}

func (a *App) titlebar(c *ui.Context) {
	t := c.Theme()
	ui.Row(c).Height(44).AlignItems(ui.Center).Background(t.Surface).Gap(0).Children(func() {
		ui.Row(c).Height(44).Padding(0, 14).AlignItems(ui.Center).Gap(8).DragWindow().Children(func() {
			glyph(c, "box", t.TextMuted)
			ui.Text(c, "FormRecall").FontSize(13).FontWeight(600)
		})
		ui.Box(c).Width(1).Height(20).Background(t.Border)
		a.toolbar(c)
		if a.windowButton(c, "最小化", "minus").Clicked() && a.win != nil {
			a.win.Minimize()
		}
		if a.windowButton(c, "最大化或还原", "square").MaximizeButton().Clicked() && a.win != nil {
			if a.win.IsMaximized() {
				a.win.Unmaximize()
			} else {
				a.win.Maximize()
			}
		}
		if a.windowButton(c, "关闭窗口", "x").Clicked() && a.win != nil {
			a.win.Close()
		}
	})
}

func (a *App) windowButton(c *ui.Context, label, icon string) ui.Element {
	button := ui.ButtonBase(c).Key(label).Size(44, 44).Label(label).Tooltip(label).Transition(controlTransition)
	color := c.Theme().TextMuted
	if button.Hovered() {
		if icon == "x" {
			button.Background(ui.Hex("#C64450"))
			color = ui.Hex("#FFFFFF")
		} else {
			button.Background(c.Theme().SurfaceHover)
		}
	}
	if button.Pressed() {
		button.Background(c.Theme().SurfacePressed)
	}
	button.Children(func() { glyph(c, icon, color) })
	return button
}

func (a *App) toolbar(c *ui.Context) {
	ui.Row(c).Grow(1).Height(44).Padding(6, 10).Gap(3).AlignItems(ui.Center).Children(func() {
		if iconButton(c, "新建", "file-plus", false).Disabled(!a.ready || a.busy).Tooltip("新建工程 · Ctrl+N").Clicked() {
			a.newProject()
		}
		if iconButton(c, "打开", "folder-open", false).Disabled(!a.ready || a.busy).Tooltip("打开工程 · Ctrl+O").Clicked() {
			a.openProject()
		}
		if iconButton(c, "保存", "save", false).Disabled(!a.ready || a.busy || len(a.document) == 0).Tooltip("保存工程 · Ctrl+S").Clicked() {
			a.save(false, nil)
		}
		toolbarDivider(c)
		toolButton(c, "导出", "upload", false).Menu(func(m *ui.Menu) {
			if m.Item("STL 网格").Chosen() {
				a.export("stl")
			}
			if m.Item("OBJ 网格").Chosen() {
				a.export("obj")
			}
			if m.Item("建模报告").Chosen() {
				a.export("report")
			}
			if m.Item("模型快照").Chosen() {
				a.export("snapshot")
			}
			for _, entry := range []struct{ label, kind string }{{"三视图", "three"}, {"四视图", "four"}, {"扩展视图", "extended"}} {
				if m.Item(entry.label).Chosen() {
					a.export(entry.kind)
				}
			}
			if m.Item("CATIA 原生零件").Chosen() {
				a.export("catia")
			}
			if m.Item("另存工程").Chosen() {
				a.save(true, nil)
			}
		}).Disabled(a.busy || len(rows(a.document["features"])) == 0)
		ui.Row(c).Grow(1).Height(32).Padding(0, 20).AlignItems(ui.Center).DragWindow().Children(func() {
			name := text(a.document["name"])
			if name == "" {
				name = "未命名工程"
			}
			if a.dirty {
				name += "  •"
			}
			ui.Text(c, name).TextColor(c.Theme().TextMuted).SingleLine()
		})
		if iconButton(c, "属性", "panel-right", a.showDetails).Clicked() {
			a.showDetails = !a.showDetails
		}
		if iconButton(c, "日志", "logs", a.showLogs).Clicked() {
			a.showLogs = !a.showLogs
		}
		if iconButton(c, "设置", "settings-2", false).Disabled(!a.ready || a.busy).Clicked() {
			a.openSettings()
		}
	})
}

func (a *App) leftPane(c *ui.Context) {
	ui.Column(c).Fill().Gap(0).Background(c.Theme().Surface).Children(func() {
		ui.Tabs(c, &a.leftTab, "建模", "零件库", "案例").Height(38)
		ui.Column(c).Key(a.leftTab).Grow(1).MinHeight(0).Transition(appear).Children(func() {
			switch a.leftTab {
			case 1:
				a.catalog(c)
			case 2:
				a.casePane(c)
			default:
				a.chat(c)
			}
		})
	})
}

func (a *App) chat(c *ui.Context) {
	ui.Row(c).Height(40).Padding(4, 12).Gap(6).AlignItems(ui.Center).Children(func() {
		ui.MenuButton(c, "示例", func(m *ui.Menu) {
			for _, sample := range a.samples {
				if m.Item(text(sample["label"])).Chosen() {
					a.prompt = text(sample["prompt"])
				}
			}
		}).Height(28).Border(0, ui.Transparent).Background(ui.Transparent).Disabled(a.busy)
		ui.Spacer(c)
		if iconButton(c, "新对话", "plus", false).Disabled(!a.ready || a.busy).Clicked() {
			a.resetChat(nil)
		}
	})
	ui.Scroll(c).Grow(1).MinHeight(0).TrackScroll(&a.chatScroll).Children(func() {
		ui.Column(c).Padding(16).Gap(20).Children(func() {
			for _, finding := range a.findings {
				a.findingCard(c, finding)
			}
			if len(a.messages) == 0 {
				ui.Column(c).Gap(8).Children(func() {
					ui.Text(c, "描述你的零件").FontSize(16).FontWeight(600)
					ui.Text(c, "输入形状与尺寸，开始参数建模。").TextColor(c.Theme().TextMuted)
				})
				for i, sample := range a.samples {
					if i >= 4 {
						break
					}
					label := text(sample["label"])
					if toolButton(c, label, "box", false).FillWidth().Justify(ui.Start).Height(34).Clicked() {
						a.prompt = text(sample["prompt"])
					}
				}
			}
			for i, message := range a.messages {
				if message.Role == "thought" && !a.showThoughts {
					continue
				}
				ui.Column(c).Key(fmt.Sprintf("msg-%d", i)).Gap(8).Transition(appear).Children(func() {
					role := map[string]string{"user": "建模需求", "assistant": "FormRecall", "thought": "推理过程"}[message.Role]
					if role == "" {
						role = message.Role
					}
					ui.Row(c).Gap(7).AlignItems(ui.Center).Children(func() {
						color := c.Theme().TextMuted
						ui.Box(c).Size(5, 5).Radius(2).Background(color)
						ui.Text(c, role).FontSize(11).FontWeight(600).TextColor(color)
					})
					ui.Text(c, message.Text).FontSize(13).Selectable()
				})
			}
		})
	})
	sectionRule(c)
	ui.Column(c).Padding(12).Gap(10).Children(func() {
		ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
			ui.Select(c, &a.mode, []string{"Quick", "Agent"}).Width(94).Height(28).Label("建模方式").Disabled(a.busy)
			ui.Spacer(c)
			if a.mode == "Agent" {
				if iconButton(c, "添加图片", "paperclip", false).Disabled(a.busy).Clicked() {
					a.attachImages()
				}
				if iconButton(c, "查询", "search", false).Tooltip("使用当前输入联网查询").Disabled(a.busy).Clicked() {
					a.onlineLookup()
				}
			}
		})
		if len(a.images) > 0 {
			ui.ScrollHorizontal(c).Height(66).Children(func() {
				ui.Row(c).Gap(6).Children(func() {
					for _, path := range a.images {
						if preview := a.thumbnails[path]; preview != nil {
							ui.Image(c, preview).Size(64, 60).Radius(5).Tooltip(filepath.Base(path))
						}
					}
				})
			})
			ui.Row(c).Gap(6).Children(func() {
				ui.Text(c, fmt.Sprintf("已附加 %d 张图片", len(a.images))).TextColor(c.Theme().TextMuted).Grow(1)
				if ui.Button(c, "移除图片").Disabled(a.busy).Clicked() {
					a.images = nil
				}
			})
		}
		ui.TextArea(c, &a.prompt).Label("建模需求").Placeholder("描述形状、尺寸和加工特征…").Height(92).FontSize(13).Background(c.Theme().Background).Disabled(a.busy)
		ui.Row(c).AlignItems(ui.Center).Gap(8).Children(func() {
			ui.Text(c, map[string]string{"Quick": "本地参数建模", "Agent": "模型服务协同建模"}[a.mode]).FontSize(11).TextColor(c.Theme().TextMuted).Grow(1)
			if a.busy {
				if ui.Button(c, "停止").Height(30).Disabled(a.mode != "Agent").Clicked() {
					a.cancel()
				}
			} else if ui.PrimaryButton(c, "生成模型").Height(30).Disabled(!a.ready || (strings.TrimSpace(a.prompt) == "" && len(a.images) == 0)).Clicked() {
				a.submit()
			}
		})
	})
}

func (a *App) catalog(c *ui.Context) {
	ui.Column(c).Fill().Padding(12).Gap(10).Children(func() {
		ui.TextInput(c, &a.search).Height(30).Label("搜索零件").Placeholder("搜索零件类型")
		ui.Scroll(c).Grow(1).Children(func() {
			ui.Column(c).Gap(0).Children(func() {
				count := 0
				for i, part := range a.parts {
					if a.search != "" && !strings.Contains(strings.ToLower(pretty(part)), strings.ToLower(a.search)) {
						continue
					}
					count++
					name, prompt := text(part["category"]), text(part["sample_prompt"])
					row := ui.ButtonBase(c).Key(i).Label(name).FillWidth().Padding(12, 8).Radius(4).Transition(controlTransition)
					if row.Hovered() {
						row.Background(c.Theme().SurfaceHover)
					}
					row.Children(func() {
						ui.Column(c).FillWidth().Gap(8).Children(func() {
							ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
								glyph(c, "box", c.Theme().TextMuted)
								ui.Text(c, name).FontWeight(600)
							})
							ui.Text(c, prompt).FontSize(11).TextColor(c.Theme().TextMuted)
						})
					})
					if row.Clicked() {
						a.prompt = prompt
						a.leftTab = 0
					}
					sectionRule(c)
				}
				if count == 0 {
					ui.Text(c, "没有匹配的零件").TextColor(c.Theme().TextMuted)
				}
			})
		})
	})
}

func (a *App) casePane(c *ui.Context) {
	ui.Scroll(c).Grow(1).Children(func() {
		ui.Column(c).Padding(14).Gap(12).Children(func() {
			ui.Text(c, "已审核案例").FontSize(14).FontWeight(600)
			ui.Text(c, "通过当前模型验收后，可存入案例库用于后续建模。").TextColor(c.Theme().TextMuted)
			if toolButton(c, "收录当前模型", "plus", false).Border(1, c.Theme().Border).Disabled(a.busy || acceptance(a.document) != "已验收").Clicked() {
				a.invoke("收录案例", "reviewed_case_save", Object{"generation": a.generation}, func(result any) { a.cases = rows(result) })
			}
			ui.Checkbox(c, &a.useCases, "Agent 使用审核案例")
			for i, item := range a.cases {
				ui.Column(c).Key(i).Padding(4, 0).Gap(8).Children(func() {
					ui.Text(c, text(item["prompt"])).Bold()
					ui.Text(c, fmt.Sprintf("%d 个建模特征", len(rows(item["features"])))).TextColor(c.Theme().TextMuted)
					if ui.Button(c, "新建并使用描述").Disabled(a.busy).Clicked() {
						a.reuseCase(item)
					}
					if toolButton(c, "从案例库移除", "trash", false).Disabled(a.busy).Clicked() {
						a.removeCase(text(item["id"]))
					}
				})
				sectionRule(c)
			}
			if len(a.cases) == 0 {
				ui.Text(c, "暂无审核案例").TextColor(c.Theme().TextMuted)
			}
		})
	})
}

func (a *App) viewportPane(c *ui.Context) {
	ui.Column(c).Grow(1).FillHeight().MinWidth(260).Gap(0).Children(func() {
		ui.Row(c).Height(38).Padding(4, 10).Gap(2).AlignItems(ui.Center).Background(c.Theme().Surface).Children(func() {
			for _, view := range []struct{ label, name, icon, hint string }{{"等轴测", "iso", "box", "等轴测视图"}, {"前", "front", "view-front", "前视图"}, {"顶", "top", "view-top", "顶视图"}, {"右", "right", "view-right", "右视图"}} {
				if iconButton(c, view.label, view.icon, false).Tooltip(view.hint).Clicked() {
					a.scene.SetView(view.name)
				}
			}
			ui.Spacer(c)
			if iconButton(c, "适配", "scan", false).Tooltip("让模型完整显示").Clicked() {
				a.scene.Fit()
			}
			iconButton(c, "显示", "sliders-horizontal", false).Menu(func(m *ui.Menu) {
				for _, view := range []struct{ label, name string }{{"后视图", "back"}, {"左视图", "left"}, {"底视图", "bottom"}} {
					if m.Item(view.label).Chosen() {
						a.scene.SetView(view.name)
					}
				}
				if m.Item(checkLabel(a.edges, "边线")).Chosen() {
					a.edges = !a.edges
				}
				if m.Item(checkLabel(a.grid, "网格")).Chosen() {
					a.grid = !a.grid
				}
				if m.Item(checkLabel(a.axes, "坐标轴")).Chosen() {
					a.axes = !a.axes
				}
				if m.Item(checkLabel(a.perspective, "透视投影")).Chosen() {
					a.perspective = !a.perspective
				}
			}).Height(30).Border(0, ui.Transparent).Background(ui.Transparent)
		})
		ui.Box(c).Grow(1).MinHeight(0).Children(func() { a.scene.View(c) })
		ui.Row(c).Height(26).Padding(0, 12).AlignItems(ui.Center).Gap(12).Children(func() {
			ui.Text(c, "拖动旋转 · 中键平移 · 滚轮缩放").FontSize(10).TextColor(c.Theme().TextMuted).Grow(1)
			projection := "透视"
			if !a.perspective {
				projection = "正交"
			}
			ui.Text(c, projection+" · mm").FontSize(10).TextColor(c.Theme().TextMuted)
		})
	})
}

func checkLabel(on bool, label string) string {
	if on {
		return "✓ " + label
	}
	return "   " + label
}

func (a *App) inspector(c *ui.Context) {
	ui.Tabs(c, &a.detailTab, "特征", "信息").Height(38)
	sectionRule(c)
	ui.Scroll(c).Key(a.detailTab).Grow(1).MinHeight(0).Transition(appear).Children(func() {
		ui.Column(c).Padding(12).Gap(12).Children(func() {
			if a.detailTab == 1 {
				a.modelInfo(c)
				return
			}
			features := rows(a.document["features"])
			ui.Row(c).Height(24).AlignItems(ui.Center).Children(func() {
				sectionTitle(c, "模型特征")
				ui.Spacer(c)
				ui.Text(c, fmt.Sprint(len(features))).FontSize(11).TextColor(c.Theme().TextMuted)
			})
			ui.Column(c).Gap(2).Children(func() {
				for index, feature := range features {
					name := text(feature["name"])
					if name == "" {
						name = text(feature["kind"])
					}
					if name == "" {
						name = fmt.Sprintf("特征 %d", index+1)
					}
					icon := "box"
					if text(feature["op"]) == "cut" || strings.Contains(text(feature["kind"]), "hole") {
						icon = "circle-dashed"
					}
					button := ui.ButtonBase(c).Key(index).Label(name).FillWidth().Height(32).Padding(0, 8).Gap(9).Justify(ui.Start).Radius(3).Transition(controlTransition)
					color := c.Theme().TextMuted
					if a.selectedFeature == index {
						button.Background(c.Theme().Selection)
						color = c.Theme().Accent
					} else if button.Hovered() {
						button.Background(c.Theme().SurfaceHover)
					}
					button.Children(func() {
						glyph(c, icon, color)
						ui.Text(c, name).FontSize(12).SingleLine().Grow(1)
						if feature["enabled"] == false {
							ui.Text(c, "已抑制").FontSize(10).TextColor(c.Theme().TextMuted)
						}
					})
					if button.Clicked() {
						a.selectFeature(index)
					}
				}
			})
			if a.selectedFeature < 0 || a.selectedFeature >= len(features) {
				ui.Text(c, "选择特征以编辑参数").FontSize(11).TextColor(c.Theme().TextMuted)
				return
			}
			ui.Column(c).Key(fmt.Sprintf("parameters-%d", a.selectedFeature)).Gap(12).Transition(appear).Children(func() {
				sectionRule(c)
				sectionTitle(c, "特征参数")
				ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
					ui.Text(c, "名称").Width(92).FontSize(11).TextColor(c.Theme().TextMuted)
					ui.TextInput(c, &a.featureName).Grow(1).MinWidth(0).Height(28).FontSize(12).Label("特征名称").Disabled(a.busy)
				})
				keys := make([]string, 0, len(a.featureValues))
				for key := range a.featureValues {
					keys = append(keys, key)
				}
				parameterOrder := map[string]int{"length": 1, "width": 2, "height": 3, "thickness": 4, "diameter": 5, "radius": 6, "depth": 7, "count": 8, "angle": 9, "center": 10, "point": 11, "axis": 12, "base": 13, "corner": 14, "corner_mode": 15}
				sort.SliceStable(keys, func(i, j int) bool {
					left, right := parameterOrder[keys[i]], parameterOrder[keys[j]]
					if left == 0 {
						left = 100
					}
					if right == 0 {
						right = 100
					}
					if left == right {
						return keys[i] < keys[j]
					}
					return left < right
				})
				if editable, ok := features[a.selectedFeature]["editable_parameters"].([]any); ok {
					keys = nil
					for _, key := range editable {
						if _, ok := a.featureValues[text(key)]; ok {
							keys = append(keys, text(key))
						}
					}
				}
				ui.Column(c).Gap(8).Children(func() {
					for _, key := range keys {
						value := a.featureValues[key]
						ui.Row(c).Key(key).Gap(8).AlignItems(ui.Center).MinHeight(28).Children(func() {
							ui.Text(c, parameterLabel(key)).Width(92).FontSize(11).TextColor(c.Theme().TextMuted).Tooltip(key)
							switch v := value.(type) {
							case float64:
								ui.NumberInput(c, &v, -1000000, 1000000, 0.1).Grow(1).MinWidth(0).Height(28).FontSize(12).Label(key).Disabled(a.busy)
								a.featureValues[key] = v
							case bool:
								ui.Checkbox(c, &v, "").Label(key).Disabled(a.busy)
								a.featureValues[key] = v
							case string:
								switch {
								case key == "axis" && (v == "X" || v == "Y" || v == "Z"):
									ui.Select(c, &v, []string{"X", "Y", "Z"}).Grow(1).MinWidth(0).Height(28).Label(key).Disabled(a.busy)
								case key == "corner_mode" && (v == "fillet" || v == "chamfer"):
									label := "圆角"
									if v == "chamfer" {
										label = "倒角"
									}
									ui.Select(c, &label, []string{"圆角", "倒角"}).Grow(1).MinWidth(0).Height(28).Label(key).Disabled(a.busy)
									v = "fillet"
									if label == "倒角" {
										v = "chamfer"
									}
								default:
									ui.TextInput(c, &v).Grow(1).MinWidth(0).Height(28).FontSize(12).Label(key).Disabled(a.busy)
								}
								a.featureValues[key] = v
							case []any:
								draft := a.arrayDrafts[key]
								ui.TextInput(c, &draft).Grow(1).MinWidth(0).Height(28).FontSize(12).Label(key).Disabled(a.busy)
								a.arrayDrafts[key] = draft
							default:
								ui.Text(c, pretty(v)).Grow(1).FontSize(11).TextColor(c.Theme().TextMuted)
							}
						})
					}
				})
				ui.Row(c).Gap(8).Padding(4, 0).Children(func() {
					if toolButton(c, "应用参数", "check", false).Height(30).Border(1, c.Theme().SurfacePressed).Disabled(a.busy).Clicked() {
						a.updateFeature("", 0, nil)
					}
					ui.Spacer(c)
					iconButton(c, "更多", "ellipsis", false).Menu(func(m *ui.Menu) {
						if a.selectedFeature > 0 && m.Item("上移").Chosen() {
							a.updateFeature("move", -1, nil)
						}
						if a.selectedFeature < len(features)-1 && m.Item("下移").Chosen() {
							a.updateFeature("move", 1, nil)
						}
						enabled := features[a.selectedFeature]["enabled"] != false
						label := "抑制特征"
						if !enabled {
							label = "启用特征"
						}
						if m.Item(label).Chosen() {
							enabled = !enabled
							a.updateFeature("", 0, &enabled)
						}
						if m.Item("删除特征").Chosen() {
							a.deleteFeature()
						}
					}).Height(30).Disabled(a.busy)
				})
			})
		})
	})
}
func (a *App) modelInfo(c *ui.Context) {
	ui.Text(c, acceptance(a.document)).FontSize(14).FontWeight(600)
	stats := object(a.document["stats"])
	for _, item := range []struct{ label, value string }{
		{"工程", text(a.document["name"])},
		{"文件", filepath.Base(text(a.document["current_path"]))},
		{"模型版本", fmt.Sprintf("%d", a.generation)},
		{"几何修订", fmt.Sprintf("%.0f", number(a.document["cad_revision"]))},
		{"外形尺寸", dimensionLabel(stats["size"])},
		{"体积", fmt.Sprintf("%.2f mm³", number(stats["volume"]))},
		{"三角面数", fmt.Sprintf("%.0f", number(stats["triangles"]))},
		{"几何检查", statusLabel(text(a.document["geometric_status"]))},
		{"视觉复核", statusLabel(text(a.document["visual_status"]))},
	} {
		ui.Text(c, item.label).FontSize(11).TextColor(c.Theme().TextMuted)
		ui.Text(c, item.value).Selectable()
	}
	if ui.Button(c, "推送到 CATIA").FillWidth().Disabled(a.busy || len(rows(a.document["features"])) == 0).Clicked() {
		a.invoke("推送 CATIA", "active_model_push_catia", Object{}, nil)
	}
}

func (a *App) statusbar(c *ui.Context) {
	ui.Row(c).Height(24).Padding(0, 12).Gap(9).AlignItems(ui.Center).Background(c.Theme().Surface).Border(1, c.Theme().Border).Children(func() {
		if a.busy {
			ui.Spinner(c).Size(14, 14)
		}
		status := a.status
		color := c.Theme().TextMuted
		if a.failure != "" {
			status = a.failure
			color = c.Theme().Danger
		}
		ui.Text(c, status).FontSize(11).TextColor(color).SingleLine().Grow(1).Tooltip(status)
		ui.Text(c, acceptance(a.document)).FontSize(11).TextColor(c.Theme().TextMuted)
		if a.turnStatus != "" {
			ui.Text(c, statusLabel(a.turnStatus)).FontSize(11).TextColor(c.Theme().TextMuted)
		}
	})
}

func (a *App) logPane(c *ui.Context) {
	ui.Column(c).Height(180).Padding(12).Gap(8).Background(c.Theme().Surface).Border(1, c.Theme().Border).Children(func() {
		ui.Row(c).AlignItems(ui.Center).Children(func() {
			ui.Text(c, "运行日志").Bold().Grow(1)
			if ui.Button(c, "清空日志").Clicked() {
				a.logs = nil
			}
			if ui.Button(c, "收起").Clicked() {
				a.showLogs = false
			}
		})
		ui.Scroll(c).Grow(1).Children(func() {
			ui.Column(c).Gap(4).Children(func() {
				for _, line := range a.logs {
					ui.Text(c, line).FontSize(11).Selectable()
				}
			})
		})
	})
}
