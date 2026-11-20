package workbench

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"image/png"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"
)

func TestUpdateIconsAreEmbedded(t *testing.T) {
	for _, name := range []string{updateCheckIcon, updateDownloadIcon, updateRestartIcon} {
		if icons()[name] == nil {
			t.Errorf("update icon %q is missing", name)
		}
	}
}

func providerSettingsFixture() Object {
	return Object{
		"llm": Object{
			"enabled": true, "active_provider_id": "first",
			"providers": []any{
				Object{"id": "first", "name": "First", "base_url": "http://first/v1", "model": "model-one", "api_key": "key-one", "reasoning_effort": "low", "vendor_option": "keep-first"},
				Object{"id": "second", "name": "Second", "base_url": "http://second/v1", "model": "model-two", "api_key": "key-two", "reasoning_effort": "high", "vendor_option": "keep-second"},
			},
			"agent_stage_overrides": Object{
				"perception":   Object{"provider_id": "first", "reasoning_effort": "low", "model": "vision-model", "future_stage_option": true},
				"review":       Object{"provider_id": "second", "reasoning_effort": "max", "model": "review-model"},
				"future_stage": Object{"enabled": true},
			},
			"vendor_root": "keep-llm",
		},
		"build":       Object{"time_budget": float64(31), "max_faces": float64(61000), "future_build": "keep-build"},
		"viewer":      Object{"max_faces": float64(6200), "future_viewer": "keep-viewer"},
		"online":      Object{"timeout": float64(9), "future_online": "keep-online"},
		"future_root": Object{"keep": true},
	}
}

func TestSettingsCancelRestoresOpeningPreview(t *testing.T) {
	a, tt, _ := newUITest(t)
	a.settings = providerSettingsFixture()
	a.edges, a.grid, a.axes, a.perspective = false, true, false, true
	a.online, a.showThoughts, a.theme = true, false, "深色"
	a.openSettings()
	a.edges, a.grid, a.axes, a.perspective = true, false, true, false
	a.online, a.showThoughts, a.theme = false, true, "浅色"
	a.providerModel = "unsaved-model"
	tt.Frame()
	click(t, tt, "取消")
	if a.showSettings || a.settingsDraft != nil || a.edges || !a.grid || a.axes || !a.perspective || !a.online || a.showThoughts || a.theme != "深色" {
		t.Fatal("cancel did not restore the settings at dialog entry")
	}
	a.openSettings()
	if a.providerModel != "model-one" {
		t.Fatal("discarded provider edit survived reopen")
	}
}

func TestSettingsSaveFailureCanStillBeCancelled(t *testing.T) {
	a, tt, queue := newUITest(t)
	a.settings = providerSettingsFixture()
	a.theme, a.online = "浅色", false
	a.service = testService{func(string, Object) (any, error) { return nil, errors.New("保存失败") }}
	a.openSettings()
	a.theme, a.online = "深色", true
	a.saveSettings()
	drain(t, a, tt, queue)
	if !a.showSettings || a.failure != "保存失败" {
		t.Fatal("save failure closed dialog or lost error")
	}
	a.cancelSettings()
	if a.theme != "浅色" || a.online {
		t.Fatal("failed save replaced cancellation baseline")
	}
}

func TestSettingsDismissalRestoresPreviewAndDefersDuringSave(t *testing.T) {
	a := New(nil, &testScene{})
	a.settings = providerSettingsFixture()
	a.online = false
	a.openSettings()
	a.online = true
	a.showSettings = false
	a.busy = true
	a.finishSettingsFrame()
	if !a.showSettings || !a.online {
		t.Fatal("an in-flight save was dismissed")
	}
	a.busy = false
	a.showSettings = false
	a.finishSettingsFrame()
	if a.online || a.settingsDraft != nil {
		t.Fatal("dismissed preview was not restored")
	}
}

