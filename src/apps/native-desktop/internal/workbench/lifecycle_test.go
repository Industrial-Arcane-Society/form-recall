package workbench

import (
	"errors"
	"reflect"
	"sync/atomic"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"
)

func TestInitializeCreatesSavableEmptyModel(t *testing.T) {
	var active Object
	var methods []string
	a := New(testService{func(method string, params Object) (any, error) {
		methods = append(methods, method)
		switch method {
		case "native_settings_load":
			return Object{}, nil
		case "active_model_snapshot":
			if active == nil {
				return nil, nil
			}
			return Object{"generation": float64(1), "document": active}, nil
		case "active_model_new":
			active = Object{"name": params["name"], "model_generation": float64(1), "revision": float64(1), "features": []any{}}
			return active, nil
		case "active_model_save":
			if active == nil {
				return nil, errors.New("no active model")
			}
			active["current_path"] = params["path"]
			return active, nil
		}
		return nil, nil
	}}, &testScene{})
	queue := make(chan func(), 20)
	a.dispatch = func(fn func()) { queue <- fn }
	tt := ui.NewTester(a.View, 980, 640)
	a.initialize()
	drain(t, a, tt, queue)
	if !a.ready || a.generation != 1 || text(a.document["name"]) != "未命名工程" || len(rows(a.document["features"])) != 0 || a.dirty {
		t.Fatalf("first startup did not create a usable empty model: %v", a.document)
	}
	if !reflect.DeepEqual(methods[:4], []string{"handshake", "native_settings_load", "active_model_snapshot", "active_model_new"}) {
		t.Fatalf("unexpected initialization sequence: %v", methods)
	}
	a.savePath("empty.aimodel", nil)
	drain(t, a, tt, queue)
	if a.failure != "" || text(a.document["current_path"]) != "empty.aimodel" {
		t.Fatalf("newly initialized model could not be saved: %s", a.failure)
	}
}

func TestInitializeRestoresExistingModelAndReportsCreateFailure(t *testing.T) {
	for _, existing := range []bool{false, true} {
		t.Run(map[bool]string{false: "creation failure", true: "existing model"}[existing], func(t *testing.T) {
			creates := 0
			a := New(testService{func(method string, _ Object) (any, error) {
				switch method {
				case "active_model_snapshot":
					if existing {
						return sampleRecord(4), nil
					}
				case "active_model_new":
					creates++
					return nil, errors.New("creation failed")
				}
				return nil, nil
			}}, &testScene{})
			queue := make(chan func(), 20)
			a.dispatch = func(fn func()) { queue <- fn }
			tt := ui.NewTester(a.View, 980, 640)
			a.initialize()
			drain(t, a, tt, queue)
			if existing {
				if !a.ready || creates != 0 || a.generation != 4 || acceptance(a.document) != "已验收" {
					t.Fatal("existing model was replaced or its acceptance was lost")
				}
			} else if a.ready || creates != 1 || len(a.document) != 0 || a.failure != "creation failed" {
				t.Fatal("failed empty-model creation was presented as a ready workspace")
			}
		})
	}
}

func TestEmptySnapshotDoesNotCreateOrReplaceDocument(t *testing.T) {
	a := New(nil, &testScene{})
	for _, snapshot := range []any{nil, Object{}} {
		a.acceptSnapshot(snapshot, true)
		if len(a.document) != 0 || a.dirty {
			t.Fatal("empty snapshot created a placeholder model")
		}
	}
	a.acceptSnapshot(sampleRecord(4), true)
	before := pretty(a.document)
	a.acceptSnapshot(nil, false)
	if pretty(a.document) != before || a.generation != 4 || !a.dirty || a.meshPath != "model.glb" {
		t.Fatal("empty snapshot discarded the existing model or dirty state")
	}
}

func TestAgentStartupFailureEndsRunningStatus(t *testing.T) {
	a, tt, queue := newUITest(t)
	a.service = testService{func(string, Object) (any, error) {
		return nil, errors.New("runtime unavailable")
	}}
	a.mode, a.prompt = "Agent", "创建底板"
	a.submit()
	drain(t, a, tt, queue)
	if a.turnStatus != "failed" || a.failure != "runtime unavailable" || a.busy {
		t.Fatal("startup error left the Agent turn running")
	}
	if !tt.HasText("本轮失败") {
		t.Fatal("the ended failure state is not visible in the workbench")
	}
	a.turnStatus = "cancelled"
	a.invoke("Agent 建模", "agent_send", Object{}, nil)
	drain(t, a, tt, queue)
	if a.turnStatus != "cancelled" {
		t.Fatal("a terminal event state was overwritten by a generic call error")
	}
}

func TestFindingRefreshRejectsChangedContext(t *testing.T) {
	for _, change := range []string{"reset empty turn", "new model", "new turn"} {
		t.Run(change, func(t *testing.T) {
			a, tt, queue := newUITest(t)
			started, release := make(chan struct{}), make(chan struct{})
			a.service = testService{func(method string, _ Object) (any, error) {
				if method == "agent_finding_review_state" {
					close(started)
					<-release
					return Object{"pending": []any{Object{"finding_id": "stale"}}}, nil
				}
				return nil, nil
			}}
			a.refreshFindings()
			select {
			case <-started:
			case <-time.After(3 * time.Second):
				t.Fatal("review request did not start")
			}
			switch change {
			case "reset empty turn":
				a.resetChat(nil)
				drain(t, a, tt, queue)
			case "new model":
				a.findings = []Object{{"finding_id": "previous-model"}}
				a.acceptSnapshot(sampleRecord(2), true)
				if len(a.findings) != 0 {
					t.Fatal("new geometry retained findings from the previous generation")
				}
			case "new turn":
				a.turn = "next-turn"
			}
			close(release)
			select {
			case update := <-queue:
				update()
			case <-time.After(3 * time.Second):
				t.Fatal("review result did not arrive")
			}
			if len(a.findings) != 0 {
				t.Fatalf("stale findings returned after %s", change)
			}
		})
	}
}

func TestFindingRefreshKeepsNewestResponse(t *testing.T) {
	for _, oldFails := range []bool{false, true} {
		t.Run(map[bool]string{false: "late success", true: "late error"}[oldFails], func(t *testing.T) {
			a, _, queue := newUITest(t)
			var calls atomic.Int32
			started := make(chan struct{})
			release := make(chan struct{})
			a.service = testService{func(string, Object) (any, error) {
				if calls.Add(1) == 1 {
					close(started)
					<-release
					if oldFails {
						return nil, errors.New("old request failed")
					}
					return Object{"pending": []any{Object{"finding_id": "older"}}}, nil
				}
				return Object{"pending": []any{Object{"finding_id": "current"}}}, nil
			}}
			a.refreshFindings()
			select {
			case <-started:
			case <-time.After(3 * time.Second):
				t.Fatal("first review request did not start")
			}
			a.refreshFindings()
			select {
			case update := <-queue:
				update()
			case <-time.After(3 * time.Second):
				t.Fatal("latest review response did not arrive")
			}
			close(release)
			select {
			case update := <-queue:
				update()
			case <-time.After(3 * time.Second):
				t.Fatal("older review response did not arrive")
			}
			if len(a.findings) != 1 || a.findings[0]["finding_id"] != "current" {
				t.Fatal("older review result replaced or cleared the latest findings")
			}
		})
	}
}
