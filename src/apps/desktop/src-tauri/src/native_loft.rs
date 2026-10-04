//! Native approximate CADPlan NACA loft builder.
//!
//! The retained legacy airfoil sections are triangulated and sewn by OCCT into
//! one closed faceted B-Rep. This is a real OCCT solid, but it is not an
//! analytic ThruSections surface and does not claim native CATIA loft support.

use crate::native_occt::{Analysis, Kernel, Shape, TriangleMesh};
use serde_json::{Value, json};

const MIN_SECTIONS: i64 = 2;
const MAX_SECTIONS: i64 = 64;
const MIN_POINTS: i64 = 8;
const MAX_POINTS: i64 = 128;
const MAX_TRIANGLES: usize = 100_000;
const SEWING_TOLERANCE_MM: f64 = 1e-5;

#[derive(Clone, Copy, Debug)]
struct Loft {
    root_chord: f64,
    tip_chord: f64,
    span: f64,
    thickness: f64,
    camber: f64,
    camber_pos: f64,
    twist_deg: f64,
    sweep: f64,
    sections: usize,
    points: usize,
    rotate_axis: Option<char>,
    rotate_deg: f64,
    translate: [f64; 3],
    profile_vertices: usize,
    triangle_count: usize,
}

pub fn preflight(parameters: &Value) -> Result<Value, String> {
    let checked = checked_parameters(parameters)?;
    Ok(json!({
        "section_axis": "global Z before optional rigid rotation",
        "root_chord_mm": checked.root_chord,
        "tip_chord_mm": checked.tip_chord,
        "span_mm": checked.span,
        "thickness_ratio": checked.thickness,
        "camber_ratio": checked.camber,
        "camber_position": checked.camber_pos,
        "twist_deg": checked.twist_deg,
        "sweep_mm": checked.sweep,
        "section_intervals": checked.sections,
        "profile_points_per_surface": checked.points,
        "profile_vertices_per_section": checked.profile_vertices,
        "triangle_faces": checked.triangle_count,
        "triangle_face_budget": MAX_TRIANGLES,
        "sewing_tolerance_mm": SEWING_TOLERANCE_MM,
        "builder": "legacy_naca_sections_triangulated_and_sewn_to_occt_facet_brep"
    }))
}

pub fn build(kernel: &Kernel, parameters: &Value) -> Result<(Vec<Shape>, Value), String> {
    let checked = checked_parameters(parameters)?;
    let mesh = build_mesh(&checked)?;
    let shape = kernel.triangle_mesh_to_solid(&mesh, SEWING_TOLERANCE_MM)?;
    let analysis = shape.analyze()?;
    require_single_closed_solid(&analysis)?;
    let volume = shape.volume()?;
    if !volume.is_finite() || volume <= 0.0 {
        return Err("loft B-Rep 体积必须为有限正数；候选已拒绝。".into());
    }
    let bounds = shape.bounds()?;
    let support = json!({
        "status": "approximate",
        "geometry_support": "approximate",
        "builder": "legacy_naca_sections_triangulated_and_sewn_to_occt_facet_brep",
        "section_axis": "global Z before optional rigid rotation",
        "root_chord_mm": checked.root_chord,
        "tip_chord_mm": checked.tip_chord,
        "span_mm": checked.span,
        "thickness_ratio": checked.thickness,
        "camber_ratio": checked.camber,
        "camber_position": checked.camber_pos,
        "twist_deg": checked.twist_deg,
        "sweep_mm": checked.sweep,
        "section_intervals": checked.sections,
        "profile_points_per_surface": checked.points,
        "profile_vertices_per_section": checked.profile_vertices,
        "triangle_faces": checked.triangle_count,
        "triangle_face_budget": MAX_TRIANGLES,
        "bounds_mm": [bounds.xmin, bounds.ymin, bounds.zmin, bounds.xmax, bounds.ymax, bounds.zmax],
        "volume_mm3": volume,
        "occt_validation": {
            "valid": analysis.valid == 1,
            "solid_count": analysis.solid_count,
            "closed_solid_count": analysis.closed_solid_count
        },
        "warnings": [
            "翼型点列与截面布局复用 legacy NACA 四位翼型公式；表面用三角平面表示并缝合为 OCCT facet-BREP，不是解析放样曲面",
            "CATIA loft 映射仍以根部截面拉伸近似，不代表已在 CATIA 创建多截面放样"
        ]
    });
    Ok((vec![shape], support))
}