func TestSettingsSaveCommitsPreviewAndPreservesExtensions(t *testing.T) {
	a, tt, queue := newUITest(t)
	a.settings = providerSettingsFixture()
	var saved Object
	a.service = testService{func(method string, params Object) (any, error) {
		if method != "native_settings_save" {
			return nil, errors.New(method)
		}
		saved = clone(object(params["settings"]))
		return saved, nil
	}}
	a.openSettings()
	a.theme, a.online, a.showThoughts = "深色", true, true
	a.providerModel = "updated-model"
	object(a.settingsDraft["build"])["time_budget"] = float64(75)
	object(a.settingsDraft["viewer"])["max_faces"] = float64(14000)
	a.saveSettings()
	drain(t, a, tt, queue)
	if a.showSettings || a.settingsDraft != nil || a.theme != "深色" || !a.online || !a.showThoughts {
		t.Fatal("successful save lost preview settings")
	}
	llm := object(saved["llm"])
	if rows(llm["providers"])[0]["model"] != "updated-model" || rows(llm["providers"])[0]["vendor_option"] != "keep-first" || llm["vendor_root"] != "keep-llm" {
		t.Fatal("provider round trip lost edited or extension fields")
	}
	if object(object(llm["agent_stage_overrides"])["perception"])["future_stage_option"] != true || object(object(llm["agent_stage_overrides"])["review"])["model"] != "review-model" || object(saved["future_root"])["keep"] != true {
		t.Fatal("stage overrides or future fields were dropped")
	}
	if number(object(saved["build"])["time_budget"]) != 75 || object(saved["build"])["future_build"] != "keep-build" || number(object(saved["viewer"])["max_faces"]) != 14000 {
		t.Fatal("geometry values did not survive save")
	}
}

func TestDeleteProviderPreservesOtherProfilesAndStageDetails(t *testing.T) {
	a := New(nil, &testScene{})
	a.settings = providerSettingsFixture()
	a.openSettings()
	if err := a.removeProviderDraft("first"); err != nil {
		t.Fatal(err)
	}
	llm := object(a.settingsDraft["llm"])
	providers := rows(llm["providers"])
	if len(providers) != 1 || providers[0]["id"] != "second" || a.profile != "second" || a.providerModel != "model-two" || providers[0]["vendor_option"] != "keep-second" {
		t.Fatal("provider deletion selected or changed the wrong profile")
	}
	stages := object(llm["agent_stage_overrides"])
	if object(stages["perception"])["provider_id"] != "" || object(stages["perception"])["model"] != "vision-model" || object(stages["perception"])["future_stage_option"] != true || object(stages["review"])["provider_id"] != "second" {
		t.Fatal("provider deletion lost unrelated stage settings")
	}
	if len(rows(object(a.settings["llm"])["providers"])) != 2 {
		t.Fatal("draft deletion changed saved settings")
	}
	a.cancelSettings()
	a.openSettings()
	if a.profile != "first" {
		t.Fatal("cancelled deletion was retained")
	}
}

func TestDeleteLastProviderDoesNotRecreateEmptyProfile(t *testing.T) {
	a := New(nil, &testScene{})
	a.settings = providerSettingsFixture()
	a.openSettings()
	if err := a.removeProviderDraft("first"); err != nil {
		t.Fatal(err)
	}
	if err := a.removeProviderDraft("second"); err != nil {
		t.Fatal(err)
	}
	a.storeProfile()
	llm := object(a.settingsDraft["llm"])
	if len(rows(llm["providers"])) != 0 || llm["active_provider_id"] != "" || llm["enabled"] != false || a.providerURL != "" || a.providerKey != "" || a.providerModel != "" {
		t.Fatal("deleting the last provider recreated or retained credentials")
	}
}

func TestProviderDeleteRejectsMalformedStageDraftWithoutMutation(t *testing.T) {
	a := New(nil, &testScene{})
	a.settings = providerSettingsFixture()
	a.openSettings()
	before := pretty(a.settingsDraft)
	a.advancedJSON = "[invalid"
	if err := a.removeProviderDraft("first"); err == nil {
		t.Fatal("invalid stage draft accepted")
	}
	if pretty(a.settingsDraft) != before {
		t.Fatal("failed deletion changed the settings draft")
	}
}

func TestSettingsGeometryControlsAndStageExpansion(t *testing.T) {
	a := New(nil, &testScene{})
	a.settings = providerSettingsFixture()
	a.openSettings()
	tt := ui.NewTester(func(c *ui.Context) {
		ui.Column(c).Fill().Children(func() { a.geometrySettings(c) })
	}, 700, 600)
	for _, label := range []string{"几何计算时限（秒）", "最大面片数", "预览面片上限", "联网搜索超时（秒）"} {
		if _, ok := tt.Find(label); !ok {
			t.Fatalf("missing numeric control %s", label)
		}
	}
	if number(object(a.settingsDraft["viewer"])["max_faces"]) != 6200 || object(a.settingsDraft["build"])["future_build"] != "keep-build" {
		t.Fatal("rendering geometry settings changed existing values")
	}
	stageTester := ui.NewTester(func(c *ui.Context) {
		ui.Column(c).Fill().Children(func() { a.providerSettings(c) })
	}, 760, 1100)
	click(t, stageTester, "分阶段模型配置")
	if !a.stageExpanded || a.showThoughts {
		t.Fatal("stage expansion changed the thought visibility preference")
	}
}

