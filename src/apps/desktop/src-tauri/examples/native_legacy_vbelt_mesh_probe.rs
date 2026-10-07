//! Run the Quick-only mesh compatibility path without loading OCCT.
use ai_modeler_ui_lib::{
    cad_plan_validation, native_active_model, native_quick, native_quick_parser,
    native_vbelt_pulley,
};
use serde_json::Value;
use std::{env, fs, path::Path};

fn main() -> Result<(), String> {
    let mut args = env::args().skip(1);
    let output_root = args
        .next()
        .ok_or("usage: native_legacy_vbelt_mesh_probe OUTPUT_ROOT [PROMPT]")?;
    let prompt = args
        .next()
        .unwrap_or_else(|| "V带轮 基准直径100 槽数3 槽型A 内孔25 宽度30".to_owned());
    let parsed = native_quick_parser::parse(&prompt);
    println!(
        "eligible_mesh_only={}",
        native_vbelt_pulley::is_mesh_only_legacy_quick_plan(&parsed["plan"])
    );
    println!(
        "plan_validation={}",
        cad_plan_validation::validate_plan(&parsed["plan"])
    );
    let root = Path::new(&output_root);
    fs::create_dir_all(root).map_err(|error| error.to_string())?;
    let document = native_quick::build(&prompt, 1, Path::new("unused-occt-manifest.json"), root)?;
    if document["state"] != "complete" {
        return Err(format!(
            "Quick did not produce a complete document: {document}"
        ));
    }
    let candidate = native_quick::active_model_candidate(&prompt, &document)?;
    let active_root = native_active_model::root_from_data_dir(root);
    let committed = native_active_model::commit(&active_root, 0, &candidate)?;
    println!(
        "{}",
        serde_json::to_string_pretty(&Value::Object(
            document.as_object().cloned().unwrap_or_default()
        ))
        .map_err(|error| error.to_string())?
    );
    if committed["geometric_status"] != "mesh_only" {
        return Err(format!(
            "mesh-only Quick was not preserved in active model: {}",
            committed["geometric_status"]
        ));
    }
    Ok(())
}
