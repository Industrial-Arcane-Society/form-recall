//! Native approximation of the legacy GB/T standard V-belt pulley revolve.
//! The groove dimensions use the retained std_pulley fallback table. This is
//! a single analytic OCCT revolve; spoke-style pulleys are rejected.
use crate::native_occt::{Axis, Kernel, TriangleMesh};
use serde_json::Value;

const MAX_GROOVES: u64 = 64;

const SERIES_Z: &[(f64, f64)] = &[
    (20., 23.2),
    (22.4, 25.6),
    (25., 28.2),
    (28., 31.2),
    (31.5, 34.7),
    (35.5, 38.7),
    (40., 43.2),
    (45., 48.2),
    (50., 54.),
    (56., 60.),
    (63., 67.),
    (71., 75.),
    (75., 79.),
    (80., 84.),
    (90., 94.),
    (100., 104.),
    (112., 116.),
    (125., 129.),
];
const SERIES_A: &[(f64, f64)] = &[
    (50., 54.),
    (56., 60.),
    (63., 67.),
    (71., 75.),
    (75., 80.5),
    (80., 85.5),
    (85., 90.5),
    (90., 95.5),
    (95., 100.5),
    (100., 105.5),
    (106., 111.5),
    (112., 117.5),
    (118., 123.5),
    (125., 130.5),
];
const SERIES_B: &[(f64, f64)] = &[
    (75., 80.5),
    (80., 85.5),
    (85., 90.5),
    (90., 95.5),
    (95., 100.5),
    (100., 105.5),
    (106., 111.5),
    (112., 117.5),
    (118., 123.5),
    (125., 132.),
];

#[derive(Clone, Copy)]
struct Groove {
    bd: f64,
    ha: f64,
    hf: f64,
    e: f64,
}

fn groove(kind: &str) -> Option<Groove> {
    match kind {
        "Y" => Some(Groove {
            bd: 5.3,
            ha: 1.6,
            hf: 4.7,
            e: 8.0,
        }),
        "Z" => Some(Groove {
            bd: 8.5,
            ha: 2.0,
            hf: 7.0,
            e: 12.0,
        }),
        "A" => Some(Groove {
            bd: 11.0,
            ha: 2.75,
            hf: 8.7,
            e: 15.0,
        }),
        "B" => Some(Groove {
            bd: 14.0,
            ha: 3.5,
            hf: 10.8,
            e: 19.0,
        }),
        "C" => Some(Groove {
            bd: 19.0,
            ha: 4.8,
            hf: 14.3,
            e: 25.5,
        }),
        "D" => Some(Groove {
            bd: 27.0,
            ha: 8.1,
            hf: 19.9,
            e: 37.0,
        }),
        "E" => Some(Groove {
            bd: 32.0,
            ha: 9.6,
            hf: 23.4,
            e: 44.5,
        }),
        _ => None,
    }
}

fn numeric(parameters: &Value, key: &str, default: Option<f64>) -> Result<f64, String> {
    let value = match parameters.get(key) {
        None | Some(Value::Null) => {
            default.ok_or_else(|| format!("vbelt_pulley 缺少数值参数 {key}。"))?
        }
        Some(value) => value
            .as_f64()
            .ok_or_else(|| format!("vbelt_pulley.{key} 必须是数值。"))?,
    };
    if !value.is_finite() {
        return Err(format!("vbelt_pulley.{key} 必须是有限数值。"));
    }
    Ok(value)
}

fn standard_type(parameters: &Value) -> Result<String, String> {
    let raw = parameters
        .get("groove_type")
        .and_then(Value::as_str)
        .ok_or("vbelt_pulley.groove_type 必须显式指定标准槽型 Y/Z/A/B/C/D/E。")?;
    let normalized = raw.trim().to_ascii_uppercase();
    let normalized = match normalized.as_str() {
        "SPZ" => "Z",
        "SPA" => "A",
        "SPB" => "B",
        "SPC" => "C",
        "Y型" => "Y",
        "Z型" => "Z",
        "A型" => "A",
        "B型" => "B",
        "C型" => "C",
        "D型" => "D",
        "E型" => "E",
        "窄V" | "窄V带" => "Z",
        "Y" | "Z" | "A" | "B" | "C" | "D" | "E" => normalized.as_str(),
        _ => {
            return Err(format!(
                "不支持 V 带槽型 {raw:?}；当前仅支持 Y/Z/A/B/C/D/E（含 SPZ/SPA/SPB/SPC 别名）。"
            ));
        }
    };
    Ok(normalized.to_owned())
}

