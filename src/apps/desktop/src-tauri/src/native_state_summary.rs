//! Read-only port of the legacy DSH CAD state summary.
use crate::{cad_plan, cad_plan_validation};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

const COORDINATE_CONTRACT: &str = "CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.";
const ACTIVE: &[&str] = &["open", "awaiting_verification", "needs_reference_check"];
const CANDIDATE_FIELDS: &[&str] = &[
    "revision",
    "preview_generation",
    "dimensions",
    "rank",
    "geometry_signature",
    "reference_coverage",
];
const HISTORY_FIELDS: &[&str] = &[
    "revision",
    "preview_generation",
    "view",
    "reference_image_index",
    "reference_id",
    "reference_image_name",
    "view_comparable",
    "score",
    "coverage_status",
    "covered_evidence_ids",
    "summary",
    "evidence",
    "unobservable",
    "out_of_scope_evidence_ids",
    "stage_scope",
    "stage_complete",
    "full_current_geometry_in_scope",
    "stage_diagnostic_only",
];
#[derive(Default)]
struct Context {
    refs: Vec<Value>,
    ref_error: Option<String>,
    turns: Vec<Value>,
    turns_error: Option<String>,
}

/// Build the legacy-compatible summary; caller owns persistence and transaction boundaries.
pub fn summary(
    state: &mut Value,
    reference_manifest_path: &Path,
    user_turns_manifest_path: &Path,
) -> Result<Value, String> {
    let ctx = load_context(reference_manifest_path, user_turns_manifest_path);
    let revision = state.get("revision").cloned().unwrap_or(json!(0));
    let Some(plan) = state.get("plan").filter(|p| p.is_object()).cloned() else {
        return Ok(
            json!({"state":"empty","revision":revision,"knowledge_search":get(state,"knowledge_search"),
            "reference_manifest_error":ctx.ref_error,"user_target_context_error":ctx.turns_error,
            "authoritative_user_turns":authoritative_turns(&ctx.turns)}),
        );
    };
    let signature = cad_plan::geometry_fingerprint(&plan)?;
    let revision_num = revision
        .as_i64()
        .or_else(|| revision.as_u64().map(|n| n.min(i64::MAX as u64) as i64))
        .unwrap_or(0);
    let validation = cad_plan_validation::validate_plan(&plan);
    let dimensions = if state.get("preview_revision") == Some(&revision) {
        state
            .get("last_preview")
            .and_then(|v| v.get("dimensions"))
            .cloned()
    } else {
        None
    };
    let unresolved = refresh_unresolved(state);
    let mut out = cad_plan::snapshot(
        &plan,
        Some(validation),
        dimensions,
        revision_num,
        Some(unresolved.clone()),
        true,
    );
    out["preview_generation"] = get_default(state, "preview_generation", json!(0));
    out["partial_preview"] = get(state, "partial_preview");
    out["last_preview_failure"] = get(state, "last_preview_failure");
    out["reference_inventory"] = obj_or_empty(state, "reference_inventory");
    out["preview_views"] = json!(state
        .get("previews")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|r| r.get("revision") == Some(&revision))
        .map(|r| json!({"view":r.get("view"),"preview_generation":r.get("preview_generation")}))
        .collect::<Vec<_>>());
    out["reference_images"] = json!(ctx
        .refs
        .iter()
        .enumerate()
        .map(|(i, r)| {
            let mut v = project(
                r,
                &["id", "sha256", "filename", "width", "height", "media_type"],
            );
            v["index"] = json!(i + 1);
            v
        })
        .collect::<Vec<_>>());
    out["reference_manifest_error"] = json!(ctx.ref_error);
    out["user_target_context_error"] = json!(ctx.turns_error);
    out["authoritative_user_turns"] = json!(authoritative_turns(&ctx.turns));
    out["main_agent_self_report_views"] = json!(state
        .get("visual_reviews")
        .and_then(Value::as_object)
        .into_iter()
        .flat_map(|m| m.iter())
        .filter(|(_, r)| r.get("revision") == Some(&revision))
        .map(|(k, _)| k.clone())
        .collect::<BTreeSet<_>>());
    let latest = latest_reviews(state, &ctx.turns, &revision);
    out["independent_reference_review_pairs"] = json!(latest
        .iter()
        .filter(|r| comparable(r, &signature, &ctx.refs))
        .count());
    out["coordinate_contract"] = json!(COORDINATE_CONTRACT);
    out["reference_similarity_review"] = state
        .get("reference_similarity_review")
        .filter(|r| r.get("revision") == Some(&revision))
        .cloned()
        .unwrap_or(Value::Null);
    out["reference_similarity_reviews"] = Value::Object(
        state
            .get("reference_similarity_reviews")
            .and_then(Value::as_object)
            .into_iter()
            .flat_map(|m| m.iter())
            .filter(|(_, r)| r.get("revision") == Some(&revision))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect(),
    );
    let history = state
        .get("reference_similarity_history")
        .and_then(Value::as_array);
    out["recent_reference_similarity_history"] = json!(history
        .into_iter()
        .flatten()
        .skip(history.map_or(0, |v| v.len().saturating_sub(6)))
        .map(|r| project(r, HISTORY_FIELDS))
        .collect::<Vec<_>>());
    out["reference_similarity_error"] = get(state, "reference_similarity_error");
    out["required_external_facts"] = obj_or_empty(state, "required_external_facts");
    out["replaced_plan_count"] = json!(state
        .get("replaced_plans")
        .and_then(Value::as_array)
        .map_or(0, Vec::len));
    out["finish_summary"] = get(state, "finish_summary");
    out["construction_milestone"] = get(state, "construction_milestone");
    out["visual_evidence_summary"] =
        visual_summary(state, &ctx.refs, &ctx.turns, &revision, &signature);
    out["unresolved_findings"] = unresolved;
    for key in [
        "last_valid_candidate",
        "best_evidence_candidate",
        "best_candidate",
    ] {
        out[key] = state
            .get(key)
            .filter(|v| v.as_object().is_some_and(|m| !m.is_empty()))
            .map(|v| project(v, CANDIDATE_FIELDS))
            .unwrap_or(Value::Null);
    }
    out["geometry_signature"] = json!(signature);
    out["geometry_mutations"] = json!(tail(state.get("geometry_mutations"), 12));
    out["knowledge_search"] = get(state, "knowledge_search");
    Ok(out)
}
fn get(v: &Value, k: &str) -> Value {
    v.get(k).cloned().unwrap_or(Value::Null)
}
fn get_default(v: &Value, k: &str, d: Value) -> Value {
    v.get(k).cloned().unwrap_or(d)
}
fn obj_or_empty(v: &Value, k: &str) -> Value {
    v.get(k)
        .filter(|x| x.is_object())
        .cloned()
        .unwrap_or_else(|| json!({}))
}
fn project(v: &Value, fields: &[&str]) -> Value {
    let mut m = Map::new();
    for k in fields {
        if let Some(x) = v.get(*k) {
            m.insert((*k).into(), x.clone());
        }
    }
    Value::Object(m)
}
fn tail(v: Option<&Value>, n: usize) -> Vec<Value> {
    v.and_then(Value::as_array)
        .map(|a| a.iter().skip(a.len().saturating_sub(n)).cloned().collect())
        .unwrap_or_default()
}
fn sha(b: &[u8]) -> String {
    format!("{:x}", Sha256::digest(b))
}
fn strval(v: &Value) -> String {
    v.as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| v.to_string())
}

