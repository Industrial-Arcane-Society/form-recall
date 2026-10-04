//! Native approximate CADPlan impeller builder.
//!
//! The retained legacy OCP path uses analytic hub/backplate solids and
//! twisted rectangular blade sections. This module preserves those parameter
//! semantics and sews a bounded triangulation of each blade into an OCCT
//! facet B-Rep before fusing it with the analytic solids.

use crate::native_occt::{Analysis, Axis, Kernel, Shape, TriangleMesh};
use serde_json::{json, Value};

const MAX_BLADES: i64 = 128;
const MIN_SEGMENTS: i64 = 4;
const MAX_SEGMENTS: i64 = 128;
const MAX_TRIANGLES: usize = 100_000;
const SEWING_TOLERANCE_MM: f64 = 1e-5;

#[derive(Clone, Copy, Debug)]
struct Impeller {
    hub_d: f64,
    tip_d: f64,
    height: f64,
    blades: usize,
    blade_t: f64,
    twist_deg: f64,
    center: [f64; 3],
    segments: usize,
    chord: f64,
    blade_axial: f64,
    radial_scale: f64,
    axial_scale: f64,
    triangle_count: usize,
}

pub fn preflight(parameters: &Value) -> Result<Value, String> {
    let p = checked_parameters(parameters)?;
    Ok(json!({
        "axis": "global Z",
        "center_mm": p.center,
        "hub_d_mm": p.hub_d,
        "tip_d_mm": p.tip_d,
        "height_mm": p.height,
        "blade_count": p.blades,
        "blade_t_mm": p.blade_t,
        "blade_axial_span_mm": p.blade_axial,
        "blade_chord_mm": p.chord,
        "twist_deg": p.twist_deg,
        "section_intervals_per_blade": p.segments,
        "triangle_faces": p.triangle_count,
        "triangle_face_budget": MAX_TRIANGLES,
        "sewing_tolerance_mm": SEWING_TOLERANCE_MM,
        "builder": "legacy_twisted_rectangular_sections_faceted_and_fused_with_analytic_hub_backplate"
    }))
}

pub fn build(kernel: &Kernel, parameters: &Value) -> Result<(Vec<Shape>, Value), String> {
    let p = checked_parameters(parameters)?;
    let rh = p.hub_d / 2.0;
    let rt = p.tip_d / 2.0;
    let [cx, cy, cz] = p.center;
    let hub = kernel.cone(
        rh,
        rh * 0.55,
        p.height,
        [cx, cy, cz - p.height / 2.0],
        Axis::Z,
    )?;
    let disc_height = (p.height * 0.28).max(0.5);
    let disc = kernel.revolve(
        &[
            [rh * 0.45, -p.height / 2.0],
            [rt, -p.height / 2.0],
            [rt, -p.height / 2.0 + disc_height],
            [rh * 0.45, -p.height / 2.0 + disc_height],
        ],
        360.0,
        p.center,
        Axis::Z,
    )?;
    let mut shapes = vec![hub, disc];
    for blade_index in 0..p.blades {
        let mesh = blade_mesh(&p, blade_index)?;
        shapes.push(kernel.triangle_mesh_to_solid(&mesh, SEWING_TOLERANCE_MM)?);
    }
    let refs = shapes.iter().collect::<Vec<_>>();
    let fused = kernel.fuse(&refs)?;
    let analysis = fused.analyze()?;
    require_single_closed_solid(analysis)?;
    let volume = fused.volume()?;
    if !volume.is_finite() || volume <= 0.0 {
        return Err("impeller B-Rep 体积必须为有限正数；候选已拒绝。".into());
    }
    let bounds = fused.bounds()?;
    let support = json!({
        "status": "approximate",
        "geometry_support": "approximate",
        "builder": "legacy_twisted_rectangular_sections_faceted_and_fused_with_analytic_hub_backplate",
        "axis": "global Z",
        "center_mm": p.center,
        "hub_d_mm": p.hub_d,
        "tip_d_mm": p.tip_d,
        "height_mm": p.height,
        "blade_count": p.blades,
        "blade_t_mm": p.blade_t,
        "blade_axial_span_mm": p.blade_axial,
        "blade_chord_mm": p.chord,
        "twist_deg": p.twist_deg,
        "section_intervals_per_blade": p.segments,
        "triangle_faces": p.triangle_count,
        "triangle_face_budget": MAX_TRIANGLES,
        "bounds_mm": [bounds.xmin, bounds.ymin, bounds.zmin, bounds.xmax, bounds.ymax, bounds.zmax],
        "volume_mm3": volume,
        "occt_validation": {"valid": analysis.valid == 1, "solid_count": analysis.solid_count, "closed_solid_count": analysis.closed_solid_count},
        "warnings": [
            "叶片使用legacy OCP截面位置的三角面近似并缝合为facet-BREP，不是解析ThruSections曲面",
            "CATIA导出仍只生成轮毂和轮盘，未创建扭转叶片"
        ]
    });
    Ok((vec![fused], support))
}

