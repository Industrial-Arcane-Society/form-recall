//! Rust-owned desktop metadata and diagnostic contracts.
//!
//! These fields replace the old Python bridge UI methods. In particular, a
//! Cordis schema count describes local plugin definitions; it is never an
//! external MCP connection check.

use serde_json::{Value, json};
use std::path::{Component, Path, PathBuf};

const DESCRIPTION: &str = "自然语言驱动的参数化三维建模工作台";

pub fn about(app_version: &str) -> Value {
    json!({
        "app_version": app_version,
        "description": DESCRIPTION,
        "version_source": "rust_package",
    })
}

/// The desktop Agent Host currently passes `mcpServers: []` to DSH. A
/// Cordis tool registry is a different integration surface and must not be
/// used as evidence that any external MCP server is connected.
pub fn external_mcp_status() -> Value {
    json!({
        "configured": false,
        "configured_server_count": 0,
        "connected": null,
        "checked": false,
        "state": "not_configured",
        "source": "dsh_agent_host",
        "reason": "当前桌面 Agent Host 未配置外部 MCP server。",
    })
}

pub fn cordis_schema_status(schema_count: Option<usize>) -> Value {
    match schema_count {
        Some(count) => json!({
            "available": true,
            "state": "schemas_available",
            "transport": "native_cordis_plugin",
            "schema_count": count,
            "connectivity_checked": false,
        }),
        None => json!({
            "available": false,
            "state": "schema_error",
            "transport": "native_cordis_plugin",
            "schema_count": null,
            "connectivity_checked": false,
        }),
    }
}

/// Resolve a help/document path under the packaged documentation directory.
/// The frontend sends a resource-relative `docs/...` path; absolute paths,
/// traversal, missing files, and symlink escapes are rejected.
pub fn resolve_help_path(resource_root: &Path, requested: &str) -> Result<PathBuf, String> {
    let relative = Path::new(requested);
    let first_component_is_docs = relative.components().next().and_then(|part| match part {
        Component::Normal(value) => value.to_str(),
        _ => None,
    }) == Some("docs");
    if requested.trim().is_empty()
        || relative.is_absolute()
        || relative.components().any(|part| {
            matches!(
                part,
                Component::ParentDir | Component::Prefix(_) | Component::RootDir
            )
        })
        || !first_component_is_docs
    {
        return Err("帮助路径必须是 docs/ 下的相对文件路径。".into());
    }

    let root = resource_root
        .canonicalize()
        .map_err(|error| format!("无法解析桌面资源根目录：{error}"))?;
    let docs_root = root
        .join("docs")
        .canonicalize()
        .map_err(|error| format!("桌面资源缺少文档目录：{error}"))?;
    let target = root
        .join(relative)
        .canonicalize()
        .map_err(|error| format!("帮助文件不存在或无法解析：{error}"))?;
    if !target.starts_with(&docs_root) || !target.is_file() {
        return Err("帮助路径只能指向 docs/ 内的普通文件。".into());
    }
    Ok(target)
}

/// Build a stable self-test response from independently gathered Rust state.
/// CATIA is reported as COM registration only; no CATIA process is started.
pub fn selftest(
    app_version: &str,
    resources: Value,
    runtime: Value,
    quick_parser_ready: bool,
) -> Value {
    let cordis = runtime.get("cordis").cloned().unwrap_or(Value::Null);
    let dsh = runtime.get("dsh").cloned().unwrap_or(Value::Null);
    let occt = runtime.get("occt").cloned().unwrap_or(Value::Null);
    let catia = runtime.get("catia").cloned().unwrap_or(Value::Null);
    let external_mcp = runtime
        .get("externalMcp")
        .cloned()
        .unwrap_or_else(external_mcp_status);

    let resources_ok = resources.get("available").and_then(Value::as_bool) == Some(true);
    let cordis_ok = cordis.get("available").and_then(Value::as_bool) == Some(true);
    let dsh_installed = dsh.get("installed").and_then(Value::as_bool) == Some(true);
    let occt_verified = occt.get("verified").and_then(Value::as_bool) == Some(true);
    let ok = resources_ok && cordis_ok && dsh_installed && occt_verified && quick_parser_ready;

    let mut lines = vec![format!("Rust 桌面版本 : {app_version}")];
    lines.push(format!(
        "桌面资源      : {}",
        state_label(resources_ok, "就绪", "不可用")
    ));
    lines.push(format!(
        "Quick 解析器  : {}",
        state_label(quick_parser_ready, "可用", "失败")
    ));
    lines.push(format!(
        "DSH Runtime   : {}",
        state_label(dsh_installed, "已安装并校验", "未安装或无效")
    ));
    lines.push(format!(
        "OCCT Runtime  : {}",
        state_label(occt_verified, "运行文件已校验（尚未加载）", "缺失或无效")
    ));
    lines.push(format!(
        "CATIA COM     : {}（仅查注册，不代表已连接）",
        if catia.get("registered").and_then(Value::as_bool) == Some(true) {
            "已注册"
        } else if catia.get("registered").and_then(Value::as_bool) == Some(false) {
            "未注册"
        } else {
            "未检查"
        }
    ));
    lines.push(format!(
        "Cordis 工具定义: {}（不表示 MCP 连通）",
        cordis
            .get("schema_count")
            .and_then(Value::as_u64)
            .map(|count| count.to_string())
            .unwrap_or_else(|| "不可用".into())
    ));
    lines.push(format!(
        "外部 MCP      : {}",
        if external_mcp.get("state").and_then(Value::as_str) == Some("not_configured") {
            "未配置"
        } else if external_mcp.get("connected").and_then(Value::as_bool) == Some(true) {
            "已连接"
        } else {
            "未连接"
        }
    ));

    json!({
        "ok": ok,
        "lines": lines,
        "checks": {
            "desktop": about(app_version),
            "resources": resources,
            "quickParser": {"available": quick_parser_ready, "state": if quick_parser_ready {"ready"} else {"failed"}},
            "dshRuntime": dsh,
            "occt": occt,
            "catia": catia,
            "cordis": cordis,
            "externalMcp": external_mcp,
        }
    })
}

