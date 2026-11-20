package workbench

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image/png"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/engine"
	"github.com/egoist/mygo/ui"
)

type testScene struct {
	path, view string
	fits       int
}

func (s *testScene) View(c *ui.Context) {
	ui.Box(c).Fill().Label("模型视口").Background(ui.Hex("#e5edf1"))
}
func (s *testScene) Load(path string) error                  { s.path = path; return nil }
func (s *testScene) SetView(view string)                     { s.view = view }
func (s *testScene) Fit()                                    { s.fits++ }
func (s *testScene) SetOptions(bool, bool, bool, bool, bool) {}
func (s *testScene) Close()                                  {}

type testService struct {
	handler func(string, Object) (any, error)
}

func (s testService) Call(_ context.Context, method string, params any, result any) error {
	value, err := s.handler(method, object(params))
	if err != nil {
		return err
	}
	if result == nil {
		return nil
	}
	data, _ := json.Marshal(value)
	return json.Unmarshal(data, result)
}

func sampleDocument(generation int) Object {
	return Object{"name": "底板", "model_generation": float64(generation), "revision": float64(generation), "cad_revision": float64(4), "geometry_fingerprint": "mesh-current", "mesh_path": "model.glb",
		"features": []any{Object{"name": "底板拉伸", "kind": "box", "enabled": true, "params": Object{"length": float64(100), "width": float64(80), "height": float64(10), "center": []any{float64(0), float64(0), float64(0)}}}},
		"stats":    Object{"size": []any{float64(100), float64(80), float64(10)}, "triangles": float64(12), "volume": float64(80000)}}
}

func sampleRecord(generation int) Object {
	finish := Object{"revision": float64(4), "geometry_fingerprint": "mesh-current", "accepted": true, "finish_state": "accepted"}
	return Object{"generation": float64(generation), "document": sampleDocument(generation), "acceptance_projection": Object{
		"active_generation": float64(generation), "revision": float64(4), "geometry_fingerprint": "mesh-current", "model": Object{
			"revision": float64(4), "geometry_fingerprint": "mesh-current", "acceptance_confirmed": true, "status": "accepted", "finish_status": "accepted", "finish_summary": finish}}}
}

func TestRestoreRequiresMatchingHostAcceptance(t *testing.T) {
	doc, generation := restoreSnapshot(sampleRecord(7), 6)
	if generation != 7 || acceptance(doc) != "已验收" {
		t.Fatalf("matching projection was not accepted: %v", doc)
	}
	for _, mutate := range []func(Object){
		func(r Object) { object(r["acceptance_projection"])["active_generation"] = float64(6) },
		func(r Object) { object(r["acceptance_projection"])["geometry_fingerprint"] = "stale" },
		func(r Object) { object(object(r["acceptance_projection"])["model"])["acceptance_confirmed"] = false },
		func(r Object) {
			object(object(object(r["acceptance_projection"])["model"])["finish_summary"])["revision"] = float64(3)
		},
		func(r Object) { delete(r, "acceptance_projection") },
	} {
		record := sampleRecord(7)
		mutate(record)
		doc, _ := restoreSnapshot(record, 6)
		if acceptance(doc) == "已验收" {
			t.Fatal("stale acceptance exposed")
		}
	}
	if doc, _ := restoreSnapshot(sampleRecord(5), 6); doc != nil {
		t.Fatal("late model replaced current generation")
	}
}

func newUITest(t *testing.T) (*App, *ui.Tester, chan func()) {
	t.Helper()
	queue := make(chan func(), 100)
	a := New(nil, &testScene{})
	a.dispatch = func(fn func()) { queue <- fn }
	a.ready = true
	a.status = "就绪"
	a.theme = "浅色"
	a.settings = Object{"llm": Object{"enabled": true, "providers": []any{}, "agent_stage_overrides": Object{}}, "viewer": Object{}}
	a.acceptSnapshot(sampleRecord(1), false)
	tt := ui.NewTester(a.View, 1440, 900)
	return a, tt, queue
}

func drain(t *testing.T, a *App, tt *ui.Tester, queue chan func()) {
	t.Helper()
	deadline := time.After(5 * time.Second)
	for a.busy {
		select {
		case fn := <-queue:
			fn()
			tt.Frame()
		case <-deadline:
			t.Fatal("UI command did not finish")
		}
	}
	tt.Advance(250 * time.Millisecond)
}

func click(t *testing.T, tt *ui.Tester, label string) {
	t.Helper()
	if err := tt.Click(label); err != nil {
		t.Fatal(err)
	}
	tt.Advance(250 * time.Millisecond)
}

