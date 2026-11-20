package workbench

import "github.com/egoist/mygo/ui"

func (a *App) findingCard(c *ui.Context, row Object) {
	ui.Column(c).Padding(12).Gap(8).Border(1, c.Theme().Warning).Radius(7).Children(func() {
		ui.Text(c, "待确认来源关联").Bold()
		ui.Text(c, text(row["item"]))
		ui.Text(c, "既有证据  "+text(row["reference_evidence"])).FontSize(12)
		ui.Text(c, "候选证据  "+text(row["candidate_reference_evidence"])).FontSize(12)
		ui.Text(c, "视角  "+text(object(row["binding"])["view"])).FontSize(12)
		if ui.Button(c, "确认来自同一差异").Disabled(a.busy).Clicked() {
			a.confirmFinding(row)
		}
		ui.Text(c, "关联后仍需当前模型的独立复核。").FontSize(11).TextColor(c.Theme().TextMuted)
	})
}

func (a *App) refreshFindings() {
	a.findingRequest++
	request, generation, turn := a.findingRequest, a.generation, a.turn
	go func() {
		var state Object
		err := a.call("agent_finding_review_state", Object{}, &state)
		a.update(func() {
			if request != a.findingRequest || generation != a.generation || turn != a.turn {
				return
			}
			if err == nil {
				a.findings = rows(state["pending"])
			} else {
				a.findings = nil
			}
		})
	}()
}

func (a *App) confirmFinding(row Object) {
	binding := object(row["binding"])
	request := Object{}
	for _, key := range []string{"expected_generation", "expected_revision", "finding_id", "candidate_evidence_id", "candidate_observation_id", "affected_feature_ids"} {
		request[key] = row[key]
	}
	for _, key := range []string{"reference_id", "source_sha256", "user_target_fingerprint", "view"} {
		request[key] = binding[key]
	}
	a.invoke("确认来源关联", "agent_adjudicate_finding", Object{"request": request}, func(result any) {
		r := object(result)
		if r["resolved"] != false || r["finding_status"] != "awaiting_verification" {
			a.failure = "宿主尚未确认待复核状态。"
		} else {
			a.status = "来源关联已记录，等待当前版本完整复核"
		}
		a.refreshFindings()
	})
}

func (a *App) reuseCase(row Object) {
	a.confirmReplace(func() {
		a.resetChat(func() {
			a.invoke("新建工程", "active_model_new", Object{"name": "未命名工程"}, func(result any) {
				a.acceptSnapshot(result, false)
				a.prompt = text(row["prompt"])
				a.mode = "Agent"
				a.leftTab = 0
			})
		})
	})
}

func (a *App) onlineLookup() {
	if a.prompt == "" {
		a.failure = "请先输入要查询的问题。"
		return
	}
	a.invoke("在线查询", "native_online_ask", Object{"query": a.prompt}, func(result any) {
		r := object(result)
		answer := text(r["answer"])
		if answer == "" {
			answer = pretty(result)
		}
		a.messages = append(a.messages, Message{Role: "assistant", Text: answer})
		a.leftTab = 0
	})
}
