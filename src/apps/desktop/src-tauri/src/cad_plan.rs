//! Transactional edits for the JSON CADPlan representation.
//!
//! This module intentionally does not execute geometry or run the full CADPlan
//! validator. It preserves unknown plan/feature fields so callers can migrate
//! domain operations incrementally without losing extension data.

use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

const SCHEMA_VERSION: &str = "1.0";
const GEOMETRY_FIELDS: [&str; 5] = ["id", "kind", "operation", "parameters", "relations"];
const EDITABLE_FIELDS: [&str; 6] = ["name", "note", "relations", "kind", "operation", "enabled"];

/// Construct a one-feature plan using the legacy Python plan_state defaults.
pub fn create(
    name: impl Into<String>,
    first_feature: Value,
    source_prompt: impl Into<String>,
    units: Option<Value>,
) -> Value {
    json!({
        "schema_version": SCHEMA_VERSION,
        "name": name.into(),
        "source_prompt": source_prompt.into(),
        "units": units.filter(|value| python_truthy(value))
            .unwrap_or_else(|| json!({"length":"mm", "angle":"deg"})),
        "coordinate_system": {
            "type":"global_cartesian", "handedness":"right",
            "axes":["X","Y","Z"], "origin":[0,0,0]
        },
        "features": [first_feature],
        "pending_questions": []
    })
}

/// Return a new plan with the feature appended; `plan` is never mutated.
pub fn add(plan: &Value, feature: &Value) -> Result<Value, String> {
    let mut candidate = plan.clone();
    let rows = candidate
        .get_mut("features")
        .and_then(Value::as_array_mut)
        .ok_or("CADPlan features 必须是数组。")?;
    rows.push(feature.clone());
    Ok(candidate)
}

/// Apply a feature edit atomically. On any error, the input plan remains intact.
pub fn update(
    plan: &Value,
    feature_id: &str,
    parameter_updates: &Value,
    source_updates: &Value,
    remove_parameters: &[String],
    fields: &Value,
) -> Result<Value, String> {
    let mut candidate = plan.clone();
    let features = candidate
        .get_mut("features")
        .and_then(Value::as_array_mut)
        .ok_or("CADPlan features 必须是数组。")?;
    let target = features
        .iter_mut()
        .find(|feature| feature.get("id").and_then(Value::as_str) == Some(feature_id))
        .ok_or_else(|| format!("未找到特征 id：{feature_id}"))?;
    let target = target.as_object_mut().ok_or("目标特征必须是 JSON 对象。")?;

    if let Some(changes) = fields.as_object() {
        for (key, value) in changes {
            if !EDITABLE_FIELDS.contains(&key.as_str()) {
                return Err(format!("不能更新特征字段：{key}"));
            }
            target.insert(key.clone(), value.clone());
        }
    } else if !fields.is_null() {
        return Err("fields 必须是 JSON 对象。".into());
    }

    // Work on local copies so an error after an earlier edit still leaves the
    // caller's plan untouched and the candidate is only committed on success.
    let mut params = target
        .get("parameters")
        .cloned()
        .unwrap_or_else(|| json!({}))
        .as_object()
        .cloned()
        .ok_or("特征 parameters 必须是 JSON 对象。")?;
    let mut sources = target
        .get("sources")
        .cloned()
        .unwrap_or_else(|| json!({}))
        .as_object()
        .cloned()
        .ok_or("特征 sources 必须是 JSON 对象。")?;
    for key in remove_parameters {
        params.remove(key);
        sources.remove(key);
    }

    let updates = as_object_or_empty(parameter_updates, "parameter_updates")?;
    let evidence = as_object_or_empty(source_updates, "source_updates")?;
    for (key, value) in updates {
        let old_value = params.get(key);
        if contains_number(value)
            && !old_value.is_some_and(|old| python_equal(old, value))
            && !evidence.contains_key(key)
        {
            return Err(format!(
                "修改数值参数 {key} 时必须同时更新其 sources 证据。"
            ));
        }
        params.insert(key.clone(), value.clone());
    }
    for (key, value) in evidence {
        sources.insert(key.clone(), value.clone());
    }
    target.insert("parameters".into(), Value::Object(params));
    target.insert("sources".into(), Value::Object(sources));
    Ok(candidate)
}