fn angle(pitch_d: f64) -> f64 {
    if pitch_d <= 60. {
        32.
    } else if pitch_d <= 118. {
        34.
    } else if pitch_d <= 190. {
        36.
    } else {
        38.
    }
}

/// Return the legacy standard rim width and the width used by Quick geometry.
/// Quick compatibility intentionally follows the old Python profile layout:
/// its slot centers use the catalog spacing, compressed only enough to keep
/// the widest root inside the requested rim. This can make adjacent root
/// sections overlap; changing that spacing changes the legacy Quick shape.
pub fn width_requirements(
    pitch_d: f64,
    grooves: u64,
    groove_type: &str,
) -> Result<(f64, f64), String> {
    if !pitch_d.is_finite() || pitch_d <= 0. || grooves == 0 || grooves > MAX_GROOVES {
        return Err(format!(
            "V 带轮基准直径必须为正，槽数范围为 1..={MAX_GROOVES}。"
        ));
    }
    let kind = standard_type(&serde_json::json!({"groove_type":groove_type}))?;
    let g = groove(&kind).expect("validated standard groove type");
    let half_angle = angle(pitch_d).to_radians() / 2.;
    let tip_w = (g.bd - 2. * g.ha * half_angle.tan()).max(1.);
    let root_w = g.bd + 2. * g.hf * half_angle.tan();
    let legacy = (grooves - 1) as f64 * g.e + tip_w.max(root_w);
    // There is no second native-only width adjustment in Quick. Keep the
    // tuple for existing parser callers, but both values now identify the
    // same legacy-compatible minimum. CADPlan may still reject a profile if
    // OCCT cannot represent its overlapping legacy groove roots as one solid.
    Ok((legacy, legacy))
}

pub fn standard_outer_diameter(pitch_d: f64, groove_type: &str) -> Result<f64, String> {
    if !pitch_d.is_finite() || pitch_d <= 0.0 {
        return Err("V 带轮基准直径必须为有限正数。".into());
    }
    let kind = standard_type(&serde_json::json!({"groove_type":groove_type}))?;
    if let Some((_, outer_d)) = diameter_series(&kind)
        .iter()
        .find(|(standard_d, _)| (standard_d - pitch_d).abs() <= 1e-8)
    {
        return Ok(*outer_d);
    }
    let g = groove(&kind).expect("validated standard groove type");
    Ok(pitch_d + 2.0 * g.ha)
}

pub fn snap_standard_diameter(
    requested: f64,
    groove_type: &str,
) -> Result<(f64, Option<f64>, bool), String> {
    if !requested.is_finite() || requested <= 0.0 {
        return Err("V 带轮基准直径必须为有限正数。".into());
    }
    let kind = standard_type(&serde_json::json!({"groove_type":groove_type}))?;
    let series = diameter_series(&kind);
    let Some((pitch_d, outer_d)) = series
        .iter()
        .copied()
        .min_by(|a, b| (a.0 - requested).abs().total_cmp(&(b.0 - requested).abs()))
    else {
        return Ok((requested, None, false));
    };
    if (pitch_d - requested).abs() <= 2.0_f64.max(0.08 * requested) {
        Ok((pitch_d, Some(outer_d), (pitch_d - requested).abs() > 1e-9))
    } else {
        Ok((requested, None, false))
    }
}

fn diameter_series(kind: &str) -> &'static [(f64, f64)] {
    match kind {
        "Z" => SERIES_Z,
        "A" => SERIES_A,
        "B" => SERIES_B,
        _ => &[],
    }
}

