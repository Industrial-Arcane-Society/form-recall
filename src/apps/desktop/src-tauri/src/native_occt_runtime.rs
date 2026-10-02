//! Verification of the packaged OCCT runtime before the bridge DLL is loaded.
//!
//! This module only validates bytes and returns the canonical bridge path. It
//! never loads a DLL and deliberately does not require build-only headers,
//! import libraries, or the smoke-test executable to be present at runtime.

use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fs::{self, File};
use std::io::Read;
use std::path::{Component, Path, PathBuf};

const REQUIRED_ABI: u64 = 1;
const EXPECTED_OCCT_VERSION: &str = "7.9.3";
const MAX_MANIFEST_BYTES: u64 = 2 * 1024 * 1024;
const EXPECTED_OCCT_DLLS: [&str; 13] = [
    "TKBO.dll",
    "TKBool.dll",
    "TKBRep.dll",
    "TKernel.dll",
    "TKG2d.dll",
    "TKG3d.dll",
    "TKGeomAlgo.dll",
    "TKGeomBase.dll",
    "TKMath.dll",
    "TKMesh.dll",
    "TKPrim.dll",
    "TKShHealing.dll",
    "TKTopAlgo.dll",
];
const EXPECTED_MSVC_DLLS: [&str; 3] = ["msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"];

/// Resolve the explicitly configured, packaged, or development runtime.
/// An explicit or present packaged runtime must validate; never conceal a bad
/// release by silently switching to a developer's build.
pub fn resolve_manifest(legacy_root: &Path) -> Result<PathBuf, String> {
    available_manifest(legacy_root)?.ok_or_else(||
        "未找到 OCCT 7.9.3 几何运行时；安装包必须包含 occt-runtime，开发时可设置 AIMODELER_OCCT_MANIFEST。".into())
}

/// Read-only sessions may start without geometry. A present invalid runtime is
/// still an error; modelling commands use the mandatory resolver above.
pub fn available_manifest(root: &Path) -> Result<Option<PathBuf>, String> {
    resolve_manifest_with_override(
        root,
        std::env::var_os("AIMODELER_OCCT_MANIFEST").map(PathBuf::from),
    )
}

fn resolve_manifest_with_override(
    root: &Path,
    explicit: Option<PathBuf>,
) -> Result<Option<PathBuf>, String> {
    if let Some(path) = explicit {
        verified_bridge_path(&path)?;
        return path.canonicalize().map(Some).map_err(|e| e.to_string());
    }
    let parent = root.parent().ok_or("应用源码目录缺少父目录。")?;
    let packaged = parent.join("occt-runtime/runtime-manifest.json");
    if packaged.exists() {
        verified_bridge_path(&packaged)?;
        return packaged.canonicalize().map(Some).map_err(|e| e.to_string());
    }
    let development = parent.join("apps/desktop/src-tauri/target/f4-occt/runtime-manifest.json");
    if development.exists() {
        verified_bridge_path(&development)?;
        return development
            .canonicalize()
            .map(Some)
            .map_err(|e| e.to_string());
    }
    Ok(None)
}

