//! Native OCCT builder for the legacy timing-pulley CADPlan recipe.
//!
//! Tooth flanks preserve the existing standard trapezoid approximation. The
//! analytic annular blank intentionally replaces the legacy 64-segment
//! revolved mesh; capability remains approximate and reports this boundary.
use crate::native_occt::{Axis, Kernel, Shape, TriangleMesh};
use serde_json::{Value, json};

const MAX_TEETH: u64 = 512;
const MAX_LEGACY_SEGMENTS: i64 = 512;

#[derive(Clone, Copy)]
struct TimingType {
    pitch: f64,
    tooth_height: f64,
    tooth_tip: f64,
}

fn timing_type(value: &str) -> (String, TimingType) {
    let name = value.trim().to_ascii_uppercase().replace('型', "");
    let row = match name.as_str() {
        "MXL" => (2.032, 0.76, 1.14),
        "XL" => (5.080, 1.27, 2.57),
        "L" => (9.525, 1.91, 4.65),
        "H" => (12.700, 2.29, 6.12),
        "XH" => (22.225, 3.05, 9.53),
        "XXH" => (31.750, 3.30, 12.70),
        "T2.5" => (2.500, 0.70, 1.50),
        "T5" => (5.000, 1.20, 2.65),
        "T10" => (10.000, 2.50, 5.30),
        "T20" => (20.000, 4.00, 10.70),
        "3M" => (3.000, 1.17, 1.85),
        "5M" => (5.000, 1.94, 3.05),
        "8M" => (8.000, 3.05, 5.20),
        "14M" => (14.000, 5.60, 9.40),
        // std_pulley.timing_pitch explicitly falls back to XL for unknown text.
        _ => {
            return (
                "XL".into(),
                TimingType {
                    pitch: 5.080,
                    tooth_height: 1.27,
                    tooth_tip: 2.57,
                },
            );
        }
    };
    (
        name,
        TimingType {
            pitch: row.0,
            tooth_height: row.1,
            tooth_tip: row.2,
        },
    )
}

pub fn preflight(parameters: &Value) -> Result<Value, String> {
    for key in ["pitch_d", "teeth", "height", "tooth_type"] {
        if parameters.get(key).is_none_or(Value::is_null) {
            return Err(format!("timing_pulley 缺少必填几何参数 {key}。"));
        }
    }
    let pitch_d = number(parameters, "pitch_d")?;
    let height = number(parameters, "height")?;
    let teeth = parameters["teeth"]
        .as_u64()
        .ok_or("timing_pulley.teeth 必须是正整数。")?;
    let raw_type = parameters["tooth_type"]
        .as_str()
        .ok_or("timing_pulley.tooth_type 必须是字符串。")?;
    let (resolved_type, spec) = timing_type(raw_type);
    if resolved_type != raw_type.trim().to_ascii_uppercase().replace('型', "") {
        return Err(format!(
            "CADPlan timing_pulley.tooth_type={raw_type:?} 不属于受支持的 14 个 legacy 标准型号。"
        ));
    }
    if spec.tooth_height * 0.55 > 1.0 {
        return Err(format!(
            "{resolved_type} 型 legacy 齿根半径与齿柱内缘之间没有材料重叠；原配方不能保证单一闭合实体，当前原生候选几何拒绝此型号。"
        ));
    }
    let center = point3(parameters.get("center"))?;
    let bore_d = optional_number(parameters, "bore_d", 0.0)?;
    let _segments = optional_integer(parameters, "segments", 64)?;
    if pitch_d <= 0.0
        || height <= 0.0
        || teeth < 6
        || teeth > MAX_TEETH
        || bore_d < 0.0
        || center.iter().any(|v| !v.is_finite())
    {
        return Err(format!(
            "同步带轮要求 pitch_d/height 为正数、teeth 为 6..={MAX_TEETH} 整数、bore_d 非负且 center 有限。"
        ));
    }
    if !(3..=MAX_LEGACY_SEGMENTS).contains(&_segments) {
        return Err(format!(
            "timing_pulley.segments 必须为 3..={MAX_LEGACY_SEGMENTS}；拒绝超过原生构造预算。"
        ));
    }
    let pitch_radius = (pitch_d / 2.0).max(2.0);
    let root_radius = pitch_radius - spec.tooth_height * 0.55;
    let tip_radius = pitch_radius + spec.tooth_height * 0.45;
    let inner_radius = if bore_d > 0.0 {
        (bore_d / 2.0).max(1.0)
    } else {
        (root_radius * 0.35).max(2.0)
    };
    if !root_radius.is_finite() || root_radius <= 0.0 || inner_radius >= root_radius {
        return Err("同步带轮中心孔半径必须小于有效齿根半径，保留连续轮缘材料。".into());
    }
    let tip_pitch = std::f64::consts::TAU * tip_radius / teeth as f64;
    if tip_pitch < spec.tooth_tip {
        return Err(format!(
            "同步带轮齿顶圆切向齿距 {tip_pitch:.6} mm 小于 {} 型齿顶宽 {:.6} mm；相邻齿会重叠。",
            resolved_type, spec.tooth_tip
        ));
    }
    Ok(
        json!({"tooth_type":resolved_type,"teeth":teeth,"pitch_radius_mm":pitch_radius,
        "root_radius_mm":root_radius,"tip_radius_mm":tip_radius,"inner_radius_mm":inner_radius,
        "tip_pitch_mm":tip_pitch,"legacy_segments":_segments}),
    )
}

