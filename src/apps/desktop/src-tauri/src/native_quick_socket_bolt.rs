//! Narrow migration of the measured legacy Quick socket-head screw recipe.
//!
//! The Python Quick recipe rebuilds catalogued screws with a smooth shank.
//! This Rust recipe keeps its measured envelope and washer dimensions while
//! using the existing native standard-feature builder for a visible faceted
//! thread. That intentional difference is surfaced in the result warning.
use serde_json::{Value, json};

const BOLT_LIBRARY: &str = include_str!("../../../../legacy/kb/bolt_library.json");
const BOLT_STANDARDS: &str = include_str!("../../../../legacy/kb/bolt_std.json");

/// Return the standard coarse metric pitch recorded for a nominal diameter.
/// The table is shared with the legacy Quick fastener recipes; an unknown
/// diameter stays unknown instead of receiving an invented default pitch.
pub fn standard_metric_pitch(diameter: f64) -> Option<f64> {
    if !diameter.is_finite() || diameter <= 0.0 {
        return None;
    }
    let rounded = diameter.round();
    if (diameter - rounded).abs() > 1e-9 {
        return None;
    }
    let standards: Value = serde_json::from_str(BOLT_STANDARDS).ok()?;
    standards
        .get("bolts")?
        .get(format!("M{}", rounded as i64))?
        .get("p")?
        .as_f64()
        .filter(|pitch| pitch.is_finite() && *pitch > 0.0)
}

