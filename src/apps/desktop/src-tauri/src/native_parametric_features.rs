//! Native builders for deterministic parametric CADPlan features.
//! These are exact local B-Rep implementations and do not imply CATIA support.
use crate::native_occt::{Axis, Kernel, Shape};
use serde_json::{Value, json};

const MAX_PISTON_GROOVES: usize = 256;
const MAX_PISTON_PROFILE_POINTS: usize = 4 * MAX_PISTON_GROOVES + 4;
pub const MAX_SPOKED_WHEEL_SPOKES: u64 = 512;
pub const MAX_CAMSHAFT_LOBES: u64 = 512;
pub const MAX_CRANKSHAFT_THROWS: u64 = 128;

pub fn preflight_crankshaft(parameters: &Value) -> Result<(), String> {
    let length = number(parameters, "length")?;
    let main_d = number(parameters, "main_d")?;
    let pin_d = number(parameters, "pin_d")?;
    let stroke = number(parameters, "stroke")?;
    let throws = integer(parameters, "throws")?;
    let center = point3(parameters.get("center"))?;
    if length <= 0.0 || main_d <= 0.0 || pin_d <= 0.0 || stroke <= 0.0
        || throws == 0 || throws > MAX_CRANKSHAFT_THROWS
        || center.iter().any(|v| !v.is_finite())
    {
        return Err("曲轴要求 length/main_d/pin_d/stroke 为正数、throws 为 1..=128，且中心坐标有限。".into());
    }
    Ok(())
}

/// Reproduces the legacy Document B-Rep path: an analytic X-axis main shaft,
/// alternating eccentric pin journals, and two overlapping rectangular webs
/// per throw. The more detailed polygon-only cheek mesh remains a separate
/// legacy compatibility path and is not claimed as this exact B-Rep contract.
pub fn build_crankshaft(kernel: &Kernel, parameters: &Value) -> Result<(Vec<Shape>, Value), String> {
    preflight_crankshaft(parameters)?;
    let length = number(parameters, "length")?;
    let main_r = number(parameters, "main_d")? / 2.0;
    let pin_r = number(parameters, "pin_d")? / 2.0;
    let stroke = number(parameters, "stroke")?;
    let throws = integer(parameters, "throws")? as usize;
    let center = point3(parameters.get("center"))?;
    let spacing = length / throws as f64;
    let pin_length = spacing * 0.42;
    let web_width = (number(parameters, "main_d")? * 0.28).max(2.0).min(spacing * 0.25);
    let shaft = kernel.cylinder(main_r, length,
        [center[0] - length / 2.0, center[1], center[2]], [1.0, 0.0, 0.0])?;
    let mut shapes = vec![shaft];
    let mut web_count = 0usize;
    for index in 0..throws {
        let x0 = center[0] - length / 2.0 + spacing * (index as f64 + 0.5);
        let dy = if index % 2 == 0 { stroke / 2.0 } else { -stroke / 2.0 };
        shapes.push(kernel.cylinder(pin_r, pin_length,
            [x0 - pin_length / 2.0, center[1] + dy, center[2]], [1.0, 0.0, 0.0])?);
        let y0 = (center[1] - main_r).min(center[1] + dy - pin_r);
        let y1 = (center[1] + dy - pin_r).max(center[1] - main_r);
        let y2 = (center[1] + main_r).min(center[1] + dy + pin_r);
        let y3 = (center[1] + dy + pin_r).max(center[1] + main_r);
        let ylo = y0.min(y2);
        let yhi = y1.max(y3);
        let zhalf = main_r.max(pin_r);
        for xmid in [x0 - pin_length / 2.0 - web_width / 2.0,
                     x0 + pin_length / 2.0 + web_width / 2.0] {
            shapes.push(kernel.make_box([web_width, yhi - ylo, 2.0 * zhalf],
                [xmid, (ylo + yhi) / 2.0, center[2]])?);
            web_count += 1;
        }
    }
    let refs: Vec<_> = shapes.iter().collect();
    let result = kernel.fuse(&refs)?;
    let analysis = result.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!("曲轴必须构成单一有效闭合实体；当前 valid={}, solids={}, closed={}",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count));
    }
    Ok((vec![result], json!({"status":"approximate", "geometry_support":"approximate", "builder":"analytic_journals_with_rectangular_webs",
        "throws_built":throws, "webs_built":web_count, "axis":"global X", "segments_ignored_by_analytic_brep":true})))
}