/// Exact old-Quick T10 surface mesh compatibility path. It deliberately does
/// not create OCCT solids: the legacy raw mesh is a 64-segment blank plus
/// independently appended tooth lofts.
pub fn legacy_quick_t10_mesh(parameters: &Value) -> Result<TriangleMesh, String> {
    if parameters.get("tooth_type").and_then(Value::as_str) != Some("T10") {
        return Err("legacy timing mesh-only path is restricted to T10".into());
    }
    let pitch_d = number(parameters, "pitch_d")?;
    let height = number(parameters, "height")?;
    let teeth = parameters["teeth"]
        .as_u64()
        .filter(|count| (6..=MAX_TEETH).contains(count))
        .ok_or("legacy T10 teeth must be in 6..=512")? as usize;
    let bore_d = optional_number(parameters, "bore_d", 0.0)?;
    let center = point3(parameters.get("center"))?;
    if pitch_d <= 0.0 || height <= 0.0 || bore_d < 0.0 {
        return Err(
            "legacy T10 requires positive pitch diameter/width and nonnegative bore".into(),
        );
    }
    let (_, spec) = timing_type("T10");
    let pitch_radius = (pitch_d / 2.0).max(2.0);
    let root_radius = pitch_radius - spec.tooth_height * 0.55;
    let tip_radius = pitch_radius + spec.tooth_height * 0.45;
    let inner_radius = if bore_d > 0.0 {
        (bore_d / 2.0).max(1.0)
    } else {
        (root_radius * 0.35).max(2.0)
    };
    if root_radius <= 0.0 || inner_radius >= root_radius {
        return Err("legacy T10 bore must remain inside the tooth-root radius".into());
    }
    let half_tip = spec.tooth_tip / 2.0;
    let half_root = (spec.pitch / 2.0 - half_tip).max(half_tip + 0.2);
    let expected_triangles = 4usize
        .checked_mul(64)
        .and_then(|count| count.checked_mul(2))
        .and_then(|count| teeth.checked_mul(36).and_then(|n| count.checked_add(n)))
        .ok_or("legacy T10 triangle count overflow")?;
    if expected_triangles > 200_000 {
        return Err("legacy T10 mesh exceeds 200,000 triangles".into());
    }
    let mut mesh = TriangleMesh {
        positions: Vec::with_capacity(expected_triangles * 3),
        normals: Vec::with_capacity(expected_triangles * 3),
    };
    let body = [
        [inner_radius, -height / 2.0],
        [root_radius, -height / 2.0],
        [root_radius, height / 2.0],
        [inner_radius, height / 2.0],
    ];
    let body_interior = [center[0] + root_radius * 0.4, center[1], center[2]];
    for segment in 0..64 {
        let a0 = std::f64::consts::TAU * segment as f64 / 64.0;
        let a1 = std::f64::consts::TAU * (segment + 1) as f64 / 64.0;
        for edge in 0..body.len() {
            let [r0, z0] = body[edge];
            let [r1, z1] = body[(edge + 1) % body.len()];
            append_legacy_polygon(
                &mut mesh,
                vec![
                    timing_point(center, r0, z0, a0),
                    timing_point(center, r1, z1, a0),
                    timing_point(center, r1, z1, a1),
                    timing_point(center, r0, z0, a1),
                ],
                body_interior,
            );
        }
    }
    let local = [
        [pitch_radius - 1.0, -half_root],
        [root_radius, -half_root],
        [tip_radius, -half_tip],
        [tip_radius, half_tip],
        [root_radius, half_root],
        [pitch_radius - 1.0, half_root],
    ];
    let z_sections = [
        center[2] - height / 2.0,
        center[2],
        center[2] + height / 2.0,
    ];
    for tooth in 0..teeth {
        let angle = std::f64::consts::TAU * tooth as f64 / teeth as f64;
        let (sin, cos) = angle.sin_cos();
        let profile = local.map(|[radius, tangent]| {
            [
                center[0] + radius * cos - tangent * sin,
                center[1] + radius * sin + tangent * cos,
            ]
        });
        let sections = z_sections.map(|z| profile.map(|[x, y]| [x, y, z]));
        let all = sections.iter().flatten();
        let mut interior = [0.0; 3];
        for point in all {
            for axis in 0..3 {
                interior[axis] += point[axis] / 18.0;
            }
        }
        for span in 0..2 {
            for edge in 0..6 {
                let next = (edge + 1) % 6;
                append_legacy_polygon(
                    &mut mesh,
                    vec![
                        sections[span][edge],
                        sections[span][next],
                        sections[span + 1][next],
                        sections[span + 1][edge],
                    ],
                    interior,
                );
            }
        }
        for section_index in [0usize, 2usize] {
            let section = sections[section_index];
            let mut centroid = [0.0; 3];
            for point in section {
                for axis in 0..3 {
                    centroid[axis] += point[axis] / 6.0;
                }
            }
            for edge in 0..6 {
                append_legacy_polygon(
                    &mut mesh,
                    vec![centroid, section[edge], section[(edge + 1) % 6]],
                    interior,
                );
            }
        }
    }
    if mesh.positions.len() / 3 != expected_triangles {
        return Err(format!(
            "legacy T10 produced {} triangles; expected {expected_triangles}",
            mesh.positions.len() / 3
        ));
    }
    Ok(mesh)
}

