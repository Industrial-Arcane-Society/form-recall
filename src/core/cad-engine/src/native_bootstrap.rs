//! Desktop resource discovery and independently reported runtime readiness.
//!
//! Packaged native desktop features need prompt, knowledge, plugin, and DSH
//! manifest assets. CATIA adapter assets are optional until CATIA is invoked.

use serde_json::{Value, json};
use std::path::{Path, PathBuf};

const REQUIRED_FILES: &[&str] = &[
    "ai_modeler/agent/dsh_native_tools.mjs",
    "ai_modeler/agent/dsh_attachment_store.mjs",
    "ai_modeler/agent/dsh_stream_bridge.mjs",
    "prompts/dsh_agent/system.md",
    "packaging/dsh-runtime/runtime-manifest.json",
    "packaging/dsh-runtime/package.json",
    "packaging/dsh-runtime/package-lock.json",
    "kb/bolt_std.json",
    "kb/modeling_recipes.json",
    "kb/native_knowledge_enrichments.v1.json",
    "datasets/训练提示词/全部提示词.jsonl",
];

const OPTIONAL_CATIA_FILES: &[&str] = &[
    "ai_modeler/catia_com_adapter.py",
    "packaging/desktop-catia-requirements-win-x64.txt",
];

fn all_files_exist(root: &Path, files: &[&str]) -> bool {
    files.iter().all(|relative| root.join(relative).is_file())
}

pub fn validate_resource_root(root: &Path) -> Result<PathBuf, String> {
    let canonical = root
        .canonicalize()
        .map_err(|_| format!("桌面资源目录无法解析：{}", root.display()))?;
    if !canonical.is_dir() {
        return Err("桌面资源根路径不是目录。".into());
    }
    let missing = REQUIRED_FILES
        .iter()
        .filter(|relative| !canonical.join(relative).is_file())
        .copied()
        .collect::<Vec<_>>();
    if !missing.is_empty() {
        return Err(format!(
            "桌面资源缺少 {} 个必需文件：{}",
            missing.len(),
            missing.join("、")
        ));
    }
    if !canonical.join("kb").is_dir() {
        return Err("桌面资源缺少原生 Agent 知识库目录。".into());
    }
    Ok(canonical)
}

/// An explicit override is authoritative. An existing packaged resource root
/// must validate rather than silently falling back to checkout resources.
pub fn resolve_resource_root(
    explicit: Option<&Path>,
    packaged: Option<&Path>,
    development: Option<&Path>,
) -> Result<PathBuf, String> {
    if let Some(root) = explicit {
        return validate_resource_root(root);
    }
    if let Some(root) = packaged.filter(|path| path.is_dir()) {
        return validate_resource_root(root);
    }
    if let Some(root) = development {
        return validate_resource_root(root);
    }
    Err("找不到 AI Modeler 桌面静态资源。".into())
}

pub fn resource_status(root: &Path) -> Result<Value, String> {
    let root = validate_resource_root(root)?;
    Ok(json!({
        "available": true,
        "root": root.to_string_lossy(),
        "nativeAgentAssets": all_files_exist(&root, &REQUIRED_FILES[..4]),
        "dshManifest": all_files_exist(&root, &REQUIRED_FILES[4..7]),
        "catiaAdapterAssets": all_files_exist(&root, OPTIONAL_CATIA_FILES),
        "nativeKnowledgeAssets": all_files_exist(&root, &REQUIRED_FILES[7..]),
        "knowledge": root.join("kb").is_dir(),
    }))
}

pub fn catia_readiness(registered: Option<bool>) -> Value {
    match registered {
        Some(true) => json!({
            "available": null,
            "registered": true,
            "connected": null,
            "connectionChecked": false,
            "state": "com_registered",
        }),
        Some(false) => json!({
            "available": null,
            "registered": false,
            "connected": null,
            "connectionChecked": false,
            "state": "not_registered",
        }),
        None => json!({
            "available": null,
            "registered": null,
            "connected": null,
            "connectionChecked": false,
            "state": "not_checked",
        }),
    }
}

