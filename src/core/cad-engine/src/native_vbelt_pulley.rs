//! Native approximation of the legacy GB/T standard V-belt pulley revolve.
//! The groove dimensions use the retained std_pulley fallback table. This is
//! an analytic OCCT revolve; large legacy spoke layouts use a dedicated
//! blank/cutter/fusion path that keeps their original box placement.
use crate::native_occt::{Axis, Kernel, Shape, TriangleMesh};
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

#[derive(Debug, Clone, Copy)]
struct SpokeLayout {
    count: u64,
    radial_length: f64,
    width: f64,
    thickness: f64,
    radial_midpoint: f64,
    z_start: f64,
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

fn standard_profile_and_spokes(
    parameters: &Value,
) -> Result<(Vec<[f64; 2]>, Option<SpokeLayout>), String> {
    standard_profile_and_spokes_with_width_policy(parameters, false)
}

fn standard_profile_and_spokes_with_width_policy(
    parameters: &Value,
    preserve_overlapping_legacy_profile: bool,
) -> Result<(Vec<[f64; 2]>, Option<SpokeLayout>), String> {
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
    let legacy_overlap_allows_requested_width = preserve_overlapping_legacy_profile
        && grooves > 1
        && groove_root_overlap(parameters)?.2 > 1e-8;
    if width + 1e-8 < min_width && !legacy_overlap_allows_requested_width {
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
    let spoke_layout = (spokes > 0).then(|| {
        let hub_r = hub_d / 2.0;
        let rim_r = outer_d / 2.0;
        let radial_length = rim_r - hub_r;
        SpokeLayout {
            count: spokes,
            radial_length,
            width: (radial_length * 0.28).max(6.0),
            thickness: web_s,
            radial_midpoint: (hub_r + rim_r) / 2.0,
            z_start: (width / 2.0 - web_s).max(0.0).min(width / 2.0) - web_s / 2.0,
        }
    });
    Ok((profile, spoke_layout))
}

fn standard_profile(parameters: &Value) -> Result<Vec<[f64; 2]>, String> {
    standard_profile_and_spokes(parameters).map(|(profile, _)| profile)
}

pub fn spokes_required(parameters: &Value) -> Result<bool, String> {
    standard_profile_and_spokes(parameters).map(|(_, spokes)| spokes.is_some())
}

fn groove_root_overlap(parameters: &Value) -> Result<(f64, f64, f64), String> {
    let pitch_d = numeric(parameters, "pitch_d", None)?;
    let height = numeric(parameters, "height", None)?;
    let grooves = parameters["grooves"]
        .as_u64()
        .ok_or("vbelt_pulley.grooves 必须是正整数。")?;
    let kind = standard_type(parameters)?;
    let groove = groove(&kind).ok_or("不支持 V 带槽型。")?;
    let root_width = groove.bd + 2.0 * groove.hf * (angle(pitch_d).to_radians() / 2.0).tan();
    if grooves == 1 {
        return Ok((root_width, groove.e, 0.0));
    }
    let max_half = height / 2.0 - root_width / 2.0 - 0.2;
    let need_half = (grooves - 1) as f64 * groove.e * 0.5;
    let effective_pitch = if need_half > max_half && need_half > 1e-9 {
        (2.0 * max_half / (grooves - 1) as f64).max(0.0)
    } else {
        groove.e
    };
    Ok((
        root_width,
        effective_pitch,
        (root_width - effective_pitch).max(0.0),
    ))
}

pub fn legacy_mesh_geometry_details(parameters: &Value) -> Result<Value, String> {
    let (profile, layout) = standard_profile_and_spokes_with_width_policy(parameters, true)?;
    let (root_width, effective_pitch, overlap) = groove_root_overlap(parameters)?;
    let spokes = layout.map(|layout| {
        let angles = (0..layout.count)
            .map(|index| 360.0 * index as f64 / layout.count as f64)
            .collect::<Vec<_>>();
        serde_json::json!({
            "count":layout.count,
            "box_size_mm":[layout.radial_length,layout.width,layout.thickness],
            "radial_start_mm":layout.radial_midpoint-layout.radial_length/2.0,
            "radial_end_mm":layout.radial_midpoint+layout.radial_length/2.0,
            "z_start_mm":layout.z_start,
            "z_end_mm":layout.z_start+layout.thickness,
            "angles_deg":angles
        })
    });
    Ok(serde_json::json!({
        "profile_points":profile.len(),
        "groove_root_width_mm":root_width,
        "groove_effective_pitch_mm":effective_pitch,
        "groove_root_overlap_mm":overlap,
        "legacy_profile_self_intersecting":overlap > 1e-8,
        "spokes":spokes
    }))
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
    let (profile, spokes) = standard_profile_and_spokes_with_width_policy(parameters, true)?;
    preflight_with_width_policy(parameters, true)?;
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
    if let Some(layout) = spokes {
        append_legacy_box_spokes(&mut mesh, center, layout);
    }
    Ok(mesh)
}

fn append_legacy_box_spokes(mesh: &mut TriangleMesh, center: [f64; 3], layout: SpokeLayout) {
    let half_length = layout.radial_length / 2.0;
    let half_width = layout.width / 2.0;
    let z0 = layout.z_start;
    let z1 = z0 + layout.thickness;
    let box_center = [
        layout.radial_midpoint,
        0.0,
        z0 + layout.thickness / 2.0,
    ];
    let profile = [
        [-half_length, -half_width],
        [half_length, -half_width],
        [half_length, half_width],
        [-half_length, half_width],
    ];
    for spoke in 0..layout.count {
        let degrees = 360.0 * spoke as f64 / layout.count as f64;
        let angle = degrees.to_radians();
        let (sin, cos) = angle.sin_cos();
        let point = |x: f64, y: f64, z: f64| {
            [layout.radial_midpoint + x, y, z]
        };
        let bottom = profile.map(|[x, y]| point(x, y, z0));
        let top = profile.map(|[x, y]| point(x, y, z1));

        // Match `extrude_profile`: four side quads, then the two ear-clipped
        // end faces from `triangulate_2d` for the CCW rectangle.
        for edge in 0..4 {
            let next = (edge + 1) % 4;
            append_legacy_box_face(
                mesh,
                vec![bottom[edge], bottom[next], top[next], top[edge]],
                box_center,
                sin,
                cos,
                center,
            );
        }
        for indices in [[3usize, 0, 1], [1, 2, 3]] {
            append_legacy_box_face(
                mesh,
                indices.iter().map(|index| top[*index]).collect(),
                box_center,
                sin,
                cos,
                center,
            );
            append_legacy_box_face(
                mesh,
                indices.iter().map(|index| bottom[*index]).collect(),
                box_center,
                sin,
                cos,
                center,
            );
        }
    }
}

fn append_legacy_box_face(
    mesh: &mut TriangleMesh,
    mut points: Vec<[f64; 3]>,
    box_center: [f64; 3],
    sin: f64,
    cos: f64,
    center: [f64; 3],
) {
    if points.len() < 3 {
        return;
    }
    let normal = normalize(cross(sub(points[1], points[0]), sub(points[2], points[0])));
    if length(normal) < 1e-9 {
        return;
    }
    let centroid = [
        points.iter().map(|point| point[0]).sum::<f64>() / points.len() as f64,
        points.iter().map(|point| point[1]).sum::<f64>() / points.len() as f64,
        points.iter().map(|point| point[2]).sum::<f64>() / points.len() as f64,
    ];
    if dot(normal, sub(centroid, box_center)) < 0.0 {
        points.reverse();
    }
    let transform = |point: [f64; 3]| {
        [
            cos * point[0] - sin * point[1] + center[0],
            sin * point[0] + cos * point[1] + center[1],
            point[2] + center[2],
        ]
    };
    let origin = transform(points[0]);
    for index in 1..points.len() - 1 {
        let a = origin;
        let b = transform(points[index]);
        let c = transform(points[index + 1]);
        let triangle_normal = normalize(cross(sub(b, a), sub(c, a)));
        if length(triangle_normal) < 1e-12 {
            continue;
        }
        mesh.positions.extend([a, b, c]);
        mesh.normals.extend([triangle_normal; 3]);
    }
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
    preflight_with_width_policy(parameters, false)
}

fn preflight_with_width_policy(
    parameters: &Value,
    preserve_overlapping_legacy_profile: bool,
) -> Result<(), String> {
    let (profile, _) = standard_profile_and_spokes_with_width_policy(
        parameters,
        preserve_overlapping_legacy_profile,
    )?;
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
    // Mesh-only remains the compatibility path for self-intersecting legacy
    // profiles. The exact Quick triangle builder also appends old box spokes;
    // non-overlapping spoked variants can use the dedicated B-Rep builder.
    let legacy_width_failure = validation.get("valid").and_then(Value::as_bool) == Some(false)
        && validation
            .get("errors")
            .and_then(Value::as_array)
            .is_some_and(|errors| {
                errors.len() == 1
                    && errors[0].get("code").and_then(Value::as_str)
                        == Some("vbelt_width_below_profile_minimum")
            });
    let valid_or_legacy_width = validation.get("valid").and_then(Value::as_bool) == Some(true)
        || legacy_width_failure;
    if !valid_or_legacy_width
        || preflight_with_width_policy(&feature["parameters"], legacy_width_failure).is_err()
    {
        return false;
    }
    let parameters = &feature["parameters"];
    let Ok((_, _, overlap)) = groove_root_overlap(parameters) else {
        return false;
    };
    overlap > 1e-8
}

pub fn build(
    kernel: &Kernel,
    parameters: &Value,
) -> Result<(Vec<crate::native_occt::Shape>, Value), String> {
    let (profile, spokes) = standard_profile_and_spokes(parameters)?;
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
    if let Some(layout) = spokes {
        let (shape, details) = build_spoked_vbelt(kernel, parameters, &profile, center, layout)?;
        return Ok((vec![shape], details));
    }
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
            "warnings":["Fallback standard groove table only; no KB diameter-series snapping."]
        }),
    ))
}