fn timing_point(center: [f64; 3], radius: f64, z: f64, angle: f64) -> [f64; 3] {
    [
        center[0] + radius * angle.cos(),
        center[1] + radius * angle.sin(),
        center[2] + z,
    ]
}

fn append_legacy_polygon(mesh: &mut TriangleMesh, mut points: Vec<[f64; 3]>, interior: [f64; 3]) {
    if points.len() < 3 {
        return;
    }
    let mut normal = [0.0; 3];
    for index in 1..points.len() - 1 {
        normal = normalize(cross(
            sub(points[index], points[0]),
            sub(points[index + 1], points[0]),
        ));
        if length(normal) > 1e-9 {
            break;
        }
    }
    if length(normal) <= 1e-9 {
        return;
    }
    let mut centroid = [0.0; 3];
    for point in &points {
        for axis in 0..3 {
            centroid[axis] += point[axis] / points.len() as f64;
        }
    }
    if dot(normal, sub(centroid, interior)) < 0.0 {
        points.reverse();
        normal = normal.map(|value| -value);
    }
    for index in 1..points.len() - 1 {
        mesh.positions
            .extend([points[0], points[index], points[index + 1]]);
        mesh.normals.extend([normal; 3]);
    }
}

/// Admit only single-feature T10 plans that the B-Rep builder rejects.
pub fn is_mesh_only_legacy_quick_plan(plan: &Value) -> bool {
    if !matches!(
        plan.get("schema_version").and_then(Value::as_str),
        Some("1.0" | "1.1")
    ) || plan.pointer("/units/length").and_then(Value::as_str) != Some("mm")
        || plan.pointer("/units/angle").and_then(Value::as_str) != Some("deg")
    {
        return false;
    }
    let Some(features) = plan.get("features").and_then(Value::as_array) else {
        return false;
    };
    if features.len() != 1 {
        return false;
    }
    let feature = &features[0];
    if feature.get("kind").and_then(Value::as_str) != Some("timing_pulley")
        || feature.get("operation").and_then(Value::as_str) != Some("add")
        || feature.get("enabled").and_then(Value::as_bool) == Some(false)
        || feature
            .get("relations")
            .and_then(Value::as_array)
            .is_some_and(|v| !v.is_empty())
        || feature
            .pointer("/parameters/tooth_type")
            .and_then(Value::as_str)
            != Some("T10")
        || feature.pointer("/parameters/standard").is_some()
    {
        return false;
    }
    legacy_quick_t10_mesh(&feature["parameters"]).is_ok()
        && crate::cad_plan_validation::validate_plan(plan)["valid"] == true
}

