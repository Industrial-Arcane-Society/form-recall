//! CADPlan-to-OCCT execution. Unsupported variants fail before kernel execution.
//! Hole reports retain sampled evidence and final B-Rep bore measurements.
use crate::{
    cad_plan, cad_plan_validation, native_bevel_gear, native_bore_metrology,
    native_geometry_evidence::{self, HoleRequest},
    native_occt::{Axis, Bounds, Kernel, Shape},
    native_impeller, native_loft, native_parametric_features, native_standard_features,
    native_timing_pulley,
};
use serde_json::{Value, json};
use std::{
    path::Path,
    sync::atomic::{AtomicBool, Ordering},
};

pub struct BuiltPlan {
    pub shape: Shape,
    pub report: Value,
}

const MAX_PATTERN_COUNT: u64 = 10_000;
const MAX_PATTERN_TRANSFORMED_TOOLS: usize = 100_000;
/// PCD hole arrays are bounded separately from feature transform patterns.
pub const MAX_HOLE_PATTERN_COUNT: u64 = 4096;

/// Native plan-builder kinds. Keep this contract aligned with the dispatch
/// branches in `build` and `feature_tools`; registry tests enforce parity.
pub fn supports_native_kind(kind: &str) -> bool {
    matches!(
        kind,
        "box"
            | "cylinder"
            | "sphere"
            | "hole"
            | "pocket"
            | "prism"
            | "revolve"
            | "pattern"
            | "gear"
            | "helical_gear"
            | "bevel_gear"
            | "socket_head_bolt"
            | "hex_bolt"
            | "spoked_wheel"
            | "piston"
            | "conrod"
            | "cross_joint"
            | "rotor"
            | "camshaft"
            | "crankshaft"
            | "timing_pulley"
            | "vbelt_pulley"
            | "scroll_disc"
            | "helix"
            | "loft"
            | "worm"
            | "impeller"
            | "shell"
    )
}

struct PendingBore {
    report_index: usize,
    request: HoleRequest,
    expected_blind_depth: Option<f64>,
    counterbore_pilot_radius: Option<f64>,
}

struct PendingBoreGroup {
    pre_cut: Shape,
    instances: Vec<PendingBore>,
}

pub fn preflight(plan: &Value) -> Result<Value, String> {
    let validation = cad_plan_validation::validate_plan(plan);
    if validation["state"] != "valid" {
        return Err(format!("CADPlan 校验未通过：{}", validation));
    }
    for feature in plan["features"].as_array().ok_or("缺少 features")? {
        if feature.get("enabled").and_then(Value::as_bool) == Some(false) {
            continue;
        }
        let p = &feature["parameters"];
        let kind = feature["kind"].as_str().unwrap_or("");
        if !supports_native_kind(kind) {
            return Err(format!(
                "Rust 几何执行尚不支持 {} ({kind})，未执行任何特征。",
                feature["id"]
            ));
        }
        if kind == "shell"
            && (plan.get("schema_version").and_then(Value::as_str) != Some("1.1")
                || ["thickness", "offset_mode", "removed_faces"]
                    .iter()
                    .any(|key| p.get(*key).is_none())
                || feature.get("selection_binding").is_none()
                || feature.get("operation").and_then(Value::as_str) != Some("cut")
                || !feature
                    .get("relations")
                    .and_then(Value::as_array)
                    .is_some_and(|rows| {
                        rows.iter()
                            .filter(|row| {
                                row.get("type").and_then(Value::as_str) == Some("shell_target")
                            })
                            .count()
                            == 1
                    }))
        {
            return Err(format!(
                "特征 {} 不是完整的 CADPlan 1.1 显式 shell；旧 thickness-only shell 不支持自动迁移。请重新枚举面，并提供 offset_mode、唯一 shell_target、removed_faces 与 selection_binding。",
                feature["id"]
            ));
        }
        if kind == "pocket" && optional_num(p, "corner", 0.)? != 0. {
            return Err("Rust 圆角矩形槽轮廓尚未接入；不能静默忽略 pocket.corner。".into());
        }
        if kind == "hole" && p.get("entry_point").is_some_and(|v| !v.is_null()) {
            return Err("Rust 显式盲孔入口局部材料核验尚未接入。".into());
        }
        let required_native_parameters: &[&str] = match kind {
            "gear" | "helical_gear" => &["teeth", "module", "height"],
            "bevel_gear" => &["teeth", "module", "height", "cone_deg"],
            "socket_head_bolt" => &[
                "shank_d",
                "thread_pitch",
                "length",
                "head_d",
                "head_h",
                "hex_s",
                "hex_t",
            ],
            "hex_bolt" => &["shank_d", "length", "across_flats", "head_h", "thread_pitch"],
            "piston" => &["diameter", "height", "pin_d", "grooves"],
            "spoked_wheel" => &["outer_r", "rim_w", "hub_r", "height", "spokes", "spoke_w"],
            "conrod" => &[
                "length",
                "big_d",
                "small_d",
                "big_bore",
                "small_bore",
                "shank_w",
                "thick",
            ],
            "cross_joint" => &["span", "journal_d"],
            "rotor" => &["radius", "n", "height", "bore_d"],
            "vbelt_pulley" => &["pitch_d", "grooves", "height", "groove_type"],
            "camshaft" => &["length", "journal_d", "base_d", "lift", "lobes", "lobe_deg"],
            "crankshaft" => &["length", "main_d", "pin_d", "stroke", "throws"],
            "timing_pulley" => &["pitch_d", "teeth", "height", "tooth_type"],
            "scroll_disc" => &["outer_d", "height", "wall_t", "turns"],
            "helix" => &["radius", "wire_r", "height", "turns"],
            "loft" => &["root_chord", "tip_chord", "span", "thickness"],
            "worm" => &["radius", "height", "thread_r", "wire_r", "turns"],
            "impeller" => &["hub_d", "tip_d", "height", "blades", "twist_deg"],
            _ => &[],
        };
        for key in required_native_parameters {
            if p.get(*key).is_none() || p[*key].is_null() {
                return Err(format!(
                    "{} 缺少必填几何参数 {key}；拒绝用默认值猜测。",
                    feature["id"]
                ));
            }
        }
        if matches!(kind, "gear" | "helical_gear") {
            let teeth = p.get("teeth").and_then(Value::as_u64).ok_or_else(|| {
                format!(
                    "{} 的 {}.teeth 必须是整数。",
                    feature["id"].as_str().unwrap_or("feature"),
                    kind
                )
            })?;
            if !(native_standard_features::MIN_GEAR_TEETH
                ..=native_standard_features::MAX_GEAR_TEETH)
                .contains(&teeth)
            {
                return Err(format!(
                    "{} 的 {}.teeth={teeth} 超出原生支持范围 {}..={}；请调整齿数后再预览。",
                    feature["id"].as_str().unwrap_or("feature"),
                    kind,
                    native_standard_features::MIN_GEAR_TEETH,
                    native_standard_features::MAX_GEAR_TEETH
                ));
            }
        }
        if kind == "piston" {
            let normalized = normalized_feature(feature, plan)?;
            native_parametric_features::preflight_piston(&normalized["parameters"])?;
        }
        if kind == "bevel_gear" {
            let normalized = normalized_feature(feature, plan)?;
            native_bevel_gear::preflight(&normalized["parameters"])?;
        }
        if kind == "camshaft" {
            let normalized = normalized_feature(feature, plan)?;
            native_parametric_features::preflight_camshaft(&normalized["parameters"])?;
        }
        if kind == "crankshaft" {
            let normalized = normalized_feature(feature, plan)?;
            native_parametric_features::preflight_crankshaft(&normalized["parameters"])?;
        }
        if kind == "timing_pulley" {
            let normalized = normalized_feature(feature, plan)?;
            native_timing_pulley::preflight(&normalized["parameters"])?;
        }
        if kind == "spoked_wheel" {
            let normalized = normalized_feature(feature, plan)?;
            native_parametric_features::preflight_spoked_wheel(&normalized["parameters"])?;
        }
        if kind == "conrod" {
            let normalized = normalized_feature(feature, plan)?;
            native_parametric_features::preflight_conrod(&normalized["parameters"])?;
        }
        if kind == "cross_joint" {
            let normalized = normalized_feature(feature, plan)?;
            native_parametric_features::preflight_cross_joint(&normalized["parameters"])?;
        }
        if kind == "rotor" {
            let normalized = normalized_feature(feature, plan)?;
            crate::native_rotor::preflight(&normalized["parameters"])?;
        }
        if kind == "vbelt_pulley" {
            let normalized = normalized_feature(feature, plan)?;
            crate::native_vbelt_pulley::preflight(&normalized["parameters"])?;
        }
        if kind == "scroll_disc" {
            let normalized = normalized_feature(feature, plan)?;
            crate::native_scroll_disc::preflight(&normalized["parameters"])?;
        }
        if kind == "helix" {
            let normalized = normalized_feature(feature, plan)?;
            crate::native_helix::preflight(&normalized["parameters"])?;
        }
        if kind == "worm" {
            let normalized = normalized_feature(feature, plan)?;
            crate::native_worm::preflight(&normalized["parameters"])?;
        }
        if kind == "loft" {
            let normalized = normalized_feature(feature, plan)?;
            native_loft::preflight(&normalized["parameters"])?;
        }
        if kind == "impeller" {
            let normalized = normalized_feature(feature, plan)?;
            native_impeller::preflight(&normalized["parameters"])?;
        }
        if kind == "pattern" {
            let count = p
                .get("count")
                .and_then(Value::as_u64)
                .filter(|count| *count > 0)
                .ok_or("pattern.count 必须是大于零的整数。")?;
            usize::try_from(count).map_err(|_| "pattern.count 超出当前平台可表示范围。")?;
            if count > MAX_PATTERN_COUNT {
                return Err(format!(
                    "pattern.count={count} 超过当前 OCCT 单特征上限 {MAX_PATTERN_COUNT}；请拆分为多个可复核特征。"
                ));
            }
            match p.get("mode").and_then(Value::as_str) {
                Some("linear") => {
                    point(p.get("step"), [10., 0., 0.])?;
                }
                Some("circular") => {
                    axis_index(p)?;
                    optional_num(p, "angle", 360.)?;
                    point(p.get("center"), [0.; 3])?;
                }
                _ => return Err("pattern.mode 必须是 linear 或 circular。".into()),
            }
        }
    }
    Ok(validation)
}