fn load_context(ref_path: &Path, turn_path: &Path) -> Context {
    let (refs, ref_error) = load_refs(ref_path);
    let (turns, turns_error) = load_turns(turn_path);
    Context {
        refs,
        ref_error,
        turns,
        turns_error,
    }
}
fn load_refs(path: &Path) -> (Vec<Value>, Option<String>) {
    if path.as_os_str().is_empty() {
        return (vec![], None);
    }
    let text = match fs::read_to_string(path) {
        Ok(v) => v,
        Err(e) => {
            return (
                vec![],
                Some(if e.kind() == std::io::ErrorKind::NotFound {
                    format!("文件不存在：{}", path.display())
                } else {
                    e.to_string()
                }),
            );
        }
    };
    let parsed: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => return (vec![], Some(e.to_string())),
    };
    let Some(rows) = parsed
        .get("images")
        .and_then(Value::as_array)
        .or_else(|| parsed.as_array())
    else {
        return (vec![], Some("images 必须是数组。".into()));
    };
    let mut records = Vec::new();
    for (i, row) in rows.iter().enumerate() {
        let (Some(obj), Some(path_s), Some(id)) =
            (row.as_object(), row["path"].as_str(), row["id"].as_str())
        else {
            return (
                vec![],
                Some(format!("第 {} 项缺少 path 或稳定 id。", i + 1)),
            );
        };
        let p = PathBuf::from(path_s);
        let bytes = match fs::read(&p) {
            Ok(v) => v,
            Err(e) => {
                return (
                    vec![],
                    Some(format!("无法读取第 {} 项图片字节：{}", i + 1, e)),
                );
            }
        };
        let digest = sha(&bytes);
        if obj
            .get("sha256")
            .and_then(Value::as_str)
            .is_some_and(|v| !v.is_empty() && v.to_ascii_lowercase() != digest)
        {
            return (
                vec![],
                Some(format!("第 {} 项 sha256 与图片实际字节不一致。", i + 1)),
            );
        }
        if id != format!("sha256:{digest}") {
            return (
                vec![],
                Some(format!("第 {} 项 id 不是图片内容的 sha256 标识。", i + 1)),
            );
        }
        let mut r = row.clone();
        r["path"] = json!(p);
        r["sha256"] = json!(digest);
        r["id"] = json!(id);
        r.as_object_mut()
            .unwrap()
            .entry("index")
            .or_insert(json!(i + 1));
        records.push(r);
    }
    (records, None)
}
fn load_turns(path: &Path) -> (Vec<Value>, Option<String>) {
    if path.as_os_str().is_empty() {
        return (vec![], None);
    }
    let text = match fs::read_to_string(path) {
        Ok(v) => v,
        Err(e) => {
            return (
                vec![],
                Some(if e.kind() == std::io::ErrorKind::NotFound {
                    format!("文件不存在：{}", path.display())
                } else {
                    e.to_string()
                }),
            );
        }
    };
    let payload: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => return (vec![], Some(e.to_string())),
    };
    let Some(rows) = payload.get("turns").and_then(Value::as_array) else {
        return (vec![], Some("turns 必须是数组。".into()));
    };
    let mut seen = BTreeSet::new();
    let mut output = Vec::new();
    for (i, row) in rows.iter().enumerate() {
        if !row.is_object() {
            return (vec![], Some(format!("第 {} 项不是对象。", i + 1)));
        }
        let id = row["turn_id"].as_str().unwrap_or_default().trim();
        if id.is_empty()
            || !seen.insert(id.to_owned())
            || !row["raw_user_prompt"].is_string()
            || row
                .get("reference_ids")
                .is_some_and(|v| !v.is_null() && !v.is_array())
        {
            return (
                vec![],
                Some(format!(
                    "第 {} 项缺少唯一 turn_id、用户原文或引用列表。",
                    i + 1
                )),
            );
        }
        let mut refs = Vec::new();
        for x in row["reference_ids"].as_array().into_iter().flatten() {
            let s = strval(x);
            if !s.trim().is_empty() && !refs.contains(&json!(s)) {
                refs.push(json!(s));
            }
        }
        output.push(json!({"turn_id":id,"session_id":row.get("session_id").filter(|v|!v.is_null()).or_else(||payload.get("session_id")).cloned().unwrap_or(json!("")),
          "raw_user_prompt":row["raw_user_prompt"],"reference_ids":refs,
          "intent":row["intent"].as_str().map(|v|v.trim().to_ascii_lowercase()).filter(|v|!v.is_empty()),
          "intent_source":row["intent_source"].as_str().map(|v|v.trim().to_ascii_lowercase()).filter(|v|!v.is_empty()).unwrap_or_else(||"unclassified".into()),
          "affects_model_target":row.get("affects_model_target").filter(|v|v.is_boolean()).cloned().unwrap_or(Value::Null),
          "created_at":get(row,"created_at"),"at_epoch_ms":get(row,"at_epoch_ms")}));
    }
    (output, None)
}
fn authoritative_turns(turns: &[Value]) -> Vec<Value> {
    turns.iter().skip(turns.len().saturating_sub(12)).map(|r|json!({"turn_id":r["turn_id"],"reference_ids":r["reference_ids"],
      "raw_user_prompt":r["raw_user_prompt"].as_str().unwrap_or_default().trim().chars().take(5000).collect::<String>(),
      "affects_model_target":turn_affects(r),"intent_source":r["intent_source"]})).collect()
}
fn turn_affects(r: &Value) -> bool {
    if let Some(v) = r["affects_model_target"].as_bool() {
        return v;
    }
    match r["intent"]
        .as_str()
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase()
        .as_str()
    {
        "model_target" | "construction" | "geometry_edit" | "correction" | "create_model" => {
            return true;
        }
        "query" | "question" | "chat" | "acknowledgement" | "status" => return false,
        _ => {}
    }
    if r["reference_ids"].as_array().is_some_and(|v| !v.is_empty()) {
        return true;
    }
    let s = r["raw_user_prompt"].as_str().unwrap_or_default();
    let cues = [
        "建模",
        "生成",
        "创建",
        "绘制",
        "画出",
        "复刻",
        "重建",
        "做一个",
        "做成",
        "按图",
        "照着",
        "修改",
        "调整",
        "改成",
        "改为",
        "设为",
        "替换",
        "换成",
        "返修",
        "修正",
        "修复",
        "新增",
        "添加",
        "补上",
        "补充",
        "增加",
        "加上",
        "去掉",
        "删除",
        "移除",
        "取消",
        "增大",
        "加大",
        "扩大",
        "缩小",
        "减小",
        "缩短",
        "加厚",
        "减薄",
        "加深",
        "变浅",
        "保持",
        "保留",
        "不要",
        "别让",
        "移动到",
        "移到",
        "偏移",
        "靠左",
        "靠右",
        "前移",
        "后移",
        "上移",
        "下移",
        "向左",
        "向右",
        "向前",
        "向后",
        "大一点",
        "小一点",
        "更大",
        "更小",
        "更宽",
        "更窄",
        "更深",
        "更浅",
        "更厚",
        "更薄",
    ];
    if cues.iter().any(|v| s.contains(v)) || correction_cue(s) {
        return true;
    }
    let q = [
        "?",
        "？",
        "吗",
        "呢",
        "什么",
        "怎么",
        "为什么",
        "几个",
        "多少",
        "哪一个",
        "哪个",
        "哪里",
        "是否",
        "是不是",
        "查看一下",
        "看一下",
        "告诉我",
        "解释一下",
        "分析一下",
        "统计一下",
        "列一下",
        "列出",
        "汇报一下",
    ];
    if q.iter().any(|v| s.contains(v)) {
        return false;
    }
    let unit = has_dimension_unit(s);
    let geometry = [
        "孔",
        "槽",
        "台阶",
        "螺纹",
        "螺钉",
        "螺栓",
        "圆柱",
        "长方体",
        "直径",
        "半径",
        "尺寸",
        "长度",
        "宽度",
        "高度",
        "厚度",
        "深度",
    ]
    .iter()
    .any(|v| s.contains(v));
    unit && geometry
}
fn correction_cue(s: &str) -> bool {
    let missing = [
        "少",
        "缺",
        "漏",
        "没有",
        "没看到",
        "没螺纹",
        "没螺牙",
        "没孔",
        "没槽",
        "没凹槽",
        "没凸台",
        "没台阶",
        "没倒角",
    ];
    let geometry = [
        "螺纹", "螺牙", "孔", "槽", "凹槽", "凸台", "台阶", "倒角", "模型", "零件",
    ];
    let discrepancy = ["少了", "缺了", "漏了", "不对", "错了", "有问题"];
    let question_edit = ["为什么", "怎么会", "怎么没有", "怎会", "是不是"]
        .iter()
        .any(|x| s.contains(x))
        && missing.iter().any(|x| s.contains(x));
    let missing_geometry =
        missing.iter().any(|x| s.contains(x)) && geometry.iter().any(|x| s.contains(x));
    let feature_discrepancy =
        geometry.iter().any(|x| s.contains(x)) && discrepancy.iter().any(|x| s.contains(x));
    question_edit || missing_geometry || feature_discrepancy
}
fn has_dimension_unit(s: &str) -> bool {
    let chars: Vec<char> = s.chars().collect();
    for i in 0..chars.len() {
        if !chars[i].is_ascii_digit() {
            continue;
        }
        let mut j = i;
        while j < chars.len() && (chars[j].is_ascii_digit() || chars[j] == '.') {
            j += 1;
        }
        while j < chars.len() && chars[j].is_whitespace() {
            j += 1;
        }
        let tail: String = chars[j..].iter().collect::<String>().to_ascii_lowercase();
        if ["mm", "毫米", "cm", "厘米", "m", "米", "°", "度", "%"]
            .iter()
            .any(|u| tail.starts_with(u))
        {
            return true;
        }
    }
    false
}