func TestRuntimeSettingsDistinguishesVerificationFromConnection(t *testing.T) {
	a := New(nil, &testScene{})
	a.runtime = Object{
		"occt":  Object{"available": true, "verified": true, "loaded": false},
		"dsh":   Object{"installed": false, "verified": false, "bundleRequired": true, "bundled": false},
		"catia": Object{"registered": true, "connected": nil, "connectionChecked": false},
	}
	tt := ui.NewTester(func(c *ui.Context) {
		ui.Column(c).Fill().Children(func() { a.runtimeSettings(c) })
	}, 700, 650)
	for _, label := range []string{"OCCT 几何内核", "已校验", "缺少安装资源", "COM 已注册"} {
		if !tt.HasText(label) {
			t.Fatalf("missing readiness state %s", label)
		}
	}
	if tt.HasText("已连接") || tt.HasText("已安装") {
		t.Fatal("registration or missing runtime was shown as ready")
	}
	if tt.HasText(pretty(a.runtime)) {
		t.Fatal("raw runtime JSON should start collapsed")
	}
	click(t, tt, "诊断详情")
	if !tt.HasText(pretty(a.runtime)) {
		t.Fatal("diagnostic detail expansion is unavailable")
	}
}

func TestStageSettingsEditsRetainExtensionFields(t *testing.T) {
	a := New(nil, &testScene{})
	a.settings = providerSettingsFixture()
	a.openSettings()
	tt := ui.NewTester(func(c *ui.Context) {
		ui.Column(c).Fill().Children(func() { a.stageSettings(c) })
	}, 640, 750)
	click(t, tt, "识图模型")
	tt.Key(ui.Cmd, ui.KeyA)
	tt.Type("replacement-vision")
	tt.Key(0, ui.KeyTab)
	click(t, tt, "识图服务")
	click(t, tt, "Second (2)")
	var stages Object
	if err := json.Unmarshal([]byte(a.advancedJSON), &stages); err != nil {
		t.Fatal(err)
	}
	perception := object(stages["perception"])
	if perception["model"] != "replacement-vision" || perception["provider_id"] != "second" || perception["future_stage_option"] != true || perception["reasoning_effort"] != "low" {
		t.Fatalf("stage edit lost data: %v", perception)
	}
	if object(stages["future_stage"])["enabled"] != true || object(stages["review"])["model"] != "review-model" {
		t.Fatal("editing one stage changed other stage fields")
	}
}

func TestSettingsModalLayoutsAndEscape(t *testing.T) {
	for _, size := range [][2]int{{1440, 900}, {980, 640}} {
		for _, theme := range []string{"浅色", "深色"} {
			t.Run(fmt.Sprintf("%dx%d-%s", size[0], size[1], theme), func(t *testing.T) {
				a := New(nil, &testScene{})
				a.settings = providerSettingsFixture()
				a.theme = theme
				a.openSettings()
				tt := ui.NewTester(a.View, size[0], size[1])
				for tab, label := range []string{"模型服务", "显示与行为", "几何计算", "运行环境"} {
					click(t, tt, label)
					if a.settingsTab != tab {
						t.Fatalf("navigation %s did not open category %d", label, tab)
					}
					modal, ok := tt.Find("设置窗口")
					if !ok || modal.W != 800 || modal.H != min(float32(600), float32(size[1])-48) {
						t.Fatalf("unexpected settings dimensions: %v", modal)
					}
					for _, label := range []string{"关闭设置", "取消", "保存设置"} {
						bounds, ok := tt.Find(label)
						if !ok || bounds.X < 0 || bounds.Y < 0 || bounds.X+bounds.W > float32(size[0]) || bounds.Y+bounds.H > float32(size[1]) {
							t.Errorf("tab %d control %s outside window: %v", tab, label, bounds)
						}
					}
					if dir := os.Getenv("FORMRECALL_TEST_ARTIFACTS"); dir != "" {
						if err := os.MkdirAll(dir, 0755); err != nil {
							t.Fatal(err)
						}
						path := filepath.Join(dir, fmt.Sprintf("settings-%d-%dx%d-%s.png", tab, size[0], size[1], theme))
						file, err := os.Create(path)
						if err != nil {
							t.Fatal(err)
						}
						if err = png.Encode(file, tt.Image()); err != nil {
							t.Fatal(err)
						}
						if err = file.Close(); err != nil {
							t.Fatal(err)
						}
					}
				}
				a.online = !a.online
				tt.Key(0, ui.KeyEscape)
				tt.Frame()
				if a.showSettings || a.settingsDraft != nil || a.online {
					t.Fatal("Escape did not dismiss and restore the settings preview")
				}
			})
		}
	}
}

