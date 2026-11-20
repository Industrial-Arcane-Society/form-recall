package workbench

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"time"

	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/branding"
	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/engine"
	"github.com/Industrial-Arcane-Society/form-recall/desktop/internal/update"
	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

type Service interface {
	Call(context.Context, string, any, any) error
}

// Scene is the interactive model viewport. Its implementation owns GPU resources.
type Scene interface {
	View(*ui.Context)
	Load(string) error
	SetView(string)
	Fit()
	SetOptions(bool, bool, bool, bool, bool)
	Close()
}

type Options struct {
	Engine                 engine.Options
	Screenshot, DemoPrompt string
	CapturePanel           string
	ExitAfter              time.Duration
	Width, Height          int
	Updater                *update.Client
	RestartUpdate          func(string)
	UpdateReady            func()
}

type App struct {
	win                                                                    *mygo.Window
	service                                                                Service
	scene                                                                  Scene
	options                                                                Options
	closed                                                                 atomic.Bool
	dispatch                                                               func(func())
	ready, busy, dirty, closing                                            bool
	status, failure, operation                                             string
	document, settings, runtime                                            Object
	generation                                                             uint64
	meshPath                                                               string
	leftWidth                                                              float32
	leftTab, detailTab                                                     int
	selectedFeature                                                        int
	featureName                                                            string
	featureValues                                                          Object
	arrayDrafts                                                            map[string]string
	prompt, mode, search, theme                                            string
	messages                                                               []Message
	chatScroll                                                             ui.ScrollState
	turn                                                                   string
	images                                                                 []string
	thumbnails                                                             map[string]ui.ImageSource
	parts, samples, cases                                                  []Object
	logs                                                                   []string
	showSettings, showLogs, showDetails, showThoughts                      bool
	stageExpanded                                                          bool
	edges, grid, axes, perspective                                         bool
	profile, providerName, providerURL, providerKey, providerModel, effort string
	providerEnabled, online, useCases                                      bool
	modelChoices                                                           []string
	settingsDraft                                                          Object
	settingsTab                                                            int
	advancedJSON                                                           string
	findings                                                               []Object
	findingRequest                                                         uint64
	turnStatus                                                             string
	softwareUpdate                                                         softwareUpdate
	updateFailure                                                          string
}

func New(service Service, scene Scene) *App {
	return &App{service: service, scene: scene, status: "正在连接建模引擎", document: Object{}, settings: Object{},
		runtime: Object{}, leftWidth: 286, selectedFeature: -1, featureValues: Object{}, arrayDrafts: map[string]string{},
		mode: "Quick", theme: "跟随系统", showDetails: true, edges: true, grid: true, axes: true, perspective: true,
		useCases: true, thumbnails: map[string]ui.ImageSource{}}
}