fn target_fingerprint(turns: &[Value]) -> String {
    let rows:Vec<Value>=turns.iter().filter(|r|turn_affects(r)).map(|r|json!({"turn_id":r["turn_id"],"raw_user_prompt":r["raw_user_prompt"],"reference_ids":r["reference_ids"]})).collect();
    sha(serde_json::to_string(&rows).unwrap_or_default().as_bytes())
}
fn latest_reviews(state: &Value, turns: &[Value], revision: &Value) -> Vec<Value> {
    let fp = target_fingerprint(turns);
    let has = !turns.is_empty();
    let mut latest: BTreeMap<(String, String), Value> = BTreeMap::new();
    for r in state["reference_similarity_history"]
        .as_array()
        .into_iter()
        .flatten()
    {
        if r.get("revision") != Some(revision) {
            continue;
        }
        if let Some(f) = r.get("user_target_fingerprint").and_then(Value::as_str) {
            if (has && f != fp) || (!has && f != fp) {
                continue;
            }
        }
        let id = r
            .get("reference_id")
            .or_else(|| r.get("reference_image_index"))
            .map(strval)
            .unwrap_or_default();
        let view = r["view"].as_str().unwrap_or_default().to_owned();
        if id.is_empty() || view.is_empty() {
            continue;
        }
        let key = (id, view);
        if latest.get(&key).map_or(true, |old| {
            num(&r["recorded_at"]) >= num(&old["recorded_at"])
        }) {
            latest.insert(key, r.clone());
        }
    }
    latest.into_values().collect()
}
fn num(v: &Value) -> f64 {
    v.as_f64()
        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        .unwrap_or(0.)
}
fn comparable(r: &Value, signature: &str, refs: &[Value]) -> bool {
    if r["view_comparable"] != true
        || r["partial_candidate_diagnostic_only"] == true
        || r["geometry_signature"].as_str() != Some(signature)
    {
        return false;
    }
    // Stable IDs are content-addressed; index-only legacy rows cannot prove source identity.
    let Some(id) = r["reference_id"].as_str() else {
        return false;
    };
    let Some((index, record)) = refs.iter().enumerate().find(|(_, row)| row["id"] == id) else {
        return false;
    };
    if r.get("reference_image_index")
        .and_then(Value::as_u64)
        .is_some_and(|value| value != (index + 1) as u64)
    {
        return false;
    }
    if r.get("source_sha256")
        .and_then(Value::as_str)
        .is_some_and(|sha| record["sha256"].as_str() != Some(sha))
    {
        return false;
    }
    // Reference similarity reviews cover the full immutable source, never an ROI crop.
    if r.get("roi_norm").is_some_and(|roi| !roi.is_null()) {
        return false;
    }
    true
}