/// Return `None` for unrelated descriptions, otherwise return a complete
/// Quick parse result (including explicit clarification/unsupported states).
pub fn parse(raw: &str) -> Option<Value> {
    let normalized = raw.to_lowercase().replace(['×', '＊'], "x");
    let has_fastener_word = contains_any(
        &normalized,
        &["螺钉", "螺栓", "螺丝", "screw", "bolt", "紧固件"],
    );
    let has_metric_designation =
        regex::Regex::new(r"(?i)M\s*[0-9]+(?:\.[0-9]+)?\s*(?:x|×)\s*[0-9]+(?:\.[0-9]+)?")
            .expect("constant metric designation regex is valid")
            .is_match(&normalized);
    let socket_head = contains_any(
        &normalized,
        &[
            "内六角圆柱头",
            "内六角螺钉",
            "内六角螺栓",
            "内六角螺丝",
            "杯头螺钉",
            "杯头螺丝",
            "socket head",
            "allen screw",
            "allen bolt",
            "iso 4762",
            "gb/t 70.1",
        ],
    ) || (normalized.contains("内六角")
        && (has_fastener_word || has_metric_designation));
    let other_head = contains_any(
        &normalized,
        &[
            "沉头",
            "盘头",
            "半圆头",
            "外六角",
            "十字",
            "一字",
            "自攻",
            "countersunk",
            "hex bolt",
            "pan head",
        ],
    );

    if !socket_head && !(has_fastener_word && has_metric_designation) {
        return None;
    }

    let size = match requested_size(&normalized) {
        Ok(size) => size,
        Err(message) => {
            return Some(result(
                "needs_clarification",
                raw,
                Value::Null,
                vec![],
                vec![issue("legacy_socket_bolt_size_conflict", message)],
                vec![],
            ));
        }
    };

    if other_head {
        return Some(result(
            "unsupported",
            raw,
            Value::Null,
            vec![],
            vec![],
            vec![issue(
                "legacy_socket_bolt_head_type_unsupported",
                "当前迁移项只覆盖 ISO 4762 / GB/T 70.1 内六角圆柱头螺钉；描述的是其它头型，不能按内六角螺钉替代。",
            )],
        ));
    }

    if !socket_head {
        return Some(result(
            "needs_clarification",
            raw,
            Value::Null,
            vec![],
            vec![issue(
                "legacy_socket_bolt_head_type_missing",
                "已识别到螺钉规格，但头型不明确。请说明是否为内六角圆柱头螺钉（ISO 4762 / GB/T 70.1）。",
            )],
            vec![],
        ));
    }

    if contains_any(
        &normalized,
        &[
            "开孔",
            "打孔",
            "钻孔",
            "通孔",
            "盲孔",
            "中心孔",
            "键槽",
            "倒角",
            "圆角",
            "螺纹长度",
            "半螺纹",
            "部分螺纹",
            "光杆段",
            "无螺纹段",
        ],
    ) {
        return Some(result(
            "unsupported",
            raw,
            Value::Null,
            vec![],
            vec![],
            vec![issue(
                "legacy_socket_bolt_compound_geometry_unsupported",
                "当前螺钉配方只生成目录中的螺钉、可选垫圈和全长可见螺纹；额外孔槽、倒角或局部螺纹要求未迁移，不能静默忽略。",
            )],
        ));
    }
    if contains_any(&normalized, &["不带螺纹", "无螺纹", "光杆", "smooth shank"]) {
        return Some(result(
            "unsupported",
            raw,
            Value::Null,
            vec![],
            vec![],
            vec![issue(
                "legacy_socket_bolt_thread_mode_unsupported",
                "当前 Rust 螺钉配方会生成全长可见螺纹；无螺纹光杆模式尚未实现。",
            )],
        ));
    }

    let Some((diameter, length)) = size else {
        return Some(result(
            "needs_clarification",
            raw,
            Value::Null,
            vec![],
            vec![issue(
                "legacy_socket_bolt_size_missing",
                "请给出螺钉规格，例如 M5×8；本配方只使用有实测尺寸记录的型号。",
            )],
            vec![],
        ));
    };
    if !diameter.is_finite() || diameter <= 0.0 || !length.is_finite() || length <= 0.0 {
        return Some(result(
            "needs_clarification",
            raw,
            Value::Null,
            vec![],
            vec![issue(
                "legacy_socket_bolt_size_invalid",
                "螺纹公称直径和螺钉长度必须为有限正数。",
            )],
            vec![],
        ));
    }

    let model = format!("M{}x{}", compact_number(diameter), compact_number(length));
    let library: Value = serde_json::from_str(BOLT_LIBRARY)
        .expect("bundled legacy bolt_library.json must remain valid JSON");
    let standards: Value = serde_json::from_str(BOLT_STANDARDS)
        .expect("bundled legacy bolt_std.json must remain valid JSON");
    let Some(measured) = library.get("bolts").and_then(|bolts| bolts.get(&model)) else {
        return Some(result(
            "unsupported",
            raw,
            Value::Null,
            vec![],
            vec![],
            vec![issue(
                "legacy_socket_bolt_model_not_measured",
                format!(
                    "{model} 不在旧 Quick 的实测螺钉目录中；当前不对未测型号猜测头部、垫圈或长度尺寸。"
                ),
            )],
        ));
    };
    let Some(spec_name) = measured.get("spec").and_then(Value::as_str) else {
        return Some(invalid_catalog(raw, &model));
    };
    let Some(standard) = standards
        .get("bolts")
        .and_then(|bolts| bolts.get(spec_name))
    else {
        return Some(result(
            "unsupported",
            raw,
            Value::Null,
            vec![],
            vec![],
            vec![issue(
                "legacy_socket_bolt_standard_missing",
                format!("实测型号 {model} 缺少 {spec_name} 的螺距/内六角标准参数记录。"),
            )],
        ));
    };

    let fields = [
        ("shank_d", "shank_d"),
        ("length", "length"),
        ("head_d", "head_d"),
        ("head_h", "head_h"),
        ("hex_s", "hex_s"),
        ("thread_pitch", "p"),
        ("hex_t", "t"),
    ];
    let mut dimensions = serde_json::Map::new();
    for (target, source) in fields {
        let value = if target == "thread_pitch" || target == "hex_t" {
            standard.get(source)
        } else {
            measured.get(source)
        }
        .and_then(Value::as_f64);
        let Some(value) = value.filter(|value| value.is_finite() && *value > 0.0) else {
            return Some(invalid_catalog(raw, &model));
        };
        dimensions.insert(target.to_owned(), json!(value));
    }

    if let Some((stated_pitch, label)) = labeled_pitch(&normalized)
        && (stated_pitch - dimensions["thread_pitch"].as_f64().unwrap()).abs() > 1e-8
    {
        return Some(result(
            "needs_clarification",
            raw,
            Value::Null,
            vec![],
            vec![issue(
                "legacy_socket_bolt_pitch_conflict",
                format!(
                    "描述中的{label} {stated_pitch} mm 与 {spec_name} 标准粗牙螺距 {} mm 不一致；请确认规格。",
                    dimensions["thread_pitch"].as_f64().unwrap()
                ),
            )],
            vec![],
        ));
    }

    for (labels, key, label) in [
        (&["头径", "头部直径", "头部外径"][..], "head_d", "头径"),
        (&["头高", "头部高度"][..], "head_h", "头高"),
        (&["内六角对边", "对边宽", "对边"][..], "hex_s", "内六角对边"),
    ] {
        if let Some(stated) = labeled_number(&normalized, labels)
            && (stated - dimensions[key].as_f64().unwrap()).abs() > 1e-8
        {
            return Some(result(
                "needs_clarification",
                raw,
                Value::Null,
                vec![],
                vec![issue(
                    "legacy_socket_bolt_dimension_conflict",
                    format!(
                        "描述中的{label} {stated} mm 与实测 {model} 目录值 {} mm 不一致；请确认是否要改用自定义螺钉尺寸。",
                        dimensions[key].as_f64().unwrap()
                    ),
                )],
                vec![],
            ));
        }
    }

    let spring = measured
        .get("spring_washer")
        .filter(|value| value.is_object());
    let flat = measured
        .get("flat_washer")
        .filter(|value| value.is_object());
    let omit_all_washers = contains_any(
        &normalized,
        &[
            "不带垫圈",
            "不要垫圈",
            "无垫圈",
            "without washer",
            "without washers",
        ],
    );
    let include_spring = !omit_all_washers
        && !contains_any(
            &normalized,
            &["不带弹垫", "不要弹垫", "无弹垫", "no spring washer"],
        );
    let include_flat = !omit_all_washers
        && !contains_any(
            &normalized,
            &["不带平垫", "不要平垫", "无平垫", "no flat washer"],
        );
    let spring = spring.filter(|_| include_spring);
    let flat = flat.filter(|_| include_flat);
    let spring_t = spring
        .and_then(|washer| washer["t"].as_f64())
        .unwrap_or(0.0);
    let flat_t = flat.and_then(|washer| washer["t"].as_f64()).unwrap_or(0.0);
    if spring_t + flat_t > dimensions["length"].as_f64().unwrap() + 1e-8 {
        return Some(result(
            "unsupported",
            raw,
            Value::Null,
            vec![],
            vec![],
            vec![issue(
                "legacy_socket_bolt_washer_stack_exceeds_shank",
                format!("{model} 的垫圈总厚度超过公称螺钉长度；旧实测装配不能在该布局中安全重建。"),
            )],
        ));
    }

    let mut features = Vec::new();
    let mut z = dimensions["head_h"].as_f64().unwrap();
    if let Some(washer) = spring {
        if let Some(feature) = washer_ring_feature(
            raw,
            features.len() + 1,
            "弹簧垫圈",
            washer,
            z,
            "旧 Quick 实测目录中的 spring_washer 尺寸；圆环轮廓直接回转成形。",
        ) {
            z += spring_t;
            features.push(feature);
        } else {
            return Some(invalid_catalog(raw, &model));
        }
    }
    if let Some(washer) = flat {
        if let Some(feature) = washer_ring_feature(
            raw,
            features.len() + 1,
            "平垫圈",
            washer,
            z,
            "旧 Quick 实测目录中的 flat_washer 尺寸；圆环轮廓直接回转成形。",
        ) {
            features.push(feature);
        } else {
            return Some(invalid_catalog(raw, &model));
        }
    }

    let mut bolt_parameters = dimensions;
    bolt_parameters.insert("center".into(), json!([0.0, 0.0, 0.0]));
    bolt_parameters.insert("segments".into(), json!(48));
    let mut bolt_sources = serde_json::Map::new();
    bolt_sources.insert(
        "shank_d".into(),
        user_source(raw, "M 规格明确给出公称螺纹直径。"),
    );
    bolt_sources.insert(
        "length".into(),
        user_source(raw, "M×L 规格明确给出螺钉公称长度。"),
    );
    for key in ["head_d", "head_h", "hex_s"] {
        bolt_sources.insert(
            key.into(),
            derived_source(
                raw,
                &format!(
                    "来自旧 Quick 实测零件目录 {model}（{}）。",
                    measured["source"].as_str().unwrap_or("实测尺寸记录")
                ),
            ),
        );
    }
    bolt_sources.insert(
        "thread_pitch".into(),
        derived_source(
            raw,
            &format!("来自 {spec_name} 的 ISO 4762 / GB/T 70.1 标准螺距表。"),
        ),
    );
    bolt_sources.insert(
        "hex_t".into(),
        derived_source(raw, &format!("来自 {spec_name} 的标准内六角最小深度表。")),
    );
    bolt_sources.insert(
        "center".into(),
        derived_source(raw, "沿用旧 Quick 螺钉实测装配的同轴原点基准。"),
    );
    bolt_sources.insert(
        "segments".into(),
        derived_source(raw, "使用 Rust 原生标准件线程网格的 48 段圆周采样。"),
    );
    features.push(json!({
        "id":feature_id(features.len() + 1),
        "kind":"socket_head_bolt",
        "operation":"add",
        "name":format!("内六角圆柱头螺钉 {model}（含可见螺纹）"),
        "parameters":bolt_parameters,
        "sources":bolt_sources,
        "relations":[]
    }));

    let plan = json!({
        "schema_version":"1.0",
        "name":format!("内六角圆柱头螺钉 {model}"),
        "source_prompt":raw,
        "units":{"length":"mm","angle":"deg"},
        "coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},
        "features":features,
        "pending_questions":[]
    });
    let warnings = vec![
        issue(
            "legacy_socket_bolt_thread_enhancement",
            format!(
                "Rust 结果保留旧 Quick 实测 {model} 的头部、杆长和垫圈尺寸，并增加可见外螺纹；旧 Python measured realparts 将杆身重建为光杆，因此这项螺纹是有意差异。"
            ),
        ),
        issue(
            "socket_bolt_thread_faceted_approximation",
            "可见螺纹由三角网格近似，不是解析螺旋面，也不等于 CATIA 原生螺纹特征。",
        ),
    ];
    Some(result("ready", raw, plan, warnings, vec![], vec![]))
}