fn state_label<'a>(ready: bool, ready_text: &'a str, failed_text: &'a str) -> &'a str {
    if ready { ready_text } else { failed_text }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn scratch() -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "ai-modeler-help-path-{}-{stamp}",
            std::process::id()
        ));
        fs::create_dir_all(path.join("docs")).unwrap();
        path
    }

    #[test]
    fn about_preserves_ui_fields_but_uses_rust_desktop_version() {
        let old_python_ui = json!({
            "app_version":"1.2.0",
            "python_version":"3.11.0",
            "description":DESCRIPTION,
        });
        let native = about("0.2.0");
        assert_eq!(native["app_version"], "0.2.0");
        assert_eq!(native["description"], old_python_ui["description"]);
        assert_eq!(native["version_source"], "rust_package");
        assert!(native.get("python_version").is_none());
    }

    #[test]
    fn cordis_tool_count_never_becomes_external_mcp_connectivity() {
        let cordis = cordis_schema_status(Some(25));
        let mcp = external_mcp_status();
        assert_eq!(cordis["schema_count"], 25);
        assert_eq!(cordis["transport"], "native_cordis_plugin");
        assert_eq!(cordis["connectivity_checked"], false);
        assert!(cordis.get("connected").is_none());
        assert_eq!(mcp["state"], "not_configured");
        assert_eq!(mcp["configured_server_count"], 0);
        assert_eq!(mcp["connected"], Value::Null);
    }

    #[test]
    fn selftest_keeps_runtime_catia_and_mcp_states_independent() {
        let report = selftest(
            "0.2.0",
            json!({"available":true}),
            json!({
                "dsh":{"installed":true,"state":"installed"},
                "occt":{"available":true,"verified":true,"state":"runtime_verified","loaded":false},
                "catia":{"registered":true,"connected":null,"state":"com_registered"},
                "cordis":cordis_schema_status(Some(25)),
                "externalMcp":external_mcp_status(),
            }),
            true,
        );
        assert_eq!(report["ok"], true);
        assert_eq!(report["checks"]["dshRuntime"]["state"], "installed");
        assert_eq!(report["checks"]["occt"]["loaded"], false);
        assert_eq!(report["checks"]["catia"]["connected"], Value::Null);
        assert_eq!(report["checks"]["externalMcp"]["state"], "not_configured");
        assert!(report["lines"].as_array().unwrap().iter().any(|line| {
            line.as_str()
                .is_some_and(|line| line.contains("不表示 MCP 连通"))
        }));
    }

    #[test]
    fn help_paths_must_be_existing_files_inside_docs() {
        let root = scratch();
        let help = root.join("docs/README.md");
        fs::write(&help, "help").unwrap();
        assert_eq!(
            resolve_help_path(&root, "docs/README.md").unwrap(),
            help.canonicalize().unwrap()
        );
        assert!(resolve_help_path(&root, "../outside.txt").is_err());
        assert!(resolve_help_path(&root, help.to_str().unwrap()).is_err());
        assert!(resolve_help_path(&root, "docs/missing.md").is_err());
        assert!(resolve_help_path(&root, "src/lib.rs").is_err());
        let _ = fs::remove_dir_all(root);
    }
}
