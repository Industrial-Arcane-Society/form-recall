//! Native .aimodel/STL compatibility probe against the actual OCCT bridge.
use ai_modeler_ui_lib::native_model_io::{
    cadplan_to_project, load_aimodel, save_aimodel, write_binary_stl,
};
use ai_modeler_ui_lib::native_occt::{Kernel, Shape};
use serde_json::{Value, json};
use std::{env, fs, path::PathBuf};

fn main() -> Result<(), String> {
    let args: Vec<_> = env::args().skip(1).collect();
    let manifest = PathBuf::from(
        args.first()
            .ok_or("usage: native_model_io_probe RUNTIME_MANIFEST LEGACY_AIMODEL OUTPUT_DIR")?,
    );
    let legacy = PathBuf::from(args.get(1).ok_or("missing legacy .aimodel path")?);
    let out = PathBuf::from(args.get(2).ok_or("missing output directory")?);
    fs::create_dir_all(&out).map_err(|e| e.to_string())?;
    let old = load_aimodel(&legacy)?;
    save_aimodel(&out.join("legacy-roundtrip.aimodel"), &old)?;
    let plan = json!({"schema_version":"1.0","name":"Native IO probe","source_prompt":"unit conversion and source preservation probe","units":{"length":"mm","angle":"deg"},"coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},"features":[{"id":"body","kind":"box","operation":"add","name":"Probe body","parameters":{"length":20,"width":10,"height":8,"center":[0,0,0]},"sources":{"length":{"type":"user","evidence":"20 mm"},"width":{"type":"user","evidence":"10 mm"},"height":{"type":"user","evidence":"8 mm"},"center":{"type":"derived","evidence":"origin"}}}]});
    let native_project = cadplan_to_project(&plan)?;
    save_aimodel(&out.join("rust-cadplan.aimodel"), &native_project)?;
    let kernel = Kernel::load_manifest(&manifest)?;
    // Bridge profile scene shared with native_render_probe and F4 OCCT acceptance.
    let stock = kernel.make_box([100., 40., 120.], [0., 0., 60.])?;
    let mut tools: Vec<Shape> = vec![kernel.make_box([20., 8., 124.], [0., 16., 60.])?];
    for x in [-32., 0., 32.] {
        for (z, r) in [(22., 6.), (60., 4.), (98., 6.)] {
            tools.push(kernel.cylinder(r, 44., [x, -22., z], [0., 1., 0.])?);
        }
    }
    let refs: Vec<_> = tools.iter().collect();
    let part = kernel.cut(&stock, &refs)?;
    let mesh = part.tessellate(0.1, 0.5)?;
    let stats = write_binary_stl(&out.join("native-profile.stl"), &mesh, "native-profile")?;
    let report: Value = json!({"legacy_input_features":old.get("features").and_then(Value::as_array).map_or(0,Vec::len),"legacy_roundtrip":out.join("legacy-roundtrip.aimodel"),"rust_cadplan_project":out.join("rust-cadplan.aimodel"),"triangle_count":stats.triangle_count,"bounds":stats.bounds,"signed_volume":stats.signed_volume,"stl":out.join("native-profile.stl")});
    fs::write(
        out.join("report.json"),
        serde_json::to_vec_pretty(&report).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    println!(
        "Wrote native project roundtrips and {}-triangle STL to {}",
        stats.triangle_count,
        out.display()
    );
    Ok(())
}
