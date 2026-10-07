//! Compatibility port of the legacy Python Quick bare-name socket screw.
//!
//! This intentionally reproduces the old parser's fixed 3-feature template.
//! Its dimensions are historical fallbacks, not a standards lookup.

use serde_json::{json, Value};

pub(super) fn parse(raw: &str, normalized: &str) -> Option<Value> {
    if raw.trim() != "内六角螺钉" || normalized.trim() != "内六角螺钉" {
        return None;
    }

    let derived = |note: &str| super::derived_source(raw, note);
    let features = vec![
        json!({
            "id":"quick-0001",
            "kind":"cylinder",
            "operation":"add",
            "name":"螺杆 ⌀10×30",
            "parameters":{
                "radius":5.0,
                "height":30.0,
                "center":[0.0,0.0,-15.0],
                "base":false,
                "axis":"Z",
                "segments":32
            },
            "sources":{
                "radius":derived("旧 Python Quick 裸名称模板的杆半径默认值为 5 mm。"),
                "height":derived("旧 Python Quick 裸名称模板的杆长默认值为 30 mm。"),
                "center":derived("旧 Python Quick 将杆中心放在 Z=-15 mm，使杆从 Z=-30 延伸至 Z=0。"),
                "base":derived("旧 Python Quick 裸名称模板使用 base=false。"),
                "axis":derived("旧 Python Quick 裸名称模板沿全局 Z 轴放置。"),
                "segments":derived("旧 Python Quick 裸名称模板使用 32 段圆柱。")
            },
            "relations":[]
        }),
        json!({
            "id":"quick-0002",
            "kind":"cylinder",
            "operation":"add",
            "name":"螺钉头 ⌀16×10",
            "parameters":{
                "radius":8.0,
                "height":10.0,
                "center":[0.0,0.0,5.0],
                "base":false,
                "axis":"Z",
                "segments":48
            },
            "sources":{
                "radius":derived("旧 Python Quick 裸名称模板的头部半径默认值为 8 mm。"),
                "height":derived("旧 Python Quick 裸名称模板的头部高度默认值为 10 mm。"),
                "center":derived("旧 Python Quick 将头部中心放在 Z=5 mm，使头部从 Z=0 延伸至 Z=10。"),
                "base":derived("旧 Python Quick 裸名称模板使用 base=false。"),
                "axis":derived("旧 Python Quick 裸名称模板沿全局 Z 轴放置。"),
                "segments":derived("旧 Python Quick 裸名称模板使用 48 段圆柱。")
            },
            "relations":[]
        }),
        json!({
            "id":"quick-0003",
            "kind":"prism",
            "operation":"cut",
            "name":"内六角孔 对边8×深5",
            "parameters":{
                "profile":[
                    [4.000000000000001,2.309401076758503],
                    [2.828200636599752e-16,4.618802153517007],
                    [-4.0,2.3094010767585047],
                    [-4.000000000000001,-2.309401076758502],
                    [-8.484601909799255e-16,-4.618802153517007],
                    [3.999999999999999,-2.3094010767585056]
                ],
                "height":5.0,
                "axis":"Z",
                "center":[0.0,0.0,5.0]
            },
            "sources":{
                "profile":derived("旧 Python Quick 裸名称模板按对边 8 mm 生成六边形轮廓；保留旧解析器的顶点坐标。"),
                "height":derived("旧 Python Quick 裸名称模板的内六角凹槽深度默认值为 5 mm。"),
                "axis":derived("旧 Python Quick 裸名称模板沿全局 Z 轴切削。"),
                "center":derived("旧 Python Quick 将凹槽中心放在 Z=5 mm，从螺钉头顶面向下切削。")
            },
            "relations":[]
        }),
    ];

    let plan = json!({
        "schema_version":"1.0",
        "name":"内六角螺钉",
        "source_prompt":raw,
        "units":{"length":"mm","angle":"deg"},
        "coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},
        "features":features,
        "pending_questions":[]
    });

    Some(super::result(
        "ready",
        raw,
        Some(plan),
        vec![json!({
            "code":"legacy_catalog_socket_screw_defaults",
            "message":"按旧 Python Quick 裸名称目录模板复现 3 个特征：⌀10×30 螺杆、⌀16×10 圆柱头和对边 8×深 5 的内六角凹槽。此固定回退不代表具体 ISO/GB 规格；请提供 M 规格以生成按规格建模的螺钉。"
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
    fn exact_bare_name_reproduces_legacy_three_feature_template() {
        let output = parse("内六角螺钉", "内六角螺钉").unwrap();
        assert_eq!(output["status"], "ready");
        let features = output["plan"]["features"].as_array().unwrap();
        assert_eq!(features.len(), 3);

        assert_eq!(features[0]["kind"], "cylinder");
        assert_eq!(features[0]["operation"], "add");
        assert_eq!(features[0]["parameters"]["radius"], 5.0);
        assert_eq!(features[0]["parameters"]["height"], 30.0);
        assert_eq!(
            features[0]["parameters"]["center"],
            json!([0.0, 0.0, -15.0])
        );
        assert_eq!(features[0]["parameters"]["segments"], 32);

        assert_eq!(features[1]["kind"], "cylinder");
        assert_eq!(features[1]["operation"], "add");
        assert_eq!(features[1]["parameters"]["radius"], 8.0);
        assert_eq!(features[1]["parameters"]["height"], 10.0);
        assert_eq!(features[1]["parameters"]["center"], json!([0.0, 0.0, 5.0]));
        assert_eq!(features[1]["parameters"]["segments"], 48);

        assert_eq!(features[2]["kind"], "prism");
        assert_eq!(features[2]["operation"], "cut");
        assert_eq!(features[2]["parameters"]["height"], 5.0);
        assert_eq!(features[2]["parameters"]["center"], json!([0.0, 0.0, 5.0]));
        assert_eq!(
            features[2]["parameters"]["profile"]
                .as_array()
                .unwrap()
                .len(),
            6
        );

        for feature in features {
            for key in feature["parameters"].as_object().unwrap().keys() {
                assert_eq!(feature["sources"][key]["type"], "derived", "{key}");
            }
        }
        let warnings = output["warnings"].to_string();
        assert!(warnings.contains("固定回退"));
        assert!(warnings.contains("不代表具体 ISO/GB 规格"));
    }

    #[test]
    fn only_bare_name_is_claimed() {
        assert!(parse("内六角螺钉", "内六角螺钉").is_some());
        for (raw, normalized) in [
            ("内六角螺钉 M5x8", "内六角螺钉 m5x8"),
            ("内六角螺钉 加垫圈", "内六角螺钉 加垫圈"),
            ("内六角螺钉 螺纹长度20", "内六角螺钉 螺纹长度20"),
        ] {
            assert!(parse(raw, normalized).is_none(), "{raw:?}");
        }
    }
}