fn washer_ring_feature(
    raw: &str,
    id: usize,
    name: &str,
    washer: &Value,
    z: f64,
    note: &str,
) -> Option<Value> {
    let inner = washer.get("d_in")?.as_f64()?;
    let outer = washer.get("d_out")?.as_f64()?;
    let thickness = washer.get("t")?.as_f64()?;
    if !inner.is_finite()
        || !outer.is_finite()
        || !thickness.is_finite()
        || inner <= 0.0
        || outer <= inner
        || thickness <= 0.0
        || !z.is_finite()
    {
        return None;
    }
    let profile = json!([
        [inner / 2.0, 0.0],
        [outer / 2.0, 0.0],
        [outer / 2.0, thickness],
        [inner / 2.0, thickness]
    ]);
    Some(json!({
        "id":feature_id(id),
        "kind":"revolve",
        "operation":"add",
        "name":format!("{name} Ø{}×{}", compact_number(outer), compact_number(thickness)),
        "parameters":{"profile":profile,"angle":360.0,"center":[0.0,0.0,z],"axis":"Z"},
        "sources":{
            "profile":derived_source(raw,note),
            "angle":derived_source(raw,"垫圈为完整 360° 轴对称圆环。"),
            "center":derived_source(raw,"与螺钉轴线同轴，按头底面和垫圈叠放次序定位。"),
            "axis":derived_source(raw,"旧 Quick 实测紧固件沿 Z 轴装配。")
        },
        "relations":[]
    }))
}