/// Remove one feature atomically; returns an error if its id is absent.
pub fn remove(plan: &Value, feature_id: &str) -> Result<Value, String> {
    let mut candidate = plan.clone();
    let features = candidate
        .get_mut("features")
        .and_then(Value::as_array_mut)
        .ok_or("CADPlan features 必须是数组。")?;
    let index = features
        .iter()
        .position(|feature| feature.get("id").and_then(Value::as_str) == Some(feature_id))
        .ok_or_else(|| format!("未找到特征 id：{feature_id}"))?;
    features.remove(index);
    Ok(candidate)
}

/// Change whether a feature participates in previews and exports while keeping
/// its parameters, source evidence, and serialized row in the plan.
pub fn set_enabled(plan: &Value, feature_id: &str, enabled: bool) -> Result<Value, String> {
    update(
        plan,
        feature_id,
        &Value::Null,
        &Value::Null,
        &[],
        &json!({"enabled":enabled}),
    )
}

/// Build the compact UI/tool summary; this is not the full validator.
pub fn snapshot(
    plan: &Value,
    validation: Option<Value>,
    dimensions: Option<Value>,
    revision: i64,
    findings: Option<Value>,
    include_features: bool,
) -> Value {
    let Some(object) = plan.as_object() else {
        return json!({"state":"empty", "revision":revision});
    };
    let features = object.get("features").and_then(Value::as_array);
    let mut result = Map::new();
    result.insert("state".into(), json!("model"));
    result.insert("revision".into(), json!(revision));
    result.insert(
        "name".into(),
        object.get("name").cloned().unwrap_or(Value::Null),
    );
    result.insert(
        "units".into(),
        object.get("units").cloned().unwrap_or(Value::Null),
    );
    result.insert("feature_count".into(), json!(features.map_or(0, Vec::len)));
    result.insert("validation".into(), validation.unwrap_or(Value::Null));
    result.insert("dimensions".into(), dimensions.unwrap_or(Value::Null));
    result.insert(
        "unresolved_findings".into(),
        findings.filter(python_truthy).unwrap_or_else(|| json!([])),
    );
    if include_features {
        result.insert(
            "features".into(),
            json!(features.cloned().unwrap_or_default()),
        );
    }
    Value::Object(result)
}

/// Hash ordered CAD geometry semantics, excluding sources and presentation fields.
pub fn geometry_fingerprint(plan: &Value) -> Result<String, String> {
    let features = plan
        .get("features")
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter_map(Value::as_object)
                .map(|feature| {
                    let mut projection = Map::new();
                    for key in GEOMETRY_FIELDS {
                        projection.insert(
                            key.to_owned(),
                            feature.get(key).cloned().unwrap_or(Value::Null),
                        );
                    }
                    if feature.get("enabled").and_then(Value::as_bool) == Some(false) {
                        projection.insert("enabled".into(), Value::Bool(false));
                    }
                    Value::Object(projection)
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    // Match Python json.dumps(features, ensure_ascii=False, sort_keys=True,
    // separators=(",", ":"), allow_nan=False), including float exponent form.
    let mut encoded = String::new();
    write_python_json(&Value::Array(features), &mut encoded)?;
    Ok(format!("{:x}", Sha256::digest(encoded)))
}

fn as_object_or_empty<'a>(value: &'a Value, name: &str) -> Result<&'a Map<String, Value>, String> {
    match value {
        Value::Null => Ok(empty_map()),
        Value::Object(object) => Ok(object),
        _ => Err(format!("{name} 必须是 JSON 对象。")),
    }
}

