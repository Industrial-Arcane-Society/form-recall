use serde_json::{json, Value};

/// Reproduce the old Python Quick fallback for the ten catalog directory names
/// whose only legacy output was a centered, non-base Z-axis box.
pub(super) fn parse(raw: &str, normalized: &str) -> Option<Value> {
    let name = raw.trim();
    let (length, width, height) = match name {
        "支座(铝)" => (50.0, 30.0, 10.0),
        "EGT连接件" | "销座" | "支座" | "止块" | "标牌" | "补高台" | "气缸连接块" => {
            (50.0, 30.0, 10.0)
        }
        "分气块" => (120.0, 65.0, 35.0),
        "阀岛" => (50.0, 30.0, 10.0),
        _ => return None,
    };

    // Only the bare directory label is eligible. Any added words, dimensions,
    // or modifiers belong to the regular explicit-feature parsers.
    if !normalized.trim().eq_ignore_ascii_case(name) || raw.trim() != name {
        return None;
    }

    let evidence = raw;
    let note = "沿用旧 Python Quick 目录样例默认方块尺寸。";
    let feature = json!({
        "id":"quick-0001",
        "kind":"box",
        "operation":"add",
        "name":name,
        "parameters":{
            "length":length,
            "width":width,
            "height":height,
            "corner":0.0,
            "corner_mode":"fillet",
            "center":[0.0,0.0,0.0],
            "base":false,
            "axis":"Z"
        },
        "sources":{
            "length":super::derived_source(evidence, note),
            "width":super::derived_source(evidence, note),
            "height":super::derived_source(evidence, note),
            "corner":super::derived_source(evidence, "旧 Python Quick 目录样例保持直角。"),
            "corner_mode":super::derived_source(evidence, "沿用旧 Python Quick 方块参数。"),
            "center":super::derived_source(evidence, "旧 Python Quick 目录样例将方块中心置于原点。"),
            "base":super::derived_source(evidence, "旧 Python Quick 目录样例使用 base=false。"),
            "axis":super::derived_source(evidence, "旧 Python Quick 目录样例沿用 Z 轴。")
        },
        "relations":[]
    });
    Some(super::plan_result(
        raw,
        name,
        vec![feature],
        vec![super::issue(
            "legacy_catalog_box_default",
            "沿用旧 Python Quick 目录样例的默认方块尺寸。",
        )],
    ))
}

#[cfg(test)]
mod tests {
    use super::parse;

    #[test]
    fn matches_only_the_ten_bare_catalog_names_and_preserves_legacy_defaults() {
        for (name, dims) in [
            ("EGT连接件", [50.0, 30.0, 10.0]),
            ("销座", [50.0, 30.0, 10.0]),
            ("支座", [50.0, 30.0, 10.0]),
            ("支座(铝)", [50.0, 30.0, 10.0]),
            ("止块", [50.0, 30.0, 10.0]),
            ("分气块", [120.0, 65.0, 35.0]),
            ("阀岛", [50.0, 30.0, 10.0]),
            ("标牌", [50.0, 30.0, 10.0]),
            ("补高台", [50.0, 30.0, 10.0]),
            ("气缸连接块", [50.0, 30.0, 10.0]),
        ] {
            let result = parse(name, name).unwrap();
            let f = &result["plan"]["features"][0];
            assert_eq!(f["parameters"]["length"], dims[0]);
            assert_eq!(f["parameters"]["width"], dims[1]);
            assert_eq!(f["parameters"]["height"], dims[2]);
            assert_eq!(
                f["parameters"]["center"],
                serde_json::json!([0.0, 0.0, 0.0])
            );
            assert_eq!(f["parameters"]["base"], false);
            assert_eq!(f["parameters"]["axis"], "Z");
            for key in ["length", "width", "height", "center", "base", "axis"] {
                assert_eq!(f["sources"][key]["type"], "derived");
                assert!(f["sources"][key]["note"]
                    .as_str()
                    .unwrap()
                    .contains("旧 Python Quick"));
            }
        }
    }

    #[test]
    fn does_not_consume_dimensions_or_modifiers() {
        for input in [
            "支座 80x40x12",
            "支座 加两个孔",
            "支座 开槽",
            "支座(铝) 60x30x10",
        ] {
            assert!(parse(input, input).is_none(), "unexpected match: {input}");
        }
        assert!(parse("支座(铝)", "支座(铝)").is_some());
    }
}