fn require_single_closed_solid(analysis: &Analysis) -> Result<(), String> {
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "loft 未生成单一有效闭合实体；当前 valid={}, solids={}, closed={}；候选已拒绝。",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    Ok(())
}

fn checked_parameters(parameters: &Value) -> Result<Loft, String> {
    for key in ["root_chord", "tip_chord", "span", "thickness"] {
        if parameters.get(key).is_none_or(Value::is_null) {
            return Err(format!("loft 缺少必填几何参数 {key}。"));
        }
    }
    let root_chord = required_number(parameters, "root_chord")?;
    let tip_chord = required_number(parameters, "tip_chord")?;
    let span = required_number(parameters, "span")?;
    let thickness = required_number(parameters, "thickness")?;
    if root_chord <= 0.0 || tip_chord <= 0.0 || span <= 0.0 {
        return Err("loft.root_chord、tip_chord 与 span 必须为有限正数。".into());
    }
    if !(0.01..=0.4).contains(&thickness) {
        return Err("loft.thickness 必须是 0.01..=0.4 的无量纲翼型厚度比。".into());
    }

    let camber = optional_number(parameters, "camber", 0.05)?;
    if !(-0.2..=0.2).contains(&camber) {
        return Err("loft.camber 必须位于 -0.2..=0.2。".into());
    }
    let camber_pos = optional_number(parameters, "camber_pos", 0.4)?;
    if !(0.05..=0.95).contains(&camber_pos) {
        return Err("loft.camber_pos 必须位于 0.05..=0.95，避免翼型公式退化。".into());
    }
    let twist_deg = optional_number(parameters, "twist", 0.0)?;
    if twist_deg.abs() > 720.0 {
        return Err("loft.twist 的绝对值不能超过 720 度。".into());
    }
    let sweep = optional_number(parameters, "sweep", 0.0)?;
    if sweep < 0.0 {
        return Err("loft.sweep 不得为负数；负向平移请使用 translate。".into());
    }
    let sections = optional_integer(parameters, "sections", 8)?;
    if !(MIN_SECTIONS..=MAX_SECTIONS).contains(&sections) {
        return Err(format!(
            "loft.sections 必须为整数 {MIN_SECTIONS}..={MAX_SECTIONS}。"
        ));
    }
    let points = optional_integer(parameters, "points", 26)?;
    if !(MIN_POINTS..=MAX_POINTS).contains(&points) {
        return Err(format!(
            "loft.points 必须为整数 {MIN_POINTS}..={MAX_POINTS}。"
        ));
    }

    let rotate = parameters.get("rotate").filter(|value| !value.is_null());
    let (rotate_axis, rotate_deg) = if let Some(rotate) = rotate {
        let values = rotate
            .as_array()
            .filter(|values| values.len() == 2)
            .ok_or("loft.rotate 必须是 [X|Y|Z, 角度]。")?;
        let axis = values[0]
            .as_str()
            .and_then(|value| value.chars().next())
            .filter(|axis| matches!(axis, 'X' | 'Y' | 'Z'))
            .ok_or("loft.rotate 轴必须是大写 X、Y 或 Z。")?;
        (Some(axis), finite_number(&values[1], "rotate[1]")?)
    } else {
        (None, 0.0)
    };
    let translate = vector3(parameters.get("translate"), [0.0; 3], "loft.translate")?;

    let profile_vertices = (points as usize)
        .checked_mul(2)
        .ok_or("loft 翼型顶点数量溢出。")?;
    let side_triangles = (sections as usize)
        .checked_mul(profile_vertices)
        .and_then(|value| value.checked_mul(2))
        .ok_or("loft 侧面三角面数量溢出。")?;
    let cap_triangles = (profile_vertices - 2)
        .checked_mul(2)
        .ok_or("loft 端盖三角面数量溢出。")?;
    let triangle_count = side_triangles
        .checked_add(cap_triangles)
        .ok_or("loft 三角面数量溢出。")?;
    if triangle_count > MAX_TRIANGLES {
        return Err(format!(
            "loft 预计生成 {triangle_count} 个三角面，超过 OCCT 上限 {MAX_TRIANGLES}。"
        ));
    }

    Ok(Loft {
        root_chord,
        tip_chord,
        span,
        thickness,
        camber,
        camber_pos,
        twist_deg,
        sweep,
        sections: sections as usize,
        points: points as usize,
        rotate_axis,
        rotate_deg,
        translate,
        profile_vertices,
        triangle_count,
    })
}

