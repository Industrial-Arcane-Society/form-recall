//! Deterministic native standard-feature builders backed by OCCT primitives
//! and validated faceted solids. Discrete teeth/threads are approximate.
use crate::native_occt::{Axis, Kernel, Shape};
use serde_json::{Value, json};

pub const MIN_GEAR_TEETH: u64 = 6;
pub const MAX_GEAR_TEETH: u64 = 10_000; // 8-point profile stays under the OCCT 100k point guard.

/// Build supported standard-feature tool shapes and return an explicit
/// geometry support report. `parameters` must already be in millimetres/degrees.
pub fn tools(
    kernel: &Kernel,
    kind: &str,
    parameters: &Value,
) -> Result<(Vec<Shape>, Value), String> {
    let p = parameters
        .as_object()
        .ok_or("standard feature parameters must be an object")?;
    match kind {
        "gear" => build_gear(kernel, p),
        "helical_gear" => build_helical_gear(kernel, p),
        "socket_head_bolt" => build_socket_head_bolt(kernel, p),
        "hex_bolt" => build_hex_bolt(kernel, p),
        _ => Err(format!(
            "native standard feature is not implemented: {kind}"
        )),
    }
}

fn build_hex_bolt(
    kernel: &Kernel,
    p: &serde_json::Map<String, Value>,
) -> Result<(Vec<Shape>, Value), String> {
    let d = number(p, "shank_d", 0.0)?;
    let pitch = number(p, "thread_pitch", 0.0)?;
    let length = number(p, "length", 0.0)?;
    let across_flats = number(p, "across_flats", 0.0)?;
    let head_h = number(p, "head_h", 0.0)?;
    if [d, pitch, length, across_flats, head_h]
        .iter()
        .any(|value| !value.is_finite() || *value <= 0.0)
    {
        return Err("hex_bolt dimensions and thread_pitch must be finite and positive".into());
    }
    let center = point3(p.get("center"))?;
    let segments = integer(p, "segments", 48)?.clamp(32, 48) as usize;
    let outer_r = across_flats / 3.0_f64.sqrt();
    let head_profile: Vec<[f64; 2]> = (0..6)
        .map(|index| {
            let angle = (60.0 * index as f64).to_radians();
            [outer_r * angle.cos(), outer_r * angle.sin()]
        })
        .collect();
    let head = kernel.prism(&head_profile, head_h, center, Axis::Z)?;
    // Keep the legacy head-at-bottom / shank-up convention. A 0.01 mm
    // overlap makes the face-faceted threaded mesh fuse into one solid while
    // keeping the requested distal length and overall envelope unchanged.
    let mesh = threaded_cylinder(
        d,
        pitch,
        length + 0.01,
        [center[0], center[1], center[2] + head_h - 0.01],
        segments,
    )?;
    let triangle_count = mesh.positions.len() / 3;
    let shank = kernel.triangle_mesh_to_solid(&mesh, 1e-5)?;
    let solid = kernel.fuse(&[&head, &shank])?;
    let analysis = solid.analyze()?;
    if analysis.valid == 0 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "hex_bolt must be one valid closed solid; got valid={}, solids={}, closed={}",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    Ok((
        vec![solid],
        json!({"kind":"hex_bolt","status":"approximate","builder":"analytic_hex_head_with_faceted_thread_brep",
            "thread":{"type":"ISO_metric_external_helical_facets","major_diameter_mm":d,"pitch_mm":pitch,"length_mm":length,
                "circumferential_segments":segments,"triangle_count":triangle_count,"visible_geometry":true},
            "warnings":["helical thread is a faceted approximation, not an exact analytic helix"]}),
    ))
}