fn standard_profile(parameters: &Value) -> Result<Vec<[f64; 2]>, String> {
    let pitch_d = numeric(parameters, "pitch_d", None)?;
    let width = numeric(parameters, "height", None)?;
    let grooves = parameters
        .get("grooves")
        .and_then(Value::as_u64)
        .ok_or("vbelt_pulley.grooves 必须是正整数。")?;
    if pitch_d <= 0. || width <= 0. || grooves == 0 || grooves > MAX_GROOVES {
        return Err(format!(
            "vbelt_pulley.pitch_d/height 必须为正，grooves 范围为 1..={MAX_GROOVES}。"
        ));
    }
    let kind = standard_type(parameters)?;
    if parameters
        .get("standard")
        .is_some_and(|value| value != &Value::Bool(true))
    {
        return Err("native 只支持标准槽型；standard=false 的旧圆柱开槽近似未迁移。".into());
    }
    let g = groove(&kind).unwrap();
    let outer_d = standard_outer_diameter(pitch_d, &kind)?;
    let phi = angle(pitch_d).to_radians();
    let tip_w = (g.bd - 2. * g.ha * (phi / 2.).tan()).max(1.);
    let root_w = g.bd + 2. * g.hf * (phi / 2.).tan();
    // Preserve the old Python Quick groove spacing and width clamp. When the
    // root width is larger than the catalog pitch, the old profile can overlap
    // adjacent root sections; the OCCT result must be checked instead of
    // silently widening/re-spacing the Quick geometry.
    let (_, min_width) = width_requirements(pitch_d, grooves, &kind)?;
    let pitch_e = g.e;
    if width + 1e-8 < min_width {
        return Err(format!(
            "输入轮宽 {width:.6} mm 无法容纳 {grooves} 个标准 V 槽；无自交剖面的最小轮宽为 {min_width:.6} mm。"
        ));
    }
    // Legacy spec silently clamps an oversized bore. Native CADPlan refuses
    // that correction so the requested dimension cannot be changed invisibly.
    let default_bore = (pitch_d * 0.22).max(8.);
    let bore = numeric(parameters, "bore_d", Some(0.))?;
    let bore = if bore == 0. { default_bore } else { bore };
    if bore <= 0. || bore >= pitch_d * 0.92 {
        return Err(
            "V 带轮中心孔必须为正且小于基准直径的 92%；native 执行不会静默缩小孔径。".into(),
        );
    }
    let hub_d = (1.95 * bore).min((bore + 6.).max(outer_d * 0.55));
    let hub_len = (1.8 * bore).min(width);
    let web_s = (0.6 * g.e).max(4.);
    let depth = g.ha + g.hf;
    let mut rim_id = outer_d - 2. * depth - 2. * 3f64.max(0.25 * depth);
    rim_id = rim_id.max(hub_d + 6.);
    if (outer_d - rim_id) / 2. > 0.35 * outer_d {
        rim_id = outer_d - 2. * (depth + 3.).max(0.12 * outer_d);
    }
    let spokes = if (rim_id - hub_d) / 2. > 45. && pitch_d >= 250. {
        (pitch_d / 60.).floor().clamp(4., 8.) as u64
    } else {
        0
    };
    if spokes > 0 {
        return Err("该直径/轮缘结构在 legacy 中需要独立辐条；native 仅支持无辐条回转体。".into());
    }

    if let Some(value) = parameters.get("outer_d").filter(|value| !value.is_null()) {
        let requested_outer = value.as_f64().ok_or("vbelt_pulley.outer_d 必须是数值。")?;
        if !requested_outer.is_finite() {
            return Err("vbelt_pulley.outer_d 必须是有限数值。".into());
        }
        if (requested_outer - outer_d).abs() > 1e-8 {
            return Err(format!(
                "outer_d 外径与标准槽型或直径系列推导值不一致：请求 {requested_outer:.6} mm，标准结果 {outer_d:.6} mm。"
            ));
        }
    }
    if let Some(value) = parameters
        .get("groove_depth")
        .filter(|value| !value.is_null())
    {
        let depth_override = value
            .as_f64()
            .ok_or("vbelt_pulley.groove_depth 必须是数值。")?;
        if !depth_override.is_finite() {
            return Err("vbelt_pulley.groove_depth 必须是有限数值。".into());
        }
        if (depth_override - depth).abs() > 1e-8 {
            return Err(format!(
                "groove_depth 在标准槽模式下必须等于 ha+hf={depth:.6} mm。"
            ));
        }
    }
    let root_r = pitch_d / 2. - g.hf;
    let bore_r = (bore / 2.).max(1.);
    let hub_r = (hub_d / 2.).max(bore_r + 1.);
    let rim_inner_r = (rim_id / 2.).max(hub_r + 1.);
    let mut root_r = root_r;
    if root_r <= rim_inner_r + 1. {
        root_r = (rim_inner_r + 1.).max(pitch_d / 2. - g.hf * 0.75);
    }
    let outer_r = (outer_d / 2.).max(root_r + 0.5);
    let half_width = width / 2.;
    let hub_half = hub_len / 2.;
    let web_half = web_s / 2.;
    let base = (hub_half - web_s / 2.).max(0.).min(hub_half);
    let tip_half = tip_w / 2.;
    let root_half = root_w / 2.;
    let max_half = half_width - root_half - 0.2;
    let need_half = (grooves - 1) as f64 * pitch_e * 0.5;
    let e_eff = if need_half > max_half && need_half > 1e-9 {
        if grooves > 1 {
            (2. * max_half / (grooves - 1) as f64).max(0.)
        } else {
            pitch_e
        }
    } else {
        pitch_e
    };
    let right_start = (grooves - 1) as f64 * e_eff * 0.5;
    let mut profile = vec![[bore_r, -hub_half], [hub_r, -hub_half]];
    if base > -hub_half + 1e-6 {
        profile.extend([[hub_r, -base], [rim_inner_r, -base]]);
    }
    profile.extend([[rim_inner_r, -half_width], [outer_r, -half_width]]);
    for i in 0..grooves {
        let c = -right_start + i as f64 * e_eff;
        profile.extend([
            [outer_r, c - tip_half],
            [root_r, c - root_half],
            [root_r, c + root_half],
            [outer_r, c + tip_half],
        ]);
    }
    profile.extend([[outer_r, half_width], [rim_inner_r, half_width]]);
    if base > -hub_half + 1e-6 {
        profile.extend([[rim_inner_r, base], [hub_r, base]]);
    }
    profile.extend([[hub_r, hub_half], [bore_r, hub_half]]);
    profile.dedup_by(|a, b| (a[0] - b[0]).abs() <= 1e-9 && (a[1] - b[1]).abs() <= 1e-9);
    if profile.len() > 2
        && (profile[0][0] - profile[profile.len() - 1][0]).abs() < 1e-9
        && (profile[0][1] - profile[profile.len() - 1][1]).abs() < 1e-9
    {
        profile.pop();
    }
    let _ = web_half; // documents the legacy web thickness relationship
    Ok(profile)
}

