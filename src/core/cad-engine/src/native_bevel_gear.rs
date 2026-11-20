//! Native builder for the retained approximate CADPlan bevel-gear recipe.
//!
//! This intentionally reproduces the legacy polygonal trapezoid-and-taper
//! mesh, then sews it to a faceted OCCT solid. It is not an involute gear.
use crate::native_occt::{Kernel, Shape, TriangleMesh};
use serde_json::{Value, json};
use std::f64::consts::{PI, TAU};

const MAX_TRIANGLES: usize = 100_000;
const SEWING_TOLERANCE_MM: f64 = 1.0e-5;
const PROFILE_POINTS_PER_TOOTH: usize = 8;
const BORE_SEGMENTS: usize = 40;

struct Recipe {
    teeth: usize,
    height: f64,
    requested_cone_deg: f64,
    cone_deg: f64,
    requested_bore_d: f64,
    bore_radius: f64,
    center: [f64; 3],
    segments: usize,
    top_scale: f64,
    profile: Vec<[f64; 2]>,
}

pub fn preflight(parameters: &Value) -> Result<(), String> {
    recipe(parameters).map(|_| ())
}

pub fn build(kernel: &Kernel, parameters: &Value) -> Result<(Vec<Shape>, Value), String> {
    let recipe = recipe(parameters)?;
    let mesh = mesh(&recipe)?;
    let shape = kernel.triangle_mesh_to_solid(&mesh, SEWING_TOLERANCE_MM)?;
    let analysis = shape.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "锥齿轮必须构成单一有效闭合 facet-BREP；当前 valid={}, solids={}, closed={}",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    Ok((
        vec![shape],
        json!({
            "status":"approximate",
            "geometry_support":"approximate",
            "builder":"legacy_tapered_trapezoidal_gear_faceted_brep",
            "teeth_built":recipe.teeth,
            "profile_points_per_end":recipe.profile.len(),
            "segments_built":recipe.segments,
            "triangles_built":mesh.positions.len()/3,
            "axis":"global Z",
            "center_mm":recipe.center,
            "height_centered":true,
            "requested_cone_deg":recipe.requested_cone_deg,
            "cone_deg_after_legacy_clamp":recipe.cone_deg,
            "top_scale":recipe.top_scale,
            "requested_bore_d_mm":recipe.requested_bore_d,
            "effective_bore_radius_mm":recipe.bore_radius,
            "zero_bore_d_uses_legacy_default_bore":recipe.requested_bore_d == 0.0,
            "warnings":[
                "复刻的是梯形齿和线性缩放的近似锥齿轮网格，不是渐开线齿面或解析齿面。",
                "CATIA 映射仍是圆柱直齿轮近似，会省略锥面收缩。"
            ]
        }),
    ))
}

