//! Native builders for deterministic parametric CADPlan features.
//! These are exact local B-Rep implementations and do not imply CATIA support.
use crate::native_occt::{Axis, Kernel, Shape};
use serde_json::{json, Value};

const MAX_PISTON_GROOVES: usize = 256;
const MAX_PISTON_PROFILE_POINTS: usize = 4 * MAX_PISTON_GROOVES + 4;

pub fn preflight_piston(parameters: &Value) -> Result<(), String> {
    let diameter = number(parameters, "diameter")?;
    let height = number(parameters, "height")?;
    let pin_d = number(parameters, "pin_d")?;
    if diameter <= 0.0 || height <= 0.0 || pin_d < 0.0 {
        return Err("活塞直径与高度必须为正，销孔直径不能为负。".into());
    }
    let center = point3(parameters.get("center"))?;
    let radius = diameter / 2.0;
    let inner_radius = 1.0_f64.max(radius * 0.55);
    if inner_radius >= radius {
        return Err("活塞直径过小，旧版活塞剖面的内半径不小于外半径。".into());
    }
    let (profile, _) = piston_profile(radius, height, groove_count(parameters)?, inner_radius)?;
    if profile.len() > MAX_PISTON_PROFILE_POINTS
        || profile
            .iter()
            .flatten()
            .any(|coordinate| !coordinate.is_finite())
        || center.iter().any(|coordinate| !coordinate.is_finite())
    {
        return Err("活塞几何参数超出当前 OCCT 有限资源范围。".into());
    }
    Ok(())
}

pub fn build_piston(kernel: &Kernel, parameters: &Value) -> Result<(Vec<Shape>, Value), String> {
    preflight_piston(parameters)?;
    let diameter = number(parameters, "diameter")?;
    let height = number(parameters, "height")?;
    let pin_d = number(parameters, "pin_d")?;
    let requested_grooves = number(parameters, "grooves")?;
    let grooves = groove_count(parameters)?;
    let center = point3(parameters.get("center"))?;
    let radius = diameter / 2.0;
    let inner_radius = 1.0_f64.max(radius * 0.55);
    if inner_radius >= radius {
        return Err("活塞直径过小，旧版活塞剖面的内半径不小于外半径。".into());
    }

    let (profile, grooves_built) = piston_profile(radius, height, grooves, inner_radius)?;
    if profile.len() > MAX_PISTON_PROFILE_POINTS {
        return Err("活塞剖面超过当前 OCCT 点数上限。".into());
    }
    let body = kernel.revolve(&profile, 360.0, center, Axis::Z)?;

    let result = if pin_d > 0.0 {
        let pin_radius = (pin_d / 2.0).min(radius * 0.75).min(height * 0.45);
        let cutter_length = radius * 3.0;
        if !pin_radius.is_finite()
            || pin_radius <= 0.0
            || !cutter_length.is_finite()
            || cutter_length <= 0.0
        {
            return Err("活塞销孔刀具尺寸超出有限范围。".into());
        }
        let cutter_base = [center[0] - cutter_length / 2.0, center[1], center[2]];
        let cutter = kernel.cylinder(pin_radius, cutter_length, cutter_base, [1.0, 0.0, 0.0])?;
        kernel.cut(&body, &[&cutter])?
    } else {
        body
    };

    let analysis = result.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "活塞必须是单一有效封闭实体；当前 valid={}, solids={}, closed={}",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    let skipped_grooves = grooves.saturating_sub(grooves_built);
    let warnings = if skipped_grooves > 0 {
        vec!["部分请求环槽因旧版剖面边界规则被跳过；查看 grooves_built 与 skipped_grooves。"]
    } else {
        Vec::new()
    };
    let support = json!({
        "kind":"piston",
        "status":"exact",
        "builder":"analytic_grooved_revolve_with_x_axis_pin_bore",
        "geometry_support":"exact",
        "profile_point_count":profile.len(),
        "inner_radius_mm":inner_radius,
        "grooves_requested":requested_grooves,
        "grooves_built":grooves_built,
        "skipped_grooves":skipped_grooves,
        "pin_bore":if pin_d > 0.0 {
            json!({"axis":"X","centerline_mm":center,"diameter_mm":pin_d.min(radius * 1.5).min(height * 0.9)})
        } else {
            Value::Null
        },
        "warnings":warnings,
        "limits":{
            "max_grooves":MAX_PISTON_GROOVES,
            "scope":"local_candidate_geometry; CATIA remains approximate"
        }
    });
    Ok((vec![result], support))
}