/// Generate the legacy Quick surface mesh without trying to sew the
/// self-intersecting axial profile into a B-Rep. This intentionally mirrors
/// `kernel.make_revolve(..., seg=64)`: each profile edge is swept separately,
/// oriented around the same interior reference point, and a quad is fan
/// triangulated for export. It is only appropriate for Quick mesh-only output;
/// callers must not report a closed solid or CATPart.
pub fn legacy_quick_mesh(parameters: &Value, segments: usize) -> Result<TriangleMesh, String> {
    if !(3..=1024).contains(&segments) {
        return Err("legacy V 带轮网格分段必须在 3..=1024 范围内。".into());
    }
    let profile = standard_profile(parameters)?;
    preflight(parameters)?;
    let center = parameters
        .get("center")
        .and_then(Value::as_array)
        .map(|values| {
            [
                values[0].as_f64().unwrap_or(f64::NAN),
                values[1].as_f64().unwrap_or(f64::NAN),
                values[2].as_f64().unwrap_or(f64::NAN),
            ]
        })
        .unwrap_or([0.0; 3]);
    let z_min = profile
        .iter()
        .map(|point| point[1])
        .fold(f64::INFINITY, f64::min);
    let z_max = profile
        .iter()
        .map(|point| point[1])
        .fold(f64::NEG_INFINITY, f64::max);
    let r_max = profile
        .iter()
        .map(|point| point[0])
        .fold(f64::NEG_INFINITY, f64::max);
    let interior = [
        center[0] + r_max * 0.4,
        center[1],
        center[2] + (z_min + z_max) * 0.5,
    ];
    let point_at = |radius: f64, z: f64, angle: f64| {
        [
            center[0] + radius * angle.cos(),
            center[1] + radius * angle.sin(),
            center[2] + z,
        ]
    };
    let mut mesh = TriangleMesh {
        positions: Vec::new(),
        normals: Vec::new(),
    };
    let expected_triangles = profile
        .len()
        .checked_mul(segments)
        .and_then(|quads| quads.checked_mul(2))
        .ok_or("legacy V 带轮三角面数量溢出。")?;
    if expected_triangles > 2_000_000 {
        return Err("legacy V 带轮三角面超过 200 万面限制。".into());
    }
    mesh.positions.reserve(expected_triangles * 3);
    mesh.normals.reserve(expected_triangles * 3);
    let two_pi = std::f64::consts::TAU;
    for segment in 0..segments {
        let angle0 = two_pi * segment as f64 / segments as f64;
        let angle1 = two_pi * (segment + 1) as f64 / segments as f64;
        for edge in 0..profile.len() {
            let [r0, z0] = profile[edge];
            let [r1, z1] = profile[(edge + 1) % profile.len()];
            let mut quad = [
                point_at(r0, z0, angle0),
                point_at(r1, z1, angle0),
                point_at(r1, z1, angle1),
                point_at(r0, z0, angle1),
            ];
            let mut normal = normalize(cross(sub(quad[1], quad[0]), sub(quad[2], quad[0])));
            if length(normal) < 1e-9 {
                continue;
            }
            let centroid = [
                quad.iter().map(|point| point[0]).sum::<f64>() * 0.25,
                quad.iter().map(|point| point[1]).sum::<f64>() * 0.25,
                quad.iter().map(|point| point[2]).sum::<f64>() * 0.25,
            ];
            if dot(normal, sub(centroid, interior)) < 0.0 {
                quad.reverse();
                normal = normal.map(|value| -value);
            }
            for indices in [[0usize, 1, 2], [0, 2, 3]] {
                let triangle = indices.map(|index| quad[index]);
                if length(cross(
                    sub(triangle[1], triangle[0]),
                    sub(triangle[2], triangle[0]),
                )) < 1e-12
                {
                    continue;
                }
                mesh.positions.extend(triangle);
                mesh.normals.extend([normal; 3]);
            }
        }
    }
    if mesh.positions.is_empty() {
        return Err("legacy V 带轮回转网格没有有效三角面。".into());
    }
    Ok(mesh)
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

pub fn preflight(parameters: &Value) -> Result<(), String> {
    let profile = standard_profile(parameters)?;
    if profile.len() < 8 {
        return Err("V 带轮标准剖面点数不足。".into());
    }
    if let Some(axis) = parameters.get("axis") {
        if axis
            .as_str()
            .is_none_or(|axis| !axis.eq_ignore_ascii_case("Z"))
        {
            return Err("native 标准 V 带轮只支持绕 Z 轴回转。".into());
        }
    }
    if let Some(base) = parameters.get("base") {
        if base.as_bool().is_none_or(|base| base) {
            return Err("标准 V 带轮只支持 base=false 或省略；不支持偏置语义。".into());
        }
    }
    if let Some(center) = parameters.get("center") {
        let values = center
            .as_array()
            .filter(|values| values.len() == 3)
            .ok_or("vbelt_pulley.center 必须是有限 XYZ 三元组。")?;
        if values
            .iter()
            .any(|value| value.as_f64().is_none_or(|v| !v.is_finite()))
        {
            return Err("vbelt_pulley.center 必须是有限 XYZ 三元组。".into());
        }
    }
    Ok(())
}

/// Narrow eligibility gate for the Quick-only mesh fallback. The shared
/// CADPlan validator remains authoritative for every other plan and for every
/// other validation error.
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
    if feature.get("kind").and_then(Value::as_str) != Some("vbelt_pulley")
        || feature.get("operation").and_then(Value::as_str) != Some("add")
        || feature.get("enabled").and_then(Value::as_bool) == Some(false)
        || feature
            .get("relations")
            .and_then(Value::as_array)
            .is_some_and(|relations| !relations.is_empty())
        || feature
            .pointer("/parameters/standard")
            .is_some_and(|standard| standard != &Value::Bool(true))
    {
        return false;
    }
    let validation = crate::cad_plan_validation::validate_plan(plan);
    let validation_is_expected = validation.get("valid").and_then(Value::as_bool) == Some(true)
        || validation
            .get("errors")
            .and_then(Value::as_array)
            .is_some_and(|errors| {
                validation.get("valid").and_then(Value::as_bool) == Some(false)
                    && errors.len() == 1
                    && errors[0].get("code").and_then(Value::as_str)
                        == Some("vbelt_width_below_profile_minimum")
            });
    if !validation_is_expected || standard_profile(&feature["parameters"]).is_err() {
        return false;
    }
    let parameters = &feature["parameters"];
    let Some(grooves) = parameters.get("grooves").and_then(Value::as_u64) else {
        return false;
    };
    if grooves < 2 {
        return false;
    }
    let Some(pitch_d) = parameters.get("pitch_d").and_then(Value::as_f64) else {
        return false;
    };
    let Some(height) = parameters.get("height").and_then(Value::as_f64) else {
        return false;
    };
    let Ok(kind) = standard_type(parameters) else {
        return false;
    };
    let Some(groove) = groove(&kind) else {
        return false;
    };
    let root_width = groove.bd + 2.0 * groove.hf * (angle(pitch_d).to_radians() / 2.0).tan();
    let max_half = height / 2.0 - root_width / 2.0 - 0.2;
    let effective_pitch = if (grooves - 1) as f64 * groove.e * 0.5 > max_half {
        (2.0 * max_half / (grooves - 1) as f64).max(0.0)
    } else {
        groove.e
    };
    effective_pitch + 1e-8 < root_width
}

