//! Native approximate CADPlan worm: a legacy polygonal shaft fused with a
//! sampled helical ridge. Compound results are rejected as incomplete worms.

use crate::native_occt::{Analysis, Axis, Kernel, Shape};
use serde_json::{Value, json};

const MIN_SHAFT_SEGMENTS: i64 = 8;
const MAX_SHAFT_SEGMENTS: i64 = 512;
const ENGAGEMENT_TOLERANCE_MM: f64 = 1e-6;

#[derive(Clone, Copy, Debug)]
struct Worm {
    radius: f64,
    height: f64,
    thread_r: f64,
    wire_r: f64,
    height2: f64,
    turns: f64,
    center: [f64; 3],
    center2: [f64; 3],
    segments: usize,
    seg_per_turn: usize,
    wire_seg: usize,
}

pub fn preflight(parameters: &Value) -> Result<Value, String> {
    let worm = checked_parameters(parameters)?;
    let helix = helix_parameters(&worm);
    let helix_support = crate::native_helix::preflight(&helix)?;
    Ok(json!({
        "axis": "Z",
        "center_mm": worm.center,
        "center2_mm": worm.center2,
        "shaft_radius_mm": worm.radius,
        "shaft_height_mm": worm.height,
        "shaft_segments": worm.segments,
        "thread_centerline_radius_mm": worm.thread_r,
        "wire_radius_mm": worm.wire_r,
        "thread_height_mm": worm.height2,
        "turns": worm.turns,
        "minimum_radial_engagement_mm": (worm.radius + worm.wire_r - worm.thread_r).max(0.0),
        "thread_crown_above_shaft_mm": worm.thread_r + worm.wire_r - worm.radius,
        "helix_preflight": helix_support,
        "shaft_profile_points": worm.segments,
        "builder": "legacy_faceted_cylinder_plus_native_helix_brep_fuse"
    }))
}

pub fn build(kernel: &Kernel, parameters: &Value) -> Result<(Vec<Shape>, Value), String> {
    let worm = checked_parameters(parameters)?;
    let helix = helix_parameters(&worm);
    let helix_support = crate::native_helix::preflight(&helix)?;
    let profile = (0..worm.segments)
        .map(|index| {
            let angle = std::f64::consts::TAU * index as f64 / worm.segments as f64;
            [worm.radius * angle.cos(), worm.radius * angle.sin()]
        })
        .collect::<Vec<_>>();
    let shaft = kernel.prism(&profile, worm.height, worm.center, Axis::Z)?;
    let (thread_shapes, thread_support) = crate::native_helix::build(kernel, &helix)?;
    let fused = kernel.fuse(&[&shaft, &thread_shapes[0]])?;
    let analysis = fused.analyze()?;
    validate_fused_analysis(analysis)?;
    let volume = fused.volume()?;
    if !volume.is_finite() || volume <= 0.0 {
        return Err("worm B-Rep 体积必须为有限正数；候选已拒绝。".into());
    }
    let bounds = fused.bounds()?;
    let support = json!({
        "status": "approximate",
        "geometry_support": "approximate",
        "builder": "legacy_faceted_cylinder_plus_native_helix_brep_fuse",
        "axis": "global Z",
        "center_mm": worm.center,
        "center2_mm": worm.center2,
        "shaft_radius_mm": worm.radius,
        "shaft_height_mm": worm.height,
        "shaft_segments": worm.segments,
        "thread_centerline_radius_mm": worm.thread_r,
        "wire_radius_mm": worm.wire_r,
        "thread_height_mm": worm.height2,
        "turns": worm.turns,
        "helix_parameters": helix,
        "helix_support": helix_support,
        "helix_mesh_support": thread_support,
        "bounds_mm": [bounds.xmin, bounds.ymin, bounds.zmin, bounds.xmax, bounds.ymax, bounds.zmax],
        "volume_mm3": volume,
        "occt_validation": {
            "valid": analysis.valid == 1,
            "solid_count": analysis.solid_count,
            "closed_solid_count": analysis.closed_solid_count
        },
        "warnings": [
            "螺旋齿沿用 helix 的多边形截面采样；蜗杆是近似 facet-BREP，不是解析螺旋齿面",
            "仅支持全局 Z 同轴轴身与螺旋齿；多闭合体 fuse 结果会拒绝",
            "CATIA 映射仍只输出光轴；本地 OCCT 螺旋齿不代表 CATIA 已执行"
        ]
    });
    Ok((vec![fused], support))
}

