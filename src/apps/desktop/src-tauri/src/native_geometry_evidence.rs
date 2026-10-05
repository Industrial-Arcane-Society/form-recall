//! Sampled, B-Rep-backed material evidence for requested holes.
//!
//! The report describes the tested rays and does not claim a fitted diameter
//! or an independent metrology result.
use crate::native_occt::Shape;
use serde_json::{json, Value};

#[derive(Clone, Debug)]
pub struct HoleRequest {
    /// A point on the nominal hole axis at parameter t=0.
    pub origin: [f64; 3],
    /// Nominal axis; normalized for radial-offset construction and by the ABI.
    pub axis: [f64; 3],
    pub radius: f64,
    pub tmin: f64,
    pub tmax: f64,
    pub tolerance: f64,
}

#[derive(Clone, Debug)]
struct RaySample {
    label: String,
    origin: [f64; 3],
    before: Vec<[f64; 2]>,
    after: Vec<[f64; 2]>,
    before_length: f64,
    after_length: f64,
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
fn norm(a: [f64; 3]) -> f64 {
    dot(a, a).sqrt()
}
fn scale(a: [f64; 3], s: f64) -> [f64; 3] {
    [a[0] * s, a[1] * s, a[2] * s]
}
fn add(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}
fn interval_length(intervals: &[[f64; 2]]) -> f64 {
    intervals.iter().map(|v| (v[1] - v[0]).max(0.0)).sum()
}

fn sample_ray(
    before: &Shape,
    after: &Shape,
    request: &HoleRequest,
    origin: [f64; 3],
    label: String,
) -> Result<RaySample, String> {
    let before_intervals = before.line_material_intervals(
        origin,
        request.axis,
        request.tmin,
        request.tmax,
        request.tolerance,
    )?;
    let after_intervals = after.line_material_intervals(
        origin,
        request.axis,
        request.tmin,
        request.tmax,
        request.tolerance,
    )?;
    Ok(RaySample {
        label,
        origin,
        before_length: interval_length(&before_intervals),
        after_length: interval_length(&after_intervals),
        before: before_intervals,
        after: after_intervals,
    })
}

fn ray_json(sample: &RaySample, passed: bool, condition: &str) -> Value {
    json!({
        "sample": sample.label,
        "origin": sample.origin,
        "before_material_intervals": sample.before,
        "after_material_intervals": sample.after,
        "before_material_length": sample.before_length,
        "after_material_length": sample.after_length,
        "removed_material_length": (sample.before_length - sample.after_length).max(0.0),
        "condition": condition,
        "passed": passed,
    })
}

/// Compare a pre-cut and post-cut shape using center, interior radial, and
/// outside-radius rays parallel to the requested hole axis.
pub fn verify_hole(
    pre_cut: &Shape,
    post_cut: &Shape,
    request: &HoleRequest,
) -> Result<Value, String> {
    verify_hole_with_protected_radius(pre_cut, post_cut, request, request.radius)
}

/// Verify a through pilot after a larger coaxial blind counterbore was cut.
/// The protected rays are placed outside the counterbore diameter so that the
/// expected top-side relief does not invalidate the pilot-bore evidence.
pub fn verify_through_pilot_with_counterbore(
    pre_cut: &Shape,
    post_cut: &Shape,
    request: &HoleRequest,
    counterbore_radius: f64,
) -> Result<Value, String> {
    if !counterbore_radius.is_finite() || counterbore_radius <= request.radius {
        return Err("沉孔半径必须大于贯穿 pilot 半径。".into());
    }
    verify_hole_with_protected_radius(pre_cut, post_cut, request, counterbore_radius)
}

fn verify_hole_with_protected_radius(
    pre_cut: &Shape,
    post_cut: &Shape,
    request: &HoleRequest,
    protected_radius: f64,
) -> Result<Value, String> {
    if request
        .origin
        .iter()
        .chain(request.axis.iter())
        .any(|v| !v.is_finite())
        || !request.radius.is_finite()
        || request.radius <= 0.0
        || !request.tmin.is_finite()
        || !request.tmax.is_finite()
        || request.tmin >= request.tmax
        || !request.tolerance.is_finite()
        || request.tolerance <= 0.0
    {
        return Err("孔核验参数必须有限，半径/公差为正且轴向范围递增。".into());
    }
    let axis_len = norm(request.axis);
    if !axis_len.is_finite() || axis_len == 0.0 {
        return Err("孔核验轴方向必须非零且有限。".into());
    }
    let axis = scale(request.axis, 1.0 / axis_len);
    let reference = if axis[0].abs() < 0.8 {
        [1.0, 0.0, 0.0]
    } else {
        [0.0, 1.0, 0.0]
    };
    let u0 = cross(axis, reference);
    let u = scale(u0, 1.0 / norm(u0));
    let v = cross(axis, u);
    let normalized = HoleRequest {
        axis,
        ..request.clone()
    };

    let center = sample_ray(
        pre_cut,
        post_cut,
        &normalized,
        request.origin,
        "center".into(),
    )?;
    const ANGLES: usize = 8;
    let mut interior = Vec::new();
    for (ring_index, fraction) in [0.50, 0.80, 0.95].into_iter().enumerate() {
        for angle_index in 0..ANGLES {
            let angle = std::f64::consts::TAU * angle_index as f64 / ANGLES as f64;
            let radial = add(scale(u, angle.cos()), scale(v, angle.sin()));
            let offset = scale(radial, request.radius * fraction);
            interior.push(sample_ray(
                pre_cut,
                post_cut,
                &normalized,
                add(request.origin, offset),
                format!("interior_r{}_{:02}", ring_index + 1, angle_index),
            )?);
        }
    }
    if !protected_radius.is_finite() || protected_radius < request.radius {
        return Err("孔外侧保护半径必须有限且不小于孔半径。".into());
    }
    let outside_margin = (protected_radius * 0.05).max(request.tolerance * 8.0);
    let mut outside = Vec::new();
    for (ring_index, factor) in [1.0, 2.0].into_iter().enumerate() {
        for angle_index in 0..ANGLES {
            let angle = std::f64::consts::TAU * angle_index as f64 / ANGLES as f64;
            let radial = add(scale(u, angle.cos()), scale(v, angle.sin()));
            let offset = scale(radial, protected_radius + outside_margin * factor);
            outside.push(sample_ray(
                pre_cut,
                post_cut,
                &normalized,
                add(request.origin, offset),
                format!("outside_r{}_{:02}", ring_index + 1, angle_index),
            )?);
        }
    }

    let min_change = request.tolerance * 4.0;
    let material_present = |s: &RaySample| s.before_length > min_change;
    let center_pre_present = material_present(&center);
    let center_removed = center.before_length - center.after_length > min_change;
    let interior_pre_count = interior.iter().filter(|s| material_present(s)).count();
    let interior_removed_count = interior
        .iter()
        .filter(|s| material_present(s) && s.before_length - s.after_length > min_change)
        .count();
    let outside_pre_count = outside.iter().filter(|s| material_present(s)).count();
    let outside_retained_count = outside
        .iter()
        .filter(|s| {
            material_present(s)
                && s.after_length > min_change
                && (s.before_length - s.after_length).abs() <= min_change
        })
        .count();
    let center_remaining_material = center.after_length > min_change;
    let through = center_pre_present
        && center.after_length <= min_change
        && interior.iter().all(|s| s.after_length <= min_change);
    let sampled_checks_pass = center_pre_present
        && center_removed
        && interior_pre_count == interior.len()
        && interior_removed_count == interior.len()
        && outside_pre_count == outside.len()
        && outside_retained_count == outside.len();

    let center_report = ray_json(
        &center,
        center_pre_present && center_removed,
        "pre-cut material exists and post-cut material length decreases",
    );
    let interior_report: Vec<Value> = interior
        .iter()
        .map(|s| {
            ray_json(
                s,
                material_present(s) && s.before_length - s.after_length > min_change,
                "interior radial sample loses material",
            )
        })
        .collect();
    let outside_report: Vec<Value> = outside
        .iter()
        .map(|s| {
            ray_json(
                s,
                material_present(s)
                    && s.after_length > min_change
                    && (s.before_length - s.after_length).abs() <= min_change,
                "outside-radius sample retains its pre-cut material span",
            )
        })
        .collect();

    Ok(json!({
        "status": "sampled_geometry_evidence",
        "accepted": sampled_checks_pass,
        "judgement": if !sampled_checks_pass { "insufficient_or_conflicting_ray_evidence" }
                     else if through { "through_material_removed_on_sampled_rays" }
                     else if center_remaining_material { "remaining_material_on_center_ray" }
                     else { "material_removed_on_sampled_rays" },
        "measurement_scope": "sampled line material intervals; not a fitted or independent diameter measurement",
        "request": {
            "origin_xyz": request.origin,
            "axis_xyz_normalized": axis,
            "nominal_radius": request.radius,
            "outside_protected_radius": protected_radius,
            "interior_radius_fractions": [0.50, 0.80, 0.95],
            "outside_radius_offsets": [outside_margin, outside_margin * 2.0],
            "t_range": [request.tmin, request.tmax],
            "tolerance": request.tolerance,
        },
        "summary": {
            "center_pre_material_present": center_pre_present,
            "center_material_removed": center_removed,
            "center_remaining_material": center_remaining_material,
            "through_on_sampled_rays": through,
            "interior_rays_total": interior.len(),
            "interior_rays_with_pre_material": interior_pre_count,
            "interior_rays_with_material_removed": interior_removed_count,
            "outside_rays_total": outside.len(),
            "outside_rays_with_pre_material": outside_pre_count,
            "outside_rays_retaining_material": outside_retained_count,
        },
        "center_ray": center_report,
        "interior_rays": interior_report,
        "outside_rays": outside_report,
    }))
}

/// Verify the annular cut of a coaxial counterbore whose smaller through pilot
/// has already removed the centerline. Radial samples stay strictly between
/// the pilot and counterbore radii; the center ray is required to remain empty,
/// while an outer ring beyond the counterbore must retain unchanged material.
pub fn verify_counterbore(
    pre_cut: &Shape,
    post_cut: &Shape,
    request: &HoleRequest,
    pilot_radius: f64,
    expected_depth: f64,
) -> Result<Value, String> {
    let axis_len = norm(request.axis);
    if !axis_len.is_finite()
        || axis_len <= 1e-12
        || !request.radius.is_finite()
        || request.radius <= 0.0
        || !pilot_radius.is_finite()
        || pilot_radius <= 0.0
        || pilot_radius >= request.radius
        || !expected_depth.is_finite()
        || expected_depth <= 0.0
        || !request.tolerance.is_finite()
        || request.tolerance <= 0.0
        || !request.tmin.is_finite()
        || !request.tmax.is_finite()
        || request.tmin >= request.tmax
        || request.origin.iter().any(|v| !v.is_finite())
    {
        return Err("沉孔环带核验请求的轴、半径、深度、范围或公差无效。".into());
    }
    let axis = scale(request.axis, 1.0 / axis_len);
    let reference = if axis[0].abs() < 0.8 {
        [1.0, 0.0, 0.0]
    } else {
        [0.0, 1.0, 0.0]
    };
    let u0 = cross(axis, reference);
    let u = scale(u0, 1.0 / norm(u0));
    let v = cross(axis, u);
    let normalized = HoleRequest { axis, ..request.clone() };
    let minimum_change = request.tolerance * 8.0;
    let depth_tolerance = (request.tolerance * 20.0).max(expected_depth * 1e-5);

    let center = sample_ray(
        pre_cut,
        post_cut,
        &normalized,
        request.origin,
        "counterbore_centerline_expected_empty".into(),
    )?;
    let centerline_empty = center.before_length <= minimum_change
        && center.after_length <= minimum_change;

    const ANGLES: usize = 16;
    let annulus_width = request.radius - pilot_radius;
    let mut annular_samples = Vec::new();
    for (ring_index, fraction) in [0.2, 0.5, 0.8].into_iter().enumerate() {
        let sample_radius = pilot_radius + annulus_width * fraction;
        for angle_index in 0..ANGLES {
            let angle = std::f64::consts::TAU * angle_index as f64 / ANGLES as f64;
            let radial = add(scale(u, angle.cos()), scale(v, angle.sin()));
            let sample = sample_ray(
                pre_cut,
                post_cut,
                &normalized,
                add(request.origin, scale(radial, sample_radius)),
                format!("annulus_r{}_{:02}", ring_index + 1, angle_index),
            )?;
            let depth_matches = match (sample.before.as_slice(), sample.after.as_slice()) {
                ([before], [after]) => {
                    let retained_at_entry = (after[0] - before[0]).abs() <= depth_tolerance;
                    let removed_at_exit = ((before[1] - after[1]) - expected_depth).abs()
                        <= depth_tolerance;
                    retained_at_entry
                        && removed_at_exit
                        && after[1] > after[0] + minimum_change
                }
                _ => false,
            };
            annular_samples.push(json!({
                "sample": sample.label,
                "origin": sample.origin,
                "radius_from_axis": sample_radius,
                "before_material_intervals": sample.before,
                "after_material_intervals": sample.after,
                "before_material_length": sample.before_length,
                "after_material_length": sample.after_length,
                "removed_material_length": (sample.before_length - sample.after_length).max(0.0),
                "expected_depth_mm": expected_depth,
                "depth_tolerance_mm": depth_tolerance,
                "depth_matches_and_bottom_material_remains": depth_matches
            }));
        }
    }
    let annular_cut_samples_pass = annular_samples
        .iter()
        .all(|sample| sample["depth_matches_and_bottom_material_remains"] == true);

    let outside_margin = (request.radius * 0.05).max(request.tolerance * 8.0);
    let mut outside_samples = Vec::new();
    for ring_index in 0..2 {
        let sample_radius = request.radius + outside_margin * (ring_index as f64 + 1.0);
        for angle_index in 0..ANGLES {
            let angle = std::f64::consts::TAU * angle_index as f64 / ANGLES as f64;
            let radial = add(scale(u, angle.cos()), scale(v, angle.sin()));
            let sample = sample_ray(
                pre_cut,
                post_cut,
                &normalized,
                add(request.origin, scale(radial, sample_radius)),
                format!("outside_counterbore_r{}_{:02}", ring_index + 1, angle_index),
            )?;
            let material_retained = sample.before_length > minimum_change
                && sample.after_length > minimum_change;
            let unchanged = material_retained
                && sample.before.len() == sample.after.len()
                && sample
                    .before
                    .iter()
                    .zip(&sample.after)
                    .all(|(before, after)| {
                        (before[0] - after[0]).abs() <= depth_tolerance
                            && (before[1] - after[1]).abs() <= depth_tolerance
                    });
            outside_samples.push(json!({
                "sample": sample.label,
                "origin": sample.origin,
                "radius_from_axis": sample_radius,
                "before_material_intervals": sample.before,
                "after_material_intervals": sample.after,
                "before_material_length": sample.before_length,
                "after_material_length": sample.after_length,
                "material_retained": material_retained,
                "material_retained_unchanged": unchanged
            }));
        }
    }
    let outside_material_retained = outside_samples
        .iter()
        .all(|sample| sample["material_retained_unchanged"] == true);
    let accepted = centerline_empty && annular_cut_samples_pass && outside_material_retained;

    Ok(json!({
        "status": "sampled_counterbore_annulus_evidence",
        "accepted": accepted,
        "measurement_scope": "sampled annular material intervals; analytic B-Rep radius is reported separately",
        "request": {
            "origin_xyz": request.origin,
            "axis_xyz_normalized": axis,
            "pilot_radius_mm": pilot_radius,
            "counterbore_radius_mm": request.radius,
            "expected_depth_mm": expected_depth,
            "depth_tolerance_mm": depth_tolerance,
            "t_range": [request.tmin, request.tmax],
            "tolerance": request.tolerance
        },
        "summary": {
            "centerline_empty_before_and_after": centerline_empty,
            "annular_rays_total": annular_samples.len(),
            "annular_rays_matching_depth_with_bottom_material": annular_samples.iter().filter(|sample| sample["depth_matches_and_bottom_material_remains"] == true).count(),
            "outside_rays_total": outside_samples.len(),
            "outside_rays_unchanged": outside_samples.iter().filter(|sample| sample["material_retained_unchanged"] == true).count(),
            "annular_cut_samples_pass": annular_cut_samples_pass,
            "outside_material_retained": outside_material_retained
        },
        "centerline": ray_json(&center, centerline_empty, "pilot centerline is already empty before and after counterbore"),
        "annular_rays": annular_samples,
        "outside_rays": outside_samples
    }))
}
