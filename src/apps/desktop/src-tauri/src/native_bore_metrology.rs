//! Analytic B-Rep cylindrical-face measurements for requested bores.
//! Face radii come from OCCT's GeomAbs_Cylinder surfaces. Material sampling
//! independently checks removal/remaining material; it does not fit a radius.
use crate::{
    native_geometry_evidence::{self, HoleRequest},
    native_occt::Shape,
};
use serde_json::{Value, json};

#[derive(Clone, Copy)]
enum BoreEvidenceMode {
    Ordinary,
    Counterbore { pilot_radius: f64 },
    ThroughPilot { counterbore_radius: f64, counterbore_depth: f64 },
}

fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
fn scale(a: [f64; 3], s: f64) -> [f64; 3] {
    [a[0] * s, a[1] * s, a[2] * s]
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

/// Scan OCCT cylindrical faces and report faces coincident with the requested
/// bore axis and radius. Multiple trimmed faces are grouped by unioning their
/// projected axial spans; a face row is never equated with a bore count.
pub fn measure_bore(
    pre: &Shape,
    post: &Shape,
    request: &HoleRequest,
    expected_blind_depth: Option<f64>,
) -> Result<Value, String> {
    measure_bore_with_evidence(pre, post, request, expected_blind_depth, BoreEvidenceMode::Ordinary)
}

pub fn measure_counterbore(
    pre: &Shape,
    post: &Shape,
    request: &HoleRequest,
    pilot_radius: f64,
    expected_depth: f64,
) -> Result<Value, String> {
    measure_bore_with_evidence(
        pre,
        post,
        request,
        Some(expected_depth),
        BoreEvidenceMode::Counterbore { pilot_radius },
    )
}

pub fn measure_through_pilot_with_counterbore(
    pre: &Shape,
    post: &Shape,
    request: &HoleRequest,
    counterbore_radius: f64,
    counterbore_depth: f64,
) -> Result<Value, String> {
    measure_bore_with_evidence(
        pre,
        post,
        request,
        None,
        BoreEvidenceMode::ThroughPilot {
            counterbore_radius,
            counterbore_depth,
        },
    )
}

fn measure_bore_with_evidence(
    pre: &Shape,
    post: &Shape,
    request: &HoleRequest,
    expected_blind_depth: Option<f64>,
    evidence_mode: BoreEvidenceMode,
) -> Result<Value, String> {
    let axis_len = norm(request.axis);
    if !axis_len.is_finite()
        || axis_len <= 1e-12
        || !request.radius.is_finite()
        || request.radius <= 0.0
        || !request.tolerance.is_finite()
        || request.tolerance <= 0.0
        || !request.tmin.is_finite()
        || !request.tmax.is_finite()
        || request.tmin >= request.tmax
        || request.origin.iter().any(|v| !v.is_finite())
    {
        return Err("孔计量请求的轴、半径、范围或公差无效。".into());
    }
    let axis = scale(request.axis, 1.0 / axis_len);
    let mut candidates = Vec::new();
    let mut measured_radii = Vec::new();
    let mut matched_faces = Vec::new();
    let mut angular_intervals = Vec::new();
    let mut matched_face_count = 0usize;
    let mut rejected = Vec::new();
    for face in post.cylindrical_faces()? {
        let alignment = dot(face.axis_direction, axis).abs();
        let delta = sub(face.axis_origin, request.origin);
        let axis_offset = norm(cross(delta, axis));
        let point_delta = sub(face.surface_point, request.origin);
        let radial = sub(point_delta, scale(axis, dot(point_delta, axis)));
        let inward = dot(face.oriented_normal, radial) < 0.0;
        let radius_error = (face.radius - request.radius).abs();
        let accepted = alignment >= 1.0 - 1e-8
            && axis_offset <= request.tolerance * 10.0
            && radius_error <= request.tolerance * 10.0
            && inward;
        let row = json!({
            "actual_radius": face.radius,
            "axis_origin": face.axis_origin,
            "axis_direction": face.axis_direction,
            "axial_parameter_span": face.axial_span,
            "angular_parameter_span_radians": face.angular_span,
            "surface_point": face.surface_point,
            "oriented_normal": face.oriented_normal,
            "axis_alignment_abs_cosine": alignment,
            "axis_offset": axis_offset,
            "radius_error": radius_error,
            "inward_facing": inward,
            "accepted_as_requested_bore_face": accepted
        });
        if accepted {
            matched_face_count += 1;
            measured_radii.push(face.radius);
            angular_intervals.push(face.angular_span);
            matched_faces.push(row);
            let vaxis = dot(face.axis_direction, axis);
            let a = dot(sub(face.axis_origin, request.origin), axis) + face.axial_span[0] * vaxis;
            let b = dot(sub(face.axis_origin, request.origin), axis) + face.axial_span[1] * vaxis;
            candidates.push([a.min(b), a.max(b)]);
        } else {
            rejected.push(row);
        }
    }
    candidates.sort_by(|a, b| a[0].total_cmp(&b[0]));
    let mut merged: Vec<[f64; 2]> = Vec::new();
    for span in candidates {
        if let Some(last) = merged.last_mut() {
            if span[0] <= last[1] + request.tolerance * 10.0 {
                last[1] = last[1].max(span[1]);
                continue;
            }
        }
        merged.push(span);
    }
    angular_intervals.sort_by(|a, b| a[0].total_cmp(&b[0]));
    let mut merged_angles: Vec<[f64; 2]> = Vec::new();
    for span in angular_intervals {
        if let Some(last) = merged_angles.last_mut() {
            if span[0] <= last[1] + 1e-10 {
                last[1] = last[1].max(span[1]);
                continue;
            }
        }
        merged_angles.push(span);
    }
    let angular_coverage_radians: f64 = merged_angles.iter().map(|span| span[1] - span[0]).sum();
    if expected_blind_depth.is_some_and(|depth| !depth.is_finite() || depth <= 0.0) {
        return Err("盲孔预期深度必须为正有限数。".into());
    }
    let sampled_material = match evidence_mode {
        BoreEvidenceMode::Ordinary => native_geometry_evidence::verify_hole(pre, post, request)?,
        BoreEvidenceMode::Counterbore { pilot_radius } => {
            native_geometry_evidence::verify_counterbore(
                pre,
                post,
                request,
                pilot_radius,
                expected_blind_depth.ok_or("沉孔核验缺少预期深度。")?,
            )?
        }
        BoreEvidenceMode::ThroughPilot { counterbore_radius, .. } => {
            native_geometry_evidence::verify_through_pilot_with_counterbore(
                pre,
                post,
                request,
                counterbore_radius,
            )?
        }
    };
    let measured_axial_length: f64 = merged.iter().map(|span| span[1] - span[0]).sum();
    let expected_material_axial_length = sampled_material["center_ray"]["before_material_length"]
        .as_f64()
        .unwrap_or(0.0);
    let axial_tolerance = (request.tolerance * 10.0).max(1e-5);
    let expected_bore_axial_length = match evidence_mode {
        BoreEvidenceMode::ThroughPilot { counterbore_depth, .. } => {
            Some(expected_material_axial_length - counterbore_depth)
        }
        _ => None,
    };
    let expectation_satisfied = match expected_blind_depth {
        Some(depth) if matches!(evidence_mode, BoreEvidenceMode::Counterbore { .. }) => {
            (measured_axial_length - depth).abs() <= axial_tolerance
                && sampled_material["accepted"] == true
                && sampled_material["summary"]["annular_cut_samples_pass"] == true
                && sampled_material["summary"]["outside_material_retained"] == true
        }
        Some(depth) => {
            (measured_axial_length - depth).abs() <= axial_tolerance
                && sampled_material["summary"]["center_remaining_material"] == true
                && sampled_material["summary"]["through_on_sampled_rays"] == false
        }
        None if matches!(evidence_mode, BoreEvidenceMode::ThroughPilot { .. }) => {
            expected_bore_axial_length.is_some_and(|expected| {
                expected > 0.0
                    && (measured_axial_length - expected).abs() <= axial_tolerance
            }) && sampled_material["accepted"] == true
                && sampled_material["summary"]["through_on_sampled_rays"] == true
        }
        None => {
            sampled_material["summary"]["through_on_sampled_rays"] == true
                && (measured_axial_length - expected_material_axial_length).abs() <= axial_tolerance
        }
    };
    Ok(json!({
        "status": "cylindrical_face_metrology",
        "accepted": !merged.is_empty() && sampled_material["accepted"] == true && expectation_satisfied,
        "measurement_method": "OCCT BRepAdaptor_Surface GeomAbs_Cylinder analytic radius; axis and trimmed UV bounds from B-Rep; inward orientation checked at UV midpoint",
        "tolerance": request.tolerance,
        "requested_radius": request.radius,
        "evidence_mode": match evidence_mode {
            BoreEvidenceMode::Ordinary => "ordinary_bore",
            BoreEvidenceMode::Counterbore { .. } => "coaxial_counterbore_annulus",
            BoreEvidenceMode::ThroughPilot { .. } => "through_pilot_with_counterbore",
        },
        "expected_blind_depth": expected_blind_depth,
        "expected_pre_cut_material_axial_length": expected_material_axial_length,
        "expected_bore_axial_length": expected_bore_axial_length,
        "axial_length_tolerance": axial_tolerance,
        "measured_cylindrical_axial_length": measured_axial_length,
        "expectation_satisfied": expectation_satisfied,
        "actual_radius_measurements": measured_radii,
        "matched_cylindrical_face_count": matched_face_count,
        "merged_axial_span_count": merged.len(),
        "merged_axial_spans": merged,
        "matched_cylindrical_faces": matched_faces,
        "merged_angular_parameter_spans_radians": merged_angles,
        "angular_coverage_radians": angular_coverage_radians,
        "unmatched_cylindrical_faces": rejected,
        "pre_post_material_evidence": sampled_material,
        "limits": ["trimmed face spans may be split by seams or booleans and are merged by projected axial overlap", "partial angular coverage is reported per face; this routine does not prove a complete 360-degree bore", "pre/post ray evidence is separate from analytic radius measurement"]
    }))
}