fn recipe(parameters: &Value) -> Result<Recipe, String> {
    let raw_teeth = parameters
        .get("teeth")
        .and_then(Value::as_i64)
        .ok_or("bevel_gear.teeth 必须是显式整数。")?;
    // The legacy Python profile explicitly clamps tooth counts below six.
    let teeth =
        usize::try_from(raw_teeth.max(6)).map_err(|_| "bevel_gear.teeth 超出当前平台范围。")?;
    let module = number(parameters, "module")?;
    let height = number(parameters, "height")?;
    let requested_cone_deg = number(parameters, "cone_deg")?;
    let requested_bore_d = optional_number(parameters, "bore_d", 0.0)?;
    let center = point3(parameters.get("center"))?;
    if module <= 0.0 || height <= 0.0 || requested_bore_d < 0.0 {
        return Err(
            "锥齿轮 module/height 必须为正数，bore_d 必须非负；0 沿用旧版默认中心孔。".into(),
        );
    }

    let requested_segments = parameters
        .get("segments")
        .map(|value| value.as_i64().ok_or("bevel_gear.segments 必须是整数。"))
        .transpose()?
        .unwrap_or(12);
    // This mirrors make_bevel_gear: max(4, min(segments, teeth // 2)).
    let segments =
        4usize.max(usize::try_from(requested_segments.min((teeth / 2) as i64)).unwrap_or(0));
    let outer_count = teeth
        .checked_mul(PROFILE_POINTS_PER_TOOTH)
        .ok_or("锥齿轮轮廓点数溢出。")?;
    let ring_count = outer_count
        .checked_add(BORE_SEGMENTS)
        .ok_or("锥齿轮环点数溢出。")?;
    let triangle_bound = ring_count
        .checked_mul(
            segments
                .checked_mul(2)
                .and_then(|n| n.checked_add(2))
                .ok_or("锥齿轮三角面数量溢出。")?,
        )
        .ok_or("锥齿轮三角面数量溢出。")?;
    if triangle_bound > MAX_TRIANGLES {
        return Err(format!(
            "锥齿轮 facet-BREP 需要至多 {triangle_bound} 个三角面，超过原生上限 {MAX_TRIANGLES}。"
        ));
    }

    let pitch_radius = module * teeth as f64 / 2.0;
    if !pitch_radius.is_finite() || pitch_radius <= 0.0 {
        return Err("锥齿轮派生分度圆半径无效。".into());
    }
    let cone_deg = requested_cone_deg.clamp(5.0, 75.0);
    let shrink = height * cone_deg.to_radians().tan() / pitch_radius.max(1.0e-6);
    let top_scale = (1.0 - shrink).max(0.3);
    let mut profile = gear_profile(teeth, module);
    if profile.len() != outer_count
        || profile
            .iter()
            .flatten()
            .any(|coordinate| !coordinate.is_finite())
    {
        return Err("锥齿轮离散齿廓无效或超出有限坐标范围。".into());
    }
    let clearance = profile_clearance(&profile);
    let bore_radius = if requested_bore_d == 0.0 {
        (0.5_f64).max(pitch_radius * 0.28)
    } else {
        requested_bore_d / 2.0
    }
    .min(pitch_radius * 0.70);
    if !clearance.is_finite() || bore_radius <= 0.0 || bore_radius >= clearance {
        return Err(format!(
            "锥齿轮中心孔半径 {bore_radius} mm 触及或穿越离散齿根轮廓（实际中心净空 {clearance} mm）。"
        ));
    }
    if center
        .iter()
        .any(|value| !(*value + pitch_radius + height).is_finite())
        || !top_scale.is_finite()
    {
        return Err("锥齿轮派生包围盒或缩放值超出有限坐标范围。".into());
    }
    // The bridge's sewing tolerance rejects triangle areas <= tolerance^2;
    // preflight checks the constructed mesh so invalid tiny facets fail before OCCT.
    let result = Recipe {
        teeth,
        height,
        requested_cone_deg,
        cone_deg,
        requested_bore_d,
        bore_radius,
        center,
        segments,
        top_scale,
        profile: std::mem::take(&mut profile),
    };
    let probe = mesh_unchecked(&result)?;
    for triangle in probe.positions.chunks_exact(3) {
        let cross = cross(sub(triangle[1], triangle[0]), sub(triangle[2], triangle[0]));
        let area2 = dot(cross, cross);
        if !area2.is_finite() || area2 <= SEWING_TOLERANCE_MM.powi(4) {
            return Err(
                "锥齿轮包含低于 OCCT 缝合容差的离散三角面；请增大尺寸或调整齿数/分段。".into(),
            );
        }
    }
    Ok(result)
}

fn mesh(recipe: &Recipe) -> Result<TriangleMesh, String> {
    mesh_unchecked(recipe)
}

fn mesh_unchecked(recipe: &Recipe) -> Result<TriangleMesh, String> {
    let mut triangles = Vec::new();
    let bore_profile: Vec<[f64; 2]> = (0..BORE_SEGMENTS)
        .map(|i| {
            let angle = TAU * i as f64 / BORE_SEGMENTS as f64;
            [
                recipe.bore_radius * angle.cos(),
                recipe.bore_radius * angle.sin(),
            ]
        })
        .collect();
    let mut outer_rings = Vec::with_capacity(recipe.segments + 1);
    let mut inner_rings = Vec::with_capacity(recipe.segments + 1);
    for k in 0..=recipe.segments {
        let t = k as f64 / recipe.segments as f64;
        let scale = 1.0 + (recipe.top_scale - 1.0) * t;
        let z = -recipe.height / 2.0 + recipe.height * t;
        outer_rings.push(ring(&recipe.profile, scale, z, recipe.center));
        inner_rings.push(ring(&bore_profile, scale, z, recipe.center));
    }
    let n = recipe.profile.len();
    let m = bore_profile.len();
    for k in 0..recipe.segments {
        append_side(&mut triangles, &outer_rings[k], &outer_rings[k + 1], false);
        append_side(&mut triangles, &inner_rings[k], &inner_rings[k + 1], true);
    }
    append_annulus_cap(
        &mut triangles,
        &outer_rings[0],
        &inner_rings[0],
        recipe.center,
        true,
    );
    append_annulus_cap(
        &mut triangles,
        &outer_rings[recipe.segments],
        &inner_rings[recipe.segments],
        recipe.center,
        false,
    );
    let expected_bound = (n + m) * (recipe.segments * 2 + 2);
    let triangle_count = triangles.len() / 3;
    if triangle_count > expected_bound || triangle_count > MAX_TRIANGLES {
        return Err("锥齿轮离散三角面数超过当前原生资源上限。".into());
    }
    Ok(TriangleMesh {
        positions: triangles,
        normals: Vec::new(),
    })
}