fn build_socket_head_bolt(
    kernel: &Kernel,
    p: &serde_json::Map<String, Value>,
) -> Result<(Vec<Shape>, Value), String> {
    let d = number(p, "shank_d", 8.0)?;
    let pitch = number(p, "thread_pitch", 0.0)?;
    let length = number(p, "length", 25.0)?;
    let head_d = number(p, "head_d", 13.0)?;
    let head_h = number(p, "head_h", 8.0)?;
    let hex_s = number(p, "hex_s", 6.0)?;
    let hex_t = number(p, "hex_t", 4.0)?;
    if [d, pitch, length, head_d, head_h, hex_s, hex_t]
        .iter()
        .any(|v| *v <= 0.0)
        || hex_t > head_h
    {
        return Err("socket_head_bolt dimensions and thread_pitch must be positive; hex_t must not exceed head_h".into());
    }
    let center = point3(p.get("center"))?;
    let segments = integer(p, "segments", 48)?.clamp(32, 48) as usize;
    let head = kernel.cylinder(head_d * 0.5, head_h, center, [0.0, 0.0, 1.0])?;
    let hex_r = hex_s / 3.0_f64.sqrt();
    let socket_profile: Vec<[f64; 2]> = (0..6)
        .map(|i| {
            let angle = (60.0 * i as f64 + 30.0).to_radians();
            [hex_r * angle.cos(), hex_r * angle.sin()]
        })
        .collect();
    // The head occupies center.z..center.z+head_h and the shank attaches at
    // the +Z face. The socket belongs on the opposite, exposed end face and
    // cuts inward along +Z; placing it at the shank junction lets the shank
    // cover the opening.
    let socket = kernel.prism(
        &socket_profile,
        hex_t + 0.02,
        [center[0], center[1], center[2] - 0.01],
        Axis::Z,
    )?;
    let head = kernel.cut(&head, &[&socket])?;
    // Overlap the shank into the head by 0.01 mm for a robust fuse while
    // keeping its far end at the requested nominal length.
    let mesh = threaded_cylinder(
        d,
        pitch,
        length + 0.01,
        [center[0], center[1], center[2] + head_h - 0.01],
        segments,
    )?;
    let triangle_count = mesh.positions.len() / 3;
    let shank = kernel.triangle_mesh_to_solid(&mesh, 1e-5)?;
    let solid = kernel.fuse(&[&head, &shank])?;
    let analysis = solid.analyze()?;
    if analysis.valid == 0 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "socket_head_bolt must be one valid closed solid; got valid={}, solids={}, closed={}",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    Ok((
        vec![solid],
        json!({"kind":"socket_head_bolt","status":"approximate","builder":"analytic_socket_head_with_faceted_thread_brep",
            "thread":{"type":"ISO_metric_external_helical_facets","major_diameter_mm":d,"pitch_mm":pitch,"length_mm":length,
                "circumferential_segments":segments,"triangle_count":triangle_count,"visible_geometry":true},
            "warnings":["helical thread is a faceted approximation, not an exact analytic helix"]}),
    ))
}