/// Validate a F4 runtime manifest and all runtime DLL bytes, returning the
/// canonical absolute path of the verified `occt_bridge.dll`.
pub fn verified_bridge_path(manifest_path: &Path) -> Result<PathBuf, String> {
    let manifest_path = manifest_path
        .canonicalize()
        .map_err(|error| format!("OCCT runtime manifest 不存在或无法解析：{error}"))?;
    if !manifest_path.is_file() {
        return Err("OCCT runtime manifest 必须是普通文件。".into());
    }
    let root = manifest_path
        .parent()
        .ok_or("OCCT runtime manifest 缺少根目录。")?
        .canonicalize()
        .map_err(|error| format!("OCCT runtime 根目录无法解析：{error}"))?;
    let metadata = fs::metadata(&manifest_path)
        .map_err(|error| format!("无法读取 OCCT runtime manifest 元数据：{error}"))?;
    if metadata.len() > MAX_MANIFEST_BYTES {
        return Err("OCCT runtime manifest 超过 2 MiB。".into());
    }
    let bytes = fs::read(&manifest_path)
        .map_err(|error| format!("无法读取 OCCT runtime manifest：{error}"))?;
    let manifest: Value = serde_json::from_slice(&bytes)
        .map_err(|_| "OCCT runtime manifest 不是有效 JSON。".to_owned())?;

    let version = manifest
        .get("occt_version")
        .and_then(Value::as_str)
        .ok_or("OCCT runtime manifest 缺少有效 occt_version。")?;
    if version != EXPECTED_OCCT_VERSION {
        return Err(format!(
            "OCCT runtime 版本不匹配；要求 {EXPECTED_OCCT_VERSION}，实际为 {version}。"
        ));
    }
    if manifest.get("abi_version").and_then(Value::as_u64) != Some(REQUIRED_ABI)
        || manifest.get("bridge_abi_version").and_then(Value::as_u64) != Some(REQUIRED_ABI)
    {
        return Err(
            "OCCT runtime manifest ABI 不匹配；要求 abi_version 与 bridge_abi_version 均为 1。"
                .into(),
        );
    }

    let bridge_entries = manifest
        .get("bridge_artifacts")
        .and_then(Value::as_array)
        .ok_or("OCCT runtime manifest 缺少 bridge_artifacts。")?;
    let bridge_rows = bridge_entries
        .iter()
        .filter(|entry| {
            entry
                .get("path")
                .and_then(Value::as_str)
                .and_then(|path| Path::new(path).file_name())
                .is_some_and(|name| {
                    name.to_string_lossy()
                        .eq_ignore_ascii_case("occt_bridge.dll")
                })
        })
        .collect::<Vec<_>>();
    if bridge_rows.len() != 1 {
        return Err("OCCT runtime manifest 必须恰好声明一个 occt_bridge.dll。".into());
    }

    let mut seen_paths = HashSet::new();
    let bridge_path = verify_runtime_file(&root, bridge_rows[0], "OCCT bridge", &mut seen_paths)?;
    let bridge_directory = bridge_path.parent().ok_or("OCCT bridge 路径缺少父目录。")?;

    verify_runtime_group(
        &root,
        manifest.get("occt_runtime_dlls"),
        "occt_runtime_dlls",
        &EXPECTED_OCCT_DLLS,
        version,
        bridge_directory,
        &mut seen_paths,
    )?;
    verify_runtime_group(
        &root,
        manifest.get("msvc_runtime_dlls"),
        "msvc_runtime_dlls",
        &EXPECTED_MSVC_DLLS,
        version,
        bridge_directory,
        &mut seen_paths,
    )?;

    Ok(bridge_path)
}

fn verify_runtime_group(
    root: &Path,
    value: Option<&Value>,
    field: &str,
    expected_names: &[&str],
    occt_version: &str,
    bridge_directory: &Path,
    seen_paths: &mut HashSet<String>,
) -> Result<(), String> {
    let entries = value
        .and_then(Value::as_array)
        .ok_or_else(|| format!("OCCT {occt_version} runtime manifest 缺少 {field}。"))?;
    if entries.len() != expected_names.len() {
        return Err(format!(
            "OCCT {occt_version} runtime manifest 的 {field} 应有 {} 项，实际为 {} 项。",
            expected_names.len(),
            entries.len()
        ));
    }
    let mut found_names = HashSet::new();
    for entry in entries {
        let path = entry
            .get("path")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("{field} 中有条目缺少 path。"))?;
        let file_name = Path::new(path)
            .file_name()
            .ok_or_else(|| format!("{field} 包含无效文件路径。"))?
            .to_string_lossy()
            .to_ascii_lowercase();
        let expected = expected_names
            .iter()
            .find(|name| name.eq_ignore_ascii_case(&file_name))
            .ok_or_else(|| format!("{field} 包含未预期的 DLL：{file_name}。"))?;
        let verified = verify_runtime_file(root, entry, field, seen_paths)?;
        if !found_names.insert(expected.to_ascii_lowercase()) {
            return Err(format!("{field} 重复声明 DLL：{expected}。"));
        }
        if verified.parent() != Some(bridge_directory) {
            return Err(format!(
                "{field} 中的 {expected} 必须与 occt_bridge.dll 位于同一目录。"
            ));
        }
    }
    for expected in expected_names {
        if !found_names.contains(&expected.to_ascii_lowercase()) {
            return Err(format!("{field} 缺少必需 DLL：{expected}。"));
        }
    }
    Ok(())
}

