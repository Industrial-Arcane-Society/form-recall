package workbench

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

func (a *App) requestClose() {
	if !a.busy {
		a.confirmReplace(func() { a.closing = true; a.win.Close() })
		return
	}
	go func() {
		answer, err := mygo.Dialog.Message(mygo.MessageOptions{Parent: a.win, Type: mygo.MessageWarning, Title: "关闭工作台", Message: "当前操作仍在进行", Detail: "关闭后会停止引擎，已提交的活动模型可在下次启动时恢复。", Buttons: []string{"继续等待", "停止并关闭"}, DefaultButton: 0, CancelButton: 0})
		a.update(func() {
			if err != nil {
				a.fail(err)
				return
			}
			if answer.Button == 1 {
				a.closing = true
				a.win.Close()
			}
		})
	}()
}

func (a *App) resetChat(next func()) {
	a.invoke("新对话", "agent_reset", Object{}, func(any) {
		a.messages = nil
		a.images = nil
		a.turn = ""
		a.turnStatus = ""
		a.findings = nil
		if next != nil {
			next()
		}
	})
}

func (a *App) confirmReplace(next func()) {
	if a.busy {
		a.failure = "请先等待当前操作完成，或停止 Agent。"
		return
	}
	if !a.dirty {
		next()
		return
	}
	go func() {
		answer, err := mygo.Dialog.Message(mygo.MessageOptions{Parent: a.win, Type: mygo.MessageQuestion,
			Title: "保存工程", Message: "当前工程有未保存的修改", Detail: "继续前可以保存当前模型。",
			Buttons: []string{"保存", "放弃修改", "取消"}, DefaultButton: 0, CancelButton: 2})
		a.update(func() {
			if err != nil {
				a.fail(err)
				return
			}
			switch answer.Button {
			case 0:
				a.save(false, next)
			case 1:
				next()
			}
		})
	}()
}

func (a *App) newProject() {
	if !a.ready {
		return
	}
	a.confirmReplace(func() {
		a.resetChat(func() {
			a.invoke("新建工程", "active_model_new", Object{"name": "未命名工程"}, func(result any) {
				a.acceptSnapshot(result, false)
				a.selectedFeature = -1
				a.messages = nil
				a.images = nil
				a.prompt = ""
			})
		})
	})
}

func (a *App) save(as bool, next func()) {
	if a.busy || !a.ready {
		return
	}
	path := text(a.document["current_path"])
	if path != "" && !as {
		a.savePath(path, next)
		return
	}
	a.busy = true
	go func() {
		path, err := mygo.Dialog.Save(mygo.SaveDialogOptions{Parent: a.win, Title: "保存工程", DefaultPath: "model.aimodel",
			Filters: []mygo.FileFilter{{Name: "FormRecall 工程", Extensions: []string{"aimodel"}}}})
		a.update(func() {
			a.busy = false
			if err != nil {
				a.fail(err)
				return
			}
			if path != "" {
				a.savePath(path, next)
			}
		})
	}()
}

func (a *App) savePath(path string, next func()) {
	a.invoke("保存工程", "active_model_save", Object{"path": path}, func(result any) {
		a.acceptSnapshot(result, false)
		a.invoke("刷新模型", "active_model_snapshot", Object{}, func(result any) {
			a.acceptSnapshot(result, false)
			if next != nil {
				next()
			}
		})
	})
}

func (a *App) openProject() {
	if !a.ready {
		return
	}
	a.confirmReplace(func() {
		a.busy = true
		go func() {
			paths, err := mygo.Dialog.Open(mygo.OpenDialogOptions{Parent: a.win, Title: "打开工程",
				Filters: []mygo.FileFilter{{Name: "FormRecall 工程", Extensions: []string{"aimodel", "json"}}}})
			if err != nil || len(paths) == 0 {
				a.update(func() {
					a.busy = false
					if err != nil {
						a.fail(err)
					}
				})
				return
			}
			answer, err := mygo.Dialog.Message(mygo.MessageOptions{Parent: a.win, Type: mygo.MessageQuestion,
				Title: "打开并重建工程", Message: "将工程转换为当前原生模型并重新计算几何", Detail: filepath.Base(paths[0]),
				Buttons: []string{"打开工程", "取消"}, DefaultButton: 0, CancelButton: 1})
			a.update(func() {
				a.busy = false
				if err != nil {
					a.fail(err)
					return
				}
				if answer.Button != 0 {
					return
				}
				a.resetChat(func() {
					a.invoke("打开工程", "active_model_import_project", Object{"path": paths[0], "confirmed": true}, func(result any) {
						a.selectedFeature = -1
						a.acceptSnapshot(result, false)
						a.refreshModel(false)
					})
				})
			})
		}()
	})
}