fn build_mesh(loft: &Loft) -> Result<TriangleMesh, String> {
    let root_profile = airfoil_profile(
        loft.root_chord,
        loft.thickness,
        loft.camber,
        loft.camber_pos,
        loft.points,
    );
    if root_profile.len() != loft.profile_vertices {
        return Err("loft 翼型点数与预检结果不一致。".into());
    }
    let signed_area = polygon_area(&root_profile);
    if !signed_area.is_finite() || signed_area.abs() <= 1e-12 {
        return Err("loft 翼型截面面积退化。".into());
    }
    let cap_triangles = triangulate_simple_polygon(&root_profile)?;
    if cap_triangles.len() != loft.profile_vertices - 2 {
        return Err("loft 端盖三角化未覆盖完整翼型轮廓。".into());
    }

    let sections_len = loft.sections + 1;
    let vertex_count = sections_len
        .checked_mul(loft.profile_vertices)
        .ok_or("loft 顶点数量溢出。")?;
    let mut vertices = Vec::new();
    vertices
        .try_reserve_exact(vertex_count)
        .map_err(|error| format!("无法为 loft 分配截面顶点：{error}"))?;

    for section_index in 0..=loft.sections {
        let u = section_index as f64 / loft.sections as f64;
        let chord = loft.root_chord + (loft.tip_chord - loft.root_chord) * u;
        let profile = if section_index == 0 {
            root_profile.clone()
        } else {
            airfoil_profile(
                chord,
                loft.thickness,
                loft.camber,
                loft.camber_pos,
                loft.points,
            )
        };
        let angle = loft.twist_deg.to_radians() * u;
        let (sin, cos) = angle.sin_cos();
        for [x, y] in profile {
            let centered_x = x - chord / 2.0 + loft.sweep * u;
            let local = [
                centered_x * cos - y * sin,
                centered_x * sin + y * cos,
                loft.span * u,
            ];
            let mut point = rotate_point(local, loft.rotate_axis, loft.rotate_deg);
            for axis in 0..3 {
                point[axis] += loft.translate[axis];
            }
            if point.iter().any(|value| !value.is_finite()) {
                return Err("loft 转换后的网格坐标出现非有限值。".into());
            }
            vertices.push(point);
        }
    }

    let ccw = signed_area > 0.0;
    let n = loft.profile_vertices;
    let mut positions = Vec::new();
    positions
        .try_reserve_exact(loft.triangle_count * 3)
        .map_err(|error| format!("无法为 loft 三角网格分配内存：{error}"))?;

    for section_index in 0..loft.sections {
        let lower = section_index * n;
        let upper = (section_index + 1) * n;
        for i in 0..n {
            let next = (i + 1) % n;
            let (a0, a1, b0, b1) = (
                vertices[lower + i],
                vertices[lower + next],
                vertices[upper + i],
                vertices[upper + next],
            );
            if ccw {
                emit_triangle(&mut positions, a0, a1, b1);
                emit_triangle(&mut positions, a0, b1, b0);
            } else {
                emit_triangle(&mut positions, a0, b1, a1);
                emit_triangle(&mut positions, a0, b0, b1);
            }
        }
    }

    let bottom = &vertices[..n];
    let top = &vertices[loft.sections * n..(loft.sections + 1) * n];
    for triangle in cap_triangles {
        let [a, b, c] = triangle;
        if ccw {
            emit_triangle(&mut positions, bottom[a], bottom[c], bottom[b]);
            emit_triangle(&mut positions, top[a], top[b], top[c]);
        } else {
            emit_triangle(&mut positions, bottom[a], bottom[b], bottom[c]);
            emit_triangle(&mut positions, top[a], top[c], top[b]);
        }
    }

    if positions.len() / 3 != loft.triangle_count {
        return Err("loft 网格三角面数量与预检预算不一致。".into());
    }
    Ok(TriangleMesh {
        positions,
        normals: Vec::new(),
    })
}