func Run(options Options, scene Scene) error {
	a := New(nil, scene)
	if err := branding.SetApplicationID(); err != nil {
		a.addLog(err.Error())
	}
	a.options = options
	updateContext, cancelUpdate := context.WithCancel(context.Background())
	defer cancelUpdate()
	a.softwareUpdate.ctx = updateContext
	if options.Updater != nil {
		a.updateFailure = options.Updater.LastError()
		a.softwareUpdate.preferences = update.LoadPreferences(options.Engine.DataDir, options.Updater.Channel())
	}
	started := make(chan *engine.Client, 1)
	launching := false
	mygo.App.WhenReady(func() {
		width, height := options.Width, options.Height
		if width == 0 {
			width = 1440
		}
		if height == 0 {
			height = 900
		}
		a.win = mygo.NewWindow(mygo.WindowOptions{Title: "FormRecall", Width: width, Height: height,
			MinWidth: 980, MinHeight: 640, TitleBarStyle: mygo.TitleBarHidden, HideWindowButtons: true,
			Content: ui.View(a.View)})
		icon, err := branding.PNG(128)
		if err == nil {
			err = a.win.SetIcon(icon)
		}
		if err != nil {
			a.addLog(fmt.Sprintf("设置窗口图标失败：%v", err))
		}
		if native, ok := scene.(interface{ SetWindow(*mygo.Window) error }); ok {
			if err := native.SetWindow(a.win); err != nil {
				a.fail(err)
			}
		}
		a.win.OnReadyToShow(func() { a.win.Show() })
		a.win.OnClose(func(e *mygo.CloseEvent) {
			if a.closing {
				return
			}
			e.PreventDefault()
			a.requestClose()
		})
		a.win.OnClosed(func() { a.closed.Store(true); scene.Close(); mygo.App.Quit() })
		if options.Updater != nil {
			go func() {
				timer := time.NewTimer(3 * time.Second)
				defer timer.Stop()
				for {
					select {
					case <-updateContext.Done():
						return
					case <-timer.C:
						a.update(func() {
							if a.softwareUpdate.preferences.Auto {
								a.checkForUpdate()
							}
						})
						timer.Reset(6 * time.Hour)
					}
				}
			}()
		}
		hostOptions := options.Engine
		hostOptions.OnEvent = func(event engine.Event) { a.update(func() { a.handleEvent(event) }) }
		hostOptions.OnLog = func(line string) { a.update(func() { a.addLog(line) }) }
		launching = true
		go func() {
			client, err := engine.Start(hostOptions)
			started <- client
			a.update(func() {
				if err != nil {
					a.fail(err)
					if a.options.Screenshot != "" || a.options.ExitAfter > 0 {
						a.scheduleCapture()
					}
					return
				}
				a.service = client
				a.initialize()
			})
		}()
	})
	err := mygo.App.Run()
	if launching {
		if client := <-started; client != nil {
			client.Close()
		}
	}
	return err
}

func (a *App) update(fn func()) {
	if a.closed.Load() {
		return
	}
	if a.win == nil {
		if a.dispatch != nil {
			a.dispatch(fn)
			return
		}
		fn()
		return
	}
	a.win.Update(func() {
		if !a.closed.Load() {
			fn()
		}
	})
}

func (a *App) addLog(line string) {
	a.logs = append(a.logs, time.Now().Format("15:04:05")+"  "+line)
	if len(a.logs) > 400 {
		a.logs = a.logs[len(a.logs)-400:]
	}
}

func (a *App) fail(err error) {
	a.failure = err.Error()
	a.status = "操作未完成"
	a.addLog(a.failure)
}

func (a *App) call(method string, params Object, result any) error {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	return a.service.Call(ctx, method, params, result)
}

// invoke serializes UI mutations while the host independently handles cancellation.
func (a *App) invoke(label, method string, params Object, done func(any)) {
	if a.busy || a.service == nil {
		return
	}
	if method == "agent_reset" {
		a.findingRequest++
	}
	a.busy, a.operation, a.status, a.failure = true, label, label, ""
	go func() {
		var result any
		err := a.call(method, params, &result)
		a.update(func() {
			a.busy, a.operation = false, ""
			if err != nil {
				if method == "agent_send" && a.turnStatus == "running" {
					a.turnStatus = "failed"
				}
				a.fail(err)
				return
			}
			a.status = label + "完成"
			a.addLog(a.status)
			if done != nil {
				done(result)
			}
		})
	}()
}