func TestQuickThroughUIAndFailureRetainsModel(t *testing.T) {
	a, tt, queue := newUITest(t)
	fail := false
	a.service = testService{func(method string, params Object) (any, error) {
		switch method {
		case "quick_build":
			if fail {
				return nil, errors.New("几何构建失败")
			}
			if text(params["text"]) != "长方体 长100 宽80 高10" {
				t.Errorf("prompt lost: %v", params)
			}
			return sampleDocument(2), nil
		case "active_model_snapshot":
			return sampleRecord(2), nil
		}
		return nil, fmt.Errorf("unexpected %s", method)
	}}
	click(t, tt, "建模需求")
	tt.Type("长方体 长100 宽80 高10")
	click(t, tt, "生成模型")
	drain(t, a, tt, queue)
	if !a.dirty || a.generation != 2 || !tt.HasText("模型已生成，可以在特征面板调整参数。") {
		t.Fatal("Quick result not visible")
	}
	fail = true
	click(t, tt, "生成模型")
	drain(t, a, tt, queue)
	if a.failure != "几何构建失败" || a.generation != 2 || text(a.document["name"]) != "底板" {
		t.Fatal("failed build discarded current model")
	}
}

func TestFeatureEditAndToggleRPC(t *testing.T) {
	a, tt, queue := newUITest(t)
	var requests []Object
	a.service = testService{func(method string, params Object) (any, error) {
		if method == "active_model_update_feature" {
			requests = append(requests, params)
			return sampleDocument(2), nil
		}
		if method == "active_model_snapshot" {
			return sampleRecord(2), nil
		}
		return nil, nil
	}}
	click(t, tt, "底板拉伸")
	a.featureValues["length"] = float64(120)
	click(t, tt, "应用参数")
	drain(t, a, tt, queue)
	if number(object(requests[0]["params"])["length"]) != 120 || number(requests[0]["index"]) != 0 {
		t.Fatal("feature values not sent")
	}
	a.updateFeature("", 0, new(bool))
	drain(t, a, tt, queue)
	if requests[1]["action"] != "toggle" || requests[1]["enabled"] != false {
		t.Fatal("toggle action lost")
	}
}

func TestSettingsPreserveProviderAndUnknownValues(t *testing.T) {
	a, tt, queue := newUITest(t)
	a.settings = Object{"llm": Object{"enabled": true, "active_provider_id": "p1", "providers": []any{Object{"id": "p1", "name": "本地", "model": "old", "api_key": "private", "base_url": "http://localhost/v1", "future_field": "keep"}}, "agent_stage_overrides": Object{"review": Object{"model": "review-model"}}}, "viewer": Object{"max_faces": float64(6000)}, "future_root": Object{"keep": true}}
	var saved Object
	a.service = testService{func(method string, params Object) (any, error) {
		if method != "native_settings_save" {
			return nil, errors.New(method)
		}
		saved = clone(object(params["settings"]))
		return saved, nil
	}}
	click(t, tt, "设置")
	a.providerModel = "new-model"
	click(t, tt, "保存设置")
	drain(t, a, tt, queue)
	llm := object(saved["llm"])
	if llm["active_provider_id"] != "p1" || rows(llm["providers"])[0]["future_field"] != "keep" || llm["model"] != "new-model" || object(saved["future_root"])["keep"] != true || object(llm["agent_stage_overrides"])["review"] == nil {
		t.Fatal("settings preservation failed")
	}
}

func TestLateAgentEventsDoNotReplaceCurrentTurn(t *testing.T) {
	a := New(nil, &testScene{})
	a.turn = "current"
	a.acceptSnapshot(sampleRecord(3), false)
	data, _ := json.Marshal(Object{"event": "model.changed", "request_id": "old", "data": sampleDocument(4)})
	a.handleEvent(engine.Event{Event: "agent", Data: data})
	if a.generation != 3 {
		t.Fatal("old turn changed active model")
	}
	data, _ = json.Marshal(Object{"event": "agent.message", "request_id": "current", "data": Object{"text": "第一段"}})
	a.handleEvent(engine.Event{Data: data})
	data, _ = json.Marshal(Object{"event": "agent.message", "request_id": "current", "data": Object{"text": "第二段"}})
	a.handleEvent(engine.Event{Data: data})
	if len(a.messages) != 1 || a.messages[0].Text != "第一段第二段" {
		t.Fatal("stream chunks not merged")
	}
}

