//! F5.107 targeted validation runner. It intentionally defaults to one measured
//! profile; it never initializes OCCT or runs the whole geometry corpus.
//!
//! `cargo run --manifest-path src/apps/desktop/src-tauri/Cargo.toml --bin f5_107_egt_sw_validate -- EGT001:SW80:88 artifacts/f5.107-egt-sw`

use ai_modeler_ui_lib::{native_model_io, native_quick_egt_sw};
use serde_json::{Value, json};
use std::{env, fs, path::PathBuf};

fn run() -> Result<(), String> {
    let mut args = env::args().skip(1);
    let id = args.next().unwrap_or_else(|| "EGT001:SW80:88".into());
    let output = PathBuf::from(args.next().unwrap_or_else(|| "artifacts/f5.107-egt-sw".into()));
    if args.next().is_some() { return Err("usage: f5_107_egt_sw_validate [profile-id] [output-dir]".into()); }
    fs::create_dir_all(&output).map_err(|error| error.to_string())?;
    let catalog: Value = serde_json::from_str(include_str!("../../../../../core/cad-engine/src/native_quick_egt_sw_catalog.json"))
        .map_err(|error| error.to_string())?;
    let profile = catalog["profiles"].as_array()
        .and_then(|rows| rows.iter().find(|row| row["id"].as_str() == Some(id.as_str())))
        .ok_or_else(|| format!("unknown measured SW50/SW80 profile id: {id}"))?;
    let parameters = json!({
        "sec_a":profile["sec_a_mm"],"sec_b":profile["sec_b_mm"],"length":profile["length_mm"],
        "axis":profile["axis"],"base":profile["base_mm"],"holes":profile["holes"],
        "segments":24,"ref_min":profile["ref_min_mm"],"ref_size":profile["ref_size_mm"]
    });
    let mesh = native_quick_egt_sw::build(&parameters)?;
    let diagnostics = native_quick_egt_sw::geometry_diagnostics(&parameters, &mesh)?;
    let stl = output.join("rust_mesh.stl");
    let stats = native_model_io::write_binary_stl(&stl, &mesh, "F5.107 Rust legacy mesh")?;
    let parameter_path = output.join("rust_parameters.json");
    fs::write(&parameter_path, serde_json::to_vec_pretty(&parameters).map_err(|e|e.to_string())?)
        .map_err(|e|e.to_string())?;
    let report = json!({
        "profile_id":id,
        "legacy_semantics":"rectangular envelope + measured through-hole triangle mesh; not a real extrusion cross-section",
        "mesh_only":true,
        "brep_available":false,
        "catpart_available":false,
        "triangle_count":stats.triangle_count,
        "bounds_mm":stats.bounds,
        "signed_mesh_volume_mm3":stats.signed_volume,
        "diagnostics":diagnostics,
        "rust_stl":stl,
        "rust_parameters":parameter_path
    });
    let report_path = output.join("rust_report.json");
    fs::write(&report_path, serde_json::to_vec_pretty(&report).map_err(|e|e.to_string())?)
        .map_err(|e|e.to_string())?;
    println!("{}", serde_json::to_string(&report).map_err(|e|e.to_string())?);
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("F5.107 validation failed: {error}");
        std::process::exit(1);
    }
}