pub fn preflight_camshaft(parameters: &Value) -> Result<(), String> {
    let length = number(parameters, "length")?;
    let journal_d = number(parameters, "journal_d")?;
    let base_d = number(parameters, "base_d")?;
    let lift = number(parameters, "lift")?;
    let lobes = integer(parameters, "lobes")?;
    let angle = optional_number(parameters, "lobe_deg", 0.0)?;
    let center = point3(parameters.get("center"))?;
    if !length.is_finite() || length < 2.0 || !journal_d.is_finite() || journal_d <= 0.0
        || !base_d.is_finite() || base_d < 2.0 || !lift.is_finite() || lift < 0.0
        || lobes == 0 || lobes > MAX_CAMSHAFT_LOBES || !angle.is_finite()
        || center.iter().any(|v| !v.is_finite())
    {
        return Err("凸轮轴要求 length/base_d>=2、journal_d>0、lift>=0、lobes 为 1..=512，且角度与中心有限。".into());
    }
    Ok(())
}

pub fn build_camshaft(kernel: &Kernel, parameters: &Value) -> Result<(Vec<Shape>, Value), String> {
    preflight_camshaft(parameters)?;
    let length = number(parameters, "length")?;
    let journal_d = number(parameters, "journal_d")?;
    let base_r = number(parameters, "base_d")? / 2.0;
    let lift = number(parameters, "lift")?;
    let lobes = integer(parameters, "lobes")? as usize;
    let phase = optional_number(parameters, "lobe_deg", 0.0)?.to_radians();
    let center = point3(parameters.get("center"))?;
    let lobe_width = (length / (lobes as f64 * 1.8)).max(2.0);
    if lobe_width > length {
        return Err("凸轮轴凸轮宽度超过轴长。".into());
    }
    let shaft = kernel.cylinder(journal_d / 2.0, length,
        [center[0] - length / 2.0, center[1], center[2]], [1.0, 0.0, 0.0])?;
    let mut shapes = vec![shaft];
    for i in 0..lobes {
        let theta = phase + std::f64::consts::TAU * i as f64 / lobes as f64;
        let mut profile = Vec::with_capacity(48);
        for j in 0..48 {
            let a = std::f64::consts::TAU * j as f64 / 48.0;
            let degrees = a.to_degrees();
            let r = if degrees < 72.0 {
                let t = degrees / 72.0;
                base_r + lift * 0.5 * (1.0 - (std::f64::consts::PI * t).cos())
            } else if degrees < 120.0 { base_r + lift } else {
                let t = (degrees - 120.0) / 240.0;
                base_r + lift * 0.5 * (1.0 + (std::f64::consts::PI * t).cos())
            };
            let (s, c) = (a + theta).sin_cos();
            profile.push([r * c, r * s]);
        }
        let x = center[0] - length / 2.0 + length * (i as f64 + 0.5) / lobes as f64;
        shapes.push(kernel.prism(&profile, lobe_width,
            [x - lobe_width / 2.0, center[1], center[2]], Axis::X)?);
    }
    let refs: Vec<_> = shapes.iter().collect();
    let result = kernel.fuse(&refs)?;
    let analysis = result.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err("凸轮轴必须构成单一有效闭合实体。".into());
    }
    Ok((vec![result], json!({"geometry_support":"exact", "lobes_built":lobes,
        "profile_samples":48, "shaft_axis":"global X", "legacy_cam_profile_angle_deg":120.0})))
}

