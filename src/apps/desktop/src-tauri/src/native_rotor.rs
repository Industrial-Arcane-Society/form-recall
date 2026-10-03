//! F4.2.9: explicit-size, approximate legacy rotor profile to native OCCT B-Rep.
//! The fixed radial recipe is NOT an exact Reuleaux or engine design curve.
use crate::native_occt::{Axis, Kernel, Shape};
use serde_json::{Value, json};
use std::f64::consts::{PI, TAU};

const FLANK: f64 = 0.82;
const MAX_POINTS: u64 = 4096;

fn number(p: &Value, key: &str) -> Result<f64, String> {
    p[key]
        .as_f64()
        .filter(|v| v.is_finite())
        .ok_or_else(|| format!("rotor.{key} 必须显式提供有限数值；不猜默认尺寸。"))
}
fn center(p: &Value) -> Result<[f64; 3], String> {
    match p.get("center") {
        None => Ok([0.; 3]),
        Some(value) => {
            let rows = value
                .as_array()
                .filter(|r| r.len() == 3)
                .ok_or("rotor.center 必须是三个有限XYZ数值；省略时只选坐标原点。")?;
            let mut result = [0.; 3];
            for i in 0..3 {
                result[i] = rows[i]
                    .as_f64()
                    .filter(|v| v.is_finite())
                    .ok_or("rotor.center 必须是三个有限XYZ数值。")?;
            }
            Ok(result)
        }
    }
}

/// Equivalent to retained Python kernel.profile_rotor(R,n,flank=0.82).
fn profile(radius: f64, n: usize) -> Vec<[f64; 2]> {
    (0..n)
        .map(|i| {
            let angle = TAU * i as f64 / n as f64;
            let radial = (0..3)
                .map(|vertex| {
                    let tip = PI / 2. + TAU * vertex as f64 / 3.;
                    radius * (FLANK + (1. - FLANK) * (angle - tip).cos().max(0.).powi(6))
                })
                .fold(0., f64::max);
            [radial * angle.cos(), radial * angle.sin()]
        })
        .collect()
}
fn area(points: &[[f64; 2]]) -> f64 {
    points
        .iter()
        .zip(points.iter().cycle().skip(1))
        .take(points.len())
        .map(|(a, b)| a[0] * b[1] - a[1] * b[0])
        .sum::<f64>()
        / 2.
}
/// Actual minimum distance from the origin to the polygon segments, rather
/// than trusting the smooth radial recipe's 0.82R at low discretization.
fn clearance(points: &[[f64; 2]]) -> f64 {
    points
        .iter()
        .zip(points.iter().cycle().skip(1))
        .take(points.len())
        .map(|(a, b)| {
            let edge = [b[0] - a[0], b[1] - a[1]];
            let t = (-(a[0] * edge[0] + a[1] * edge[1]) / (edge[0] * edge[0] + edge[1] * edge[1]))
                .clamp(0., 1.);
            (a[0] + t * edge[0]).hypot(a[1] + t * edge[1])
        })
        .fold(f64::INFINITY, f64::min)
}

pub fn preflight(p: &Value) -> Result<(), String> {
    if p.get("axis").is_some_and(|v| v.as_str() != Some("Z")) || p.get("base").is_some() {
        return Err("rotor 仅支持全局Z轴与center几何中点；不接受base语义。".into());
    }
    let radius = number(p, "radius")?;
    let height = number(p, "height")?;
    let bore = number(p, "bore_d")?;
    if radius <= 0. || height <= 0. || bore < 0. {
        return Err("rotor radius/height须为正，bore_d须非负（0表示明确无孔）。".into());
    }
    let n = p["n"]
        .as_u64()
        .filter(|n| (12..=MAX_POINTS).contains(n))
        .ok_or("rotor.n 必须显式为12..=4096整数；不截断或静默夹紧。")? as usize;
    let points = profile(radius, n);
    let clear = clearance(&points);
    if !clear.is_finite() || bore / 2. >= clear {
        return Err("rotor孔径触及或穿越实际离散轮廓；须严格保留孔壁。".into());
    }
    let expected = (area(&points) - PI * (bore / 2.).powi(2)) * height;
    let c = center(p)?;
    if !expected.is_finite()
        || expected <= 0.
        || c.iter().any(|v| !(*v + radius + height).is_finite())
    {
        return Err("rotor派生面积/体积或坐标溢出，不能生成有限实体。".into());
    }
    Ok(())
}