/// Returns true when a parameter set is within the legacy T10 mesh builder's
/// bounded compatibility scope, without implying an editable B-Rep exists.
pub fn legacy_quick_t10_parameters_supported(parameters: &Value) -> bool {
    parameters.get("tooth_type").and_then(Value::as_str) == Some("T10")
        && legacy_quick_t10_mesh(parameters).is_ok()
}

/// Mirror the legacy part-library warning for T10 teeth whose tangential
/// pitch at the tooth-tip radius is narrower than the standard tooth tip.
pub fn legacy_t10_tooth_spacing_warning(parameters: &Value) -> Option<String> {
    if parameters.get("tooth_type").and_then(Value::as_str) != Some("T10") {
        return None;
    }
    let pitch_d = parameters.get("pitch_d")?.as_f64()?;
    let teeth = parameters.get("teeth")?.as_u64()?;
    if !pitch_d.is_finite() || pitch_d <= 0.0 || teeth == 0 {
        return None;
    }
    let (_, spec) = timing_type("T10");
    let effective_teeth = teeth.max(6);
    let tip_radius = pitch_d / 2.0 + spec.tooth_height * 0.45;
    let tip_spacing = std::f64::consts::TAU * tip_radius / effective_teeth as f64;
    if tip_spacing >= spec.tooth_tip {
        return None;
    }
    let minimum_pitch_diameter = effective_teeth as f64 * spec.tooth_tip / std::f64::consts::PI
        - 2.0 * spec.tooth_height * 0.45;
    let recommended_teeth = ((std::f64::consts::TAU * tip_radius / spec.tooth_tip).floor() as u64)
        .max(6);
    Some(format!(
        "{teeth} 齿 T10 型同步带轮：齿顶处的切向齿距 {tip_spacing:.2} mm 小于齿顶宽 {:.2} mm，相邻齿会互相重叠（输入自相矛盾，齿形不可用）。按 {teeth} 齿算节圆直径至少需 ⌀{:.2} mm；或按 ⌀{} mm 改用 {recommended_teeth} 齿。",
        spec.tooth_tip,
        pitch_d.max(minimum_pitch_diameter),
        pitch_d
    ))
}