fn build_spoked_vbelt(
    kernel: &Kernel,
    parameters: &Value,
    profile: &[[f64; 2]],
    center: [f64; 3],
    layout: SpokeLayout,
) -> Result<(Shape, Value), String> {
    let grooves = parameters["grooves"]
        .as_u64()
        .ok_or("vbelt_pulley.grooves 必须是正整数。")? as usize;
    let groove_start = 6usize;
    let groove_end = groove_start
        .checked_add(grooves.checked_mul(4).ok_or("V 带轮槽数量溢出。")?)
        .ok_or("V 带轮槽剖面索引溢出。")?;
    if groove_end >= profile.len() || groove_start >= profile.len() {
        return Err("V 带轮旧剖面槽边界与槽数不匹配。".into());
    }

    // Remove the intersecting groove edges from the axial outline and create a
    // simple blank. Each old V groove is then represented by a revolved cutter;
    // the cutters are fused as a union before cutting the blank. This preserves
    // the old overlapping groove-root spacing without silently moving or
    // widening grooves merely to make the original self-intersecting wire valid.
    let mut blank_profile = profile[..groove_start].to_vec();
    blank_profile.extend_from_slice(&profile[groove_end..]);
    let blank = kernel.revolve(&blank_profile, 360.0, center, Axis::Z)?;
    let blank_analysis = blank.analyze()?;

    let outer_r = profile[groove_start][0];
    let extension = 1.0;
    let mut groove_tools = Vec::with_capacity(grooves);
    for index in 0..grooves {
        let point = groove_start + index * 4;
        let [[_, z0], [root_r0, z1], [root_r1, z2], [_, z3]] = [
            profile[point],
            profile[point + 1],
            profile[point + 2],
            profile[point + 3],
        ];
        if (root_r0 - root_r1).abs() > 1e-9 || root_r0 <= 0.0 {
            return Err("V 带轮槽根剖面不是可构造的正半径平底槽。".into());
        }
        let cutter_profile = [
            [outer_r + extension, z0],
            [root_r0, z1],
            [root_r1, z2],
            [outer_r + extension, z3],
        ];
        groove_tools.push(kernel.revolve(&cutter_profile, 360.0, center, Axis::Z)?);
    }
    let cutter_refs = groove_tools.iter().collect::<Vec<_>>();
    let groove_union = kernel.fuse(&cutter_refs)?;
    let cutter_analysis = groove_union.analyze()?;
    let cut_body = kernel.cut(&blank, &[&groove_union])?;
    let cut_analysis = cut_body.analyze()?;

    let angles = (0..layout.count)
        .map(|index| 360.0 * index as f64 / layout.count as f64)
        .collect::<Vec<_>>();
    let mut result = cut_body;
    let mut fuse_stages = Vec::with_capacity(layout.count as usize);
    for angle in &angles {
        let bar = kernel.make_box(
            [layout.radial_length, layout.width, layout.thickness],
            [
                center[0] + layout.radial_midpoint,
                center[1],
                center[2] + layout.z_start + layout.thickness / 2.0,
            ],
        )?;
        let matrix = rotation_about_z(center, *angle)?;
        let rotated = kernel.transform(&bar, matrix)?;
        result = kernel.fuse(&[&result, &rotated])?;
        let stage = result.analyze()?;
        fuse_stages.push(serde_json::json!({
            "angle_deg":angle,
            "valid":stage.valid == 1,
            "solid_count":stage.solid_count,
            "closed_solid_count":stage.closed_solid_count
        }));
    }
    let analysis = result.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "V 带轮槽根并集及 {} 根旧布局辐条未形成单一有效闭合实体；母体 valid={}, solids={}, closed={}；刀具并集 valid={}, solids={}, closed={}；切槽后 valid={}, solids={}, closed={}；最终 valid={}, solids={}, closed={}，逐根融合={fuse_stages:?}",
            layout.count,
            blank_analysis.valid, blank_analysis.solid_count, blank_analysis.closed_solid_count,
            cutter_analysis.valid, cutter_analysis.solid_count, cutter_analysis.closed_solid_count,
            cut_analysis.valid, cut_analysis.solid_count, cut_analysis.closed_solid_count,
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }

    let (root_width, effective_pitch, overlap) = groove_root_overlap(parameters)?;
    let root_warning = if overlap > 1e-8 {
        "旧 V 带轮的相邻槽根区间重叠；本地闭合实体以槽刀具体积并集处理重叠，不等同于旧逐边网格拼接。".to_string()
    } else {
        "该槽数没有相邻槽根重叠；本地几何按旧轴向布局构建，不等同于旧逐边网格拼接。".to_string()
    };
    let spoke_warning = format!(
        "轮缘内环形辐板和旧布局的 {} 根箱体辐条轴向位置按旧配方保留；此项为受限几何映射，不宣称原始网格逐面 parity。",
        layout.count
    );
    Ok((
        result,
        serde_json::json!({
            "status":"approximate",
            "geometry_support":"restricted_legacy_geometry_mapping",
            "legacy_raw_parity":false,
            "builder":"legacy_axial_blank_fused_v_groove_cutters_and_rotated_boxes",
            "axis":"Z",
            "center_mm":center,
            "profile_points":profile.len(),
            "groove_root_width_mm":root_width,
            "groove_effective_pitch_mm":effective_pitch,
            "hub_diameter_mm":profile[1][0] * 2.0,
            "hub_length_mm":profile[1][1].abs() * 2.0,
            "rim_inner_diameter_mm":profile[3][0] * 2.0,
            "web_thickness_mm":layout.thickness,
            "spokes":{
                "count":layout.count,
                "box_size_mm":[layout.radial_length,layout.width,layout.thickness],
                "radial_start_mm":layout.radial_midpoint-layout.radial_length/2.0,
                "radial_end_mm":layout.radial_midpoint+layout.radial_length/2.0,
                "z_start_mm":layout.z_start,
                "z_end_mm":layout.z_start+layout.thickness,
                "angles_deg":angles
            },
            "groove_root_overlap_mm":overlap,
            "legacy_profile_self_intersecting":overlap > 1e-8,
            "solid_count":analysis.solid_count,
            "closed_solid_count":analysis.closed_solid_count,
            "valid":analysis.valid == 1,
            "brep_available":true,
            "warnings":[root_warning, spoke_warning]
        }),
    ))
}