fn checked_parameters(parameters: &Value) -> Result<Worm, String> {
    for key in ["radius", "height", "thread_r", "wire_r", "turns"] {
        if parameters.get(key).is_none_or(Value::is_null) {
            return Err(format!("worm 缺少必填几何参数 {key}。"));
        }
    }
    let radius = number(parameters, "radius")?;
    let height = number(parameters, "height")?;
    let thread_r = number(parameters, "thread_r")?;
    let wire_r = number(parameters, "wire_r")?;
    let turns = number(parameters, "turns")?;
    let height2 = optional_number(parameters, "height2", height)?;
    if radius <= 0.0
        || height <= 0.0
        || thread_r <= 0.0
        || wire_r <= 0.0
        || turns < 0.25
        || height2 <= 0.0
    {
        return Err(
            "worm 要求 radius/height/thread_r/wire_r/height2 为正数且 turns>=0.25。".into(),
        );
    }
    if let Some(axis) = parameters.get("axis").and_then(Value::as_str) {
        if !axis.eq_ignore_ascii_case("Z") {
            return Err("worm 仅支持全局 Z 轴；非 Z 轴方向不受支持。".into());
        }
    } else if parameters.get("axis").is_some_and(|value| !value.is_null()) {
        return Err("worm.axis 必须是字符串 Z。".into());
    }

    let center = point3(parameters.get("center"), "center")?;
    let center2 = if parameters.get("center2").is_none_or(Value::is_null) {
        center
    } else {
        point3(parameters.get("center2"), "center2")?
    };
    if (center2[0] - center[0]).abs() > ENGAGEMENT_TOLERANCE_MM
        || (center2[1] - center[1]).abs() > ENGAGEMENT_TOLERANCE_MM
    {
        return Err(
            "worm 的 center 与 center2 必须沿同一全局 Z 轴；横向轴心错位会形成偏心螺旋。".into(),
        );
    }
    let shaft_top = center[2] + height;
    let thread_bottom = center2[2] - wire_r;
    let thread_top = center2[2] + height2 + wire_r;
    if !shaft_top.is_finite() || !thread_bottom.is_finite() || !thread_top.is_finite() {
        return Err("worm 轴向范围必须是有限数值。".into());
    }
    if thread_top <= center[2] + ENGAGEMENT_TOLERANCE_MM
        || thread_bottom >= shaft_top - ENGAGEMENT_TOLERANCE_MM
    {
        return Err("worm 的螺旋齿与轴身在 Z 方向没有重叠；拒绝不相连候选。".into());
    }
    if thread_r - wire_r > radius + ENGAGEMENT_TOLERANCE_MM {
        return Err("worm 的螺旋齿内缘与轴身外圆存在径向间隙；拒绝分离实体。".into());
    }
    if thread_r + wire_r <= radius + ENGAGEMENT_TOLERANCE_MM {
        return Err("worm 的螺旋齿冠被轴身完全覆盖；该参数不会生成可见蜗杆齿。".into());
    }

    let segments = integer(parameters, "segments", 48)?;
    if !(MIN_SHAFT_SEGMENTS..=MAX_SHAFT_SEGMENTS).contains(&segments) {
        return Err(format!(
            "worm.segments 必须为 {MIN_SHAFT_SEGMENTS}..={MAX_SHAFT_SEGMENTS}。"
        ));
    }
    let seg_per_turn = integer(parameters, "seg_per_turn", 14)?;
    let wire_seg = integer(parameters, "wire_seg", 8)?;
    Ok(Worm {
        radius,
        height,
        thread_r,
        wire_r,
        height2,
        turns,
        center,
        center2,
        segments: segments as usize,
        seg_per_turn: seg_per_turn as usize,
        wire_seg: wire_seg as usize,
    })
}

fn validate_fused_analysis(analysis: Analysis) -> Result<(), String> {
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "worm 合并后必须是单一有效闭合实体；当前 valid={}, solids={}, closed={}；候选已拒绝。",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    Ok(())
}

fn helix_parameters(worm: &Worm) -> Value {
    json!({
        "radius": worm.thread_r,
        "wire_r": worm.wire_r,
        "height": worm.height2,
        "turns": worm.turns,
        "center": worm.center2,
        "axis": "Z",
        "seg_per_turn": worm.seg_per_turn,
        "wire_seg": worm.wire_seg
    })
}