pub fn build(kernel: &Kernel, parameters: &Value) -> Result<(Vec<Shape>, Value), String> {
    let checked = preflight(parameters)?;
    let pitch_d = number(parameters, "pitch_d")?;
    let height = number(parameters, "height")?;
    let teeth = parameters["teeth"]
        .as_u64()
        .ok_or("timing_pulley.teeth 必须是正整数。")? as usize;
    let raw_type = parameters["tooth_type"]
        .as_str()
        .ok_or("timing_pulley.tooth_type 必须是字符串。")?;
    let (_, spec) = timing_type(raw_type);
    let pitch_radius = checked["pitch_radius_mm"].as_f64().unwrap();
    let root_radius = checked["root_radius_mm"].as_f64().unwrap();
    let tip_radius = checked["tip_radius_mm"].as_f64().unwrap();
    let inner_radius = checked["inner_radius_mm"].as_f64().unwrap();
    let bore_d = optional_number(parameters, "bore_d", 0.0)?;
    let center = point3(parameters.get("center"))?;
    let half_tip = spec.tooth_tip / 2.0;
    let half_root = (spec.pitch / 2.0 - half_tip).max(half_tip + 0.2);

    let body_profile = [
        [inner_radius, -height / 2.0],
        [root_radius, -height / 2.0],
        [root_radius, height / 2.0],
        [inner_radius, height / 2.0],
    ];
    let body = kernel.revolve(&body_profile, 360.0, center, Axis::Z)?;
    let mut shapes = Vec::with_capacity(teeth + 1);
    shapes.push(body);
    let step = std::f64::consts::TAU / teeth as f64;
    let origin = [center[0], center[1], center[2] - height / 2.0];
    for index in 0..teeth {
        let angle = step * index as f64;
        let (sin, cos) = angle.sin_cos();
        let local = [
            [pitch_radius - 1.0, -half_root],
            [root_radius, -half_root],
            [tip_radius, -half_tip],
            [tip_radius, half_tip],
            [root_radius, half_root],
            [pitch_radius - 1.0, half_root],
        ];
        let profile = local
            .map(|[radius, tangent]| [radius * cos - tangent * sin, radius * sin + tangent * cos]);
        shapes.push(kernel.prism(&profile, height, origin, Axis::Z)?);
    }
    let refs: Vec<_> = shapes.iter().collect();
    let result = kernel.fuse(&refs)?;
    let analysis = result.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "timing_pulley 必须生成单一有效闭合实体；当前 valid={}, solids={}, closed={}",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    let known = checked["tooth_type"] == raw_type.trim().to_ascii_uppercase().replace('型', "");
    Ok((
        vec![result],
        json!({"status":"approximate","geometry_support":"approximate",
        "builder":"legacy_trapezoidal_teeth_with_analytic_annular_blank",
        "tooth_type":checked["tooth_type"],"teeth_built":teeth,"axis":"global Z",
        "pitch_d_mm":pitch_d,"height_mm":height,"bore_d_input_mm":bore_d,
        "inner_radius_mm":inner_radius,"segments_ignored_by_analytic_blank":true,
        "type_fallback_to_xl":!known,
        "warnings":["旧配方中的齿形本身是标准梯形近似；原生连续回转盘体替换了旧版分段 facet-BREP，体积会有有限差异。"]}),
    ))
}

fn number(parameters: &Value, key: &str) -> Result<f64, String> {
    let value = parameters
        .get(key)
        .and_then(Value::as_f64)
        .ok_or_else(|| format!("timing_pulley.{key} 必须是有限数值。"))?;
    if !value.is_finite() {
        return Err(format!("timing_pulley.{key} 必须是有限数值。"));
    }
    Ok(value)
}
fn optional_number(parameters: &Value, key: &str, default: f64) -> Result<f64, String> {
    if parameters.get(key).is_none_or(Value::is_null) {
        Ok(default)
    } else {
        number(parameters, key)
    }
}
fn optional_integer(parameters: &Value, key: &str, default: i64) -> Result<i64, String> {
    if parameters.get(key).is_none_or(Value::is_null) {
        return Ok(default);
    }
    parameters[key]
        .as_i64()
        .ok_or_else(|| format!("timing_pulley.{key} 必须是整数。"))
}
fn point3(value: Option<&Value>) -> Result<[f64; 3], String> {
    let Some(value) = value else {
        return Ok([0.0; 3]);
    };
    let values = value
        .as_array()
        .filter(|v| v.len() == 3)
        .ok_or("timing_pulley.center 必须包含三个有限XYZ数值。")?;
    let mut point = [0.0; 3];
    for (index, value) in values.iter().enumerate() {
        point[index] = value
            .as_f64()
            .filter(|v| v.is_finite())
            .ok_or("timing_pulley.center 必须包含三个有限XYZ数值。")?;
    }
    Ok(point)
}

fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
fn cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn length(value: [f64; 3]) -> f64 {
    dot(value, value).sqrt()
}
fn normalize(value: [f64; 3]) -> [f64; 3] {
    let magnitude = length(value);
    if magnitude <= f64::MIN_POSITIVE {
        [0.0; 3]
    } else {
        value.map(|component| component / magnitude)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parameters(tooth_type: &str) -> Value {
        json!({"pitch_d":80,"teeth":50,"height":10,"tooth_type":tooth_type,
            "bore_d":20,"center":[12,-4,7]})
    }

    #[test]
    fn timing_pulley_preflight_preserves_standard_dimensions_and_defaults() {
        let value = preflight(&parameters("XL型")).unwrap();
        assert_eq!(value["tooth_type"], "XL");
        assert_eq!(value["pitch_radius_mm"], 40.0);
        assert_eq!(value["root_radius_mm"], 39.3015);
        assert_eq!(value["tip_radius_mm"], 40.5715);
        assert_eq!(value["inner_radius_mm"], 10.0);
        let default_bore =
            preflight(&json!({"pitch_d":80,"teeth":50,"height":10,"tooth_type":"XL"})).unwrap();
        assert!(
            (default_bore["inner_radius_mm"].as_f64().unwrap() - 39.3015 * 0.35).abs() < 1e-12
        );
        assert_eq!(default_bore["legacy_segments"], 64);
    }

    #[test]
    fn timing_pulley_preflight_rejects_missing_invalid_and_unsafe_variants() {
        let mut p = parameters("XL");
        p.as_object_mut().unwrap().remove("teeth");
        assert!(preflight(&p).unwrap_err().contains("teeth"));
        for (key, value) in [
            ("pitch_d", json!(0)),
            ("height", json!(-1)),
            ("teeth", json!(5)),
            ("teeth", json!(513)),
            ("bore_d", json!(-1)),
            ("bore_d", json!(100)),
            ("center", json!([0, 0, "nan"])),
            ("segments", json!(1)),
        ] {
            let mut invalid = parameters("XL");
            invalid[key] = value;
            assert!(
                preflight(&invalid).is_err(),
                "accepted {key}={}",
                invalid[key]
            );
        }
        let mut overlap = parameters("XL");
        overlap["pitch_d"] = json!(20);
        overlap["teeth"] = json!(50);
        overlap["bore_d"] = json!(0);
        assert!(preflight(&overlap).unwrap_err().contains("齿顶圆切向齿距"));
        let mut unknown_type = parameters("XL");
        unknown_type["tooth_type"] = json!("CUSTOM");
        assert!(
            preflight(&unknown_type)
                .unwrap_err()
                .contains("14 个 legacy 标准型号")
        );
        for unsupported in ["L", "H", "XH", "XXH", "T10", "T20", "5M", "8M", "14M"] {
            let error = preflight(&parameters(unsupported)).unwrap_err();
            assert!(error.contains("没有材料重叠"), "{unsupported}: {error}");
        }
    }

    #[test]
    fn timing_pulley_known_types_and_legacy_unknown_fallback_are_explicit() {
        for name in [
            "MXL", "XL", "L", "H", "XH", "XXH", "T2.5", "T5", "T10", "T20", "3M", "5M", "8M", "14M",
        ] {
            let (_, timing) = timing_type(name);
            assert!(timing.pitch > 0.0 && timing.tooth_height > 0.0 && timing.tooth_tip > 0.0);
        }
        assert_eq!(timing_type("CUSTOM").0, "XL");
        assert_eq!(timing_type("XL").0, "XL");
    }
}