pub fn preflight_cross_joint(parameters: &Value) -> Result<(), String> {
    let span = number(parameters, "span")?;
    let journal_d = number(parameters, "journal_d")?;
    if span <= 0.0 || journal_d <= 0.0 {
        return Err("十字轴 span 与 journal_d 必须为正数。".into());
    }
    let hub = (span * 0.30).min(journal_d * 0.90);
    let half_journal = (span - hub) / 2.0;
    if !hub.is_finite() || hub <= 0.0 || !half_journal.is_finite() || half_journal <= 0.0 {
        return Err("十字轴参数无法形成有限的中心块和轴颈。".into());
    }
    let center = point3(parameters.get("center"))?;
    if center.iter().any(|coordinate| !coordinate.is_finite()) {
        return Err("十字轴 center 必须为有限XYZ坐标。".into());
    }
    Ok(())
}

pub fn build_cross_joint(
    kernel: &Kernel,
    parameters: &Value,
) -> Result<(Vec<Shape>, Value), String> {
    preflight_cross_joint(parameters)?;
    let span = number(parameters, "span")?;
    let journal_d = number(parameters, "journal_d")?;
    let center = point3(parameters.get("center"))?;
    let radius = journal_d / 2.0;
    let hub = (span * 0.30).min(journal_d * 0.90);
    let half = (span - hub) / 2.0;
    let axial_start = hub / 2.0;

    // Match the legacy dimensions without its faceted approximation. Each
    // axial cylinder starts on the hub face and keeps its stated full length.
    // No overlap offset is added: OCCT must validate the actual shared faces.
    let hub_shape = kernel.make_box([hub; 3], center)?;
    let journals = [
        kernel.cylinder(
            radius,
            half,
            [center[0] + axial_start, center[1], center[2]],
            [1.0, 0.0, 0.0],
        )?,
        kernel.cylinder(
            radius,
            half,
            [center[0] - span / 2.0, center[1], center[2]],
            [1.0, 0.0, 0.0],
        )?,
        kernel.cylinder(
            radius,
            half,
            [center[0], center[1] + axial_start, center[2]],
            [0.0, 1.0, 0.0],
        )?,
        kernel.cylinder(
            radius,
            half,
            [center[0], center[1] - span / 2.0, center[2]],
            [0.0, 1.0, 0.0],
        )?,
    ];
    let mut references = Vec::with_capacity(5);
    references.push(&hub_shape);
    references.extend(journals.iter());
    let result = kernel.fuse(&references)?;
    let analysis = result.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "十字轴原始尺寸融合后必须是单一有效闭合实体；当前 valid={}, solids={}, closed={}",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }

    let samples = [
        (
            "x_journal",
            [center[0], center[1], center[2]],
            [1.0, 0.0, 0.0],
            span / 2.0,
        ),
        (
            "y_journal",
            [center[0], center[1], center[2]],
            [0.0, 1.0, 0.0],
            span / 2.0,
        ),
        (
            "z_hub",
            [center[0], center[1], center[2]],
            [0.0, 0.0, 1.0],
            hub / 2.0,
        ),
    ];
    let mut material_samples = Vec::with_capacity(samples.len());
    for (label, origin, direction, expected_half_span) in samples {
        let intervals = result.line_material_intervals(
            origin,
            direction,
            -expected_half_span - 1.0,
            expected_half_span + 1.0,
            1e-7,
        )?;
        if intervals.len() != 1
            || (intervals[0][0] + expected_half_span).abs() > 1e-6
            || (intervals[0][1] - expected_half_span).abs() > 1e-6
        {
            return Err(format!(
                "十字轴 {label} 轴心材料采样不连续或尺寸不符：{intervals:?}"
            ));
        }
        material_samples.push(json!({
            "label":label,
            "origin_mm":origin,
            "direction":direction,
            "material_intervals_mm":intervals,
            "expected_span_mm":2.0 * expected_half_span,
            "passed":true
        }));
    }
    let details = json!({
        "kind":"cross_joint",
        "status":"approximate",
        "geometry_support":"approximate",
        "builder":"analytic_hub_box_with_four_fused_cylindrical_journals",
        "center_mm":center,
        "span_mm":span,
        "journal_d_mm":journal_d,
        "hub_size_mm":hub,
        "journal_length_mm":half,
        "axis_layout":"four journals along global +X,-X,+Y,-Y; hub centered at global XYZ",
        "baseline_comparison":"analytic OCCT cylinders compared with the legacy 24-segment faceted approximation; bounds are checked independently, volume difference is reported but is not exact-parity acceptance",
        "material_samples":material_samples,
        "warnings":["Legacy facet cylinders are replaced by analytic OCCT cylinders; facet-volume equality is not expected."]
    });
    Ok((vec![result], details))
}

