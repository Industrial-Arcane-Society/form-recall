//! Native approximate CADPlan helix builder.
//!
//! This keeps the legacy `make_helix` sampled section layout and sews the
//! resulting closed triangle shell into one OCCT faceted B-Rep. It is not an
//! analytic sweep and does not claim native CATIA spring semantics.

use crate::native_occt::{Kernel, Shape, TriangleMesh};
use serde_json::{Value, json};

const MIN_SEGMENTS_PER_TURN: i64 = 6;
const MAX_SEGMENTS_PER_TURN: i64 = 512;
const MIN_WIRE_SEGMENTS: i64 = 3;
const MAX_WIRE_SEGMENTS: i64 = 64;
const MAX_TRIANGLES: usize = 100_000;
const SEWING_TOLERANCE_MM: f64 = 1e-5;

#[derive(Clone, Copy, Debug)]
struct Helix {
    radius: f64,
    wire_r: f64,
    height: f64,
    turns: f64,
    center: [f64; 3],
    axis: char,
    taper: f64,
    taper_start: f64,
    seg_per_turn: usize,
    wire_seg: usize,
    path_segments: usize,
    triangle_count: usize,
    minimum_axial_pitch: f64,
    minimum_curvature_radius: f64,
}

pub fn preflight(parameters: &Value) -> Result<Value, String> {
    let checked = checked_parameters(parameters)?;
    Ok(json!({
        "axis": checked.axis.to_string(),
        "center_mm": checked.center,
        "centerline_radius_start_mm": checked.radius,
        "centerline_radius_end_mm": checked.radius * (1.0 - checked.taper),
        "wire_radius_mm": checked.wire_r,
        "height_mm": checked.height,
        "turns": checked.turns,
        "taper_fraction": checked.taper,
        "taper_start_fraction": checked.taper_start,
        "path_segments": checked.path_segments,
        "seg_per_turn": checked.seg_per_turn,
        "wire_segments": checked.wire_seg,
        "triangle_faces": checked.triangle_count,
        "triangle_face_budget": MAX_TRIANGLES,
        "minimum_axial_turn_pitch_mm": checked.minimum_axial_pitch,
        "minimum_sampled_centerline_curvature_radius_mm": checked.minimum_curvature_radius,
        "sewing_tolerance_mm": SEWING_TOLERANCE_MM,
        "section_model": "legacy_radial_axial_circle_sections"
    }))
}

pub fn build(kernel: &Kernel, parameters: &Value) -> Result<(Vec<Shape>, Value), String> {
    let checked = checked_parameters(parameters)?;
    let mesh = build_mesh(&checked)?;
    let shape = kernel.triangle_mesh_to_solid(&mesh, SEWING_TOLERANCE_MM)?;
    let analysis = shape.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "helix 未生成单一有效闭合实体；当前 valid={}, solids={}, closed={}；候选已拒绝。",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    let volume = shape.volume()?;
    if !volume.is_finite() || volume <= 0.0 {
        return Err("helix B-Rep 体积必须为有限正数；候选已拒绝。".into());
    }
    let bounds = shape.bounds()?;
    let support = json!({
        "status": "approximate",
        "geometry_support": "approximate",
        "builder": "legacy_oriented_triangle_sweep_sewn_to_occt_facet_brep",
        "axis": checked.axis.to_string(),
        "center_mm": checked.center,
        "centerline_radius_start_mm": checked.radius,
        "centerline_radius_end_mm": checked.radius * (1.0 - checked.taper),
        "wire_radius_mm": checked.wire_r,
        "height_mm": checked.height,
        "turns": checked.turns,
        "taper_fraction": checked.taper,
        "taper_start_fraction": checked.taper_start,
        "path_segments": checked.path_segments,
        "seg_per_turn": checked.seg_per_turn,
        "wire_segments": checked.wire_seg,
        "triangle_faces": checked.triangle_count,
        "triangle_face_budget": MAX_TRIANGLES,
        "sewing_tolerance_mm": SEWING_TOLERANCE_MM,
        "minimum_axial_turn_pitch_mm": checked.minimum_axial_pitch,
        "minimum_sampled_centerline_curvature_radius_mm": checked.minimum_curvature_radius,
        "bounds_mm": [bounds.xmin, bounds.ymin, bounds.zmin, bounds.xmax, bounds.ymax, bounds.zmax],
        "volume_mm3": volume,
        "occt_validation": {
            "valid": analysis.valid == 1,
            "solid_count": analysis.solid_count,
            "closed_solid_count": analysis.closed_solid_count
        },
        "warnings": [
            "截面和路径沿用 legacy 多边形采样；结果是缝合的 facet-BREP，不是解析曲面扫掠",
            "CATIA 特征转换仍不支持 helix；本地 OCCT 成功不代表 CATIA 已执行"
        ]
    });
    Ok((vec![shape], support))
}

