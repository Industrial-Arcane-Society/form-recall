use ai_modeler_ui_lib::{native_geometry_backend, native_plate_learning};
use serde_json::{Value, json};
use std::{fs, path::PathBuf, sync::atomic::AtomicBool};

fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 2 {
        return Err("用法：plate_layout_demo <OCCT manifest> <新的输出目录>".into());
    }
    let manifest = PathBuf::from(&args[0])
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let out = PathBuf::from(&args[1]);
    fs::create_dir(&out).map_err(|e| e.to_string())?;
    let out = out.canonicalize().map_err(|e| e.to_string())?;
    let group = "现场安装板";
    let target = [130.0, 110.0, 14.0];
    let target_diameter = 10.0;
    let guess =
        || native_plate_learning::build_transfer(&out, &manifest, group, target, target_diameter);
    let cold = guess().expect_err("cold run must not invent a layout");
    let inputs = [
        ([100.0, 70.0, 12.0], 8.0, [12.0, 10.5], "sample-a"),
        ([160.0, 90.0, 16.0], 9.0, [12.0, 13.5], "sample-b"),
    ];
    let mut learned = Vec::new();
    let mut artifacts: Vec<Value> = Vec::new();
    let mut few_shot = String::new();
    for (i, (size, diameter, margins, label)) in inputs.into_iter().enumerate() {
        let plan = native_plate_learning::plan_for_plate(size, diameter, margins, label)?;
        let outputs = native_geometry_backend::build_preview(
            &plan,
            1,
            1,
            &manifest,
            &out.join(label),
            &AtomicBool::new(false),
        )?
        .outcome
        .output_paths;
        let case = native_plate_learning::learn(&out, &manifest, group, &plan)?;
        learned.push(case);
        artifacts.push(outputs);
        if i == 0 {
            few_shot = guess().expect_err("one example must not select a rule");
        }
    }
    let result = guess()?;
    let evidence = &result["plate_layout_learning"];
    assert_eq!(evidence["rules"], json!(["固定边距", "按跨度比例"]));
    let measured = &evidence["measured_margins_mm"];
    assert!((measured[0].as_f64().unwrap() - 12.0).abs() < 1e-7);
    assert!((measured[1].as_f64().unwrap() - 16.5).abs() < 1e-7);
    let report = &evidence["geometry_report"];
    let volume = 130.0 * 110.0 * 14.0 - 4.0 * std::f64::consts::PI * 5.0_f64.powi(2) * 14.0;
    assert!((report["volume_mm3"].as_f64().unwrap() - volume).abs() < 1e-3);
    assert_eq!(report["hole_instances"].as_array().unwrap().len(), 4);
    let obstructed =
        native_plate_learning::build_transfer(&out, &manifest, group, [18.0, 110.0, 14.0], 10.0)
            .expect_err("must reject intersecting or out-of-bounds holes");
    let output_paths = &result["output_paths"];
    for key in [
        "aimodel",
        "stl",
        "glb",
        "preview",
        "geometry_report",
        "cadplan",
    ] {
        let path = PathBuf::from(output_paths[key].as_str().ok_or("缺少产物路径")?);
        let target = out.join(format!(
            "transfer-plate.{}",
            path.extension().unwrap().to_string_lossy()
        ));
        if key != "cadplan" {
            fs::copy(&path, target).map_err(|e| e.to_string())?;
        } else {
            fs::copy(&path, out.join("transfer-cadplan.json")).map_err(|e| e.to_string())?;
        }
    }
    let summary = json!({"cold_refusal":cold,"one_sample_refusal":few_shot,"learned_samples":learned,
        "train_artifacts":artifacts,"unseen_target_mm":target,"unseen_target_diameter_mm":target_diameter,
        "transfer":evidence,"target_artifacts":output_paths,"bad_dimensions_refusal":obstructed,
        "analytic_expected_volume_mm3":volume});
    fs::write(
        out.join("verification.json"),
        serde_json::to_vec_pretty(&summary).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    println!("OCCT 比对通过。产物：{}", out.display());
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
