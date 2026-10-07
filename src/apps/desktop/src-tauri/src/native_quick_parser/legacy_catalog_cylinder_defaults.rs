//! Exact bare-name fallbacks retained from the old Python Quick part catalog.
//!
//! These dimensions reproduce a legacy directory recipe. They are not
//! engineering recommendations or industry-standard cylinder dimensions.

use serde_json::{Value, json};

const LEGACY_NAMES: [&str; 4] = ["夹紧气缸", "手动夹紧气缸", "双导杆气缸", "接近开关"];

pub(super) fn parse(raw: &str, normalized: &str) -> Option<Value> {
    let name = normalized.trim();
    if !LEGACY_NAMES.contains(&name) {
        return None;
    }

    let source_note = "旧 Python Quick 目录配方默认：圆柱半径 25 mm、高度 100 mm；仅为兼容旧目录样例，不代表行业标准尺寸。";
    let sources = json!({
        "radius": super::derived_source(raw, source_note),
        "height": super::derived_source(raw, source_note),
        "center": super::derived_source(raw, "旧 Python Quick 目录配方默认将圆柱中心置于原点。"),
        "base": super::derived_source(raw, "旧 Python Quick 目录配方默认采用 center 对称圆柱（base=false）。"),
        "axis": super::derived_source(raw, "旧 Python Quick 目录配方默认圆柱轴线为 Z。"),
        "segments": super::derived_source(raw, "旧 Python Quick 目录配方默认使用 48 段圆周离散。")
    });
    let cylinder = super::feature(
        1,
        "cylinder",
        "add",
        json!({
            "radius":25.0,
            "height":100.0,
            "center":[0.0,0.0,0.0],
            "base":false,
            "axis":"Z",
            "segments":48
        }),
        sources,
    );
    let plan = json!({
        "schema_version":"1.0",
        "name":"Quick Part",
        "source_prompt":raw,
        "units":{"length":"mm","angle":"deg"},
        "coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},
        "features":[cylinder],
        "pending_questions":[]
    });
    Some(super::result(
        "ready",
        raw,
        Some(plan),
        vec![super::issue(
            "legacy_catalog_cylinder_defaults",
            "已复现旧 Python Quick 裸目录名称的圆柱默认配方（半径 25 mm、高度 100 mm）；这是旧目录兼容值，不是行业标准尺寸。",
        )],
        vec![],
        vec![],
    ))
}

#[cfg(test)]
mod tests {
    use super::parse;

    #[test]
    fn exact_bare_catalog_names_reproduce_cylinder_defaults_and_provenance() {
        for name in ["夹紧气缸", "手动夹紧气缸", "双导杆气缸", "接近开关"] {
            let result = parse(name, name).expect("known bare directory name");
            let feature = &result["plan"]["features"][0];
            assert_eq!(feature["kind"], "cylinder", "{name}");
            assert_eq!(feature["parameters"]["radius"], 25.0, "{name}");
            assert_eq!(feature["parameters"]["height"], 100.0, "{name}");
            assert_eq!(feature["parameters"]["center"], serde_json::json!([0.0, 0.0, 0.0]));
            assert_eq!(feature["parameters"]["base"], false);
            assert_eq!(feature["parameters"]["axis"], "Z");
            assert_eq!(feature["parameters"]["segments"], 48);
            assert_eq!(feature["sources"]["radius"]["type"], "derived");
            assert!(feature["sources"]["radius"]["note"].as_str().unwrap().contains("旧 Python Quick 目录配方默认"));
            assert!(feature["sources"]["height"]["note"].as_str().unwrap().contains("不代表行业标准尺寸"));
            assert!(result["warnings"].to_string().contains("legacy_catalog_cylinder_defaults"));
            assert!(result["warnings"].to_string().contains("不代表行业标准尺寸"));
        }
    }

    #[test]
    fn dimensions_and_feature_modifiers_are_left_to_other_parsers() {
        for text in [
            "夹紧气缸直径 50 高 100",
            "夹紧气缸半径25mm",
            "手动夹紧气缸带安装孔",
            "双导杆气缸带导杆",
            "接近开关开槽",
        ] {
            assert!(parse(text, text).is_none(), "must not swallow modifier: {text}");
        }
    }
}