fn checked_parameters(parameters: &Value) -> Result<Helix, String> {
    for key in ["radius", "wire_r", "height", "turns"] {
        if parameters.get(key).is_none_or(Value::is_null) {
            return Err(format!("helix 缺少必填几何参数 {key}。"));
        }
    }
    let radius = number(parameters, "radius")?;
    let wire_r = number(parameters, "wire_r")?;
    let height = number(parameters, "height")?;
    let turns = number(parameters, "turns")?;
    if radius <= 0.0 || wire_r <= 0.0 || height <= 0.0 || turns < 0.25 {
        return Err("helix 要求 radius>0、wire_r>0、height>0、turns>=0.25。".into());
    }

    let axis = parameters
        .get("axis")
        .and_then(Value::as_str)
        .unwrap_or("Z")
        .to_ascii_uppercase();
    let axis = match axis.as_str() {
        "X" => 'X',
        "Y" => 'Y',
        "Z" => 'Z',
        _ => return Err("helix.axis 必须是全局 X、Y 或 Z。".into()),
    };
    let center = point3(parameters.get("center"))?;
    let taper = optional_number(parameters, "taper", 0.0)?;
    if !(0.0..=1.0).contains(&taper) {
        return Err("helix.taper 必须为 0..=1 的半径收缩比例。".into());
    }
    let taper_start = optional_number(parameters, "taper_start", 0.0)?;
    if !(0.0..=0.9).contains(&taper_start) {
        return Err("helix.taper_start 必须为 0..=0.9 的归一化路径位置。".into());
    }
    let seg_per_turn = integer(parameters, "seg_per_turn", 14)?;
    if !(MIN_SEGMENTS_PER_TURN..=MAX_SEGMENTS_PER_TURN).contains(&seg_per_turn) {
        return Err(format!(
            "helix.seg_per_turn 必须为 {MIN_SEGMENTS_PER_TURN}..={MAX_SEGMENTS_PER_TURN}。"
        ));
    }
    let wire_seg = integer(parameters, "wire_seg", 8)?;
    if !(MIN_WIRE_SEGMENTS..=MAX_WIRE_SEGMENTS).contains(&wire_seg) {
        return Err(format!(
            "helix.wire_seg 必须为 {MIN_WIRE_SEGMENTS}..={MAX_WIRE_SEGMENTS}。"
        ));
    }

    let raw_segments = turns * seg_per_turn as f64;
    if !raw_segments.is_finite() || raw_segments > MAX_TRIANGLES as f64 {
        return Err("helix 路径采样超出三角面预算；请降低 turns 或 seg_per_turn。".into());
    }
    let path_segments = python_round(raw_segments).max(6.0) as usize;
    let triangle_count = (wire_seg as usize)
        .checked_mul(path_segments + 1)
        .and_then(|value| value.checked_mul(2))
        .ok_or("helix 三角面数量溢出。")?;
    if triangle_count > MAX_TRIANGLES {
        return Err(format!(
            "helix 预计生成 {triangle_count} 个三角面，超过 OCCT 上限 {MAX_TRIANGLES}。"
        ));
    }

    let axial_pitch = height / turns;
    if turns >= 1.0 && axial_pitch <= 2.0 * wire_r + 1e-6 {
        return Err(format!(
            "helix 相邻圈可能自交或相切：轴向螺距 {axial_pitch:.6} mm 不大于线材直径 {:.6} mm。",
            2.0 * wire_r
        ));
    }
    let minimum_curvature_radius =
        minimum_curvature_radius(radius, height, turns, taper, taper_start);
    if wire_r >= minimum_curvature_radius - 1e-8 {
        return Err(format!(
            "helix 线材半径 {:.6} mm 不小于采样中心线最小曲率半径 {:.6} mm；拒绝可能自交的截面扫掠。",
            wire_r, minimum_curvature_radius
        ));
    }
    // Keep the end ring away from a degenerate radial basis at the axis.
    if radius * (1.0 - taper) <= 1e-8 {
        return Err("helix taper 收缩后的末端中心线半径必须大于 0。".into());
    }

    Ok(Helix {
        radius,
        wire_r,
        height,
        turns,
        center,
        axis,
        taper,
        taper_start,
        seg_per_turn: seg_per_turn as usize,
        wire_seg: wire_seg as usize,
        path_segments,
        triangle_count,
        minimum_axial_pitch: axial_pitch,
        minimum_curvature_radius,
    })
}