pub fn build(
    kernel: &Kernel,
    parameters: &Value,
) -> Result<(Vec<crate::native_occt::Shape>, Value), String> {
    let profile = standard_profile(parameters)?;
    preflight(parameters)?;
    let center = parameters
        .get("center")
        .and_then(Value::as_array)
        .map(|v| {
            [
                v[0].as_f64().unwrap_or(f64::NAN),
                v[1].as_f64().unwrap_or(f64::NAN),
                v[2].as_f64().unwrap_or(f64::NAN),
            ]
        })
        .unwrap_or([0., 0., 0.]);
    let shape = kernel.revolve(&profile, 360., center, Axis::Z)?;
    Ok((
        vec![shape],
        serde_json::json!({
            "status":"approximate",
            "geometry_support":"approximate",
            "builder":"legacy_standard_vbelt_axial_profile_analytic_revolve",
            "axis":"Z",
            "center_mm":center,
            "profile_points":profile.len(),
            "baseline_comparison":"Reproduces the retained std_pulley fallback-table profile; the engineering design and CATIA mapping remain approximate.",
            "warnings":["Fallback standard groove table only; no KB diameter-series snapping.","Spoke-style construction is unsupported and rejected before OCCT."]
        }),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn sample() -> Value {
        json!({"pitch_d":80,"grooves":3,"height":55,"groove_type":"A","bore_d":12,"center":[3,-4,5]})
    }
    #[test]
    fn standard_profile_preserves_legacy_a_three_groove_dimensions() {
        let p = standard_profile(&sample()).unwrap();
        assert_eq!(p.len(), 24);
        assert_eq!(p[0], [6., -10.8]);
        assert!((p[2][0] - 11.7).abs() < 1e-12);
        assert!((p[2][1] + 6.3).abs() < 1e-12);
        assert!(p.iter().all(|point| point.iter().all(|v| v.is_finite())));
    }

    #[test]
    fn quick_compatibility_keeps_legacy_three_groove_width_and_effective_centers() {
        let (legacy_width, quick_width) = width_requirements(100.0, 3, "A").unwrap();
        let expected_width = 46.319_713_857_380_69;
        assert!((legacy_width - expected_width).abs() < 1e-10);
        assert!((quick_width - expected_width).abs() < 1e-10);

        let plan = serde_json::json!({
            "pitch_d": 100.0,
            "grooves": 3,
            "height": expected_width,
            "groove_type": "A",
            "outer_d": 105.5,
            "bore_d": 25.0,
            "center": [0.0, 0.0, 0.0]
        });
        let profile = standard_profile(&plan).unwrap();
        // These root endpoints are taken from the legacy Python std_pulley.profile
        // for the same input. Their midpoints are the effective groove centers
        // after its 0.2 mm end-margin clamp (e=14.7998569 mm).
        let root_centers = [
            (profile[7][1] + profile[8][1]) * 0.5,
            (profile[11][1] + profile[12][1]) * 0.5,
            (profile[15][1] + profile[16][1]) * 0.5,
        ];
        let expected_centers = [-14.799_856_928_690_346, 0.0, 14.799_856_928_690_346];
        for (actual, expected) in root_centers.into_iter().zip(expected_centers) {
            assert!((actual - expected).abs() < 1e-10);
        }
    }

    #[test]
    fn legacy_mesh_only_revolve_matches_old_quick_face_count() {
        let plan = serde_json::json!({
            "schema_version":"1.1",
            "units":{"length":"mm","angle":"deg"},
            "features":[{
                "id":"quick-0001",
                "kind":"vbelt_pulley",
                "operation":"add",
                "enabled":true,
                "relations":[],
                "parameters":{
                    "pitch_d":100.0,"grooves":3,"height":46.31971385738069,
                    "groove_type":"A","outer_d":105.5,"bore_d":25.0,"center":[0,0,0]
                }
            }]
        });
        let mesh = legacy_quick_mesh(&plan["features"][0]["parameters"], 64).unwrap();
        let profile = standard_profile(&plan["features"][0]["parameters"]).unwrap();
        assert_eq!(mesh.positions.len() / 3, profile.len() * 64 * 2);
        assert_eq!(mesh.positions.len(), mesh.normals.len());
        assert!(
            mesh.positions
                .iter()
                .flatten()
                .all(|value| value.is_finite())
        );
    }

    #[test]
    fn rejects_width_bore_and_spoked_variant_instead_of_changing_requested_geometry() {
        let mut p = sample();
        p["height"] = json!(40.);
        assert!(standard_profile(&p).unwrap_err().contains("最小轮宽"));
        let mut p = sample();
        p["bore_d"] = json!(80.);
        assert!(standard_profile(&p).unwrap_err().contains("不会静默缩小"));
        let mut p = sample();
        p["pitch_d"] = json!(300.);
        p["groove_type"] = json!("C");
        p["bore_d"] = json!(8.);
        p["height"] = json!(180.);
        assert!(standard_profile(&p).unwrap_err().contains("辐条"));
    }
    #[test]
    fn rejects_unknown_type_and_mismatched_overrides() {
        let mut p = sample();
        p["groove_type"] = json!("X");
        assert!(
            standard_profile(&p)
                .unwrap_err()
                .contains("不支持 V 带槽型")
        );
        let mut p = sample();
        p["outer_d"] = json!(100.);
        assert!(standard_profile(&p).unwrap_err().contains("外径"));
        let mut p = sample();
        p["standard"] = json!(false);
        assert!(standard_profile(&p).unwrap_err().contains("standard=false"));
        let mut p = sample();
        p["axis"] = json!("X");
        assert!(preflight(&p).unwrap_err().contains("Z 轴"));
    }
}
