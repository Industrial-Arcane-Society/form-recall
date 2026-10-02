//! Bounded, offline Quick parser for common CADPlan primitives and cuts.
//!
//! This intentionally does not port the legacy part-library/template matcher.
//! Missing sizes and ambiguous cut locations are surfaced to the caller.
use serde_json::{Value, json};

#[derive(Clone, Debug)]
struct Number {
    value: f64,
    unit: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum BaseKind {
    Box,
    Cylinder,
    Cone,
    Frustum,
    Sphere,
}

/// Parse a deliberately narrow Quick grammar and return a structured result.
/// `plan` is present only when every supported feature is explicit enough to
/// construct without legacy typical dimensions or guessed hole locations.
pub fn parse(text: &str) -> Value {
    let normalized = normalize(text);
    let mut questions = Vec::<Value>::new();
    let mut unsupported = Vec::<Value>::new();
    let mut warnings = vec![json!({
        "code":"legacy_implicit_dimensions_removed",
        "message":"Quick 不再沿用 legacy 的长方体/圆柱/圆锥/球体/孔/槽典型尺寸；缺少必需尺寸或孔位时返回澄清。"
    })];
    if text.trim().is_empty() {
        return result(
            "needs_clarification",
            text,
            None,
            warnings,
            vec![issue("empty_input", "请描述一个基础形体及其尺寸。")],
            unsupported,
        );
    }

    let clauses = split_clauses(&normalized);
    let mut base_kinds = Vec::new();
    for kind in [
        BaseKind::Box,
        BaseKind::Cylinder,
        BaseKind::Cone,
        BaseKind::Frustum,
        BaseKind::Sphere,
    ] {
        if contains_base(&normalized, kind) {
            base_kinds.push(kind);
        }
    }
    let base_kind = if base_kinds.len() == 1 {
        Some(base_kinds[0])
    } else if base_kinds.len() > 1 {
        unsupported.push(issue(
            "multiple_base_shapes",
            "Quick 当前一次只支持一个基础形体；本句包含多个基础形体词。",
        ));
        None
    } else {
        None
    };

    for (word, explanation) in [
        ("gear", "齿轮配方不在 Quick 基础形体范围内。"),
        ("齿轮", "齿轮配方不在 Quick 基础形体范围内。"),
        ("圆角", "圆角/倒角尚未迁入 Quick 基础解析范围。"),
        ("倒角", "圆角/倒角尚未迁入 Quick 基础解析范围。"),
        ("fillet", "圆角/倒角尚未迁入 Quick 基础解析范围。"),
        ("chamfer", "圆角/倒角尚未迁入 Quick 基础解析范围。"),
        ("array", "阵列尚未迁入 Quick 基础解析范围。"),
        ("阵列", "阵列尚未迁入 Quick 基础解析范围。"),
        ("thread", "螺纹紧固件尚未迁入 Quick 基础解析范围。"),
        ("螺纹", "螺纹紧固件尚未迁入 Quick 基础解析范围。"),
    ] {
        if normalized.contains(word) {
            unsupported.push(issue("unsupported_feature", explanation));
        }
    }
    if base_kind.is_none() && unsupported.is_empty() {
        let has_dimensions = find_dimension_triplet(&normalized).is_some()
            || [
                "length", "width", "height", "diameter", "radius", "长", "宽", "高", "直径", "半径",
            ]
            .iter()
            .any(|k| normalized.contains(k));
        if has_dimensions {
            questions.push(issue(
                "base_shape_missing",
                "已识别尺寸，但没有明确的长方体、圆柱、圆锥、圆台或球体类型。",
            ));
        } else {
            unsupported.push(issue(
                "unsupported_request",
                "没有识别到 Quick 当前支持的基础形体或切除要求。",
            ));
        }
    }

    let modifier_clauses: Vec<String> = clauses
        .iter()
        .filter(|c| has_hole(c) || has_slot(c))
        .cloned()
        .collect();
    let mut base_text = clauses
        .iter()
        .filter(|c| !has_hole(c) && !has_slot(c))
        .cloned()
        .collect::<Vec<_>>()
        .join(" ");
    if base_text.is_empty() {
        if let Some(kind) = base_kind {
            let marker = first_modifier_index(&normalized);
            base_text = marker
                .map(|i| normalized[..i].to_owned())
                .unwrap_or_else(|| normalized.clone());
            if !contains_base(&base_text, kind) {
                base_text = normalized.clone();
            }
        }
    } else if let Some(kind) = base_kind {
        if !contains_base(&base_text, kind) {
            let marker = first_modifier_index(&normalized).unwrap_or(normalized.len());
            base_text = normalized[..marker].to_owned();
        }
    }

    let mut features = Vec::<Value>::new();
    if let Some(kind) = base_kind {
        if let Some(base) = build_base(kind, &base_text, text, &mut questions, &mut warnings) {
            features.push(base);
        }
    }

    for (i, clause) in modifier_clauses.iter().enumerate() {
        let is_hole = has_hole(clause);
        let is_slot = has_slot(clause);
        if is_hole && is_slot {
            questions.push(issue(
                "mixed_cut_clause",
                "同一片段同时包含孔和槽；请用逗号/然后分开描述，以免混淆尺寸和位置。",
            ));
            continue;
        }
        let mut cut_text = clause.clone();
        if modifier_clauses.len() == 1 && clauses.len() == 1 {
            if let Some(start) = first_modifier_index(&normalized) {
                cut_text = normalized[start..].to_owned();
            }
        }
        if is_hole {
            if let Some(feature) = build_hole(&cut_text, text, i, &mut questions, &mut warnings) {
                features.push(feature);
            }
        } else if let Some(feature) = build_slot(&cut_text, text, i, &mut questions, &mut warnings)
        {
            features.push(feature);
        }
    }

    if !unsupported.is_empty() {
        return result("unsupported", text, None, warnings, questions, unsupported);
    }
    if base_kind.is_none() && questions.is_empty() {
        questions.push(issue("base_shape_missing", "请先明确需要创建的基础形体。"));
    }
    if !modifier_clauses.is_empty() && base_kind.is_none() {
        questions.push(issue(
            "cut_without_base",
            "孔或矩形槽需要明确一个待切除的基础形体。",
        ));
    }
    if !questions.is_empty() || features.is_empty() {
        if questions.is_empty() {
            questions.push(issue(
                "incomplete_request",
                "缺少可执行的完整基础特征参数。",
            ));
        }
        return result(
            "needs_clarification",
            text,
            None,
            warnings,
            questions,
            unsupported,
        );
    }

    let plan = json!({
        "schema_version":"1.0",
        "name":"Quick Part",
        "source_prompt":text,
        "units":{"length":"mm","angle":"deg"},
        "coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},
        "features":features,
        "pending_questions":[]
    });
    result("ready", text, Some(plan), warnings, questions, unsupported)
}

fn result(
    status: &str,
    text: &str,
    plan: Option<Value>,
    warnings: Vec<Value>,
    questions: Vec<Value>,
    unsupported: Vec<Value>,
) -> Value {
    json!({"status":status,"source_text":text,"plan":plan,"warnings":warnings,"questions":questions,"unsupported":unsupported,
        "compatibility":{"scope":"common explicit primitives and cuts only","legacy_typical_defaults":"intentionally removed; missing required values now need clarification"}})
}
fn issue(code: &str, message: &str) -> Value {
    json!({"code":code,"message":message})
}

fn feature(id: usize, kind: &str, operation: &str, parameters: Value, sources: Value) -> Value {
    json!({"id":format!("quick-{id:04}"),"kind":kind,"operation":operation,"parameters":parameters,"sources":sources,"relations":[]})
}
fn user_source(raw: &str) -> Value {
    json!({"type":"user","evidence":raw})
}
fn add_user(
    parameters: &mut serde_json::Map<String, Value>,
    sources: &mut serde_json::Map<String, Value>,
    raw: &str,
    key: &str,
    value: Value,
) {
    parameters.insert(key.into(), value);
    sources.insert(key.into(), user_source(raw));
}
fn derived_source(raw: &str, note: &str) -> Value {
    json!({"type":"derived","evidence":raw,"note":note})
}

fn build_base(
    kind: BaseKind,
    clause: &str,
    raw: &str,
    questions: &mut Vec<Value>,
    warnings: &mut Vec<Value>,
) -> Option<Value> {
    let mut parameters = serde_json::Map::new();
    let mut sources = serde_json::Map::new();
    let units_explicit = has_unit(clause);
    match kind {
        BaseKind::Box => {
            let triple = find_dimension_triplet(clause);
            let l = scalar(
                clause,
                &["length", "overall length", "长", "长度", "总长", "全长"],
            );
            let w = scalar(clause, &["width", "宽度", "宽"]);
            let h = scalar(clause, &["height", "高度", "高", "thickness", "厚度", "厚"]);
            let vals = [
                l.or_else(|| triple.as_ref().map(|t| t[0])),
                w.or_else(|| triple.as_ref().map(|t| t[1])),
                h.or_else(|| triple.as_ref().map(|t| t[2])),
            ];
            for (key, value) in ["length", "width", "height"].into_iter().zip(vals) {
                match value.filter(|v| v.is_finite() && *v > 0.0) {
                    Some(v) => add_user(&mut parameters, &mut sources, raw, key, json!(v)),
                    None => questions.push(issue(
                        "box_dimension_missing_or_invalid",
                        &format!("长方体缺少明确的正数 {key}；请给出长度、宽度、高度或 L×W×H。"),
                    )),
                }
            }
        }
        BaseKind::Cylinder => {
            let radius = scalar(clause, &["radius", "半径"]);
            let diameter = diameter_value(clause);
            let r = match (radius, diameter) {
                (Some(r), Some(d)) if !close(r * 2.0, d) => {
                    questions.push(issue(
                        "conflicting_diameter_radius",
                        "圆柱同时给出的半径和直径不一致。",
                    ));
                    None
                }
                (Some(r), _) => Some((r, false)),
                (None, Some(d)) => Some((d * 0.5, true)),
                _ => None,
            };
            match r.filter(|(v, _)| v.is_finite() && *v > 0.0) {
                Some((v, true)) => {
                    parameters.insert("radius".into(), json!(v));
                    sources.insert("radius".into(),derived_source(raw,"radius = explicit diameter / 2; the original diameter evidence is preserved."));
                }
                Some((v, false)) => {
                    add_user(&mut parameters, &mut sources, raw, "radius", json!(v))
                }
                None => questions.push(issue(
                    "cylinder_radius_missing",
                    "圆柱缺少明确的正半径或直径。",
                )),
            }
            match scalar(clause, &["height", "高度", "高", "length", "长度", "长"])
                .filter(|v| v.is_finite() && *v > 0.0)
            {
                Some(v) => add_user(&mut parameters, &mut sources, raw, "height", json!(v)),
                None => questions.push(issue(
                    "cylinder_height_missing",
                    "圆柱缺少明确的正高度/长度。",
                )),
            }
        }
        BaseKind::Cone | BaseKind::Frustum => {
            let base_d = scalar(
                clause,
                &[
                    "base diameter",
                    "bottom diameter",
                    "底部直径",
                    "底径",
                    "大端直径",
                ],
            )
            .or_else(|| {
                if kind == BaseKind::Cone {
                    diameter_value(clause)
                } else {
                    None
                }
            });
            let base_r = scalar(
                clause,
                &[
                    "base radius",
                    "bottom radius",
                    "底部半径",
                    "底半径",
                    "大端半径",
                ],
            );
            let (r, derived) = match (base_r, base_d) {
                (Some(r), Some(d)) if !close(r * 2.0, d) => {
                    questions.push(issue(
                        "conflicting_cone_base",
                        "圆锥/圆台底部半径与直径不一致。",
                    ));
                    (None, false)
                }
                (Some(r), _) => (Some(r), false),
                (None, Some(d)) => (Some(d * 0.5), true),
                _ => (None, false),
            };
            match r.filter(|v| v.is_finite() && *v > 0.0) {
                Some(v) if derived => {
                    parameters.insert("radius".into(), json!(v));
                    sources.insert(
                        "radius".into(),
                        derived_source(raw, "radius = explicit base diameter / 2."),
                    );
                }
                Some(v) => add_user(&mut parameters, &mut sources, raw, "radius", json!(v)),
                None => questions.push(issue(
                    "cone_base_missing",
                    "圆锥/圆台缺少明确的正底半径或底直径。",
                )),
            }
            let top_d = scalar(
                clause,
                &[
                    "top diameter",
                    "tip diameter",
                    "顶部直径",
                    "顶径",
                    "小端直径",
                ],
            );
            let top_r = scalar(
                clause,
                &["top radius", "tip radius", "顶部半径", "顶半径", "小端半径"],
            );
            if let (Some(a), Some(b)) = (top_d, top_r) {
                if !close(a * 0.5, b) {
                    questions.push(issue("conflicting_cone_top", "圆台顶部半径与直径不一致。"));
                }
            }
            if kind == BaseKind::Frustum && top_d.is_none() && top_r.is_none() {
                questions.push(issue(
                    "frustum_top_missing",
                    "圆台需要明确顶部半径或顶部直径；不会将其默认成尖顶。",
                ));
            } else if kind == BaseKind::Frustum {
                let (v, derived) = top_r
                    .map(|v| (v, false))
                    .or_else(|| top_d.map(|v| (v * 0.5, true)))
                    .unwrap();
                if !v.is_finite() || v <= 0.0 {
                    questions.push(issue("frustum_top_invalid", "圆台顶部半径必须为正数。"));
                } else if derived {
                    parameters.insert("radius2".into(), json!(v));
                    sources.insert(
                        "radius2".into(),
                        derived_source(raw, "radius2 = explicit top diameter / 2."),
                    );
                } else {
                    add_user(&mut parameters, &mut sources, raw, "radius2", json!(v));
                }
            } else {
                parameters.insert("radius2".into(), json!(0.0));
                sources.insert(
                    "radius2".into(),
                    derived_source(
                        raw,
                        "The explicit cone term defines a pointed top radius of zero.",
                    ),
                );
            }
            match scalar(clause, &["height", "高度", "高", "length", "长度", "长"])
                .filter(|v| v.is_finite() && *v > 0.0)
            {
                Some(v) => add_user(&mut parameters, &mut sources, raw, "height", json!(v)),
                None => questions.push(issue("cone_height_missing", "圆锥/圆台缺少明确的正高度。")),
            }
            if let Some(axis) = axis_value(clause, questions) {
                add_user(&mut parameters, &mut sources, raw, "axis", json!(axis));
            }
        }
        BaseKind::Sphere => {
            let r = scalar(clause, &["radius", "半径"]);
            let d = diameter_value(clause);
            let chosen = match (r, d) {
                (Some(r), Some(d)) if !close(r * 2., d) => {
                    questions.push(issue(
                        "conflicting_sphere_size",
                        "球体给出的半径和直径不一致。",
                    ));
                    None
                }
                (Some(r), _) => Some((r, false)),
                (None, Some(d)) => Some((d * 0.5, true)),
                _ => None,
            };
            match chosen.filter(|(v, _)| v.is_finite() && *v > 0.) {
                Some((v, true)) => {
                    parameters.insert("radius".into(), json!(v));
                    sources.insert(
                        "radius".into(),
                        derived_source(raw, "radius = explicit diameter / 2."),
                    );
                }
                Some((v, false)) => {
                    add_user(&mut parameters, &mut sources, raw, "radius", json!(v))
                }
                None => questions.push(issue(
                    "sphere_radius_missing",
                    "球体缺少明确的正半径或直径。",
                )),
            }
        }
    }
    if !units_explicit {
        warnings.push(issue(
            "unit_interpreted_as_mm",
            "未写长度单位的数值按 CADPlan 默认单位 mm 解释。",
        ));
    }
    if let Some(axis) = axis_value(clause, questions) {
        if kind == BaseKind::Box || kind == BaseKind::Cylinder || kind == BaseKind::Sphere {
            add_user(&mut parameters, &mut sources, raw, "axis", json!(axis));
        }
    }
    if let Some((center, source_text)) = center_coordinates(clause) {
        add_user(&mut parameters, &mut sources, raw, "center", json!(center));
        if text_contains_center_midpoint(clause)
            && (kind == BaseKind::Cylinder
                || kind == BaseKind::Cone
                || kind == BaseKind::Frustum
                || kind == BaseKind::Box)
        {
            parameters.insert("base".into(), json!(false));
            sources.insert("base".into(),derived_source(source_text,"A stated geometric center requires CADPlan base=false for axial/extrusion midpoint semantics."));
        }
    } else if has_center_location(clause) {
        questions.push(issue(
            "center_coordinates_missing",
            "描述了中心/坐标位置，但没有给出完整的三维坐标 (x,y,z)。",
        ));
    }
    let id = 1;
    let feature_kind = if kind == BaseKind::Box {
        "box"
    } else if kind == BaseKind::Sphere {
        "sphere"
    } else {
        "cylinder"
    };
    Some(feature(
        id,
        feature_kind,
        "add",
        Value::Object(parameters),
        Value::Object(sources),
    ))
}

fn build_hole(
    clause: &str,
    raw: &str,
    index: usize,
    questions: &mut Vec<Value>,
    warnings: &mut Vec<Value>,
) -> Option<Value> {
    let mut p = serde_json::Map::new();
    let mut s = serde_json::Map::new();
    let d = diameter_value(clause);
    let r = scalar(clause, &["radius", "半径"]);
    let (dia, derived) = match (d, r) {
        (Some(d), Some(r)) if !close(d, r * 2.) => {
            questions.push(issue(
                "conflicting_hole_size",
                "孔同时给出的半径与直径不一致。",
            ));
            (None, false)
        }
        (Some(d), _) => (Some(d), false),
        (None, Some(r)) => (Some(r * 2.), true),
        _ => (None, false),
    };
    match dia.filter(|v| v.is_finite() && *v > 0.) {
        Some(v) => {
            p.insert("diameter".into(), json!(v));
            s.insert(
                "diameter".into(),
                if derived {
                    derived_source(raw, "diameter = explicit hole radius × 2.")
                } else {
                    user_source(raw)
                },
            );
        }
        None => questions.push(issue("hole_diameter_missing", "孔缺少明确的正直径或半径。")),
    }
    let through_word = contains_any(clause, &["through", "thru", "通孔", "贯穿"]);
    let blind_word = contains_any(clause, &["blind", "盲孔"]);
    let depth = scalar(clause, &["depth", "hole depth", "深度", "孔深", "深"]);
    if through_word && blind_word {
        questions.push(issue("hole_type_conflict", "同一孔同时描述为通孔和盲孔。"));
    }
    let through = if through_word && depth.is_none() {
        Some(true)
    } else if through_word && depth.is_some() {
        questions.push(issue(
            "through_hole_has_depth",
            "通孔同时指定了盲孔深度；请确认只保留通孔或改为盲孔。",
        ));
        None
    } else if blind_word || depth.is_some() {
        Some(false)
    } else {
        questions.push(issue(
            "hole_type_missing",
            "请明确孔是通孔，或给出盲孔深度。",
        ));
        None
    };
    if let Some(v) = through {
        p.insert("through".into(), json!(v));
        s.insert(
            "through".into(),
            if through_word || blind_word {
                user_source(raw)
            } else {
                derived_source(
                    raw,
                    "An explicit depth without a through-hole term is interpreted as blind depth.",
                )
            },
        );
    }
    if let Some(v) = depth {
        if v.is_finite() && v > 0.0 {
            p.insert("depth".into(), json!(v));
            s.insert("depth".into(), user_source(raw));
        } else {
            questions.push(issue("hole_depth_invalid", "盲孔深度必须是正数。"));
        }
    }
    let center = has_center_location(clause);
    let coords = center_coordinates(clause);
    let named = named_xyz(clause);
    if center && (coords.is_some() || named.is_some()) {
        questions.push(issue(
            "hole_position_conflict",
            "孔同时描述了中心位置与坐标点。",
        ));
    }
    if center {
        p.insert("pos".into(), json!("center"));
        s.insert("pos".into(), user_source(raw));
    } else if let Some((point, _)) = coords.or(named) {
        p.insert("pos".into(), json!("point"));
        p.insert("point".into(), json!(point));
        s.insert("pos".into(), user_source(raw));
        s.insert("point".into(), user_source(raw));
    } else {
        questions.push(issue(
            "hole_position_missing",
            "孔没有明确中心位置或完整 (x,y,z) 坐标；Quick 不会默认在中心钻孔。",
        ));
    }
    if let Some(axis) = axis_value(clause, questions) {
        p.insert("axis".into(), json!(axis));
        s.insert("axis".into(), user_source(raw));
    }
    if !has_unit(clause) {
        warnings.push(issue(
            "unit_interpreted_as_mm",
            "未写长度单位的数值按 CADPlan 默认单位 mm 解释。",
        ));
    }
    Some(feature(
        index + 2,
        "hole",
        "cut",
        Value::Object(p),
        Value::Object(s),
    ))
}

fn build_slot(
    clause: &str,
    raw: &str,
    index: usize,
    questions: &mut Vec<Value>,
    warnings: &mut Vec<Value>,
) -> Option<Value> {
    if contains_any(clause, &["圆槽", "圆形槽", "circular slot"]) {
        questions.push(issue(
            "unsupported_slot_profile",
            "当前 Quick 只支持矩形槽，不支持圆槽。",
        ));
        return None;
    }
    let mut p = serde_json::Map::new();
    let mut s = serde_json::Map::new();
    let triple = find_dimension_triplet(clause);
    let vals = [
        scalar(clause, &["length", "长度", "长"]).or_else(|| triple.as_ref().map(|v| v[0])),
        scalar(clause, &["width", "宽度", "宽"]).or_else(|| triple.as_ref().map(|v| v[1])),
        scalar(clause, &["depth", "槽深", "深度", "深"]).or_else(|| triple.as_ref().map(|v| v[2])),
    ];
    for (key, value) in ["length", "width", "depth"].into_iter().zip(vals) {
        match value.filter(|v| v.is_finite() && *v > 0.) {
            Some(v) => {
                p.insert(key.into(), json!(v));
                s.insert(key.into(), user_source(raw));
            }
            None => questions.push(issue(
                "slot_dimension_missing_or_invalid",
                &format!("矩形槽缺少明确的正数 {key}；请给出长、宽、深或 L×W×D。"),
            )),
        }
    }
    if let Some(axis) = axis_value(clause, questions) {
        p.insert("axis".into(), json!(axis));
        s.insert("axis".into(), user_source(raw));
    }
    if let Some((point, _)) = center_coordinates(clause).or_else(|| named_xyz(clause)) {
        p.insert("x".into(), json!(point[0]));
        p.insert("y".into(), json!(point[1]));
        s.insert("x".into(), user_source(raw));
        s.insert("y".into(), user_source(raw));
    }
    if !has_unit(clause) {
        warnings.push(issue(
            "unit_interpreted_as_mm",
            "未写长度单位的数值按 CADPlan 默认单位 mm 解释。",
        ));
    }
    Some(feature(
        index + 2,
        "pocket",
        "cut",
        Value::Object(p),
        Value::Object(s),
    ))
}

fn normalize(text: &str) -> String {
    text.chars()
        .map(|c| match c {
            '０'..='９' => char::from_u32(c as u32 - 0xFEE0).unwrap_or(c),
            '．' => '.',
            '，' => ',',
            '；' => ';',
            '：' => ':',
            '（' => '(',
            '）' => ')',
            '／' => '/',
            '－' => '-',
            '＋' => '+',
            '×' | '＊' | 'Ｘ' | 'ｘ' => 'x',
            'Ø' | 'ø' | 'Φ' | 'φ' | 'ϕ' | '∅' | '⌀' | 'Ф' | 'ф' => '⌀',
            '　' => ' ',
            _ => c.to_ascii_lowercase(),
        })
        .collect()
}
fn split_clauses(s: &str) -> Vec<String> {
    let mut prepared = s
        .replace("然后", ";")
        .replace("接着", ";")
        .replace("之后", ";")
        .replace("并且", ";")
        .replace("随后", ";")
        .replace("再把", ";")
        .replace(" then ", ";")
        .replace(" and ", ";");
    prepared = prepared.replace("并", ";");
    let mut out = Vec::new();
    let mut buf = String::new();
    let mut depth = 0i32;
    for c in prepared.chars() {
        if c == '(' || c == '[' {
            depth += 1;
        }
        if c == ')' || c == ']' {
            depth = (depth - 1).max(0);
        }
        if depth == 0 && (c == ',' || c == ';' || c == '。' || c == '\n' || c == '\r') {
            let t = buf.trim();
            if !t.is_empty() {
                out.push(t.to_owned());
            }
            buf.clear();
        } else {
            buf.push(c);
        }
    }
    let t = buf.trim();
    if !t.is_empty() {
        out.push(t.to_owned());
    }
    out
}
fn contains_base(s: &str, k: BaseKind) -> bool {
    match k {
        BaseKind::Box => contains_any(s, &["长方体", "长方块", "矩形块", "cuboid", "box", "block"]),
        BaseKind::Cylinder => contains_any(s, &["圆柱体", "圆柱", "圆棒", "圆轴", "cylinder"]),
        BaseKind::Cone => contains_any(s, &["圆锥", "cone"]),
        BaseKind::Frustum => contains_any(s, &["圆台", "锥台", "frustum"]),
        BaseKind::Sphere => contains_any(s, &["球体", "圆球", "sphere", "ball"]),
    }
}
fn has_hole(s: &str) -> bool {
    contains_any(s, &["孔", "bore", "hole", "drill"])
}
fn has_slot(s: &str) -> bool {
    contains_any(s, &["槽", "slot", "pocket", "keyway"])
}
fn contains_any(s: &str, words: &[&str]) -> bool {
    words.iter().any(|w| s.contains(w))
}
fn first_modifier_index(s: &str) -> Option<usize> {
    [
        "通孔", "盲孔", "孔", "hole", "bore", "drill", "槽", "slot", "pocket", "keyway",
    ]
    .iter()
    .filter_map(|w| s.find(w))
    .min()
}
fn close(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-9_f64.max(a.abs().max(b.abs()) * 1e-9)
}

fn parse_number(s: &str, start: usize) -> Option<(Number, usize)> {
    let mut i = start;
    while let Some(c) = s[i..].chars().next() {
        if c.is_whitespace() || c == '=' || c == ':' || c == '为' {
            i += c.len_utf8();
        } else {
            break;
        }
    }
    let begin = i;
    let mut dot = false;
    if s[i..].starts_with('-') || s[i..].starts_with('+') {
        i += 1;
    }
    let mut digits = 0;
    while i < s.len() {
        let c = s[i..].chars().next()?;
        if c.is_ascii_digit() {
            digits += 1;
            i += c.len_utf8();
        } else if c == '.' && !dot {
            dot = true;
            i += 1;
        } else {
            break;
        }
    }
    if digits == 0 {
        return None;
    }
    let value = s[begin..i].parse::<f64>().ok()?;
    if !value.is_finite() {
        return None;
    }
    let (unit, end) = read_unit(s, i);
    Some((Number { value, unit }, end))
}
fn read_unit(s: &str, mut i: usize) -> (String, usize) {
    while i < s.len() {
        let c = s[i..].chars().next().unwrap();
        if c.is_whitespace() {
            i += c.len_utf8()
        } else {
            break;
        }
    }
    for u in [
        "毫米", "厘米", "分米", "英寸", "mm", "cm", "dm", "in", "米", "m",
    ] {
        if s[i..].starts_with(u) {
            return (u.into(), i + u.len());
        }
    }
    (String::new(), i)
}
fn scale(u: &str) -> f64 {
    match u {
        "mm" | "毫米" => 1.,
        "cm" | "厘米" => 10.,
        "dm" | "分米" => 100.,
        "m" | "米" => 1000.,
        "in" | "英寸" => 25.4,
        _ => 1.,
    }
}
fn find_dimension_triplet(s: &str) -> Option<[f64; 3]> {
    for (start, c) in s.char_indices() {
        if !c.is_ascii_digit() && c != '-' && c != '+' {
            continue;
        }
        let Some((a, e1)) = parse_number(s, start) else {
            continue;
        };
        let mut j = skip_ws(s, e1);
        if !s[j..].starts_with('x') {
            continue;
        }
        j += 1;
        let Some((b, e2)) = parse_number(s, j) else {
            continue;
        };
        j = skip_ws(s, e2);
        if !s[j..].starts_with('x') {
            continue;
        }
        j += 1;
        let Some((c, e3)) = parse_number(s, j) else {
            continue;
        };
        let mut vals = [
            a.value * scale(&a.unit),
            b.value * scale(if b.unit.is_empty() { &c.unit } else { &b.unit }),
            c.value * scale(&c.unit),
        ];
        if a.unit.is_empty() {
            vals[0] *= scale(&c.unit);
        }
        if b.unit.is_empty() {
            vals[1] = b.value * scale(&c.unit);
        }
        let _ = e3;
        return Some(vals);
    }
    None
}
fn scalar(s: &str, aliases: &[&str]) -> Option<f64> {
    let mut found = Vec::<f64>::new();
    for alias in aliases {
        for (i, _) in s.match_indices(alias) {
            if alias.len() == 1 && alias.chars().next().unwrap().is_ascii_alphabetic() {
                continue;
            }
            let prev = &s[..i];
            if *alias == "diameter"
                && contains_any(&prev[prev.len().saturating_sub(12)..], &["top ", "tip "])
            {
                continue;
            }
            if *alias == "直径" && prev.ends_with("顶") {
                continue;
            }
            let after = i + alias.len();
            let mut j = skip_ws(s, after);
            if s[j..].starts_with('⌀') || s[j..].starts_with('ø') || s[j..].starts_with('r') {
                j += s[j..].chars().next().unwrap().len_utf8();
            }
            if let Some((n, _)) = parse_number(s, j) {
                found.push(n.value * scale(&n.unit));
            }
        }
    }
    if found.is_empty() {
        None
    } else if found.iter().all(|v| close(*v, found[0])) {
        Some(found[0])
    } else {
        Some(f64::NAN)
    }
}
fn diameter_value(s: &str) -> Option<f64> {
    let mut found = Vec::new();
    for a in ["diameter", "dia", "直径", "外径", "内径", "⌀", "d="] {
        for (i, _) in s.match_indices(a) {
            if a == "diameter" && contains_any(&s[..i], &["top ", "tip "]) {
                continue;
            }
            if a == "直径" && s[..i].ends_with("顶") {
                continue;
            }
            let j = skip_ws(s, i + a.len());
            if let Some((n, _)) = parse_number(s, j) {
                found.push(n.value * scale(&n.unit));
            }
        }
    }
    if found.is_empty() {
        None
    } else if found.iter().all(|v| close(*v, found[0])) {
        Some(found[0])
    } else {
        Some(f64::NAN)
    }
}
fn skip_ws(s: &str, mut i: usize) -> usize {
    while i < s.len() {
        let c = s[i..].chars().next().unwrap();
        if c.is_whitespace() {
            i += c.len_utf8()
        } else {
            break;
        }
    }
    i
}
fn has_unit(s: &str) -> bool {
    ["mm", "cm", "dm", "in", "毫米", "厘米", "分米", "英寸", "米"]
        .iter()
        .any(|u| s.contains(u))
}

fn axis_value(s: &str, questions: &mut Vec<Value>) -> Option<&'static str> {
    let mut axes = Vec::new();
    for (axis, words) in [
        (
            "X",
            &["沿x", "x方向", "x轴", "x-axis", "along x", "horizontal"][..],
        ),
        ("Y", &["沿y", "y方向", "y轴", "y-axis", "along y"][..]),
        (
            "Z",
            &[
                "沿z",
                "z方向",
                "z轴",
                "z-axis",
                "along z",
                "vertical",
                "竖直",
                "垂直方向",
            ][..],
        ),
    ] {
        if contains_any(s, &words) {
            axes.push(axis)
        }
    }
    axes.sort_unstable();
    axes.dedup();
    if axes.len() > 1 {
        questions.push(issue("axis_conflict", "同一特征指定了多个轴向。"));
        None
    } else {
        axes.first().copied()
    }
}
fn has_center_location(s: &str) -> bool {
    contains_any(s, &["center", "centre", "middle", "中心", "中间", "正中"])
}
fn text_contains_center_midpoint(s: &str) -> bool {
    has_center_location(s)
}
fn center_coordinates(s: &str) -> Option<([f64; 3], &str)> {
    for (i, c) in s.char_indices() {
        if c != '(' && c != '[' {
            continue;
        }
        let close = if c == '(' { ')' } else { ']' };
        let end = s[i + c.len_utf8()..].find(close)? + i + c.len_utf8();
        let prior = &s[..i];
        if !contains_any(
            prior,
            &["center", "centre", "坐标", "位置", "at ", "position"],
        ) {
            continue;
        }
        let inner = &s[i + c.len_utf8()..end];
        let mut nums = Vec::new();
        let mut j = 0;
        while j < inner.len() {
            if inner[j..].starts_with(',') {
                j += 1;
                continue;
            }
            let Some((n, e)) = parse_number(inner, j) else {
                break;
            };
            nums.push(n.value * scale(&n.unit));
            j = e;
            while j < inner.len() && inner[j..].chars().next().unwrap().is_whitespace() {
                j += inner[j..].chars().next().unwrap().len_utf8();
            }
        }
        if nums.len() == 3 {
            return Some(([nums[0], nums[1], nums[2]], s));
        }
    }
    named_xyz(s).map(|(p, e)| (p, e))
}
fn named_xyz(s: &str) -> Option<([f64; 3], &str)> {
    let mut vals = [None; 3];
    for (i, key) in ['x', 'y', 'z'].iter().enumerate() {
        for (pos, _) in s.match_indices(*key) {
            let mut j = skip_ws(s, pos + 1);
            if !s[j..].starts_with('=') && !s[j..].starts_with(':') {
                continue;
            }
            j += 1;
            if let Some((n, _)) = parse_number(s, j) {
                vals[i] = Some(n.value * scale(&n.unit));
            }
        }
    }
    Some(([vals[0]?, vals[1]?, vals[2]?], s))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn plan(text: &str) -> Value {
        let out = parse(text);
        assert_eq!(out["status"], "ready", "{out}");
        out["plan"].clone()
    }
    #[test]
    fn fullwidth_dimensions_and_units_are_normalized_without_losing_prompt() {
        let out = parse("创建 100×60×20毫米 长方体");
        assert_eq!(out["source_text"], "创建 100×60×20毫米 长方体");
        assert_eq!(out["plan"]["features"][0]["parameters"]["length"], 100.0);
        assert_eq!(out["plan"]["features"][0]["parameters"]["height"], 20.0);
    }
    #[test]
    fn inch_and_centimeter_scaling_are_applied_to_explicit_values() {
        assert_eq!(
            plan("box length 10cm width 2in height 5mm")["features"][0]["parameters"]["width"],
            50.8
        );
    }
    #[test]
    fn diameter_to_radius_is_derived_and_keeps_user_evidence() {
        let p = plan("cylinder diameter 30 mm height 50 mm");
        assert_eq!(p["features"][0]["parameters"]["radius"], 15.0);
        assert_eq!(p["features"][0]["sources"]["radius"]["type"], "derived");
        assert!(
            p["features"][0]["sources"]["radius"]["evidence"]
                .as_str()
                .unwrap()
                .contains("diameter 30 mm")
        );
    }
    #[test]
    fn through_blind_hole_and_slot_support_multiple_clauses() {
        let p = plan(
            "box 100×60×20 mm, center through hole diameter 10 mm, blind hole at (10,0,10) diameter 4 mm depth 3 mm, rectangular slot 20×8×2 mm",
        );
        assert_eq!(p["features"].as_array().unwrap().len(), 4);
        assert_eq!(p["features"][1]["parameters"]["through"], true);
        assert_eq!(
            p["features"][2]["parameters"]["point"],
            json!([10.0, 0.0, 10.0])
        );
        assert_eq!(p["features"][3]["kind"], "pocket");
    }
    #[test]
    fn absent_dimensions_and_ambiguous_hole_location_request_clarification() {
        assert_eq!(parse("make a box")["status"], "needs_clarification");
        assert_eq!(
            parse("box 100×60×20 mm, through hole diameter 10 mm")["status"],
            "needs_clarification"
        );
    }
    #[test]
    fn contradictory_sizes_and_unsupported_operations_fail_closed() {
        assert_eq!(
            parse("box length 100mm length 120mm width 60mm height 20mm")["status"],
            "needs_clarification"
        );
        assert_eq!(
            parse("box 100×60×20 with gear teeth")["status"],
            "unsupported"
        );
    }
    #[test]
    fn cone_and_frustum_semantics_are_explicit() {
        let cone = plan("圆锥底径30mm高50mm");
        assert_eq!(cone["features"][0]["parameters"]["radius"], 15.0);
        assert_eq!(cone["features"][0]["parameters"]["radius2"], 0.0);
        assert_eq!(
            parse("frustum bottom diameter 30mm height 20mm")["status"],
            "needs_clarification"
        );
    }
}
