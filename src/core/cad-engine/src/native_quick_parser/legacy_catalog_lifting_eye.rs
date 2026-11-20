//! Exact bare-name compatibility for the old Python Quick `吊环` catalog row.
//!
//! This preserves the legacy simplified three-feature recipe. It does not
//! claim standards compliance or equivalence to the measured lifting-eye
//! models in the separate purchased-parts library.

use serde_json::{json, Value};

pub(super) fn parse(raw: &str, normalized: &str) -> Option<Value> {
    // Keep this fallback narrow: parameterized or structurally qualified
    // descriptions must remain available to the regular parsers.
    if raw.trim() != "吊环" || normalized.trim() != "吊环" {
        return None;
    }

    let derived = |note: &str| super::derived_source(raw, note);
    let ring = legacy_feature(
        1,
        "cylinder",
        "add",
        "环体 ⌀63×35",
        json!({
            "radius":31.5,
            "height":35.0,
            "center":[0.0,0.0,0.0],
            "base":false,
            "axis":"Y",
            "segments":48
        }),
        json!({
            "radius":derived("旧 Python Quick 裸名称目录配方回退值：环体外径 63 mm，半径 31.5 mm。"),
            "height":derived("旧 Python Quick 裸名称目录配方回退值：环体轴向厚度 35 mm。"),
            "center":derived("旧 Python Quick 配方将环体中心置于全局原点。"),
            "base":derived("旧 Python Quick 配方使用 center 对称圆柱（base=false）。"),
            "axis":derived("旧 Python Quick 配方将环体轴线设为 Y。"),
            "segments":derived("旧 Python Quick 配方将环体圆周离散为 48 段。")
        }),
    );
    let bore = legacy_feature(
        2,
        "hole",
        "cut",
        "环体内孔 ⌀34.65",
        json!({
            "diameter":34.650000000000006,
            "through":true,
            "pos":"center",
            "axis":"Y",
            "segments":48
        }),
        json!({
            "diameter":derived("旧 Python Quick 裸名称目录配方回退值：环体内孔直径 34.650000000000006 mm。"),
            "through":derived("旧 Python Quick 配方将环体内孔设为贯穿。"),
            "pos":derived("旧 Python Quick 配方将环体内孔置于中心。"),
            "axis":derived("旧 Python Quick 配方将环体内孔轴线设为 Y。"),
            "segments":derived("旧 Python Quick 配方将内孔圆周离散为 48 段。")
        }),
    );
    let stem = legacy_feature(
        3,
        "cylinder",
        "add",
        "螺杆 ⌀16.38×58",
        json!({
            "radius":8.19,
            "height":58.0,
            "center":[0.0,0.0,29.0],
            "base":false,
            "axis":"Z",
            "segments":32
        }),
        json!({
            "radius":derived("旧 Python Quick 裸名称目录配方回退值：螺杆直径 16.38 mm，半径 8.19 mm。"),
            "height":derived("旧 Python Quick 裸名称目录配方回退值：螺杆长度 58 mm。"),
            "center":derived("旧 Python Quick 配方将螺杆中心置于 [0, 0, 29] mm。"),
            "base":derived("旧 Python Quick 配方使用 center 对称圆柱（base=false）。"),
            "axis":derived("旧 Python Quick 配方将螺杆轴线设为 Z。"),
            "segments":derived("旧 Python Quick 配方将螺杆圆周离散为 32 段。")
        }),
    );

    let plan = json!({
        "schema_version":"1.0",
        "name":"吊环",
        "source_prompt":raw,
        "units":{"length":"mm","angle":"deg"},
        "coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},
        "features":[ring,bore,stem],
        "pending_questions":[]
    });
    Some(super::result(
        "ready",
        raw,
        Some(plan),
        vec![super::issue(
            "legacy_catalog_lifting_eye_simplified",
            "已复现旧 Python Quick 裸名称目录的简化三特征配方；尺寸是旧解析器回退值，不代表标准吊环。杆与环体的最终融合/实体数须由几何内核验证。",
        )],
        vec![],
        vec![],
    ))
}

fn legacy_feature(
    id: usize,
    kind: &str,
    operation: &str,
    name: &str,
    parameters: Value,
    sources: Value,
) -> Value {
    json!({
        "id":format!("quick-{id:04}"),
        "kind":kind,
        "operation":operation,
        "name":name,
        "parameters":parameters,
        "sources":sources,
        "relations":[]
    })
}

#[cfg(test)]
mod tests {
    use super::parse;
    use serde_json::json;

    #[test]
    fn exact_bare_name_reproduces_legacy_three_feature_recipe() {
        let output = parse("吊环", "吊环").expect("legacy bare catalog entry");
        assert_eq!(output["status"], "ready");
        let features = output["plan"]["features"].as_array().unwrap();
        assert_eq!(features.len(), 3);

        assert_eq!(features[0]["kind"], "cylinder");
        assert_eq!(features[0]["operation"], "add");
        assert_eq!(features[0]["name"], "环体 ⌀63×35");
        assert_eq!(features[0]["parameters"]["radius"], 31.5);
        assert_eq!(features[0]["parameters"]["height"], 35.0);
        assert_eq!(features[0]["parameters"]["center"], json!([0.0, 0.0, 0.0]));
        assert_eq!(features[0]["parameters"]["base"], false);
        assert_eq!(features[0]["parameters"]["axis"], "Y");
        assert_eq!(features[0]["parameters"]["segments"], 48);

        assert_eq!(features[1]["kind"], "hole");
        assert_eq!(features[1]["operation"], "cut");
        assert_eq!(features[1]["name"], "环体内孔 ⌀34.65");
        assert_eq!(features[1]["parameters"]["diameter"], 34.650000000000006);
        assert_eq!(features[1]["parameters"]["through"], true);
        assert_eq!(features[1]["parameters"]["pos"], "center");
        assert_eq!(features[1]["parameters"]["axis"], "Y");
        assert_eq!(features[1]["parameters"]["segments"], 48);

        assert_eq!(features[2]["kind"], "cylinder");
        assert_eq!(features[2]["operation"], "add");
        assert_eq!(features[2]["name"], "螺杆 ⌀16.38×58");
        assert_eq!(features[2]["parameters"]["radius"], 8.19);
        assert_eq!(features[2]["parameters"]["height"], 58.0);
        assert_eq!(features[2]["parameters"]["center"], json!([0.0, 0.0, 29.0]));
        assert_eq!(features[2]["parameters"]["base"], false);
        assert_eq!(features[2]["parameters"]["axis"], "Z");
        assert_eq!(features[2]["parameters"]["segments"], 32);

        for feature in features {
            for source in feature["sources"].as_object().unwrap().values() {
                assert_eq!(source["type"], "derived");
                assert!(source["note"].as_str().unwrap().contains("旧 Python Quick"));
            }
        }
        let warning = output["warnings"].to_string();
        assert!(warning.contains("legacy_catalog_lifting_eye_simplified"));
        assert!(warning.contains("须由几何内核验证"));
    }

    #[test]
    fn only_the_exact_bare_name_uses_this_fallback() {
        for text in ["吊环 M16", "吊环 GB825", "吊环 高58", "吊环 带螺纹"] {
            assert!(parse(text, text).is_none(), "must not swallow: {text}");
        }
        assert!(parse(" 吊环 ", "吊环").is_some());
    }
}