func (a *App) export(kind string) {
	if a.busy || !a.ready {
		return
	}
	ext := map[string]string{"stl": "stl", "obj": "obj", "report": "json", "snapshot": "png", "three": "png", "four": "png", "extended": "png", "catia": "CATPart"}[kind]
	a.busy = true
	go func() {
		path, err := mygo.Dialog.Save(mygo.SaveDialogOptions{Parent: a.win, Title: "导出模型", DefaultPath: "model." + ext,
			Filters: []mygo.FileFilter{{Name: ext + " 文件", Extensions: []string{ext}}}})
		a.update(func() {
			a.busy = false
			if err != nil {
				a.fail(err)
				return
			}
			if path == "" {
				return
			}
			method := "active_model_export"
			params := Object{"kind": kind, "path": path}
			if kind == "snapshot" || kind == "three" || kind == "four" || kind == "extended" {
				method = "active_model_export_snapshot"
				viewSet := kind
				if kind == "snapshot" {
					viewSet = "single"
				}
				params = Object{"path": path, "viewSet": viewSet}
			}
			a.invoke("导出模型", method, params, nil)
		})
	}()
}

func (a *App) updateFeature(action string, delta int, enabled *bool) {
	if a.selectedFeature < 0 {
		return
	}
	values := clone(a.featureValues)
	if action == "" && enabled == nil {
		for key, draft := range a.arrayDrafts {
			var value []any
			if err := json.Unmarshal([]byte(draft), &value); err != nil || value == nil {
				a.failure = key + " 需要有效数组，例如 [0, 0, 0]。"
				return
			}
			values[key] = value
		}
	}
	params := Object{"index": a.selectedFeature, "params": values, "name": a.featureName}
	if action != "" {
		params["action"] = action
	}
	if delta != 0 {
		params["delta"] = delta
	}
	if enabled != nil {
		params["enabled"] = *enabled
		params["action"] = "toggle"
	}
	a.invoke("更新特征", "active_model_update_feature", params, func(result any) {
		if action == "move" {
			a.selectedFeature += delta
		}
		if action == "delete" {
			a.selectedFeature = -1
		}
		a.acceptSnapshot(result, true)
		a.refreshModel(true)
	})
}

func (a *App) deleteFeature() {
	if a.busy || a.selectedFeature < 0 {
		return
	}
	go func() {
		answer, err := mygo.Dialog.Message(mygo.MessageOptions{Parent: a.win, Type: mygo.MessageWarning, Title: "删除特征",
			Message: "删除所选特征并重新构建模型", Detail: a.featureName, Buttons: []string{"取消", "删除特征"}, DefaultButton: 0, CancelButton: 0})
		a.update(func() {
			if err != nil {
				a.fail(err)
				return
			}
			if answer.Button == 1 {
				a.updateFeature("delete", 0, nil)
			}
		})
	}()
}

func (a *App) removeCase(id string) {
	go func() {
		answer, err := mygo.Dialog.Message(mygo.MessageOptions{Parent: a.win, Type: mygo.MessageQuestion, Title: "移除案例",
			Message: "将此条目从审核案例库移除", Buttons: []string{"取消", "移除案例"}, DefaultButton: 0, CancelButton: 0})
		a.update(func() {
			if err != nil {
				a.fail(err)
				return
			}
			if answer.Button == 1 {
				a.invoke("移除案例", "reviewed_case_remove", Object{"id": id}, func(result any) { a.cases = rows(result) })
			}
		})
	}()
}

func (a *App) attachImages() {
	if a.busy {
		return
	}
	a.busy = true
	go func() {
		paths, err := mygo.Dialog.Open(mygo.OpenDialogOptions{Parent: a.win, Title: "选择参考图片", Multiple: true,
			Filters: []mygo.FileFilter{{Name: "图片", Extensions: []string{"png", "jpg", "jpeg", "webp"}}}})
		var staged []string
		thumbnails := map[string]ui.ImageSource{}
		if err == nil {
			for _, path := range paths {
				var target string
				if err = a.call("stage_input_image", Object{"sourcePath": path}, &target); err != nil {
					break
				}
				staged = append(staged, target)
				if bytes, readError := os.ReadFile(target); readError == nil {
					if preview, decodeError := ui.DecodeImage(bytes); decodeError == nil {
						thumbnails[target] = preview
					}
				}
			}
		}
		a.update(func() {
			a.busy = false
			a.images = append(a.images, staged...)
			for path, preview := range thumbnails {
				a.thumbnails[path] = preview
			}
			if err != nil {
				a.fail(fmt.Errorf("添加图片失败 %w", err))
			}
		})
	}()
}
