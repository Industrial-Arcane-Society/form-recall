//! Exercise sampled OCCT material evidence on synthetic B-Rep fixtures only.
use ai_modeler_ui_lib::{
    native_geometry_evidence::{self, HoleRequest},
    native_occt::{Kernel, Shape},
};
use serde_json::json;
use std::{env, fs, path::PathBuf};

fn cut_one(kernel: &Kernel, stock: &Shape, radius: f64, base: [f64; 3]) -> Result<Shape, String> {
    let tool = kernel.cylinder(radius, 20.0, base, [0.0, 0.0, 1.0])?;
    kernel.cut(stock, &[&tool])
}

fn request(x: f64, y: f64, radius: f64, tmax: f64) -> HoleRequest {
    HoleRequest {
        origin: [x, y, -5.0],
        axis: [0.0, 0.0, 1.0],
        radius,
        tmin: 0.0,
        tmax,
        tolerance: 1e-7,
    }
}

fn main() -> Result<(), String> {
    let args: Vec<_> = env::args().skip(1).collect();
    let manifest = PathBuf::from(
        args.first()
            .ok_or("usage: native_geometry_evidence_probe RUNTIME_MANIFEST OUTPUT_JSON")?,
    );
    let output = PathBuf::from(args.get(1).ok_or("missing output JSON path")?);
    let kernel = Kernel::load_manifest(&manifest)?;

    // Deterministic OCCT fixture: 100x80x20 stock with nine through bores.
    // It does not load a user image, Agent output, or a supplied CAD plan.
    let stock = kernel.make_box([100.0, 80.0, 20.0], [0.0, 0.0, 10.0])?;
    let mut tools = Vec::new();
    let xs = [-30.0, 0.0, 30.0];
    let ys = [-25.0, 0.0, 25.0];
    let radii = [5.0, 3.0, 5.0];
    for y in ys {
        for (column, x) in xs.into_iter().enumerate() {
            tools.push(kernel.cylinder(radii[column], 20.0, [x, y, 0.0], [0.0, 0.0, 1.0])?);
        }
    }
    let tool_refs: Vec<_> = tools.iter().collect();
    let nine_hole_part = kernel.cut(&stock, &tool_refs)?;
    let mut nine_hole_reports = Vec::new();
    for y in ys {
        for (column, x) in xs.into_iter().enumerate() {
            let report = native_geometry_evidence::verify_hole(
                &stock,
                &nine_hole_part,
                &request(x, y, radii[column], 30.0),
            )?;
            if report["accepted"] != true || report["summary"]["through_on_sampled_rays"] != true {
                return Err(format!(
                    "correct through-hole fixture did not pass at ({x},{y}): {report}"
                ));
            }
            nine_hole_reports
                .push(json!({"center_xy":[x,y],"nominal_radius":radii[column],"evidence":report}));
        }
    }

    // Blind-hole fixture leaves 10 mm of stock under a 10 mm deep bore.
    let blind_post = cut_one(&kernel, &stock, 4.0, [15.0, 10.0, 10.0])?;
    let blind_report = native_geometry_evidence::verify_hole(
        &stock,
        &blind_post,
        &request(15.0, 10.0, 4.0, 30.0),
    )?;
    if blind_report["accepted"] != true
        || blind_report["summary"]["through_on_sampled_rays"] != false
        || blind_report["summary"]["center_remaining_material"] != true
    {
        return Err(format!(
            "blind-hole fixture did not report remaining material: {blind_report}"
        ));
    }

    // Negative fixtures: a misplaced bore and a bore smaller than requested.
    let misplaced_post = cut_one(&kernel, &stock, 5.0, [3.0, 0.0, 0.0])?;
    let misplaced_report = native_geometry_evidence::verify_hole(
        &stock,
        &misplaced_post,
        &request(0.0, 0.0, 5.0, 30.0),
    )?;
    if misplaced_report["accepted"] == true {
        return Err(format!(
            "misplaced-hole negative fixture was accepted: {misplaced_report}"
        ));
    }
    let wrong_diameter_post = cut_one(&kernel, &stock, 4.0, [0.0, 0.0, 0.0])?;
    let wrong_diameter_report = native_geometry_evidence::verify_hole(
        &stock,
        &wrong_diameter_post,
        &request(0.0, 0.0, 5.0, 30.0),
    )?;
    if wrong_diameter_report["accepted"] == true {
        return Err(format!(
            "wrong-diameter negative fixture was accepted: {wrong_diameter_report}"
        ));
    }

    let report = json!({
        "status": "passed",
        "occt_version": kernel.version(),
        "fixture_source": "synthetic_rust_occt_brep_only",
        "user_image_loaded": false,
        "agent_output_injected": false,
        "hole_measurements_verified": false,
        "evidence_kind": "sampled_geometry_evidence",
        "scope": "pre-cut versus post-cut material intervals on center, 24 interior, and 16 outside-radius rays per request; not fitted diameter metrology",
        "nine_through_holes": nine_hole_reports,
        "blind_hole": blind_report,
        "misplaced_hole_negative": misplaced_report,
        "wrong_diameter_negative": wrong_diameter_report,
    });
    let text = serde_json::to_string_pretty(&report).map_err(|e| e.to_string())?;
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    fs::write(&output, text.as_bytes()).map_err(|e| e.to_string())?;
    println!(
        "passed: nine through holes={}, blind remaining material={}, misplaced rejected={}, wrong diameter rejected={}; full report: {}",
        nine_hole_reports.len(),
        blind_report["summary"]["center_remaining_material"],
        misplaced_report["accepted"] == false,
        wrong_diameter_report["accepted"] == false,
        output.display()
    );
    Ok(())
}