fn number(parameters: &Value, key: &str) -> Result<f64, String> {
    parameters
        .get(key)
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .ok_or_else(|| format!("worm.{key} 必须是有限数值。"))
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
        .ok_or_else(|| format!("worm.{key} 必须是整数。"))
}

fn point3(value: Option<&Value>, name: &str) -> Result<[f64; 3], String> {
    let Some(value) = value.filter(|value| !value.is_null()) else {
        return Ok([0.0; 3]);
    };
    let coordinates = value
        .as_array()
        .filter(|coordinates| coordinates.len() == 3)
        .ok_or_else(|| format!("worm.{name} 必须是全局 XYZ 三维坐标。"))?;
    let mut point = [0.0; 3];
    for (index, coordinate) in coordinates.iter().enumerate() {
        point[index] = coordinate
            .as_f64()
            .filter(|coordinate| coordinate.is_finite())
            .ok_or_else(|| format!("worm.{name} 坐标必须是有限数值。"))?;
    }
    Ok(point)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Value {
        json!({
            "radius":10.0,"height":60.0,"thread_r":12.5,"wire_r":2.5,
            "height2":50.0,"turns":3.75,
            "center":[0.0,0.0,-30.0],"center2":[0.0,0.0,-25.0],
            "segments":48,"seg_per_turn":16,"wire_seg":8
        })
    }

    #[test]
    fn preflight_accepts_legacy_tangent_worm_and_exposes_thread_dimensions() {
        let support = preflight(&fixture()).unwrap();
        assert_eq!(support["axis"], "Z");
        assert_eq!(support["shaft_profile_points"], 48);
        assert_eq!(
            support["builder"],
            "legacy_faceted_cylinder_plus_native_helix_brep_fuse"
        );
        assert_eq!(support["thread_centerline_radius_mm"], 12.5);
        assert_eq!(support["wire_radius_mm"], 2.5);
    }

    #[test]
    fn preflight_rejects_missing_or_invalid_axial_geometry() {
        let mut missing = fixture();
        missing.as_object_mut().unwrap().remove("thread_r");
        assert!(preflight(&missing).unwrap_err().contains("thread_r"));

        let mut non_z = fixture();
        non_z["axis"] = json!("X");
        assert!(preflight(&non_z).unwrap_err().contains("全局 Z"));

        let mut offset = fixture();
        offset["center2"] = json!([0.01, 0.0, -25.0]);
        assert!(preflight(&offset).unwrap_err().contains("同一全局 Z 轴"));

        let mut no_axial_overlap = fixture();
        no_axial_overlap["center2"] = json!([0.0, 0.0, 40.0]);
        assert!(
            preflight(&no_axial_overlap)
                .unwrap_err()
                .contains("Z 方向没有重叠")
        );
    }

    #[test]
    fn preflight_rejects_radial_gap_and_buried_thread_crown() {
        let mut gap = fixture();
        gap["thread_r"] = json!(13.6);
        assert!(preflight(&gap).unwrap_err().contains("径向间隙"));

        let mut buried = fixture();
        buried["thread_r"] = json!(8.0);
        buried["wire_r"] = json!(1.0);
        assert!(preflight(&buried).unwrap_err().contains("完全覆盖"));
    }

    #[test]
    fn preflight_enforces_shaft_facet_resource_limit() {
        let mut too_many = fixture();
        too_many["segments"] = json!(513);
        assert!(preflight(&too_many).unwrap_err().contains("segments"));
    }

    #[test]
    fn multi_solid_or_invalid_boolean_result_can_never_be_accepted() {
        assert!(
            validate_fused_analysis(Analysis {
                valid: 1,
                solid_count: 2,
                closed_solid_count: 2,
            })
            .unwrap_err()
            .contains("单一有效闭合实体")
        );
        assert!(
            validate_fused_analysis(Analysis {
                valid: 0,
                solid_count: 1,
                closed_solid_count: 1,
            })
            .is_err()
        );
        assert!(
            validate_fused_analysis(Analysis {
                valid: 1,
                solid_count: 1,
                closed_solid_count: 0,
            })
            .is_err()
        );
        validate_fused_analysis(Analysis {
            valid: 1,
            solid_count: 1,
            closed_solid_count: 1,
        })
        .unwrap();
    }
}