fn requested_size(text: &str) -> Result<Option<(f64, f64)>, String> {
    let spec_pattern =
        regex::Regex::new(r"(?i)M\s*([0-9]+(?:\.[0-9]+)?)\s*x\s*([0-9]+(?:\.[0-9]+)?)")
            .expect("constant screw-size regex is valid");
    let mut explicit = spec_pattern
        .captures_iter(text)
        .filter_map(|capture| {
            Some((
                capture.get(1)?.as_str().parse::<f64>().ok()?,
                capture.get(2)?.as_str().parse::<f64>().ok()?,
            ))
        })
        .collect::<Vec<_>>();
    explicit.dedup_by(|a, b| (a.0 - b.0).abs() < 1e-8 && (a.1 - b.1).abs() < 1e-8);
    if explicit.len() > 1 {
        return Err("描述包含多个互相冲突的 M×长度规格；请只保留一个规格，例如 M5×8。".into());
    }
    if let Some(size) = explicit.first().copied() {
        return Ok(Some(size));
    }

    let diameter_pattern = regex::Regex::new(r"(?i)M\s*([0-9]+(?:\.[0-9]+)?)")
        .expect("constant metric diameter regex is valid");
    let diameters = diameter_pattern
        .captures_iter(text)
        .filter_map(|capture| capture.get(1)?.as_str().parse::<f64>().ok())
        .collect::<Vec<_>>();
    let Some(diameter) = diameters.first().copied() else {
        return Ok(None);
    };
    if diameters
        .iter()
        .any(|value| (value - diameter).abs() > 1e-8)
    {
        return Err("描述中出现多个公称直径；请明确唯一的 M 规格。".into());
    }
    let length = labeled_number(text, &["长度", "长", "length", "L"]);
    Ok(length.map(|length| (diameter, length)))
}

