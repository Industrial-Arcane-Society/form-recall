//! Native approximate CADPlan builder for the legacy polygonal scroll disc.
//!
//! The spiral wall follows the retained Archimedean polyline and is extruded
//! as one OCCT prism. A cylindrical backplate overlaps its lower face and is
//! fused into the same closed solid. The curve remains a sampled polygon; this
//! does not claim a true analytic spiral or an engineering tolerance.
use crate::native_occt::{Axis, Kernel, Shape};
use serde_json::{json, Value};

const MIN_SEGMENTS: i64 = 24;
const MAX_SEGMENTS: i64 = 4096;
const MIN_SAMPLES_PER_TURN: f64 = 24.0;
const AREA_MESH_LINEAR_DEFLECTION_MM: f64 = 0.08;
const AREA_MESH_ANGULAR_DEFLECTION_RAD: f64 = 0.35;

#[derive(Clone, Copy, Debug)]
struct ScrollDisc {
    outer_radius: f64,
    height: f64,
    wall_t: f64,
    turns: f64,
    center: [f64; 3],
    segments: usize,
    start_radius: f64,
    pitch: f64,
    minimum_turn_spacing: f64,
}

pub fn preflight(parameters: &Value) -> Result<Value, String> {
    let checked = checked_parameters(parameters)?;
    Ok(json!({
        "axis": "Z",
        "center_mm": checked.center,
        "outer_radius_mm": checked.outer_radius,
        "height_mm": checked.height,
        "wall_thickness_mm": checked.wall_t,
        "turns": checked.turns,
        "profile_segments": checked.segments,
        "profile_points": 2 * (checked.segments + 1),
        "spiral_start_radius_mm": checked.start_radius,
        "spiral_pitch_mm": checked.pitch,
        "minimum_normal_turn_spacing_mm": checked.minimum_turn_spacing,
        "curve_model": "sampled_archimedean_polyline"
    }))
}

pub fn build(kernel: &Kernel, parameters: &Value) -> Result<(Vec<Shape>, Value), String> {
    let checked = checked_parameters(parameters)?;
    let profile = scroll_profile(&checked);
    let lower = checked.center[2] - checked.height / 2.0;
    let wall = kernel.prism(
        &profile,
        checked.height,
        [checked.center[0], checked.center[1], lower],
        Axis::Z,
    )?;
    let backplate_height = (checked.height * 0.35).max(0.5);
    let backplate = kernel.cylinder(
        checked.outer_radius,
        backplate_height,
        [checked.center[0], checked.center[1], lower],
        [0.0, 0.0, 1.0],
    )?;
    let fused = kernel.fuse(&[&wall, &backplate])?;
    let analysis = fused.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "scroll_disc 必须生成单一有效闭合实体；当前 valid={}, solids={}, closed={}",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    let volume = fused.volume()?;
    if !volume.is_finite() || volume <= 0.0 {
        return Err("scroll_disc 的融合实体体积必须为有限正数。".into());
    }
    let surface_area_estimate = approximate_surface_area(&fused)?;
    let angular_step = std::f64::consts::TAU * checked.turns / checked.segments as f64;
    let radial_rate = checked.pitch / std::f64::consts::TAU;
    // For p(theta)=(r0+b*theta)*(cos(theta),sin(theta)),
    // |p''| <= sqrt(R^2 + 4b^2). The linear interpolation error is at most
    // max(|p''|)*delta_theta^2/8 for each profile edge.
    let chordal_deviation_bound = (checked.outer_radius.powi(2) + 4.0 * radial_rate.powi(2)).sqrt()
        * angular_step.powi(2)
        / 8.0;
    let support = json!({
        "status": "approximate",
        "builder": "legacy_prismatic_archimedean_spiral_and_backplate",
        "axis": "Z",
        "center_mm": checked.center,
        "profile_segments": checked.segments,
        "profile_points": profile.len(),
        "spiral_start_radius_mm": checked.start_radius,
        "spiral_pitch_mm": checked.pitch,
        "minimum_normal_turn_spacing_mm": checked.minimum_turn_spacing,
        "maximum_polyline_deviation_bound_mm": chordal_deviation_bound,
        "volume_mm3": volume,
        "surface_area_estimate_mm2": surface_area_estimate,
        "surface_area_estimate_method": {
            "mesh": "OCCT triangle tessellation",
            "linear_deflection_mm": AREA_MESH_LINEAR_DEFLECTION_MM,
            "angular_deflection_rad": AREA_MESH_ANGULAR_DEFLECTION_RAD,
            "relative_comparison_tolerance": 0.006
        },
        "comparison_boundary": {
            "volume_vs_legacy_occt_mm3": 1e-5,
            "surface_area_estimate_vs_legacy_occt_relative": 0.006,
            "bounds_vs_legacy_occt_mm": 1e-6,
            "scope": "same sampled polyline and OCCT backplate; not error versus an ideal analytic scroll"
        },
        "warnings": [
            "spiral wall is a sampled polygonal approximation, not an analytic Archimedean surface",
            "CATIA export creates the legacy backplate approximation and omits the spiral wall"
        ]
    });
    Ok((vec![fused], support))
}