func (a *App) initialize() {
	a.busy = true
	go func() {
		var settings, runtime, snapshot any
		var parts, samples, cases []Object
		var history any
		err := a.call("handshake", Object{}, nil)
		if err == nil {
			err = a.call("native_settings_load", Object{}, &settings)
		}
		if err == nil {
			err = a.call("active_model_snapshot", Object{}, &snapshot)
		}
		if err == nil && len(object(snapshot)) == 0 {
			err = a.call("active_model_new", Object{"name": "未命名工程"}, &snapshot)
		}
		if err == nil {
			err = a.call("native_runtime_status", Object{}, &runtime)
		}
		_ = a.call("native_parts_list", Object{}, &parts)
		_ = a.call("native_samples_list", Object{}, &samples)
		_ = a.call("reviewed_cases_list", Object{}, &cases)
		_ = a.call("agent_history", Object{}, &history)
		a.update(func() {
			a.busy = false
			if err != nil {
				a.fail(err)
				if a.options.Screenshot != "" || a.options.ExitAfter > 0 {
					a.scheduleCapture()
				}
				return
			}
			a.ready = true
			a.settings = object(settings)
			a.runtime = object(runtime)
			a.parts, a.samples, a.cases = parts, samples, cases
			a.applyViewerSettings()
			a.acceptSnapshot(snapshot, false)
			a.restoreHistory(history)
			a.status = "就绪"
			if a.options.UpdateReady != nil && a.failure == "" {
				a.options.UpdateReady()
			}
			if a.options.DemoPrompt != "" {
				a.prompt = a.options.DemoPrompt
				a.submit()
			}
			if a.options.Screenshot != "" || a.options.ExitAfter > 0 {
				a.scheduleCapture()
			}
		})
	}()
}

func (a *App) applyViewerSettings() {
	v := object(a.settings["viewer"])
	for key, target := range map[string]*bool{"edges": &a.edges, "grid": &a.grid, "axes": &a.axes, "perspective": &a.perspective} {
		if value, ok := v[key].(bool); ok {
			*target = value
		}
	}
	a.online = object(a.settings["online"])["enabled"] == true
	native := object(a.settings["native_desktop"])
	if theme := text(native["theme"]); theme != "" {
		a.theme = theme
	}
	a.showThoughts = native["show_thoughts"] == true
}

func (a *App) acceptSnapshot(value any, dirty bool) {
	record := object(value)
	if len(record) == 0 {
		return
	}
	previousGeneration := a.generation
	document := record
	if len(object(record["document"])) > 0 {
		var generation uint64
		document, generation = restoreSnapshot(record, a.generation)
		if document == nil {
			return
		}
		a.generation = generation
	} else {
		generation := uint64(number(document["model_generation"]))
		if generation < a.generation {
			return
		}
		a.generation = generation
		document = clone(document)
		document["acceptance_confirmed"] = false
	}
	if len(document) == 0 {
		return
	}
	a.document = document
	a.dirty = dirty
	if a.generation != previousGeneration {
		a.findings = nil
	}
	path := text(document["mesh_path"])
	if path != "" && path != a.meshPath {
		if err := a.scene.Load(path); err != nil {
			a.fail(fmt.Errorf("模型视口加载失败 %w", err))
		} else {
			a.meshPath = path
		}
	}
	if path == "" && a.meshPath != "" {
		_ = a.scene.Load("")
		a.meshPath = ""
	}
	features := rows(document["features"])
	if a.selectedFeature >= len(features) {
		a.selectedFeature = -1
	}
	if a.selectedFeature >= 0 {
		a.selectFeature(a.selectedFeature)
	}
}

func (a *App) refreshModel(dirty bool) {
	a.invoke("刷新模型", "active_model_snapshot", Object{}, func(result any) { a.acceptSnapshot(result, dirty) })
}

func (a *App) selectFeature(index int) {
	features := rows(a.document["features"])
	if index < 0 || index >= len(features) {
		return
	}
	a.selectedFeature = index
	a.featureValues = clone(object(features[index]["params"]))
	a.arrayDrafts = map[string]string{}
	for key, value := range a.featureValues {
		if _, ok := value.([]any); ok {
			encoded, _ := json.Marshal(value)
			a.arrayDrafts[key] = string(encoded)
		}
	}
	a.featureName = text(features[index]["name"])
	a.detailTab = 0
}

func (a *App) restoreHistory(value any) {
	items := rows(value)
	if len(items) == 0 {
		items = rows(object(value)["turns"])
	}
	for _, item := range items {
		content := text(item["content"])
		if content == "" {
			content = text(item["text"])
		}
		if content != "" {
			a.messages = append(a.messages, Message{Role: text(item["role"]), Text: content, Turn: text(item["request_id"])})
		}
		if assistant := text(item["assistant"]); assistant != "" {
			a.messages = append(a.messages, Message{Role: "assistant", Text: assistant, Turn: text(item["request_id"])})
		}
	}
}