func TestSettingsNavigationPreservesEditsThroughSaveAndCancel(t *testing.T) {
	a, tt, queue := newUITest(t)
	a.settings = providerSettingsFixture()
	var saved Object
	a.service = testService{func(method string, params Object) (any, error) {
		if method != "native_settings_save" {
			return nil, errors.New(method)
		}
		saved = clone(object(params["settings"]))
		return saved, nil
	}}
	a.openSettings()
	tt.Frame()
	click(t, tt, "模型名称")
	tt.Key(ui.Cmd, ui.KeyA)
	tt.Type("edited-model")
	click(t, tt, "显示与行为")
	click(t, tt, "允许在线查询")
	click(t, tt, "几何计算")
	budget, ok := tt.Find("几何计算时限（秒）")
	if !ok {
		t.Fatal("missing geometry time budget")
	}
	// The field heading precedes the native NumberInput by a six-pixel gap.
	// Enter text at its leading edge, clear of the increment buttons.
	tt.ClickAt(budget.X+8, budget.Y+budget.H+6+15)
	tt.Key(ui.Cmd, ui.KeyA)
	tt.Type("88")
	tt.Key(0, ui.KeyTab)
	click(t, tt, "运行环境")
	click(t, tt, "模型服务")
	if a.providerModel != "edited-model" || !a.online || number(object(a.settingsDraft["build"])["time_budget"]) != 88 {
		t.Fatalf("category navigation lost unsaved settings: model=%q online=%t budget=%v", a.providerModel, a.online, object(a.settingsDraft["build"])["time_budget"])
	}
	click(t, tt, "保存设置")
	drain(t, a, tt, queue)
	if a.showSettings || rows(object(saved["llm"])["providers"])[0]["model"] != "edited-model" || object(saved["online"])["enabled"] != true || number(object(saved["build"])["time_budget"]) != 88 {
		t.Fatalf("save did not commit edits across categories: %v", saved)
	}
	if object(saved["build"])["future_build"] != "keep-build" || object(object(object(saved["llm"])["agent_stage_overrides"])["perception"])["future_stage_option"] != true {
		t.Fatal("category save dropped extension fields")
	}
	a.openSettings()
	tt.Frame()
	click(t, tt, "模型名称")
	tt.Key(ui.Cmd, ui.KeyA)
	tt.Type("discard-model")
	click(t, tt, "显示与行为")
	click(t, tt, "允许在线查询")
	click(t, tt, "取消")
	a.openSettings()
	tt.Frame()
	click(t, tt, "模型服务")
	if a.providerModel != "edited-model" || !a.online || number(object(a.settingsDraft["build"])["time_budget"]) != 88 {
		t.Fatal("cancel did not restore the last saved settings")
	}
}

func TestSettingsSmallWindowScrollKeepsNavigationAndActions(t *testing.T) {
	a := New(nil, &testScene{})
	a.settings = providerSettingsFixture()
	a.openSettings()
	tt := ui.NewTester(a.View, 980, 640)
	click(t, tt, "分阶段模型配置")
	content, ok := tt.Find("设置内容")
	if !ok {
		t.Fatal("missing settings content scroll region")
	}
	saveBefore, _ := tt.Find("保存设置")
	navBefore, _ := tt.Find("显示与行为")
	tt.Scroll(content.X+content.W/2, content.Y+content.H/2, 0, 1000)
	tt.Advance(250 * time.Millisecond)
	click(t, tt, "复核模型")
	tt.Key(ui.Cmd, ui.KeyA)
	tt.Type("scrolled-review-model")
	tt.Key(0, ui.KeyTab)
	saveAfter, _ := tt.Find("保存设置")
	navAfter, _ := tt.Find("显示与行为")
	if saveAfter != saveBefore || navAfter != navBefore {
		t.Fatal("scrolling the form moved fixed navigation or footer")
	}
	var stages Object
	if err := json.Unmarshal([]byte(a.advancedJSON), &stages); err != nil {
		t.Fatal(err)
	}
	if object(stages["review"])["model"] != "scrolled-review-model" {
		t.Fatal("last stage model was not editable after scrolling")
	}
	if dir := os.Getenv("FORMRECALL_TEST_ARTIFACTS"); dir != "" {
		if err := os.MkdirAll(dir, 0755); err != nil {
			t.Fatal(err)
		}
		file, err := os.Create(filepath.Join(dir, "settings-expanded-980x640.png"))
		if err != nil {
			t.Fatal(err)
		}
		if err = png.Encode(file, tt.Image()); err != nil {
			t.Fatal(err)
		}
		if err = file.Close(); err != nil {
			t.Fatal(err)
		}
	}
	click(t, tt, "显示与行为")
	click(t, tt, "模型服务")
	click(t, tt, "配置名称")
	click(t, tt, "取消")
	if a.showSettings || a.settingsDraft != nil {
		t.Fatal("cancel was inaccessible after scrolling")
	}
}