fn ring(profile: &[[f64; 2]], scale: f64, z: f64, center: [f64; 3]) -> Vec<[f64; 3]> {
    profile
        .iter()
        .map(|point| {
            [
                center[0] + point[0] * scale,
                center[1] + point[1] * scale,
                center[2] + z,
            ]
        })
        .collect()
}

fn append_side(out: &mut Vec<[f64; 3]>, current: &[[f64; 3]], next: &[[f64; 3]], flip: bool) {
    for i in 0..current.len() {
        let j = (i + 1) % current.len();
        push_triangle(out, current[i], current[j], next[j], flip);
        push_triangle(out, current[i], next[j], next[i], flip);
    }
}

fn append_annulus_cap(
    out: &mut Vec<[f64; 3]>,
    outer: &[[f64; 3]],
    inner: &[[f64; 3]],
    center: [f64; 3],
    flip: bool,
) {
    fn key(point: [f64; 3], center: [f64; 3]) -> f64 {
        let angle = (point[1] - center[1]).atan2(point[0] - center[0]);
        if angle >= 0.0 { angle } else { angle + TAU }
    }
    let mut outer_sorted = outer.to_vec();
    let mut inner_sorted = inner.to_vec();
    // Python's sorted is stable; ties are impossible for these legacy rings,
    // but the index tie-break preserves that behavior explicitly.
    outer_sorted.sort_by(|a, b| key(*a, center).total_cmp(&key(*b, center)));
    inner_sorted.sort_by(|a, b| key(*a, center).total_cmp(&key(*b, center)));
    let (n, m) = (outer_sorted.len(), inner_sorted.len());
    let (mut i, mut j) = (0usize, 0usize);
    let (mut pa, mut pb) = (outer_sorted[0], inner_sorted[0]);
    let z = outer[0][2];
    while i < n || j < m {
        let na = key(outer_sorted[(i + 1) % n], center) + if i + 1 >= n { TAU } else { 0.0 };
        let nb = key(inner_sorted[(j + 1) % m], center) + if j + 1 >= m { TAU } else { 0.0 };
        if i < n && (j >= m || na <= nb) {
            i += 1;
            let pn = outer_sorted[i % n];
            push_triangle(
                out,
                [pb[0], pb[1], z],
                [pn[0], pn[1], z],
                [pa[0], pa[1], z],
                flip,
            );
            pa = pn;
        } else {
            j += 1;
            let pn = inner_sorted[j % m];
            push_triangle(
                out,
                [pa[0], pa[1], z],
                [pb[0], pb[1], z],
                [pn[0], pn[1], z],
                flip,
            );
            pb = pn;
        }
    }
}

fn push_triangle(
    out: &mut Vec<[f64; 3]>,
    a: [f64; 3],
    mut b: [f64; 3],
    mut c: [f64; 3],
    flip: bool,
) {
    if flip {
        std::mem::swap(&mut b, &mut c);
    }
    let cross = cross(sub(b, a), sub(c, a));
    if dot(cross, cross).sqrt() >= 1.0e-12 {
        out.extend([a, b, c]);
    }
}

fn gear_profile(teeth: usize, module: f64) -> Vec<[f64; 2]> {
    let pitch_radius = module * teeth as f64 / 2.0;
    let addendum_radius = pitch_radius + module;
    let root_radius = (pitch_radius - 1.25 * module).max(1.0);
    let half = PI / (2.0 * teeth as f64);
    let offsets = [
        (-half * 1.55, root_radius),
        (-half * 1.15, root_radius + 0.15 * module),
        (-half * 0.95, pitch_radius * 0.99),
        (-half * 0.48, addendum_radius),
        (half * 0.48, addendum_radius),
        (half * 0.95, pitch_radius * 0.99),
        (half * 1.15, root_radius + 0.15 * module),
        (half * 1.55, root_radius),
    ];
    let mut points = Vec::with_capacity(teeth * PROFILE_POINTS_PER_TOOTH);
    for tooth in 0..teeth {
        let base_angle = TAU * tooth as f64 / teeth as f64;
        for (delta, radius) in offsets {
            let angle = base_angle + delta;
            points.push([radius * angle.cos(), radius * angle.sin()]);
        }
    }
    points
}