fn empty_map() -> &'static Map<String, Value> {
    static EMPTY: std::sync::OnceLock<Map<String, Value>> = std::sync::OnceLock::new();
    EMPTY.get_or_init(Map::new)
}

fn contains_number(value: &Value) -> bool {
    match value {
        Value::Number(_) => true,
        Value::Array(items) => items.iter().any(contains_number),
        Value::Object(object) => object.values().any(contains_number),
        _ => false,
    }
}

/// Python's JSON values compare numbers by numeric value, so `10 == 10.0`.
/// The recursive behavior also matches list/dict equality for JSON structures.
fn python_equal(left: &Value, right: &Value) -> bool {
    match (left, right) {
        (Value::Number(a), Value::Number(b)) => python_number_equal(a, b),
        (Value::Bool(a), Value::Number(b)) | (Value::Number(b), Value::Bool(a)) => {
            python_integer_number_equal(i128::from(*a), b)
        }
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(x, y)| python_equal(x, y))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter()
                    .all(|(key, value)| b.get(key).is_some_and(|other| python_equal(value, other)))
        }
        _ => left == right,
    }
}

fn python_number_equal(left: &serde_json::Number, right: &serde_json::Number) -> bool {
    match (integer_value(left), integer_value(right)) {
        (Some(a), Some(b)) => a == b,
        (Some(integer), None) => integer_float_equal(integer, right.as_f64().unwrap_or(f64::NAN)),
        (None, Some(integer)) => integer_float_equal(integer, left.as_f64().unwrap_or(f64::NAN)),
        (None, None) => left.as_f64() == right.as_f64(),
    }
}

fn integer_value(value: &serde_json::Number) -> Option<i128> {
    value
        .as_i64()
        .map(i128::from)
        .or_else(|| value.as_u64().map(i128::from))
}

fn python_integer_number_equal(integer: i128, number: &serde_json::Number) -> bool {
    if let Some(other) = integer_value(number) {
        return integer == other;
    }
    integer_float_equal(integer, number.as_f64().unwrap_or(f64::NAN))
}

fn integer_float_equal(integer: i128, float: f64) -> bool {
    if !float.is_finite()
        || float.fract() != 0.0
        || float < i64::MIN as f64
        || float >= 18_446_744_073_709_551_616.0
    {
        return false;
    }
    (float as i128) == integer
}

fn write_python_json(value: &Value, output: &mut String) -> Result<(), String> {
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(false) => output.push_str("false"),
        Value::Bool(true) => output.push_str("true"),
        Value::Number(number) => {
            if let Some(float) = number.as_f64().filter(|_| number.is_f64()) {
                output.push_str(&python_float_repr(float));
            } else {
                output.push_str(&number.to_string());
            }
        }
        Value::String(string) => {
            output.push_str(&serde_json::to_string(string).map_err(|error| error.to_string())?)
        }
        Value::Array(items) => {
            output.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                write_python_json(item, output)?;
            }
            output.push(']');
        }
        Value::Object(object) => {
            output.push('{');
            for (index, (key, item)) in object.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                output.push_str(&serde_json::to_string(key).map_err(|error| error.to_string())?);
                output.push(':');
                write_python_json(item, output)?;
            }
            output.push('}');
        }
    }
    Ok(())
}

fn python_float_repr(value: f64) -> String {
    let raw = format!("{value:?}");
    let Some((mantissa, exponent)) = raw.split_once('e') else {
        return raw;
    };
    let exponent: i32 = exponent.parse().unwrap_or(0);
    let sign = if exponent < 0 { '-' } else { '+' };
    format!("{mantissa}e{sign}{:02}", exponent.unsigned_abs())
}