fn labeled_pitch(text: &str) -> Option<(f64, &'static str)> {
    let pattern = regex::Regex::new(r"(?i)(螺距|牙距|pitch)\s*(?:=|:)?\s*([0-9]+(?:\.[0-9]+)?)")
        .expect("constant thread-pitch regex is valid");
    let captures = pattern.captures(text)?;
    let label = match captures.get(1)?.as_str().to_lowercase().as_str() {
        "牙距" => "牙距",
        "pitch" => "螺距",
        _ => "螺距",
    };
    Some((captures.get(2)?.as_str().parse().ok()?, label))
}

fn labeled_number(text: &str, labels: &[&str]) -> Option<f64> {
    for label in labels {
        let pattern = format!(
            r"(?i){}\s*(?:=|:)?\s*([0-9]+(?:\.[0-9]+)?)",
            regex::escape(label)
        );
        let regex = regex::Regex::new(&pattern).ok()?;
        if let Some(value) = regex
            .captures(text)
            .and_then(|capture| capture.get(1))
            .and_then(|capture| capture.as_str().parse::<f64>().ok())
        {
            return Some(value);
        }
    }
    None
}

fn compact_number(value: f64) -> String {
    if (value - value.round()).abs() <= 1e-8 {
        format!("{:.0}", value)
    } else {
        format!("{value}")
    }
}

fn feature_id(id: usize) -> String {
    format!("quick-{id:04}")
}

fn contains_any(value: &str, needles: &[&str]) -> bool {
    needles.iter().any(|needle| value.contains(needle))
}

fn user_source(raw: &str, note: &str) -> Value {
    json!({"type":"user","evidence":raw,"note":note})
}

fn derived_source(raw: &str, note: &str) -> Value {
    json!({"type":"derived","evidence":raw,"note":note})
}

fn issue(code: &str, message: impl Into<String>) -> Value {
    json!({"code":code,"message":message.into()})
}

fn invalid_catalog(raw: &str, model: &str) -> Value {
    result(
        "unsupported",
        raw,
        Value::Null,
        vec![],
        vec![],
        vec![issue(
            "legacy_socket_bolt_catalog_invalid",
            format!("旧 Quick 中的 {model} 实测目录缺少有效尺寸；拒绝继续猜测模型参数。"),
        )],
    )
}