fn threaded_cylinder(
    major_d: f64,
    pitch: f64,
    length: f64,
    center: [f64; 3],
    nt: usize,
) -> Result<crate::native_occt::TriangleMesh, String> {
    if [major_d, pitch, length]
        .iter()
        .any(|v| !v.is_finite() || *v <= 0.0)
    {
        return Err("thread major diameter, pitch and length must be finite and positive".into());
    }
    if !(24..=48).contains(&nt) {
        return Err("thread circumferential segments must be 24..=48".into());
    }
    let turns = length / pitch;
    let nz = (turns * 12.0)
        .ceil()
        .max(12.0)
        .min((80_000 / (2 * nt)) as f64) as usize;
    let major_r = major_d * 0.5;
    let depth = (0.61343 * pitch).min(major_r * 0.45);
    let minor_r = major_r - depth;
    let mut positions = Vec::with_capacity((2 * nz * nt + 2 * nt) * 3);
    let point = |iz: usize, it: usize| -> [f64; 3] {
        let z = length * iz as f64 / nz as f64;
        let theta = 2.0 * std::f64::consts::PI * it as f64 / nt as f64;
        let phase = z / pitch - theta / (2.0 * std::f64::consts::PI);
        let q = (phase + 0.5).rem_euclid(1.0) - 0.5;
        let aq = q.abs();
        let rise = if aq <= 1.0 / 16.0 {
            1.0
        } else if aq >= 0.36 {
            0.0
        } else {
            (0.36 - aq) / (0.36 - 1.0 / 16.0)
        };
        let radius = minor_r + depth * rise;
        [
            center[0] + radius * theta.cos(),
            center[1] + radius * theta.sin(),
            center[2] + z,
        ]
    };
    let mut triangle = |a: [f64; 3], b: [f64; 3], c: [f64; 3]| {
        positions.extend_from_slice(&a);
        positions.extend_from_slice(&b);
        positions.extend_from_slice(&c);
    };
    for iz in 0..nz {
        for it in 0..nt {
            let next = (it + 1) % nt;
            let a = point(iz, it);
            let b = point(iz, next);
            let c = point(iz + 1, next);
            let d = point(iz + 1, it);
            triangle(a, b, c);
            triangle(a, c, d);
        }
    }
    let bottom = [center[0], center[1], center[2]];
    let top = [center[0], center[1], center[2] + length];
    for it in 0..nt {
        let next = (it + 1) % nt;
        triangle(bottom, point(0, next), point(0, it));
        triangle(top, point(nz, it), point(nz, next));
    }
    let positions: Vec<[f64; 3]> = positions
        .chunks_exact(3)
        .map(|p| [p[0], p[1], p[2]])
        .collect();
    if positions.len() / 3 > 100_000 {
        return Err("thread tessellation exceeds native bridge face limit".into());
    }
    Ok(crate::native_occt::TriangleMesh {
        positions,
        normals: Vec::new(),
    })
}

fn build_gear(
    kernel: &Kernel,
    p: &serde_json::Map<String, Value>,
) -> Result<(Vec<Shape>, Value), String> {
    let teeth = integer(p, "teeth", 20)?;
    if !(MIN_GEAR_TEETH..=MAX_GEAR_TEETH).contains(&teeth) {
        return Err(format!(
            "gear teeth must be {MIN_GEAR_TEETH}..={MAX_GEAR_TEETH} (OCCT profile resource limit)"
        ));
    }
    let module = number(p, "module", 2.0)?;
    let height = number(p, "height", 10.0)?;
    if module <= 0.0 || height <= 0.0 {
        return Err("gear module and height must be positive".into());
    }
    let center = point3(p.get("center"))?;
    let axis = match p
        .get("axis")
        .and_then(Value::as_str)
        .unwrap_or("Z")
        .to_ascii_uppercase()
        .as_str()
    {
        "X" => Axis::X,
        "Y" => Axis::Y,
        "Z" => Axis::Z,
        _ => return Err("gear axis must be X, Y or Z".into()),
    };
    let base = match p.get("base") {
        None => true,
        Some(v) => v.as_bool().ok_or("gear base must be boolean")?,
    };
    let profile = gear_profile(teeth as usize, module);
    let origin = if base {
        center
    } else {
        let i = axis as usize;
        let mut o = center;
        o[i] -= height * 0.5;
        o
    };
    let shape = kernel.prism(&profile, height, origin, axis)?;
    let support = json!({"kind":"gear","status":"approximate","builder":"polygonal_tooth_profile_occt_prism",
        "tooth_profile":"legacy 8-point trapezoidal radial profile; not an involute tooth flank","teeth":teeth,"profile_points":profile.len(),
        "module_mm":module,"height_mm":height,"axis":format!("{:?}",axis),"base_semantics":if base{"start_face_at_center"}else{"symmetric_about_center"},
        "warnings":["polygonal tooth-flank geometry is an approximation; do not mark exact"]});
    Ok((vec![shape], support))
}