fn checked_parameters(parameters: &Value) -> Result<Impeller, String> {
    for key in ["hub_d", "tip_d", "height", "blades", "blade_t", "twist_deg"] {
        if parameters.get(key).is_none_or(Value::is_null) {
            return Err(format!(
                "impeller 缺少必填几何参数 {key}；拒绝用默认值猜测。"
            ));
        }
    }
    let hub_d = number(parameters, "hub_d")?;
    let tip_d = number(parameters, "tip_d")?;
    let height = number(parameters, "height")?;
    let blade_t = number(parameters, "blade_t")?;
    let twist_deg = number(parameters, "twist_deg")?;
    let blades = integer(parameters, "blades")?;
    if hub_d < 2.0 || tip_d < hub_d * 1.05 || height < 1.0 || blade_t <= 0.0 {
        return Err("impeller 要求 hub_d>=2 mm、tip_d>=1.05*hub_d、height>=1 mm 且 blade_t>0 mm；会触发旧配方静默尺寸夹取的参数不受支持。".into());
    }
    if !(3..=MAX_BLADES).contains(&blades) {
        return Err(format!("impeller.blades 必须为整数 3..={MAX_BLADES}。"));
    }
    if twist_deg.abs() > 720.0 {
        return Err("impeller.twist_deg 的绝对值不能超过 720 度。".into());
    }
    let center = point3(parameters.get("center"))?;
    let segments = if parameters.get("segments").is_none_or(Value::is_null) {
        12
    } else {
        integer(parameters, "segments")?
    };
    if !(MIN_SEGMENTS..=MAX_SEGMENTS).contains(&segments) {
        return Err(format!(
            "impeller.segments 必须为整数 {MIN_SEGMENTS}..={MAX_SEGMENTS}。"
        ));
    }

    let rh = hub_d / 2.0;
    let rt = tip_d / 2.0;
    let blade_len = rt - rh;
    let chord = (blade_len * 0.62).max(1.0);
    let blade_axial = height.min(blade_t);
    let profile = [
        [-chord / 2.0, -blade_axial / 2.0],
        [chord / 2.0, -blade_axial / 2.0],
        [chord / 2.0, blade_axial / 2.0],
        [-chord / 2.0, blade_axial / 2.0],
    ];
    let mut radial_max: f64 = 0.0;
    let mut axial_max: f64 = 0.0;
    for step in 0..=segments as usize {
        let t = step as f64 / segments as f64;
        let radial = rh + blade_len * 1.35 * t;
        let angle = twist_deg.to_radians() * t;
        let (sin, cos) = angle.sin_cos();
        for [tangent, axial] in profile {
            let qy = tangent * cos - axial * sin;
            let qz = tangent * sin + axial * cos;
            radial_max = radial_max.max((radial * radial + qy * qy).sqrt());
            axial_max = axial_max.max(qz.abs());
        }
    }
    if radial_max <= 0.0 || axial_max <= 0.0 {
        return Err("impeller 叶片截面退化。".into());
    }
    let triangle_count = (blades as usize)
        .checked_mul(segments as usize)
        .and_then(|n| n.checked_mul(8))
        .and_then(|n| n.checked_add(blades as usize * 4))
        .ok_or("impeller 三角面数量溢出。")?;
    if triangle_count > MAX_TRIANGLES {
        return Err(format!(
            "impeller 预计生成 {triangle_count} 个三角面，超过 OCCT 上限 {MAX_TRIANGLES}。"
        ));
    }
    Ok(Impeller {
        hub_d,
        tip_d,
        height,
        blades: blades as usize,
        blade_t,
        twist_deg,
        center,
        segments: segments as usize,
        chord,
        blade_axial,
        radial_scale: rt / radial_max,
        axial_scale: (height / 2.0 / axial_max).min(1.0),
        triangle_count,
    })
}