fn verify_runtime_file(
    root: &Path,
    entry: &Value,
    group: &str,
    seen_paths: &mut HashSet<String>,
) -> Result<PathBuf, String> {
    let relative = entry
        .get("path")
        .and_then(Value::as_str)
        .filter(|path| !path.trim().is_empty())
        .ok_or_else(|| format!("{group} 中有条目缺少 path。"))?;
    let sha256 = entry
        .get("sha256")
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{group} 中 {relative} 缺少 sha256。"))?;
    if !is_sha256_hex(sha256) {
        return Err(format!("{group} 中 {relative} 的 sha256 格式无效。"));
    }

    let relative_path = Path::new(relative);
    if relative_path.is_absolute()
        || relative_path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(format!(
            "{group} 中的路径必须是根目录内的相对路径：{relative}。"
        ));
    }
    let normalized_relative = relative.replace('\\', "/").to_ascii_lowercase();
    if !seen_paths.insert(normalized_relative) {
        return Err(format!("OCCT runtime manifest 重复声明路径：{relative}。"));
    }

    let candidate = root.join(relative_path);
    let canonical = candidate
        .canonicalize()
        .map_err(|_| format!("OCCT runtime 文件缺失或无法解析：{relative}。"))?;
    if !canonical.starts_with(root) {
        return Err(format!(
            "OCCT runtime 文件逃逸出 manifest 根目录：{relative}。"
        ));
    }
    if !canonical.is_file() {
        return Err(format!("OCCT runtime 路径不是普通文件：{relative}。"));
    }
    let canonical_key = canonical.to_string_lossy().to_ascii_lowercase();
    if !seen_paths.insert(canonical_key) {
        return Err(format!(
            "OCCT runtime manifest 的不同路径指向同一文件：{relative}。"
        ));
    }

    let actual_hash =
        sha256_file(&canonical).map_err(|_| format!("无法读取 OCCT runtime 文件：{relative}。"))?;
    if !actual_hash.eq_ignore_ascii_case(sha256) {
        return Err(format!("OCCT runtime 文件哈希不匹配：{relative}。"));
    }
    Ok(canonical)
}

fn sha256_file(path: &Path) -> std::io::Result<String> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