/// Build the retained CADPlan -> Document helical_gear semantics: a straight,
/// centered trapezoidal gear prism. The legacy Document builder ignores
/// `helix_deg`, adds no implicit bore, and facets an explicitly requested bore.
fn build_helical_gear(
    kernel: &Kernel,
    p: &serde_json::Map<String, Value>,
) -> Result<(Vec<Shape>, Value), String> {
    let teeth = integer(p, "teeth", 20)?;
    if !(MIN_GEAR_TEETH..=MAX_GEAR_TEETH).contains(&teeth) {
        return Err(format!(
            "helical_gear teeth must be {MIN_GEAR_TEETH}..={MAX_GEAR_TEETH}"
        ));
    }
    let module = number(p, "module", 2.0)?;
    let height = number(p, "height", 10.0)?;
    // The retained CADPlan -> Document -> OCP path deliberately ignores helix_deg.
    let helix_deg = number(p, "helix_deg", 18.0)?;
    let bore_d = number(p, "bore_d", 0.0)?;
    if module <= 0.0 || height <= 0.0 || bore_d < 0.0 {
        return Err("helical_gear module/height must be positive and bore_d nonnegative".into());
    }
    let center = point3(p.get("center"))?;
    let mut origin = center;
    origin[2] -= height * 0.5;
    let profile = gear_profile(teeth as usize, module);
    let blank = kernel.prism(&profile, height, origin, Axis::Z)?;
    let (shape, bore_support) = if bore_d > 0.0 {
        let bore = circle_profile(bore_d * 0.5, 40);
        let cutter = kernel.prism(
            &bore,
            height + 0.02,
            [center[0], center[1], origin[2] - 0.01],
            Axis::Z,
        )?;
        (
            kernel.cut(&blank, &[&cutter])?,
            json!({
                "kind":"polygonal_through_bore", "diameter_mm":bore_d, "profile_points":40,
                "source":"explicit bore_d; no implicit bore is added"
            }),
        )
    } else {
        (blank, json!({"kind":"none", "source":"bore_d=0"}))
    };
    let analysis = shape.analyze()?;
    if analysis.valid == 0 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err(format!(
            "helical_gear must be one valid closed solid; got valid={}, solids={}, closed={}",
            analysis.valid, analysis.solid_count, analysis.closed_solid_count
        ));
    }
    Ok((
        vec![shape],
        json!({
            "kind":"helical_gear", "status":"approximate", "builder":"legacy_straight_trapezoidal_gear_prism",
            "teeth":teeth, "module_mm":module, "height_mm":height, "center_mm":center,
            "helix_deg_input":helix_deg, "helix_deg_applied":0.0, "bore":bore_support,
            "warnings":["The retained CADPlan/Document geometry path omits helix_deg and builds a straight trapezoidal gear; no implicit bore is added. CATIA export is also approximate and does not preserve helix angle."]
        }),
    ))
}

fn circle_profile(radius: f64, segments: usize) -> Vec<[f64; 2]> {
    (0..segments)
        .map(|i| {
            let angle = 2.0 * std::f64::consts::PI * i as f64 / segments as f64;
            [radius * angle.cos(), radius * angle.sin()]
        })
        .collect()
}