pub fn runtime_readiness(python_installed: bool, dsh: Value, occt: Value, catia: Value) -> Value {
    json!({
        "python":{"available":python_installed,"required":false},
        "dsh":dsh,
        "occt":occt,
        "catia":catia,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            let stamp = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos();
            let root = std::env::temp_dir().join(format!(
                "ai-modeler-native-bootstrap-{}-{stamp}",
                std::process::id()
            ));
            fs::create_dir_all(&root).unwrap();
            Self(root)
        }

        fn resource_root(&self) -> PathBuf {
            let root = self.0.join("resources");
            for relative in REQUIRED_FILES.iter().chain(OPTIONAL_CATIA_FILES.iter()) {
                let path = root.join(relative);
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                fs::write(path, b"fixture resource").unwrap();
            }
            fs::create_dir_all(root.join("kb")).unwrap();
            root
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn resource_discovery_does_not_require_python_source_files() {
        let scratch = Scratch::new();
        let root = scratch.resource_root();
        assert!(!root.join("ai_modeler/__main__.py").exists());
        let resolved = resolve_resource_root(None, None, Some(&root)).unwrap();
        assert_eq!(resolved, root.canonicalize().unwrap());
        let status = resource_status(&root).unwrap();
        assert_eq!(status["available"], true);
        assert_eq!(status["nativeAgentAssets"], true);
        assert_eq!(status["dshManifest"], true);
        assert_eq!(status["catiaAdapterAssets"], true);
    }

    #[test]
    fn missing_optional_catia_assets_do_not_block_dsh_or_quick_resources() {
        let scratch = Scratch::new();
        let root = scratch.resource_root();
        for relative in OPTIONAL_CATIA_FILES {
            fs::remove_file(root.join(relative)).unwrap();
        }

        let resolved = resolve_resource_root(None, None, Some(&root)).unwrap();
        assert_eq!(resolved, root.canonicalize().unwrap());

        let status = resource_status(&root).unwrap();
        assert_eq!(status["available"], true);
        assert_eq!(status["nativeAgentAssets"], true);
        assert_eq!(status["dshManifest"], true);
        assert_eq!(status["nativeKnowledgeAssets"], true);
        assert_eq!(status["catiaAdapterAssets"], false);
    }

    #[test]
    fn missing_required_assets_are_all_reported_and_fail_closed() {
        let scratch = Scratch::new();
        let root = scratch.resource_root();
        fs::remove_file(root.join("ai_modeler/agent/dsh_native_tools.mjs")).unwrap();
        fs::remove_file(root.join("kb/native_knowledge_enrichments.v1.json")).unwrap();
        fs::remove_file(root.join("packaging/dsh-runtime/package-lock.json")).unwrap();

        let error = resolve_resource_root(None, None, Some(&root)).unwrap_err();
        assert!(error.contains("缺少 3 个必需文件"));
        assert!(error.contains("ai_modeler/agent/dsh_native_tools.mjs"));
        assert!(error.contains("packaging/dsh-runtime/package-lock.json"));
        assert!(error.contains("kb/native_knowledge_enrichments.v1.json"));
    }

    #[test]
    fn invalid_existing_package_resources_fail_closed_without_dev_fallback() {
        let scratch = Scratch::new();
        let packaged = scratch.0.join("packaged");
        fs::create_dir_all(&packaged).unwrap();
        let development = scratch.resource_root();
        assert!(resolve_resource_root(None, Some(&packaged), Some(&development)).is_err());
    }

    #[test]
    fn python_dsh_occt_and_catia_readiness_are_separate() {
        let report = runtime_readiness(
            false,
            json!({"available":true,"installed":true,"state":"ready"}),
            json!({"available":false,"state":"missing"}),
            json!({"available":null,"state":"not_checked"}),
        );
        assert_eq!(report["python"]["available"], false);
        assert_eq!(report["python"]["required"], false);
        assert_eq!(report["dsh"]["available"], true);
        assert_eq!(report["occt"]["available"], false);
        assert_eq!(report["catia"]["available"], Value::Null);
        assert_eq!(report["catia"]["state"], "not_checked");
        assert_eq!(catia_readiness(Some(true))["state"], "com_registered");
        assert_eq!(catia_readiness(Some(false))["available"], Value::Null);
        assert_eq!(catia_readiness(Some(false))["connected"], Value::Null);
        assert_eq!(catia_readiness(Some(false))["connectionChecked"], false);
    }
}