func (a *App) handleEvent(event engine.Event) {
	var wire struct {
		Event     string `json:"event"`
		RequestID string `json:"request_id"`
		Data      Object `json:"data"`
	}
	if json.Unmarshal(event.Data, &wire) != nil {
		return
	}
	if wire.RequestID != "" && a.turn != "" && wire.RequestID != a.turn {
		return
	}
	data := wire.Data
	switch wire.Event {
	case "agent.message":
		if a.chatScroll.Y >= a.chatScroll.MaxY-30 {
			a.chatScroll.Y = math.MaxFloat32
		}
		a.messages = appendStream(a.messages, "assistant", wire.RequestID, text(data["text"]))
	case "agent.thought":
		a.messages = appendStream(a.messages, "thought", wire.RequestID, text(data["text"]))
	case "agent.tool":
		line := text(data["title"]) + " · " + text(data["status"])
		a.addLog(line)
		a.status = line
	case "agent.warning":
		a.failure = text(data["message"])
		a.addLog(a.failure)
	case "job.progress", "agent.progress", "agent.status":
		if message := text(data["message"]); message != "" {
			a.status = message
			a.addLog(message)
		}
	case "model.changed":
		a.acceptSnapshot(data, true)
	case "agent.turn_complete":
		a.turnStatus = text(data["turn_status"])
	}
}

func (a *App) submit() {
	input := strings.TrimSpace(a.prompt)
	if input == "" && len(a.images) == 0 {
		return
	}
	if a.busy || !a.ready {
		return
	}
	if a.mode == "Quick" && len(a.images) > 0 {
		a.failure = "带图片的请求请切换为 Agent 模式。"
		return
	}
	if a.mode == "Agent" && object(a.settings["llm"])["enabled"] != true {
		a.failure = "请在设置中启用并配置模型服务。"
		a.openSettings()
		return
	}
	previousGeneration := a.generation
	previousDirty := a.dirty
	a.turn = fmt.Sprintf("native-%d", time.Now().UnixNano())
	a.messages = append(a.messages, Message{Role: "user", Text: input, Turn: a.turn})
	a.chatScroll.Y = math.MaxFloat32
	a.leftTab = 0
	if a.mode == "Quick" {
		a.invoke("生成模型", "quick_build", Object{"text": input, "revision": number(a.document["revision"]) + 1}, func(result any) {
			doc := object(result)
			if doc["state"] == "parse_failed" {
				a.fail(fmt.Errorf("%s", text(doc["message"])))
				return
			}
			a.acceptSnapshot(doc, true)
			if a.selectedFeature < 0 && len(rows(a.document["features"])) > 0 {
				a.selectFeature(0)
			}
			a.messages = append(a.messages, Message{Role: "assistant", Text: "模型已生成，可以在特征面板调整参数。", Turn: a.turn})
			a.refreshModel(true)
		})
	} else {
		llm := clone(object(a.settings["llm"]))
		images := append([]string{}, a.images...)
		a.turnStatus = "running"
		a.invoke("Agent 建模", "agent_send", Object{"requestId": a.turn, "text": input, "images": images, "llmConfig": llm, "onlineEnabled": a.online, "useReviewedCases": a.useCases}, func(result any) {
			response := object(result)
			a.turnStatus = text(response["turn_status"])
			if reply := text(response["reply"]); reply != "" {
				found := false
				for _, m := range a.messages {
					if m.Role == "assistant" && m.Turn == a.turn {
						found = true
					}
				}
				if !found {
					a.messages = append(a.messages, Message{Role: "assistant", Text: reply, Turn: a.turn})
				}
			}
			a.images = nil
			a.invoke("刷新模型", "active_model_snapshot", Object{}, func(result any) {
				a.acceptSnapshot(result, previousDirty || uint64(number(object(result)["generation"])) != previousGeneration)
				a.refreshFindings()
			})
		})
	}
}