pub fn build(kernel: &Kernel, p: &Value) -> Result<(Vec<Shape>, Value), String> {
    preflight(p)?;
    let radius = number(p, "radius")?;
    let height = number(p, "height")?;
    let bore = number(p, "bore_d")?;
    let n = p["n"].as_u64().unwrap() as usize;
    let c = center(p)?;
    let points = profile(radius, n);
    let bottom = [c[0], c[1], c[2] - height / 2.];
    let prism = kernel.prism(&points, height, bottom, Axis::Z)?;
    // Cutting at the exact body end planes removes no hidden dimensional
    // padding or guessed drilling depth; the kernel must validate this cut.
    let shape = if bore > 0. {
        let cutter = kernel.cylinder(bore / 2., height, bottom, [0., 0., 1.])?;
        kernel.cut(&prism, &[&cutter])?
    } else {
        prism
    };
    let analysis = shape.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err("rotor原生构造未得到单一有效闭合B-Rep。".into());
    }
    let expected = (area(&points) - PI * (bore / 2.).powi(2)) * height;
    let volume = shape.volume()?;
    if (volume - expected).abs() > 1e-6_f64.max(expected.abs() * 1e-9) {
        return Err(format!(
            "rotor B-Rep体积与离散轮廓柱体减解析孔的体积不符：{volume} vs {expected}"
        ));
    }
    Ok((
        vec![shape],
        json!({"status":"approximate","builder":"legacy_radial_polygon_prism_with_analytic_bore",
        "axis":"Z","center_is_geometric_midpoint":true,"profile_points":n,
        "profile_recipe":{"flank":FLANK,"cosine_exponent":6},
        "polygon_area_mm2":area(&points),"profile_bore_clearance_mm":clearance(&points),
        "bore_d_mm":bore,"expected_polygon_volume_mm3":expected,"actual_brep_volume_mm3":volume,
        "note":"Polygonal legacy rounded triangle, not exact Reuleaux/engine rotor; central bore is an analytic cylindrical cut, not independent metrology or CATIA acceptance."}),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{native_feature_specs, native_plan_geometry};
    use std::{path::PathBuf, sync::atomic::AtomicBool};
    fn parameters() -> Value {
        json!({"radius":20,"n":48,"height":8,"bore_d":4,"center":[12,-7,5]})
    }
    fn plan(p: Value, unit: &str) -> Value {
        let sources:serde_json::Map<String,Value>=p.as_object().unwrap().keys()
            .filter(|k| k.as_str()!="axis" && k.as_str()!="base")
            .map(|k|(k.clone(),json!({"type":"user","evidence":"deterministic geometry fixture, not Agent image input"}))).collect();
        json!({"schema_version":"1.0","name":"rotor-fixture","source_prompt":"Explicit rotor geometry fixture",
            "units":{"length":unit,"angle":"deg"},"coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},
            "features":[{"id":"rotor","kind":"rotor","operation":"add","parameters":p,"sources":sources}],"pending_questions":[]})
    }
    #[test]
    fn rotor_rejects_missing_sizes_invalid_counts_clearance_and_orientation() {
        let valid = parameters();
        assert!(preflight(&valid).is_ok());
        for key in ["radius", "n", "height", "bore_d"] {
            let mut p = valid.clone();
            p.as_object_mut().unwrap().remove(key);
            assert!(preflight(&p).is_err(), "missing {key}");
            assert!(native_plan_geometry::preflight(&plan(p, "mm")).is_err());
        }
        for (key, value) in [
            ("radius", json!(0)),
            ("height", json!(-1)),
            ("bore_d", json!(-1)),
            ("bore_d", json!(40)),
            ("n", json!(11)),
            ("n", json!(4097)),
            ("n", json!(48.5)),
            ("center", json!([1, "bad", 3])),
            ("center", json!(null)),
            ("axis", json!("X")),
            ("base", json!(true)),
            ("radius", json!(1e200)),
        ] {
            let mut p = valid.clone();
            p[key] = value;
            assert!(preflight(&p).is_err(), "invalid {key}: {p}");
            assert!(native_plan_geometry::preflight(&plan(p, "mm")).is_err());
        }
        let mut p = valid;
        p["n"] = json!(12);
        let threshold = clearance(&profile(20., 12)) * 2.;
        p["bore_d"] = json!(threshold);
        assert!(preflight(&p).is_err());
    }
    #[test]
    fn rotor_capability_is_approximate_and_fixed_recipe_has_explicit_samples() {
        let spec = native_feature_specs::feature_spec("rotor").unwrap();
        assert_eq!(
            spec["native_execution"]["status"],
            "implemented_approximate"
        );
        assert_eq!(spec["native_execution"]["geometry_support"], "approximate");
        let points = profile(20., 12);
        assert!(points[3][0].abs() < 1e-12);
        assert!((points[3][1] - 20.).abs() < 1e-12);
        assert!(
            (points[0][0] - 20. * (0.82 + 0.18 * 0.8660254037844386_f64.powi(6))).abs() < 1e-12
        );
    }
    #[test]
    fn rotor_real_occt_plan_has_valid_brep_correct_bounds_volume_translation_and_cm_units() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let manifest = root.join("target/f4-occt/runtime-manifest.json");
        assert!(
            manifest.is_file(),
            "real OCCT runtime required for this acceptance; no skipped pass"
        );
        let cases = [
            (
                "solid_origin",
                json!({"radius":20,"n":48,"height":8,"bore_d":0}),
                "mm",
            ),
            (
                "bored_origin",
                json!({"radius":20,"n":48,"height":8,"bore_d":4}),
                "mm",
            ),
            ("bored_translated", parameters(), "mm"),
            (
                "bored_cm",
                json!({"radius":2,"n":48,"height":0.8,"bore_d":0.4,"center":[1.2,-0.7,0.5]}),
                "cm",
            ),
            (
                "coarse_profile",
                json!({"radius":20,"n":12,"height":8,"bore_d":4}),
                "mm",
            ),
        ];
        let mut reports = Vec::new();
        let mut origin_bore_volume = 0.;
        for (name, p, unit) in cases {
            let built = native_plan_geometry::build(
                &plan(p.clone(), unit),
                &manifest,
                &AtomicBool::new(false),
            )
            .unwrap();
            let analysis = built.shape.analyze().unwrap();
            assert_eq!(
                (
                    analysis.valid,
                    analysis.solid_count,
                    analysis.closed_solid_count
                ),
                (1, 1, 1)
            );
            let factor = if unit == "cm" { 10. } else { 1. };
            let radius = p["radius"].as_f64().unwrap() * factor;
            let h = p["height"].as_f64().unwrap() * factor;
            let bore = p["bore_d"].as_f64().unwrap() * factor;
            let c = center(&p).unwrap().map(|v| v * factor);
            let n = p["n"].as_u64().unwrap() as usize;
            let points = profile(radius, n);
            let bb = built.shape.bounds().unwrap();
            let actual = [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax];
            let expected = [
                points.iter().map(|v| v[0]).fold(f64::INFINITY, f64::min) + c[0],
                points.iter().map(|v| v[1]).fold(f64::INFINITY, f64::min) + c[1],
                c[2] - h / 2.,
                points
                    .iter()
                    .map(|v| v[0])
                    .fold(f64::NEG_INFINITY, f64::max)
                    + c[0],
                points
                    .iter()
                    .map(|v| v[1])
                    .fold(f64::NEG_INFINITY, f64::max)
                    + c[1],
                c[2] + h / 2.,
            ];
            for i in 0..6 {
                assert!((actual[i] - expected[i]).abs() < 1e-6, "{name} bbox[{i}]");
            }
            let volume = built.shape.volume().unwrap();
            let expected_volume = (area(&points) - PI * (bore / 2.).powi(2)) * h;
            assert!((volume - expected_volume).abs() < 1e-5, "{name} volume");
            let lower = (0.5 * n as f64 * (FLANK * radius).powi(2) * (TAU / n as f64).sin()
                - PI * (bore / 2.).powi(2))
                * h;
            let upper = (PI * radius.powi(2) - PI * (bore / 2.).powi(2)) * h;
            assert!(
                volume >= lower - 1e-5 && volume <= upper + 1e-5,
                "{name} independent area bounds"
            );
            let axial_material = built
                .shape
                .line_material_intervals(c, [0., 0., 1.], -h / 2. - 1., h / 2. + 1., 1e-7)
                .unwrap();
            if bore > 0. {
                assert!(
                    axial_material.is_empty(),
                    "{name} central bore must remove axial material"
                );
            } else {
                assert_eq!(axial_material.len(), 1);
                assert!((axial_material[0][0] + h / 2.).abs() < 1e-6);
                assert!((axial_material[0][1] - h / 2.).abs() < 1e-6);
            }
            if name == "bored_origin" {
                origin_bore_volume = volume;
            }
            if name == "bored_translated" || name == "bored_cm" {
                assert!((volume - origin_bore_volume).abs() < 1e-5);
            }
            assert_eq!(
                built.report["feature_results"][0]["geometry_support"],
                "approximate"
            );
            reports.push(json!({"case":name,"status":"passed","bounds_mm":actual,"expected_bounds_mm":expected,
                "volume_mm3":volume,"expected_polygon_volume_mm3":expected_volume,"volume_boundary_mm3":[lower,upper],
                "valid":analysis.valid,"solid_count":analysis.solid_count,"closed_solid_count":analysis.closed_solid_count,"python_used":false,
                "axial_material_intervals_mm":axial_material,"scope":"material existence, not fitted bore diameter metrology"}));
        }
        let output = root.join("target/f4-occt/rotor-acceptance.json");
        std::fs::write(
            &output,
            serde_json::to_vec_pretty(&json!({"status":"passed","geometry_support":"approximate",
            "scope":"deterministic_geometry_fixture_not_Agent_or_CATIA","cases":reports}))
            .unwrap(),
        )
        .unwrap();
    }
}