func TestAgentTextAndImageOnlyRequestsUseArray(t *testing.T) {
	for _, imageOnly := range []bool{false, true} {
		t.Run(fmt.Sprint(imageOnly), func(t *testing.T) {
			a, tt, queue := newUITest(t)
			a.mode = "Agent"
			a.prompt = "解释当前模型"
			if imageOnly {
				a.prompt = ""
				a.images = []string{"reference.png"}
			}
			called := false
			a.service = testService{func(method string, params Object) (any, error) {
				if method == "agent_send" {
					data, _ := json.Marshal(params)
					var request Object
					_ = json.Unmarshal(data, &request)
					if _, ok := request["images"].([]any); !ok {
						t.Error("images must be a JSON array")
					}
					called = true
					return Object{"reply": "这是当前模型的说明", "turn_status": "completed"}, nil
				}
				if method == "active_model_snapshot" {
					return sampleRecord(1), nil
				}
				return Object{}, nil
			}}
			tt.Frame()
			click(t, tt, "生成模型")
			drain(t, a, tt, queue)
			if !called || a.dirty {
				t.Fatal("Agent request or QA dirty state failed")
			}
		})
	}
}

func TestSaveRestoresAcceptanceAndNewResetsAgent(t *testing.T) {
	a, tt, queue := newUITest(t)
	var methods []string
	a.service = testService{func(method string, params Object) (any, error) {
		methods = append(methods, method)
		switch method {
		case "active_model_snapshot":
			return sampleRecord(2), nil
		case "active_model_save":
			return sampleDocument(2), nil
		case "active_model_new":
			doc := sampleDocument(3)
			doc["features"] = []any{}
			doc["mesh_path"] = ""
			return doc, nil
		}
		return Object{}, nil
	}}
	a.savePath("test.aimodel", nil)
	drain(t, a, tt, queue)
	if acceptance(a.document) != "已验收" || a.dirty {
		t.Fatal("saved model lost host acceptance")
	}
	a.newProject()
	drain(t, a, tt, queue)
	if len(methods) != 4 || methods[2] != "agent_reset" || methods[3] != "active_model_new" {
		t.Fatalf("new project sequence %v", methods)
	}
}

func TestHistoryRestoresEmbeddedAssistant(t *testing.T) {
	a := New(nil, &testScene{})
	a.restoreHistory(Object{"turns": []any{Object{"role": "user", "text": "做一个底板", "assistant": "底板已生成"}}})
	if len(a.messages) != 2 || a.messages[1].Role != "assistant" || a.messages[1].Text != "底板已生成" {
		t.Fatal("history was not restored")
	}
}

func TestInvalidArrayDoesNotSendFeatureUpdate(t *testing.T) {
	a := New(nil, &testScene{})
	a.ready = true
	a.acceptSnapshot(sampleRecord(1), false)
	a.selectFeature(0)
	a.arrayDrafts["center"] = "[1,"
	a.service = testService{func(string, Object) (any, error) { t.Error("invalid array sent to host"); return nil, nil }}
	a.updateFeature("", 0, nil)
	if a.failure == "" || a.busy {
		t.Fatal("invalid array was not rejected")
	}
}

func TestWorkbenchLayoutsAndControls(t *testing.T) {
	for _, size := range [][2]int{{1440, 900}, {1180, 760}, {980, 640}} {
		for _, theme := range []string{"浅色", "深色"} {
			t.Run(fmt.Sprintf("%dx%d-%s", size[0], size[1], theme), func(t *testing.T) {
				a := New(nil, &testScene{})
				a.ready = true
				a.status = "就绪"
				a.theme = theme
				a.acceptSnapshot(sampleRecord(1), false)
				tt := ui.NewTester(a.View, size[0], size[1])
				tt.Advance(250 * time.Millisecond)
				for _, label := range []string{"关闭窗口", "设置", "建模需求", "生成模型", "模型视口"} {
					r, ok := tt.Find(label)
					if !ok || r.X < 0 || r.Y < 0 || r.X+r.W > float32(size[0])+1 || r.Y+r.H > float32(size[1])+1 {
						t.Errorf("%s outside window: %v", label, r)
					}
				}
				click(t, tt, "前")
				if a.scene.(*testScene).view != "front" {
					t.Fatal("camera action missing")
				}
				click(t, tt, "适配")
				if a.scene.(*testScene).fits != 1 {
					t.Fatal("fit missing")
				}
				click(t, tt, "日志")
				if !tt.HasText("运行日志") {
					t.Fatal("log drawer missing")
				}
				if dir := os.Getenv("FORMRECALL_TEST_ARTIFACTS"); dir != "" {
					_ = os.MkdirAll(dir, 0755)
					file, err := os.Create(filepath.Join(dir, fmt.Sprintf("layout-%dx%d-%s.png", size[0], size[1], theme)))
					if err != nil {
						t.Fatal(err)
					}
					defer file.Close()
					if err = png.Encode(file, tt.Image()); err != nil {
						t.Fatal(err)
					}
				}
			})
		}
	}
}
