//! Compatibility port of the legacy Python Quick `EGT型材` directory sample.
//! The old parser emitted a plain cube; this is not the real extrusion profile.

use serde_json::{json, Value};

pub(super) fn parse(raw: &str, normalized: &str) -> Option<Value> {
    // This catalog entry had no parameterized grammar. Keep it deliberately
    // exact so dimensions or added structure are handled by other parsers.
    if raw.trim() != "EGT型材" || !normalized.trim().eq_ignore_ascii_case("EGT型材") {
        return None;
    }

    let evidence = raw;
    let derived = |note: &str| super::derived_source(evidence, note);
    let feature = json!({
        "id":"quick-0001",
        "kind":"box",
        "operation":"add",
        "name":"EGT型材",
        "parameters":{
            "length":80.0,
            "width":80.0,
            "height":88.0,
            "corner":0.0,
            "corner_mode":"fillet",
            "center":[0.0,0.0,0.0],
            "base":false,
            "axis":"Z"
        },
        "sources":{
            "length":derived("旧 Python Quick 的 EGT型材目录样例实际回退值为 80 mm。"),
            "width":derived("旧 Python Quick 的 EGT型材目录样例实际回退值为 80 mm。"),
            "height":derived("旧 Python Quick 的 EGT型材目录样例实际回退值为 88 mm。"),
            "corner":derived("旧 Python Quick 样例输出 corner=0，不生成圆角。"),
            "corner_mode":derived("旧 Python Quick 样例沿用 fillet 模式字段；corner=0，因此没有圆角。"),
            "center":derived("旧 Python Quick 样例将方盒置于全局原点中心。"),
            "base":derived("旧 Python Quick 样例使用 center 对称放置，base=false。"),
            "axis":derived("旧 Python Quick 样例沿全局 Z 轴放置。")
        },
        "relations":[]
    });
    let plan = json!({
        "schema_version":"1.0",
        "name":"EGT型材",
        "source_prompt":raw,
        "units":{"length":"mm","angle":"deg"},
        "coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},
        "features":[feature],
        "pending_questions":[]
    });
    Some(super::result(
        "ready",
        raw,
        Some(plan),
        vec![json!({
            "code":"legacy_egt_profile_simplified_box",
            "message":"迁移的是旧 Python Quick 的简化目录样例：它实际只输出 80×80×88 mm 方盒，并未还原真实 EGT 铝型材挤压槽型截面。"
        })],
        vec![],
        vec![],
    ))
}

#[cfg(test)]
mod tests {
    use super::parse;
    use serde_json::json;

    #[test]
    fn exact_bare_catalog_name_matches_legacy_box_and_provenance() {
        let output = parse("EGT型材", "EGT型材").unwrap();
        assert_eq!(output["status"], "ready");
        let feature = &output["plan"]["features"][0];
        assert_eq!(feature["kind"], "box");
        assert_eq!(feature["parameters"]["length"], 80.0);
        assert_eq!(feature["parameters"]["width"], 80.0);
        assert_eq!(feature["parameters"]["height"], 88.0);
        assert_eq!(feature["parameters"]["corner"], 0.0);
        assert_eq!(feature["parameters"]["corner_mode"], "fillet");
        assert_eq!(feature["parameters"]["center"], json!([0.0, 0.0, 0.0]));
        assert_eq!(feature["parameters"]["base"], false);
        assert_eq!(feature["parameters"]["axis"], "Z");
        for key in [
            "length",
            "width",
            "height",
            "corner",
            "corner_mode",
            "center",
            "base",
            "axis",
        ] {
            assert_eq!(feature["sources"][key]["type"], "derived", "{key}");
        }
        let warning = output["warnings"].to_string();
        assert!(warning.contains("简化目录样例"));
        assert!(warning.contains("未还原真实 EGT 铝型材挤压槽型截面"));
    }

    #[test]
    fn explicit_dimensions_and_added_structure_are_not_consumed() {
        for (raw, normalized) in [
            ("EGT型材 长100", "egt型材 长100"),
            ("EGT型材 加孔", "egt型材 加孔"),
        ] {
            assert!(parse(raw, normalized).is_none(), "{raw:?}");
        }
        assert!(parse("EGT型材", "egt型材").is_some());
    }
}
