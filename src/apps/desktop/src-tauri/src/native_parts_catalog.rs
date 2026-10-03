//! Embedded, read-only metadata for the legacy parts/examples panels.
//!
//! The data file is a snapshot of the old Python `parts.list` and
//! `samples.list` results. It is descriptive UI metadata only; it does not
//! declare that the geometry kernel can build any listed catalog item.

use serde_json::{Value, json};
use std::sync::OnceLock;

const CATALOG_JSON: &str = include_str!("../resources/native_parts_catalog.json");

fn catalog() -> &'static Value {
    static CATALOG: OnceLock<Value> = OnceLock::new();
    CATALOG.get_or_init(|| {
        serde_json::from_str(CATALOG_JSON)
            .expect("embedded native parts catalog must be valid JSON")
    })
}

/// Return the read-only catalog rows using the old Python category/alias
/// substring filter and insertion order.
pub fn list_parts(query: Option<&str>) -> Vec<Value> {
    let query = query.unwrap_or_default().trim().to_lowercase();
    catalog()["parts"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|row| {
            if query.is_empty() {
                return true;
            }
            row["category"]
                .as_str()
                .is_some_and(|category| category.to_lowercase().contains(&query))
                || row["aliases"].as_array().is_some_and(|aliases| {
                    aliases.iter().any(|alias| {
                        alias
                            .as_str()
                            .is_some_and(|alias| alias.to_lowercase().contains(&query))
                    })
                })
        })
        .cloned()
        .collect()
}

/// Return the canonical text samples in their legacy UI order.
pub fn list_samples() -> Vec<Value> {
    catalog()["samples"].as_array().cloned().unwrap_or_default()
}

#[tauri::command(rename_all = "camelCase")]
pub fn native_parts_list(query: Option<String>) -> Vec<Value> {
    list_parts(query.as_deref())
}

#[tauri::command]
pub fn native_samples_list() -> Vec<Value> {
    list_samples()
}

/// Return both legacy collections for parity tooling.
pub fn snapshot() -> Value {
    json!({
        "parts": list_parts(None),
        "samples": list_samples(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedded_rows_preserve_legacy_order_fields_counts_and_samples() {
        let rows = list_parts(None);
        let samples = list_samples();
        assert_eq!(rows.len(), 66);
        assert_eq!(samples.len(), 17);
        assert_eq!(rows.first().unwrap()["category"], "深沟球轴承");
        assert_eq!(rows.last().unwrap()["category"], "接近开关");
        assert_eq!(rows[0]["manual_count"], 103);
        assert_eq!(rows[0]["sample_prompt"], "深沟球轴承 内径20 外径47 宽度14");
        assert_eq!(samples[0]["label"], "文档示例: 100×20 矩形拉伸");
        assert_eq!(samples.last().unwrap()["label"], "三角转子 (极高难度)");
    }

    #[test]
    fn category_and_alias_queries_keep_legacy_substring_behavior() {
        let category = list_parts(Some("圆锥滚子轴承"));
        assert_eq!(category.len(), 1);
        assert_eq!(category[0]["category"], "圆锥滚子轴承");

        let alias = list_parts(Some("球轴承"));
        assert!(alias.iter().any(|row| row["category"] == "深沟球轴承"));

        assert!(list_parts(Some("does not exist")).is_empty());
        assert_eq!(list_parts(Some("  ")).len(), 66);
    }

    #[test]
    fn rows_are_descriptive_and_do_not_claim_geometry_support() {
        for row in list_parts(None) {
            assert!(row.get("category").is_some());
            assert!(row.get("aliases").is_some());
            assert!(row.get("params").is_some());
            assert!(row.get("manual_count").is_some());
            assert!(row.get("sample_prompt").is_some());
            assert!(row.get("supported").is_none());
            assert!(row.get("geometry_capability").is_none());
        }
    }
}
