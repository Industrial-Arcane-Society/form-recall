use ai_modeler_ui_lib::native_parts_catalog;
use serde_json::json;
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);
    let command = args.next().unwrap_or_else(|| "snapshot".to_owned());
    let result = match command.as_str() {
        "snapshot" if args.next().is_none() => native_parts_catalog::snapshot(),
        "parts" => {
            let query = args.next();
            if args.next().is_some() {
                eprintln!("usage: native_parts_catalog_cli parts [query]");
                return ExitCode::from(2);
            }
            json!(native_parts_catalog::list_parts(query.as_deref()))
        }
        "samples" if args.next().is_none() => json!(native_parts_catalog::list_samples()),
        _ => {
            eprintln!("usage: native_parts_catalog_cli [snapshot | parts [query] | samples]");
            return ExitCode::from(2);
        }
    };
    println!("{result}");
    ExitCode::SUCCESS
}