fn build_mesh(helix: &Helix) -> Result<TriangleMesh, String> {
    let (u, v, w) = axis_basis(helix.axis);
    let wire_n = helix.wire_seg;
    let path_n = helix.path_segments;
    let section_count = path_n + 1;
    let point_count = section_count
        .checked_mul(wire_n)
        .ok_or("helix 截面点数量溢出。")?;
    let mut sections = Vec::new();
    sections
        .try_reserve_exact(point_count)
        .map_err(|error| format!("无法为 helix 截面分配内存：{error}"))?;
    for path_index in 0..=path_n {
        let t = path_index as f64 / path_n as f64;
        let angle = std::f64::consts::TAU * helix.turns * t;
        let radius = centerline_radius(helix, t);
        let radial = add(scale(u, angle.cos()), scale(v, angle.sin()));
        let origin = add(
            add(helix.center, scale(radial, radius)),
            scale(w, helix.height * t),
        );
        for wire_index in 0..wire_n {
            let phi = std::f64::consts::TAU * wire_index as f64 / wire_n as f64;
            sections.push(add(
                add(origin, scale(radial, helix.wire_r * phi.cos())),
                scale(w, helix.wire_r * phi.sin()),
            ));
        }
    }
    let global_center = centroid(&sections);
    let mut positions = Vec::new();
    positions
        .try_reserve_exact(helix.triangle_count * 3)
        .map_err(|error| format!("无法为 helix 三角面分配内存：{error}"))?;

    for path_index in 0..path_n {
        let a_offset = path_index * wire_n;
        let b_offset = (path_index + 1) * wire_n;
        for wire_index in 0..wire_n {
            let next = (wire_index + 1) % wire_n;
            let quad = [
                sections[a_offset + wire_index],
                sections[a_offset + next],
                sections[b_offset + next],
                sections[b_offset + wire_index],
            ];
            emit_oriented_polygon(&mut positions, quad.to_vec(), global_center);
        }
    }

    for section_offset in [0, path_n * wire_n] {
        let section = &sections[section_offset..section_offset + wire_n];
        let cap_center = centroid(section);
        for wire_index in 0..wire_n {
            let next = (wire_index + 1) % wire_n;
            emit_oriented_polygon(
                &mut positions,
                vec![cap_center, section[wire_index], section[next]],
                global_center,
            );
        }
    }
    if positions.len() / 3 != helix.triangle_count {
        return Err("helix 网格生成的三角面数量与预检预算不一致。".into());
    }
    Ok(TriangleMesh {
        positions,
        normals: Vec::new(),
    })
}

fn emit_oriented_polygon(
    positions: &mut Vec<[f64; 3]>,
    mut points: Vec<[f64; 3]>,
    center: [f64; 3],
) {
    let normal = cross(sub(points[1], points[0]), sub(points[2], points[0]));
    if dot(normal, sub(centroid(&points), center)) < 0.0 {
        points.reverse();
    }
    for index in 1..points.len() - 1 {
        positions.extend([points[0], points[index], points[index + 1]]);
    }
}

fn minimum_curvature_radius(
    radius: f64,
    height: f64,
    turns: f64,
    taper: f64,
    taper_start: f64,
) -> f64 {
    let omega = std::f64::consts::TAU * turns;
    let axial_per_radian = height / omega;
    let mut min_radius = f64::INFINITY;
    for index in 0..=256 {
        let t = index as f64 / 256.0;
        let (r, dr_dt) = if t > taper_start {
            let taper_span = 1.0 - taper_start;
            (
                radius * (1.0 - taper * (t - taper_start) / taper_span),
                -radius * taper / taper_span,
            )
        } else {
            (radius, 0.0)
        };
        let dr_dtheta = dr_dt / omega;
        let axial_dtheta = axial_per_radian;
        let speed_squared = dr_dtheta.powi(2) + r.powi(2) + axial_dtheta.powi(2);
        let cross_norm = ((2.0 * dr_dtheta.powi(2) + r.powi(2)).powi(2)
            + (2.0 * axial_dtheta * dr_dtheta).powi(2)
            + (axial_dtheta * r).powi(2))
        .sqrt();
        let curvature_radius = if cross_norm <= 1e-18 {
            f64::INFINITY
        } else {
            speed_squared.powf(1.5) / cross_norm
        };
        min_radius = min_radius.min(curvature_radius);
    }
    min_radius
}