fn inventory_key(source: &str) -> String {
    sha(serde_json::to_string(
        &json!({"source_sha256":source.to_ascii_lowercase(),"roi_norm":null}),
    )
    .unwrap_or_default()
    .as_bytes())
}
fn inventory_requirements(state: &Value, refs: &[Value], id: &str) -> Value {
    let record = refs.iter().find(|r| r["id"] == id);
    let inv = state["reference_inventory"].get(id);
    let source = record
        .map(|r| r["sha256"].as_str().unwrap_or_default())
        .unwrap_or_default();
    let visible = inv
        .and_then(|v| v["visible_evidence"].as_array())
        .cloned()
        .unwrap_or_default();
    let mut required = Vec::new();
    let mut seen = BTreeSet::new();
    let mut invalid = 0;
    for item in &visible {
        if !item.is_object() {
            invalid += 1;
            continue;
        }
        let evidence = item["evidence_id"].as_str().unwrap_or_default().trim();
        if evidence.is_empty() || !seen.insert(evidence.to_owned()) {
            invalid += 1;
            continue;
        }
        required.push(json!({"evidence_id":evidence,"group_id":short(item["group_id"].as_str().unwrap_or_default(),200),"description":short(item["description"].as_str().unwrap_or_default(),800)}));
    }
    let valid = record.is_some()
        && !source.is_empty()
        && inv.is_some_and(|v| {
            v["reference_id"] == id
                && v["source_sha256"] == source
                && v["roi_norm"].is_null()
                && v["roi_cache_key"] == inventory_key(source)
                && v["observation_valid"] == true
        })
        && !required.is_empty()
        && invalid == 0;
    let mut optional = Vec::new();
    for (i, item) in inv
        .and_then(|v| v["uncertain"].as_array())
        .into_iter()
        .flatten()
        .enumerate()
    {
        if item.is_object() {
            optional.push(json!({"uncertain_id":item.get("uncertain_id").map(strval).unwrap_or_else(||(i+1).to_string()),"description":short(item["description"].as_str().unwrap_or_default(),800),"reason":short(item["reason"].as_str().unwrap_or_default(),800)}))
        } else if !item.is_null() && !strval(item).trim().is_empty() {
            optional.push(json!({"uncertain_id":(i+1).to_string(),"description":short(&strval(item),800),"reason":""}))
        }
    }
    json!({"inventory_valid":valid,"required_evidence":required,"required_evidence_ids":required.iter().map(|v|v["evidence_id"].clone()).collect::<Vec<_>>(),"optional_unknowns":optional,"invalid_evidence_count":invalid})
}
fn short(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn validated_target(e: &Value, turns: &[Value]) -> bool {
    let basis = e["assessment_basis"]
        .as_str()
        .unwrap_or("reference")
        .trim()
        .to_ascii_lowercase();
    if basis != "user_target" && basis != "user_override" {
        return true;
    }
    let id = e["target_turn_id"].as_str().unwrap_or_default().trim();
    let quote = e["target_quote"].as_str().unwrap_or_default().trim();
    if id.is_empty() || quote.chars().count() < 4 {
        return false;
    }
    let Some(t) = turns.iter().find(|t| t["turn_id"] == id) else {
        return false;
    };
    if !turn_affects(t) {
        return false;
    }
    ws(quote).len() > 0
        && ws(t["raw_user_prompt"].as_str().unwrap_or_default()).contains(&ws(quote))
}
fn ws(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}
fn status(v: &Value) -> String {
    let s = v
        .as_str()
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase()
        .replace(' ', "_");
    if ["", "unknown", "unobservable", "not_evaluated"].contains(&s.as_str()) {
        return if s.is_empty() { "unknown".into() } else { s };
    }
    if s.contains("unobservable") {
        return "unobservable".into();
    }
    if ["partial", "partial_match", "partial_mismatch"].contains(&s.as_str()) {
        return "partial_mismatch".into();
    }
    if s.contains("mismatch") || ["different", "not_match"].contains(&s.as_str()) {
        return "mismatch".into();
    }
    if ["match_with_minor_difference", "approximate_match"].contains(&s.as_str()) {
        return "approximate_match".into();
    }
    s
}
fn push(a: &mut Value, v: Value) {
    if !a.is_array() {
        *a = json!([])
    }
    let rows = a.as_array_mut().unwrap();
    if !rows.contains(&v) {
        rows.push(v)
    }
}
fn strings(v: &Value) -> BTreeSet<String> {
    v.as_array().into_iter().flatten().map(strval).collect()
}
fn visual_summary(
    state: &Value,
    refs: &[Value],
    turns: &[Value],
    revision: &Value,
    geometry: &str,
) -> Value {
    let latest = latest_reviews(state, turns, revision);
    let mut records: BTreeMap<String, Value> = BTreeMap::new();
    for (i, r) in refs.iter().enumerate() {
        let id = r["id"]
            .as_str()
            .or_else(|| r["index"].as_str())
            .map(str::to_owned)
            .unwrap_or_else(|| (i + 1).to_string());
        let req = inventory_requirements(state, refs, &id);
        records.insert(id.clone(), blank_ref(&id, r.get("filename"), &req));
    }
    let mut facts: BTreeMap<(String, String), BTreeMap<String, Vec<Value>>> = BTreeMap::new();
    for review in &latest {
        let id = review
            .get("reference_id")
            .or_else(|| review.get("reference_image_index"))
            .map(strval)
            .unwrap_or_default();
        if !records.contains_key(&id) {
            let req = inventory_requirements(state, refs, &id);
            records.insert(
                id.clone(),
                blank_ref(&id, review.get("reference_image_name"), &req),
            );
        }
        let rec = records.get_mut(&id).unwrap();
        if !comparable(review, geometry, refs) {
            rec["noncomparable_pairs"] =
                json!(rec["noncomparable_pairs"].as_u64().unwrap_or(0) + 1);
            continue;
        }
        rec["comparable_pairs"] = json!(rec["comparable_pairs"].as_u64().unwrap_or(0) + 1);
        let req: BTreeSet<String> = strings(&rec["required_evidence_ids"]);
        let covered = strings(&review["covered_evidence_ids"]);
        let view = review["view"].as_str().unwrap_or_default().to_owned();
        let mut seen = BTreeSet::new();
        for e in review["evidence"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|x| x.is_object())
        {
            let evidence = e["evidence_id"].as_str().unwrap_or_default().trim();
            if evidence.is_empty() {
                continue;
            }
            seen.insert(evidence.to_owned());
            if e["status"].as_str() == Some("out_of_scope") {
                push(&mut rec["out_of_scope_evidence_ids"], json!(evidence));
                continue;
            }
            if !req.contains(evidence) {
                push(&mut rec["unmapped_evidence_ids"], json!(evidence));
                continue;
            }
            let st = status(&e["status"]);
            let basis = e["assessment_basis"]
                .as_str()
                .unwrap_or("reference")
                .trim()
                .to_ascii_lowercase();
            let rs = status(&e["reference_status"]);
            let target_valid = e
                .get("target_context_validated")
                .and_then(Value::as_bool)
                .unwrap_or_else(|| validated_target(e, turns));
            let override_valid = basis == "user_override"
                && target_valid
                && e["target_overrides_reference"] == true
                && !e["target"].as_str().unwrap_or_default().trim().is_empty()
                && !rs.is_empty()
                && rs != "unknown";
            let grounded = if basis == "user_target" || basis == "user_override" {
                target_valid
                    && !e["target"].as_str().unwrap_or_default().trim().is_empty()
                    && !e["reference"]
                        .as_str()
                        .unwrap_or_default()
                        .trim()
                        .is_empty()
                    && !e["model"].as_str().unwrap_or_default().trim().is_empty()
            } else {
                !e["reference"]
                    .as_str()
                    .unwrap_or_default()
                    .trim()
                    .is_empty()
                    && !e["model"].as_str().unwrap_or_default().trim().is_empty()
            };
            facts.entry((id.clone(),evidence.into())).or_default().entry(view.clone()).or_default().push(json!({"status":st,"grounded":grounded,"covered":covered.contains(evidence),"reference_conflict":e["reference_conflict"]==true,"assessment_basis":basis,"reference_status":rs,"override_valid":override_valid,"target_valid":target_valid}));
        }
        for x in covered.difference(&seen) {
            push(&mut rec["unsupported_coverage_ids"], json!(x))
        }
        for x in covered.intersection(&seen).filter(|x| !req.contains(*x)) {
            push(&mut rec["unmapped_coverage_ids"], json!(x))
        }
    }
    for ((refid, eid), views) in facts {
        let rec = records.get_mut(&refid).unwrap();
        let rows: Vec<&Value> = views.values().flatten().collect();
        if views.values().any(|v| v.len() > 1) {
            push(&mut rec["mismatch_evidence_ids"], json!(eid));
            continue;
        }
        for r in &rows {
            if r["override_valid"] == true
                && ["mismatch", "partial_mismatch"]
                    .contains(&r["reference_status"].as_str().unwrap_or_default())
            {
                push(&mut rec["overridden_reference_evidence_ids"], json!(eid))
            }
        }
        if rows.iter().any(|r| {
            r["reference_conflict"] == true
                || ["mismatch", "partial_mismatch"]
                    .contains(&r["status"].as_str().unwrap_or_default())
                || (r["assessment_basis"] == "user_target"
                    && r["target_valid"] == true
                    && ["mismatch", "partial_mismatch"]
                        .contains(&r["reference_status"].as_str().unwrap_or_default()))
        }) {
            push(&mut rec["mismatch_evidence_ids"], json!(eid));
            continue;
        }
        let supported: Vec<&Value> = rows
            .iter()
            .copied()
            .filter(|r| {
                r["grounded"] == true
                    && r["covered"] == true
                    && ["match", "approximate_match"]
                        .contains(&r["status"].as_str().unwrap_or_default())
                    && (r["assessment_basis"] != "user_override" || r["override_valid"] == true)
                    && (r["assessment_basis"] != "user_target"
                        || ["match", "approximate_match"]
                            .contains(&r["reference_status"].as_str().unwrap_or_default()))
            })
            .collect();
        if !supported.is_empty() {
            let k = if supported.iter().any(|r| r["status"] == "approximate_match") {
                "estimated_evidence_ids"
            } else {
                "supported_evidence_ids"
            };
            push(&mut rec[k], json!(eid))
        } else if rows.iter().any(|r| r["status"] == "unobservable") {
            push(&mut rec["unobservable_evidence_ids"], json!(eid))
        } else {
            push(&mut rec["unknown_required_evidence_ids"], json!(eid))
        }
    }
    for (id, rec) in records.iter_mut() {
        let required = strings(&rec["required_evidence_ids"]);
        let resolved: BTreeSet<_> = strings(&rec["supported_evidence_ids"])
            .union(&strings(&rec["estimated_evidence_ids"]))
            .cloned()
            .collect();
        for e in &required {
            if !facts_present(&latest, id, e, geometry, refs) {
                push(&mut rec["missing_required_evidence_ids"], json!(e))
            }
        }
        for attempt in state["reference_similarity_attempts"]
            .as_array()
            .into_iter()
            .flatten()
        {
            if attempt["failed"] != true
                || attempt.get("revision") != Some(revision)
                || attempt.get("reference_id").map(strval).as_deref() != Some(id.as_str())
            {
                continue;
            }
            let mut d = project(
                attempt,
                &[
                    "revision",
                    "preview_generation",
                    "view",
                    "reference_id",
                    "reference_image_index",
                    "review_scope",
                    "started_at",
                    "failed",
                    "error",
                ],
            );
            let err = &state["reference_similarity_error"];
            if err.get("revision") == attempt.get("revision")
                && err.get("preview_generation") == attempt.get("preview_generation")
                && err.get("view") == attempt.get("view")
                && err.get("reference_id").map(strval).as_deref() == Some(id.as_str())
            {
                d["error"] = get(err, "error")
            }
            rec["failed_review_attempts"]
                .as_array_mut()
                .unwrap()
                .push(d)
        }
        if let Some(ledger) = state["finding_ledger"].as_object() {
            for (_, f) in ledger {
                let fid = f
                    .get("reference_id")
                    .or_else(|| f.get("reference_image_index"))
                    .map(strval)
                    .unwrap_or_default();
                if fid != *id {
                    continue;
                }
                let e = f["evidence_id"].as_str().unwrap_or_default();
                match f["status"].as_str().unwrap_or_default() {
                    "needs_reference_check" => {
                        push(&mut rec["reference_conflicts"], json!(e));
                        if required.contains(e) {
                            push(&mut rec["mismatch_evidence_ids"], json!(e))
                        }
                    }
                    "open" | "awaiting_verification" => {
                        push(&mut rec["open_finding_evidence_ids"], json!(e));
                        if required.contains(e) {
                            push(&mut rec["mismatch_evidence_ids"], json!(e))
                        }
                    }
                    _ => {}
                }
            }
        }
        for e in resolved.difference(&strings(&rec["mismatch_evidence_ids"])) {
            push(&mut rec["resolved_required_evidence_ids"], json!(e))
        }
        let mismatch = !strings(&rec["mismatch_evidence_ids"]).is_empty()
            || !strings(&rec["reference_conflicts"]).is_empty();
        let unsupported = !strings(&rec["unsupported_coverage_ids"]).is_empty();
        let unresolved: BTreeSet<String> = [
            "missing_required_evidence_ids",
            "unknown_required_evidence_ids",
            "unobservable_evidence_ids",
            "out_of_scope_evidence_ids",
            "mismatch_evidence_ids",
        ]
        .iter()
        .flat_map(|k| strings(&rec[*k]))
        .collect();
        rec["unresolved_required_evidence_ids"] = json!(unresolved);
        let covered = rec["inventory_valid"] == true
            && !required.is_empty()
            && unresolved.is_empty()
            && !mismatch
            && !unsupported;
        rec["covered"] = json!(covered);
        rec["coverage_status"] = json!(if mismatch || unsupported || !unresolved.is_empty() {
            "partial"
        } else if !strings(&rec["estimated_evidence_ids"]).is_empty() {
            "estimated"
        } else if covered {
            "verified"
        } else {
            "unverified"
        });
    }
    let n = latest.len();
    let c = latest
        .iter()
        .filter(|r| comparable(r, geometry, refs))
        .count();
    json!({"revision":revision,"references":records,"latest_review_pairs":n,"comparable_review_pairs":c,"noncomparable_review_pairs":n-c,
  "all_references_covered":!records.is_empty()&&records.values().all(|r|r["covered"]==true),
  "all_required_evidence_covered":!records.is_empty()&&records.values().all(|r|r["covered"]==true)})
}
fn blank_ref(id: &str, filename: Option<&Value>, req: &Value) -> Value {
    json!({"reference_id":id,"filename":filename.cloned().unwrap_or(Value::Null),"comparable_pairs":0,"noncomparable_pairs":0,
  "supported_evidence_ids":[],"estimated_evidence_ids":[],"mismatch_evidence_ids":[],"unobservable_evidence_ids":[],
  "missing_required_evidence_ids":[],"unknown_required_evidence_ids":[],"unresolved_required_evidence_ids":[],
  "out_of_scope_evidence_ids":[],
  "required_evidence_ids":req["required_evidence_ids"],"optional_unknowns":req["optional_unknowns"],
  "unmapped_evidence_ids":[],"unmapped_coverage_ids":[],"unsupported_coverage_ids":[],"failed_review_attempts":[],
  "inventory_valid":req["inventory_valid"],"invalid_inventory_evidence_count":req["invalid_evidence_count"],
  "coverage_status":"unverified","covered":false})
}
fn facts_present(latest: &[Value], id: &str, e: &str, geometry: &str, refs: &[Value]) -> bool {
    latest.iter().any(|r| {
        r.get("reference_id")
            .or_else(|| r.get("reference_image_index"))
            .map(strval)
            .as_deref()
            == Some(id)
            && comparable(r, geometry, refs)
            && r["evidence"]
                .as_array()
                .is_some_and(|rows| rows.iter().any(|v| v["evidence_id"] == e))
    })
}

fn refresh_unresolved(state: &mut Value) -> Value {
    if !state.is_object() {
        *state = json!({});
    }
    let mut rows = Vec::new();
    if let Some(ledger) = state.get("finding_ledger").and_then(Value::as_object) {
        for (id, finding) in ledger {
            if !ACTIVE.contains(&finding["status"].as_str().unwrap_or_default()) {
                continue;
            }
            rows.push(json!({"finding_id":id,"evidence_id":get(finding,"evidence_id"),"item":get(finding,"item"),
                "status":get(finding,"status"),"severity":get(finding,"severity"),
                "reference_image_index":get(finding,"reference_image_index"),"reference_id":get(finding,"reference_id"),
                "affected_feature_ids":finding.get("affected_feature_ids").filter(|v|v.is_array()).cloned().unwrap_or_else(||json!([])),
                "reference_evidence":get(finding,"reference_evidence"),"model_evidence":get(finding,"model_evidence"),
                "suggested_change":get(finding,"suggested_change"),"last_revision":get(finding,"last_revision")}));
        }
    }
    state["unresolved_findings"] = json!(rows);
    state["unresolved_findings"].clone()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};
    fn dir() -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "native-state-summary-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&p).unwrap();
        p
    }
    fn put(path: &Path, v: &Value) {
        fs::write(path, serde_json::to_vec(v).unwrap()).unwrap()
    }
    fn fixture() -> (PathBuf, PathBuf, PathBuf, Value) {
        let d = dir();
        let image = d.join("sample.png");
        let bytes = b"fixture-image";
        fs::write(&image, bytes).unwrap();
        let digest = sha(bytes);
        let rid = format!("sha256:{digest}");
        let refs = d.join("refs.json");
        put(
            &refs,
            &json!({"images":[{"path":image,"id":rid,"sha256":digest,"filename":"sample.png","width":100,"height":80,"media_type":"image/png"}]}),
        );
        let turns = d.join("turns.json");
        put(
            &turns,
            &json!({"session_id":"s1","turns":[
   {"turn_id":"target","raw_user_prompt":"请创建一个带 8 mm 孔的支架","reference_ids":[rid],"intent":"model_target"},
   {"turn_id":"question","raw_user_prompt":"当前模型有几个孔？","reference_ids":[],"intent":"question"}]}),
        );
        let plan = json!({"schema_version":"1.0","name":"fixture","source_prompt":"support","units":{"length":"mm","angle":"deg"},
   "coordinate_system":{"handedness":"right","up_axis":"Z","forward_axis":"Y"},
   "features":[{"id":"base","kind":"box","operation":"add","parameters":{"length":20,"width":10,"height":5,"center":[0,0,0]},"relations":[]}],"pending_questions":[]});
        let state = json!({"plan":plan,"revision":3,"preview_revision":3,"preview_generation":2,"last_preview":{"dimensions":[20,10,5]},
   "previews":[{"revision":3,"preview_generation":2,"view":"iso"}],"visual_reviews":{"front":{"revision":3},"old":{"revision":2}},
   "reference_similarity_history":[],"reference_similarity_attempts":[],"reference_inventory":{},
   "finding_ledger":{"f1":{"status":"open","severity":"major","evidence_id":"e1","item":"孔未闭合","affected_feature_ids":["base"],"last_revision":3},
    "f2":{"status":"resolved","evidence_id":"e2"}},"knowledge_search":{"status":"no_match"}});
        (refs, turns, d, state)
    }
    #[test]
    fn no_plan_keeps_context_contract() {
        let (r, t, d, mut s) = fixture();
        s["plan"] = Value::Null;
        let out = summary(&mut s, &r, &t).unwrap();
        assert_eq!(out["state"], "empty");
        assert_eq!(
            out["authoritative_user_turns"][0]["affects_model_target"],
            true
        );
        assert_eq!(
            out["authoritative_user_turns"][1]["affects_model_target"],
            false
        );
        assert_eq!(out["knowledge_search"]["status"], "no_match");
        fs::remove_dir_all(d).unwrap();
    }
    #[test]
    fn review_versions_inventory_hash_roi_and_ledger_are_gated() {
        let (r, t, d, mut s) = fixture();
        let sig = cad_plan::geometry_fingerprint(&s["plan"]).unwrap();
        let manifest: Value = serde_json::from_slice(&fs::read(&r).unwrap()).unwrap();
        let rid = manifest["images"][0]["id"].clone();
        let source = manifest["images"][0]["sha256"].as_str().unwrap();
        let (turns, err) = load_turns(&t);
        assert!(err.is_none());
        let fp = target_fingerprint(&turns);
        let good = json!({"revision":3,"recorded_at":10,"reference_id":rid,"view":"iso","view_comparable":true,"geometry_signature":sig,
   "user_target_fingerprint":fp,"covered_evidence_ids":["e1"],"evidence":[{"evidence_id":"e1","status":"match","reference":"one circular hole","model":"one circular hole"}]});
        let mut older = good.clone();
        older["revision"] = json!(2);
        older["recorded_at"] = json!(99);
        let mut stale = good.clone();
        stale["recorded_at"] = json!(11);
        stale["geometry_signature"] = json!("stale-signature");
        s["reference_similarity_history"] = json!([older, good, stale]);
        s["reference_inventory"][rid.as_str().unwrap()] = json!({"reference_id":rid,"source_sha256":source,"observation_valid":true,
   "roi_cache_key":inventory_key(source),"visible_evidence":[{"evidence_id":"e1","group_id":"g","description":"hole"}]});
        let out = summary(&mut s, &r, &t).unwrap();
        let visual = &out["visual_evidence_summary"];
        let rec = &visual["references"][rid.as_str().unwrap()];
        assert_eq!(visual["comparable_review_pairs"], 0);
        assert_eq!(visual["noncomparable_review_pairs"], 1);
        assert_eq!(rec["inventory_valid"], true);
        assert_eq!(rec["supported_evidence_ids"].as_array().unwrap().len(), 0);
        assert_eq!(rec["coverage_status"], "partial");
        assert_eq!(out["unresolved_findings"].as_array().unwrap().len(), 1);
        assert_eq!(out["independent_reference_review_pairs"], 0);
        assert_eq!(out["geometry_signature"], sig);
        fs::remove_dir_all(d).unwrap();
    }
    #[test]
    fn missing_roi_or_sha_never_validates_inventory() {
        let (r, t, d, mut s) = fixture();
        let manifest: Value = serde_json::from_slice(&fs::read(&r).unwrap()).unwrap();
        let id = manifest["images"][0]["id"].as_str().unwrap();
        let source = manifest["images"][0]["sha256"].as_str().unwrap();
        s["reference_inventory"][id] = json!({"reference_id":id,"source_sha256":source,"observation_valid":true,
            "visible_evidence":[{"evidence_id":"x","description":"visible"}]});
        let out = summary(&mut s, &r, &t).unwrap();
        assert_eq!(
            out["visual_evidence_summary"]["references"][id]["inventory_valid"],
            false
        );
        fs::remove_dir_all(d).unwrap();
    }
    #[test]
    fn review_source_hash_and_roi_must_match_current_full_image() {
        let (r, t, d, mut s) = fixture();
        let manifest: Value = serde_json::from_slice(&fs::read(&r).unwrap()).unwrap();
        let rid = manifest["images"][0]["id"].clone();
        let source = manifest["images"][0]["sha256"].clone();
        let sig = cad_plan::geometry_fingerprint(&s["plan"]).unwrap();
        s["reference_similarity_history"] = json!([
            {"revision":3,"recorded_at":1,"reference_id":rid,"reference_image_index":1,"view":"iso","view_comparable":true,"geometry_signature":sig,"source_sha256":"wrong"},
            {"revision":3,"recorded_at":2,"reference_id":rid,"reference_image_index":1,"view":"front","view_comparable":true,"geometry_signature":sig,"source_sha256":source,"roi_norm":[0.1,0.1,0.5,0.5]}
        ]);
        let out = summary(&mut s, &r, &t).unwrap();
        assert_eq!(out["independent_reference_review_pairs"], 0);
        assert_eq!(out["visual_evidence_summary"]["comparable_review_pairs"], 0);
        assert_eq!(
            out["visual_evidence_summary"]["noncomparable_review_pairs"],
            2
        );
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn reference_file_hash_mismatch_is_reported_and_not_projected() {
        let (r, t, d, mut s) = fixture();
        let mut m: Value = serde_json::from_slice(&fs::read(&r).unwrap()).unwrap();
        m["images"][0]["sha256"] = json!("wrong");
        put(&r, &m);
        let out = summary(&mut s, &r, &t).unwrap();
        assert!(out["reference_manifest_error"]
            .as_str()
            .unwrap()
            .contains("sha256"));
        assert_eq!(out["reference_images"].as_array().unwrap().len(), 0);
        fs::remove_dir_all(d).unwrap();
    }
}