func TestSettingsCategoryTransitionRespectsReducedMotion(t *testing.T) {
	for _, reduced := range []bool{false, true} {
		t.Run(fmt.Sprintf("reduced-%t", reduced), func(t *testing.T) {
			a := New(nil, &testScene{})
			a.settings = providerSettingsFixture()
			a.openSettings()
			tt := ui.NewTester(func(c *ui.Context) { a.settingsView(c) }, 980, 640)
			tt.SetPreferences(ui.Preferences{ReduceMotion: reduced})
			tt.Advance(time.Second)
			if err := tt.Click("几何计算"); err != nil {
				t.Fatal(err)
			}
			if a.settingsTab != 2 {
				t.Fatal("category did not change")
			}
			if tt.FramePending() == reduced {
				t.Fatalf("category transition pending=%t with reduced motion=%t", tt.FramePending(), reduced)
			}
			tt.Advance(160 * time.Millisecond)
			if tt.FramePending() {
				t.Fatal("category transition did not settle within 160 ms")
			}
			click(t, tt, "显示与行为")
			click(t, tt, "允许在线查询")
			if !a.online {
				t.Fatal("category controls were unavailable after transition")
			}
		})
	}
}

func TestSettingsModalEnterExitAndReducedMotion(t *testing.T) {
	for _, reduced := range []bool{false, true} {
		t.Run(fmt.Sprintf("reduced-%t", reduced), func(t *testing.T) {
			a := New(nil, &testScene{})
			a.settings = providerSettingsFixture()
			tt := ui.NewTester(func(c *ui.Context) {
				c.SetTheme(workbenchTheme(false))
				ui.Box(c).Fill().Background(ui.Hex("#436880"))
				a.settingsView(c)
			}, 980, 640)
			tt.SetPreferences(ui.Preferences{ReduceMotion: reduced})
			background := bytes.Clone(tt.Image().Pix)
			a.openSettings()
			tt.Frame()
			if tt.FramePending() == reduced {
				t.Fatalf("modal opening pending=%t with reduced motion=%t", tt.FramePending(), reduced)
			}
			tt.Advance(80 * time.Millisecond)
			opening := bytes.Clone(tt.Image().Pix)
			tt.Advance(100 * time.Millisecond)
			if bytes.Equal(opening, tt.Image().Pix) != reduced {
				t.Fatal("modal entrance did not match the system motion preference")
			}
			a.online = !a.online
			tt.Key(0, ui.KeyEscape)
			if a.showSettings || a.settingsDraft != nil || a.online {
				t.Fatal("Escape failed to discard the settings preview")
			}
			if bytes.Equal(background, tt.Image().Pix) != reduced {
				t.Fatal("modal exit did not retain its rendered panel until the fade completed")
			}
			tt.Advance(180 * time.Millisecond)
			if tt.FramePending() || !bytes.Equal(background, tt.Image().Pix) {
				t.Fatal("modal exit left a frame request or residual overlay")
			}
		})
	}
}

func TestSettingsAsyncModelFetchKeepsDraftUntilCompletion(t *testing.T) {
	a, tt, queue := newUITest(t)
	a.settings = providerSettingsFixture()
	release := make(chan struct{})
	a.service = testService{func(method string, params Object) (any, error) {
		if method != "native_provider_fetch_models" {
			return nil, errors.New(method)
		}
		<-release
		return Object{"models": []any{"fetched-one", "fetched-two"}}, nil
	}}
	a.openSettings()
	tt.Frame()
	click(t, tt, "获取模型")
	if !a.busy {
		t.Fatal("model lookup did not retain its pending state")
	}
	tt.Key(0, ui.KeyEscape)
	tt.Frame()
	if !a.showSettings || a.settingsDraft == nil {
		t.Fatal("pending model lookup allowed its draft to be discarded")
	}
	click(t, tt, "取消")
	if !a.showSettings {
		t.Fatal("cancel button remained active during the request")
	}
	close(release)
	drain(t, a, tt, queue)
	if len(a.modelChoices) != 2 || a.modelChoices[0] != "fetched-one" || a.profile != "first" {
		t.Fatal("model lookup result did not apply to the original provider")
	}
	a.cancelSettings()
	if a.modelChoices != nil {
		t.Fatal("discarded lookup results survived cancellation")
	}
}