pub fn preflight_conrod(parameters: &Value) -> Result<(), String> {
    let length = number(parameters, "length")?;
    let big_d = number(parameters, "big_d")?;
    let small_d = number(parameters, "small_d")?;
    let big_bore = number(parameters, "big_bore")?;
    let small_bore = number(parameters, "small_bore")?;
    let shank_w = number(parameters, "shank_w")?;
    let thick = number(parameters, "thick")?;
    if [length, big_d, small_d, big_bore, small_bore, shank_w, thick]
        .iter()
        .any(|value| *value <= 0.0)
    {
        return Err("连杆 length、直径、孔径、杆宽和厚度必须为正数。".into());
    }
    let big_r = big_d / 2.0;
    let small_r = small_d / 2.0;
    let big_bore_r = big_bore / 2.0;
    let small_bore_r = small_bore / 2.0;
    if big_bore >= big_d || small_bore >= small_d {
        return Err("连杆大小头孔径必须小于对应头部直径。".into());
    }
    let delta_ratio = (big_r - small_r) / length;
    if !delta_ratio.is_finite() || delta_ratio.abs() >= 1.0 {
        return Err("连杆长度不足以构造大小头外公切杆身。".into());
    }
    let radial_big = big_r - (shank_w / 2.0).min((big_r - big_bore_r) * 0.8);
    let radial_small = small_r - (shank_w / 2.0).min((small_r - small_bore_r) * 0.8);
    let inset = 0.25;
    if !radial_big.is_finite()
        || !radial_small.is_finite()
        || radial_big - inset <= big_bore_r
        || radial_small - inset <= small_bore_r
    {
        return Err("连杆杆身无法在保留大小头孔壁的条件下连接。".into());
    }
    let center = point3(parameters.get("center"))?;
    if center.iter().any(|coordinate| !coordinate.is_finite()) {
        return Err("连杆 center 必须为有限XYZ坐标。".into());
    }
    Ok(())
}

