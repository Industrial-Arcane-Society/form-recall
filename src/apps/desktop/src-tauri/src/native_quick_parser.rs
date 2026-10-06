//! Bounded, offline Quick parser for common CADPlan primitives, selected
//! executable legacy templates, and cuts. Legacy template defaults are kept
//! only for the explicitly ported templates and are returned as warnings.
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
    Prism,
    Revolve,
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
        "message":"仅已迁移的旧 Quick 基础体、拉伸/回转、模板和矩形槽规则沿用默认值，并逐项说明；其余缺少必需尺寸或孔位时返回澄清。"
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

    if let Some(parsed) = parse_legacy_template(text, &normalized) {
        return parsed;
    }
    if let Some(parsed) = parse_legacy_gear(text, &normalized) {
        return parsed;
    }

    let mut clauses = split_clauses(&normalized);
    let mut clause_index = 1;
    while clause_index < clauses.len() {
        let previous = &clauses[clause_index - 1];
        let current = &clauses[clause_index];
        if has_hole(previous)
            && contains_any(previous, &["blind", "盲孔"])
            && !contains_any(previous, &["through", "通孔", "贯穿"])
            && hole_depth_value(previous).is_none()
            && is_standalone_depth_clause(current)
        {
            let continuation = clauses.remove(clause_index);
            clauses[clause_index - 1].push(' ');
            clauses[clause_index - 1].push_str(&continuation);
            continue;
        }
        if has_hole(previous) && is_standalone_depth_clause(current) {
            questions.push(issue(
                "hole_depth_continuation_unbound",
                "深度片段只有紧跟一个未标深度的盲孔时才会接入该孔；当前片段无法安全绑定，请合并重述孔型和深度。",
            ));
        }
        if has_hole(&clauses[clause_index - 1])
            && contains_any(
                &clauses[clause_index],
                &[
                    "edge margin",
                    "edge distance",
                    "margin",
                    "边距",
                    "距边",
                    "离边",
                    "留边",
                    "内缩",
                ],
            )
        {
            let continuation = clauses.remove(clause_index);
            clauses[clause_index - 1].push(' ');
            clauses[clause_index - 1].push_str(&continuation);
        } else {
            clause_index += 1;
        }
    }
    let mut base_kinds = Vec::new();
    if legacy_revolve_requested(&normalized) {
        base_kinds.push(BaseKind::Revolve);
    } else if extrusion_requested(&normalized) {
        base_kinds.push(BaseKind::Prism);
    } else {
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
        if base_kinds.is_empty() && legacy_plain_cylinder_requested(&normalized) {
            base_kinds.push(BaseKind::Cylinder);
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

    for clause in clauses.iter().filter(|clause| has_hole(clause) && has_corner_location(clause)) {
        if base_kind != Some(BaseKind::Box) {
            unsupported.push(issue(
                "corner_holes_require_box",
                "Quick 四角孔与边距语法当前仅支持长方体基础形体。",
            ));
        }
        if contains_any(
            clause,
            &["pcd", "分度圆", "均布", "equally spaced", "孔数", "phase", "相位"],
        ) {
            unsupported.push(issue(
                "corner_hole_pattern_unsupported",
                "四角孔当前固定生成四孔；与 PCD/均布/孔数阵列语法组合尚未支持。",
            ));
        }
    }

    let circular_pattern_requested =
        contains_any(&normalized, &["圆周阵列", "环形阵列"]);
    for (word, explanation) in [
        ("gear", "齿轮配方不在 Quick 基础形体范围内。"),
        ("齿轮", "齿轮配方不在 Quick 基础形体范围内。"),
        ("array", "当前仅支持显式线性阵列；圆周及其他阵列语法尚未支持。"),
        ("阵列", "当前仅支持显式线性阵列；圆周及其他阵列语法尚未支持。"),
        ("thread", "螺纹紧固件尚未迁入 Quick 基础解析范围。"),
        ("螺纹", "螺纹紧固件尚未迁入 Quick 基础解析范围。"),
        ("扩孔", "Quick 当前只支持显式中心通孔带沉孔；扩孔语法尚未迁入。"),
        ("沉台", "Quick 沉台语法尚未迁入。"),
        ("沉坑", "Quick 沉坑语法尚未迁入。"),
        ("锪孔", "Quick 锪孔语法尚未迁入。"),
        ("埋头孔", "Quick 埋头孔语法尚未迁入。"),
        (
            "叶片",
            "旧 Quick 可解析叶片放样，但当前原生几何执行器不支持 loft；已拒绝，不生成空模型。",
        ),
        (
            "blade",
            "旧 Quick 可解析叶片放样，但当前原生几何执行器不支持 loft；已拒绝，不生成空模型。",
        ),
        (
            "叶盘",
            "旧 Quick 可解析叶盘，但当前原生几何执行器不支持 loft；已拒绝，不生成不完整模型。",
        ),
        (
            "叶轮",
            "叶轮模板当前依赖未迁入的叶片/放样几何；Quick 已拒绝，不生成不完整模型。",
        ),
        (
            "blisk",
            "旧 Quick 可解析叶盘，但当前原生几何执行器不支持 loft；已拒绝，不生成不完整模型。",
        ),
    ] {
        let explicit_linear_array = contains_any(&normalized, &["阵列", "linear array"])
            && regex::Regex::new(r"(?:阵列|linear\s+array)\s*[0-9]+\s*(?:个|份|次)?")
                .is_ok_and(|re| re.is_match(&normalized));
        if normalized.contains(word)
            && !((explicit_linear_array || circular_pattern_requested)
                && (word == "array" || word == "阵列"))
        {
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

    let fillet_requested = contains_any(&base_text, &["圆角", "倒圆", "圆滑", "fillet"]);
    let chamfer_requested = contains_any(&base_text, &["倒角", "切角", "chamfer"]);
    if (fillet_requested || chamfer_requested) && base_kind != Some(BaseKind::Box) {
        unsupported.push(issue(
            "corner_modifier_requires_box",
            "旧 Python Quick 圆角/倒角轮廓目前仅迁入长方体基础形体；当前特征未生成。",
        ));
    }
    if fillet_requested && chamfer_requested {
        questions.push(issue(
            "box_corner_mode_conflict",
            "同一长方体不能同时指定圆角和倒角；请保留一种轮廓方式。",
        ));
    }

    let mut features = Vec::<Value>::new();
    if let Some(kind) = base_kind {
        if let Some(base) = build_base(
            kind,
            &base_text,
            text,
            &mut questions,
            &mut warnings,
            modifier_clauses.is_empty(),
        ) {
            features.push(base);
        }
    }

    for clause in &modifier_clauses {
        let feature_index = features.len().saturating_sub(1);
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
            if clause.contains("沉孔") {
                let center_requested = contains_any(clause, &["中心孔", "中心通孔"]);
                let pcd_marker = contains_any(clause, &["pcd", "分度圆", "节圆", "分布圆"]);
                let pattern_modifier = contains_any(clause, &["均布", "孔数", "phase", "相位"]);
                let pcd_counterbore = pcd_marker && !center_requested;
                let center_counterbore = center_requested && !pcd_marker && !pattern_modifier;
                let prior_corner_pilot = features.last().is_some_and(|feature| {
                    feature["kind"] == "hole"
                        && feature["operation"] == "cut"
                        && feature["parameters"]["pos"] == "corners"
                        && feature["parameters"]["through"] == true
                });
                let corner_counterbore = has_corner_location(clause) || prior_corner_pilot;
                let unsupported_location_or_combination = !(center_counterbore
                    || pcd_counterbore
                    || corner_counterbore)
                    || (corner_counterbore
                        && (center_requested || pcd_marker || pattern_modifier))
                    || contains_any(
                        clause,
                        &[
                            "坐标",
                            "coordinate",
                            "position",
                            "位置",
                        ],
                    )
                    || (!corner_counterbore
                        && contains_any(
                            clause,
                            &[
                                "边距",
                                "距边",
                                "离边",
                                "留边",
                                "内缩",
                            ],
                        ))
                    || center_coordinates(clause).is_some()
                    || named_xyz(clause).is_some()
                    || clause.contains('(')
                    || clause.contains('[')
                    || (!corner_counterbore && modifier_clauses.len() != 1);
                if unsupported_location_or_combination {
                    unsupported.push(issue(
                        "counterbore_variant_unsupported",
                        "Quick 沉孔当前只支持中心孔、PCD 均布孔和继承的四角孔；坐标及混合位置尚未支持。",
                    ));
                    continue;
                }
                if corner_counterbore {
                    let counterbore_features = build_corner_counterbore(
                        &cut_text,
                        text,
                        feature_index,
                        &mut features,
                        &mut questions,
                        &mut warnings,
                    );
                    features.extend(counterbore_features);
                } else if pcd_counterbore {
                    features.extend(build_pcd_counterbore(
                        &cut_text,
                        text,
                        feature_index,
                        &mut questions,
                        &mut warnings,
                    ));
                } else {
                    features.extend(build_center_counterbore(
                        &cut_text,
                        text,
                        feature_index,
                        &mut questions,
                        &mut warnings,
                    ));
                }
                continue;
            }
            if let Some(feature) = build_hole(
                &cut_text,
                text,
                feature_index,
                &mut questions,
                &mut warnings,
            ) {
                features.push(feature);
            }
        } else if let Some(slot_features) = build_slot(
            &cut_text,
            text,
            feature_index,
            &mut questions,
            &mut warnings,
        ) {
            features.extend(slot_features);
        }
    }

    let linear_pattern_requested = contains_any(&normalized, &["阵列", "linear array"])
        && !circular_pattern_requested;
    if linear_pattern_requested {
        let count = regex::Regex::new(r"(?:阵列|linear\s+array)\s*([0-9]+)\s*(?:个|份|次)?")
            .ok()
            .and_then(|re| re.captures(&normalized))
            .and_then(|captures| captures.get(1))
            .and_then(|value| value.as_str().parse::<u64>().ok());
        let axis = axis_value(&normalized, &mut questions);
        let step = scalar(&normalized, &["spacing", "间距", "步距"]);
        match (count, axis, step) {
            (Some(count), Some(axis), Some(step)) if count >= 2 && count <= crate::native_plan_geometry::MAX_HOLE_PATTERN_COUNT && step.is_finite() && step > 0.0 => {
                if let Some(source) = features.last() {
                    let source_id = source["id"].as_str().unwrap_or("").to_owned();
                    let operation = source["operation"].as_str().unwrap_or("add").to_owned();
                    let vector = match axis { "X" => json!([step, 0.0, 0.0]), "Y" => json!([0.0, step, 0.0]), _ => json!([0.0, 0.0, step]) };
                    features.push(json!({
                        "id":format!("quick-{:04}", features.len() + 1), "kind":"pattern",
                        "operation":operation, "name":"线性阵列",
                        "parameters":{"mode":"linear", "count":count, "axis":axis, "step":vector},
                        "sources":{"count":user_source(text), "axis":user_source(text), "step":user_source(text)},
                        "relations":[{"type":"pattern_source", "feature_id":source_id}]
                    }));
                } else {
                    questions.push(issue("linear_pattern_source_missing", "线性阵列前必须先定义一个基础或切除特征。"));
                }
            }
            _ => questions.push(issue("linear_pattern_needs_explicit_values", "线性阵列需明确阵列数量、X/Y/Z 方向和正间距。")),
        }
    }

    if circular_pattern_requested {
        let count = regex::Regex::new(r"(?:圆周阵列|环形阵列)\s*([0-9]+)\s*(?:个|份|次)?")
            .ok()
            .and_then(|re| re.captures(&normalized))
            .and_then(|captures| captures.get(1))
            .and_then(|value| value.as_str().parse::<u64>().ok());
        let axis = axis_value(&normalized, &mut questions);
        let angle = circular_pattern_angle(&normalized);
        match (count, axis, angle) {
            (Some(count), Some(axis), Ok(Some(angle)))
                if (2..=crate::native_plan_geometry::MAX_HOLE_PATTERN_COUNT).contains(&count)
                    && angle.is_finite()
                    && angle > 0.0 =>
            {
                if let Some(source) = features.last() {
                    let source_id = source["id"].as_str().unwrap_or("").to_owned();
                    let operation = source["operation"].as_str().unwrap_or("add").to_owned();
                    features.push(json!({
                        "id":format!("quick-{:04}", features.len() + 1),
                        "kind":"pattern",
                        "operation":operation,
                        "name":"圆周阵列",
                        "parameters":{
                            "mode":"circular",
                            "count":count,
                            "axis":axis,
                            "angle":angle,
                            "full":(angle - 360.0).abs() < 1e-6,
                            "center":[0.0, 0.0, 0.0]
                        },
                        "sources":{
                            "count":user_source(text),
                            "axis":user_source(text),
                            "angle":user_source(text),
                            "full":derived_source(text, "根据圆周阵列角度推导是否为完整圆周。"),
                            "center":derived_source(text, "沿用 Python Quick 圆周阵列的全局原点旋转中心。")
                        },
                        "relations":[{"type":"pattern_source", "feature_id":source_id}]
                    }));
                } else {
                    questions.push(issue(
                        "circular_pattern_source_missing",
                        "圆周阵列前必须先定义一个基础或切除特征。",
                    ));
                }
            }
            (_, _, Err("radians")) => questions.push(issue(
                "circular_pattern_angle_unit_unsupported",
                "圆周阵列角度只接受度（°/deg/度），不接受弧度。",
            )),
            (_, _, Err("conflict")) => questions.push(issue(
                "circular_pattern_angle_conflict",
                "圆周阵列描述了互相冲突的角度值。",
            )),
            (Some(count), _, _) if count < 2 || count > crate::native_plan_geometry::MAX_HOLE_PATTERN_COUNT => {
                questions.push(issue(
                    "circular_pattern_count_invalid",
                    format!(
                        "圆周阵列数量必须为 2 到 {} 之间的整数。",
                        crate::native_plan_geometry::MAX_HOLE_PATTERN_COUNT
                    ),
                ));
            }
            (Some(_), Some(_), Ok(Some(angle))) if angle <= 0.0 => questions.push(issue(
                "circular_pattern_angle_invalid",
                "圆周阵列角度必须是有限的正数。",
            )),
            (_, _, Ok(Some(_))) => questions.push(issue(
                "circular_pattern_values_missing",
                "圆周阵列需明确数量、X/Y/Z 旋转轴和正角度。",
            )),
            _ => questions.push(issue(
                "circular_pattern_values_missing",
                "圆周阵列需明确数量、X/Y/Z 旋转轴和正角度。",
            )),
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
        "compatibility":{"scope":"common explicit primitives/cuts, migrated standalone primitive defaults and legacy extrusion/revolve constructors, rectangular slots, straight-gear construction, PCD counterbores, and executable flange/stepped-shaft/sleeve templates","legacy_typical_defaults":"restored only for migrated Quick constructors/templates, straight gears, and rectangular slots; defaults and raw-dimension mappings are reported as warnings"}})
}

fn parse_legacy_template(raw: &str, normalized: &str) -> Option<Value> {
    if contains_any(normalized, &["阶梯轴", "台阶轴", "stepped shaft"]) {
        return Some(parse_stepped_shaft(raw, normalized));
    }
    if contains_any(normalized, &["法兰", "flange"]) {
        return Some(parse_flange(raw, normalized));
    }
    if contains_any(normalized, &["套筒", "圆管", "钢管", "sleeve", "tube"]) {
        return Some(parse_tube(raw, normalized));
    }
    None
}

/// Retain the old Quick `make_gear` constructor. Its tooth profile is a
/// straight trapezoidal approximation, not an involute gear.
fn parse_legacy_gear(raw: &str, normalized: &str) -> Option<Value> {
    if !contains_any(normalized, &["齿轮", "齿坯", "gear"]) {
        return None;
    }
    // Mixed base requests stay in the generic fail-closed parser.
    if contains_any(
        normalized,
        &[
            "长方体", "长方块", "矩形块", "cuboid", "box", "block", "圆柱", "圆棒", "圆轴",
            "cylinder", "圆锥", "cone", "圆台", "锥台", "frustum", "球体", "圆球", "sphere",
            "ball", "拉伸", "挤出", "extrude", "pad", "回转", "旋转体", "revolve",
        ],
    ) {
        return None;
    }

    if contains_any(
        normalized,
        &[
            "锥齿轮", "伞齿轮", "蜗轮", "蜗杆", "斜齿轮", "螺旋齿轮", "内齿轮", "bevel gear",
            "worm gear", "helical gear", "internal gear",
        ],
    ) {
        return Some(result(
            "unsupported",
            raw,
            None,
            vec![],
            vec![],
            vec![issue(
                "special_gear_type_unsupported",
                "当前 Quick 只迁入旧版直齿轮构造器；锥齿轮、斜齿轮、内齿轮和蜗轮/蜗杆需要各自的几何定义。",
            )],
        ));
    }
    if contains_any(
        normalized,
        &[
            "圆角", "倒角", "fillet", "chamfer", "键槽", "凹槽", "切槽", "螺纹", "thread",
            "pcd", "分度圆孔", "螺栓孔", "圆周阵列", "环形阵列", "linear array", "线性阵列",
        ],
    ) {
        return Some(result(
            "unsupported",
            raw,
            None,
            vec![],
            vec![],
            vec![issue(
                "gear_modifier_unsupported",
                "本次只迁入旧 Quick 齿轮主体与中心通孔；齿轮上的其他修饰/阵列尚未迁入。",
            )],
        ));
    }

    let mut warnings = vec![issue(
        "legacy_gear_profile_approximation",
        "沿用旧 Quick 的梯形离散齿廓；它不是渐开线齿形，当前本地几何为近似。",
    )];

    let mut questions = Vec::new();
    let mut features = Vec::new();
    let teeth_value = scalar(normalized, &["齿数", "teeth", "tooth count"])
        .or_else(|| scalar(normalized, &["count"]))
        .or_else(|| {
            regex::Regex::new(r"([0-9]+)\s*(?:个\s*)?齿")
                .ok()?
                .captures(normalized)?
                .get(1)?
                .as_str()
                .parse::<f64>()
                .ok()
        });
    let teeth = match teeth_value {
        Some(value)
            if value.is_finite() && value.fract() == 0.0 && (6.0..=10_000.0).contains(&value) =>
        {
            value as u64
        }
        Some(value) => {
            questions.push(issue(
                "gear_teeth_invalid",
                format!("齿数必须是 6 到 10000 之间的整数；收到 {value}。"),
            ));
            20
        }
        None => {
            warnings.push(issue(
                "legacy_gear_default_teeth",
                "未指定齿数，沿用旧 Python Quick 默认值 20 齿。",
            ));
            20
        }
    };

    let mut outer_diameter = scalar(
        normalized,
        &[
            "齿顶圆直径", "齿顶圆", "轮缘外径", "外径", "外圆直径", "头径", "outer diameter",
            "gear diameter",
        ],
    );
    let bore_marked = contains_any(
        normalized,
        &["中心孔", "中心通孔", "内孔", "轴孔", "内径", "孔径", "bore", "shaft hole"],
    );
    if outer_diameter.is_none() && !bore_marked {
        outer_diameter = scalar(normalized, &["直径", "diameter"]);
    }
    if outer_diameter.is_some_and(|value| !value.is_finite() || value <= 0.0) {
        questions.push(issue(
            "gear_outer_diameter_invalid",
            "齿轮齿顶圆/外径必须是有限正数。",
        ));
    }

    let module_value = scalar(normalized, &["模数", "module"]);
    let module = match module_value {
        Some(value) if value.is_finite() && value > 0.0 => value,
        Some(value) => {
            questions.push(issue(
                "gear_module_invalid",
                format!("齿轮模数必须是有限正数；收到 {value}。"),
            ));
            2.0
        }
        None => match outer_diameter {
            Some(diameter)
                if diameter.is_finite()
                    && diameter > 0.0
                    && teeth_value.map_or(true, |v| {
                        v.is_finite() && v.fract() == 0.0 && (6.0..=10_000.0).contains(&v)
                    }) =>
            {
                let derived = diameter / (teeth as f64 + 2.0);
                warnings.push(issue(
                    "legacy_gear_module_derived",
                    format!("未指定模数，沿用旧 Python Quick 公式：模数 = 齿顶圆直径 / (齿数 + 2) = {derived} mm。"),
                ));
                derived
            }
            Some(_) => 2.0,
            None => {
                warnings.push(issue(
                    "legacy_gear_default_module",
                    "未指定模数或齿顶圆直径，沿用旧 Python Quick 默认值 2 mm。",
                ));
                2.0
            }
        },
    };

    let height_value = scalar(
        normalized,
        &["height", "高度", "厚度", "厚", "高", "齿宽", "face width"],
    );
    let height = match height_value {
        Some(value) if value.is_finite() && value > 0.0 => value,
        Some(value) => {
            questions.push(issue(
                "gear_height_invalid",
                format!("齿轮厚度必须是有限正数；收到 {value}。"),
            ));
            10.0
        }
        None => {
            warnings.push(issue(
                "legacy_gear_default_height",
                "未指定厚度，沿用旧 Python Quick 默认值 10 mm。",
            ));
            10.0
        }
    };
    if height_value.is_some() && !has_unit(normalized) {
        warnings.push(issue(
            "unit_interpreted_as_mm",
            "未写长度单位的齿轮厚度按 CADPlan 默认单位 mm 解释。",
        ));
    }

    let mut axis_questions = Vec::new();
    let explicit_axis = axis_value(normalized, &mut axis_questions);
    let axis = explicit_axis.unwrap_or("Z");
    questions.extend(axis_questions);
    if axis != "Z" {
        return Some(result(
            "unsupported",
            raw,
            None,
            warnings,
            questions,
            vec![issue(
                "gear_axis_unsupported",
                "当前 Quick 齿轮 CADPlan 仅支持全局 Z 轴；请移除 X/Y 轴向要求。",
            )],
        ));
    }

    let mut parameters = serde_json::Map::new();
    let mut sources = serde_json::Map::new();
    parameters.insert("teeth".into(), json!(teeth));
    sources.insert(
        "teeth".into(),
        teeth_value.map_or_else(
            || derived_source(raw, "沿用旧 Python Quick 默认值 20 齿。"),
            |_| user_source(raw),
        ),
    );
    parameters.insert("module".into(), json!(module));
    sources.insert(
        "module".into(),
        if module_value.is_some() {
            user_source(raw)
        } else if outer_diameter.is_some()
            && !questions
                .iter()
                .any(|question| question["code"] == "gear_outer_diameter_invalid")
        {
            derived_source(raw, "未给模数时沿用旧 Python Quick：module = outer_diameter / (teeth + 2)。")
        } else {
            derived_source(raw, "沿用旧 Python Quick 默认值 2 mm。")
        },
    );
    parameters.insert("height".into(), json!(height));
    sources.insert(
        "height".into(),
        height_value.map_or_else(
            || derived_source(raw, "沿用旧 Python Quick 默认值 10 mm。"),
            |_| user_source(raw),
        ),
    );
    parameters.insert("center".into(), json!([0.0, 0.0, 0.0]));
    sources.insert("center".into(), derived_source(raw, "沿用旧 Python Quick 齿轮全局原点中心。"));
    parameters.insert("axis".into(), json!("Z"));
    sources.insert(
        "axis".into(),
        explicit_axis.map_or_else(
            || derived_source(raw, "未指定轴向时沿用旧 Python Quick 默认 Z 轴。"),
            |_| user_source(raw),
        ),
    );
    parameters.insert("base".into(), json!(true));
    sources.insert(
        "base".into(),
        derived_source(raw, "沿用旧 Python Quick base=true，齿轮从中心平面沿轴向正向拉伸。"),
    );
    features.push(feature(1, "gear", "add", Value::Object(parameters), Value::Object(sources)));

    let bore_value = if bore_marked {
        scalar(
            normalized,
            &[
                "中心孔直径", "中心通孔直径", "内孔直径", "轴孔直径", "通孔直径", "孔径", "内径",
                "bore diameter", "shaft hole diameter", "bore",
            ],
        )
    } else {
        None
    };
    if bore_marked {
        match bore_value {
            Some(value) if value.is_finite() && value > 0.0 => {
                let clause = format!("中心孔 通孔 直径 {value}");
                if let Some(mut hole) = build_hole(&clause, raw, features.len(), &mut questions, &mut warnings) {
                    if let Some(sources) = hole["sources"].as_object_mut() {
                        sources.insert(
                            "through".into(),
                            derived_source(raw, "旧 Python Quick 将齿轮中心孔尺寸构造成贯穿孔。"),
                        );
                    }
                    features.push(hole);
                }
            }
            Some(_) => questions.push(issue("gear_bore_diameter_invalid", "齿轮中心孔直径必须是有限正数。")),
            None => questions.push(issue("gear_bore_diameter_missing", "已提到齿轮中心孔，但没有识别到孔径；请补充中心孔直径。")),
        }
    }
    if !questions.is_empty() {
        return Some(result("needs_clarification", raw, None, warnings, questions, vec![]));
    }

    let plan = json!({
        "schema_version":"1.0",
        "name":"Quick Gear",
        "source_prompt":raw,
        "units":{"length":"mm","angle":"deg"},
        "coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},
        "features":features,
        "pending_questions":[]
    });
    Some(result("ready", raw, Some(plan), warnings, vec![], vec![]))
}

fn parse_stepped_shaft(raw: &str, text: &str) -> Value {
    let pairs = legacy_diameter_length_pairs(text);
    let pairs = if !pairs.is_empty() {
        pairs
    } else if let Some([diameter, length, _]) = find_dimension_triplet(text) {
        vec![(diameter, length)]
    } else {
        vec![(30.0, 50.0), (20.0, 40.0), (30.0, 30.0)]
    };
    let used_defaults =
        legacy_diameter_length_pairs(text).is_empty() && find_dimension_triplet(text).is_none();
    let mut features = Vec::with_capacity(pairs.len());
    let mut z = 0.0;
    for (index, (diameter, length)) in pairs.into_iter().enumerate() {
        if !diameter.is_finite() || !length.is_finite() || diameter <= 0.0 || length <= 0.0 {
            return parse_result_with_issue(
                "needs_clarification",
                raw,
                "step_shaft_invalid_segment",
                "阶梯轴每段必须提供正直径和正长度。",
            );
        }
        let mut p = serde_json::Map::new();
        let mut s = serde_json::Map::new();
        add_user(&mut p, &mut s, raw, "radius", json!(diameter / 2.0));
        if used_defaults {
            s.insert(
                "radius".into(),
                derived_source(raw, "沿用 Python Quick 阶梯轴模板默认尺寸。"),
            );
        } else {
            s.insert(
                "radius".into(),
                derived_source(raw, "radius = explicit diameter / 2."),
            );
        }
        if used_defaults {
            p.insert("height".into(), json!(length));
            s.insert(
                "height".into(),
                derived_source(raw, "沿用 Python Quick 阶梯轴模板默认尺寸。"),
            );
        } else {
            add_user(&mut p, &mut s, raw, "height", json!(length));
        }
        p.insert("center".into(), json!([0.0, 0.0, z]));
        s.insert(
            "center".into(),
            derived_source(raw, "每段沿 Z 轴首尾堆叠。"),
        );
        p.insert("base".into(), json!(true));
        p.insert("axis".into(), json!("Z"));
        p.insert("segments".into(), json!(48));
        s.insert(
            "segments".into(),
            derived_source(raw, "沿用 Python Quick 阶梯轴模板分段数 48。"),
        );
        features.push(
            json!({"id":format!("quick-{:04}",index+1),"kind":"cylinder","operation":"add",
            "name":format!("轴段{}",index+1),"parameters":p,"sources":s,"relations":[]}),
        );
        z += length;
    }
    let mut warnings = vec![issue(
        "legacy_template_migrated",
        "已按 Python Quick 阶梯轴模板建立轴段；尺寸使用 mm。",
    )];
    if used_defaults {
        warnings.push(issue(
            "legacy_template_defaults",
            "未提供分段尺寸，沿用旧模板的默认轴段：⌀30×50、⌀20×40、⌀30×30 mm。",
        ));
    }
    plan_result(raw, "阶梯轴", features, warnings)
}

fn parse_flange(raw: &str, text: &str) -> Value {
    let diameter = scalar(text, &["outer diameter", "外径"]).unwrap_or(120.0);
    let thickness = scalar(text, &["thickness", "厚度", "厚", "height", "高"]).unwrap_or(20.0);
    let inner = scalar(text, &["inner diameter", "内径", "内孔", "孔径"])
        .filter(|value| value.is_finite() && *value > 0.0);
    if !diameter.is_finite() || !thickness.is_finite() || diameter <= 0.0 || thickness <= 0.0 {
        return parse_result_with_issue(
            "needs_clarification",
            raw,
            "flange_invalid_dimensions",
            "法兰外径和厚度必须是正数。",
        );
    }
    let mut features = Vec::new();
    let defaults = !has_labeled_value(text, &["outer diameter", "外径"])
        || !has_labeled_value(text, &["thickness", "厚度", "厚", "height", "高"]);
    features.push(cylinder_template_feature(
        raw,
        1,
        "法兰盘体",
        diameter / 2.0,
        thickness,
        false,
        64,
        defaults,
    ));
    if let Some(inner) = inner {
        features.push(hole_template_feature(
            raw,
            2,
            "中心孔",
            inner,
            "center",
            None,
            None,
            defaults,
        ));
    }
    let count = scalar(text, &["count", "均布", "等分", "孔数"])
        .filter(|value| value.is_finite() && *value >= 2.0)
        .map(|value| value.floor() as u64);
    if let Some(count) = count {
        let bolt_diameter = bolt_hole_diameter(text).unwrap_or((diameter * 0.08).max(6.0));
        let pcd = scalar(text, &["pcd", "分度圆"])
            .filter(|value| value.is_finite() && *value > 0.0)
            .or_else(|| Some((diameter + inner.unwrap_or(diameter * 0.3)) / 2.0))
            .unwrap_or(0.0);
        features.push(hole_template_feature(
            raw,
            features.len() + 1,
            "螺栓孔",
            bolt_diameter,
            "pcd",
            Some(count),
            Some(pcd),
            defaults,
        ));
    }
    let mut warnings = vec![issue(
        "legacy_template_migrated",
        "已按 Python Quick 法兰模板映射为圆盘和明确描述的贯穿孔。",
    )];
    if defaults {
        warnings.push(issue(
            "legacy_template_defaults",
            "缺少外径或厚度时沿用旧法兰模板默认值：外径 120 mm、厚度 20 mm。",
        ));
    }
    plan_result(raw, "法兰", features, warnings)
}

fn parse_tube(raw: &str, text: &str) -> Value {
    let diameter = scalar(text, &["outer diameter", "外径"]).unwrap_or(60.0);
    let length = scalar(text, &["length", "长度", "长", "height", "高度", "高"])
        .or_else(|| find_dimension_triplet(text).map(|values| values[2]))
        .unwrap_or(80.0);
    let explicit_inner = scalar(text, &["inner diameter", "内径", "内孔"])
        .filter(|value| value.is_finite() && *value > 0.0);
    // The public Python entrypoint has two observable routes: ordinary tube
    // parsing keeps the inner diameter and emits 32 segments, while the
    // part-library path selected by the legacy field order outer→inner→length
    // halves the inner diameter and emits 48 segments. Keep both behaviors.
    let legacy_partlib_route = explicit_inner.is_some()
        && ordered_labels(text, &[&["outer diameter", "外径"], &["inner diameter", "内径", "内孔"], &["length", "长度", "长", "height", "高度", "高"]]);
    let inner = explicit_inner
        .map(|value| if legacy_partlib_route { value / 2.0 } else { value })
        .unwrap_or(diameter * 0.6);
    if !diameter.is_finite()
        || !length.is_finite()
        || !inner.is_finite()
        || diameter <= 0.0
        || length <= 0.0
        || inner <= 0.0
        || inner >= diameter
    {
        return parse_result_with_issue(
            "needs_clarification",
            raw,
            "tube_invalid_dimensions",
            "圆管外径、内孔直径和长度必须为正数，且内孔直径小于外径。旧 Python 对显式内径存在二分行为；请核对该尺寸。",
        );
    }
    let defaults = !has_labeled_value(text, &["outer diameter", "外径"])
        || !has_labeled_value(text, &["length", "长度", "长", "height", "高度", "高"]);
    let mut features = vec![
        cylinder_template_feature(raw, 1, "管体", diameter / 2.0, length, false, 64, defaults),
        hole_template_feature(raw, 2, "内孔", inner, "center", None, None, defaults),
    ];
    features[1]["parameters"]["segments"] = json!(if legacy_partlib_route { 48 } else { 32 });
    let mut warnings = vec![issue(
        "legacy_template_migrated",
        "已按 Python Quick 套筒/圆管模板映射为外圆柱与中心贯穿孔。",
    )];
    if legacy_partlib_route {
        warnings.push(issue("legacy_template_inner_diameter_compatibility", "兼容旧 Python 解析结果：输入内径值按其既有行为除以 2 后作为孔直径，请确认该旧语法单位含义。"));
    }
    if defaults {
        warnings.push(issue(
            "legacy_template_defaults",
            "缺少必需尺寸时沿用旧套筒模板默认值：外径 60 mm、长度 80 mm；默认孔径为外径的 60%。",
        ));
    }
    plan_result(raw, "套筒/圆管", features, warnings)
}

fn ordered_labels(text: &str, groups: &[&[&str]]) -> bool {
    let mut previous = None;
    for aliases in groups {
        let found = aliases.iter().filter_map(|alias| text.find(alias)).min();
        let Some(found) = found else { return false; };
        if previous.is_some_and(|prior| found <= prior) { return false; }
        previous = Some(found);
    }
    true
}

fn plan_result(raw: &str, name: &str, features: Vec<Value>, warnings: Vec<Value>) -> Value {
    let plan = json!({"schema_version":"1.0","name":name,"source_prompt":raw,
        "units":{"length":"mm","angle":"deg"},
        "coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},
        "features":features,"pending_questions":[]});
    result("ready", raw, Some(plan), warnings, vec![], vec![])
}

fn parse_result_with_issue(status: &str, raw: &str, code: &str, message: &str) -> Value {
    result(
        status,
        raw,
        None,
        vec![],
        vec![issue(code, message)],
        vec![],
    )
}

fn cylinder_template_feature(
    raw: &str,
    id: usize,
    name: &str,
    radius: f64,
    height: f64,
    base: bool,
    segments: u64,
    defaults: bool,
) -> Value {
    let mut parameters = serde_json::Map::new();
    let mut sources = serde_json::Map::new();
    parameters.insert("radius".into(), json!(radius));
    sources.insert(
        "radius".into(),
        derived_source(raw, "radius = outer diameter / 2."),
    );
    parameters.insert("height".into(), json!(height));
    sources.insert(
        "height".into(),
        if defaults {
            derived_source(raw, "沿用 Python Quick 模板默认尺寸。")
        } else {
            user_source(raw)
        },
    );
    parameters.insert("center".into(), json!([0.0, 0.0, 0.0]));
    sources.insert(
        "center".into(),
        derived_source(raw, "旧 Quick 模板将圆柱中心置于原点。"),
    );
    parameters.insert("base".into(), json!(base));
    parameters.insert("axis".into(), json!("Z"));
    parameters.insert("segments".into(), json!(segments));
    sources.insert(
        "segments".into(),
        derived_source(raw, "沿用 Python Quick 模板分段数。"),
    );
    json!({"id":format!("quick-{id:04}"),"kind":"cylinder","operation":"add","name":name,
        "parameters":parameters,"sources":sources,"relations":[]})
}

fn hole_template_feature(
    raw: &str,
    id: usize,
    name: &str,
    diameter: f64,
    position: &str,
    count: Option<u64>,
    pcd: Option<f64>,
    defaults: bool,
) -> Value {
    let mut parameters = serde_json::Map::new();
    let mut sources = serde_json::Map::new();
    parameters.insert("diameter".into(), json!(diameter));
    sources.insert(
        "diameter".into(),
        if defaults {
            derived_source(raw, "沿用 Python Quick 模板默认孔径。")
        } else {
            user_source(raw)
        },
    );
    parameters.insert("through".into(), json!(true));
    parameters.insert("pos".into(), json!(position));
    parameters.insert("axis".into(), json!("Z"));
    parameters.insert("segments".into(), json!(24));
    sources.insert(
        "segments".into(),
        derived_source(raw, "沿用 Python Quick 模板分段数。"),
    );
    if let (Some(count), Some(pcd)) = (count, pcd) {
        parameters.insert("count".into(), json!(count));
        parameters.insert("pcd".into(), json!(pcd));
        sources.insert("count".into(), user_source(raw));
        sources.insert(
            "pcd".into(),
            if has_labeled_value(raw, &["pcd", "分度圆"]) {
                user_source(raw)
            } else {
                derived_source(raw, "按旧 Python Quick 法兰模板从外径与中心孔推导分度圆。")
            },
        );
    }
    json!({"id":format!("quick-{id:04}"),"kind":"hole","operation":"cut","name":name,
        "parameters":parameters,"sources":sources,"relations":[]})
}

fn legacy_diameter_length_pairs(text: &str) -> Vec<(f64, f64)> {
    let number = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))";
    let patterns = [
        format!(
            r"(?:⌀|ø|diameter\s*)\s*{number}\s*(?:mm)?\s*x\s*{number}",
            number = number
        ),
        format!(
            r"(?:直径|diameter)\s*{number}\s*(?:mm)?\s*(?:长度|长|length)\s*{number}",
            number = number
        ),
    ];
    for pattern in patterns {
        if let Ok(regex) = regex::Regex::new(&pattern) {
            let found = regex
                .captures_iter(text)
                .filter_map(|capture| {
                    Some((
                        capture.get(1)?.as_str().parse::<f64>().ok()?,
                        capture.get(2)?.as_str().parse::<f64>().ok()?,
                    ))
                })
                .collect::<Vec<_>>();
            if !found.is_empty() {
                return found;
            }
        }
    }
    Vec::new()
}