fn blade_mesh(p: &Impeller, blade_index: usize) -> Result<TriangleMesh, String> {
    let [cx, cy, cz] = p.center;
    let rh = p.hub_d / 2.0;
    let blade_len = p.tip_d / 2.0 - rh;
    let profile = [
        [-p.chord / 2.0, -p.blade_axial / 2.0],
        [p.chord / 2.0, -p.blade_axial / 2.0],
        [p.chord / 2.0, p.blade_axial / 2.0],
        [-p.chord / 2.0, p.blade_axial / 2.0],
    ];
    let blade_angle = std::f64::consts::TAU * blade_index as f64 / p.blades as f64;
    let (blade_sin, blade_cos) = blade_angle.sin_cos();
    let mut sections = Vec::with_capacity(p.segments + 1);
    for step in 0..=p.segments {
        let t = step as f64 / p.segments as f64;
        let radial = rh + blade_len * 1.35 * t;
        let angle = p.twist_deg.to_radians() * t;
        let (sin, cos) = angle.sin_cos();
        let mut section = [[0.0; 3]; 4];
        for (index, [tangent, axial]) in profile.into_iter().enumerate() {
            let qy = tangent * cos - axial * sin;
            let qz = tangent * sin + axial * cos;
            let x = radial * p.radial_scale;
            let y = qy * p.radial_scale;
            section[index] = [
                cx + x * blade_cos - y * blade_sin,
                cy + x * blade_sin + y * blade_cos,
                cz + qz * p.axial_scale,
            ];
        }
        sections.push(section);
    }
    let mut mesh = TriangleMesh {
        positions: Vec::with_capacity((8 * p.segments + 4) * 3),
        normals: Vec::new(),
    };
    // Four quads per section interval form the twisted rectangular shell.
    for pair in sections.windows(2) {
        for edge in 0..4 {
            let next = (edge + 1) % 4;
            let a = pair[0][edge];
            let b = pair[0][next];
            let c = pair[1][next];
            let d = pair[1][edge];
            mesh.positions.extend([a, b, c, a, c, d]);
        }
    }
    let first = sections.first().ok_or("impeller 缺少叶片根截面。")?;
    let last = sections.last().ok_or("impeller 缺少叶片尖截面。")?;
    mesh.positions
        .extend([first[0], first[2], first[1], first[0], first[3], first[2]]);
    mesh.positions
        .extend([last[0], last[1], last[2], last[0], last[2], last[3]]);
    Ok(mesh)
}

fn require_single_closed_solid(analysis: Analysis) -> Result<(), String> {
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "impeller 必须是单一有效闭合实体；当前 valid={}, solids={}, closed={}；候选已拒绝。",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    Ok(())
}

fn number(parameters: &Value, key: &str) -> Result<f64, String> {
    parameters
        .get(key)
        .and_then(Value::as_f64)
        .filter(|v| v.is_finite())
        .ok_or_else(|| format!("impeller.{key} 必须是有限数值。"))
}
fn integer(parameters: &Value, key: &str) -> Result<i64, String> {
    parameters
        .get(key)
        .and_then(Value::as_i64)
        .ok_or_else(|| format!("impeller.{key} 必须是整数。"))
}
fn point3(value: Option<&Value>) -> Result<[f64; 3], String> {
    let Some(value) = value else {
        return Ok([0.0; 3]);
    };
    if value.is_null() {
        return Ok([0.0; 3]);
    }
    let values = value
        .as_array()
        .filter(|values| values.len() == 3)
        .ok_or("impeller.center 必须是有限三维向量。")?;
    let point = [
        values[0]
            .as_f64()
            .filter(|v| v.is_finite())
            .ok_or("impeller.center[0] 必须有限。")?,
        values[1]
            .as_f64()
            .filter(|v| v.is_finite())
            .ok_or("impeller.center[1] 必须有限。")?,
        values[2]
            .as_f64()
            .filter(|v| v.is_finite())
            .ok_or("impeller.center[2] 必须有限。")?,
    ];
    Ok(point)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn impeller_requires_all_schema_geometry_parameters() {
        let error =
            checked_parameters(&json!({"hub_d":20,"tip_d":60,"height":15,"blades":6,"blade_t":2}))
                .unwrap_err();
        assert!(error.contains("twist_deg"), "{error}");
    }

    #[test]
    fn impeller_rejects_dimensions_legacy_would_silently_clamp() {
        let error = checked_parameters(
            &json!({"hub_d":1,"tip_d":60,"height":15,"blades":6,"blade_t":2,"twist_deg":0}),
        )
        .unwrap_err();
        assert!(error.contains("hub_d"), "{error}");
    }

    #[test]
    fn impeller_rejects_tip_diameter_that_legacy_would_expand() {
        let error = checked_parameters(
            &json!({"hub_d":20,"tip_d":20.5,"height":15,"blades":6,"blade_t":2,"twist_deg":0}),
        )
        .unwrap_err();
        assert!(error.contains("tip_d>=1.05*hub_d"), "{error}");
    }

    #[test]
    fn impeller_preflight_reports_explicit_geometry_and_budget() {
        let result = preflight(
            &json!({"hub_d":20,"tip_d":60,"height":15,"blades":6,"blade_t":2,"twist_deg":25}),
        )
        .unwrap();
        assert_eq!(result["blade_count"], 6);
        assert_eq!(result["blade_axial_span_mm"], 2.0);
        assert_eq!(result["section_intervals_per_blade"], 12);
    }
}