pub fn build_conrod(kernel: &Kernel, parameters: &Value) -> Result<(Vec<Shape>, Value), String> {
    preflight_conrod(parameters)?;
    let length = number(parameters, "length")?;
    let big_d = number(parameters, "big_d")?;
    let small_d = number(parameters, "small_d")?;
    let big_bore = number(parameters, "big_bore")?;
    let small_bore = number(parameters, "small_bore")?;
    let shank_w = number(parameters, "shank_w")?;
    let thick = number(parameters, "thick")?;
    let center = point3(parameters.get("center"))?;
    let big_r = big_d / 2.0;
    let small_r = small_d / 2.0;
    let big_bore_r = big_bore / 2.0;
    let small_bore_r = small_bore / 2.0;
    let big_x = -length / 2.0;
    let small_x = length / 2.0;
    let half_thick = thick / 2.0;

    let big_ring = kernel.revolve(
        &[
            [big_bore_r, -half_thick],
            [big_r, -half_thick],
            [big_r, half_thick],
            [big_bore_r, half_thick],
        ],
        360.0,
        [center[0] + big_x, center[1], center[2]],
        Axis::Z,
    )?;
    let small_ring = kernel.revolve(
        &[
            [small_bore_r, -half_thick],
            [small_r, -half_thick],
            [small_r, half_thick],
            [small_bore_r, half_thick],
        ],
        360.0,
        [center[0] + small_x, center[1], center[2]],
        Axis::Z,
    )?;

    let delta_ratio = (big_r - small_r) / length;
    let tangent_y = (1.0 - delta_ratio * delta_ratio).sqrt();
    let radial_big = big_r - (shank_w / 2.0).min((big_r - big_bore_r) * 0.8);
    let radial_small = small_r - (shank_w / 2.0).min((small_r - small_bore_r) * 0.8);
    let inset = 0.25;
    let web_profile = [
        [
            big_x + (radial_big - inset) * delta_ratio,
            (radial_big - inset) * tangent_y,
        ],
        [
            small_x + (radial_small - inset) * delta_ratio,
            (radial_small - inset) * tangent_y,
        ],
        [
            small_x + (radial_small - inset) * delta_ratio,
            -(radial_small - inset) * tangent_y,
        ],
        [
            big_x + (radial_big - inset) * delta_ratio,
            -(radial_big - inset) * tangent_y,
        ],
    ];
    // Keep web coordinates local to center; unlike the legacy implementation,
    // do not pre-translate the profile and then translate it again as a prism.
    let web = kernel.prism(
        &web_profile,
        thick,
        [center[0], center[1], center[2] - half_thick],
        Axis::Z,
    )?;
    let result = kernel.fuse(&[&big_ring, &small_ring, &web])?;
    let analysis = result.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "连杆必须是单一有效闭合实体；当前 valid={}, solids={}, closed={}",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    let details = json!({
        "kind":"conrod",
        "status":"approximate",
        "geometry_support":"approximate",
        "builder":"analytic_annular_ends_with_external_tangent_web",
        "center_mm":center,
        "big_end_center_mm":[center[0] + big_x, center[1], center[2]],
        "small_end_center_mm":[center[0] + small_x, center[1], center[2]],
        "big_d_mm":big_d,
        "small_d_mm":small_d,
        "big_bore_mm":big_bore,
        "small_bore_mm":small_bore,
        "shank_width_mm":shank_w,
        "thickness_mm":thick,
        "warnings":["杆身采用外公切梯形棱柱近似；未表达工字截面、锻造过渡及倒角。"]
    });
    Ok((vec![result], details))
}

pub fn preflight_spoked_wheel(parameters: &Value) -> Result<(), String> {
    if let Some(axis) = parameters.get("axis") {
        if axis.as_str() != Some("Z") {
            return Err("辐条轮只支持沿全局 Z 轴建模。".into());
        }
    }
    let outer_r = number(parameters, "outer_r")?;
    let rim_w = number(parameters, "rim_w")?;
    let hub_r = number(parameters, "hub_r")?;
    let height = number(parameters, "height")?;
    let spoke_w = number(parameters, "spoke_w")?;
    if [outer_r, rim_w, hub_r, height, spoke_w]
        .iter()
        .any(|value| *value <= 0.0)
    {
        return Err("辐条轮尺寸必须为正。".into());
    }
    let spokes = parameters
        .get("spokes")
        .and_then(Value::as_u64)
        .filter(|count| *count >= 2)
        .ok_or("辐条轮至少需要两根整数辐条。")?;
    if spokes > MAX_SPOKED_WHEEL_SPOKES {
        return Err(format!(
            "辐条轮辐条数超过当前原生几何资源上限 {MAX_SPOKED_WHEEL_SPOKES}。"
        ));
    }
    let inner_r = outer_r - rim_w;
    if inner_r <= hub_r {
        return Err("辐条轮轮缘内半径必须大于轮毂半径。".into());
    }
    if spoke_w >= 2.0 * hub_r {
        return Err("辐条轮辐条宽度必须小于轮毂直径。".into());
    }
    let center = point3(parameters.get("center"))?;
    if center.iter().any(|coordinate| !coordinate.is_finite()) {
        return Err("辐条轮 center 必须是有限数值。".into());
    }
    let radial_start = hub_r - 0.05_f64.max(0.25_f64.min(rim_w * 0.05));
    let radial_length = outer_r - radial_start;
    let bore_r = 1.0_f64.max(hub_r * 0.35);
    if !radial_start.is_finite()
        || !radial_length.is_finite()
        || radial_length <= 0.0
        || !bore_r.is_finite()
        || bore_r >= hub_r
    {
        return Err("辐条轮尺寸组合无法形成有效轮缘、轮毂或辐条。".into());
    }
    Ok(())
}