fn piston_profile(
    radius: f64,
    height: f64,
    grooves: usize,
    inner_radius: f64,
) -> Result<(Vec<[f64; 2]>, usize), String> {
    let groove_depth = 0.4_f64.max(radius * 0.06);
    let groove_width = 0.6_f64.max(height * 0.035);
    let z_top = height / 2.0;
    let z_bottom = -height / 2.0;
    let mut profile = Vec::with_capacity(2 + 4 * grooves + 2);
    profile.push([inner_radius, z_bottom]);
    profile.push([radius, z_bottom]);
    let mut previous_groove_top = z_bottom;
    let mut grooves_built = 0;
    if grooves > 0 {
        let band = height * 0.34;
        let step = band / grooves as f64;
        for index in (0..grooves).rev() {
            let z_center = z_top - step * (index as f64 + 0.5);
            let low = z_center - groove_width;
            if z_center >= z_top || low <= z_bottom + groove_width * 0.5 {
                continue;
            }
            if radius - groove_depth <= inner_radius {
                return Err("活塞环槽深度会穿透旧版剖面的内壁；拒绝生成自交轮廓。".into());
            }
            if low <= previous_groove_top + 1e-10 {
                return Err(
                    "活塞环槽间距小于槽宽，旧版剖面会自交；请减少 grooves 或增大高度。".into(),
                );
            }
            profile.extend([
                [radius, low],
                [radius - groove_depth, z_center - groove_width * 0.85],
                [radius - groove_depth, z_center - groove_width * 0.15],
                [radius, z_center],
            ]);
            previous_groove_top = z_center;
            grooves_built += 1;
        }
    }
    profile.extend([[radius, z_top], [inner_radius, z_top]]);
    if profile
        .iter()
        .flatten()
        .any(|coordinate| !coordinate.is_finite())
    {
        return Err("活塞剖面坐标超出有限范围。".into());
    }
    Ok((profile, grooves_built))
}

fn groove_count(parameters: &Value) -> Result<usize, String> {
    let value = number(parameters, "grooves")?.trunc();
    if value > MAX_PISTON_GROOVES as f64 {
        return Err(format!(
            "活塞 grooves 超过当前原生几何资源上限 {MAX_PISTON_GROOVES}。"
        ));
    }
    Ok(if value > 0.0 { value as usize } else { 0 })
}

fn number(parameters: &Value, key: &str) -> Result<f64, String> {
    parameters
        .get(key)
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .ok_or_else(|| format!("活塞参数 {key} 必须是有限数值。"))
}

fn point3(value: Option<&Value>) -> Result<[f64; 3], String> {
    let Some(value) = value.filter(|value| !value.is_null()) else {
        return Ok([0.0; 3]);
    };
    let values = value
        .as_array()
        .filter(|values| values.len() == 3)
        .ok_or("活塞 center 必须是全局 XYZ 三维数组。")?;
    let mut point = [0.0; 3];
    for (index, value) in values.iter().enumerate() {
        point[index] = value
            .as_f64()
            .filter(|coordinate| coordinate.is_finite())
            .ok_or("活塞 center 必须是有限数值。")?;
    }
    Ok(point)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn piston_profile_matches_legacy_annular_section_and_grooves() {
        let (plain, plain_grooves) = piston_profile(10.0, 12.0, 0, 5.5).unwrap();
        assert_eq!(
            plain,
            vec![[5.5, -6.0], [10.0, -6.0], [10.0, 6.0], [5.5, 6.0]]
        );
        assert_eq!(plain_grooves, 0);

        let (grooved, grooves) = piston_profile(10.0, 12.0, 2, 5.5).unwrap();
        assert_eq!(grooves, 2);
        assert_eq!(grooved.len(), 12);
        assert!((grooved[2][1] - 2.34).abs() < 1e-12);
        assert!((grooved[5][1] - 2.94).abs() < 1e-12);
        assert!((grooved[6][1] - 4.38).abs() < 1e-12);
        let (_, short_height_grooves) = piston_profile(10.0, 1.0, 1, 5.5).unwrap();
        assert_eq!(short_height_grooves, 0);
        let (_, skipped_deep_groove) = piston_profile(1.3, 1.0, 1, 1.0).unwrap();
        assert_eq!(skipped_deep_groove, 0);
    }

    #[test]
    fn piston_preflight_rejects_invalid_dimensions_and_excessive_grooves() {
        let mut parameters = json!({"diameter":20,"height":12,"pin_d":1,"grooves":1});
        assert!(preflight_piston(&parameters).is_ok());
        parameters["diameter"] = json!(0);
        assert!(preflight_piston(&parameters)
            .unwrap_err()
            .contains("必须为正"));
        parameters["diameter"] = json!(20);
        parameters["grooves"] = json!(MAX_PISTON_GROOVES + 1);
        assert!(preflight_piston(&parameters)
            .unwrap_err()
            .contains("资源上限"));
    }

    #[test]
    fn piston_profile_rejects_overlapping_ring_grooves() {
        let error = piston_profile(10.0, 12.0, 7, 5.5).unwrap_err();
        assert!(error.contains("会自交"));
    }
}