fn python_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|number| number != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plan() -> Value {
        json!({
            "schema_version":"1.0", "name":"fixture", "vendor_extension":{"x":1},
            "features":[{
                "id":"base", "kind":"box", "operation":"add", "name":"old",
                "parameters":{"length":10,"width":4,"height":2,"future":{"flag":true}},
                "sources":{"length":{"type":"image_estimate","evidence":"比例"}},
                "relations":[{"type":"touches","feature_id":"other"}],
                "future_feature_field":"kept"
            }]
        })
    }

    #[test]
    fn wire_float_parsing_retains_python_fingerprint() {
        let plan: Value = serde_json::from_str(r#"{"features":[{"id":"body","kind":"box","operation":"add","parameters":{"length":15227.446303581859}}]}"#).unwrap();
        assert_eq!(
            geometry_fingerprint(&plan).unwrap(),
            "e6563e9d8568d7272a10c403ac43eacfd70541912e30be176fe0e48c9e3cd06c"
        );
    }

    #[test]
    fn edits_are_immutable_and_preserve_unknown_fields_and_sources() {
        let original = plan();
        let updated = update(
            &original,
            "base",
            &json!({"height":3}),
            &json!({"height":{"type":"derived","evidence":"user confirmation"}}),
            &[],
            &json!({"name":"new"}),
        )
        .unwrap();
        assert_eq!(original["features"][0]["name"], "old");
        assert_eq!(updated["vendor_extension"], json!({"x":1}));
        assert_eq!(updated["features"][0]["future_feature_field"], "kept");
        assert_eq!(
            updated["features"][0]["parameters"]["future"],
            json!({"flag":true})
        );
        assert_eq!(
            updated["features"][0]["sources"]["length"]["type"],
            "image_estimate"
        );
        assert_eq!(
            updated["features"][0]["sources"]["height"]["type"],
            "derived"
        );
    }

    #[test]
    fn enabled_toggle_is_serialized_and_changes_fingerprint_without_rewriting_feature() {
        let mut plan = plan();
        let original_fingerprint = geometry_fingerprint(&plan).unwrap();
        let disabled = set_enabled(&plan, "base", false).unwrap();
        assert_eq!(plan["features"][0].get("enabled"), None);
        assert_eq!(disabled["features"][0]["enabled"], false);
        assert_eq!(
            disabled["features"][0]["parameters"],
            plan["features"][0]["parameters"]
        );
        assert_ne!(
            geometry_fingerprint(&disabled).unwrap(),
            original_fingerprint
        );
        plan["features"][0]["enabled"] = json!(true);
        assert_eq!(geometry_fingerprint(&plan).unwrap(), original_fingerprint);
    }

    #[test]
    fn create_add_remove_and_snapshot_match_plan_state_shapes() {
        let feature = json!({"id":"base","kind":"box","operation":"add"});
        let created = create("part", feature.clone(), "prompt", None);
        assert_eq!(created["schema_version"], SCHEMA_VERSION);
        assert_eq!(created["units"], json!({"length":"mm","angle":"deg"}));
        assert_eq!(created["features"], json!([feature]));
        let added = add(&created, &json!({"id":"hole"})).unwrap();
        assert_eq!(created["features"].as_array().unwrap().len(), 1);
        assert_eq!(added["features"].as_array().unwrap().len(), 2);
        let removed = remove(&added, "base").unwrap();
        assert_eq!(removed["features"], json!([{"id":"hole"}]));
        let summary = snapshot(&removed, None, None, 3, Some(json!([])), false);
        assert_eq!(summary["state"], "model");
        assert_eq!(summary["revision"], 3);
        assert_eq!(summary["feature_count"], 1);
        assert_eq!(summary["unresolved_findings"], json!([]));
        assert!(summary.get("features").is_none());
        assert_eq!(
            snapshot(&Value::Null, None, None, 4, None, true),
            json!({"state":"empty","revision":4})
        );
    }

    #[test]
    fn numeric_update_requires_source_and_failed_edit_rolls_back() {
        let original = plan();
        let error = update(
            &original,
            "base",
            &json!({"length":12}),
            &Value::Null,
            &[],
            &json!({"note":"partial"}),
        )
        .unwrap_err();
        assert!(error.contains("必须同时更新"));
        assert_eq!(original["features"][0]["parameters"]["length"], 10);
        assert_eq!(original["features"][0].get("note"), None);
        let equivalent = update(
            &original,
            "base",
            &json!({"length":10.0,"future":{"flag":1.0}}),
            &Value::Null,
            &[],
            &Value::Null,
        )
        .expect("Python-compatible numeric equality should avoid a false provenance guard");
        assert_eq!(equivalent["features"][0]["parameters"]["length"], 10.0);
        assert_eq!(
            equivalent["features"][0]["parameters"]["future"]["flag"],
            1.0
        );
        let forbidden = update(
            &original,
            "base",
            &Value::Null,
            &Value::Null,
            &[],
            &json!({"parameters":{"x":1}}),
        )
        .unwrap_err();
        assert!(forbidden.contains("不能更新特征字段"));
        assert_eq!(original["features"][0]["parameters"]["length"], 10);
    }

    #[test]
    fn remove_parameter_removes_its_source_and_missing_ids_error() {
        let original = plan();
        let changed = update(
            &original,
            "base",
            &Value::Null,
            &Value::Null,
            &["length".into()],
            &Value::Null,
        )
        .unwrap();
        assert!(changed["features"][0]["parameters"].get("length").is_none());
        assert!(changed["features"][0]["sources"].get("length").is_none());
        assert_eq!(original["features"][0]["parameters"]["length"], 10);
        assert!(
            remove(&original, "missing")
                .unwrap_err()
                .contains("未找到特征 id")
        );
        assert!(
            update(
                &original,
                "missing",
                &Value::Null,
                &Value::Null,
                &[],
                &Value::Null
            )
            .unwrap_err()
            .contains("未找到特征 id")
        );
    }

    #[test]
    fn fingerprint_ignores_sources_names_and_object_key_order_but_keeps_relation_order() {
        let first = json!({"features":[
            {"id":"a","kind":"box","operation":"add","name":"one","parameters":{"x":1,"y":2},"sources":{"x":{"type":"user"}},"relations":[{"feature_id":"b"},{"feature_id":"c"}]},
            {"id":"b","kind":"hole","operation":"cut","parameters":{"point":[0,0,0]}}
        ]});
        let second = json!({"features":[
            {"relations":[{"feature_id":"b"},{"feature_id":"c"}],"parameters":{"y":2,"x":1},"operation":"add","kind":"box","id":"a","name":"two","sources":{"x":{"type":"derived"}}},
            {"id":"b","kind":"hole","operation":"cut","parameters":{"point":[0,0,0]},"extra":true}
        ]});
        assert_eq!(
            geometry_fingerprint(&first).unwrap(),
            geometry_fingerprint(&second).unwrap()
        );
        let python_golden = "b6f565bb98856fe3fd135159fe999c7f322ed1ae6217943332b272bceac5596f";
        assert_eq!(geometry_fingerprint(&first).unwrap(), python_golden);
    }

    #[test]
    fn fingerprint_matches_python_float_unicode_missing_and_relation_order_golden() {
        let plan = json!({"features":[
            {"id":"零件","kind":"box","operation":"add","parameters":{
                "integer_float":1.0,"small_exp":1e-7,"large_exp":1e20,
                "negative_zero":-0.0,"unicode":"孔 β"
            },"relations":[{"type":"依赖","id":"β"},{"type":"顺序","id":"孔"}]},
            {"id":"empty-relations","kind":"hole","operation":"cut","parameters":{}}
        ]});
        assert_eq!(
            geometry_fingerprint(&plan).unwrap(),
            "9d4c1ca7576beb8c1d6f9816249520f440b1fadb3b252890cc86d151dd6e0349"
        );
        let mut reversed = plan.clone();
        reversed["features"][0]["relations"] = json!([
            {"type":"顺序","id":"孔"},{"type":"依赖","id":"β"}
        ]);
        assert_ne!(
            geometry_fingerprint(&plan).unwrap(),
            geometry_fingerprint(&reversed).unwrap()
        );
    }
}
