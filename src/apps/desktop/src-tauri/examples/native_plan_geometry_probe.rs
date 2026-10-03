//! Execute externally supplied CADPlan with the native kernel; no Agent run.
use ai_modeler_ui_lib::native_plan_geometry;
use serde_json::Value;
use std::{env, fs, path::PathBuf, sync::atomic::AtomicBool};
fn main() -> Result<(), String> {
    let args: Vec<_> = env::args().skip(1).collect();
    if args.len() != 3 {
        return Err(
            "usage: native_plan_geometry_probe PLAN_JSON RUNTIME_MANIFEST REPORT_JSON".into(),
        );
    }
    let input = PathBuf::from(&args[0]);
    if fs::metadata(&input).map_err(|e| e.to_string())?.len() > 4 * 1024 * 1024 {
        return Err("CADPlan exceeds 4 MiB".into());
    }
    let bytes = fs::read(input).map_err(|e| e.to_string())?;
    let plan: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    let built =
        native_plan_geometry::build(&plan, &PathBuf::from(&args[1]), &AtomicBool::new(false))?;
    // Actual triangle generation is exercised separately from B-Rep checks.
    let mesh = built.shape.tessellate(0.1, 0.5)?;
    let mut report = built.report;
    report["triangle_count"] = Value::from(mesh.positions.len() / 3);
    let output = PathBuf::from(&args[2]);
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(&report).map_err(|e| e.to_string())?;
    fs::write(output, text.as_bytes()).map_err(|e| e.to_string())?;
    println!("{text}");
    Ok(())
}
