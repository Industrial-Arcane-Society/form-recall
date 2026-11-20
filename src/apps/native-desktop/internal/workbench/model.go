package workbench

import (
	"encoding/json"
	"fmt"
)

type Object map[string]any

func object(value any) Object {
	if value, ok := value.(map[string]any); ok {
		return value
	}
	if value, ok := value.(Object); ok {
		return value
	}
	return Object{}
}

func text(value any) string {
	if value, ok := value.(string); ok {
		return value
	}
	return ""
}

func number(value any) float64 {
	if value, ok := value.(float64); ok {
		return value
	}
	if value, ok := value.(int); ok {
		return float64(value)
	}
	return 0
}

func rows(value any) []Object {
	var result []Object
	if values, ok := value.([]any); ok {
		for _, item := range values {
			result = append(result, object(item))
		}
	}
	return result
}

func pretty(value any) string { data, _ := json.MarshalIndent(value, "", "  "); return string(data) }

func clone(value Object) Object {
	var result Object
	_ = json.Unmarshal([]byte(pretty(value)), &result)
	return result
}

// Acceptance follows the host's generation and fingerprint confirmation contract.
func acceptance(document Object) string {
	if len(rows(document["features"])) == 0 {
		return "尚无模型"
	}
	finish := object(document["finish_summary"])
	fingerprint := text(document["geometry_fingerprint"])
	current := fingerprint != "" && text(finish["geometry_fingerprint"]) == fingerprint &&
		number(finish["revision"]) == number(document["cad_revision"])
	if current && finish["accepted"] == true && finish["finish_state"] == "accepted" && document["acceptance_confirmed"] == true {
		return "已验收"
	}
	if len(finish) > 0 && !current {
		return "验收记录已过期"
	}
	return "候选模型"
}

// Restore only accepts a host projection tied to this exact active generation.
func restoreSnapshot(record Object, currentGeneration uint64) (Object, uint64) {
	document := object(record["document"])
	if len(document) == 0 {
		return nil, currentGeneration
	}
	generation := uint64(number(record["generation"]))
	if generation < currentGeneration || number(document["model_generation"]) != float64(generation) || number(document["revision"]) != float64(generation) {
		return nil, currentGeneration
	}
	document = clone(document)
	projection := object(record["acceptance_projection"])
	model := object(projection["model"])
	fingerprint := text(document["geometry_fingerprint"])
	revision := number(document["cad_revision"])
	matches := fingerprint != "" && number(projection["active_generation"]) == float64(generation) && number(projection["revision"]) == revision && text(projection["geometry_fingerprint"]) == fingerprint && number(model["revision"]) == revision && text(model["geometry_fingerprint"]) == fingerprint
	finish := object(model["finish_summary"])
	current := matches && number(finish["revision"]) == revision && text(finish["geometry_fingerprint"]) == fingerprint
	confirmed := current && model["acceptance_confirmed"] == true && model["status"] == "accepted" && model["finish_status"] == "accepted" && finish["accepted"] == true && finish["finish_state"] == "accepted"
	document["acceptance_confirmed"] = confirmed
	document["geometric_status"] = "unknown"
	document["visual_status"] = "unknown"
	if matches {
		document["geometric_status"] = object(model["geometric"])["status"]
		document["visual_status"] = object(model["visual"])["status"]
	}
	document["finish_summary"] = nil
	if current && (confirmed || (finish["accepted"] != true && finish["finish_state"] != "accepted")) {
		document["finish_summary"] = finish
	}
	return document, generation
}

func parameterLabel(key string) string {
	labels := map[string]string{"length": "长度 · mm", "width": "宽度 · mm", "height": "高度 · mm", "thickness": "厚度 · mm", "diameter": "直径 · mm", "radius": "半径 · mm", "depth": "深度 · mm", "center": "中心坐标 · mm", "point": "定位点 · mm", "axis": "轴向", "base": "以底面为基准", "corner": "圆角尺寸 · mm", "corner_mode": "圆角模式", "through": "贯穿", "count": "数量", "angle": "角度 · °"}
	if label := labels[key]; label != "" {
		return label
	}
	return key
}

func statusLabel(value string) string {
	labels := map[string]string{"passed": "通过", "accepted": "已验收", "candidate": "候选", "no_references": "未提供参考图", "not_reviewed": "尚未复核", "not_built": "尚未构建", "unknown": "尚无结论", "completed": "本轮完成", "failed": "本轮失败", "cancelled": "已停止", "running": "处理中", "": "尚无结论"}
	if label := labels[value]; label != "" {
		return label
	}
	return value
}

func dimensionLabel(value any) string {
	values, ok := value.([]any)
	if !ok || len(values) < 3 {
		return ""
	}
	return fmt.Sprintf("%.1f × %.1f × %.1f mm", number(values[0]), number(values[1]), number(values[2]))
}

type Message struct {
	Role, Text, Turn, Tool string
}

func appendStream(messages []Message, role, turn, content string) []Message {
	for index := len(messages) - 1; index >= 0; index-- {
		if messages[index].Role == role && messages[index].Turn == turn {
			messages[index].Text += content
			return messages
		}
	}
	return append(messages, Message{Role: role, Text: content, Turn: turn})
}