fn airfoil_profile(
    chord: f64,
    thickness: f64,
    camber: f64,
    camber_pos: f64,
    points: usize,
) -> Vec<[f64; 2]> {
    let mut upper = Vec::with_capacity(points + 1);
    let mut lower = Vec::with_capacity(points + 1);
    for i in 0..=points {
        let beta = std::f64::consts::PI * i as f64 / points as f64;
        let x = 0.5 * (1.0 - beta.cos());
        let yt = 5.0
            * thickness
            * (0.2969 * x.sqrt() - 0.1260 * x - 0.3516 * x.powi(2) + 0.2843 * x.powi(3)
                - 0.1036 * x.powi(4));
        if camber != 0.0 {
            let (yc, dy) = if x < camber_pos {
                (
                    camber / camber_pos.powi(2) * (2.0 * camber_pos * x - x.powi(2)),
                    2.0 * camber / camber_pos.powi(2) * (camber_pos - x),
                )
            } else {
                (
                    camber / (1.0 - camber_pos).powi(2)
                        * ((1.0 - 2.0 * camber_pos) + 2.0 * camber_pos * x - x.powi(2)),
                    2.0 * camber / (1.0 - camber_pos).powi(2) * (camber_pos - x),
                )
            };
            let theta = dy.atan();
            upper.push([
                (x - yt * theta.sin()) * chord,
                (yc + yt * theta.cos()) * chord,
            ]);
            lower.push([
                (x + yt * theta.sin()) * chord,
                (yc - yt * theta.cos()) * chord,
            ]);
        } else {
            upper.push([x * chord, yt * chord]);
            lower.push([x * chord, -yt * chord]);
        }
    }
    let mut profile = Vec::with_capacity(points * 2);
    profile.extend(upper.into_iter().rev());
    profile.extend(lower.into_iter().skip(1).take(points.saturating_sub(1)));
    profile
}

fn triangulate_simple_polygon(points: &[[f64; 2]]) -> Result<Vec<[usize; 3]>, String> {
    if points.len() < 3 {
        return Err("loft 端盖轮廓少于三个顶点。".into());
    }
    let area = polygon_area(points);
    let orientation = area.signum();
    if !area.is_finite() || orientation == 0.0 {
        return Err("loft 端盖轮廓面积为零或非有限值。".into());
    }
    let epsilon = (area.abs() * 1e-12).max(1e-14);
    let mut remaining: Vec<usize> = (0..points.len()).collect();
    let mut triangles = Vec::with_capacity(points.len() - 2);
    while remaining.len() > 3 {
        let mut ear = None;
        for offset in 0..remaining.len() {
            let prev = remaining[(offset + remaining.len() - 1) % remaining.len()];
            let curr = remaining[offset];
            let next = remaining[(offset + 1) % remaining.len()];
            if cross2(points[prev], points[curr], points[next]) * orientation <= epsilon {
                continue;
            }
            if remaining.iter().copied().any(|index| {
                index != prev
                    && index != curr
                    && index != next
                    && point_in_triangle(
                        points[index],
                        points[prev],
                        points[curr],
                        points[next],
                        orientation,
                        epsilon,
                    )
            }) {
                continue;
            }
            ear = Some((offset, [prev, curr, next]));
            break;
        }
        let (offset, triangle) = ear.ok_or("loft NACA 端盖轮廓无法可靠耳切三角化。")?;
        triangles.push(triangle);
        remaining.remove(offset);
    }
    triangles.push([remaining[0], remaining[1], remaining[2]]);
    Ok(triangles)
}

fn point_in_triangle(
    point: [f64; 2],
    a: [f64; 2],
    b: [f64; 2],
    c: [f64; 2],
    orientation: f64,
    epsilon: f64,
) -> bool {
    cross2(a, b, point) * orientation >= -epsilon
        && cross2(b, c, point) * orientation >= -epsilon
        && cross2(c, a, point) * orientation >= -epsilon
}

fn polygon_area(points: &[[f64; 2]]) -> f64 {
    points
        .iter()
        .zip(points.iter().cycle().skip(1))
        .take(points.len())
        .map(|(a, b)| a[0] * b[1] - b[0] * a[1])
        .sum::<f64>()
        * 0.5
}