/// Match `core.geometry.kernel.profile_gear`: trapezoidal teeth sampled at
/// eight radial points per tooth, extruded by the native OCCT prism builder.
fn gear_profile(teeth: usize, module: f64) -> Vec<[f64; 2]> {
    let z = teeth.max(6);
    let rp = module * z as f64 / 2.0;
    let ra = rp + module;
    let rf = (rp - 1.25 * module).max(1.0);
    let half = std::f64::consts::PI / (2.0 * z as f64);
    let offsets = [
        (-half * 1.55, rf),
        (-half * 1.15, rf + 0.15 * module),
        (-half * 0.95, rp * 0.99),
        (-half * 0.48, ra),
        (half * 0.48, ra),
        (half * 0.95, rp * 0.99),
        (half * 1.15, rf + 0.15 * module),
        (half * 1.55, rf),
    ];
    let mut points = Vec::with_capacity(z * 8);
    for i in 0..z {
        let a0 = 2.0 * std::f64::consts::PI * i as f64 / z as f64;
        for (da, r) in offsets {
            let a = a0 + da;
            points.push([r * a.cos(), r * a.sin()]);
        }
    }
    points
}
fn number(p: &serde_json::Map<String, Value>, key: &str, default: f64) -> Result<f64, String> {
    let x = match p.get(key) {
        None => default,
        Some(v) => v
            .as_f64()
            .ok_or_else(|| format!("{key} must be a number"))?,
    };
    if !x.is_finite() {
        return Err(format!("{key} must be finite"));
    }
    Ok(x)
}
fn integer(p: &serde_json::Map<String, Value>, key: &str, default: u64) -> Result<u64, String> {
    let x = match p.get(key) {
        None => default,
        Some(v) => v
            .as_u64()
            .ok_or_else(|| format!("{key} must be a positive integer"))?,
    };
    Ok(x)
}
fn point3(v: Option<&Value>) -> Result<[f64; 3], String> {
    let Some(v) = v else { return Ok([0.; 3]) };
    let a = v
        .as_array()
        .filter(|a| a.len() == 3)
        .ok_or("center must contain three coordinates")?;
    let mut p = [0.; 3];
    for i in 0..3 {
        p[i] = a[i]
            .as_f64()
            .filter(|n| n.is_finite())
            .ok_or("center coordinates must be finite numbers")?
    }
    Ok(p)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn legacy_gear_profile_uses_eight_samples_per_tooth_and_nominal_radii() {
        let p = gear_profile(20, 2.0);
        assert_eq!(p.len(), 160);
        let r = p.iter().map(|v| v[0].hypot(v[1])).fold(0.0, f64::max);
        assert!((r - 22.0).abs() < 1e-10);
    }
    #[test]
    fn helical_gear_circle_profile_uses_forty_facets_for_explicit_bores() {
        let bore = circle_profile(4.0, 40);
        assert_eq!(bore.len(), 40);
        assert!((bore[0][0] - 4.0).abs() < 1e-12);
        assert!(bore.iter().all(|p| (p[0].hypot(p[1]) - 4.0).abs() < 1e-12));
    }

    #[test]
    fn guard_helpers_reject_invalid_resource_and_nonfinite_values() {
        let p = json!({"teeth":10001,"module":2,"height":10})
            .as_object()
            .unwrap()
            .clone();
        assert!(integer(&p, "teeth", 20).unwrap() > MAX_GEAR_TEETH);
        let p = json!({"module":"two"}).as_object().unwrap().clone();
        assert!(number(&p, "module", 2.).is_err());
        assert!(point3(Some(&json!([0, 1, f64::NAN]))).is_err());
    }

    #[test]
    fn threaded_shank_has_legacy_iso_radial_profile_and_bounded_mesh() {
        let mesh = threaded_cylinder(8.0, 1.25, 20.02, [0.0; 3], 48).unwrap();
        let radii: Vec<f64> = mesh
            .positions
            .iter()
            .filter(|p| p[2] > 0.0 && p[2] < 20.02)
            .map(|p| p[0].hypot(p[1]))
            .collect();
        let lo = radii.iter().copied().fold(f64::INFINITY, f64::min);
        let hi = radii.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        assert!((hi - 4.0).abs() < 1e-10);
        assert!(lo < 4.0 - 0.2);
        assert!(lo >= 4.0 - 0.61343 * 1.25 - 1e-10);
        assert!(mesh.positions.len() / 3 <= 100_000);
        assert_eq!(mesh.positions.len() % 3, 0);
    }

    #[test]
    fn thread_generator_rejects_invalid_dimensions_and_resolution() {
        assert!(threaded_cylinder(8.0, 0.0, 20.0, [0.0; 3], 48).is_err());
        assert!(threaded_cylinder(8.0, 1.0, 20.0, [0.0; 3], 16).is_err());
        assert!(threaded_cylinder(8.0, 1.0, 20.0, [0.0; 3], 24).is_ok());
    }
}
