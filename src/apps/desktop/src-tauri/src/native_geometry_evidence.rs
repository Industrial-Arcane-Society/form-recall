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
    let outside_margin = (request.radius * 0.05).max(request.tolerance * 8.0);
    let mut outside = Vec::new();
    for (ring_index, factor) in [1.0, 2.0].into_iter().enumerate() {
        for angle_index in 0..ANGLES {
            let angle = std::f64::consts::TAU * angle_index as f64 / ANGLES as f64;
            let radial = add(scale(u, angle.cos()), scale(v, angle.sin()));
            let offset = scale(radial, request.radius + outside_margin * factor);
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