fn cross2(a: [f64; 2], b: [f64; 2], c: [f64; 2]) -> f64 {
    (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
}

fn emit_triangle(positions: &mut Vec<[f64; 3]>, a: [f64; 3], b: [f64; 3], c: [f64; 3]) {
    positions.extend([a, b, c]);
}

fn rotate_point(point: [f64; 3], axis: Option<char>, degrees: f64) -> [f64; 3] {
    let Some(axis) = axis else { return point };
    let (sin, cos) = degrees.to_radians().sin_cos();
    match axis {
        'X' => [
            point[0],
            point[1] * cos - point[2] * sin,
            point[1] * sin + point[2] * cos,
        ],
        'Y' => [
            point[0] * cos + point[2] * sin,
            point[1],
            -point[0] * sin + point[2] * cos,
        ],
        'Z' => [
            point[0] * cos - point[1] * sin,
            point[0] * sin + point[1] * cos,
            point[2],
        ],
        _ => point,
    }
}

fn required_number(parameters: &Value, key: &str) -> Result<f64, String> {
    finite_number(
        parameters
            .get(key)
            .ok_or_else(|| format!("loft 缺少 {key}。"))?,
        key,
    )
}

fn optional_number(parameters: &Value, key: &str, default: f64) -> Result<f64, String> {
    parameters
        .get(key)
        .filter(|value| !value.is_null())
        .map(|value| finite_number(value, key))
        .unwrap_or(Ok(default))
}

fn finite_number(value: &Value, key: &str) -> Result<f64, String> {
    let number = value
        .as_f64()
        .ok_or_else(|| format!("loft.{key} 必须是有限数值。"))?;
    if !number.is_finite() {
        return Err(format!("loft.{key} 必须是有限数值。"));
    }
    Ok(number)
}

fn optional_integer(parameters: &Value, key: &str, default: i64) -> Result<i64, String> {
    parameters
        .get(key)
        .filter(|value| !value.is_null())
        .map(|value| {
            value
                .as_i64()
                .ok_or_else(|| format!("loft.{key} 必须是整数。"))
        })
        .unwrap_or(Ok(default))
}

fn vector3(value: Option<&Value>, default: [f64; 3], key: &str) -> Result<[f64; 3], String> {
    let Some(value) = value.filter(|value| !value.is_null()) else {
        return Ok(default);
    };
    let values = value
        .as_array()
        .filter(|values| values.len() == 3)
        .ok_or_else(|| format!("{key} 必须是有限 XYZ 向量。"))?;
    Ok([
        finite_number(&values[0], key)?,
        finite_number(&values[1], key)?,
        finite_number(&values[2], key)?,
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn baseline() -> Value {
        json!({
            "root_chord": 30.0,
            "tip_chord": 16.5,
            "span": 60.0,
            "thickness": 0.12,
            "camber": 0.05,
            "camber_pos": 0.4,
            "twist": 18.0
        })
    }

    #[test]
    fn legacy_airfoil_layout_and_mesh_triangle_budget_match() {
        let loft = checked_parameters(&baseline()).unwrap();
        let mesh = build_mesh(&loft).unwrap();
        assert_eq!(loft.profile_vertices, 52);
        assert_eq!(mesh.positions.len() / 3, loft.triangle_count);
        assert_eq!(loft.triangle_count, 932);
        assert!(polygon_area(&airfoil_profile(30.0, 0.12, 0.05, 0.4, 26)) > 0.0);
    }

    #[test]
    fn loft_rejects_degenerate_profiles_and_unbounded_tessellation() {
        let mut invalid = baseline();
        invalid["thickness"] = json!(0.005);
        assert!(
            checked_parameters(&invalid)
                .unwrap_err()
                .contains("0.01..=0.4")
        );
        let mut invalid = baseline();
        invalid["camber_pos"] = json!(1.0);
        assert!(
            checked_parameters(&invalid)
                .unwrap_err()
                .contains("camber_pos")
        );
        let mut invalid = baseline();
        invalid["sections"] = json!(65);
        assert!(
            checked_parameters(&invalid)
                .unwrap_err()
                .contains("sections")
        );
        let mut invalid = baseline();
        invalid["points"] = json!(129);
        assert!(checked_parameters(&invalid).unwrap_err().contains("points"));
    }

    #[test]
    fn cap_triangulation_covers_concave_clockwise_and_counterclockwise_polygons() {
        let ccw = [[0., 0.], [3., 0.], [3., 1.], [1., 1.], [1., 3.], [0., 3.]];
        let cw = [[0., 3.], [1., 3.], [1., 1.], [3., 1.], [3., 0.], [0., 0.]];
        for points in [&ccw[..], &cw[..]] {
            let triangles = triangulate_simple_polygon(points).unwrap();
            assert_eq!(triangles.len(), points.len() - 2);
            let covered_area: f64 = triangles
                .iter()
                .map(|[a, b, c]| cross2(points[*a], points[*b], points[*c]).abs() * 0.5)
                .sum();
            assert!((covered_area - polygon_area(points).abs()).abs() < 1e-10);
        }
    }

    #[test]
    fn loft_rejects_occt_compounds_and_open_results() {
        let error = require_single_closed_solid(&Analysis {
            valid: 1,
            solid_count: 2,
            closed_solid_count: 2,
        })
        .unwrap_err();
        assert!(error.contains("单一有效闭合实体"));
        assert!(
            require_single_closed_solid(&Analysis {
                valid: 1,
                solid_count: 1,
                closed_solid_count: 0,
            })
            .is_err()
        );
    }
}
