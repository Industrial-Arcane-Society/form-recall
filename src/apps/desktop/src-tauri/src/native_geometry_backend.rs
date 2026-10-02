//! Version-bound local build/export. No Agent decisions or Python fallback.
use crate::{
    cad_plan,
    native_cad_session::BackendOutcome,
    native_model_io, native_plan_geometry,
    native_render::{self, RenderOptions, View},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::Path,
    sync::atomic::{AtomicBool, Ordering},
};

pub struct PreviewOutput {
    pub outcome: BackendOutcome,
    pub views: Vec<Value>,
}

pub fn build_preview(
    plan: &Value,
    revision: i64,
    generation: u64,
    manifest: &Path,
    session_dir: &Path,
    cancelled: &AtomicBool,
) -> Result<PreviewOutput, String> {
    if revision < 0 || generation == 0 {
        return Err("无效几何版本或预览 generation。".into());
    }
    let built = native_plan_geometry::build(plan, manifest, cancelled)?;
    let mesh = built.shape.tessellate(0.08, 0.35)?;
    check_cancel(cancelled)?;
    let signature = cad_plan::geometry_fingerprint(plan)?;
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let directory = session_dir
        .join("native-output")
        .join(format!("r{revision}-g{generation}-{signature}-{nonce}"));
    if directory.exists() {
        return Err("预览 generation 输出目录已存在；不能覆盖旧证据。".into());
    }
    fs::create_dir_all(directory.join("views")).map_err(|e| e.to_string())?;
    let project = native_model_io::cadplan_to_project(plan)?;
    let project_path = directory.join("candidate.aimodel");
    let stl_path = directory.join("candidate.stl");
    let glb_path = directory.join("candidate.glb");
    native_model_io::save_aimodel(&project_path, &project)?;
    native_model_io::write_binary_stl(&stl_path, &mesh, "native CADPlan")?;
    crate::native_quick::write_glb(&mesh, &glb_path)?;
    let plan_path = directory.join("cadplan.json");
    native_model_io::atomic_write(
        &plan_path,
        &serde_json::to_vec_pretty(plan).map_err(|e| e.to_string())?,
    )?;
    let mut views = Vec::new();
    for name in ["iso", "front", "right", "top", "back", "left", "bottom"] {
        check_cancel(cancelled)?;
        let rendered = native_render::render(
            &mesh,
            &RenderOptions {
                view: View::parse(name)?,
                ..Default::default()
            },
        )?;
        let path = directory.join("views").join(format!("{name}.png"));
        native_model_io::atomic_write(&path, &rendered.png)?;
        let metadata = rendered.metadata;
        views.push(json!({"revision":revision,"preview_generation":generation,"view":name,
            "geometry_fingerprint":signature,"preview_sha256":format!("{:x}",Sha256::digest(&rendered.png)),
            "preview_image_path":path,"render_options":{"projection":metadata.projection,
            "width":metadata.width,"height":metadata.height,"framing_center":metadata.framing_center,
            "units_per_pixel":metadata.units_per_pixel,"shading":metadata.shading,"depth_test":metadata.depth_test}}));
    }
    check_cancel(cancelled)?;
    let report_path = directory.join("geometry-report.json");
    let mut report = built.report;
    report["revision"] = json!(revision);
    report["preview_generation"] = json!(generation);
    report["triangle_count"] = json!(mesh.positions.len() / 3);
    report["state"] = json!("passed");
    native_model_io::atomic_write(
        &report_path,
        &serde_json::to_vec_pretty(&report).map_err(|e| e.to_string())?,
    )?;
    let preview = json!({"state":"candidate","execution_engine":"rust_c_abi_occt","revision":revision,
        "geometry_fingerprint":signature,"preview_generation":generation,"dimensions":report["dimensions_mm"],
        "local_geometry_checks":report,"preview_image_path":views[0]["preview_image_path"],
        "preview_sha256":views[0]["preview_sha256"],"triangle_count":mesh.positions.len()/3,
        "catia_executed":false});
    let output_paths = json!({"aimodel":project_path,"stl":stl_path,"glb":glb_path,"cadplan":plan_path,
        "geometry_report":report_path,"preview":views[0]["preview_image_path"]});
    Ok(PreviewOutput {
        outcome: BackendOutcome {
            revision,
            geometry_fingerprint: signature,
            output_paths,
            preview,
        },
        views,
    })
}

fn check_cancel(cancelled: &AtomicBool) -> Result<(), String> {
    if cancelled.load(Ordering::Acquire) {
        Err("原生几何输出已取消。".into())
    } else {
        Ok(())
    }
}