fn checked_parameters(parameters: &Value) -> Result<ScrollDisc, String> {
    for key in ["outer_d", "height", "wall_t", "turns"] {
        if parameters.get(key).is_none_or(Value::is_null) {
            return Err(format!("scroll_disc 缺少必填几何参数 {key}。"));
        }
    }
    let outer_d = number(parameters, "outer_d")?;
    let height = number(parameters, "height")?;
    let wall_t = number(parameters, "wall_t")?;
    let turns = number(parameters, "turns")?;
    if outer_d < 4.0 || height < 1.0 || wall_t < 0.5 || turns < 0.5 {
        return Err(
            "scroll_disc 要求 outer_d>=4 mm、height>=1 mm、wall_t>=0.5 mm、turns>=0.5；拒绝旧实现的静默下限裁剪。"
                .into(),
        );
    }
    let center = point3(parameters.get("center"))?;
    let raw_segments = optional_integer(parameters, "segments", 120)?;
    if !(MIN_SEGMENTS..=MAX_SEGMENTS).contains(&raw_segments) {
        return Err(format!(
            "scroll_disc.segments 必须为 {MIN_SEGMENTS}..={MAX_SEGMENTS}；拒绝超出原生轮廓预算。"
        ));
    }
    let segments = raw_segments as usize;
    if segments as f64 / turns < MIN_SAMPLES_PER_TURN {
        return Err(format!(
            "scroll_disc 至少需要每圈 {MIN_SAMPLES_PER_TURN:.0} 个螺旋轮廓采样点；当前 segments/turns={:.3}。",
            segments as f64 / turns
        ));
    }

    let outer_radius = outer_d / 2.0;
    let start_radius = outer_radius * 0.2;
    let available_radial_span = outer_radius - wall_t / 2.0 - start_radius;
    if start_radius <= wall_t / 2.0 || available_radial_span <= 0.0 {
        return Err(
            "scroll_disc 内侧螺旋壁半径必须为正，且外径须留出完整的壁厚与螺旋径向行程。".into(),
        );
    }
    let pitch = available_radial_span / turns;
    if !pitch.is_finite() || pitch <= 0.0 {
        return Err("scroll_disc 螺旋螺距必须为有限正数。".into());
    }
    let minimum_turn_spacing =
        pitch / (1.0 + (pitch / (std::f64::consts::TAU * start_radius)).powi(2)).sqrt();
    if turns >= 1.0 && minimum_turn_spacing <= wall_t + 1e-6 {
        return Err(format!(
            "scroll_disc 相邻螺旋壁会自交或相切：最小法向圈间距 {minimum_turn_spacing:.6} mm 不大于壁厚 {wall_t:.6} mm。"
        ));
    }

    Ok(ScrollDisc {
        outer_radius,
        height,
        wall_t,
        turns,
        center,
        segments,
        start_radius,
        pitch,
        minimum_turn_spacing,
    })
}

fn scroll_profile(checked: &ScrollDisc) -> Vec<[f64; 2]> {
    let mut outer = Vec::with_capacity(checked.segments + 1);
    let mut inner = Vec::with_capacity(checked.segments + 1);
    for index in 0..=checked.segments {
        let fraction = index as f64 / checked.segments as f64;
        let angle = std::f64::consts::TAU * checked.turns * fraction;
        let centerline_radius = checked.start_radius + checked.pitch * checked.turns * fraction;
        let (sin, cos) = angle.sin_cos();
        outer.push([
            (centerline_radius + checked.wall_t / 2.0) * cos,
            (centerline_radius + checked.wall_t / 2.0) * sin,
        ]);
        inner.push([
            (centerline_radius - checked.wall_t / 2.0) * cos,
            (centerline_radius - checked.wall_t / 2.0) * sin,
        ]);
    }
    outer.extend(inner.into_iter().rev());
    outer
}