fn profile_clearance(profile: &[[f64; 2]]) -> f64 {
    profile
        .iter()
        .zip(profile.iter().cycle().skip(1))
        .map(|(a, b)| {
            let edge = [b[0] - a[0], b[1] - a[1]];
            let denom = edge[0] * edge[0] + edge[1] * edge[1];
            let t = (-(a[0] * edge[0] + a[1] * edge[1]) / denom).clamp(0.0, 1.0);
            (a[0] + t * edge[0]).hypot(a[1] + t * edge[1])
        })
        .fold(f64::INFINITY, f64::min)
}

fn number(parameters: &Value, key: &str) -> Result<f64, String> {
    parameters
        .get(key)
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .ok_or_else(|| format!("bevel_gear.{key} 必须显式提供有限数值。"))
}

fn optional_number(parameters: &Value, key: &str, default: f64) -> Result<f64, String> {
    match parameters.get(key) {
        None | Some(Value::Null) => Ok(default),
        Some(value) => value
            .as_f64()
            .filter(|number| number.is_finite())
            .ok_or_else(|| format!("bevel_gear.{key} 必须是有限数值。")),
    }
}

fn point3(value: Option<&Value>) -> Result<[f64; 3], String> {
    match value {
        None | Some(Value::Null) => Ok([0.0; 3]),
        Some(value) => {
            let rows = value
                .as_array()
                .filter(|rows| rows.len() == 3)
                .ok_or("bevel_gear.center 必须是三个有限 XYZ 数值。")?;
            let mut point = [0.0; 3];
            for index in 0..3 {
                point[index] = rows[index]
                    .as_f64()
                    .filter(|number| number.is_finite())
                    .ok_or("bevel_gear.center 必须是三个有限 XYZ 数值。")?;
            }
            Ok(point)
        }
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

#[cfg(test)]
mod tests {
    use super::*;

    fn valid() -> Value {
        json!({"teeth":20,"module":2,"height":12,"cone_deg":45,"bore_d":0,
            "center":[0,0,0],"segments":12})
    }

    #[test]
    fn bevel_gear_preflight_matches_legacy_defaults_and_enforces_limits() {
        assert!(preflight(&valid()).is_ok());
        for (key, value, expected) in [
            ("module", json!(0), "module/height"),
            ("height", json!(-1), "module/height"),
            ("bore_d", json!(-1), "bore_d"),
            ("teeth", json!(100_000), "超过原生上限"),
        ] {
            let mut parameters = valid();
            parameters[key] = value;
            let error = preflight(&parameters).unwrap_err();
            assert!(error.contains(expected), "{key}: {error}");
        }
        let mut root_crossing = valid();
        root_crossing["teeth"] = json!(6);
        root_crossing["bore_d"] = json!(9);
        assert!(
            preflight(&root_crossing)
                .unwrap_err()
                .contains("中心孔半径")
        );
        let mut missing = valid();
        missing.as_object_mut().unwrap().remove("cone_deg");
        assert!(preflight(&missing).unwrap_err().contains("cone_deg"));
    }

    #[test]
    fn bevel_gear_profile_and_legacy_clamps_are_deterministic() {
        let baseline = recipe(&valid()).unwrap();
        assert_eq!(baseline.teeth, 20);
        assert_eq!(baseline.profile.len(), 160);
        assert_eq!(baseline.segments, 10);
        assert!((baseline.bore_radius - 5.6).abs() < 1e-12);
        assert!((baseline.top_scale - 0.4).abs() < 1e-12);
        let mut clamped = valid();
        clamped["teeth"] = json!(4);
        clamped["cone_deg"] = json!(90);
        clamped["segments"] = json!(1);
        let clamped = recipe(&clamped).unwrap();
        assert_eq!(clamped.teeth, 6);
        assert_eq!(clamped.cone_deg, 75.0);
        assert_eq!(clamped.segments, 4);
        assert_eq!(clamped.top_scale, 0.3);
    }
}