fn centerline_radius(helix: &Helix, t: f64) -> f64 {
    if t <= helix.taper_start {
        helix.radius
    } else {
        let taper_fraction = (t - helix.taper_start) / (1.0 - helix.taper_start);
        helix.radius * (1.0 - helix.taper * taper_fraction)
    }
}

fn python_round(value: f64) -> f64 {
    let lower = value.floor();
    let fraction = value - lower;
    if fraction < 0.5 {
        lower
    } else if fraction > 0.5 || (lower as i64).rem_euclid(2) == 1 {
        lower + 1.0
    } else {
        lower
    }
}

fn axis_basis(axis: char) -> ([f64; 3], [f64; 3], [f64; 3]) {
    match axis {
        'X' => ([0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]),
        'Y' => ([0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]),
        _ => ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]),
    }
}

fn number(parameters: &Value, key: &str) -> Result<f64, String> {
    parameters
        .get(key)
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .ok_or_else(|| format!("helix.{key} 必须是有限数值。"))
}

fn optional_number(parameters: &Value, key: &str, default: f64) -> Result<f64, String> {
    if parameters.get(key).is_none_or(Value::is_null) {
        Ok(default)
    } else {
        number(parameters, key)
    }
}

fn integer(parameters: &Value, key: &str, default: i64) -> Result<i64, String> {
    if parameters.get(key).is_none_or(Value::is_null) {
        return Ok(default);
    }
    parameters[key]
        .as_i64()
        .ok_or_else(|| format!("helix.{key} 必须是整数。"))
}

fn point3(value: Option<&Value>) -> Result<[f64; 3], String> {
    let Some(value) = value.filter(|value| !value.is_null()) else {
        return Ok([0.0; 3]);
    };
    let coordinates = value
        .as_array()
        .filter(|coordinates| coordinates.len() == 3)
        .ok_or("helix.center 必须是全局 XYZ 三维坐标。")?;
    let mut point = [0.0; 3];
    for (index, coordinate) in coordinates.iter().enumerate() {
        point[index] = coordinate
            .as_f64()
            .filter(|coordinate| coordinate.is_finite())
            .ok_or("helix.center 坐标必须是有限数值。")?;
    }
    Ok(point)
}

fn centroid(points: &[[f64; 3]]) -> [f64; 3] {
    let mut center = [0.0; 3];
    for point in points {
        for axis in 0..3 {
            center[axis] += point[axis];
        }
    }
    for coordinate in &mut center {
        *coordinate /= points.len() as f64;
    }
    center
}

fn add(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}

fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

fn scale(vector: [f64; 3], scalar: f64) -> [f64; 3] {
    [vector[0] * scalar, vector[1] * scalar, vector[2] * scalar]
}

fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn schema_valid_legacy_helix_variants_pass_native_preflight() {
        for parameters in [
            json!({"radius":8,"wire_r":1,"height":20,"turns":3}),
            json!({"radius":10,"wire_r":1,"height":20,"turns":4,"axis":"Y","center":[12,-7,5]}),
            json!({"radius":10,"wire_r":1,"height":20,"turns":4,"axis":"X","center":[2,3,4],"taper":0.15,"taper_start":0.25}),
        ] {
            assert!(checked_parameters(&parameters).is_ok(), "{parameters}");
        }
    }

    #[test]
    fn helix_preflight_rejects_self_intersection_taper_degeneracy_and_mesh_budget() {
        let mut parameters = json!({"radius":8,"wire_r":1,"height":20,"turns":3});
        parameters["height"] = json!(3);
        assert!(
            checked_parameters(&parameters)
                .unwrap_err()
                .contains("自交或相切")
        );

        let parameters = json!({"radius":8,"wire_r":1,"height":20,"turns":3,"taper":1});
        assert!(
            checked_parameters(&parameters)
                .unwrap_err()
                .contains("末端中心线半径")
        );

        let parameters = json!({"radius":8,"wire_r":1,"height":200,"turns":3,
            "seg_per_turn":512,"wire_seg":64});
        assert!(
            checked_parameters(&parameters)
                .unwrap_err()
                .contains("超过 OCCT 上限")
        );
    }

    #[test]
    fn legacy_path_segment_rounding_uses_python_ties_to_even() {
        assert_eq!(python_round(14.5), 14.0);
        assert_eq!(python_round(15.5), 16.0);
    }
}
