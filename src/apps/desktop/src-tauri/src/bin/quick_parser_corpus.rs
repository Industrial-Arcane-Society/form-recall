//! Parse-only batch runner for comparing Rust Quick parsing with the saved
//! legacy Python Quick corpus. This intentionally never calls the geometry
//! builder, CADPlan validator, OCCT, or any export path.
//!
//! Run from the repository root:
//! `cargo run --manifest-path src/apps/desktop/src-tauri/Cargo.toml --bin quick_parser_corpus -- <input.jsonl> <output.jsonl>`

use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::env;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::PathBuf;

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn field_or_null(value: &Value, key: &str) -> Value {
    value.get(key).cloned().unwrap_or(Value::Null)
}

fn issue_codes(value: &Value) -> Vec<Value> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .map(|issue| {
            issue
                .get("code")
                .or_else(|| issue.get("type"))
                .cloned()
                .unwrap_or_else(|| json!("uncoded"))
        })
        .collect()
}

/// Preserve provenance source type without copying prompt text that may be
/// present in a source's `evidence` field. Hash evidence for join/debug only.
fn source_projection(value: &Value) -> Value {
    let Some(fields) = value.as_object() else {
        return Value::Null;
    };
    let mut projected = serde_json::Map::new();
    for (field, source) in fields {
        let mut item = serde_json::Map::new();
        if let Some(source_type) = source.get("type") {
            item.insert("type".into(), source_type.clone());
        }
        if let Some(evidence) = source.get("evidence").and_then(Value::as_str) {
            item.insert("evidence_sha256".into(), json!(sha256(evidence.as_bytes())));
        }
        projected.insert(field.clone(), Value::Object(item));
    }
    Value::Object(projected)
}

fn run() -> Result<(), String> {
    let mut args = env::args_os().skip(1);
    let input = args
        .next()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("src/legacy/datasets/训练提示词/全部提示词.jsonl"));
    let output = args
        .next()
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from("artifacts/f5.rust-parser-corpus-comparison/rust_parser_results.jsonl")
        });
    if args.next().is_some() {
        return Err("usage: quick_parser_corpus [input.jsonl] [output.jsonl]".into());
    }

    let input_bytes = fs::read(&input)
        .map_err(|error| format!("cannot read {}: {error}", input.display()))?;
    let corpus_sha256 = sha256(&input_bytes);
    let input_file = File::open(&input)
        .map_err(|error| format!("cannot open {}: {error}", input.display()))?;
    let reader = BufReader::new(input_file);
    let output_file = File::create(&output)
        .map_err(|error| format!("cannot create {}: {error}", output.display()))?;
    let mut writer = BufWriter::new(output_file);
    let mut rows = 0usize;

    for (line_index, line_result) in reader.lines().enumerate() {
        let line_number = line_index + 1;
        let line = line_result.map_err(|error| format!("line {line_number}: {error}"))?;
        if line.trim().is_empty() {
            continue;
        }
        let row: Value = serde_json::from_str(&line)
            .map_err(|error| format!("invalid JSON at line {line_number}: {error}"))?;
        let prompt = row
            .get("prompt")
            .and_then(Value::as_str)
            .filter(|prompt| !prompt.trim().is_empty())
            .ok_or_else(|| format!("missing nonblank string prompt at line {line_number}"))?;
        let parsed = ai_modeler_ui_lib::native_quick_parser::parse(prompt);
        let plan_features = parsed["plan"]["features"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let features: Vec<Value> = plan_features
            .iter()
            .map(|feature| {
                json!({
                    "kind": feature.get("kind").cloned().unwrap_or(Value::Null),
                    "operation": feature.get("operation").cloned().unwrap_or(Value::Null),
                    "parameters": feature.get("parameters").cloned().unwrap_or(Value::Null),
                    "sources": source_projection(&feature["sources"]),
                    "relations": feature.get("relations").cloned().unwrap_or(Value::Null)
                })
            })
            .collect();
        let signature: Vec<Value> = features
            .iter()
            .map(|feature| {
                json!({
                    "kind": feature["kind"],
                    "operation": feature["operation"]
                })
            })
            .collect();
        let output_row = json!({
            "schema_version": 1,
            "dataset_sha256": corpus_sha256,
            "dataset_id": field_or_null(&row, "id"),
            "prompt_sha256": sha256(prompt.as_bytes()),
            "category": field_or_null(&row, "category"),
            "variant": field_or_null(&row, "variant"),
            "status": parsed.get("status").cloned().unwrap_or(Value::Null),
            "feature_count": features.len(),
            "signature": signature,
            "features": features,
            "warning_codes": issue_codes(&parsed["warnings"]),
            "question_codes": issue_codes(&parsed["questions"]),
            "unsupported_codes": issue_codes(&parsed["unsupported"])
        });
        serde_json::to_writer(&mut writer, &output_row)
            .map_err(|error| format!("cannot encode Rust result at line {line_number}: {error}"))?;
        writer
            .write_all(b"\n")
            .map_err(|error| format!("cannot write result at line {line_number}: {error}"))?;
        rows += 1;
    }
    writer
        .flush()
        .map_err(|error| format!("cannot flush {}: {error}", output.display()))?;
    eprintln!(
        "parse-only complete: {rows} rows; corpus sha256 {corpus_sha256}; output {}",
        output.display()
    );
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("quick_parser_corpus: {error}");
        std::process::exit(2);
    }
}