pub fn build_spoked_wheel(
    kernel: &Kernel,
    parameters: &Value,
) -> Result<(Vec<Shape>, Value), String> {
    preflight_spoked_wheel(parameters)?;
    let outer_r = number(parameters, "outer_r")?;
    let rim_w = number(parameters, "rim_w")?;
    let hub_r = number(parameters, "hub_r")?;
    let height = number(parameters, "height")?;
    let spoke_w = number(parameters, "spoke_w")?;
    let spokes = parameters["spokes"].as_u64().ok_or("spokes 必须是整数。")?;
    let center = point3(parameters.get("center"))?;
    let inner_r = outer_r - rim_w;
    let hub_bore_r = 1.0_f64.max(hub_r * 0.35);
    let half_height = height / 2.0;

    // Match the legacy Python OCP profile exactly: a hollow annular rim and
    // annular hub, both centered on the feature center along global Z.
    let rim = kernel.revolve(
        &[
            [inner_r, -half_height],
            [outer_r, -half_height],
            [outer_r, half_height],
            [inner_r, half_height],
        ],
        360.0,
        center,
        Axis::Z,
    )?;
    let hub = kernel.revolve(
        &[
            [hub_bore_r, -half_height],
            [hub_r, -half_height],
            [hub_r, half_height],
            [hub_bore_r, half_height],
        ],
        360.0,
        center,
        Axis::Z,
    )?;

    let radial_start = hub_r - 0.05_f64.max(0.25_f64.min(rim_w * 0.05));
    let radial_length = outer_r - radial_start;
    let mid = radial_start + radial_length / 2.0;
    let mut parts = Vec::with_capacity(usize::try_from(spokes).unwrap_or(0) + 2);
    parts.push(rim);
    parts.push(hub);
    for index in 0..spokes {
        let bar = kernel.make_box(
            [radial_length, spoke_w, height],
            [center[0] + mid, center[1], center[2]],
        )?;
        let angle = 360.0 * index as f64 / spokes as f64;
        let matrix = rotation_about_z(center, angle)?;
        parts.push(kernel.transform(&bar, matrix)?);
    }
    let references = parts.iter().collect::<Vec<_>>();
    let result = kernel.fuse(&references)?;
    let analysis = result.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "辐条轮必须是单一有效封闭实体；当前 valid={}, solids={}, closed={}",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    let details = json!({
        "kind":"spoked_wheel",
        "status":"exact",
        "geometry_support":"exact",
        "builder":"analytic_annular_rim_hub_and_rotated_prismatic_spokes",
        "axis":"Z",
        "center_mm":center,
        "outer_radius_mm":outer_r,
        "rim_inner_radius_mm":inner_r,
        "hub_radius_mm":hub_r,
        "hub_bore_radius_mm":hub_bore_r,
        "spoke_count":spokes,
        "spoke_width_mm":spoke_w,
        "warnings":[],
        "limits":{"max_spokes":MAX_SPOKED_WHEEL_SPOKES,"catia":"approximate"}
    });
    Ok((vec![result], details))
}

fn rotation_about_z(center: [f64; 3], angle_degrees: f64) -> Result<[f64; 16], String> {
    if !angle_degrees.is_finite() || center.iter().any(|coordinate| !coordinate.is_finite()) {
        return Err("辐条轮旋转参数必须为有限数值。".into());
    }
    let angle = angle_degrees.to_radians();
    let (sin, cos) = angle.sin_cos();
    let tx = center[0] - cos * center[0] + sin * center[1];
    let ty = center[1] - sin * center[0] - cos * center[1];
    Ok([
        cos, -sin, 0.0, tx, sin, cos, 0.0, ty, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0,
    ])
}

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
        .ok_or_else(|| format!("几何参数 {key} 必须是有限数值。"))
}