pub fn build(plan: &Value, manifest: &Path, cancelled: &AtomicBool) -> Result<BuiltPlan, String> {
    let validation = preflight(plan)?;
    check_cancel(cancelled)?;
    let kernel = Kernel::load_manifest(manifest)?;
    let mut current: Option<Shape> = None;
    let mut rows = Vec::new();
    let mut holes = Vec::new();
    let mut pending_bore_groups = Vec::new();
    let mut previous_feature_id: Option<String> = None;
    let mut previous_feature_tools = Vec::<Shape>::new();
    let mut previous_feature_holes = Vec::<Value>::new();
    for (feature_index, feature) in plan["features"]
        .as_array()
        .ok_or("缺少 features")?
        .iter()
        .enumerate()
    {
        check_cancel(cancelled)?;
        if feature.get("enabled").and_then(Value::as_bool) == Some(false) {
            continue;
        }
        let feature_id = feature["id"].as_str().unwrap_or("").to_owned();
        let feature_result = (|| -> Result<(), String> {
            let before_volume = current
                .as_ref()
                .map(Shape::volume)
                .transpose()?
                .unwrap_or(0.);
            let bb = current.as_ref().map(Shape::bounds).transpose()?;
            let normalized = normalized_feature(feature, plan)?;
            if normalized["kind"] == "shell" {
                let stock = current
                    .as_ref()
                    .ok_or("shell 必须作用于当前唯一闭合实体。")?;
                let analysis = stock.analyze()?;
                if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1
                {
                    return Err("shell 目标必须是当前唯一有效闭合实体。".into());
                }
                let prefix = plan_prefix(plan, feature_index)?;
                let expected_fingerprint = cad_plan::geometry_fingerprint(&prefix)?;
                let binding = feature.get("selection_binding").ok_or(
                    "显式 shell 缺少 selection_binding；旧 thickness-only shell 不会自动迁移。",
                )?;
                let target_feature_id = feature
                    .get("relations")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .find(|relation| {
                        relation.get("type").and_then(Value::as_str) == Some("shell_target")
                    })
                    .and_then(|relation| relation.get("feature_id"))
                    .and_then(Value::as_str)
                    .ok_or("shell 必须通过唯一 shell_target relation 显式指定目标 feature。")?;
                if previous_feature_id.as_deref() != Some(target_feature_id) {
                    return Err("shell_target 必须指向当前累计实体的 plan-head feature。".into());
                }
                let selectors = normalized["parameters"]["removed_faces"]
                    .as_array()
                    .ok_or("shell.removed_faces 须为opaque selector数组。")?;
                let thickness = normalized["parameters"]["thickness"]
                    .as_f64()
                    .ok_or("shell.thickness 须为长度值。")?;
                let removed = crate::native_shell::resolve_removed_faces(
                    stock,
                    binding,
                    selectors,
                    &expected_fingerprint,
                    &kernel.version(),
                    target_feature_id,
                    thickness,
                )?;
                let offset_mode = match normalized["parameters"]["offset_mode"].as_str() {
                    Some("inward") => crate::native_occt::ShellDirection::Inward,
                    Some("outward") => crate::native_occt::ShellDirection::Outward,
                    _ => return Err("shell.offset_mode 须明确为 inward 或 outward。".into()),
                };
                let candidate = kernel.make_thick_solid(stock, &removed, thickness, offset_mode)?;
                let analysis = candidate.analyze()?;
                if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1
                {
                    return Err("shell 未生成单一有效闭合实体；拒绝候选。".into());
                }
                let volume = candidate.volume()?;
                let delta = volume - before_volume;
                rows.push(json!({"feature_id":feature["id"],"kind":"shell","operation":"cut",
                    "state":"built","geometry_support":"exact","volume_before_mm3":before_volume,
                    "volume_after_mm3":volume,"volume_delta_mm3":delta,
                    "shell":{"offset_mode":match offset_mode {crate::native_occt::ShellDirection::Inward => "inward", crate::native_occt::ShellDirection::Outward => "outward"},"thickness_mm":thickness,
                        "removed_face_count":removed.len(),"target_feature_id":target_feature_id}}));
                previous_feature_id = feature["id"].as_str().map(str::to_owned);
                previous_feature_tools.clear();
                previous_feature_holes.clear();
                current = Some(candidate);
                return Ok(());
            }
            let (tools, mut requested_holes, standard_support) = if normalized["kind"] == "pattern" {
                let (tools, holes, support) = pattern_tools(
                    &kernel,
                    &normalized,
                    previous_feature_id.as_deref(),
                    &previous_feature_tools,
                    &previous_feature_holes,
                    cancelled,
                )?;
                (tools, holes, Some(support))
            } else if matches!(
                normalized["kind"].as_str(),
                Some("gear" | "helical_gear" | "socket_head_bolt" | "hex_bolt")
            ) {
                let (tools, support) = native_standard_features::tools(
                    &kernel,
                    normalized["kind"].as_str().unwrap_or(""),
                    &normalized["parameters"],
                )?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "bevel_gear" {
                let (tools, support) = native_bevel_gear::build(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "rotor" {
                let (tools, support) = crate::native_rotor::build(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "vbelt_pulley" {
                let (tools, support) =
                    crate::native_vbelt_pulley::build(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "scroll_disc" {
                let (tools, support) =
                    crate::native_scroll_disc::build(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "helix" {
                let (tools, support) = crate::native_helix::build(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "worm" {
                let (tools, support) = crate::native_worm::build(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "loft" {
                let (tools, support) = native_loft::build(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "impeller" {
                let (tools, support) = native_impeller::build(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "piston" {
                let (tools, support) =
                    native_parametric_features::build_piston(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "camshaft" {
                let (tools, support) =
                    native_parametric_features::build_camshaft(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "crankshaft" {
                let (tools, support) =
                    native_parametric_features::build_crankshaft(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "timing_pulley" {
                let (tools, support) = native_timing_pulley::build(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "spoked_wheel" {
                let (tools, support) =
                    native_parametric_features::build_spoked_wheel(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "conrod" {
                let (tools, support) =
                    native_parametric_features::build_conrod(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else if normalized["kind"] == "cross_joint" {
                let (tools, support) =
                    native_parametric_features::build_cross_joint(&kernel, &normalized["parameters"])?;
                (tools, vec![], Some(support))
            } else {
                let (tools, holes) = feature_tools(&kernel, &normalized, bb, current.as_ref(), &holes)?;
                (tools, holes, None)
            };
            let operation = feature["operation"].as_str().unwrap_or("add");
            let repeated_hole_array = normalized["kind"] == "hole"
                && normalized["parameters"]["pos"] == "pcd";
            // The legacy Quick bent-part recipe accepts an unlocated center
            // bore, even when the bounding-box center falls in the L-profile's
            // open notch. Keep that requested feature visible as a no-op while
            // still rejecting other ordinary holes that fail to cut material.
            let legacy_center_hole = normalized["kind"] == "hole"
                && normalized["parameters"]["pos"] == "center";
            let mut no_op_cut = normalized["kind"] == "pattern" && tools.is_empty();
            let hole_pre_cut = if !requested_holes.is_empty() && operation == "cut" {
                current
                    .as_ref()
                    .map(|shape| kernel.transform(shape, identity_matrix()))
                    .transpose()?
            } else {
                None
            };
            let tool_refs: Vec<_> = tools.iter().collect();
            let candidate = if no_op_cut {
                let stock = current.as_ref().ok_or("空阵列不能作为首个实体特征。")?;
                kernel.transform(stock, identity_matrix())?
            } else {
                match (&current, operation) {
                    (None, "add") => kernel.fuse(&tool_refs)?,
                    (Some(stock), "cut") => kernel.cut(stock, &tool_refs)?,
                    (Some(stock), "add") => {
                        let mut refs = vec![stock];
                        refs.extend(tool_refs);
                        kernel.fuse(&refs)?
                    }
                    (None, "cut") => return Err("首特征不能切除；未提交候选。".into()),
                    _ => return Err("operation 必须是 add 或 cut。".into()),
                }
            };
            check_cancel(cancelled)?;
            let analysis = candidate.analyze()?;
            if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
                return Err(format!(
                    "特征 {} 未生成单一有效封闭实体；未提交候选。",
                    feature["id"]
                ));
            }
            let volume = candidate.volume()?;
            let delta = volume - before_volume;
            if (normalized["kind"] == "pattern" || repeated_hole_array || legacy_center_hole)
                && operation == "cut"
                && delta >= -1e-7_f64.max(before_volume * 1e-10)
            {
                // Keep the valid stock and report a no-op instead of claiming
                // that a requested cut changed the geometry.
                no_op_cut = true;
                requested_holes.clear();
            }
            if operation == "cut" && !no_op_cut && delta >= -1e-7_f64.max(before_volume * 1e-10) {
                return Err(format!(
                    "特征 {} 切除没有实际减少材料；请检查孔位、方向或重复切除。",
                    feature["id"]
                ));
            }
            if !requested_holes.is_empty() {
                if operation == "cut" {
                    if let Some(pre_cut) = current.as_ref() {
                        for record in &mut requested_holes {
                            let evidence = verify_hole_record(pre_cut, &candidate, record, &holes)?;
                            let status = evidence["status"].clone();
                            record["status"] = status;
                            record["sampled_evidence"] = evidence;
                        }
                    } else {
                        for record in &mut requested_holes {
                            record["status"] = json!("unverified");
                            record["sampling_reason"] = json!("no_pre_cut_shape");
                        }
                    }
                } else {
                    for record in &mut requested_holes {
                        record["status"] = json!("unverified");
                        record["sampling_reason"] = json!("hole_feature_operation_not_cut");
                    }
                }
            }
            rows.push(
                json!({"feature_id":feature["id"],"kind":feature["kind"],"operation":operation,
                "state":if no_op_cut {"no_op"} else {"built"},"geometry_support":standard_support.as_ref().and_then(|v|v.get("status")).cloned().unwrap_or(json!("exact")),
                "support_details":standard_support,"volume_before_mm3":before_volume,
                "volume_after_mm3":volume,"volume_delta_mm3":delta,
                "warning":if no_op_cut {Some("切除没有移除材料；刀具体可能落在已有空腔中，或没有与实体相交。") } else {None}}),
            );
            let report_start = holes.len();
            if let Some(pre_cut) = hole_pre_cut {
                let mut instances = Vec::with_capacity(requested_holes.len());
                for (offset, record) in requested_holes.iter_mut().enumerate() {
                    let counterbore_pilot_radius = matching_counterbore_pilot_radius(&holes, record);
                    match hole_request_from_record(&pre_cut, record) {
                        Ok((request, expected_blind_depth)) => instances.push(PendingBore {
                            report_index: report_start + offset,
                            request,
                            expected_blind_depth,
                            counterbore_pilot_radius,
                        }),
                        Err(error) => {
                            record["final_measurement_status"] = json!("candidate_unverified");
                            record["final_measurement_reason"] = json!(error);
                        }
                    }
                }
                if !instances.is_empty() {
                    pending_bore_groups.push(PendingBoreGroup { pre_cut, instances });
                }
            }
            previous_feature_id = feature["id"].as_str().map(str::to_owned);
            previous_feature_tools = tools;
            previous_feature_holes = requested_holes.clone();
            holes.extend(requested_holes);
            current = Some(candidate);
            Ok(())
            })();
        if let Err(error) = feature_result {
            check_cancel(cancelled)?;
            let Some(prefix_shape) = current.take() else {
                return Err(error);
            };
            return partial_build_result(
                plan,
                &validation,
                &kernel,
                prefix_shape,
                &rows,
                &feature_id,
                &error,
            );
        }
    }
    let shape = current.ok_or("没有启用的实体特征。")?;
    let mut hole_instances = holes.clone();
    for group in pending_bore_groups {
        for pending in group.instances {
            check_cancel(cancelled)?;
            let later_counterbore = matching_later_counterbore(&hole_instances, pending.report_index);
            let measurement = if let Some(pilot_radius) = pending.counterbore_pilot_radius {
                native_bore_metrology::measure_counterbore(
                    &group.pre_cut,
                    &shape,
                    &pending.request,
                    pilot_radius,
                    pending.expected_blind_depth.expect("counterbore is blind"),
                )
            } else if let Some((counterbore_radius, counterbore_depth)) = later_counterbore {
                native_bore_metrology::measure_through_pilot_with_counterbore(
                    &group.pre_cut,
                    &shape,
                    &pending.request,
                    counterbore_radius,
                    counterbore_depth,
                )
            } else {
                native_bore_metrology::measure_bore(
                    &group.pre_cut,
                    &shape,
                    &pending.request,
                    pending.expected_blind_depth,
                )
            };
            let (status, payload) = match measurement {
                Ok(value) => {
                    let angle_coverage = value["angular_coverage_radians"].as_f64().unwrap_or(0.0);
                    let full_cylinder = (angle_coverage - 2.0 * std::f64::consts::PI).abs() <= 1e-5;
                    let accepted = value["accepted"] == true && full_cylinder;
                    let kind = if pending.expected_blind_depth.is_some() {
                        "verified_blind"
                    } else {
                        "verified_open"
                    };
                    let status = if accepted {
                        kind
                    } else {
                        "candidate_unverified"
                    };
                    let mut report = value;
                    report["full_angular_coverage"] = json!(full_cylinder);
                    report["status"] = json!(status);
                    if !full_cylinder {
                        report["reason_code"] =
                            json!("partial_or_incomplete_cylindrical_face_coverage");
                    } else if report["accepted"] != true {
                        report["reason_code"] =
                            json!("final_revision_geometry_or_material_evidence_mismatch");
                    }
                    (status, report)
                }
                Err(error) => (
                    "candidate_unverified",
                    json!({"status":"candidate_unverified","reason_code":"final_brep_measurement_failed","reason":error,
                        "measurement_method":"OCCT BRepAdaptor_Surface GeomAbs_Cylinder; not measured"}),
                ),
            };
            if let Some(record) = hole_instances.get_mut(pending.report_index) {
                record["status"] = json!(status);
                record["final_measurement"] = payload;
            }
        }
    }
    let hole_measurement_complete = hole_measurements_complete(&hole_instances);
    let hole_measurements_verified = !hole_instances.is_empty() && hole_measurement_complete;
    let bb = shape.bounds()?;
    let fingerprint = cad_plan::geometry_fingerprint(plan)?;
    let report = json!({"state":"built","execution_engine":"rust_c_abi_occt","occt_version":kernel.version(),
        "geometry_fingerprint":fingerprint,"validation":validation,"feature_results":rows,
        "bounds_mm":[bb.xmin,bb.ymin,bb.zmin,bb.xmax,bb.ymax,bb.zmax],
        "dimensions_mm":[bb.xmax-bb.xmin,bb.ymax-bb.ymin,bb.zmax-bb.zmin],
        "volume_mm3":shape.volume()?,"valid":true,"closed_solid_count":1,
        "requested_holes":holes.clone(),"sampled_hole_evidence":holes,
        "hole_instances":hole_instances,
        "hole_measurement_complete":hole_measurement_complete,
        "hole_measurements_verified":hole_measurements_verified,
        "catia_executed":false,"image_acceptance":"not_performed"});
    Ok(BuiltPlan { shape, report })
}

fn partial_build_result(
    plan: &Value,
    validation: &Value,
    kernel: &Kernel,
    shape: Shape,
    rows: &[Value],
    failed_feature_id: &str,
    failure: &str,
) -> Result<BuiltPlan, String> {
    let applied = longest_built_prefix(plan, rows, failed_feature_id)
        .ok_or_else(|| failure.to_owned())?;

    let analysis = shape.analyze()?;
    if !is_valid_single_closed_solid(
        analysis.valid,
        analysis.solid_count,
        analysis.closed_solid_count,
    ) {
        return Err(failure.to_owned());
    }
    let bounds = shape.bounds()?;
    let volume = shape.volume()?;
    let fingerprint = cad_plan::geometry_fingerprint(plan)?;
    let report = json!({
        "state":"partial",
        "execution_engine":"rust_c_abi_occt",
        "occt_version":kernel.version(),
        "geometry_fingerprint":fingerprint,
        "validation":validation,
        "feature_results":rows,
        "applied_feature_ids":applied,
        "failed_feature_id":failed_feature_id,
        "failure_code":"feature_build_failed",
        "failure_summary":failure.chars().take(500).collect::<String>(),
        "bounds_mm":[bounds.xmin,bounds.ymin,bounds.zmin,bounds.xmax,bounds.ymax,bounds.zmax],
        "dimensions_mm":[bounds.xmax-bounds.xmin,bounds.ymax-bounds.ymin,bounds.zmax-bounds.zmin],
        "volume_mm3":volume,
        "valid":true,
        "solid_count":1,
        "closed_solid_count":1,
        "catia_executed":false,
        "image_acceptance":"not_performed"
    });
    Ok(BuiltPlan {
        shape,
        report,
    })
}

fn longest_built_prefix(
    plan: &Value,
    rows: &[Value],
    failed_feature_id: &str,
) -> Option<Vec<String>> {
    let enabled = plan["features"]
        .as_array()?
        .iter()
        .filter(|feature| feature["enabled"] != false)
        .map(|feature| feature["id"].as_str())
        .collect::<Option<Vec<_>>>()?;
    let applied = rows
        .iter()
        .map(|row| row["feature_id"].as_str())
        .collect::<Option<Vec<_>>>()?;
    let prefix_len = applied.len();
    if prefix_len == 0
        || prefix_len >= enabled.len()
        || rows.iter().any(|row| row["state"] != "built")
        || applied != enabled.iter().take(prefix_len).copied().collect::<Vec<_>>()
        || enabled.get(prefix_len).copied() != Some(failed_feature_id)
    {
        return None;
    }
    Some(applied.into_iter().map(str::to_owned).collect())
}

fn is_valid_single_closed_solid(valid: i32, solid_count: usize, closed_solid_count: usize) -> bool {
    valid == 1 && solid_count == 1 && closed_solid_count == 1
}

fn hole_measurements_complete(instances: &[Value]) -> bool {
    // The build loop only adds instances from enabled features. A verified
    // sampling status alone is not a final B-Rep measurement: require the
    // accepted final measurement and its complete cylindrical coverage.
    instances.iter().all(|record| {
        matches!(
            record["status"].as_str(),
            Some("verified_open" | "verified_blind")
        ) && record["final_measurement"]["accepted"] == true
            && record["final_measurement"]["full_angular_coverage"] == true
    })
}

fn plan_prefix(plan: &Value, feature_index: usize) -> Result<Value, String> {
    let mut prefix = plan.clone();
    let features = prefix
        .get_mut("features")
        .and_then(Value::as_array_mut)
        .ok_or("CADPlan features 必须是数组。")?;
    if feature_index > features.len() {
        return Err("shell 前置特征索引无效。".into());
    }
    features.truncate(feature_index);
    Ok(prefix)
}

fn vector_dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn vector_cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

fn vector_length(v: [f64; 3]) -> f64 {
    vector_dot(v, v).sqrt()
}

fn same_directed_axis(
    a_center: [f64; 3],
    a_axis: [f64; 3],
    b_center: [f64; 3],
    b_axis: [f64; 3],
) -> bool {
    let Ok(a_axis) = normalize_vector(a_axis) else {
        return false;
    };
    let Ok(b_axis) = normalize_vector(b_axis) else {
        return false;
    };
    let delta = [
        a_center[0] - b_center[0],
        a_center[1] - b_center[1],
        a_center[2] - b_center[2],
    ];
    vector_dot(a_axis, b_axis) >= 1.0 - 1e-8
        && vector_length(vector_cross(delta, a_axis)) <= 1e-6
}

fn matching_through_pilot_radius(
    prior_holes: &[Value],
    centerline: [f64; 3],
    axis: [f64; 3],
    counterbore_diameter: f64,
) -> Option<f64> {
    prior_holes.iter().rev().find_map(|record| {
        if record["through"].as_bool() != Some(true) {
            return None;
        }
        let diameter = record["diameter_mm"].as_f64()?;
        if !diameter.is_finite() || diameter <= 0.0 || diameter >= counterbore_diameter {
            return None;
        }
        let center = point(record.get("centerline_mm"), [0.0; 3]).ok()?;
        let record_axis = record_axis_direction(record).ok()?;
        same_directed_axis(centerline, axis, center, record_axis).then_some(diameter / 2.0)
    })
}

fn matching_counterbore_pilot_radius(prior_holes: &[Value], record: &Value) -> Option<f64> {
    if record["through"].as_bool() != Some(false) {
        return None;
    }
    let diameter = record["diameter_mm"].as_f64()?;
    let centerline = point(record.get("centerline_mm"), [0.0; 3]).ok()?;
    let axis = record_axis_direction(record).ok()?;
    matching_through_pilot_radius(prior_holes, centerline, axis, diameter)
}

fn matching_later_counterbore(holes: &[Value], pilot_index: usize) -> Option<(f64, f64)> {
    let pilot = holes.get(pilot_index)?;
    if pilot["through"].as_bool() != Some(true) {
        return None;
    }
    let pilot_diameter = pilot["diameter_mm"].as_f64()?;
    let pilot_centerline = point(pilot.get("centerline_mm"), [0.0; 3]).ok()?;
    let pilot_axis = record_axis_direction(pilot).ok()?;
    holes.iter().skip(pilot_index + 1).find_map(|record| {
        if record["through"].as_bool() != Some(false) {
            return None;
        }
        let diameter = record["diameter_mm"].as_f64()?;
        let depth = record["depth_mm"].as_f64()?;
        if !diameter.is_finite()
            || diameter <= pilot_diameter
            || !depth.is_finite()
            || depth <= 0.0
        {
            return None;
        }
        let centerline = point(record.get("centerline_mm"), [0.0; 3]).ok()?;
        let axis = record_axis_direction(record).ok()?;
        same_directed_axis(pilot_centerline, pilot_axis, centerline, axis)
            .then_some((diameter / 2.0, depth))
    })
}

fn annular_material_span(
    shape: &Shape,
    centerline: [f64; 3],
    axis: [f64; 3],
    inner_radius: f64,
    outer_radius: f64,
    tmin: f64,
    tmax: f64,
    tolerance: f64,
) -> Result<[f64; 2], String> {
    if !inner_radius.is_finite()
        || !outer_radius.is_finite()
        || inner_radius <= 0.0
        || outer_radius <= inner_radius
        || !tmin.is_finite()
        || !tmax.is_finite()
        || tmin >= tmax
    {
        return Err("沉孔环带入口核验参数无效。".into());
    }
    let axis = normalize_vector(axis)?;
    let reference = if axis[0].abs() < 0.8 {
        [1.0, 0.0, 0.0]
    } else {
        [0.0, 1.0, 0.0]
    };
    let u = normalize_vector(vector_cross(axis, reference))?;
    let v = vector_cross(axis, u);
    let sample_radius = (inner_radius + outer_radius) / 2.0;
    let mut expected: Option<[f64; 2]> = None;
    let span_tolerance = ((tmax - tmin) * 1e-8).max(tolerance * 20.0);
    for angle_index in 0..16 {
        let angle = std::f64::consts::TAU * angle_index as f64 / 16.0;
        let radial = [
            u[0] * angle.cos() + v[0] * angle.sin(),
            u[1] * angle.cos() + v[1] * angle.sin(),
            u[2] * angle.cos() + v[2] * angle.sin(),
        ];
        let sample_origin = [
            centerline[0] + radial[0] * sample_radius,
            centerline[1] + radial[1] * sample_radius,
            centerline[2] + radial[2] * sample_radius,
        ];
        let spans = shape.line_material_intervals(sample_origin, axis, tmin, tmax, tolerance)?;
        if spans.len() != 1 {
            return Err(format!(
                "沉孔环带第 {angle_index} 条样本线具有 {} 段材料，无法确认唯一入口。",
                spans.len()
            ));
        }
        let span = spans[0];
        if let Some(reference) = expected {
            if (span[0] - reference[0]).abs() > span_tolerance
                || (span[1] - reference[1]).abs() > span_tolerance
            {
                return Err("沉孔环带样本线的材料入口/厚度不一致，拒绝按包围盒猜测。".into());
            }
        } else {
            expected = Some(span);
        }
    }
    expected.ok_or("沉孔环带未能取得局部材料区间。".into())
}

fn rounded_rectangle_profile(
    length: f64,
    width: f64,
    corner: f64,
    mode: &str,
) -> Vec<[f64; 2]> {
    let half_length = length / 2.0;
    let half_width = width / 2.0;
    let radius = corner
        .max(0.0)
        .min((half_length.min(half_width) - 1e-6).max(0.0));
    if radius <= 1e-9 {
        return vec![
            [-half_length, -half_width],
            [half_length, -half_width],
            [half_length, half_width],
            [-half_length, half_width],
        ];
    }

    let corners = [
        (half_length - radius, -half_width + radius, -90.0),
        (half_length - radius, half_width - radius, 0.0),
        (-half_length + radius, half_width - radius, 90.0),
        (-half_length + radius, -half_width + radius, 180.0),
    ];
    let mut points = Vec::with_capacity(if mode == "chamfer" { 8 } else { 36 });
    for (cx, cy, start_angle) in corners {
        let steps = if mode == "chamfer" { 1 } else { 8 };
        for step in 0..=steps {
            let angle = (start_angle + 90.0 * step as f64 / steps as f64).to_radians();
            points.push([cx + radius * angle.cos(), cy + radius * angle.sin()]);
        }
    }
    points
}

fn feature_tools(
    kernel: &Kernel,
    feature: &Value,
    bb: Option<Bounds>,
    pre_cut: Option<&Shape>,
    prior_holes: &[Value],
) -> Result<(Vec<Shape>, Vec<Value>), String> {
    let p = &feature["parameters"];
    let axis = axis_index(p)?;
    let kind = feature["kind"].as_str().ok_or("缺少 kind")?;
    let mut center = point(p.get("center"), [0.; 3])?;
    match kind {
        "box" => {
            let dimensions = [num(p, "length")?, num(p, "width")?, num(p, "height")?];
            let plane = profile_indices(axis);
            let mut size = [0.; 3];
            size[plane[0]] = dimensions[0];
            size[plane[1]] = dimensions[1];
            size[axis] = dimensions[2];
            // CADPlan overrides Document's one-sided default when a box
            // explicitly supplies center but omits base.
            if p.get("base")
                .and_then(Value::as_bool)
                .unwrap_or(p.get("center").is_none())
            {
                center[axis] += dimensions[2] / 2.;
            }
            let corner = optional_num(p, "corner", 0.)?;
            if corner <= 1e-9 {
                Ok((vec![kernel.make_box(size, center)?], vec![]))
            } else {
                let profile = rounded_rectangle_profile(
                    dimensions[0],
                    dimensions[1],
                    corner,
                    p.get("corner_mode")
                        .and_then(Value::as_str)
                        .unwrap_or("fillet"),
                );
                let mut origin = center;
                origin[axis] -= dimensions[2] / 2.;
                Ok((
                    vec![kernel.prism(
                        &profile,
                        dimensions[2],
                        origin,
                        Axis::from_index(axis)?,
                    )?],
                    vec![],
                ))
            }
        }
        "cylinder" => {
            let height = num(p, "height")?;
            if !p.get("base").and_then(Value::as_bool).unwrap_or(true) {
                center[axis] -= height / 2.;
            }
            let radius = num(p, "radius")?;
            let radius2 = optional_num(p, "radius2", radius)?;
            let shape = if (radius - radius2).abs() <= 1e-10 {
                kernel.cylinder(radius, height, center, axis_vector(axis))?
            } else {
                kernel.cone(radius, radius2, height, center, Axis::from_index(axis)?)?
            };
            Ok((vec![shape], vec![]))
        }
        "prism" | "revolve" => {
            let profile = p["profile"]
                .as_array()
                .ok_or("profile 必须是二维点数组。")?
                .iter()
                .map(|row| {
                    let values = row
                        .as_array()
                        .filter(|a| a.len() == 2)
                        .ok_or("轮廓点必须包含两个数值。")?;
                    Ok([
                        values[0].as_f64().ok_or("轮廓坐标必须是数值。")?,
                        values[1].as_f64().ok_or("轮廓坐标必须是数值。")?,
                    ])
                })
                .collect::<Result<Vec<[f64; 2]>, String>>()?;
            let axis = Axis::from_index(axis)?;
            let shape = if kind == "prism" {
                kernel.prism(&profile, num(p, "height")?, center, axis)?
            } else {
                kernel.revolve(&profile, optional_num(p, "angle", 360.)?, center, axis)?
            };
            Ok((vec![shape], vec![]))
        }
        "sphere" => Ok((vec![kernel.sphere(num(p, "radius")?, center)?], vec![])),
        "hole" => {
            let bb = bb.ok_or("孔前没有实体。")?;
            let bounds = bounds_array(bb);
            let plane = plane_indices(axis);
            let low = bounds[axis];
            let high = bounds[axis + 3];
            let through = p.get("through").and_then(Value::as_bool).unwrap_or(true);
            let diameter = num(p, "diameter")?;
            let depth = if through {
                None
            } else {
                Some(num(p, "depth")?)
            };
            if depth.is_some_and(|d| d <= 0.) {
                return Err("盲孔深度必须为正。".into());
            }
            let positions = hole_positions(p, bounds, axis, diameter)?;
            let mut tools = vec![];
            let mut records = vec![];
            for (i, [a, b]) in positions.iter().enumerate() {
                let mut base = [0.; 3];
                base[plane[0]] = *a;
                base[plane[1]] = *b;
                let (start, height, local_span) = if let Some(d) = depth {
                    let shape = pre_cut.ok_or("盲孔前缺少实体。")?;
                    let mut origin = base;
                    origin[axis] = 0.;
                    let margin = ((high - low) * 0.05).max(1.0);
                    let direction = axis_vector(axis);
                    let counterbore_pilot = matching_through_pilot_radius(
                        prior_holes,
                        origin,
                        direction,
                        diameter,
                    );
                    let span = if let Some(pilot_radius) = counterbore_pilot {
                        annular_material_span(
                            shape,
                            origin,
                            direction,
                            pilot_radius,
                            diameter / 2.0,
                            low - margin,
                            high + margin,
                            1e-7,
                        )?
                    } else {
                        let spans = shape.line_material_intervals(
                            origin,
                            direction,
                            low - margin,
                            high + margin,
                            1e-7,
                        )?;
                        spans.last().copied().ok_or(
                            "盲孔中心线没有前置材料区间；拒绝按全局包围盒猜测入口。",
                        )?
                    };
                    let thickness = span[1] - span[0];
                    let depth_tolerance = 1e-6_f64.max(thickness * 1e-8);
                    if thickness <= d + depth_tolerance {
                        return Err(format!(
                            "盲孔深度 {d:.6} mm 不小于孔位局部正向材料厚度 {thickness:.6} mm。"
                        ));
                    }
                    let epsilon = (d * 1e-6).clamp(1e-5, 1e-3);
                    base[axis] = (span[0] + span[1]) / 2.;
                    (span[1] - d, d + epsilon, Some(span))
                } else {
                    (low - 10., high - low + 20., None)
                };
                let mut cutter_base = base;
                cutter_base[axis] = start;
                tools.push(kernel.cylinder(
                    diameter / 2.,
                    height,
                    cutter_base,
                    axis_vector(axis),
                )?);
                let mut centerline = base;
                if let Some(span) = local_span {
                    centerline[axis] = (span[0] + span[1]) / 2.;
                } else {
                    centerline[axis] = (low + high) / 2.;
                }
                records.push(json!({"feature_id":feature["id"],"instance_index":i+1,
                    "centerline_mm":centerline,"axis":(["X","Y","Z"][axis]),
                    "axis_vector_mm":axis_vector(axis),"diameter_mm":diameter,
                    "through":through,"depth_mm":depth,"status":"requested_not_measured",
                    "local_positive_material_span_mm":local_span}));
            }
            Ok((tools, records))
        }
        "pocket" => {
            let bounds = bounds_array(bb.ok_or("凹槽前没有实体。")?);
            let plane = plane_indices(axis);
            let low = bounds[axis];
            let high = bounds[axis + 3];
            let depth = num(p, "depth")?;
            if depth >= high - low - (1e-6_f64.max((high - low) * 1e-8)) {
                return Err("盲槽深度必须小于实体轴向厚度。".into());
            }
            let epsilon = (depth * 1e-6).clamp(1e-5, 1e-3);
            let mut center = [0.; 3];
            center[plane[0]] =
                optional_num(p, "x", (bounds[plane[0]] + bounds[plane[0] + 3]) / 2.)?;
            center[plane[1]] =
                optional_num(p, "y", (bounds[plane[1]] + bounds[plane[1] + 3]) / 2.)?;
            center[axis] = match p.get("side").and_then(Value::as_str).unwrap_or("positive") {
                "positive" => high - depth / 2. + epsilon / 2.,
                "negative" => low + depth / 2. - epsilon / 2.,
                _ => return Err("pocket.side 必须是 positive 或 negative。".into()),
            };
            let profile = profile_indices(axis);
            let mut size = [0.; 3];
            size[profile[0]] = num(p, "length")?;
            size[profile[1]] = num(p, "width")?;
            size[axis] = depth + epsilon;
            Ok((vec![kernel.make_box(size, center)?], vec![]))
        }
        _ => Err("未实现的 Rust 特征。".into()),
    }
}

fn pattern_tools(
    kernel: &Kernel,
    feature: &Value,
    previous_feature_id: Option<&str>,
    source_tools: &[Shape],
    source_holes: &[Value],
    cancelled: &AtomicBool,
) -> Result<(Vec<Shape>, Vec<Value>, Value), String> {
    let parameters = &feature["parameters"];
    let relation_rows = feature["relations"]
        .as_array()
        .ok_or("pattern 必须声明唯一 pattern_source 关系。")?;
    let source_ids = relation_rows
        .iter()
        .filter(|row| row["type"] == "pattern_source")
        .filter_map(|row| row["feature_id"].as_str())
        .collect::<Vec<_>>();
    if source_ids.len() != 1 {
        return Err("pattern 必须声明唯一 pattern_source 关系。".into());
    }
    let source_id = source_ids[0];
    if previous_feature_id != Some(source_id) {
        return Err(format!(
            "pattern_source {source_id} 不是当前已构建的紧邻前一特征。"
        ));
    }
    let count = parameters["count"]
        .as_u64()
        .filter(|count| *count > 0)
        .ok_or("pattern.count 必须是大于零的整数。")?;
    if count > 1 && source_tools.is_empty() {
        return Err("pattern_source 没有可复制的原生几何。".into());
    }
    let instance_count =
        usize::try_from(count - 1).map_err(|_| "pattern 实例数量超出当前平台可表示范围。")?;
    let tool_capacity = instance_count
        .checked_mul(source_tools.len())
        .ok_or("pattern 几何数量溢出。")?;
    if tool_capacity > MAX_PATTERN_TRANSFORMED_TOOLS {
        return Err(format!(
            "pattern 需要变换 {tool_capacity} 个刀具，超过当前 OCCT 单特征上限 {MAX_PATTERN_TRANSFORMED_TOOLS}。"
        ));
    }
    let hole_capacity = instance_count
        .checked_mul(source_holes.len())
        .ok_or("pattern 孔记录数量溢出。")?;
    if hole_capacity > MAX_PATTERN_TRANSFORMED_TOOLS {
        return Err(format!(
            "pattern 需要生成 {hole_capacity} 条孔记录，超过当前单特征上限 {MAX_PATTERN_TRANSFORMED_TOOLS}。"
        ));
    }
    let mut tools = Vec::new();
    tools
        .try_reserve_exact(tool_capacity)
        .map_err(|error| format!("无法为 pattern 几何分配空间：{error}"))?;
    let mut holes = Vec::new();
    holes
        .try_reserve_exact(hole_capacity)
        .map_err(|error| format!("无法为 pattern 孔记录分配空间：{error}"))?;

    let mode = parameters["mode"].as_str().ok_or("pattern.mode 无效。")?;
    let mut linear_step = [0.; 3];
    let mut rotation_axis = 2;
    let mut rotation_center = [0.; 3];
    let mut angle_per_instance = 0.;
    match mode {
        "linear" => linear_step = point(parameters.get("step"), [10., 0., 0.])?,
        "circular" => {
            rotation_axis = axis_index(parameters)?;
            rotation_center = point(parameters.get("center"), [0.; 3])?;
            let angle = optional_num(parameters, "angle", 360.)?;
            let full = parameters
                .get("full")
                .and_then(Value::as_bool)
                .unwrap_or((angle - 360.).abs() <= 1e-8);
            let denominator = if full {
                count.max(1)
            } else {
                count.saturating_sub(1).max(1)
            } as f64;
            angle_per_instance = angle / denominator;
        }
        _ => return Err("pattern.mode 必须是 linear 或 circular。".into()),
    }

    for instance in 1..count {
        check_cancel(cancelled)?;
        let multiplier = instance as f64;
        let matrix = if mode == "linear" {
            let shift = linear_step.map(|value| value * multiplier);
            if shift.iter().any(|value| !value.is_finite()) {
                return Err("pattern 线性位移超出有限范围。".into());
            }
            translation_matrix(shift)
        } else {
            rotation_matrix(
                rotation_axis,
                angle_per_instance * multiplier,
                rotation_center,
            )?
        };
        for shape in source_tools {
            check_cancel(cancelled)?;
            tools.push(kernel.transform(shape, matrix)?);
        }
        for source in source_holes {
            check_cancel(cancelled)?;
            let mut record = source.clone();
            let centerline = point(source.get("centerline_mm"), [0.; 3])?;
            let transformed_centerline = transform_point(matrix, centerline);
            if transformed_centerline
                .iter()
                .any(|value| !value.is_finite())
            {
                return Err("pattern 变换后的孔中心线超出有限范围。".into());
            }
            record["centerline_mm"] = json!(transformed_centerline);
            let axis_direction = record_axis_direction(source)?;
            let transformed_axis_direction =
                normalize_vector(transform_vector(matrix, axis_direction))?;
            record["axis_vector_mm"] = json!(transformed_axis_direction);
            let base_index = source["instance_index"].as_u64().unwrap_or(1);
            let multiplier = instance;
            let source_hole_count =
                u64::try_from(source_holes.len()).map_err(|_| "pattern 孔记录数溢出。")?;
            let hole_offset = multiplier
                .checked_mul(source_hole_count)
                .ok_or("pattern 孔实例索引溢出。")?;
            record["instance_index"] = json!(
                base_index
                    .checked_add(hole_offset)
                    .ok_or("pattern 孔实例索引溢出。")?
            );
            holes.push(record);
        }
    }
    Ok((
        tools,
        holes,
        json!({"kind":"pattern","status":"exact","builder":"explicit_pattern_transform",
            "mode":mode,"source_feature_id":source_id,"count":count,
            "created_instances":count.saturating_sub(1)}),
    ))
}

fn identity_matrix() -> [f64; 16] {
    [
        1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.,
    ]
}

fn translation_matrix(shift: [f64; 3]) -> [f64; 16] {
    let mut matrix = identity_matrix();
    matrix[3] = shift[0];
    matrix[7] = shift[1];
    matrix[11] = shift[2];
    matrix
}

fn rotation_matrix(axis: usize, angle_degrees: f64, center: [f64; 3]) -> Result<[f64; 16], String> {
    if axis > 2 || !angle_degrees.is_finite() || center.iter().any(|value| !value.is_finite()) {
        return Err("pattern 圆周变换参数无效。".into());
    }
    let angle = angle_degrees.to_radians();
    let (sin, cos) = angle.sin_cos();
    let rotation = match axis {
        0 => [[1., 0., 0.], [0., cos, -sin], [0., sin, cos]],
        1 => [[cos, 0., sin], [0., 1., 0.], [-sin, 0., cos]],
        2 => [[cos, -sin, 0.], [sin, cos, 0.], [0., 0., 1.]],
        _ => unreachable!("axis checked above"),
    };
    let mut translation = center;
    for row in 0..3 {
        translation[row] -= (0..3)
            .map(|column| rotation[row][column] * center[column])
            .sum::<f64>();
    }
    if translation.iter().any(|value| !value.is_finite()) {
        return Err("pattern 旋转中心超出有限范围。".into());
    }
    Ok([
        rotation[0][0],
        rotation[0][1],
        rotation[0][2],
        translation[0],
        rotation[1][0],
        rotation[1][1],
        rotation[1][2],
        translation[1],
        rotation[2][0],
        rotation[2][1],
        rotation[2][2],
        translation[2],
        0.,
        0.,
        0.,
        1.,
    ])
}

fn transform_point(matrix: [f64; 16], point: [f64; 3]) -> [f64; 3] {
    [
        matrix[0] * point[0] + matrix[1] * point[1] + matrix[2] * point[2] + matrix[3],
        matrix[4] * point[0] + matrix[5] * point[1] + matrix[6] * point[2] + matrix[7],
        matrix[8] * point[0] + matrix[9] * point[1] + matrix[10] * point[2] + matrix[11],
    ]
}

fn transform_vector(matrix: [f64; 16], vector: [f64; 3]) -> [f64; 3] {
    [
        matrix[0] * vector[0] + matrix[1] * vector[1] + matrix[2] * vector[2],
        matrix[4] * vector[0] + matrix[5] * vector[1] + matrix[6] * vector[2],
        matrix[8] * vector[0] + matrix[9] * vector[1] + matrix[10] * vector[2],
    ]
}

fn record_axis_direction(record: &Value) -> Result<[f64; 3], String> {
    let fallback = match record["axis"].as_str() {
        Some("X") => axis_vector(0),
        Some("Y") => axis_vector(1),
        Some("Z") => axis_vector(2),
        _ => return Err("孔记录缺少有效轴向。".into()),
    };
    normalize_vector(point(record.get("axis_vector_mm"), fallback)?)
}

fn normalize_vector(vector: [f64; 3]) -> Result<[f64; 3], String> {
    let length = vector.iter().map(|value| value * value).sum::<f64>().sqrt();
    if !length.is_finite() || length <= 1e-12 {
        return Err("孔轴向变换后不是有限的非零向量。".into());
    }
    Ok(vector.map(|value| value / length))
}

fn projected_line_limits(origin: [f64; 3], axis: [f64; 3], bounds: [f64; 6]) -> (f64, f64) {
    let mut low = f64::INFINITY;
    let mut high = f64::NEG_INFINITY;
    for mask in 0..8 {
        let corner = [
            if mask & 1 == 0 { bounds[0] } else { bounds[3] },
            if mask & 2 == 0 { bounds[1] } else { bounds[4] },
            if mask & 4 == 0 { bounds[2] } else { bounds[5] },
        ];
        let parameter = (0..3)
            .map(|index| (corner[index] - origin[index]) * axis[index])
            .sum::<f64>();
        low = low.min(parameter);
        high = high.max(parameter);
    }
    (low, high)
}

fn hole_request_from_record(
    pre_cut: &Shape,
    record: &Value,
) -> Result<(HoleRequest, Option<f64>), String> {
    let origin = point(record.get("centerline_mm"), [0.; 3])?;
    let axis = record_axis_direction(record)?;
    let radius = num(record, "diameter_mm")? / 2.0;
    if radius <= 0.0 {
        return Err("孔记录直径必须为正。".into());
    }
    let bounds = bounds_array(pre_cut.bounds()?);
    let (low, high) = projected_line_limits(origin, axis, bounds);
    let margin = ((high - low) * 0.05).max(1.0);
    let request = HoleRequest {
        origin,
        axis,
        radius,
        tmin: low - margin,
        tmax: high + margin,
        tolerance: 1e-6,
    };
    let expected_blind_depth = if record["through"].as_bool() == Some(false) {
        Some(num(record, "depth_mm")?)
    } else {
        None
    };
    Ok((request, expected_blind_depth))
}

fn verify_hole_record(
    pre_cut: &Shape,
    post_cut: &Shape,
    record: &Value,
    prior_holes: &[Value],
) -> Result<Value, String> {
    let centerline = point(record.get("centerline_mm"), [0.; 3])?;
    let axis = record_axis_direction(record)?;
    let radius = num(record, "diameter_mm")? / 2.0;
    let bounds = bounds_array(pre_cut.bounds()?);
    let (low, high) = projected_line_limits(centerline, axis, bounds);
    let margin = ((high - low) * 0.05).max(1.0);
    let tmin = low - margin;
    let tmax = high + margin;
    let request = HoleRequest {
        origin: centerline,
        axis,
        radius,
        tmin,
        tmax,
        tolerance: 1e-7,
    };
    if let Some(pilot_radius) = matching_counterbore_pilot_radius(prior_holes, record) {
        let expected_depth = record["depth_mm"]
            .as_f64()
            .ok_or("与通孔 pilot 配对的沉孔缺少显式深度。")?;
        let evidence = native_geometry_evidence::verify_counterbore(
            pre_cut,
            post_cut,
            &request,
            pilot_radius,
            expected_depth,
        )?;
        let passed = evidence["accepted"] == true;
        return Ok(json!({
            "status":if passed {"verified_by_sampled_rays"} else {"partial"},
            "reason_codes":if passed {json!([])} else {json!(["counterbore_annulus_depth_or_outer_material_check_failed"])},
            "expected_through":false,
            "expected_depth_mm":expected_depth,
            "counterbore_pilot_radius_mm":pilot_radius,
            "actual_material":Value::Null,
            "through_on_sampled_rays":false,
            "ray_evidence":evidence,
            "measurement_scope":"annular counterbore ray evidence plus later analytic B-Rep cylinder measurement"
        }));
    }
    let sampled = match native_geometry_evidence::verify_hole(pre_cut, post_cut, &request) {
        Ok(evidence) => evidence,
        Err(error) => {
            return Ok(json!({
                "status":"unverified",
                "reason_codes":["line_sampling_failed"],
                "reason":error,
                "expected_through":record["through"],
                "expected_depth_mm":record["depth_mm"],
                "actual_material":Value::Null,
                "through_on_sampled_rays":Value::Null,
                "ray_evidence":Value::Null,
            }));
        }
    };
    let expected_through = record["through"].as_bool().unwrap_or(true);
    let expected_depth = record["depth_mm"].as_f64();
    let center = &sampled["center_ray"];
    let before_length = center["before_material_length"].as_f64().unwrap_or(0.0);
    let after_length = center["after_material_length"].as_f64().unwrap_or(0.0);
    let removed_length = (before_length - after_length).max(0.0);
    let depth_tolerance = expected_depth.map(|d| 1e-4_f64.max(d * 1e-5));
    let depth_matches =
        expected_depth.is_some_and(|d| (removed_length - d).abs() <= depth_tolerance.unwrap());
    let sampled_through = sampled["summary"]["through_on_sampled_rays"] == true;
    let center_pre_material = sampled["summary"]["center_pre_material_present"] == true;
    let center_remaining = sampled["summary"]["center_remaining_material"] == true;
    let base_samples_pass = sampled["accepted"] == true;
    let mode_pass = if expected_through {
        sampled_through && after_length <= 4e-7
    } else {
        center_remaining && depth_matches && !sampled_through
    };
    let passed = center_pre_material && base_samples_pass && mode_pass;
    let status = if passed {
        "verified_by_sampled_rays"
    } else if center_pre_material
        || sampled["summary"]["interior_rays_with_pre_material"]
            .as_u64()
            .unwrap_or(0)
            > 0
    {
        "partial"
    } else {
        "unverified"
    };
    let mut reasons = Vec::new();
    if !center_pre_material {
        reasons.push("centerline_has_no_pre_cut_material");
    }
    if sampled["summary"]["interior_rays_with_material_removed"]
        .as_u64()
        .unwrap_or(0)
        < sampled["summary"]["interior_rays_total"]
            .as_u64()
            .unwrap_or(0)
    {
        reasons.push("some_interior_rays_lack_pre_material_or_removal");
    }
    if sampled["summary"]["outside_rays_retaining_material"]
        .as_u64()
        .unwrap_or(0)
        < sampled["summary"]["outside_rays_total"]
            .as_u64()
            .unwrap_or(0)
    {
        reasons.push("some_outside_rays_lack_pre_material_or_change_material");
    }
    if expected_through && !sampled_through {
        reasons.push("requested_through_hole_has_remaining_material_on_sampled_rays");
    }
    if !expected_through && !depth_matches {
        reasons.push("centerline_removed_length_does_not_match_requested_depth");
    }
    if !expected_through && !center_remaining {
        reasons.push("blind_hole_centerline_has_no_remaining_material");
    }
    let center_before_intervals = center["before_material_intervals"].clone();
    let center_after_intervals = center["after_material_intervals"].clone();
    Ok(json!({
        "status":status,
        "reason_codes":reasons,
        "expected_through":expected_through,
        "expected_depth_mm":expected_depth,
        "center_removed_material_mm":removed_length,
        "depth_tolerance_mm":depth_tolerance,
        "depth_matches_sampled_center_change":expected_depth.map(|_|depth_matches),
        "through_on_sampled_rays":sampled_through,
        "actual_material":{
            "center_before_intervals":center_before_intervals,
            "center_after_intervals":center_after_intervals,
            "center_before_length_mm":before_length,
            "center_after_length_mm":after_length,
            "center_remaining_material":center_remaining,
            "interior_rays_with_removal":sampled["summary"]["interior_rays_with_material_removed"],
            "interior_rays_total":sampled["summary"]["interior_rays_total"],
            "outside_rays_retaining_material":sampled["summary"]["outside_rays_retaining_material"],
            "outside_rays_total":sampled["summary"]["outside_rays_total"],
        },
        "ray_evidence":sampled,
        "measurement_scope":"sampled material intervals; not fitted diameter metrology",
    }))
}

fn normalized_feature(feature: &Value, plan: &Value) -> Result<Value, String> {
    let length_factor = match plan["units"]["length"].as_str().unwrap_or("mm") {
        "mm" => 1.,
        "cm" => 10.,
        "m" => 1000.,
        "in" => 25.4,
        _ => return Err("未知长度单位。".into()),
    };
    let angle_factor = match plan["units"]["angle"].as_str().unwrap_or("deg") {
        "deg" => 1.,
        "rad" => 180. / std::f64::consts::PI,
        _ => return Err("未知角度单位。".into()),
    };
    let mut result = feature.clone();
    let is_shell = result.get("kind").and_then(Value::as_str) == Some("shell");
    let params = result["parameters"]
        .as_object_mut()
        .ok_or("parameters 必须为对象。")?;
    for (key, value) in params.iter_mut() {
        let factor = if (is_shell && key == "thickness")
            || [
                "length",
                "width",
                "height",
                "wall_t",
                "radius",
                "radius2",
                "module",
                "corner",
                "margin",
                "diameter",
                "pitch_d",
                "outer_d",
                "hub_d",
                "tip_d",
                "blade_t",
                "groove_depth",
                "depth",
                "pcd",
                "x",
                "y",
                "z",
                "center",
                "center2",
                "point",
                "step",
                "entry_point",
                "profile",
                "shank_d",
                "pin_d",
                "thread_pitch",
                "head_d",
                "head_h",
                "hex_s",
                "hex_t",
                "across_flats",
                "outer_r",
                "rim_w",
                "hub_r",
                "spoke_w",
                "big_d",
                "main_d",
                "stroke",
                "small_d",
                "big_bore",
                "small_bore",
                "shank_w",
                "thick",
                "span",
                "journal_d",
                "base_d",
                "lift",
                "bore_d",
                "wire_r",
                "thread_r",
                "height2",
                "pitch_d",
                "root_chord",
                "tip_chord",
                "sweep",
                "translate",
            ]
            .contains(&key.as_str())
        {
            length_factor
        } else if key == "phase"
            || key == "angle"
            || key == "lobe_deg"
            || key == "cone_deg"
            || key == "twist"
            || key == "twist_deg"
        {
            angle_factor
        } else {
            continue;
        };
        fn scale(value: &mut Value, factor: f64) -> Result<(), String> {
            if value.is_null() {
                return Ok(());
            }
            if let Some(rows) = value.as_array_mut() {
                for row in rows {
                    scale(row, factor)?;
                }
                return Ok(());
            }
            let number = value.as_f64().ok_or("长度/角度必须为数值。")? * factor;
            if !number.is_finite() {
                return Err("单位换算超出有限数值范围。".into());
            }
            *value = json!(number);
            Ok(())
        }
        scale(value, factor)?;
    }
    if let Some(rotate) = params.get_mut("rotate").filter(|value| !value.is_null()) {
        let values = rotate
            .as_array_mut()
            .filter(|values| values.len() == 2)
            .ok_or("rotate 必须是 [X|Y|Z, 角度]。")?;
        let angle = values[1].as_f64().ok_or("rotate 角度必须为数值。")? * angle_factor;
        if !angle.is_finite() {
            return Err("rotate 单位换算超出有限数值范围。".into());
        }
        values[1] = json!(angle);
    }
    Ok(result)
}

fn hole_positions(
    p: &Value,
    bounds: [f64; 6],
    axis: usize,
    diameter: f64,
) -> Result<Vec<[f64; 2]>, String> {
    let plane = plane_indices(axis);
    let [a, b] = plane;
    let middle = [
        (bounds[a] + bounds[a + 3]) / 2.,
        (bounds[b] + bounds[b + 3]) / 2.,
    ];
    match p.get("pos").and_then(Value::as_str).unwrap_or("center") {
        "corners" => {
            let margin = optional_num(p, "margin", 0.)? + diameter / 2.;
            Ok(vec![
                [bounds[a] + margin, bounds[b] + margin],
                [bounds[a + 3] - margin, bounds[b] + margin],
                [bounds[a + 3] - margin, bounds[b + 3] - margin],
                [bounds[a] + margin, bounds[b + 3] - margin],
            ])
        }
        "pcd" => {
            let count = p.get("count").and_then(Value::as_u64).unwrap_or(4);
            if count == 0 || count > MAX_HOLE_PATTERN_COUNT {
                return Err("孔阵列 count 超出几何资源上限。".into());
            }
            let radius = num(p, "pcd")? / 2.;
            let phase = optional_num(p, "phase", 0.)?.to_radians();
            Ok((0..count)
                .map(|i| {
                    let angle = 2. * std::f64::consts::PI * i as f64 / count as f64 + phase;
                    [radius * angle.cos(), radius * angle.sin()]
                })
                .collect())
        }
        "point" => {
            let xyz = point(p.get("point"), [0.; 3])?;
            Ok(vec![[xyz[a], xyz[b]]])
        }
        "center" => Ok(vec![[
            optional_num(p, "x", middle[0])?,
            optional_num(p, "y", middle[1])?,
        ]]),
        _ => Err("未知孔位模式。".into()),
    }
}
fn check_cancel(cancelled: &AtomicBool) -> Result<(), String> {
    if cancelled.load(Ordering::Acquire) {
        Err("几何任务已取消。".into())
    } else {
        Ok(())
    }
}
fn num(p: &Value, key: &str) -> Result<f64, String> {
    p.get(key)
        .and_then(Value::as_f64)
        .filter(|v| v.is_finite())
        .ok_or_else(|| format!("参数 {key} 必须是有限数值。"))
}
fn optional_num(p: &Value, key: &str, default: f64) -> Result<f64, String> {
    if p.get(key).is_none_or(Value::is_null) {
        Ok(default)
    } else {
        num(p, key)
    }
}
fn point(value: Option<&Value>, default: [f64; 3]) -> Result<[f64; 3], String> {
    let Some(value) = value.filter(|v| !v.is_null()) else {
        return Ok(default);
    };
    let array = value
        .as_array()
        .filter(|v| v.len() == 3)
        .ok_or("坐标必须是全局 XYZ 数组。")?;
    let mut result = [0.; 3];
    for i in 0..3 {
        result[i] = array[i]
            .as_f64()
            .filter(|v| v.is_finite())
            .ok_or("坐标必须是有限数值。")?;
    }
    Ok(result)
}
fn axis_index(p: &Value) -> Result<usize, String> {
    match p
        .get("axis")
        .and_then(Value::as_str)
        .unwrap_or("Z")
        .to_uppercase()
        .as_str()
    {
        "X" => Ok(0),
        "Y" => Ok(1),
        "Z" => Ok(2),
        _ => Err("未知轴。".into()),
    }
}
fn plane_indices(axis: usize) -> [usize; 2] {
    match axis {
        0 => [1, 2],
        1 => [0, 2],
        _ => [0, 1],
    }
}
fn profile_indices(axis: usize) -> [usize; 2] {
    // Existing right-handed prism basis: Y extrusion uses (Z,X), while
    // hole/pocket position parameters remain ascending global plane axes.
    match axis {
        0 => [1, 2],
        1 => [2, 0],
        _ => [0, 1],
    }
}
fn axis_vector(axis: usize) -> [f64; 3] {
    let mut v = [0.; 3];
    v[axis] = 1.;
    v
}
fn bounds_array(bb: Bounds) -> [f64; 6] {
    [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counterbore_pairing_requires_an_earlier_smaller_coaxial_through_pilot() {
        let pilot = json!({
            "feature_id":"pilot","through":true,"diameter_mm":6.6,
            "centerline_mm":[0.0,0.0,10.0],"axis":"Z","axis_vector_mm":[0.0,0.0,1.0]
        });
        let counterbore = json!({
            "feature_id":"counterbore","through":false,"diameter_mm":11.0,"depth_mm":5.0,
            "centerline_mm":[0.0,0.0,10.0],"axis":"Z","axis_vector_mm":[0.0,0.0,1.0]
        });
        assert_eq!(
            matching_counterbore_pilot_radius(std::slice::from_ref(&pilot), &counterbore),
            Some(3.3)
        );
        assert_eq!(matching_later_counterbore(&[pilot.clone(), counterbore], 0), Some((5.5, 5.0)));

        let ordinary_blind = json!({
            "feature_id":"ordinary-blind","through":false,"diameter_mm":11.0,"depth_mm":5.0,
            "centerline_mm":[0.0,0.0,10.0],"axis":"Z","axis_vector_mm":[0.0,0.0,1.0]
        });
        assert_eq!(
            matching_counterbore_pilot_radius(&[], &ordinary_blind),
            None,
            "an ordinary blind hole must continue through centerline material sampling"
        );

        let offset_pilot = json!({
            "feature_id":"offset-pilot","through":true,"diameter_mm":6.6,
            "centerline_mm":[1.0,0.0,10.0],"axis":"Z","axis_vector_mm":[0.0,0.0,1.0]
        });
        assert_eq!(
            matching_counterbore_pilot_radius(std::slice::from_ref(&offset_pilot), &ordinary_blind),
            None
        );
        let reversed_pilot = json!({
            "feature_id":"reversed-pilot","through":true,"diameter_mm":6.6,
            "centerline_mm":[0.0,0.0,10.0],"axis":"Z","axis_vector_mm":[0.0,0.0,-1.0]
        });
        assert_eq!(
            matching_counterbore_pilot_radius(std::slice::from_ref(&reversed_pilot), &ordinary_blind),
            None
        );
        let blind_pilot = json!({
            "feature_id":"blind-pilot","through":false,"diameter_mm":6.6,"depth_mm":4.0,
            "centerline_mm":[0.0,0.0,10.0],"axis":"Z","axis_vector_mm":[0.0,0.0,1.0]
        });
        assert_eq!(
            matching_counterbore_pilot_radius(std::slice::from_ref(&blind_pilot), &ordinary_blind),
            None
        );
    }

    fn fixture() -> Value {
        serde_json::from_str(include_str!(
            "../examples/fixtures/native_geometry_plan.json"
        ))
        .unwrap()
    }
    #[test]
    fn partial_builder_keeps_only_a_nonempty_longest_built_prefix() {
        let plan = json!({"features":[
            {"id":"body","enabled":true},
            {"id":"hole-1","enabled":true},
            {"id":"hole-2","enabled":true}
        ]});
        assert_eq!(longest_built_prefix(&plan, &[], "body"), None,
            "first-feature failure has no partial solid to publish");
        assert_eq!(
            longest_built_prefix(
                &plan,
                &[json!({"feature_id":"body","state":"built"})],
                "hole-1"
            ),
            Some(vec!["body".to_string()])
        );
        assert_eq!(
            longest_built_prefix(
                &plan,
                &[json!({"feature_id":"body","state":"no_op"})],
                "hole-1"
            ),
            None,
            "no-op feature is not evidence that geometry was applied"
        );
        assert_eq!(
            longest_built_prefix(
                &plan,
                &[json!({"feature_id":"hole-1","state":"built"})],
                "hole-2"
            ),
            None,
            "a non-prefix applied ID list is rejected"
        );
    }

    #[test]
    fn partial_builder_rejects_invalid_multi_or_open_brep() {
        assert!(is_valid_single_closed_solid(1, 1, 1));
        assert!(!is_valid_single_closed_solid(0, 1, 1));
        assert!(!is_valid_single_closed_solid(1, 2, 2));
        assert!(!is_valid_single_closed_solid(1, 1, 0));
    }

    #[test]
    fn fixture_passes_preflight_and_box_corner_profiles_are_native() {
        let original = fixture();
        assert_eq!(preflight(&original).unwrap()["state"], "valid");
        let mut rounded = original.clone();
        rounded["features"][0]["parameters"]["corner"] = json!(2);
        rounded["features"][0]["sources"]["corner"] = json!({"type":"user"});
        rounded["features"][0]["parameters"]["corner_mode"] = json!("fillet");
        rounded["features"][0]["sources"]["corner_mode"] = json!({"type":"user"});
        assert_eq!(preflight(&rounded).unwrap()["state"], "valid");
        let mut rounded_pocket = original.clone();
        rounded_pocket["features"][1]["parameters"]["corner"] = json!(1);
        rounded_pocket["features"][1]["sources"]["corner"] = json!({"type":"user"});
        let unsupported_pocket_corner = preflight(&rounded_pocket).unwrap_err();
        assert!(unsupported_pocket_corner.contains("pocket.corner"), "{unsupported_pocket_corner}");
        assert_eq!(original["features"].as_array().unwrap().len(), 11);
    }

    #[test]
    fn legacy_box_corner_profile_matches_polygon_contract_and_clamps_radius() {
        let fillet = rounded_rectangle_profile(100.0, 60.0, 5.0, "fillet");
        assert_eq!(fillet.len(), 36);
        let chamfer = rounded_rectangle_profile(80.0, 40.0, 3.0, "chamfer");
        assert_eq!(chamfer.len(), 8);
        let oversized = rounded_rectangle_profile(80.0, 40.0, 25.0, "chamfer");
        let clamped = rounded_rectangle_profile(80.0, 40.0, 19.999999, "chamfer");
        assert_eq!(oversized, clamped);
        assert_eq!(rounded_rectangle_profile(80.0, 40.0, 0.0, "fillet").len(), 4);
    }

    #[test]
    fn gear_tooth_parameter_limit_is_enforced_by_preflight() {
        let make_plan = |teeth| {
            crate::cad_plan::create(
                "gear preflight fixture",
                json!({"id":"gear-1","kind":"gear","operation":"add",
                    "parameters":{"teeth":teeth,"module":2,"height":5},
                    "sources":{"teeth":{"type":"image_estimate","evidence":"fixture top view"},
                        "module":{"type":"image_estimate","evidence":"fixture top view"},
                        "height":{"type":"image_estimate","evidence":"fixture front view"}}}),
                "fixture",
                None,
            )
        };
        let out_of_range = make_plan(10_001);
        let error = preflight(&out_of_range).unwrap_err();
        assert!(error.contains("gear-1 的 gear.teeth=10001"), "{error}");
        assert!(error.contains("6..=10000"), "{error}");
        assert!(error.contains("调整齿数后再预览"), "{error}");

        assert_eq!(preflight(&make_plan(6)).unwrap()["state"], "valid");
        let limits = crate::native_feature_specs::feature_spec("gear").unwrap()["native_execution"]
            ["parameter_limits"]["teeth"]
            .as_str()
            .unwrap()
            .to_owned();
        assert!(limits.contains(&format!(
            "{}..={}",
            native_standard_features::MIN_GEAR_TEETH,
            native_standard_features::MAX_GEAR_TEETH
        )));
    }

    #[test]
    fn helical_gear_is_registered_preflighted_and_rejects_missing_or_out_of_range_teeth() {
        let make_plan = |parameters| {
            crate::cad_plan::create(
                "helical gear preflight fixture",
                json!({"id":"helical-1","kind":"helical_gear","operation":"add",
                    "parameters":parameters,
                    "sources":{"teeth":{"type":"image_estimate","evidence":"fixture top view"},
                        "module":{"type":"image_estimate","evidence":"fixture top view"},
                        "height":{"type":"image_estimate","evidence":"fixture front view"}}}),
                "fixture",
                None,
            )
        };
        assert!(supports_native_kind("helical_gear"));
        assert_eq!(
            preflight(&make_plan(json!({"teeth":20,"module":2,"height":10}))).unwrap()["state"],
            "valid"
        );
        let missing = preflight(&make_plan(json!({"teeth":20,"module":2}))).unwrap_err();
        assert!(missing.contains("height"), "{missing}");
        let out_of_range =
            preflight(&make_plan(json!({"teeth":10_001,"module":2,"height":10}))).unwrap_err();
        assert!(
            out_of_range.contains("helical-1 的 helical_gear.teeth=10001"),
            "{out_of_range}"
        );
        assert!(out_of_range.contains("6..=10000"), "{out_of_range}");
    }

    #[test]
    fn vbelt_pulley_native_preflight_accepts_supported_profile_and_rejects_missing_or_narrow_width() {
        let make_plan = |parameters| {
            crate::cad_plan::create(
                "V belt pulley preflight fixture",
                json!({"id":"pulley-1","kind":"vbelt_pulley","operation":"add",
                    "parameters":parameters,
                    "sources":{"pitch_d":{"type":"user"},"grooves":{"type":"user"},
                        "height":{"type":"user"},"groove_type":{"type":"user"},
                        "bore_d":{"type":"user"}}}),
                "fixture",
                None,
            )
        };
        assert!(supports_native_kind("vbelt_pulley"));
        let good = make_plan(
            json!({"pitch_d":80,"grooves":3,"height":55,"groove_type":"A","bore_d":12}),
        );
        assert_eq!(preflight(&good).unwrap()["state"], "valid");
        let missing = make_plan(json!({"pitch_d":80,"grooves":3,"height":55}));
        assert!(preflight(&missing).unwrap_err().contains("groove_type"));
        let narrow =
            make_plan(json!({"pitch_d":80,"grooves":3,"height":40,"groove_type":"A"}));
        assert!(preflight(&narrow).unwrap_err().contains("最小轮宽"));
        let mut centimeters = make_plan(json!({"pitch_d":24,"grooves":3,"height":70,
            "groove_type":"B","bore_d":2,"outer_d":24.7}));
        centimeters["units"]["length"] = json!("cm");
        centimeters["features"][0]["sources"]["outer_d"] = json!({"type":"user"});
        assert_eq!(preflight(&centimeters).unwrap()["state"], "valid");
    }

    #[test]
    fn bevel_gear_length_and_cone_angle_units_are_normalized_before_preflight() {
        let mut plan = fixture();
        plan["units"] = json!({"length":"cm","angle":"rad"});
        let feature = json!({"id":"bevel","kind":"bevel_gear","operation":"add",
            "parameters":{"teeth":20,"module":0.2,"height":1.2,
                "cone_deg":0.7853981633974483,"bore_d":0,"center":[1.2,-0.7,0.5],"segments":12},
            "sources":{"teeth":{"type":"user"},"module":{"type":"user"},
                "height":{"type":"user"},"cone_deg":{"type":"user"},
                "bore_d":{"type":"user"},"center":{"type":"user"},"segments":{"type":"user"}}});
        let normalized = normalized_feature(&feature, &plan).unwrap();
        assert_eq!(normalized["parameters"]["module"], json!(2.0));
        assert_eq!(normalized["parameters"]["height"], json!(12.0));
        assert_eq!(normalized["parameters"]["center"], json!([12.0,-7.0,5.0]));
        assert_eq!(normalized["parameters"]["bore_d"], json!(0.0));
        assert!((normalized["parameters"]["cone_deg"].as_f64().unwrap() - 45.0).abs() < 1e-12);
    }

    #[test]
    fn loft_length_angle_and_rigid_transform_units_are_normalized() {
        let mut plan = fixture();
        plan["units"] = json!({"length":"cm","angle":"rad"});
        let feature = json!({"id":"loft","kind":"loft","operation":"add",
            "parameters":{"root_chord":3,"tip_chord":1.65,"span":6,"thickness":0.12,
                "twist":0.3141592653589793,"sweep":0.5,"translate":[1,2,3],
                "rotate":["Y",1.5707963267948966]},"sources":{}});
        let normalized = normalized_feature(&feature, &plan).unwrap();
        assert_eq!(normalized["parameters"]["root_chord"], json!(30.0));
        assert_eq!(normalized["parameters"]["tip_chord"], json!(16.5));
        assert_eq!(normalized["parameters"]["span"], json!(60.0));
        assert_eq!(normalized["parameters"]["sweep"], json!(5.0));
        assert_eq!(normalized["parameters"]["translate"], json!([10.0,20.0,30.0]));
        assert!((normalized["parameters"]["twist"].as_f64().unwrap() - 18.0).abs() < 1e-12);
        assert!((normalized["parameters"]["rotate"][1].as_f64().unwrap() - 90.0).abs() < 1e-12);
        assert_eq!(normalized["parameters"]["thickness"], json!(0.12));
    }

    #[test]
    fn hole_point_uses_global_coordinates_on_each_axis() {
        let p = json!({"pos":"point","point":[11,22,33]});
        assert_eq!(
            hole_positions(&p, [0.; 6], 0, 5.).unwrap(),
            vec![[22., 33.]]
        );
        assert_eq!(
            hole_positions(&p, [0.; 6], 1, 5.).unwrap(),
            vec![[11., 33.]]
        );
        assert_eq!(
            hole_positions(&p, [0.; 6], 2, 5.).unwrap(),
            vec![[11., 22.]]
        );
    }

    #[test]
    fn four_corner_hole_positions_apply_edge_margin_plus_hole_radius() {
        let p = json!({"pos":"corners","margin":15.0});
        assert_eq!(
            hole_positions(&p, [0., 0., 0., 100., 80., 20.], 2, 10.).unwrap(),
            vec![[20., 20.], [80., 20.], [80., 60.], [20., 60.]]
        );
    }

    #[test]
    fn pcd_hole_centers_use_global_origin_count_and_degree_phase() {
        let pcd = json!({"pos":"pcd","pcd":60.0,"count":4});
        let points = hole_positions(&pcd, [-50., -40., 0., 50., 40., 20.], 2, 10.)
            .unwrap();
        let expected = [[30., 0.], [0., 30.], [-30., 0.], [0., -30.]];
        for (actual, expected) in points.iter().zip(expected) {
            assert!((actual[0] - expected[0]).abs() < 1e-10);
            assert!((actual[1] - expected[1]).abs() < 1e-10);
        }

        let phased = json!({"pos":"pcd","pcd":60.0,"count":4,"phase":30.0});
        let points = hole_positions(&phased, [-50., -40., 0., 50., 40., 20.], 2, 10.)
            .unwrap();
        let radius = 30.0_f64;
        let expected = [
            [radius * 30.0_f64.to_radians().cos(), radius * 30.0_f64.to_radians().sin()],
            [radius * 120.0_f64.to_radians().cos(), radius * 120.0_f64.to_radians().sin()],
            [radius * 210.0_f64.to_radians().cos(), radius * 210.0_f64.to_radians().sin()],
            [radius * 300.0_f64.to_radians().cos(), radius * 300.0_f64.to_radians().sin()],
        ];
        for (actual, expected) in points.iter().zip(expected) {
            assert!((actual[0] - expected[0]).abs() < 1e-10);
            assert!((actual[1] - expected[1]).abs() < 1e-10);
        }

        let maximum = json!({
            "pos":"pcd",
            "pcd":60.0,
            "count":MAX_HOLE_PATTERN_COUNT
        });
        assert_eq!(
            hole_positions(&maximum, [0.; 6], 2, 10.).unwrap().len(),
            MAX_HOLE_PATTERN_COUNT as usize
        );

        let over_limit = json!({
            "pos":"pcd",
            "pcd":60.0,
            "count":MAX_HOLE_PATTERN_COUNT + 1
        });
        assert!(hole_positions(&over_limit, [0.; 6], 2, 10.).is_err());
    }

    #[test]
    fn hole_measurement_complete_requires_every_enabled_instance_to_be_finally_accepted() {
        let through = json!({
            "feature_id":"h1","through":true,"status":"verified_open",
            "final_measurement":{"accepted":true,"full_angular_coverage":true}
        });
        let blind = json!({
            "feature_id":"h2","through":false,"status":"verified_blind",
            "final_measurement":{"accepted":true,"full_angular_coverage":true}
        });
        assert!(hole_measurements_complete(&[through.clone(), blind]));
        assert!(hole_measurements_complete(&[]));

        // Sampling may have marked the bore open, but a failed request to
        // construct its final measurement must not count as complete.
        let sampled_only = json!({
            "feature_id":"h3","through":true,"status":"verified_open",
            "final_measurement_status":"candidate_unverified"
        });
        assert!(!hole_measurements_complete(&[
            through.clone(),
            sampled_only.clone(),
        ]));

        let rejected = json!({
            "feature_id":"h4","through":true,"status":"candidate_unverified",
            "final_measurement":{"accepted":false,"full_angular_coverage":true}
        });
        assert!(!hole_measurements_complete(&[through.clone(), rejected]));

        let partial_coverage = json!({
            "feature_id":"h5","through":true,"status":"candidate_unverified",
            "final_measurement":{"accepted":true,"full_angular_coverage":false}
        });
        assert!(!hole_measurements_complete(&[through, partial_coverage]));

        let plan = json!({"features":[
            {"id":"h1","kind":"hole","enabled":true},
            {"id":"h2","kind":"hole","enabled":false}
        ]});
        let report = json!({
            "hole_measurements_verified":true,
            "hole_measurement_complete":true,
            "hole_instances":[{
                "feature_id":"h1","through":true,"status":"verified_open"
            }]
        });
        assert_eq!(
            crate::native_cad_session::hole_measurement(&plan, &report)["complete"],
            true,
            "the finish gate should accept complete measurements for every enabled hole"
        );

        let incomplete_report = json!({
            "hole_measurements_verified":true,
            "hole_measurement_complete":hole_measurements_complete(&[sampled_only]),
            "hole_instances":[{
                "feature_id":"h1","through":true,"status":"verified_open"
            }]
        });
        assert_eq!(
            crate::native_cad_session::hole_measurement(&plan, &incomplete_report)["complete"],
            false,
            "the finish gate must reject sampled-only or failed final measurements"
        );
    }

    #[test]
    fn pcd_remains_global_origin_as_legacy_contract() {
        let positions =
            hole_positions(&json!({"pos":"pcd","pcd":20,"count":4}), [10.; 6], 2, 5.).unwrap();
        assert_eq!(positions[0], [10., 0.]);
        assert!((positions[1][1] - 10.).abs() < 1e-10);
    }
    #[test]
    fn pattern_transform_preserves_global_linear_and_right_handed_circular_semantics() {
        let translated = transform_point(translation_matrix([2., -3., 4.]), [1., 2., 3.]);
        assert_eq!(translated, [3., -1., 7.]);

        let center = [1., 2., 3.];
        let around_x = transform_point(rotation_matrix(0, 90., center).unwrap(), [1., 3., 3.]);
        let around_y = transform_point(rotation_matrix(1, 90., center).unwrap(), [1., 2., 4.]);
        let around_z = transform_point(rotation_matrix(2, 90., center).unwrap(), [2., 2., 3.]);
        assert!(
            around_x
                .iter()
                .zip([1., 2., 4.])
                .all(|(a, b)| (a - b).abs() < 1e-10)
        );
        assert!(
            around_y
                .iter()
                .zip([2., 2., 3.])
                .all(|(a, b)| (a - b).abs() < 1e-10)
        );
        assert!(
            around_z
                .iter()
                .zip([1., 3., 3.])
                .all(|(a, b)| (a - b).abs() < 1e-10)
        );

        let rotated_axis = normalize_vector(transform_vector(
            rotation_matrix(2, 45., [0., 0., 0.]).unwrap(),
            axis_vector(1),
        ))
        .unwrap();
        let expected_axis = [
            -std::f64::consts::FRAC_1_SQRT_2,
            std::f64::consts::FRAC_1_SQRT_2,
            0.,
        ];
        assert!(
            rotated_axis
                .iter()
                .zip(expected_axis)
                .all(|(a, b)| (a - b).abs() < 1e-10)
        );
        let limits = projected_line_limits(
            [2., 2., 0.],
            rotated_axis,
            [-10., -10., -10., 10., 10., 10.],
        );
        assert!(limits.0 < -14. && limits.1 > 14.);
    }

    #[test]
    fn native_pattern_preflight_requires_a_valid_mode_and_finite_transform() {
        let mut plan = fixture();
        plan["features"] = json!([
            {"id":"body","kind":"box","operation":"add","parameters":{"length":10,"width":10,"height":10},
             "sources":{"length":{"type":"user"},"width":{"type":"user"},"height":{"type":"user"}}},
            {"id":"pattern","kind":"pattern","operation":"add","parameters":{"mode":"linear","count":2,"step":[1,0,0]},
             "sources":{"count":{"type":"user"},"step":{"type":"user"}},
             "relations":[{"type":"pattern_source","feature_id":"body"}]}
        ]);
        assert_eq!(preflight(&plan).unwrap()["state"], "valid");
        let mut missing_step = plan.clone();
        missing_step["features"][1]["parameters"]
            .as_object_mut()
            .unwrap()
            .remove("step");
        missing_step["features"][1]["sources"]
            .as_object_mut()
            .unwrap()
            .remove("step");
        assert!(preflight(&missing_step).is_err());
        plan["features"][1]["parameters"] = json!({
            "mode":"circular","count":4,"axis":"Z","angle":360,"center":[0,0,0]
        });
        plan["features"][1]["sources"] = json!({
            "count":{"type":"user"},"angle":{"type":"user"},"center":{"type":"user"}
        });
        assert_eq!(preflight(&plan).unwrap()["state"], "valid");
        plan["features"][1]["parameters"]["count"] = json!(MAX_PATTERN_COUNT + 1);
        assert!(preflight(&plan).unwrap_err().contains("OCCT 单特征上限"));
        plan["features"][1]["parameters"]["count"] = json!(2);
        plan["features"][1]["parameters"]["mode"] = json!("linear");
        plan["features"][1]["parameters"]["step"] = json!([1, "bad", 0]);
        assert!(preflight(&plan).is_err());
    }

    #[test]
    fn pattern_step_uses_the_plan_length_unit() {
        let mut plan = fixture();
        plan["units"]["length"] = json!("cm");
        let pattern = json!({"id":"pattern","kind":"pattern","operation":"add",
            "parameters":{"mode":"linear","count":3,"step":[1,0,0]},
            "sources":{"count":{"type":"user"},"step":{"type":"user"}}});
        let normalized = normalized_feature(&pattern, &plan).unwrap();
        assert_eq!(normalized["parameters"]["step"], json!([10., 0., 0.]));
    }
    #[test]
    fn piston_dimensions_use_the_plan_length_unit() {
        let mut plan = fixture();
        plan["units"]["length"] = json!("cm");
        let piston = json!({"id":"piston","kind":"piston","operation":"add",
            "parameters":{"diameter":2,"height":1.2,"pin_d":0.4,"grooves":2,"center":[0.2,-0.3,1]},
            "sources":{"diameter":{"type":"user"},"height":{"type":"user"},
                "pin_d":{"type":"user"},"grooves":{"type":"user"},"center":{"type":"user"}}});
        let normalized = normalized_feature(&piston, &plan).unwrap();
        assert_eq!(normalized["parameters"]["diameter"], json!(20.));
        assert_eq!(normalized["parameters"]["height"], json!(12.));
        assert_eq!(normalized["parameters"]["pin_d"], json!(4.));
        assert_eq!(normalized["parameters"]["center"], json!([2., -3., 10.]));
        assert_eq!(piston["parameters"]["pin_d"], json!(0.4));
        plan["features"] = json!([piston]);
        assert_eq!(preflight(&plan).unwrap()["state"], "valid");
    }

    #[test]
    fn shell_thickness_is_normalized_but_selector_binding_is_not_scaled() {
        let mut plan = fixture();
        plan["units"]["length"] = json!("cm");
        let feature = json!({"id":"shell","kind":"shell","operation":"cut",
            "parameters":{"thickness":0.2,"offset_mode":"inward",
                "removed_faces":[format!("shell-face-v1:{}", "a".repeat(64))]},
            "selection_binding":{"revision":7,"geometry_fingerprint":"a".repeat(64),
                "kernel_version":"7.9.3","inventory_version":2,"target_feature_id":"base"}});
        let normalized = normalized_feature(&feature, &plan).unwrap();
        assert_eq!(normalized["parameters"]["thickness"], json!(2.0));
        assert_eq!(normalized["parameters"]["offset_mode"], "inward");
        assert_eq!(
            normalized["parameters"]["removed_faces"],
            feature["parameters"]["removed_faces"]
        );
        assert_eq!(
            normalized["selection_binding"],
            feature["selection_binding"]
        );
    }

    #[test]
    fn camshaft_dimensions_use_plan_length_and_angle_units() {
        let mut plan = fixture();
        plan["units"] = json!({"length":"cm","angle":"rad"});
        let camshaft = json!({"id":"camshaft","kind":"camshaft","operation":"add",
            "parameters":{"length":8,"journal_d":1.2,"base_d":2,"lift":0.4,
                "lobes":4,"lobe_deg":0.296705972839036,"center":[1.2,-0.7,0.5]},
            "sources":{"length":{"type":"user"},"journal_d":{"type":"user"},
                "base_d":{"type":"user"},"lift":{"type":"user"},"lobes":{"type":"user"},
                "lobe_deg":{"type":"user"},"center":{"type":"user"}}});
        let normalized = normalized_feature(&camshaft, &plan).unwrap();
        assert_eq!(normalized["parameters"]["length"], json!(80.));
        assert_eq!(normalized["parameters"]["journal_d"], json!(12.));
        assert_eq!(normalized["parameters"]["base_d"], json!(20.));
        assert_eq!(normalized["parameters"]["lift"], json!(4.));
        assert_eq!(normalized["parameters"]["lobe_deg"], json!(17.));
        assert_eq!(normalized["parameters"]["center"], json!([12., -7., 5.]));
        assert_eq!(camshaft["parameters"]["length"], json!(8));
    }

    #[test]
    fn crankshaft_dimensions_use_the_plan_length_unit() {
        let mut plan = fixture();
        plan["units"]["length"] = json!("cm");
        let crankshaft = json!({"id":"crankshaft","kind":"crankshaft","operation":"add",
            "parameters":{"length":32,"main_d":5,"pin_d":4.2,"stroke":8.6,
                "throws":4,"center":[1.2,-0.7,0.5]},
            "sources":{"length":{"type":"user"},"main_d":{"type":"user"},
                "pin_d":{"type":"user"},"stroke":{"type":"user"},
                "throws":{"type":"user"},"center":{"type":"user"}}});
        let normalized = normalized_feature(&crankshaft, &plan).unwrap();
        assert_eq!(normalized["parameters"]["length"], json!(320.));
        assert_eq!(normalized["parameters"]["main_d"], json!(50.));
        assert_eq!(normalized["parameters"]["pin_d"], json!(42.));
        assert_eq!(normalized["parameters"]["stroke"], json!(86.));
        assert_eq!(normalized["parameters"]["center"], json!([12., -7., 5.]));
        assert_eq!(crankshaft["parameters"]["length"], json!(32));
    }

    #[test]
    fn timing_pulley_is_registered_and_normalizes_length_center_and_bore() {
        let mut plan = fixture();
        plan["units"] = json!({"length":"cm","angle":"deg"});
        let feature = json!({"id":"timing","kind":"timing_pulley","operation":"add",
            "parameters":{"pitch_d":8,"teeth":50,"height":1,"tooth_type":"XL",
                "bore_d":2,"center":[1.2,-0.7,0.5]},
            "sources":{"pitch_d":{"type":"user"},"teeth":{"type":"user"},
                "height":{"type":"user"},"tooth_type":{"type":"user"},
                "bore_d":{"type":"user"},"center":{"type":"user"}}});
        let normalized = normalized_feature(&feature, &plan).unwrap();
        assert_eq!(normalized["parameters"]["pitch_d"], json!(80.0));
        assert_eq!(normalized["parameters"]["height"], json!(10.0));
        assert_eq!(normalized["parameters"]["bore_d"], json!(20.0));
        assert_eq!(normalized["parameters"]["center"], json!([12.0, -7.0, 5.0]));
        assert!(supports_native_kind("timing_pulley"));
        let capability = crate::native_feature_specs::feature_spec("timing_pulley").unwrap();
        assert_eq!(capability["native_execution"]["status"], "implemented_approximate");
        assert_eq!(capability["catia_mapping"], "approximate");
    }

    #[test]
    fn helix_is_registered_and_normalizes_wire_radius_with_plan_length_units() {
        let mut plan = fixture();
        plan["units"]["length"] = json!("cm");
        plan["features"] = json!([{
            "id":"spring","kind":"helix","operation":"add",
            "parameters":{"radius":0.8,"wire_r":0.1,"height":2,"turns":3},
            "sources":{"radius":{"type":"user"},"wire_r":{"type":"user"},
                "height":{"type":"user"},"turns":{"type":"user"}}
        }]);
        assert_eq!(crate::cad_plan_validation::validate_plan(&plan)["state"], "valid");
        assert!(supports_native_kind("helix"));
        let normalized = normalized_feature(&plan["features"][0], &plan).unwrap();
        assert_eq!(normalized["parameters"]["radius"], json!(8.0));
        assert_eq!(normalized["parameters"]["wire_r"], json!(1.0));
        assert_eq!(normalized["parameters"]["height"], json!(20.0));
        assert_eq!(preflight(&plan).unwrap()["state"], "valid");
        assert_eq!(plan["features"][0]["parameters"]["wire_r"], json!(0.1));
    }

    #[test]
    fn worm_is_registered_and_normalizes_both_centers_and_all_radial_lengths() {
        let mut plan = fixture();
        plan["units"]["length"] = json!("cm");
        let parameters = json!({
            "radius":1.0,"height":6.0,"thread_r":1.25,"wire_r":0.25,
            "height2":5.0,"turns":3.75,"axis":"Z","segments":48,
            "seg_per_turn":16,"wire_seg":8,
            "center":[0.0,0.0,-3.0],"center2":[0.0,0.0,-2.5]
        });
        let mut sources = serde_json::Map::new();
        for key in [
            "radius", "height", "thread_r", "wire_r", "height2", "turns",
            "segments", "seg_per_turn", "wire_seg", "center", "center2",
        ] {
            sources.insert(key.into(), json!({"type":"user"}));
        }
        plan["features"] = json!([{
            "id":"worm","kind":"worm","operation":"add",
            "parameters":parameters,"sources":sources
        }]);
        assert_eq!(crate::cad_plan_validation::validate_plan(&plan)["state"], "valid");
        assert!(supports_native_kind("worm"));
        let normalized = normalized_feature(&plan["features"][0], &plan).unwrap();
        for (key, value) in [
            ("radius", 10.0), ("height", 60.0), ("thread_r", 12.5),
            ("wire_r", 2.5), ("height2", 50.0),
        ] {
            assert_eq!(normalized["parameters"][key], json!(value), "{key}");
        }
        assert_eq!(normalized["parameters"]["center"], json!([0.0,0.0,-30.0]));
        assert_eq!(normalized["parameters"]["center2"], json!([0.0,0.0,-25.0]));
        assert_eq!(preflight(&plan).unwrap()["state"], "valid");
        assert_eq!(plan["features"][0]["parameters"]["wire_r"], json!(0.25));
        assert_eq!(plan["features"][0]["parameters"]["center2"], json!([0.0,0.0,-2.5]));
    }

    #[test]
    fn piston_geometry_is_advertised_as_native_exact_without_overstating_catia() {
        let support = crate::native_feature_specs::feature_spec("piston").unwrap();
        assert_eq!(support["native_execution"]["status"], "implemented_exact");
        assert_eq!(support["native_execution"]["geometry_support"], "exact");
        assert_eq!(support["catia_mapping"], "approximate");
    }
    #[test]
    fn unit_scaling_preserves_boolean_flags_and_source_plan() {
        let mut plan = fixture();
        plan["units"] = json!({"length":"cm","angle":"rad"});
        let original = plan["features"][0].clone();
        let scaled = normalized_feature(&original, &plan).unwrap();
        assert_eq!(scaled["parameters"]["length"], 1000.);
        assert_eq!(scaled["parameters"]["base"], true);
        assert_eq!(plan["features"][0], original);
    }
    #[test]
    fn invalid_or_cancelled_input_cannot_reach_dll_loading() {
        assert!(
            build(
                &json!({}),
                Path::new("nonexistent"),
                &AtomicBool::new(false)
            )
            .err()
            .expect("invalid plan cannot build")
            .contains("校验")
        );
        assert!(check_cancel(&AtomicBool::new(true)).is_err());
    }
}
            let stacked = p.get("stack").and_then(Value::as_bool) == Some(true);
            if stacked {
                let bounds = bb.ok_or("stack=true 的长方体需要已有实体作为堆叠目标。")?;
                let top = match axis {
                    0 => bounds.xmax,
                    1 => bounds.ymax,
                    _ => bounds.zmax,
                };
                center[axis] += top;
            }
