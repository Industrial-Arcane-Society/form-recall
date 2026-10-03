use std::path::PathBuf;

fn main() {
    let mut args = std::env::args_os().skip(1);
    let resources = args.next().map(PathBuf::from);
    let data_dir = args.next().map(PathBuf::from);
    if args.next().is_some() || resources.is_none() || data_dir.is_none() {
        eprintln!("usage: ai-modeler-headless-smoke <app-resource-root> <scratch-data-dir>");
        std::process::exit(2);
    }
    let resources = resources.unwrap();
    let data_dir = data_dir.unwrap();
    match ai_modeler_ui_lib::headless_quick_smoke(&resources, &data_dir) {
        Ok(document) => {
            println!(
                "{}",
                serde_json::json!({
                    "status": "ok",
                    "path": "native_quick -> OCCT -> active_model_commit",
                    "globalPythonRequired": false,
                    "globalNodeRequired": false,
                    "pythonChildStarted": false,
                    "nodeChildStarted": false,
                    "document": document,
                })
            );
        }
        Err(error) => {
            eprintln!("headless native Quick smoke failed: {error}");
            std::process::exit(1);
        }
    }
}