fn has_labeled_value(text: &str, labels: &[&str]) -> bool {
    labels
        .iter()
        .any(|label| scalar(text, &[*label]).is_some_and(f64::is_finite))
}

fn bolt_hole_diameter(text: &str) -> Option<f64> {
    let regex = regex::Regex::new(r"(?:螺栓孔|bolt\s*hole).{0,16}(?:直径|diameter|⌀)\s*([0-9]+(?:\.[0-9]+)?)|(?:直径|diameter|⌀)\s*([0-9]+(?:\.[0-9]+)?).{0,16}(?:螺栓孔|bolt\s*hole)").ok()?;
    regex
        .captures(text)
        .and_then(|capture| capture.get(1).or_else(|| capture.get(2)))
        .and_then(|value| value.as_str().parse::<f64>().ok())
}
fn issue(code: &str, message: impl Into<String>) -> Value {
    json!({"code":code,"message":message.into()})
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
    allow_legacy_defaults: bool,
) -> Option<Value> {
    let mut parameters = serde_json::Map::new();
    let mut sources = serde_json::Map::new();
    let mut legacy_defaulted = Vec::<(&str, f64)>::new();
    let mut legacy_raw_mappings = Vec::<String>::new();
    let units_explicit = has_unit(clause);
    match kind {
        BaseKind::Box => {
            let triple = find_dimension_triplet(clause);
            let pair = find_dimension_pair(clause);
            let l = scalar(
                clause,
                &["length", "overall length", "长", "长度", "总长", "全长"],
            );
            let w = scalar(clause, &["width", "宽度", "宽"]);
            let h = scalar(clause, &["height", "高度", "高", "thickness", "厚度", "厚"]);
            let mut vals = [
                l.or_else(|| triple.as_ref().map(|t| t[0]))
                    .or_else(|| pair.as_ref().map(|v| v[0])),
                w.or_else(|| triple.as_ref().map(|t| t[1]))
                    .or_else(|| pair.as_ref().map(|v| v[1])),
                h.or_else(|| triple.as_ref().map(|t| t[2])),
            ];
            let mut remaining_raw = if allow_legacy_defaults {
                dimension_values(clause)
            } else {
                Vec::new()
            };
            let explicit_values = vals.iter().flatten().copied().collect::<Vec<_>>();
            remaining_raw.retain(|candidate| {
                !explicit_values
                    .iter()
                    .any(|explicit| close(*candidate, *explicit))
            });
            let mut raw_iter = remaining_raw.into_iter();
            let defaults = [100.0, 60.0, 20.0];
            let mut used_default = Vec::new();
            for (index, value) in vals.iter_mut().enumerate() {
                if value.is_none() && allow_legacy_defaults {
                    if let Some(raw_value) = raw_iter.next() {
                        *value = Some(raw_value);
                    } else {
                        *value = Some(defaults[index]);
                        used_default.push((
                            ["length", "width", "height"][index],
                            defaults[index],
                        ));
                    }
                }
            }
            for (key, value) in ["length", "width", "height"].into_iter().zip(vals) {
                match value.filter(|v| v.is_finite() && *v > 0.0) {
                    Some(v) => {
                        let was_default = used_default.iter().any(|(default_key, _)| *default_key == key);
                        parameters.insert(key.into(), json!(v));
                        sources.insert(
                            key.into(),
                            if was_default {
                                derived_source(
                                    raw,
                                    &format!(
                                        "旧 Python Quick 长方体默认尺寸：{key}={v} mm。"
                                    ),
                                )
                            } else {
                                user_source(raw)
                            },
                        );
                    }
                    None => questions.push(issue(
                        "box_dimension_missing_or_invalid",
                        &format!("长方体缺少明确的正数 {key}；请给出长度、宽度、高度或 L×W×H。"),
                    )),
                }
            }
            if !used_default.is_empty() {
                let description = used_default
                    .iter()
                    .map(|(key, value)| format!("{key}={value} mm"))
                    .collect::<Vec<_>>()
                    .join("、");
                warnings.push(issue(
                    "legacy_box_dimensions_defaulted",
                    &format!("沿用旧 Python Quick 长方体默认尺寸：{description}。"),
                ));
            }
            let fillet_requested = contains_any(clause, &["圆角", "倒圆", "圆滑", "fillet"]);
            let chamfer_requested = contains_any(clause, &["倒角", "切角", "chamfer"]);
            let corner = if chamfer_requested {
                scalar(clause, &["倒角c", "切角c", "chamfer c", "倒角", "切角", "chamfer"])
            } else {
                scalar(
                    clause,
                    &["圆角半径", "圆角", "倒圆角", "倒圆", "圆滑", "fillet"],
                )
            };
            let mode = if chamfer_requested { "chamfer" } else { "fillet" };
            match corner {
                Some(value) if value.is_finite() && value >= 0.0 => {
                    parameters.insert("corner".into(), json!(value));
                    sources.insert("corner".into(), user_source(raw));
                }
                Some(_) => questions.push(issue(
                    "box_corner_invalid",
                    "长方体圆角半径或倒角边长必须是有限的非负数。",
                )),
                None => {
                    parameters.insert("corner".into(), json!(0.0));
                    sources.insert(
                        "corner".into(),
                        derived_source(raw, "旧 Python Quick 长方体轮廓未给尺寸时沿用 corner=0。"),
                    );
                    if fillet_requested || chamfer_requested {
                        warnings.push(issue(
                            "legacy_box_corner_value_missing",
                            "未取得圆角/倒角尺寸，沿用旧 Python Quick 行为忽略修饰并生成直角长方体。",
                        ));
                    }
                }
            }
            parameters.insert("corner_mode".into(), json!(mode));
            sources.insert(
                "corner_mode".into(),
                if fillet_requested || chamfer_requested {
                    user_source(raw)
                } else {
                    derived_source(raw, "旧 Python Quick 长方体默认轮廓模式为 fillet。")
                },
            );
        }
        BaseKind::Cylinder => {
            let radius = scalar(clause, &["radius", "半径"]);
            let diameter = diameter_value(clause);
            let pair = find_dimension_pair(clause);
            let triple = find_dimension_triplet(clause);
            let raw_values = dimension_values(clause);
            let r = match (radius, diameter) {
                (Some(r), Some(d)) if !close(r * 2.0, d) => {
                    questions.push(issue(
                        "conflicting_diameter_radius",
                        "圆柱同时给出的半径和直径不一致。",
                    ));
                    None
                }
                (Some(r), _) => Some((r, "user", false, "")),
                (None, Some(d)) => Some((
                    d * 0.5,
                    "derived",
                    false,
                    "旧 Python Quick 按直径的一半作为半径。",
                )),
                (None, None) if allow_legacy_defaults => {
                    let paired = triple
                        .map(|values| values[0])
                        .or_else(|| pair.map(|values| values[0]));
                    if let Some(diameter) = paired {
                        Some((
                            diameter * 0.5,
                            "derived",
                            false,
                            "旧 Python Quick 从尺寸组首值的一半推导圆柱半径。",
                        ))
                    } else if let Some(raw) = raw_values.first().copied() {
                        legacy_raw_mappings.push(format!("radius={} mm", raw * 0.5));
                        Some((
                            raw * 0.5,
                            "derived",
                            false,
                            "旧 Python Quick 将首个未标注尺寸的一半用作圆柱半径。",
                        ))
                    } else {
                        legacy_defaulted.push(("radius", 25.0));
                        Some((
                            25.0,
                            "derived",
                            true,
                            "旧 Python Quick 圆柱默认半径为 25 mm。",
                        ))
                    }
                }
                _ => None,
            };
            match r.filter(|(v, _, _, _)| v.is_finite() && *v > 0.0) {
                Some((v, "derived", _, note)) => {
                    parameters.insert("radius".into(), json!(v));
                    sources.insert("radius".into(), derived_source(raw, note));
                }
                Some((v, _, _, _)) => {
                    add_user(&mut parameters, &mut sources, raw, "radius", json!(v));
                }
                None => questions.push(issue(
                    "cylinder_radius_missing",
                    "圆柱缺少明确的正半径或直径。",
                )),
            }
            let height = scalar(clause, &["height", "高度", "高", "length", "长度", "长"]);
            let h = match height {
                Some(v) => Some((v, "user", false, "")),
                None if allow_legacy_defaults => {
                    if let Some(v) = triple
                        .map(|values| values[2])
                        .or_else(|| pair.map(|values| values[1]))
                    {
                        Some((v, "user", false, ""))
                    } else if let Some(radius) = r.map(|value| value.0) {
                        let remaining = raw_values
                            .iter()
                            .copied()
                            .find(|value| !close(*value, radius) && !close(*value, radius * 2.0));
                        if let Some(v) = remaining {
                            legacy_raw_mappings.push(format!("height={v} mm"));
                            Some((
                                v,
                                "derived",
                                false,
                                "旧 Python Quick 将未被半径消耗的尺寸用作圆柱高度。",
                            ))
                        } else {
                            legacy_defaulted.push(("height", 50.0));
                            Some((
                                50.0,
                                "derived",
                                true,
                                "旧 Python Quick 圆柱默认高度为 50 mm。",
                            ))
                        }
                    } else {
                        legacy_defaulted.push(("height", 50.0));
                        Some((
                            50.0,
                            "derived",
                            true,
                            "旧 Python Quick 圆柱默认高度为 50 mm。",
                        ))
                    }
                }
                None => None,
            };
            match h.filter(|(v, _, _, _)| v.is_finite() && *v > 0.0) {
                Some((v, "derived", _, note)) => {
                    parameters.insert("height".into(), json!(v));
                    sources.insert("height".into(), derived_source(raw, note));
                }
                Some((v, _, _, _)) => {
                    add_user(&mut parameters, &mut sources, raw, "height", json!(v))
                }
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
            let mut base_defaulted = false;
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
                (None, None) if kind == BaseKind::Cone && allow_legacy_defaults => {
                    legacy_defaulted.push(("radius", 25.0));
                    base_defaulted = true;
                    (Some(25.0), true)
                }
                _ => (None, false),
            };
            match r.filter(|v| v.is_finite() && *v > 0.0) {
                Some(v) if derived => {
                    parameters.insert("radius".into(), json!(v));
                    sources.insert(
                        "radius".into(),
                        derived_source(
                            raw,
                            if base_defaulted {
                                "旧 Python Quick 圆锥默认底半径为 25 mm。"
                            } else {
                                "radius = explicit base diameter / 2."
                            },
                        ),
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
            let height = scalar(clause, &["height", "高度", "高", "length", "长度", "长"]);
            let h = match height {
                Some(v) => Some((v, "user", "")),
                None if kind == BaseKind::Cone && allow_legacy_defaults => {
                    if let Some(v) = dimension_values(clause).last().copied() {
                        legacy_raw_mappings.push(format!("height={v} mm"));
                        Some((
                            v,
                            "derived",
                            "旧 Python Quick 将最后一个未标注尺寸用作圆锥高度。",
                        ))
                    } else {
                        legacy_defaulted.push(("height", 50.0));
                        Some((50.0, "derived", "旧 Python Quick 圆锥默认高度为 50 mm。"))
                    }
                }
                None => None,
            };
            match h.filter(|(value, _, _)| value.is_finite() && *value > 0.0) {
                Some((value, "derived", note)) => {
                    parameters.insert("height".into(), json!(value));
                    sources.insert("height".into(), derived_source(raw, note));
                }
                Some((value, _, _)) => {
                    add_user(&mut parameters, &mut sources, raw, "height", json!(value));
                }
                None => questions.push(issue("cone_height_missing", "圆锥/圆台缺少明确的正高度。")),
            }
            if let Some(axis) = axis_value(clause, questions) {
                add_user(&mut parameters, &mut sources, raw, "axis", json!(axis));
            }
        }
        BaseKind::Prism => {
            let raw_values = dimension_values(clause);
            let pair = find_dimension_pair(clause);
            let triple = find_dimension_triplet(clause);
            let profile_cue = contains_any(
                clause,
                &["草图", "轮廓", "截面", "剖面", "sketch", "profile"],
            );
            let circular = clause.contains('圆') || contains_any(clause, &["circle", "circular"]);
            let mut defaulted = Vec::<String>::new();
            let mut raw_mapped = Vec::<String>::new();
            let profile = if circular {
                let radius = scalar(clause, &["radius", "半径"]);
                let diameter = diameter_value(clause);
                let explicit_radius = radius.or_else(|| diameter.map(|value| value * 0.5));
                let mapped_radius = if explicit_radius.is_none() {
                    raw_values.first().copied().map(|value| value * 0.5)
                } else {
                    None
                };
                let chosen = explicit_radius.or(mapped_radius).unwrap_or(25.0);
                if !chosen.is_finite() || chosen <= 0.0 {
                    questions.push(issue(
                        "extrusion_profile_radius_invalid",
                        "圆形拉伸轮廓半径/直径必须为有限正数。",
                    ));
                }
                if explicit_radius.is_none() {
                    if let Some(value) = mapped_radius {
                        raw_mapped.push(format!("profile radius={value} mm"));
                    } else {
                        defaulted.push("profile radius=25 mm".into());
                    }
                }
                circle_profile(chosen, 48)
            } else {
                let length = pair
                    .map(|values| values[0])
                    .or_else(|| triple.map(|values| values[0]))
                    .or_else(|| scalar(clause, &["length", "长度", "长"]))
                    .or_else(|| {
                        (profile_cue && raw_values.len() >= 2)
                            .then(|| raw_values[0])
                    })
                    .unwrap_or(100.0);
                let width = pair
                    .map(|values| values[1])
                    .or_else(|| triple.map(|values| values[1]))
                    .or_else(|| scalar(clause, &["width", "宽度", "宽"]))
                    .or_else(|| {
                        (profile_cue && raw_values.len() >= 2)
                            .then(|| raw_values[1])
                    })
                    .unwrap_or(60.0);
                if !length.is_finite() || length <= 0.0 || !width.is_finite() || width <= 0.0 {
                    questions.push(issue(
                        "extrusion_profile_dimensions_invalid",
                        "矩形拉伸轮廓长和宽必须为有限正数。",
                    ));
                }
                let length_defaulted = pair.is_none()
                    && triple.is_none()
                    && scalar(clause, &["length", "长度", "长"]).is_none()
                    && !(profile_cue && raw_values.len() >= 2);
                let width_defaulted = pair.is_none()
                    && triple.is_none()
                    && scalar(clause, &["width", "宽度", "宽"]).is_none()
                    && !(profile_cue && raw_values.len() >= 2);
                if length_defaulted {
                    defaulted.push("profile length=100 mm".into());
                } else if pair.is_none()
                    && triple.is_none()
                    && scalar(clause, &["length", "长度", "长"]).is_none()
                    && profile_cue
                    && raw_values.len() >= 2
                {
                    raw_mapped.push(format!("profile length={length} mm"));
                }
                if width_defaulted {
                    defaulted.push("profile width=60 mm".into());
                } else if pair.is_none()
                    && triple.is_none()
                    && scalar(clause, &["width", "宽度", "宽"]).is_none()
                    && profile_cue
                    && raw_values.len() >= 2
                {
                    raw_mapped.push(format!("profile width={width} mm"));
                }
                vec![
                    [-length * 0.5, -width * 0.5],
                    [length * 0.5, -width * 0.5],
                    [length * 0.5, width * 0.5],
                    [-length * 0.5, width * 0.5],
                ]
            };
            let explicit_height = scalar(clause, &["height", "高度", "高"])
                .or_else(|| triple.map(|values| values[2]));
            let raw_height = if explicit_height.is_none()
                && (!profile_cue || raw_values.len() >= 3)
            {
                raw_values.last().copied()
            } else {
                None
            };
            let profile_radius = if circular {
                profile
                    .first()
                    .map(|point| point[0])
                    .unwrap_or(25.0)
            } else {
                0.0
            };
            let rect_size = if circular {
                [0.0, 0.0]
            } else {
                [
                    profile[1][0] - profile[0][0],
                    profile[2][1] - profile[1][1],
                ]
            };
            let legacy_height = raw_height.or_else(|| {
                if profile_cue {
                    Some(if circular {
                        profile_radius * 2.0
                    } else {
                        rect_size[0].min(rect_size[1])
                    })
                } else {
                    Some(if circular { 50.0 } else { 20.0 })
                }
            });
            let height = explicit_height.or(legacy_height).unwrap_or(20.0);
            if !height.is_finite() || height <= 0.0 {
                questions.push(issue(
                    "extrusion_height_invalid",
                    "拉伸高度必须为有限正数。",
                ));
            }
            if explicit_height.is_none() {
                if let Some(value) = raw_height {
                    raw_mapped.push(format!("extrusion height={value} mm"));
                } else {
                    defaulted.push(format!("extrusion height={height} mm"));
                }
            }
            parameters.insert("profile".into(), json!(profile));
            sources.insert(
                "profile".into(),
                derived_source(raw, "按旧 Python Quick 拉伸规则从尺寸生成闭合二维轮廓。"),
            );
            if explicit_height.is_some() {
                add_user(&mut parameters, &mut sources, raw, "height", json!(height));
            } else {
                parameters.insert("height".into(), json!(height));
                sources.insert(
                    "height".into(),
                    derived_source(raw, "沿用旧 Python Quick 拉伸高度回退规则。"),
                );
            }
            let explicit_axis = axis_value(clause, questions);
            let axis = explicit_axis.unwrap_or("Z");
            parameters.insert("center".into(), json!([0.0, 0.0, 0.0]));
            sources.insert(
                "center".into(),
                derived_source(raw, "沿用旧 Python Quick 的全局原点拉伸中心。"),
            );
            parameters.insert("axis".into(), json!(axis));
            if explicit_axis.is_some() {
                sources.insert("axis".into(), user_source(raw));
            } else {
                sources.insert(
                    "axis".into(),
                    derived_source(raw, "未指定方向时沿用旧 Python Quick 默认 Z 轴。"),
                );
            }
            if !defaulted.is_empty() {
                warnings.push(issue(
                    "legacy_extrusion_dimensions_defaulted",
                    &format!(
                        "沿用旧 Python Quick 拉伸默认值（{}）；这些值不是用户标注尺寸。",
                        defaulted.join("、")
                    ),
                ));
            }
            if !raw_mapped.is_empty() {
                warnings.push(issue(
                    "legacy_extrusion_raw_dimensions_mapped",
                    &format!(
                        "沿用旧 Python Quick 对未标注尺寸的映射：{}；请核对这些尺寸的含义。",
                        raw_mapped.join("、")
                    ),
                ));
            }
        }
        BaseKind::Revolve => {
            let radius = scalar(clause, &["radius", "半径"]);
            let diameter = diameter_value(clause);
            let has_radius_or_diameter = radius.is_some() || diameter.is_some();
            let raw_values = dimension_values(clause);
            let mapped_radius = if !has_radius_or_diameter {
                raw_values.first().copied().map(|value| value * 0.5)
            } else {
                None
            };
            let explicit_radius = radius.or_else(|| diameter.map(|value| value * 0.5));
            let radius = explicit_radius.or(mapped_radius).unwrap_or(20.0);
            let explicit_height = scalar(clause, &["height", "高度", "高"]);
            let height = explicit_height.unwrap_or(if has_radius_or_diameter {
                20.0
            } else {
                40.0
            });
            if !radius.is_finite() || radius <= 0.0 || !height.is_finite() || height <= 0.0 {
                questions.push(issue(
                    "revolve_dimensions_invalid",
                    "回转轮廓半径与高度必须为有限正数。",
                ));
            }
            let angle = match circular_pattern_angle(clause) {
                Ok(Some(value)) if value.is_finite() && value > 0.0 && value <= 360.0 => value,
                Ok(Some(_)) => {
                    questions.push(issue(
                        "revolve_angle_invalid",
                        "回转角度必须大于 0 且不超过 360 度。",
                    ));
                    360.0
                }
                Ok(None) => 360.0,
                Err("radians") => {
                    questions.push(issue(
                        "revolve_angle_unit_unsupported",
                        "旧 Quick 回转角度只接受度（°/deg/度），不接受弧度。",
                    ));
                    360.0
                }
                Err(_) => {
                    questions.push(issue(
                        "revolve_angle_conflict",
                        "回转描述了互相冲突的角度值。",
                    ));
                    360.0
                }
            };
            if axis_value(clause, questions).is_some_and(|axis| axis != "Z") {
                questions.push(issue(
                    "revolve_axis_fixed_to_z",
                    "旧 Python Quick 回转特征固定绕 Z 轴；请移除冲突轴向或改用其他建模方式。",
                ));
            }
            let profile = vec![[0.0, 0.0], [radius, 0.0], [radius, height], [0.0, height]];
            parameters.insert("profile".into(), json!(profile));
            sources.insert(
                "profile".into(),
                derived_source(raw, "按旧 Python Quick 回转规则从半径和高度生成矩形母线。"),
            );
            parameters.insert("angle".into(), json!(angle));
            if circular_pattern_angle(clause).ok().flatten().is_some() {
                sources.insert("angle".into(), user_source(raw));
            } else {
                sources.insert(
                    "angle".into(),
                    derived_source(raw, "沿用旧 Python Quick 的 360 度默认回转角。"),
                );
            }
            parameters.insert("center".into(), json!([0.0, 0.0, 0.0]));
            sources.insert(
                "center".into(),
                derived_source(raw, "沿用旧 Python Quick 的全局原点回转中心。"),
            );
            parameters.insert("axis".into(), json!("Z"));
            sources.insert(
                "axis".into(),
                derived_source(raw, "旧 Python Quick 回转始终绕 Z 轴。"),
            );
            parameters.insert("segments".into(), json!(48));
            sources.insert(
                "segments".into(),
                derived_source(raw, "沿用旧 Python Quick 回转特征的 48 段设置。"),
            );
            let mut defaulted = Vec::<String>::new();
            let mut raw_mapped = Vec::<String>::new();
            if explicit_radius.is_none() {
                if let Some(value) = mapped_radius {
                    raw_mapped.push(format!("profile radius={value} mm"));
                } else {
                    defaulted.push("profile radius=20 mm".into());
                }
            }
            if explicit_height.is_none() {
                defaulted.push(format!("profile height={height} mm"));
            }
            if circular_pattern_angle(clause).ok().flatten().is_none() {
                defaulted.push("angle=360 deg".into());
            }
            if !defaulted.is_empty() {
                warnings.push(issue(
                    "legacy_revolve_dimensions_defaulted",
                    &format!(
                        "沿用旧 Python Quick 回转默认值（{}）；这些值不是用户标注尺寸。",
                        defaulted.join("、")
                    ),
                ));
            }
            if !raw_mapped.is_empty() {
                warnings.push(issue(
                    "legacy_revolve_raw_dimensions_mapped",
                    &format!(
                        "沿用旧 Python Quick 对未标注尺寸的映射：{}；请核对这些尺寸的含义。",
                        raw_mapped.join("、")
                    ),
                ));
            }
        }
        BaseKind::Sphere => {
            let r = scalar(clause, &["radius", "半径"]);
            let d = diameter_value(clause);
            let raw_values = dimension_values(clause);
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
                (None, None) if allow_legacy_defaults => {
                    if let Some(raw) = raw_values.first().copied() {
                        let value = raw * 0.5;
                        legacy_raw_mappings.push(format!("radius={value} mm"));
                        Some((value, true))
                    } else {
                        legacy_defaulted.push(("radius", 25.0));
                        Some((25.0, true))
                    }
                }
                _ => None,
            };
            match chosen.filter(|(v, _)| v.is_finite() && *v > 0.) {
                Some((v, true)) => {
                    parameters.insert("radius".into(), json!(v));
                    sources.insert(
                        "radius".into(),
                        derived_source(
                            raw,
                            if legacy_defaulted.iter().any(|(key, _)| *key == "radius") {
                                "旧 Python Quick 球体默认半径为 25 mm。"
                            } else if legacy_raw_mappings
                                .iter()
                                .any(|mapping| mapping.starts_with("radius="))
                            {
                                "旧 Python Quick 将首个裸尺寸的一半用作球体半径。"
                            } else {
                                "radius = explicit diameter / 2."
                            },
                        ),
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
    if allow_legacy_defaults {
        match kind {
            BaseKind::Cylinder | BaseKind::Cone => {
                for (key, value, note) in [
                    (
                        "center",
                        json!([0.0, 0.0, 0.0]),
                        "沿用 Python Quick 的全局原点轴向底面中心。",
                    ),
                    ("base", json!(true), "沿用 Python Quick 的 base=true。"),
                    (
                        "axis",
                        json!("Z"),
                        "未指定轴向时沿用 Python Quick 默认 Z 轴。",
                    ),
                    (
                        "segments",
                        json!(48),
                        "沿用 Python Quick 圆柱/圆锥模板的 48 段设置。",
                    ),
                ] {
                    if !parameters.contains_key(key) {
                        parameters.insert(key.into(), value);
                        sources.insert(key.into(), derived_source(raw, note));
                    }
                }
            }
            BaseKind::Sphere => {
                for (key, value, note) in [
                    (
                        "center",
                        json!([0.0, 0.0, 0.0]),
                        "沿用 Python Quick 的全局原点球心。",
                    ),
                    (
                        "segments",
                        json!(32),
                        "沿用 Python Quick 球体的 32 段设置。",
                    ),
                    ("rings", json!(16), "沿用 Python Quick 球体的 16 环设置。"),
                ] {
                    if !parameters.contains_key(key) {
                        parameters.insert(key.into(), value);
                        sources.insert(key.into(), derived_source(raw, note));
                    }
                }
            }
            _ => {}
        }
    }
    if kind == BaseKind::Box {
        for (key, value, note) in [
            (
                "center",
                json!([0.0, 0.0, 0.0]),
                "沿用旧 Python Quick 长方体默认原点底面中心。",
            ),
            ("base", json!(true), "沿用旧 Python Quick 长方体默认 base=true。"),
            (
                "axis",
                json!("Z"),
                "未指定轴向时沿用旧 Python Quick 长方体默认 Z 轴。",
            ),
        ] {
            if !parameters.contains_key(key) {
                parameters.insert(key.into(), value);
                sources.insert(key.into(), derived_source(raw, note));
            }
        }
    }
    if !legacy_defaulted.is_empty() {
        let fields = legacy_defaulted
            .iter()
            .map(|(key, value)| format!("{key}={value} mm"))
            .collect::<Vec<_>>()
            .join("、");
        warnings.push(issue(
            "legacy_primitive_dimensions_defaulted",
            &format!("沿用旧 Python Quick 基础体默认尺寸（{fields}）；这些值不是用户标注尺寸。"),
        ));
    }
    if !legacy_raw_mappings.is_empty() {
        warnings.push(issue(
            "legacy_primitive_raw_dimensions_mapped",
            &format!(
                "沿用旧 Python Quick 对未标注尺寸的映射：{}；请核对这些尺寸的含义。",
                legacy_raw_mappings.join("、")
            ),
        ));
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
    let feature_kind = match kind {
        BaseKind::Box => "box",
        BaseKind::Sphere => "sphere",
        BaseKind::Prism => "prism",
        BaseKind::Revolve => "revolve",
        _ => "cylinder",
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
    let depth = hole_depth_value(clause);
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
    let corners = has_corner_location(clause);
    let center = has_center_location(clause);
    let margin = scalar(
        clause,
        &[
            "edge margin",
            "edge distance",
            "margin",
            "边距",
            "距边",
            "离边",
            "留边",
            "内缩",
        ],
    );
    let coords = center_coordinates(clause);
    let named = named_xyz(clause);
    let pcd_hint = contains_any(
        clause,
        &["pcd", "分度圆", "节圆", "分布圆"],
    );
    let count_result = hole_pattern_count(clause);
    let count_hint = contains_any(
        clause,
        &["hole count", "count", "孔数", "数量", "均布", "等分"],
    ) || matches!(count_result, Ok(Some(_)) | Err(_));
    let phase_hint = contains_any(clause, &["phase", "相位", "起始角"]);
    let pattern_requested = !corners && (pcd_hint || count_hint || phase_hint);
    if (center || corners || pattern_requested) && (coords.is_some() || named.is_some()) {
        questions.push(issue(
            "hole_position_conflict",
            "孔同时描述了规则位置（中心/四角）与坐标点。",
        ));
    }
    if pattern_requested && center {
        questions.push(issue(
            "hole_position_conflict",
            "同一孔不能同时指定中心位置和 PCD 孔阵列。",
        ));
    }
    if pattern_requested {
        p.insert("pos".into(), json!("pcd"));
        s.insert("pos".into(), user_source(raw));
        match scalar(
            clause,
            &[
                "pcd",
                "分度圆直径",
                "分度圆",
                "节圆直径",
                "节圆",
                "分布圆直径",
                "分布圆",
            ],
        ) {
            Some(v) if v.is_finite() && v > 0.0 => {
                add_user(&mut p, &mut s, raw, "pcd", json!(v));
            }
            Some(v) if !v.is_finite() => questions.push(issue(
                "hole_pcd_conflict",
                "PCD 孔阵列描述了互相冲突的分度圆直径。",
            )),
            Some(_) => questions.push(issue(
                "hole_pcd_invalid",
                "分度圆直径必须是有限的正数。",
            )),
            None => questions.push(issue(
                "hole_pcd_missing",
                "孔阵列缺少明确的 PCD/分度圆直径。",
            )),
        }
        match count_result {
            Ok(Some(count)) => {
                p.insert("count".into(), json!(count));
                s.insert("count".into(), user_source(raw));
            }
            Ok(None) => questions.push(issue(
                "hole_pattern_count_missing",
                "PCD 孔阵列缺少明确孔数。",
            )),
            Err("hole_pattern_count_conflict") => questions.push(issue(
                "hole_pattern_count_conflict",
                "PCD 孔阵列描述了互相冲突的孔数。",
            )),
            Err(_) => questions.push(issue(
                "hole_pattern_count_invalid",
                format!(
                    "PCD 孔数必须是 2 到 {} 之间的整数。",
                    crate::native_plan_geometry::MAX_HOLE_PATTERN_COUNT
                ),
            )),
        }
        p.insert("segments".into(), json!(24));
        s.insert(
            "segments".into(),
            derived_source(raw, "沿用 Python Quick 孔特征圆周分段数 24。"),
        );
        if let Some(axis) = axis_value(clause, questions) {
            add_user(&mut p, &mut s, raw, "axis", json!(axis));
        } else {
            p.insert("axis".into(), json!("Z"));
            s.insert(
                "axis".into(),
                derived_source(raw, "未指定孔阵列轴时沿用 Python Quick 默认 Z 轴。"),
            );
        }
        if phase_hint {
            if phase_uses_radians(clause) {
                questions.push(issue(
                    "hole_phase_unit_unsupported",
                    "PCD 孔阵列相位按角度处理；当前只接受度（°/deg/度），不接受弧度。",
                ));
            } else {
                match scalar(clause, &["phase", "相位", "起始角"]) {
                    Some(v) if v.is_finite() => {
                        add_user(&mut p, &mut s, raw, "phase", json!(v));
                        warnings.push(issue(
                            "pcd_phase_native_extension",
                            "孔阵列相位是 Rust Quick 扩展字段；旧 Python Quick 不解析该值。",
                        ));
                    }
                    Some(_) => questions.push(issue(
                        "hole_phase_conflict",
                        "PCD 孔阵列描述了互相冲突的相位角。",
                    )),
                    None => questions.push(issue(
                        "hole_phase_missing",
                        "已指定相位，但没有给出明确的角度值。",
                    )),
                }
            }
        }
    } else if corners {
        p.insert("pos".into(), json!("corners"));
        s.insert("pos".into(), user_source(raw));
        p.insert("segments".into(), json!(24));
        s.insert(
            "segments".into(),
            derived_source(raw, "沿用 Python Quick 四角孔默认圆周分段数 24。"),
        );
        match margin {
            Some(v) if v.is_finite() && v >= 0. => {
                p.insert("margin".into(), json!(v));
                s.insert("margin".into(), user_source(raw));
            }
            Some(_) => questions.push(issue(
                "hole_margin_invalid",
                "四角孔边距必须是有限的非负数。",
            )),
            None => {
                p.insert("margin".into(), json!(0.0));
                s.insert(
                    "margin".into(),
                    derived_source(raw, "未指定边距时沿用 Python Quick 四角孔默认值 0 mm。"),
                );
            }
        }
    } else if center {
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
    } else if corners {
        p.insert("axis".into(), json!("Z"));
        s.insert(
            "axis".into(),
            derived_source(raw, "四角孔未指定切入轴时沿用 Python Quick 默认 Z 轴。"),
        );
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

fn build_center_counterbore(
    clause: &str,
    raw: &str,
    index: usize,
    questions: &mut Vec<Value>,
    warnings: &mut Vec<Value>,
) -> Vec<Value> {
    if clause.match_indices('带').count() != 1 {
        questions.push(issue(
            "counterbore_separator_missing_or_ambiguous",
            "中心沉孔语法需在同一句明确使用一次“带”，分隔通孔与沉孔尺寸。",
        ));
        return Vec::new();
    }
    let Some((pilot_clause, counterbore_clause)) = clause.split_once('带') else {
        questions.push(issue(
            "counterbore_separator_missing_or_ambiguous",
            "中心沉孔语法需明确使用“带”分隔通孔与沉孔尺寸。",
        ));
        return Vec::new();
    };
    let mut valid = true;
    if contains_any(pilot_clause, &["盲孔", "blind"]) {
        valid = false;
        questions.push(issue(
            "counterbore_pilot_type_conflict",
            "中心通孔部分同时描述了通孔与盲孔类型。",
        ));
    } else if !pilot_clause.contains("通孔") {
        valid = false;
        questions.push(issue(
            "counterbore_pilot_type_missing",
            "中心孔必须明确为通孔，才能创建中心沉孔组合。",
        ));
    }
    if contains_any(counterbore_clause, &["通孔", "through", "贯穿"]) {
        valid = false;
        questions.push(issue(
            "counterbore_type_conflict",
            "沉孔是盲切，不能同时描述为通孔。",
        ));
    } else if !counterbore_clause.contains("沉孔") {
        valid = false;
        questions.push(issue(
            "counterbore_type_missing",
            "“带”之后必须明确描述沉孔。",
        ));
    }
    if scalar(
        pilot_clause,
        &["depth", "hole depth", "深度", "孔深", "深"],
    )
    .is_some()
    {
        valid = false;
        questions.push(issue(
            "counterbore_pilot_depth_conflict",
            "贯穿孔不能同时指定盲孔深度。",
        ));
    }

    let pilot_diameter = diameter_value(pilot_clause);
    let counterbore_diameter = diameter_value(counterbore_clause);
    match pilot_diameter {
        Some(value) if value.is_finite() && value > 0.0 => {}
        Some(_) => {
            valid = false;
            questions.push(issue(
                "counterbore_pilot_diameter_conflict",
                "中心通孔部分存在互相冲突或非法的直径值。",
            ));
        }
        None => {
            valid = false;
            questions.push(issue(
                "counterbore_pilot_diameter_missing",
                "中心通孔部分缺少明确的正直径。",
            ));
        }
    }
    match counterbore_diameter {
        Some(value) if value.is_finite() && value > 0.0 => {}
        Some(_) => {
            valid = false;
            questions.push(issue(
                "counterbore_diameter_conflict",
                "沉孔部分存在互相冲突或非法的直径值。",
            ));
        }
        None => {
            valid = false;
            questions.push(issue(
                "counterbore_diameter_missing",
                "沉孔部分缺少明确的正直径。",
            ));
        }
    }

    let depth = scalar(
        counterbore_clause,
        &["depth", "hole depth", "深度", "孔深", "深"],
    );
    match depth {
        Some(value) if value.is_finite() && value > 0.0 => {}
        Some(_) => {
            valid = false;
            questions.push(issue(
                "counterbore_depth_conflict_or_invalid",
                "沉孔深度必须唯一、有限且为正数。",
            ));
        }
        None => {
            valid = false;
            questions.push(issue(
                "counterbore_depth_missing",
                "请明确沉孔深度；Quick 不沿用旧 parser 的默认 5 mm。",
            ));
        }
    }

    if let (Some(pilot), Some(counterbore)) = (pilot_diameter, counterbore_diameter) {
        if pilot.is_finite() && counterbore.is_finite() && counterbore <= pilot {
            valid = false;
            questions.push(issue(
                "counterbore_diameter_order_invalid",
                "沉孔直径必须大于中心通孔直径。",
            ));
        }
    }
    let axis = axis_value(clause, questions);
    let axis_explicit = axis.is_some();
    if !valid || questions.iter().any(|q| q["code"] == "axis_conflict") {
        return Vec::new();
    }

    let pilot = pilot_diameter.expect("validated pilot diameter");
    let counterbore = counterbore_diameter.expect("validated counterbore diameter");
    let depth = depth.expect("validated counterbore depth");
    let axis = axis.unwrap_or("Z");
    let axis_source = if axis_explicit {
        user_source(raw)
    } else {
        derived_source(raw, "沿用旧 Python Quick 孔特征未指定轴时的 Z 轴默认值。")
    };
    if !has_unit(clause) {
        warnings.push(issue(
            "unit_interpreted_as_mm",
            "未写长度单位的数值按 CADPlan 默认单位 mm 解释。",
        ));
    }

    let mut pilot_sources = serde_json::Map::new();
    pilot_sources.insert("diameter".into(), user_source(raw));
    pilot_sources.insert("through".into(), user_source(raw));
    pilot_sources.insert("axis".into(), axis_source.clone());
    pilot_sources.insert("pos".into(), user_source(raw));
    pilot_sources.insert(
        "segments".into(),
        derived_source(raw, "沿用旧 Python Quick 孔特征固定的 24 个圆周分段。"),
    );

    let mut counterbore_sources = serde_json::Map::new();
    counterbore_sources.insert("diameter".into(), user_source(raw));
    counterbore_sources.insert(
        "through".into(),
        derived_source(raw, "显式沉孔按旧 Python Quick 语义生成盲切。"),
    );
    counterbore_sources.insert("axis".into(), axis_source);
    counterbore_sources.insert("pos".into(), user_source(raw));
    counterbore_sources.insert("segments".into(), derived_source(raw, "沿用旧 Python Quick 孔特征固定的 24 个圆周分段。"));
    counterbore_sources.insert("depth".into(), user_source(raw));

    vec![
        feature(
            index + 2,
            "hole",
            "cut",
            json!({
                "diameter":pilot,
                "through":true,
                "axis":axis,
                "pos":"center",
                "segments":24
            }),
            Value::Object(pilot_sources),
        ),
        feature(
            index + 3,
            "hole",
            "cut",
            json!({
                "diameter":counterbore,
                "through":false,
                "axis":axis,
                "pos":"center",
                "segments":24,
                "depth":depth
            }),
            Value::Object(counterbore_sources),
        ),
    ]
}

fn build_pcd_counterbore(
    clause: &str,
    raw: &str,
    index: usize,
    questions: &mut Vec<Value>,
    warnings: &mut Vec<Value>,
) -> Vec<Value> {
    if clause.match_indices('带').count() != 1 {
        questions.push(issue(
            "counterbore_separator_missing_or_ambiguous",
            "PCD 沉孔语法需在同一句明确使用一次“带”，分隔通孔与沉孔尺寸。",
        ));
        return Vec::new();
    }
    let Some((pilot_clause, counterbore_clause)) = clause.split_once('带') else {
        questions.push(issue(
            "counterbore_separator_missing_or_ambiguous",
            "PCD 沉孔语法需明确使用“带”分隔通孔与沉孔尺寸。",
        ));
        return Vec::new();
    };
    if contains_any(pilot_clause, &["盲孔", "blind"])
        || !contains_any(pilot_clause, &["通孔", "through", "贯穿"])
    {
        questions.push(issue(
            "counterbore_pilot_type_missing_or_conflicting",
            "PCD 沉孔的先导孔必须明确为通孔，不能描述为盲孔。",
        ));
        return Vec::new();
    }
    if !counterbore_clause.contains("沉孔")
        || contains_any(counterbore_clause, &["通孔", "through", "贯穿", "盲孔", "blind"])
    {
        questions.push(issue(
            "counterbore_type_conflict",
            "沉孔子句必须只描述盲切沉孔，不能同时描述为通孔。",
        ));
        return Vec::new();
    }

    let questions_before_pilot = questions.len();
    let pilot = build_hole(pilot_clause, raw, index, questions, warnings);
    if questions.len() != questions_before_pilot {
        return Vec::new();
    }
    let Some(pilot) = pilot else {
        questions.push(issue(
            "counterbore_pcd_pilot_missing",
            "PCD 沉孔前必须能解析一个完整的分度圆贯穿孔阵列。",
        ));
        return Vec::new();
    };
    if pilot["parameters"]["pos"] != "pcd"
        || pilot["parameters"]["through"] != true
        || pilot["parameters"].get("pcd").is_none()
        || pilot["parameters"].get("count").is_none()
    {
        questions.push(issue(
            "counterbore_pcd_pilot_missing",
            "PCD 沉孔前必须明确分度圆直径、孔数和贯穿孔直径。",
        ));
        return Vec::new();
    }

    let pilot_diameter = pilot["parameters"]["diameter"].as_f64();
    let counterbore_diameter = diameter_value(counterbore_clause);
    let depth = hole_depth_value(counterbore_clause);
    let mut valid = true;
    for (code, value, label) in [
        ("counterbore_diameter_missing", counterbore_diameter, "沉孔直径"),
        ("counterbore_depth_missing", depth, "沉孔深度"),
    ] {
        match value {
            Some(value) if value.is_finite() && value > 0.0 => {}
            Some(_) => {
                valid = false;
                questions.push(issue(
                    if code == "counterbore_diameter_missing" {
                        "counterbore_diameter_conflict"
                    } else {
                        "counterbore_depth_conflict_or_invalid"
                    },
                    format!("{label}必须是唯一、有限且为正数。"),
                ));
            }
            None => {
                valid = false;
                questions.push(issue(code, format!("PCD 沉孔缺少明确的正{label}。")));
            }
        }
    }
    if let (Some(pilot_diameter), Some(counterbore_diameter)) =
        (pilot_diameter, counterbore_diameter)
    {
        if counterbore_diameter.is_finite()
            && pilot_diameter.is_finite()
            && counterbore_diameter <= pilot_diameter
        {
            valid = false;
            questions.push(issue(
                "counterbore_diameter_order_invalid",
                "沉孔直径必须大于 PCD 通孔直径。",
            ));
        }
    }
    if !valid {
        return Vec::new();
    }

    let mut parameters = pilot["parameters"].clone();
    parameters["diameter"] = json!(counterbore_diameter.unwrap());
    parameters["through"] = json!(false);
    parameters["depth"] = json!(depth.unwrap());
    let mut sources = pilot["sources"].as_object().cloned().unwrap_or_default();
    sources.insert("diameter".into(), user_source(raw));
    sources.insert(
        "through".into(),
        derived_source(raw, "显式沉孔按旧 Python Quick 语义生成盲切，并继承先导孔的完整 PCD 阵列。"),
    );
    sources.insert("depth".into(), user_source(raw));
    if !has_unit(clause) {
        warnings.push(issue(
            "unit_interpreted_as_mm",
            "未写长度单位的数值按 CADPlan 默认单位 mm 解释。",
        ));
    }
    warnings.push(issue(
        "pcd_counterbore_inherits_pilot_pattern",
        "沉孔沿用同一子句中先导通孔的 PCD、孔数、轴向和相位。",
    ));

    vec![
        pilot,
        feature(
            index + 3,
            "hole",
            "cut",
            parameters,
            Value::Object(sources),
        ),
    ]
}

fn build_corner_counterbore(
    clause: &str,
    raw: &str,
    index: usize,
    existing_features: &mut Vec<Value>,
    questions: &mut Vec<Value>,
    warnings: &mut Vec<Value>,
) -> Vec<Value> {
    let separator_count = clause.match_indices('带').count();
    if separator_count > 1 {
        questions.push(issue(
            "counterbore_separator_missing_or_ambiguous",
            "四角沉孔子句最多只能有一个“带”分隔符。",
        ));
        return Vec::new();
    }

    let split = if separator_count == 1 {
        clause.split_once('带')
    } else {
        clause.find("沉孔").map(|at| (&clause[..at], &clause[at..]))
    };
    let (pilot_clause, counterbore_clause) = split.unwrap_or(("", clause));
    if !counterbore_clause.contains("沉孔")
        || contains_any(counterbore_clause, &["通孔", "through", "贯穿"])
    {
        questions.push(issue(
            "counterbore_type_conflict",
            "沉孔子句必须描述盲切沉孔，不能同时描述为通孔。",
        ));
        return Vec::new();
    }

    let inline_pilot = has_corner_location(pilot_clause)
        && contains_any(pilot_clause, &["通孔", "through", "贯穿"])
        && !contains_any(pilot_clause, &["盲孔", "blind"]);
    let pilot = if inline_pilot {
        let questions_before_pilot = questions.len();
        let pilot = build_hole(pilot_clause, raw, index, questions, warnings);
        if questions.len() != questions_before_pilot {
            return Vec::new();
        }
        pilot
    } else {
        existing_features
            .last()
            .filter(|feature| {
                feature["kind"] == "hole"
                    && feature["operation"] == "cut"
                    && feature["parameters"]["pos"] == "corners"
                    && feature["parameters"]["through"] == true
            })
            .cloned()
    };
    let Some(mut pilot) = pilot else {
        questions.push(issue(
            "counterbore_corner_pilot_missing",
            "四角沉孔前必须先有明确的四角贯穿孔；请在同一子句中描述先导孔和沉孔。",
        ));
        return Vec::new();
    };

    let Some(pilot_diameter) = pilot["parameters"]["diameter"].as_f64() else {
        questions.push(issue(
            "counterbore_corner_pilot_missing",
            "四角沉孔先导孔缺少有效直径。",
        ));
        return Vec::new();
    };
    if pilot["parameters"]["pos"] != "corners" || pilot["parameters"]["through"] != true {
        questions.push(issue(
            "counterbore_corner_pilot_missing",
            "四角沉孔必须继承四角贯穿先导孔。",
        ));
        return Vec::new();
    }

    let counterbore_diameter = diameter_value(counterbore_clause);
    let depth = hole_depth_value(counterbore_clause);
    let mut valid = true;
    for (code, value, label) in [
        ("counterbore_diameter_missing", counterbore_diameter, "沉孔直径"),
        ("counterbore_depth_missing", depth, "沉孔深度"),
    ] {
        match value {
            Some(value) if value.is_finite() && value > 0.0 => {}
            Some(_) => {
                valid = false;
                questions.push(issue(
                    if code == "counterbore_diameter_missing" {
                        "counterbore_diameter_conflict"
                    } else {
                        "counterbore_depth_conflict_or_invalid"
                    },
                    format!("{label}必须是唯一、有限且为正数。"),
                ));
            }
            None => {
                valid = false;
                questions.push(issue(code, format!("四角沉孔缺少明确的正{label}。")));
            }
        }
    }
    if let Some(counterbore_diameter) = counterbore_diameter
        && counterbore_diameter.is_finite()
        && counterbore_diameter <= pilot_diameter
    {
        valid = false;
        questions.push(issue(
            "counterbore_diameter_order_invalid",
            "沉孔直径必须大于四角先导通孔直径。",
        ));
    }

    let margin_aliases = [
        "edge margin",
        "edge distance",
        "margin",
        "边距",
        "距边",
        "离边",
        "留边",
        "内缩",
    ];
    let pilot_margin = scalar(pilot_clause, &margin_aliases).or_else(|| {
        (!inline_pilot && pilot["sources"]["margin"]["type"] == "user")
            .then(|| pilot["parameters"]["margin"].as_f64())
            .flatten()
    });
    let counterbore_margin = scalar(counterbore_clause, &margin_aliases);
    let explicit_axis = axis_value(clause, questions);
    for margin in [pilot_margin, counterbore_margin].into_iter().flatten() {
        if !margin.is_finite() || margin < 0.0 {
            valid = false;
            questions.push(issue(
                "hole_margin_invalid",
                "四角沉孔边距必须是有限的非负数。",
            ));
        }
    }
    if let (Some(pilot_margin), Some(counterbore_margin)) = (pilot_margin, counterbore_margin)
        && (pilot_margin - counterbore_margin).abs() > 1e-9
    {
        valid = false;
        questions.push(issue(
            "corner_counterbore_margin_conflict",
            "先导孔和沉孔给出了不同的四角边距；请统一边距，或只指定一个外缘边距。",
        ));
    }
    if !valid || !questions.is_empty() {
        return Vec::new();
    }

    let counterbore_diameter = counterbore_diameter.unwrap();
    let depth = depth.unwrap();
    let outer_margin = counterbore_margin
        .or(pilot_margin)
        .or_else(|| (!inline_pilot).then(|| pilot["parameters"]["margin"].as_f64()).flatten())
        .unwrap_or(0.0);
    let concentric_pilot_margin = outer_margin + (counterbore_diameter - pilot_diameter) / 2.0;
    let mut parameters = if inline_pilot {
        pilot["parameters"].clone()
    } else {
        json!({
            "axis": explicit_axis.unwrap_or("Z"),
            "pos": "corners",
            "segments": 24,
            "margin": outer_margin
        })
    };
    let mut sources = if inline_pilot {
        pilot["sources"].as_object().cloned().unwrap_or_default()
    } else {
        let mut sources = serde_json::Map::new();
        sources.insert(
            "axis".into(),
            explicit_axis.map_or_else(
                || derived_source(raw, "沉孔子句未指定轴向时沿用旧 Python Quick 默认 Z 轴。"),
                |_| user_source(raw),
            ),
        );
        sources.insert(
            "pos".into(),
            derived_source(raw, "沉孔子句继承前一个四角贯穿孔的位置类型。"),
        );
        sources.insert(
            "segments".into(),
            derived_source(raw, "沿用旧 Python Quick 孔特征圆周分段数 24。"),
        );
        sources.insert(
            "margin".into(),
            if counterbore_margin.is_some() || (inline_pilot && pilot_margin.is_some()) {
                user_source(raw)
            } else if pilot_margin.is_some() {
                derived_source(raw, "沉孔沿用同一工件先导孔指定的边距作为沉孔外缘间隙。")
            } else {
                derived_source(raw, "四角沉孔未指定边距时沿用 Python Quick 默认值 0 mm。")
            },
        );
        sources
    };

    if let Some(axis) = explicit_axis {
        parameters["axis"] = json!(axis);
        sources.insert("axis".into(), user_source(raw));
        pilot["parameters"]["axis"] = json!(axis);
        pilot["sources"]["axis"] = user_source(raw);
    }
    pilot["parameters"]["margin"] = json!(concentric_pilot_margin);
    pilot["sources"]["margin"] = derived_source(
        raw,
        "为保证先导孔与沉孔同心，按两孔直径差和沉孔外缘间隙推导先导孔边距。",
    );

    parameters["diameter"] = json!(counterbore_diameter);
    parameters["through"] = json!(false);
    parameters["depth"] = json!(depth);
    parameters["margin"] = json!(outer_margin);
    sources.insert("diameter".into(), user_source(raw));
    sources.insert(
        "through".into(),
        derived_source(raw, "显式沉孔按旧 Python Quick 语义生成盲切。"),
    );
    sources.insert("depth".into(), user_source(raw));

    if !has_unit(clause) {
        warnings.push(issue(
            "unit_interpreted_as_mm",
            "未写长度单位的数值按 CADPlan 默认单位 mm 解释。",
        ));
    }
    warnings.push(issue(
        "corner_counterbore_concentric_margin_adjustment",
        "四角边距按沉孔外缘间隙解释；为保证先导孔和沉孔同心，系统会推导先导孔边距。旧 Python Quick 对不同直径重复使用同一边距会造成偏心。",
    ));

    let counterbore = feature(
        if inline_pilot { index + 3 } else { index + 2 },
        "hole",
        "cut",
        parameters,
        Value::Object(sources),
    );
    if inline_pilot {
        vec![pilot, counterbore]
    } else {
        if let Some(existing_pilot) = existing_features.last_mut() {
            *existing_pilot = pilot;
        }
        vec![counterbore]
    }
}

fn build_slot(
    clause: &str,
    raw: &str,
    index: usize,
    questions: &mut Vec<Value>,
    warnings: &mut Vec<Value>,
) -> Option<Vec<Value>> {
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
    let pair = find_dimension_pair(clause);
    let vals = [
        scalar(clause, &["length", "长度", "长"])
            .or_else(|| triple.as_ref().map(|v| v[0]))
            .or_else(|| pair.as_ref().map(|v| v[0])),
        scalar(clause, &["width", "宽度", "宽"])
            .or_else(|| triple.as_ref().map(|v| v[1]))
            .or_else(|| pair.as_ref().map(|v| v[1])),
        scalar(clause, &["depth", "槽深", "深度", "深"]).or_else(|| triple.as_ref().map(|v| v[2])),
    ];
    let defaults = [20.0, 20.0, 5.0];
    let mut defaulted = Vec::new();
    for (index, (key, value)) in ["length", "width", "depth"]
        .into_iter()
        .zip(vals)
        .enumerate()
    {
        match value {
            Some(v) if v.is_finite() && v > 0.0 => {
                p.insert(key.into(), json!(v));
                s.insert(key.into(), user_source(raw));
            }
            Some(_) => questions.push(issue(
                "slot_dimension_missing_or_invalid",
                &format!("矩形槽的 {key} 必须是正数；请给出长、宽、深或 L×W×D。"),
            )),
            None => {
                p.insert(key.into(), json!(defaults[index]));
                s.insert(
                    key.into(),
                    derived_source(
                        raw,
                        &format!(
                            "旧 Python Quick 矩形槽缺少 {key} 时默认使用 {} mm。",
                            defaults[index]
                        ),
                    ),
                );
                defaulted.push((key, defaults[index]));
            }
        }
    }
    let slot_count = match slot_array_count(clause) {
        Ok(Some(count)) => count,
        Ok(None) => 1,
        Err("slot_array_count_conflict") => {
            questions.push(issue(
                "slot_array_count_conflict",
                "矩形槽描述了互相冲突的槽数；请只保留一个数量。",
            ));
            return None;
        }
        Err(_) => {
            questions.push(issue(
                "slot_array_count_invalid",
                "槽数必须是 1 到 4096 之间的整数。",
            ));
            return None;
        }
    };
    p.insert("corner".into(), json!(0.0));
    s.insert(
        "corner".into(),
        derived_source(raw, "旧 Python Quick 矩形槽默认直角轮廓（corner=0）。"),
    );
    match axis_value(clause, questions) {
        Some(axis) => add_user(&mut p, &mut s, raw, "axis", json!(axis)),
        None => {
            p.insert("axis".into(), json!("Z"));
            s.insert(
                "axis".into(),
                derived_source(raw, "未指定槽切入轴时沿用 Python Quick 默认 Z 轴。"),
            );
        }
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
    if !defaulted.is_empty() {
        let summary = defaulted
            .iter()
            .map(|(key, value)| format!("{key}={value} mm"))
            .collect::<Vec<_>>()
            .join("、");
        warnings.push(issue(
            "legacy_slot_dimensions_defaulted",
            &format!(
                "沿用旧 Python Quick 矩形槽默认尺寸（{summary}）；输入未提供这些尺寸，请按需核对。"
            ),
        ));
    }
    if slot_count == 1 {
        return Some(vec![feature(
            index + 2,
            "pocket",
            "cut",
            Value::Object(p),
            Value::Object(s),
        )]);
    }

    if ["length", "width", "depth"]
        .iter()
        .any(|key| !p.get(*key).and_then(Value::as_f64).is_some_and(|value| value > 0.0))
    {
        // The dimension questions above already prevent a plan from being
        // returned. Without length, the legacy spacing rule cannot be applied.
        return Some(Vec::new());
    }

    let length = p["length"].as_f64().expect("validated slot length");
    let pitch = length * 1.5;
    let first_x = -pitch * (slot_count as f64 - 1.0) / 2.0;
    let mut result = Vec::with_capacity(slot_count as usize);
    for slot_index in 0..slot_count {
        let mut parameters = p.clone();
        let mut sources = s.clone();
        parameters.insert("x".into(), json!(first_x + slot_index as f64 * pitch));
        parameters.remove("y");
        sources.remove("y");
        sources.insert(
            "x".into(),
            derived_source(
                raw,
                &format!(
                    "旧 Python Quick 将多个槽沿 X 居中排列，间距为槽长的 1.5 倍（{pitch} mm）。"
                ),
            ),
        );
        result.push(feature(
            index + 2 + slot_index as usize,
            "pocket",
            "cut",
            Value::Object(parameters),
            Value::Object(sources),
        ));
    }
    warnings.push(issue(
        "slot_array_legacy_spacing",
        &format!(
            "多个矩形槽按旧 Quick 规则沿 X 轴居中排列，间距为槽长的 1.5 倍（{pitch} mm）；旧 parser 不使用显式间距或 XY 位置。"
        ),
    ));
    Some(result)
}

fn slot_array_count(s: &str) -> Result<Option<u64>, &'static str> {
    let count_units = ["个", "条", "处", "颗", "枚", "只", "组", "道"];
    let mut found = Vec::<u64>::new();

    for (start, ch) in s.char_indices() {
        if !ch.is_ascii_digit() && ch != '-' && ch != '+' {
            continue;
        }
        if s[..start]
            .chars()
            .next_back()
            .is_some_and(|previous| previous.is_ascii_digit() || previous == '.')
        {
            continue;
        }
        let Some((number, end)) = parse_number(s, start) else {
            continue;
        };
        if !number.unit.is_empty() {
            continue;
        }
        let cursor = skip_ws(s, end);
        if count_units.iter().any(|unit| s[cursor..].starts_with(unit)) {
            if !number.value.is_finite() || number.value.fract() != 0.0 || number.value < 1.0 {
                return Err("slot_array_count_invalid");
            }
            found.push(number.value as u64);
        }
    }

    for (start, ch) in s.char_indices() {
        let Some(count) = mandarin_count(ch) else {
            continue;
        };
        let cursor = skip_ws(s, start + ch.len_utf8());
        if count_units.iter().any(|unit| s[cursor..].starts_with(unit)) {
            found.push(count);
        }
    }

    if found.is_empty() {
        return Ok(None);
    }
    if found.iter().any(|count| *count != found[0]) {
        return Err("slot_array_count_conflict");
    }
    if found[0] > crate::native_plan_geometry::MAX_HOLE_PATTERN_COUNT {
        return Err("slot_array_count_invalid");
    }
    Ok(Some(found[0]))
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
        BaseKind::Prism => extrusion_requested(s),
        BaseKind::Revolve => legacy_revolve_requested(s),
    }
}
fn extrusion_requested(s: &str) -> bool {
    contains_any(s, &["拉伸", "挤出", "拉出", "拉伸体", "extrude", "pad"])
}
fn legacy_plain_cylinder_requested(s: &str) -> bool {
    let has_diameter_or_radius = diameter_value(s)
        .or_else(|| scalar(s, &["radius", "半径"]))
        .is_some_and(|value| value.is_finite() && value > 0.0);
    let has_height = scalar(s, &["height", "高度", "高", "length", "长度", "长"])
        .is_some_and(|value| value.is_finite() && value > 0.0);
    if !has_diameter_or_radius || !has_height {
        return false;
    }
    if contains_any(
        s,
        &["轮廓", "草图", "截面", "剖面", "圆弧", "母线", "扫掠", "旋成", "绕", "轴心线"],
    ) {
        return false;
    }
    !regex::Regex::new(r"(回转体|旋转体|车削|回转成|回转\s*[0-9.]+)")
        .expect("fixed legacy revolve regex")
        .is_match(s)
}
fn legacy_revolve_requested(s: &str) -> bool {
    contains_any(s, &["回转", "旋转体", "车削", "旋转成型", "旋成", "revolve", "lathe"])
        && !legacy_plain_cylinder_requested(s)
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
fn circle_profile(radius: f64, segments: usize) -> Vec<[f64; 2]> {
    (0..segments)
        .map(|index| {
            let angle = std::f64::consts::TAU * index as f64 / segments as f64;
            [radius * angle.cos(), radius * angle.sin()]
        })
        .collect()
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
fn find_dimension_pair(s: &str) -> Option<[f64; 2]> {
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
        let Some((b, _)) = parse_number(s, j) else {
            continue;
        };
        let shared_unit = if b.unit.is_empty() {
            &a.unit
        } else {
            &b.unit
        };
        return Some([
            a.value * scale(if a.unit.is_empty() { shared_unit } else { &a.unit }),
            b.value * scale(if b.unit.is_empty() { shared_unit } else { &b.unit }),
        ]);
    }
    None
}
fn dimension_values(s: &str) -> Vec<f64> {
    let mut values = Vec::new();
    for (start, ch) in s.char_indices() {
        if !ch.is_ascii_digit() && ch != '-' && ch != '+' {
            continue;
        }
        if s[..start]
            .chars()
            .next_back()
            .is_some_and(|previous| previous.is_ascii_digit() || previous == '.')
        {
            continue;
        }
        if let Some((number, _)) = parse_number(s, start) {
            values.push(number.value * scale(&number.unit));
        }
    }
    values
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

fn hole_depth_value(s: &str) -> Option<f64> {
    let aliases = ["depth", "hole depth", "深度", "孔深", "深"];
    let after_label = scalar(s, &aliases);
    let before_label = scalar_before_alias(s, &aliases);
    match (after_label, before_label) {
        (Some(after), Some(before)) if close(after, before) => Some(after),
        (Some(_), Some(_)) => Some(f64::NAN),
        (Some(value), None) | (None, Some(value)) => Some(value),
        (None, None) => None,
    }
}

fn scalar_before_alias(s: &str, aliases: &[&str]) -> Option<f64> {
    let mut found = Vec::<f64>::new();
    for (start, ch) in s.char_indices() {
        if !ch.is_ascii_digit() && ch != '-' && ch != '+' {
            continue;
        }
        if s[..start]
            .chars()
            .next_back()
            .is_some_and(|previous| previous.is_ascii_digit() || previous == '.')
        {
            continue;
        }
        let Some((number, end)) = parse_number(s, start) else {
            continue;
        };
        let preceding = s[..start]
            .trim_end_matches(|ch: char| ch.is_whitespace() || matches!(ch, ':' | '：' | '='))
            .to_lowercase();
        if [
            "hole depth",
            "diameter",
            "radius",
            "length",
            "width",
            "height",
            "depth",
            "直径",
            "孔径",
            "半径",
            "长",
            "宽",
            "高",
            "厚",
            "孔深",
            "深度",
            "分度圆",
            "边距",
        ]
        .iter()
        .any(|label| preceding.ends_with(label))
        {
            // A value introduced by another dimension label (for example
            // "diameter 4 mm depth 3 mm") belongs to that label, even though
            // the next token also happens to be a postfixed depth alias.
            continue;
        }
        let suffix = skip_ws(s, end);
        if aliases.iter().any(|alias| s[suffix..].starts_with(alias)) {
            found.push(number.value * scale(&number.unit));
        }
    }
    if found.is_empty() {
        None
    } else if found.iter().all(|value| close(*value, found[0])) {
        Some(found[0])
    } else {
        Some(f64::NAN)
    }
}

fn is_standalone_depth_clause(s: &str) -> bool {
    contains_any(s, &["depth", "hole depth", "深度", "孔深", "深"])
        && !has_hole(s)
        && !has_slot(s)
        && !contains_any(
            s,
            &[
                "长方体", "长方块", "矩形块", "圆柱", "圆锥", "圆台", "锥台", "球体", "圆球",
                "box", "block", "cylinder", "cone", "frustum", "sphere",
            ],
        )
        && hole_depth_value(s).is_some()
}

fn circular_pattern_angle(s: &str) -> Result<Option<f64>, &'static str> {
    let mut found = Vec::<f64>::new();
    for (start, ch) in s.char_indices() {
        if !ch.is_ascii_digit() && ch != '-' && ch != '+' {
            continue;
        }
        if s[..start]
            .chars()
            .next_back()
            .is_some_and(|previous| previous.is_ascii_digit() || previous == '.')
        {
            continue;
        }
        let Some((number, end)) = parse_number(s, start) else {
            continue;
        };
        let suffix = skip_ws(s, end);
        if s[suffix..].starts_with("rad") || s[suffix..].starts_with("弧度") {
            return Err("radians");
        }
        if number.unit.is_empty()
            && (s[suffix..].starts_with('°')
                || s[suffix..].starts_with("度")
                || s[suffix..].starts_with("deg"))
        {
            found.push(number.value);
        }
    }
    if found.is_empty() {
        Ok(None)
    } else if found.iter().all(|value| close(*value, found[0])) {
        Ok(Some(found[0]))
    } else {
        Err("conflict")
    }
}

fn hole_pattern_count(s: &str) -> Result<Option<u64>, &'static str> {
    let mut found = Vec::<f64>::new();
    for alias in ["hole count", "count", "孔数", "数量", "均布", "等分"] {
        for (index, _) in s.match_indices(alias) {
            let mut cursor = skip_ws(s, index + alias.len());
            if s[cursor..].starts_with('=') || s[cursor..].starts_with(':') {
                cursor += 1;
                cursor = skip_ws(s, cursor);
            }
            if let Some((number, _)) = parse_number(s, cursor) {
                if !number.unit.is_empty() {
                    return Err("hole_pattern_count_invalid");
                }
                found.push(number.value);
            }
        }
    }

    // Support the legacy Mandarin form “打 4 个直径 10 的通孔”, while
    // excluding size labels so “直径 10 个” can never become ten holes.
    for (start, ch) in s.char_indices() {
        if !ch.is_ascii_digit() && ch != '-' && ch != '+' {
            continue;
        }
        if s[..start]
            .chars()
            .next_back()
            .is_some_and(|previous| previous.is_ascii_digit() || previous == '.')
        {
            continue;
        }
        let Some((number, end)) = parse_number(s, start) else {
            continue;
        };
        let before = s[..start].trim_end();
        if ["直径", "diameter", "pcd", "分度圆", "分布圆", "节圆"]
            .iter()
            .any(|label| before.ends_with(label))
        {
            continue;
        }
        let cursor = skip_ws(s, end);
        if ["个", "处", "颗", "枚", "只"]
            .iter()
            .any(|unit| s[cursor..].starts_with(unit))
        {
            found.push(number.value);
        }
    }

    // Match the Python Quick count extraction for one Mandarin numeral token
    // (一二两三四五六七八九十) followed by a count unit. This mirrors its
    // single-character regex; it is not a general Chinese integer parser, and
    // multi-character numerals can therefore retain legacy suffix-matching quirks.
    for (start, ch) in s.char_indices() {
        let Some(value) = mandarin_count(ch) else {
            continue;
        };
        let cursor = skip_ws(s, start + ch.len_utf8());
        if ["个", "条", "处", "颗", "枚", "只", "组"]
            .iter()
            .any(|unit| s[cursor..].starts_with(unit))
        {
            found.push(value as f64);
        }
    }

    if found.is_empty() {
        return Ok(None);
    }
    let first = found[0];
    if found.iter().any(|value| !close(*value, first)) {
        return Err("hole_pattern_count_conflict");
    }
    if !first.is_finite()
        || first.fract() != 0.0
        || first < 2.0
        || first > crate::native_plan_geometry::MAX_HOLE_PATTERN_COUNT as f64
    {
        return Err("hole_pattern_count_invalid");
    }
    Ok(Some(first as u64))
}

fn mandarin_count(ch: char) -> Option<u64> {
    match ch {
        '一' => Some(1),
        '二' | '两' => Some(2),
        '三' => Some(3),
        '四' => Some(4),
        '五' => Some(5),
        '六' => Some(6),
        '七' => Some(7),
        '八' => Some(8),
        '九' => Some(9),
        '十' => Some(10),
        _ => None,
    }
}

fn phase_uses_radians(s: &str) -> bool {
    for alias in ["phase", "相位", "起始角"] {
        for (index, _) in s.match_indices(alias) {
            let cursor = skip_ws(s, index + alias.len());
            if let Some((_, end)) = parse_number(s, cursor) {
                let unit = skip_ws(s, end);
                if s[unit..].starts_with("rad") || s[unit..].starts_with("弧度") {
                    return true;
                }
            }
        }
    }
    false
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
fn has_corner_location(s: &str) -> bool {
    contains_any(s, &["four corners", "corner holes", "四角", "四个角", "各角"])
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
    fn legacy_straight_gear_defaults_and_center_bore_match_quick_constructor() {
        let defaulted = parse("齿轮");
        assert_eq!(defaulted["status"], "ready", "{defaulted}");
        let feature = &defaulted["plan"]["features"][0];
        assert_eq!(feature["kind"], "gear");
        assert_eq!(feature["parameters"]["teeth"], 20);
        assert_eq!(feature["parameters"]["module"], 2.0);
        assert_eq!(feature["parameters"]["height"], 10.0);
        assert_eq!(feature["parameters"]["center"], json!([0.0, 0.0, 0.0]));
        assert_eq!(feature["parameters"]["axis"], "Z");
        assert_eq!(feature["parameters"]["base"], true);
        assert_eq!(feature["sources"]["teeth"]["type"], "derived");
        assert_eq!(feature["sources"]["module"]["type"], "derived");
        assert_eq!(feature["sources"]["height"]["type"], "derived");
        assert!(defaulted["warnings"].to_string().contains("梯形离散齿廓"));
        assert_eq!(
            crate::cad_plan_validation::validate_plan(&defaulted["plan"])["state"],
            "valid"
        );
        assert_eq!(
            crate::native_plan_geometry::preflight(&defaulted["plan"]).unwrap()["state"],
            "valid"
        );

        let explicit = parse("直齿轮 齿数30 齿顶圆直径64 齿宽20 中心孔直径12");
        assert_eq!(explicit["status"], "ready", "{explicit}");
        assert_eq!(explicit["plan"]["features"].as_array().unwrap().len(), 2);
        let gear = &explicit["plan"]["features"][0];
        assert_eq!(gear["parameters"]["teeth"], 30);
        assert_eq!(gear["parameters"]["module"], 2.0);
        assert_eq!(gear["parameters"]["height"], 20.0);
        assert_eq!(gear["sources"]["teeth"]["type"], "user");
        assert_eq!(gear["sources"]["module"]["type"], "derived");
        let bore = &explicit["plan"]["features"][1];
        assert_eq!(bore["kind"], "hole");
        assert_eq!(bore["operation"], "cut");
        assert_eq!(bore["parameters"]["diameter"], 12.0);
        assert_eq!(bore["parameters"]["pos"], "center");
        assert_eq!(bore["parameters"]["through"], true);
        assert_eq!(bore["sources"]["diameter"]["type"], "user");
        assert_eq!(bore["sources"]["pos"]["type"], "user");
        assert_eq!(bore["sources"]["through"]["type"], "derived");
        assert_eq!(
            crate::cad_plan_validation::validate_plan(&explicit["plan"])["state"],
            "valid"
        );
        assert_eq!(
            crate::native_plan_geometry::preflight(&explicit["plan"]).unwrap()["state"],
            "valid"
        );
    }

    #[test]
    fn legacy_gear_rejects_ambiguous_or_unmigrated_requests() {
        let invalid = parse("齿轮 齿数5 模数2");
        assert_eq!(invalid["status"], "needs_clarification", "{invalid}");
        assert!(invalid["questions"].to_string().contains("gear_teeth_invalid"));
        assert!(invalid["plan"].is_null());

        let missing_bore_size = parse("齿轮 中心孔");
        assert_eq!(missing_bore_size["status"], "needs_clarification", "{missing_bore_size}");
        assert!(missing_bore_size["questions"].to_string().contains("gear_bore_diameter_missing"));

        let special = parse("锥齿轮 模数2 齿数30 厚10");
        assert_eq!(special["status"], "unsupported", "{special}");
        assert!(special["unsupported"].to_string().contains("special_gear_type_unsupported"));

        let helical = parse("斜齿轮 模数2 齿数30 齿宽10");
        assert_eq!(helical["status"], "unsupported", "{helical}");
        assert!(helical["unsupported"].to_string().contains("special_gear_type_unsupported"));

        let unsupported_axis = parse("齿轮 模数2 齿数30 厚10 沿X轴");
        assert_eq!(unsupported_axis["status"], "unsupported", "{unsupported_axis}");
        assert!(unsupported_axis["unsupported"].to_string().contains("gear_axis_unsupported"));
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
    fn standalone_box_uses_legacy_defaults_and_unpositioned_holes_request_clarification() {
        let legacy_box = parse("make a box");
        assert_eq!(legacy_box["status"], "ready", "{legacy_box}");
        assert_eq!(
            legacy_box["plan"]["features"][0]["parameters"]["length"],
            100.0
        );
        assert_eq!(
            legacy_box["plan"]["features"][0]["parameters"]["width"],
            60.0
        );
        assert_eq!(
            legacy_box["plan"]["features"][0]["parameters"]["height"],
            20.0
        );
        assert_eq!(
            parse("box 100×60×20 mm, through hole diameter 10 mm")["status"],
            "needs_clarification"
        );
    }

    #[test]
    fn four_corner_holes_match_python_and_keep_margin_provenance() {
        let out = parse("长方体长100宽80高20，四角孔直径10通孔，边距15");
        assert_eq!(out["status"], "ready", "{out}");
        let features = out["plan"]["features"].as_array().unwrap();
        assert_eq!(features.len(), 2);
        assert_eq!(features[1]["parameters"]["pos"], "corners");
        assert_eq!(features[1]["parameters"]["margin"], 15.0);
        assert_eq!(features[1]["sources"]["pos"]["type"], "user");
        assert_eq!(features[1]["sources"]["margin"]["type"], "user");
        assert_eq!(features[1]["sources"]["margin"]["evidence"],
            "长方体长100宽80高20，四角孔直径10通孔，边距15");
    }

    #[test]
    fn four_corner_holes_use_legacy_zero_margin_and_refuse_missing_or_invalid_sizes() {
        let no_margin = plan("长方体长100宽80高20，四角孔直径10通孔");
        assert_eq!(no_margin["features"][1]["parameters"]["margin"], 0.0);
        assert_eq!(no_margin["features"][1]["sources"]["margin"]["type"], "derived");
        assert_eq!(
            crate::cad_plan_validation::validate_plan(&no_margin)["state"],
            "valid"
        );

        let missing_diameter = parse("长方体长100宽80高20，四角通孔，边距15");
        assert_eq!(missing_diameter["status"], "needs_clarification");
        assert!(missing_diameter["questions"].to_string().contains("hole_diameter_missing"));

        let negative_margin = parse("长方体长100宽80高20，四角孔直径10通孔，边距-2");
        assert_eq!(negative_margin["status"], "needs_clarification");
        assert!(negative_margin["questions"].to_string().contains("hole_margin_invalid"));

        let point_conflict = parse("长方体长100宽80高20，四角孔直径10通孔中心坐标(10,10,0)");
        assert_eq!(point_conflict["status"], "needs_clarification");
        assert!(point_conflict["questions"].to_string().contains("hole_position_conflict"));

        let non_box = parse("圆柱直径50高20，四角孔直径5通孔");
        assert_eq!(non_box["status"], "unsupported");
        assert!(non_box["unsupported"].to_string().contains("corner_holes_require_box"));

        let pcd = parse("长方体长100宽80高20，四角孔直径10通孔均布8孔分度圆60");
        assert_eq!(pcd["status"], "unsupported");
        assert!(pcd["unsupported"].to_string().contains("corner_hole_pattern_unsupported"));
    }

    #[test]
    fn explicit_center_counterbore_matches_python_oracle_and_rejects_out_of_scope_requests() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_counterbore_contract.json"
        ))
        .expect("counterbore Quick oracle fixture is valid JSON");
        for case in fixture["legacy_accepted"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            let plan = &output["plan"];
            let validation = crate::cad_plan_validation::validate_plan(plan);
            assert_eq!(validation["state"], "valid", "{input}: {validation}");
            let features = plan["features"].as_array().unwrap();
            assert_eq!(features.len(), 3, "{input}: {features:?}");
            assert_eq!(features[1]["id"], "quick-0002", "{input}");
            assert_eq!(features[2]["id"], "quick-0003", "{input}");

            for expected in case["holes"].as_array().unwrap() {
                let index = expected["index"].as_u64().unwrap() as usize;
                let actual = &features[index];
                assert_eq!(actual["kind"], expected["kind"], "{input}");
                assert_eq!(actual["operation"], expected["operation"], "{input}");
                for (key, value) in expected["parameters"].as_object().unwrap() {
                    assert_eq!(actual["parameters"][key], *value, "{input}: {key}");
                }
                for (key, expected_type) in expected["rust_sources"].as_object().unwrap() {
                    let source = &actual["sources"][key];
                    assert_eq!(source["type"], *expected_type, "{input}: {key}");
                    assert_eq!(source["evidence"], input, "{input}: {key}");
                    if expected_type == "derived" {
                        assert!(source["note"].as_str().is_some_and(|note| !note.is_empty()));
                    }
                }
            }
        }

        for case in fixture["native_rejected"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], case["status"], "{input}: {output}");
            assert!(output["plan"].is_null(), "{input}: {output}");
            let expected_issue = case["issue"].as_str().unwrap();
            let issue_bucket = if output["status"] == "unsupported" {
                &output["unsupported"]
            } else {
                &output["questions"]
            };
            assert!(issue_bucket.to_string().contains(expected_issue), "{input}: {output}");
        }

        for case in fixture["legacy_default_not_migrated"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], case["rust_status"], "{input}: {output}");
            assert!(output["plan"].is_null(), "{input}: {output}");
            assert!(
                output["questions"].to_string().contains(case["rust_issue"].as_str().unwrap()),
                "{input}: {output}"
            );
        }
    }

    #[test]
    fn legacy_pcd_counterbore_inherits_the_pilot_array_and_requires_explicit_depth() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_pcd_counterbore_contract.json"
        ))
        .expect("PCD counterbore Quick oracle fixture is valid JSON");
        for case in fixture["legacy_accepted"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            let plan = &output["plan"];
            assert_eq!(crate::cad_plan_validation::validate_plan(plan)["state"], "valid", "{input}");
            let features = plan["features"].as_array().unwrap();
            assert_eq!(features.len(), 3, "{input}: {features:?}");
            for (offset, expected) in case["holes"].as_array().unwrap().iter().enumerate() {
                let actual = &features[offset + 1];
                assert_eq!(actual["kind"], expected["kind"], "{input}");
                assert_eq!(actual["operation"], expected["operation"], "{input}");
                for (key, value) in expected["parameters"].as_object().unwrap() {
                    assert_eq!(actual["parameters"][key], *value, "{input}: {key}");
                }
                assert_eq!(actual["sources"]["diameter"]["type"], "user", "{input}: diameter");
                assert_eq!(actual["sources"]["diameter"]["evidence"], input, "{input}: diameter evidence");
                for key in ["axis", "pos", "count", "pcd", "segments"] {
                    assert_eq!(actual["sources"][key]["evidence"], input, "{input}: inherited {key}");
                }
            }
            assert_eq!(features[1]["sources"]["through"]["type"], "user");
            assert_eq!(features[2]["sources"]["through"]["type"], "derived");
            assert_eq!(features[2]["sources"]["depth"]["type"], "user");
            assert_eq!(features[2]["sources"]["depth"]["evidence"], input);
            assert!(output["warnings"].to_string().contains("pcd_counterbore_inherits_pilot_pattern"));
        }

        for case in fixture["native_rejected"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], case["rust_status"], "{input}: {output}");
            assert!(output["plan"].is_null(), "{input}: {output}");
            assert!(output["questions"].to_string().contains(case["issue"].as_str().unwrap()), "{input}: {output}");
        }
    }

    #[test]
    fn corner_counterbores_preserve_legacy_inputs_and_correct_centering() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_corner_counterbore_contract.json"
        ))
        .expect("corner counterbore Quick oracle fixture is valid JSON");
        assert_eq!(fixture["legacy_python_cases"].as_array().unwrap().len(), 3);
        for case in fixture["native_expected_cases"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            assert_eq!(
                crate::cad_plan_validation::validate_plan(&output["plan"])["state"],
                "valid",
                "{input}: {output}"
            );
            let features = output["plan"]["features"].as_array().unwrap();
            assert_eq!(features.len(), 3, "{input}");
            assert_eq!(features[1]["kind"], "hole", "{input}");
            assert_eq!(features[1]["operation"], "cut", "{input}");
            assert_eq!(features[1]["parameters"]["diameter"], 6.0, "{input}");
            assert_eq!(features[1]["parameters"]["through"], true, "{input}");
            assert_eq!(
                features[1]["parameters"]["margin"],
                case["pilot_margin_mm"],
                "{input}"
            );
            assert_eq!(features[2]["kind"], "hole", "{input}");
            assert_eq!(features[2]["operation"], "cut", "{input}");
            assert_eq!(features[2]["parameters"]["diameter"], 12.0, "{input}");
            assert_eq!(features[2]["parameters"]["through"], false, "{input}");
            assert_eq!(features[2]["parameters"]["depth"], 4.0, "{input}");
            assert_eq!(
                features[2]["parameters"]["margin"],
                case["counterbore_margin_mm"],
                "{input}"
            );
            assert_eq!(features[1]["sources"]["diameter"]["type"], "user", "{input}");
            assert_eq!(features[2]["sources"]["diameter"]["type"], "user", "{input}");
            assert_eq!(features[1]["sources"]["through"]["type"], "user", "{input}");
            assert_eq!(features[2]["sources"]["through"]["type"], "derived", "{input}");
            assert_eq!(features[2]["sources"]["depth"]["type"], "user", "{input}");
            assert_eq!(features[1]["sources"]["margin"]["type"], "derived", "{input}");
            assert_eq!(
                features[2]["sources"]["margin"]["type"],
                case["counterbore_margin_source"],
                "{input}"
            );
            assert!(output["warnings"]
                .to_string()
                .contains("corner_counterbore_concentric_margin_adjustment"));
        }

        for case in fixture["native_rejected"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], case["status"], "{input}: {output}");
            assert!(output["plan"].is_null(), "{input}: {output}");
            assert!(output["questions"].to_string().contains(case["issue"].as_str().unwrap()), "{input}: {output}");
        }
    }

    #[test]
    fn ordinary_pcd_holes_match_python_golden_and_keep_provenance() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_pcd_contract.json"
        ))
        .expect("PCD Quick oracle fixture is valid JSON");
        for case in fixture["legacy_accepted"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            let validation = crate::cad_plan_validation::validate_plan(&output["plan"]);
            assert_eq!(validation["state"], "valid", "{input}: {validation}");

            let index = case["hole_index"].as_u64().unwrap() as usize;
            let actual = &output["plan"]["features"][index];
            let expected = &case["legacy_hole"];
            assert_eq!(actual["kind"], expected["kind"], "{input}");
            assert_eq!(actual["operation"], "cut", "{input}");
            for (key, value) in expected["params"].as_object().unwrap() {
                assert_eq!(actual["parameters"][key], *value, "{input}: {key}");
            }
            for (key, legacy_source) in expected["provenance"].as_object().unwrap() {
                assert_eq!(legacy_source, "explicit", "{input}: {key}");
                assert_eq!(actual["sources"][key]["type"], "user", "{input}: {key}");
                assert_eq!(actual["sources"][key]["evidence"], input, "{input}: {key}");
            }
        }
    }

    #[test]
    fn pcd_phase_is_an_explicit_native_extension_with_user_provenance() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_pcd_contract.json"
        ))
        .unwrap();
        let extension = &fixture["native_phase_extension"];
        let input = extension["input"].as_str().unwrap();
        let output = parse(input);
        assert_eq!(output["status"], "ready", "{output}");
        let hole = &output["plan"]["features"][1];
        assert_eq!(hole["parameters"]["pos"], "pcd");
        assert_eq!(hole["parameters"]["phase"], extension["phase"]);
        assert_eq!(hole["sources"]["phase"]["type"], "user");
        assert_eq!(hole["sources"]["phase"]["evidence"], input);
        assert!(output["warnings"].to_string().contains("pcd_phase_native_extension"));
        assert_eq!(
            crate::cad_plan_validation::validate_plan(&output["plan"])["state"],
            "valid"
        );

        let english = plan("box 100×80×20 mm, count 4 holes diameter 10mm through PCD 6cm phase 30deg");
        let hole = &english["features"][1];
        assert_eq!(hole["parameters"]["count"], 4);
        assert_eq!(hole["parameters"]["pcd"], 60.0);
        assert_eq!(hole["parameters"]["phase"], 30.0);
    }

    #[test]
    fn pcd_hole_count_accepts_inclusive_limits() {
        for count in [2, crate::native_plan_geometry::MAX_HOLE_PATTERN_COUNT] {
            let input = format!(
                "长方体 长100宽80高20，均布{count}个直径10的通孔分度圆60"
            );
            let output = parse(&input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            assert_eq!(output["plan"]["features"][1]["parameters"]["count"], count);
            assert_eq!(
                crate::cad_plan_validation::validate_plan(&output["plan"])["state"],
                "valid",
                "{input}: {}",
                output["plan"]
            );
        }
    }

    #[test]
    fn ordinary_pcd_holes_fail_closed_on_missing_conflicting_or_out_of_range_values() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_pcd_contract.json"
        ))
        .unwrap();
        for case in fixture["native_rejected"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "needs_clarification", "{input}: {output}");
            assert!(output["plan"].is_null(), "{input}: {output}");
            assert!(
                output["questions"].to_string().contains(case["issue"].as_str().unwrap()),
                "{input}: {output}"
            );
        }
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

    #[test]
    fn migrated_legacy_templates_match_python_parser_golden_contract() {
        let fixture: Value = serde_json::from_str(include_str!("../tests/fixtures/quick_legacy_contract.json"))
            .expect("legacy Quick oracle fixture is valid JSON");
        for case in fixture["accepted"].as_array().unwrap() {
            let output = parse(case["input"].as_str().unwrap());
            assert_eq!(output["status"], "ready", "{}: {output}", case["input"]);
            let validation = crate::cad_plan_validation::validate_plan(&output["plan"]);
            assert_eq!(validation["state"], "valid", "{}: {validation}", case["input"]);
            for expected in case["features"].as_array().unwrap() {
                let id = expected["index"].as_u64().unwrap() as usize;
                let actual = &output["plan"]["features"][id];
                assert_eq!(actual["kind"], expected["kind"], "{}", case["input"]);
                assert_eq!(actual["operation"], expected["operation"], "{}", case["input"]);
                for (key, value) in expected["parameters"].as_object().unwrap() {
                    assert_eq!(actual["parameters"][key], *value, "{}: {key}", case["input"]);
                }
            }
        }
        for case in fixture["rejected"].as_array().unwrap() {
            let output = parse(case["input"].as_str().unwrap());
            assert_eq!(output["status"], case["status"], "{}: {output}", case["input"]);
            assert!(output["plan"].is_null(), "rejected input must not yield a plan: {output}");
        }
    }

    #[test]
    fn blind_hole_depth_fragment_continues_only_to_the_adjacent_blind_hole() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_blind_hole_continuation_contract.json"
        ))
        .expect("blind-hole continuation oracle fixture is valid JSON");

        for case in fixture["legacy_accepted"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            let warnings = output["warnings"]
                .as_array()
                .unwrap_or_else(|| panic!("{input}: warnings must be an array: {output}"));
            assert!(
                warnings
                    .iter()
                    .any(|warning| warning["code"] == "unit_interpreted_as_mm"),
                "{input}: Rust Quick should keep its existing default-mm warning: {output}"
            );
            let validation = crate::cad_plan_validation::validate_plan(&output["plan"]);
            assert_eq!(validation["state"], "valid", "{input}: {validation}");

            let index = case["hole_index"].as_u64().unwrap() as usize;
            let actual = &output["plan"]["features"][index];
            let expected = &case["legacy_hole"];
            assert_eq!(actual["kind"], expected["kind"], "{input}");
            assert_eq!(actual["operation"], expected["op"], "{input}");
            for key in case["rust_parameter_keys"].as_array().unwrap() {
                let key = key.as_str().unwrap();
                assert_eq!(
                    actual["parameters"][key], expected["params"][key],
                    "{input}: {key}"
                );
            }
            for (key, value) in expected["provenance"].as_object().unwrap() {
                assert_eq!(value, "explicit", "{input}: {key}");
                assert_eq!(actual["sources"][key]["type"], "user", "{input}: {key}");
                assert_eq!(actual["sources"][key]["evidence"], input, "{input}: {key}");
            }
        }

        for case in fixture["rust_rejected"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "needs_clarification", "{input}: {output}");
            assert!(output["plan"].is_null(), "{input}: {output}");
            assert!(
                output["questions"].to_string().contains(case["issue"].as_str().unwrap()),
                "{input}: {output}"
            );
        }
    }

    #[test]
    fn multiple_rectangular_slots_match_legacy_default_pitch_and_reject_ambiguous_counts() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_multiple_slot_contract.json"
        ))
        .expect("multiple-slot oracle fixture is valid JSON");

        for case in fixture["legacy_accepted"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            let validation = crate::cad_plan_validation::validate_plan(&output["plan"]);
            assert_eq!(validation["state"], "valid", "{input}: {validation}");

            let features = output["plan"]["features"].as_array().unwrap();
            assert_eq!(features.len(), 3, "{input}: {output}");
            for (actual, expected) in features.iter().zip(case["feature_kinds"].as_array().unwrap()) {
                assert_eq!(actual["kind"], *expected, "{input}");
            }
            for expected in case["slots"].as_array().unwrap() {
                let index = expected["feature_index"].as_u64().unwrap() as usize;
                let actual = &features[index];
                assert_eq!(actual["operation"], expected["op"], "{input}: slot {index}");
                for (key, value) in expected["params"].as_object().unwrap() {
                    assert_eq!(actual["parameters"][key], *value, "{input}: slot {index} {key}");
                }
                assert_eq!(actual["sources"]["x"]["type"], "derived", "{input}: slot {index}");
                assert_eq!(actual["sources"]["length"]["type"], "user", "{input}: slot {index}");
                assert_eq!(actual["sources"]["width"]["type"], "user", "{input}: slot {index}");
                assert_eq!(actual["sources"]["depth"]["type"], "user", "{input}: slot {index}");
            }
            assert!(
                output["warnings"].to_string().contains("slot_array_legacy_spacing"),
                "{input}: missing fallback-spacing warning: {output}"
            );
        }

        for case in fixture["rust_rejected"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "needs_clarification", "{input}: {output}");
            assert!(output["plan"].is_null(), "{input}: {output}");
            assert!(
                output["questions"].to_string().contains(case["issue"].as_str().unwrap()),
                "{input}: {output}"
            );
        }
    }

    #[test]
    fn standalone_legacy_boxes_fill_missing_dimensions_from_python_defaults() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_box_defaults_contract.json"
        ))
        .expect("legacy box defaults fixture is valid JSON");

        for case in fixture["legacy_accepted"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            let validation = crate::cad_plan_validation::validate_plan(&output["plan"]);
            assert_eq!(validation["state"], "valid", "{input}: {validation}");
            let features = output["plan"]["features"].as_array().unwrap();
            assert_eq!(features.len(), 1, "{input}: {output}");
            let feature = &features[0];
            assert_eq!(feature["kind"], "box", "{input}");
            for (key, expected) in ["length", "width", "height"]
                .into_iter()
                .zip(case["dimensions_mm"].as_array().unwrap())
            {
                assert_eq!(feature["parameters"][key], *expected, "{input}: {key}");
                let defaulted = case["defaulted_fields"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|value| value == key);
                assert_eq!(
                    feature["sources"][key]["type"],
                    if defaulted { "derived" } else { "user" },
                    "{input}: {key} source"
                );
            }
            assert!(
                output["warnings"]
                    .to_string()
                    .contains("legacy_box_dimensions_defaulted"),
                "{input}: missing visibility warning for legacy defaults: {output}"
            );
        }
    }

    #[test]
    fn rectangular_slots_fill_missing_dimensions_from_python_defaults() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_slot_defaults_contract.json"
        ))
        .expect("legacy slot defaults fixture is valid JSON");

        for case in fixture["legacy_accepted"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            let validation = crate::cad_plan_validation::validate_plan(&output["plan"]);
            assert_eq!(validation["state"], "valid", "{input}: {validation}");

            let features = output["plan"]["features"].as_array().unwrap();
            let expected_slots = case["slots"].as_array().unwrap();
            assert_eq!(features.len(), expected_slots.len() + 1, "{input}: {output}");
            let defaulted_fields = case["defaulted_fields"].as_array().unwrap();
            for expected in expected_slots {
                let index = expected["feature_index"].as_u64().unwrap() as usize;
                let actual = &features[index];
                assert_eq!(actual["kind"], "pocket", "{input}: slot {index}");
                assert_eq!(actual["operation"], "cut", "{input}: slot {index}");
                for (key, value) in expected["params"].as_object().unwrap() {
                    assert_eq!(
                        actual["parameters"][key], *value,
                        "{input}: slot {index} {key}"
                    );
                    if ["length", "width", "depth"].contains(&key.as_str()) {
                        let was_defaulted = defaulted_fields
                            .iter()
                            .any(|field| field.as_str() == Some(key.as_str()));
                        assert_eq!(
                            actual["sources"][key]["type"],
                            if was_defaulted { "derived" } else { "user" },
                            "{input}: slot {index} {key} source"
                        );
                    }
                }
            }

            let warnings = output["warnings"].to_string();
            if !defaulted_fields.is_empty() {
                assert!(
                    warnings.contains("legacy_slot_dimensions_defaulted"),
                    "{input}: defaults must be visible: {output}"
                );
            }
            if expected_slots.len() > 1 {
                assert!(
                    warnings.contains("slot_array_legacy_spacing"),
                    "{input}: {output}"
                );
            }
        }

        for case in fixture["native_rejected"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], case["status"], "{input}: {output}");
            assert!(output["plan"].is_null(), "{input}: {output}");
            let issue_bucket = if output["status"] == "unsupported" {
                &output["unsupported"]
            } else {
                &output["questions"]
            };
            assert!(
                issue_bucket
                    .to_string()
                    .contains(case["issue"].as_str().unwrap()),
                "{input}: {output}"
            );
        }

        let invalid = parse("长方体 长100宽60高20，矩形槽 长0宽8深2");
        assert_eq!(invalid["status"], "needs_clarification", "{invalid}");
        assert!(invalid["questions"]
            .to_string()
            .contains("slot_dimension_missing_or_invalid"));
    }

    #[test]
    fn standalone_legacy_cylinders_cones_and_spheres_match_python_defaults() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_primitive_defaults_contract.json"
        ))
        .expect("legacy primitive defaults fixture is valid JSON");

        for case in fixture["legacy_accepted"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            let validation = crate::cad_plan_validation::validate_plan(&output["plan"]);
            assert_eq!(validation["state"], "valid", "{input}: {validation}");
            let features = output["plan"]["features"].as_array().unwrap();
            assert_eq!(features.len(), 1, "{input}: {output}");
            let feature = &features[0];
            assert_eq!(feature["kind"], case["feature_kind"], "{input}");
            for (key, expected) in case["parameters"].as_object().unwrap() {
                assert_eq!(feature["parameters"][key], *expected, "{input}: {key}");
            }
            for field in case["defaulted_fields"].as_array().unwrap() {
                let field = field.as_str().unwrap();
                assert_eq!(
                    feature["sources"][field]["type"], "derived",
                    "{input}: {field}"
                );
                assert!(
                    feature["sources"][field]["note"]
                        .as_str()
                        .is_some_and(|note| !note.is_empty()),
                    "{input}: default source needs an explanation"
                );
            }
            for field in case["raw_mapped_fields"].as_array().unwrap() {
                let field = field.as_str().unwrap();
                assert_eq!(
                    feature["sources"][field]["type"], "derived",
                    "{input}: {field}"
                );
            }
            let warnings = output["warnings"].to_string();
            if !case["defaulted_fields"].as_array().unwrap().is_empty() {
                assert!(
                    warnings.contains("legacy_primitive_dimensions_defaulted"),
                    "{input}: missing default warning: {output}"
                );
            }
            if !case["raw_mapped_fields"].as_array().unwrap().is_empty() {
                assert!(
                    warnings.contains("legacy_primitive_raw_dimensions_mapped"),
                    "{input}: missing raw mapping warning: {output}"
                );
            }
        }

        let invalid = parse("圆柱 直径0 高10");
        assert_eq!(invalid["status"], "needs_clarification", "{invalid}");
        assert!(
            invalid["questions"]
                .to_string()
                .contains("cylinder_radius_missing")
        );
        let modified = parse("圆柱 高50，中心通孔直径10");
        assert_eq!(modified["status"], "needs_clarification", "{modified}");
        assert!(
            modified["questions"]
                .to_string()
                .contains("cylinder_radius_missing")
        );
    }

    #[test]
    fn legacy_extrusion_revolve_and_plain_cylinder_match_python_contract() {
        use serde_json::Value;

        fn assert_json_matches(actual: &Value, expected: &Value, path: &str) {
            if let (Some(actual), Some(expected)) = (actual.as_f64(), expected.as_f64()) {
                assert!(
                    (actual - expected).abs() <= 1e-8,
                    "{path}: actual={actual}, expected={expected}"
                );
                return;
            }
            match (actual, expected) {
                (Value::Array(actual), Value::Array(expected)) => {
                    assert_eq!(actual.len(), expected.len(), "{path}: array length");
                    for (index, (actual, expected)) in actual.iter().zip(expected).enumerate() {
                        assert_json_matches(actual, expected, &format!("{path}[{index}]"));
                    }
                }
                (Value::Object(actual), Value::Object(expected)) => {
                    for (key, expected) in expected {
                        assert_json_matches(
                            actual.get(key).unwrap_or(&Value::Null),
                            expected,
                            &format!("{path}.{key}"),
                        );
                    }
                }
                _ => assert_eq!(actual, expected, "{path}"),
            }
        }

        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_extrusion_revolve_contract.json"
        ))
        .expect("legacy extrusion/revolve fixture is valid JSON");
        for case in fixture["legacy_accepted"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            let features = output["plan"]["features"].as_array().unwrap();
            assert_eq!(features.len(), 1, "{input}: {output}");
            assert_eq!(features[0]["kind"], case["feature_kind"], "{input}");
            assert_json_matches(
                &features[0]["parameters"],
                &case["parameters"],
                input,
            );
            let validation = crate::cad_plan_validation::validate_plan(&output["plan"]);
            assert_eq!(validation["state"], "valid", "{input}: {validation}");
        }

        let unsupported_axis = parse("回转体 半径20 高30 回转180度 绕X轴");
        assert_eq!(unsupported_axis["status"], "needs_clarification");
        assert!(unsupported_axis["questions"].to_string().contains("revolve_axis_fixed_to_z"));
        let invalid_prism = parse("拉伸 长0宽20高10");
        assert_eq!(invalid_prism["status"], "needs_clarification");
        assert!(invalid_prism["questions"].to_string().contains("extrusion_profile_dimensions_invalid"));
        let invalid_angle = parse("回转体 半径20 高30 回转0度");
        assert_eq!(invalid_angle["status"], "needs_clarification");
        assert!(invalid_angle["questions"].to_string().contains("revolve_angle_invalid"));
    }

    #[test]
    fn legacy_box_corner_profiles_match_python_contract() {
        use serde_json::Value;

        fn assert_json_matches(actual: &Value, expected: &Value, path: &str) {
            if let (Some(actual), Some(expected)) = (actual.as_f64(), expected.as_f64()) {
                assert!(
                    (actual - expected).abs() <= 1e-8,
                    "{path}: actual={actual}, expected={expected}"
                );
                return;
            }
            match (actual, expected) {
                (Value::Array(actual), Value::Array(expected)) => {
                    assert_eq!(actual.len(), expected.len(), "{path}: array length");
                    for (index, (actual, expected)) in actual.iter().zip(expected).enumerate() {
                        assert_json_matches(actual, expected, &format!("{path}[{index}]"));
                    }
                }
                (Value::Object(actual), Value::Object(expected)) => {
                    for (key, expected) in expected {
                        assert_json_matches(
                            actual.get(key).unwrap_or(&Value::Null),
                            expected,
                            &format!("{path}.{key}"),
                        );
                    }
                }
                _ => assert_eq!(actual, expected, "{path}"),
            }
        }

        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_box_corners_contract.json"
        ))
        .expect("legacy box corner fixture is valid JSON");
        for case in fixture["legacy_accepted"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            let features = output["plan"]["features"].as_array().unwrap();
            assert_eq!(features.len(), 1, "{input}: {output}");
            let feature = &features[0];
            assert_eq!(feature["kind"], case["feature_kind"], "{input}");
            assert_json_matches(&feature["parameters"], &case["parameters"], input);
            assert_eq!(
                feature["sources"]["corner"]["type"],
                case["rust_corner_source"],
                "{input}: explicit corner evidence must not inherit the legacy provenance bug"
            );
            if let Some(warning) = case["warning_contains"].as_str() {
                assert!(output["warnings"].to_string().contains(warning), "{input}: {output}");
            }
            let validation = crate::cad_plan_validation::validate_plan(&output["plan"]);
            assert_eq!(validation["state"], "valid", "{input}: {validation}");
        }

        for case in fixture["native_rejected"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], case["status"], "{input}: {output}");
            assert!(output["plan"].is_null(), "{input}: {output}");
            let issue_bucket = if output["status"] == "unsupported" {
                &output["unsupported"]
            } else {
                &output["questions"]
            };
            assert!(issue_bucket.to_string().contains(case["issue"].as_str().unwrap()), "{output}");
        }
    }

    #[test]
    fn explicit_linear_pattern_matches_python_oracle_and_rejects_incomplete_requests() {
        let fixture: Value = serde_json::from_str(include_str!("../tests/fixtures/quick_linear_pattern_contract.json")).unwrap();
        for case in fixture["legacy_accepted"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{output}");
            let plan = &output["plan"];
            let validation = crate::cad_plan_validation::validate_plan(plan);
            assert_eq!(validation["state"], "valid", "{validation}");
            let features = plan["features"].as_array().unwrap();
            assert_eq!(features[0]["kind"], case["source_kind"]);
            let pattern = features.last().unwrap();
            for (key, value) in case["pattern"].as_object().unwrap() {
                assert_eq!(pattern["parameters"][key], *value, "{key}");
            }
            assert_eq!(pattern["kind"], "pattern");
            assert_eq!(pattern["operation"], "add");
            assert_eq!(pattern["relations"][0]["feature_id"], features[0]["id"]);
        }
        for input in [
            "圆柱 直径30mm 长50mm，沿X方向阵列4个",
            "圆柱 直径30mm 长50mm，阵列4个，间距40mm",
            "圆柱 直径30mm 长50mm，沿X方向阵列1个，间距40mm",
        ] {
            let output = parse(input);
            assert_eq!(output["status"], "needs_clarification", "{output}");
            assert!(output["plan"].is_null(), "{output}");
        }
    }

    #[test]
    fn explicit_circular_pattern_matches_python_oracle_and_rejects_incomplete_requests() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_circular_pattern_contract.json"
        ))
        .expect("circular Quick oracle fixture is valid JSON");
        for case in fixture["legacy_accepted"].as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = parse(input);
            assert_eq!(output["status"], "ready", "{input}: {output}");
            let plan = &output["plan"];
            let validation = crate::cad_plan_validation::validate_plan(plan);
            assert_eq!(validation["state"], "valid", "{input}: {validation}");

            let features = plan["features"].as_array().unwrap();
            let source_index = case["source_index"].as_u64().unwrap() as usize;
            let source = &features[source_index];
            let pattern = features.last().unwrap();
            assert_eq!(source["kind"], case["source_kind"], "{input}");
            assert_eq!(source["operation"], case["source_operation"], "{input}");
            assert_eq!(pattern["kind"], "pattern", "{input}");
            assert_eq!(pattern["operation"], source["operation"], "{input}");
            for (key, value) in case["pattern"].as_object().unwrap() {
                assert_eq!(pattern["parameters"][key], *value, "{input}: {key}");
            }
            assert_eq!(
                pattern["relations"][0]["feature_id"],
                source["id"],
                "{input}"
            );
            for key in ["count", "axis", "angle"] {
                assert_eq!(pattern["sources"][key]["type"], "user", "{input}: {key}");
                assert_eq!(
                    pattern["sources"][key]["evidence"], input,
                    "{input}: {key}"
                );
            }
            for key in ["full", "center"] {
                assert_eq!(
                    pattern["sources"][key]["type"], "derived",
                    "{input}: {key}"
                );
            }
        }

        for (input, issue_id) in [
            (
                "圆柱 直径30mm 长50mm，绕Z轴圆周阵列4个",
                "circular_pattern_values_missing",
            ),
            (
                "圆柱 直径30mm 长50mm，绕Z轴圆周阵列角度180度",
                "circular_pattern_values_missing",
            ),
            (
                "圆柱 直径30mm 长50mm，圆周阵列4个角度180度",
                "circular_pattern_values_missing",
            ),
            (
                "圆柱 直径30mm 长50mm，绕Z轴圆周阵列4个角度180rad",
                "circular_pattern_angle_unit_unsupported",
            ),
            (
                "圆柱 直径30mm 长50mm，绕Z轴圆周阵列1个角度180度",
                "circular_pattern_count_invalid",
            ),
            (
                "圆柱 直径30mm 长50mm，绕Z轴圆周阵列4个角度0度",
                "circular_pattern_angle_invalid",
            ),
        ] {
            let output = parse(input);
            assert_eq!(output["status"], "needs_clarification", "{input}: {output}");
            assert!(output["plan"].is_null(), "{input}: {output}");
            assert!(output["questions"].to_string().contains(issue_id), "{input}: {output}");
        }
    }

    #[test]
    fn disabled_native_unsupported_feature_is_retained_but_skipped_by_preflight() {
        let mut candidate = parse("box 20×10×8 mm")["plan"].clone();
        let mut disabled = candidate["features"][0].clone();
        disabled["id"] = json!("disabled-rounded-box");
        disabled["name"] = json!("disabled feature");
        disabled["enabled"] = json!(false);
        disabled["parameters"]["corner"] = json!(4.0);
        disabled["sources"]["corner"] = derived_source("disabled fixture", "test");
        candidate["features"].as_array_mut().unwrap().push(disabled);

        assert_eq!(
            crate::native_plan_geometry::preflight(&candidate).unwrap()["state"],
            "valid"
        );
        assert_eq!(candidate["features"][1]["enabled"], false);
        assert_eq!(candidate["features"][1]["parameters"]["corner"], 4.0);
    }
}