fn integer(parameters: &Value, key: &str) -> Result<u64, String> {
    parameters.get(key).and_then(Value::as_u64)
        .ok_or_else(|| format!("几何参数 {key} 必须是非负整数。"))
}

fn optional_number(parameters: &Value, key: &str, default: f64) -> Result<f64, String> {
    match parameters.get(key) {
        None | Some(Value::Null) => Ok(default),
        Some(value) => value.as_f64().filter(|v| v.is_finite())
            .ok_or_else(|| format!("几何参数 {key} 必须是有限数值。")),
    }
}

fn point3(value: Option<&Value>) -> Result<[f64; 3], String> {
    let Some(value) = value.filter(|value| !value.is_null()) else {
        return Ok([0.0; 3]);
    };
    let values = value
        .as_array()
        .filter(|values| values.len() == 3)
        .ok_or("center 必须是全局 XYZ 三维数组。")?;
    let mut point = [0.0; 3];
    for (index, value) in values.iter().enumerate() {
        point[index] = value
            .as_f64()
            .filter(|coordinate| coordinate.is_finite())
            .ok_or("center 必须是有限数值。")?;
    }
    Ok(point)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn camshaft_preflight_accepts_buildable_variants_and_rejects_invalid_dimensions() {
        assert!(preflight_camshaft(&json!({"length":80,"journal_d":12,"base_d":20,"lift":4,"lobes":4,"lobe_deg":15,"center":[2,-3,4]})).is_ok());
        assert!(preflight_camshaft(&json!({"length":80,"journal_d":0.5,"base_d":20,"lift":4,"lobes":4})).is_ok());
        for invalid in [
            json!({"length":1,"journal_d":12,"base_d":20,"lift":4,"lobes":4}),
            json!({"length":80,"journal_d":0,"base_d":20,"lift":4,"lobes":4}),
            json!({"length":80,"journal_d":12,"base_d":20,"lift":-1,"lobes":4}),
            json!({"length":80,"journal_d":12,"base_d":20,"lift":4,"lobes":513}),
            json!({"length":80,"journal_d":12,"base_d":20,"lift":4,"lobes":4,"center":[0,0,"NaN"]}),
        ] {
            assert!(preflight_camshaft(&invalid).is_err(), "accepted {invalid}");
        }
    }

    #[test]
    fn crankshaft_preflight_enforces_dimensions_and_resource_bounds() {
        let valid = json!({"length":320,"main_d":50,"pin_d":42,"stroke":86,
            "throws":4,"center":[12,-7,5]});
        assert!(preflight_crankshaft(&valid).is_ok());
        for invalid in [
            json!({"length":0,"main_d":50,"pin_d":42,"stroke":86,"throws":4}),
            json!({"length":320,"main_d":0,"pin_d":42,"stroke":86,"throws":4}),
            json!({"length":320,"main_d":50,"pin_d":42,"stroke":-1,"throws":4}),
            json!({"length":320,"main_d":50,"pin_d":42,"stroke":86,"throws":0}),
            json!({"length":320,"main_d":50,"pin_d":42,"stroke":86,"throws":129}),
        ] {
            assert!(preflight_crankshaft(&invalid).is_err(), "accepted {invalid}");
        }
        assert!(preflight_crankshaft(&json!({"length":320,"main_d":50,"pin_d":42,
            "stroke":86,"throws":4,"segments":3})).is_ok());
    }

    #[test]
    fn cross_joint_preflight_rejects_missing_or_nonpositive_dimensions() {
        let valid = json!({"span":40,"journal_d":12,"center":[12,-7,5]});
        assert!(preflight_cross_joint(&valid).is_ok());
        for key in ["span", "journal_d"] {
            let mut missing = valid.clone();
            missing.as_object_mut().unwrap().remove(key);
            assert!(preflight_cross_joint(&missing).is_err(), "missing {key}");
        }
        for (key, value) in [("span", 0.0), ("journal_d", -1.0)] {
            let mut invalid = valid.clone();
            invalid[key] = json!(value);
            assert!(preflight_cross_joint(&invalid)
                .unwrap_err()
                .contains("必须为正数"));
        }
        let mut invalid_center = valid;
        invalid_center["center"] = json!([0, "bad", 0]);
        assert!(preflight_cross_joint(&invalid_center)
            .unwrap_err()
            .contains("有限数值"));
    }

    #[test]
    fn conrod_preflight_rejects_missing_or_impossible_required_geometry() {
        let valid = json!({
            "length":80,"big_d":30,"small_d":18,"big_bore":20,
            "small_bore":10,"shank_w":10,"thick":8,"center":[20,-11,7]
        });
        assert!(preflight_conrod(&valid).is_ok());
        for key in [
            "length",
            "big_d",
            "small_d",
            "big_bore",
            "small_bore",
            "shank_w",
            "thick",
        ] {
            let mut missing = valid.clone();
            missing.as_object_mut().unwrap().remove(key);
            assert!(
                preflight_conrod(&missing).is_err(),
                "missing {key} must be rejected"
            );
        }
        let mut bad_bore = valid.clone();
        bad_bore["big_bore"] = json!(30);
        assert!(
            preflight_conrod(&bad_bore)
                .unwrap_err()
                .contains("孔径必须小于")
        );
        let mut too_short = valid.clone();
        too_short["length"] = json!(5);
        assert!(preflight_conrod(&too_short).unwrap_err().contains("外公切"));
        let mut non_finite = valid.clone();
        non_finite["shank_w"] = json!(f64::INFINITY);
        assert!(
            preflight_conrod(&non_finite)
                .unwrap_err()
                .contains("有限数值")
        );
    }

    #[test]
    fn spoked_wheel_preflight_validates_profile_dimensions_and_resource_bounds() {
        let mut parameters = json!({
            "outer_r":20,"rim_w":4,"hub_r":6,"height":8,
            "spokes":5,"spoke_w":3,"center":[12,-7,5]
        });
        assert!(preflight_spoked_wheel(&parameters).is_ok());

        parameters["rim_w"] = json!(15);
        assert!(
            preflight_spoked_wheel(&parameters)
                .unwrap_err()
                .contains("轮缘内半径")
        );
        parameters["rim_w"] = json!(4);
        parameters["spoke_w"] = json!(12);
        assert!(
            preflight_spoked_wheel(&parameters)
                .unwrap_err()
                .contains("轮毂直径")
        );
        parameters["spoke_w"] = json!(3);
        parameters["spokes"] = json!(1);
        assert!(
            preflight_spoked_wheel(&parameters)
                .unwrap_err()
                .contains("至少需要两根")
        );
        parameters["spokes"] = json!(MAX_SPOKED_WHEEL_SPOKES + 1);
        assert!(
            preflight_spoked_wheel(&parameters)
                .unwrap_err()
                .contains("资源上限")
        );
    }

    #[test]
    fn spoked_wheel_rotation_is_about_the_global_feature_center() {
        let center = [10.0, -5.0, 5.0];
        let matrix = rotation_about_z(center, 90.0).unwrap();
        let point = [center[0] + 2.0, center[1], center[2]];
        let transformed = [
            matrix[0] * point[0] + matrix[1] * point[1] + matrix[3],
            matrix[4] * point[0] + matrix[5] * point[1] + matrix[7],
            matrix[8] * point[0] + matrix[9] * point[1] + matrix[10] * point[2] + matrix[11],
        ];
        assert!((transformed[0] - center[0]).abs() < 1e-12);
        assert!((transformed[1] - (center[1] + 2.0)).abs() < 1e-12);
        assert!((transformed[2] - center[2]).abs() < 1e-12);
    }

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
        assert!(
            preflight_piston(&parameters)
                .unwrap_err()
                .contains("必须为正")
        );
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
