//! CADPlan-to-OCCT execution. Unsupported variants fail before kernel execution.
//! Hole metadata describes requested cuts, not independently measured bores.
use crate::{
    cad_plan, cad_plan_validation,
    native_occt::{Bounds, Kernel, Shape},
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
        if !["box", "cylinder", "sphere", "hole", "pocket"].contains(&kind) {
            return Err(format!(
                "Rust 几何执行尚不支持 {} ({kind})，未执行任何特征。",
                feature["id"]
            ));
        }
        if matches!(kind, "box" | "pocket") && optional_num(p, "corner", 0.)? != 0. {
            return Err("Rust 圆角矩形尚未接入；不能静默忽略 corner。".into());
        }
        if kind == "cylinder"
            && p.get("radius2").is_some_and(|v| !v.is_null())
            && (num(p, "radius2")? - num(p, "radius")?).abs() > 1e-10
        {
            return Err("Rust 圆锥尚未接入；不能改成等径圆柱。".into());
        }
        if kind == "hole" && p.get("entry_point").is_some_and(|v| !v.is_null()) {
            return Err("Rust 显式盲孔入口局部材料核验尚未接入。".into());
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
    for feature in plan["features"].as_array().ok_or("缺少 features")? {
        check_cancel(cancelled)?;
        if feature.get("enabled").and_then(Value::as_bool) == Some(false) {
            continue;
        }
        let before_volume = current
            .as_ref()
            .map(Shape::volume)
            .transpose()?
            .unwrap_or(0.);
        let bb = current.as_ref().map(Shape::bounds).transpose()?;
        let normalized = normalized_feature(feature, plan)?;
        let (tools, requested_holes) = feature_tools(&kernel, &normalized, bb)?;
        let tool_refs: Vec<_> = tools.iter().collect();
        let operation = feature["operation"].as_str().unwrap_or("add");
        let candidate = match (&current, operation) {
            (None, "add") => kernel.fuse(&tool_refs)?,
            (Some(stock), "cut") => kernel.cut(stock, &tool_refs)?,
            (Some(stock), "add") => {
                let mut refs = vec![stock];
                refs.extend(tool_refs);
                kernel.fuse(&refs)?
            }
            (None, "cut") => return Err("首特征不能切除；未提交候选。".into()),
            _ => return Err("operation 必须是 add 或 cut。".into()),
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
        if operation == "cut" && delta >= -1e-7_f64.max(before_volume * 1e-10) {
            return Err(format!(
                "特征 {} 切除没有实际减少材料；请检查孔位、方向或重复切除。",
                feature["id"]
            ));
        }
        rows.push(
            json!({"feature_id":feature["id"],"kind":feature["kind"],"operation":operation,
            "state":"built","geometry_support":"exact","volume_before_mm3":before_volume,
            "volume_after_mm3":volume,"volume_delta_mm3":delta}),
        );
        holes.extend(requested_holes);
        current = Some(candidate);
    }
    let shape = current.ok_or("没有启用的实体特征。")?;
    let bb = shape.bounds()?;
    let fingerprint = cad_plan::geometry_fingerprint(plan)?;
    let report = json!({"state":"built","execution_engine":"rust_c_abi_occt","occt_version":kernel.version(),
        "geometry_fingerprint":fingerprint,"validation":validation,"feature_results":rows,
        "bounds_mm":[bb.xmin,bb.ymin,bb.zmin,bb.xmax,bb.ymax,bb.zmax],
        "dimensions_mm":[bb.xmax-bb.xmin,bb.ymax-bb.ymin,bb.zmax-bb.zmin],
        "volume_mm3":shape.volume()?,"valid":true,"closed_solid_count":1,
        "requested_holes":holes,"hole_measurements_verified":false,
        "catia_executed":false,"image_acceptance":"not_performed"});
    Ok(BuiltPlan { shape, report })
}

fn feature_tools(
    kernel: &Kernel,
    feature: &Value,
    bb: Option<Bounds>,
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
            Ok((vec![kernel.make_box(size, center)?], vec![]))
        }
        "cylinder" => {
            let height = num(p, "height")?;
            if !p.get("base").and_then(Value::as_bool).unwrap_or(true) {
                center[axis] -= height / 2.;
            }
            Ok((
                vec![kernel.cylinder(num(p, "radius")?, height, center, axis_vector(axis))?],
                vec![],
            ))
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
            if depth
                .is_some_and(|d| d <= 0. || d >= high - low - (1e-6_f64.max((high - low) * 1e-8)))
            {
                return Err("盲孔深度必须小于实体轴向厚度，不能改成通孔。".into());
            }
            let positions = hole_positions(p, bounds, axis, diameter)?;
            let mut tools = vec![];
            let mut records = vec![];
            for (i, [a, b]) in positions.iter().enumerate() {
                let (start, height) = if let Some(d) = depth {
                    (high - d, d + 1.)
                } else {
                    (low - 10., high - low + 20.)
                };
                let mut base = [0.; 3];
                base[plane[0]] = *a;
                base[plane[1]] = *b;
                base[axis] = start;
                tools.push(kernel.cylinder(diameter / 2., height, base, axis_vector(axis))?);
                base[axis] = (low + high) / 2.;
                records.push(json!({"feature_id":feature["id"],"instance_index":i+1,
                    "centerline_mm":base,"axis":(["X","Y","Z"][axis]),"diameter_mm":diameter,
                    "through":through,"depth_mm":depth,"status":"requested_not_measured"}));
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
    let params = result["parameters"]
        .as_object_mut()
        .ok_or("parameters 必须为对象。")?;
    for (key, value) in params.iter_mut() {
        let factor = if [
            "length",
            "width",
            "height",
            "radius",
            "radius2",
            "corner",
            "margin",
            "diameter",
            "depth",
            "pcd",
            "x",
            "y",
            "z",
            "center",
            "point",
            "entry_point",
        ]
        .contains(&key.as_str())
        {
            length_factor
        } else if key == "phase" {
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
            if count == 0 || count > 4096 {
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
    fn fixture() -> Value {
        serde_json::from_str(include_str!(
            "../examples/fixtures/native_geometry_plan.json"
        ))
        .unwrap()
    }
    #[test]
    fn fixture_passes_preflight_but_unimplemented_variants_do_not() {
        let original = fixture();
        assert_eq!(preflight(&original).unwrap()["state"], "valid");
        let mut rounded = original.clone();
        rounded["features"][0]["parameters"]["corner"] = json!(2);
        rounded["features"][0]["sources"]["corner"] = json!({"type":"user"});
        assert!(preflight(&rounded).unwrap_err().contains("corner"));
        assert_eq!(original["features"].as_array().unwrap().len(), 11);
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
    fn pcd_remains_global_origin_as_legacy_contract() {
        let positions =
            hole_positions(&json!({"pos":"pcd","pcd":20,"count":4}), [10.; 6], 2, 5.).unwrap();
        assert_eq!(positions[0], [10., 0.]);
        assert!((positions[1][1] - 10.).abs() < 1e-10);
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