func (a *App) cancel() {
	if a.service == nil {
		return
	}
	go func() {
		err := a.call("agent_cancel", Object{}, nil)
		a.update(func() {
			if err != nil {
				a.fail(err)
			} else {
				a.status = "已请求停止，正在收尾"
			}
		})
	}()
}

func (a *App) scheduleCapture() {
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
		defer cancel()
		defer func() {
			if a.options.ExitAfter > 0 && !a.closed.Load() {
				time.Sleep(a.options.ExitAfter)
				a.update(func() { a.closing = true; a.win.Close() })
			}
		}()
		onUI := func(fn func()) error {
			request, stop := context.WithTimeout(ctx, 5*time.Second)
			defer stop()
			completed := make(chan error, 1)
			a.update(func() {
				if err := request.Err(); err != nil {
					completed <- err
					return
				}
				fn()
				completed <- nil
			})
			select {
			case err := <-completed:
				return err
			case <-request.Done():
				return request.Err()
			}
		}
		idle := func() bool {
			loading := false
			if scene, ok := a.scene.(interface{ Loading() bool }); ok {
				loading = scene.Loading()
			}
			return !a.busy && !loading
		}
		for !a.closed.Load() {
			select {
			case <-time.After(500 * time.Millisecond):
			case <-ctx.Done():
				fmt.Fprintln(os.Stderr, "截图等待建模或视口加载超时")
				return
			}
			ready := false
			if err := onUI(func() {
				ready = idle()
				if ready {
					switch a.options.CapturePanel {
					case "settings":
						a.openSettings()
					case "updates":
						if a.options.Updater != nil {
							a.openSettings()
							a.settingsTab = 4
							if a.softwareUpdate.status == "" {
								a.checkForUpdate()
							}
							ready = !a.softwareUpdate.working
						}
					}
				}
			}); err != nil {
				if !a.closed.Load() {
					fmt.Fprintln(os.Stderr, "截图等待界面失败", err)
				}
				return
			}
			if !ready {
				continue
			}
			select {
			case <-time.After(800 * time.Millisecond):
			case <-ctx.Done():
				fmt.Fprintln(os.Stderr, "截图等待布局完成超时")
				return
			}
			if a.closed.Load() {
				return
			}
			var state, data []byte
			var captureErr error
			if err := onUI(func() {
				ready = idle()
				if !ready || a.options.Screenshot == "" {
					return
				}
				viewportError := ""
				if scene, ok := a.scene.(interface{ Error() string }); ok {
					viewportError = scene.Error()
				}
				state, captureErr = json.MarshalIndent(Object{
					"ready": a.ready, "busy": a.busy, "status": a.status,
					"failure": a.failure, "document": a.document, "logs": a.logs,
					"viewport_loading": false, "viewport_error": viewportError,
				}, "", "  ")
				if captureErr != nil {
					return
				}
				if native, ok := a.scene.(interface {
					CaptureWindow(*mygo.Window) ([]byte, error)
				}); ok {
					data, captureErr = native.CaptureWindow(a.win)
				} else {
					data, captureErr = a.win.CapturePage()
				}
			}); err != nil {
				if !a.closed.Load() {
					fmt.Fprintln(os.Stderr, "截图采集失败", err)
				}
				return
			}
			if !ready {
				continue
			}
			if a.options.Screenshot != "" {
				if captureErr == nil {
					captureErr = os.MkdirAll(filepath.Dir(a.options.Screenshot), 0755)
				}
				if captureErr == nil {
					captureErr = os.WriteFile(a.options.Screenshot+".json", state, 0644)
				}
				if captureErr == nil {
					captureErr = os.WriteFile(a.options.Screenshot, data, 0644)
				}
				if captureErr != nil {
					fmt.Fprintln(os.Stderr, captureErr)
				}
			}
			return
		}
	}()
}