fn is_sha256_hex(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(1);

    struct Fixture {
        root: PathBuf,
        manifest: PathBuf,
        bridge: PathBuf,
        outside_dir: PathBuf,
        value: Value,
    }

    impl Fixture {
        fn new() -> Self {
            let id = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
            let suffix = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos();
            let root = std::env::temp_dir().join(format!(
                "ai-modeler-occt-runtime-{}-{suffix}-{id}",
                std::process::id()
            ));
            let bin = root.join("install/win64/vc14/bin");
            fs::create_dir_all(&bin).unwrap();
            let bridge = bin.join("occt_bridge.dll");
            let bridge_bytes = b"fixture bridge bytes; never loaded";
            fs::write(&bridge, bridge_bytes).unwrap();
            let outside_dir = root.parent().unwrap().join(format!(
                "{}-outside",
                root.file_name().unwrap().to_string_lossy()
            ));
            fs::create_dir_all(&outside_dir).unwrap();
            let outside = outside_dir.join("occt_bridge.dll");
            fs::write(&outside, b"outside bytes").unwrap();

            let occt = EXPECTED_OCCT_DLLS
                .iter()
                .enumerate()
                .map(|(index, name)| {
                    let bytes = format!("fixture OCCT DLL {index} {name}");
                    fs::write(bin.join(name), bytes.as_bytes()).unwrap();
                    json!({"path":format!("install/win64/vc14/bin/{name}"),"sha256":sha256_bytes(bytes.as_bytes())})
                })
                .collect::<Vec<_>>();
            let msvc = EXPECTED_MSVC_DLLS
                .iter()
                .enumerate()
                .map(|(index, name)| {
                    let bytes = format!("fixture MSVC runtime {index} {name}");
                    fs::write(bin.join(name), bytes.as_bytes()).unwrap();
                    json!({"path":format!("install/win64/vc14/bin/{name}"),"sha256":sha256_bytes(bytes.as_bytes())})
                })
                .collect::<Vec<_>>();
            let value = json!({
                "occt_version":"7.9.3",
                "abi_version":1,
                "bridge_abi_version":1,
                "bridge_artifacts":[
                    {"path":"install/win64/vc14/bin/occt_bridge.dll","sha256":sha256_bytes(bridge_bytes)},
                    {"path":"install/win64/vc14/lib/occt_bridge.lib","sha256":"unused-missing-dev-artifact"},
                    {"path":"bridge-install/include/occt_bridge.h","sha256":"unused-missing-dev-artifact"}
                ],
                "occt_runtime_dlls":occt,
                "msvc_runtime_dlls":msvc,
                "smoke_test_executable":{"path":"install/win64/vc14/bin/occt_bridge_smoke.exe","sha256":"unused-not-required"}
            });
            let manifest = root.join("runtime-manifest.json");
            fs::write(&manifest, serde_json::to_vec_pretty(&value).unwrap()).unwrap();
            Self {
                root,
                manifest,
                bridge,
                outside_dir,
                value,
            }
        }

        fn write_value(&self, value: &Value) {
            fs::write(&self.manifest, serde_json::to_vec_pretty(value).unwrap()).unwrap();
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
            let _ = fs::remove_dir_all(&self.outside_dir);
        }
    }

    fn sha256_bytes(bytes: &[u8]) -> String {
        Sha256::digest(bytes)
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect()
    }

    #[test]
    fn verifies_f4_runtime_hashes_without_requiring_development_artifacts() {
        let fixture = Fixture::new();
        let bridge = verified_bridge_path(&fixture.manifest).unwrap();
        assert_eq!(bridge, fixture.bridge.canonicalize().unwrap());
        assert!(bridge.is_absolute());
    }

    fn copy_fixture(fixture: &Fixture, destination: &Path) -> PathBuf {
        for field in ["bridge_artifacts", "occt_runtime_dlls", "msvc_runtime_dlls"] {
            for row in fixture.value[field].as_array().unwrap() {
                let relative = row["path"].as_str().unwrap();
                let from = fixture.root.join(relative);
                if from.is_file() {
                    let to = destination.join(relative);
                    fs::create_dir_all(to.parent().unwrap()).unwrap();
                    fs::copy(from, to).unwrap();
                }
            }
        }
        let manifest = destination.join("runtime-manifest.json");
        fs::write(&manifest, serde_json::to_vec(&fixture.value).unwrap()).unwrap();
        manifest
    }

    #[test]
    fn resolves_packaged_runtime_and_rejects_corruption_without_dev_fallback() {
        let fixture = Fixture::new();
        let root = fixture.root.join("app");
        fs::create_dir_all(&root).unwrap();
        let packaged = copy_fixture(&fixture, &fixture.root.join("occt-runtime"));
        copy_fixture(
            &fixture,
            &fixture.root.join("apps/desktop/src-tauri/target/f4-occt"),
        );
        assert_eq!(
            resolve_manifest_with_override(&root, None).unwrap(),
            Some(packaged.canonicalize().unwrap())
        );
        fs::write(
            fixture
                .root
                .join("occt-runtime/install/win64/vc14/bin/TKBO.dll"),
            b"corrupt",
        )
        .unwrap();
        assert!(
            resolve_manifest_with_override(&root, None)
                .unwrap_err()
                .contains("哈希不匹配")
        );
    }

    #[test]
    fn resolves_development_and_explicit_runtime_and_reports_absence() {
        let fixture = Fixture::new();
        let root = fixture.root.join("src/legacy");
        fs::create_dir_all(&root).unwrap();
        assert!(
            resolve_manifest_with_override(&root, None)
                .unwrap()
                .is_none()
        );
        let dev = copy_fixture(
            &fixture,
            &fixture
                .root
                .join("src/apps/desktop/src-tauri/target/f4-occt"),
        );
        assert_eq!(
            resolve_manifest_with_override(&root, None).unwrap(),
            Some(dev.canonicalize().unwrap())
        );
        assert_eq!(
            resolve_manifest_with_override(&root, Some(fixture.manifest.clone())).unwrap(),
            Some(fixture.manifest.canonicalize().unwrap())
        );
        assert!(
            resolve_manifest_with_override(&root, Some(fixture.root.join("missing.json"))).is_err()
        );
    }

    #[test]
    fn rejects_tampered_runtime_bytes_and_hashes() {
        let fixture = Fixture::new();
        fs::write(
            fixture.root.join("install/win64/vc14/bin/TKBO.dll"),
            b"tampered",
        )
        .unwrap();
        let error = verified_bridge_path(&fixture.manifest).unwrap_err();
        assert!(error.contains("哈希不匹配"));

        let fixture = Fixture::new();
        let mut changed = fixture.value.clone();
        changed["bridge_artifacts"][0]["sha256"] = json!("0".repeat(64));
        fixture.write_value(&changed);
        let error = verified_bridge_path(&fixture.manifest).unwrap_err();
        assert!(error.contains("哈希不匹配"));
    }

    #[test]
    fn rejects_parent_traversal_even_when_the_target_exists() {
        let fixture = Fixture::new();
        let mut changed = fixture.value.clone();
        changed["bridge_artifacts"][0]["path"] = json!(format!(
            "../{}/occt_bridge.dll",
            fixture.outside_dir.file_name().unwrap().to_string_lossy()
        ));
        fixture.write_value(&changed);
        let error = verified_bridge_path(&fixture.manifest).unwrap_err();
        assert!(error.contains("相对路径"));
    }

    #[test]
    fn rejects_missing_occt_dll_and_duplicate_runtime_paths() {
        let fixture = Fixture::new();
        fs::remove_file(fixture.root.join("install/win64/vc14/bin/TKBO.dll")).unwrap();
        let error = verified_bridge_path(&fixture.manifest).unwrap_err();
        assert!(error.contains("文件缺失"));

        let fixture = Fixture::new();
        let mut changed = fixture.value.clone();
        changed["occt_runtime_dlls"][1] = changed["occt_runtime_dlls"][0].clone();
        fixture.write_value(&changed);
        let error = verified_bridge_path(&fixture.manifest).unwrap_err();
        assert!(error.contains("重复声明路径"));
    }

    #[test]
    fn rejects_wrong_abi_and_non_colocated_dependencies() {
        let fixture = Fixture::new();
        let mut changed = fixture.value.clone();
        changed["bridge_abi_version"] = json!(2);
        fixture.write_value(&changed);
        let error = verified_bridge_path(&fixture.manifest).unwrap_err();
        assert!(error.contains("ABI 不匹配"));

        let fixture = Fixture::new();
        let mut changed = fixture.value.clone();
        changed["occt_version"] = json!("7.9.2");
        fixture.write_value(&changed);
        let error = verified_bridge_path(&fixture.manifest).unwrap_err();
        assert!(error.contains("版本不匹配"));

        let fixture = Fixture::new();
        let other_bin = fixture.root.join("other-bin");
        fs::create_dir_all(&other_bin).unwrap();
        let bytes = b"fixture OCCT DLL 0 TKBO.dll";
        fs::write(other_bin.join("TKBO.dll"), bytes).unwrap();
        let mut changed = fixture.value.clone();
        changed["occt_runtime_dlls"][0]["path"] = json!("other-bin/TKBO.dll");
        fixture.write_value(&changed);
        let error = verified_bridge_path(&fixture.manifest).unwrap_err();
        assert!(error.contains("同一目录"));
    }
}