fn result(
    status: &str,
    source_text: &str,
    plan: Value,
    warnings: Vec<Value>,
    questions: Vec<Value>,
    unsupported: Vec<Value>,
) -> Value {
    json!({
        "status":status,
        "source_text":source_text,
        "plan":plan,
        "warnings":warnings,
        "questions":questions,
        "unsupported":unsupported,
        "compatibility":{
            "scope":"catalogued ISO 4762 / GB/T 70.1 socket-head screws from the legacy measured bolt library, including optional measured spring/flat washers",
            "legacy_geometry_difference":"legacy Python Quick reconstructed a smooth shank; native Rust adds visible faceted helical thread geometry",
            "thread_geometry":"faceted approximation, not an analytic helix or native CATIA thread"
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn measured_m5x8_uses_catalogued_washers_and_visible_thread_feature() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_socket_head_bolt_contract.json"
        ))
        .expect("socket-head screw oracle fixture is valid JSON");
        for case in fixture["native_rust_expected"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let parsed =
                parse(input).unwrap_or_else(|| panic!("socket screw was not claimed: {input}"));
            assert_eq!(parsed["status"], "ready", "{input}: {parsed}");
            let plan = &parsed["plan"];
            let validation = crate::cad_plan_validation::validate_plan(plan);
            assert_eq!(validation["state"], "valid", "{input}: {validation}");

            let features = plan["features"].as_array().unwrap();
            let expected_kinds = case["feature_kinds"].as_array().unwrap();
            assert_eq!(features.len(), expected_kinds.len(), "{input}");
            for (feature, expected_kind) in features.iter().zip(expected_kinds) {
                assert_eq!(feature["kind"], *expected_kind, "{input}");
            }

            let bolt = features.last().unwrap();
            for (parameter, expected) in [
                ("shank_d", 5.0),
                ("length", 8.0),
                ("head_d", 8.5),
                ("head_h", 5.0),
                ("hex_s", 4.0),
                ("thread_pitch", 0.8),
                ("hex_t", 2.5),
            ] {
                assert_eq!(
                    bolt["parameters"][parameter], expected,
                    "{input}: {parameter}"
                );
            }
            assert_eq!(bolt["sources"]["shank_d"]["type"], "user");
            assert_eq!(bolt["sources"]["length"]["type"], "user");
            assert_eq!(bolt["sources"]["thread_pitch"]["type"], "derived");
            assert!(
                parsed["warnings"]
                    .to_string()
                    .contains("legacy_socket_bolt_thread_enhancement")
            );
        }
    }

    #[test]
    fn socket_screw_requests_fail_closed_on_ambiguity_and_unmeasured_models() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_socket_head_bolt_contract.json"
        ))
        .unwrap();
        for case in fixture["needs_clarification"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let parsed = parse(input).unwrap();
            assert_eq!(parsed["status"], "needs_clarification", "{input}: {parsed}");
            assert!(
                parsed["questions"]
                    .to_string()
                    .contains(case["issue"].as_str().unwrap())
            );
            assert!(parsed["plan"].is_null());
        }
        for case in fixture["unsupported"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let parsed = parse(input).unwrap();
            assert_eq!(parsed["status"], "unsupported", "{input}: {parsed}");
            assert!(
                parsed["unsupported"]
                    .to_string()
                    .contains(case["issue"].as_str().unwrap())
            );
            assert!(parsed["plan"].is_null());
        }
    }

    #[test]
    fn metric_diameter_with_labeled_length_uses_the_same_measured_record() {
        let parsed = parse("M5 内六角圆柱头螺钉，长度8 mm").unwrap();
        assert_eq!(parsed["status"], "ready", "{parsed}");
        let bolt = parsed["plan"]["features"]
            .as_array()
            .unwrap()
            .last()
            .unwrap()
            .clone();
        assert_eq!(bolt["parameters"]["shank_d"], 5.0);
        assert_eq!(bolt["parameters"]["length"], 8.0);
        assert_eq!(parsed["plan"]["features"].as_array().unwrap().len(), 3);
    }

    #[test]
    fn a_socket_hole_in_another_part_does_not_claim_the_screw_recipe() {
        assert!(parse("支架正面开一个内六角孔，孔深4 mm").is_none());
    }
}