fn rotation_about_z(center: [f64; 3], angle_degrees: f64) -> Result<[f64; 16], String> {
    if !angle_degrees.is_finite() || center.iter().any(|value| !value.is_finite()) {
        return Err("V 带轮辐条旋转参数必须为有限数值。".into());
    }
    let (sin, cos) = angle_degrees.to_radians().sin_cos();
    let tx = center[0] - cos * center[0] + sin * center[1];
    let ty = center[1] - sin * center[0] - cos * center[1];
    Ok([
        cos, -sin, 0.0, tx, sin, cos, 0.0, ty, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0,
    ])
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
        // after its 0.2 mm end-margin clamp (effective pitch e=14.8 mm).
        let root_centers = [
            (profile[7][1] + profile[8][1]) * 0.5,
            (profile[11][1] + profile[12][1]) * 0.5,
            (profile[15][1] + profile[16][1]) * 0.5,
        ];
        let expected_centers = [-14.8, 0.0, 14.8];
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
    fn rejects_width_and_bore_instead_of_changing_requested_geometry() {
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
        assert!(spokes_required(&p).unwrap());
        assert!(standard_profile(&p).is_ok());
    }

    #[test]
    fn c300_spoked_layout_preserves_legacy_profile_and_positions() {
        let parameters = serde_json::json!({
            "pitch_d":300.0,"grooves":3,"height":180.0,"groove_type":"C",
            "outer_d":309.6,"bore_d":20.0,"center":[0.0,0.0,0.0]
        });
        let (profile, layout) = standard_profile_and_spokes(&parameters).unwrap();
        let layout = layout.expect("C300 legacy recipe requires five spokes");
        assert_eq!(profile.len(), 24);
        assert_eq!(layout.count, 5);
        assert!((layout.radial_length - 135.3).abs() < 1e-10);
        assert!((layout.width - 37.884).abs() < 1e-10);
        assert!((layout.thickness - 15.3).abs() < 1e-10);
        assert!((layout.radial_midpoint - 87.15).abs() < 1e-10);
        assert!((layout.z_start - 67.05).abs() < 1e-10);
        assert!((profile[2][0] - 19.5).abs() < 1e-10);
        assert!((profile[2][1] + 10.35).abs() < 1e-10);
        assert!((profile[3][0] - 130.925).abs() < 1e-10);
        assert!((profile[3][1] + 10.35).abs() < 1e-10);
        let root_width = 19.0 + 2.0 * 14.3 * (angle(300.0).to_radians() / 2.0).tan();
        assert!((root_width - 28.847769740084427).abs() < 1e-10);
        assert!((root_width - 25.5 - 3.347769740084427).abs() < 1e-10);
    }

    #[test]
    fn c300_single_groove_reports_no_neighbor_root_overlap() {
        let parameters = serde_json::json!({
            "pitch_d":300.0,"grooves":1,"height":180.0,"groove_type":"C",
            "outer_d":309.6,"bore_d":20.0,"center":[0.0,0.0,0.0]
        });
        let details = legacy_mesh_geometry_details(&parameters).unwrap();
        assert_eq!(details["profile_points"], 16);
        assert_eq!(details["groove_root_overlap_mm"], 0.0);
        assert_eq!(details["legacy_profile_self_intersecting"], false);
        assert_eq!(details["spokes"]["count"], 5);
    }

    #[test]
    fn legacy_width_overlap_mesh_gate_rejects_non_width_or_non_single_feature_plans() {
        let output = crate::native_quick_parser::parse(
            "V带轮 基准直径100 槽数3 槽型A 内孔25 宽度30",
        );
        let plan = output["plan"].clone();
        assert_eq!(output["status"], "ready");
        assert!(is_mesh_only_legacy_quick_plan(&plan));

        let mut extra_feature = plan.clone();
        extra_feature["features"]
            .as_array_mut()
            .unwrap()
            .push(serde_json::json!({
                "id":"extra","kind":"box","operation":"add",
                "parameters":{"width":1.0,"depth":1.0,"height":1.0}
            }));
        assert!(!is_mesh_only_legacy_quick_plan(&extra_feature));

        let mut invalid_axis = plan.clone();
        invalid_axis["features"][0]["parameters"]["axis"] = serde_json::json!("X");
        assert!(!is_mesh_only_legacy_quick_plan(&invalid_axis));

        let mut invalid_bore = plan.clone();
        invalid_bore["features"][0]["parameters"]["bore_d"] = serde_json::json!(200.0);
        assert!(!is_mesh_only_legacy_quick_plan(&invalid_bore));

        let mut no_neighbor_overlap = plan;
        no_neighbor_overlap["features"][0]["parameters"]["grooves"] = serde_json::json!(1);
        assert!(!is_mesh_only_legacy_quick_plan(&no_neighbor_overlap));
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