fn approximate_surface_area(shape: &Shape) -> Result<f64, String> {
    let mesh = shape.tessellate(
        AREA_MESH_LINEAR_DEFLECTION_MM,
        AREA_MESH_ANGULAR_DEFLECTION_RAD,
    )?;
    if mesh.positions.len() < 3 || mesh.positions.len() % 3 != 0 {
        return Err("scroll_disc 面积估算收到无效三角网格。".into());
    }
    let mut area = 0.0;
    for triangle in mesh.positions.chunks_exact(3) {
        let a = triangle[0];
        let b = triangle[1];
        let c = triangle[2];
        let ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
        let ac = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
        let cross = [
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        ];
        area += (cross[0].powi(2) + cross[1].powi(2) + cross[2].powi(2)).sqrt() / 2.0;
    }
    if !area.is_finite() || area <= 0.0 {
        return Err("scroll_disc 三角网格面积估算必须为有限正数。".into());
    }
    Ok(area)
}

fn number(parameters: &Value, key: &str) -> Result<f64, String> {
    parameters
        .get(key)
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .ok_or_else(|| format!("scroll_disc.{key} 必须是有限数值。"))
}

fn optional_integer(parameters: &Value, key: &str, default: i64) -> Result<i64, String> {
    if parameters.get(key).is_none_or(Value::is_null) {
        return Ok(default);
    }
    parameters[key]
        .as_i64()
        .ok_or_else(|| format!("scroll_disc.{key} 必须是整数。"))
}

fn point3(value: Option<&Value>) -> Result<[f64; 3], String> {
    let Some(value) = value else {
        return Ok([0.0; 3]);
    };
    let values = value
        .as_array()
        .filter(|values| values.len() == 3)
        .ok_or("scroll_disc.center 必须包含三个有限 XYZ 数值。")?;
    let mut center = [0.0; 3];
    for (index, value) in values.iter().enumerate() {
        center[index] = value
            .as_f64()
            .filter(|value| value.is_finite())
            .ok_or("scroll_disc.center 必须包含三个有限 XYZ 数值。")?;
    }
    Ok(center)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parameters() -> Value {
        json!({"outer_d":60,"height":8,"wall_t":2,"turns":2.5})
    }

    #[test]
    fn scroll_disc_preflight_preserves_legacy_oracle_and_profile_contract() {
        let checked = preflight(&parameters()).unwrap();
        assert_eq!(checked["axis"], "Z");
        assert_eq!(checked["center_mm"], json!([0.0, 0.0, 0.0]));
        assert_eq!(checked["outer_radius_mm"], 30.0);
        assert_eq!(checked["profile_segments"], 120);
        assert_eq!(checked["profile_points"], 242);
        assert!((checked["spiral_start_radius_mm"].as_f64().unwrap() - 6.0).abs() < 1e-12);
        assert!((checked["spiral_pitch_mm"].as_f64().unwrap() - 9.2).abs() < 1e-12);

        let translated = preflight(&json!({"outer_d":60,"height":8,"wall_t":2,"turns":2.5,
            "center":[20,-11,7]}))
        .unwrap();
        assert_eq!(translated["center_mm"], json!([20.0, -11.0, 7.0]));
    }

    #[test]
    fn scroll_disc_preflight_rejects_degenerate_resource_and_self_intersection_cases() {
        for (key, value) in [
            ("outer_d", json!(0)),
            ("outer_d", json!(3.99)),
            ("height", json!(0)),
            ("height", json!(0.99)),
            ("wall_t", json!(0)),
            ("wall_t", json!(0.49)),
            ("turns", json!(0)),
            ("turns", json!(0.49)),
            ("segments", json!(23)),
            ("segments", json!(4097)),
            ("center", json!([0, 0, "nan"])),
        ] {
            let mut invalid = parameters();
            invalid[key] = value;
            assert!(
                preflight(&invalid).is_err(),
                "accepted {key}={}",
                invalid[key]
            );
        }
        let mut undersampled = parameters();
        undersampled["turns"] = json!(5.1);
        assert!(preflight(&undersampled).unwrap_err().contains("每圈"));

        let mut overlapping_turns = parameters();
        overlapping_turns["wall_t"] = json!(8);
        assert!(preflight(&overlapping_turns)
            .unwrap_err()
            .contains("自交或相切"));

        let mut collapsed_inner_wall = parameters();
        collapsed_inner_wall["wall_t"] = json!(13);
        assert!(preflight(&collapsed_inner_wall)
            .unwrap_err()
            .contains("内侧螺旋壁"));
    }
}
