//! Verified, application-private Node.js and DSH runtime installation.

use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::env;
use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use zip::ZipArchive;

const MANIFEST_RELATIVE_PATH: &str = "packaging/dsh-runtime/runtime-manifest.json";
const PACKAGE_RELATIVE_PATH: &str = "packaging/dsh-runtime/package.json";
const LOCK_RELATIVE_PATH: &str = "packaging/dsh-runtime/package-lock.json";
const BUNDLE_MANIFEST_RELATIVE_PATH: &str = "packaging/dsh-runtime/runtime-bundle-manifest.json";
const BUNDLE_FILE_NAME: &str = "dsh-runtime-win-x64.zip";
const RUNTIME_FOLDER: &str = "dsh-runtime";
const CACHE_FOLDER: &str = "dsh-runtime-cache";

#[derive(Clone, Debug)]
pub struct RuntimePaths {
    pub root: PathBuf,
    pub node: PathBuf,
    pub dsh_cli: PathBuf,
    pub home: PathBuf,
}

#[derive(Clone, Debug)]
struct RuntimeSpec {
    node_version: String,
    archive_file: String,
    archive_url: String,
    archive_sha256: String,
    npm_version: String,
    dsh_version: String,
    acp_version: String,
    acp_sdk_version: String,
    cli_path: PathBuf,
    registry: String,
    package_json_sha256: String,
    package_lock_sha256: String,
}

#[derive(Clone, Debug)]
struct RuntimeBundle {
    path: PathBuf,
    sha256: String,
    size: u64,
    unpacked_size: u64,
    file_count: usize,
}

pub fn status(root: &Path, data_dir: &Path) -> Result<Value, String> {
    let spec = read_spec(root)?;
    let bundle = read_bundle(root, &spec)?;
    let runtime_root = data_dir.join(RUNTIME_FOLDER);
    recover_previous_install(&runtime_root)?;
    let current = runtime_root.join("current");
    let marker = read_json(&current.join("install-manifest.json")).ok();
    let installed = marker
        .as_ref()
        .map(|marker| marker_matches(marker, &spec, &current, bundle.as_ref()))
        .unwrap_or(false);
    Ok(json!({
        "required": true,
        "installed": installed,
        "bundled": bundle.is_some(),
        "bundleRequired": !cfg!(debug_assertions),
        "platform": "windows-x86_64",
        "nodeVersion": spec.node_version,
        "npmVersion": spec.npm_version,
        "dshVersion": spec.dsh_version,
        "acpVersion": spec.acp_version,
    }))
}

pub fn resolve(root: &Path, data_dir: &Path) -> Result<RuntimePaths, String> {
    let spec = read_spec(root)?;
    let bundle = read_bundle(root, &spec)?;
    let runtime_root = data_dir.join(RUNTIME_FOLDER);
    recover_previous_install(&runtime_root)?;
    let current = runtime_root.join("current");
    let marker = read_json(&current.join("install-manifest.json"))
        .map_err(|_| "DSH Runtime 尚未安装。请先完成首次运行时安装。".to_owned())?;
    if !marker_matches(&marker, &spec, &current, bundle.as_ref()) {
        return Err("DSH Runtime 版本或校验状态不匹配。请重新安装运行时。".into());
    }
    Ok(RuntimePaths {
        root: current.clone(),
        node: current.join("node").join("node.exe"),
        dsh_cli: current.join("dsh").join(&spec.cli_path),
        home: data_dir.join("dsh-home"),
    })
}

pub fn install(
    root: &Path,
    data_dir: &Path,
    channel: &crate::agent_host::EventSink,
) -> Result<Value, String> {
    let spec = read_spec(root)?;
    let bundle = read_bundle(root, &spec)?;
    fs::create_dir_all(data_dir).map_err(|error| {
        format!(
            "无法创建 DSH Runtime 用户目录（{}）：{error}",
            data_dir.display()
        )
    })?;
    let runtime_root = data_dir.join(RUNTIME_FOLDER);
    fs::create_dir_all(&runtime_root).map_err(|error| error.to_string())?;
    recover_previous_install(&runtime_root)?;

    let current = runtime_root.join("current");
    if let Ok(marker) = read_json(&current.join("install-manifest.json")) {
        if marker_matches(&marker, &spec, &current, bundle.as_ref()) {
            super::send_install_event(
                channel,
                "progress",
                "DSH Runtime 已是固定版本，无需重新安装。",
            );
            return Ok(status(root, data_dir)?);
        }
    }

    let archive = if bundle.is_none() {
        let cache_root = data_dir.join(CACHE_FOLDER);
        fs::create_dir_all(&cache_root).map_err(|error| error.to_string())?;
        Some(download_node_archive(&spec, &cache_root, channel)?)
    } else {
        None
    };

    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let staging = runtime_root.join(format!(".installing-{}-{nonce}", std::process::id()));
    if staging.exists() {
        remove_owned_directory(&runtime_root, &staging)?;
    }
    fs::create_dir_all(&staging)
        .map_err(|error| format!("无法创建 DSH Runtime 暂存目录：{error}"))?;

    let prepared = (|| {
        if let Some(bundle) = bundle.as_ref() {
            super::send_install_event(
                channel,
                "progress",
                "正在从安装包解压已包含的 Node.js 与 DSH/ACP 依赖…",
            );
            extract_runtime_bundle(bundle, &staging, channel)?;
        } else {
            let node_root = staging.join("node");
            fs::create_dir_all(&node_root).map_err(|error| error.to_string())?;
            extract_node_runtime(
                archive.as_ref().expect("downloaded for online install"),
                &spec,
                &node_root,
            )?;
            copy_install_inputs(root, &staging.join("dsh"))?;
            install_npm_packages(&spec, data_dir, &staging, channel)?;
        }
        validate_runtime(&spec, &staging)?;

        let marker = json!({
            "manifestVersion": 1,
            "nodeVersion": spec.node_version,
            "npmVersion": spec.npm_version,
            "dshVersion": spec.dsh_version,
            "acpVersion": spec.acp_version,
            "acpSdkVersion": spec.acp_sdk_version,
            "packageJsonSha256": spec.package_json_sha256,
            "packageLockSha256": spec.package_lock_sha256,
            "runtimeSource": if bundle.is_some() { "bundled" } else { "online" },
            "bundleSha256": bundle.as_ref().map(|bundle| bundle.sha256.as_str()),
            "installedAt": SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs(),
        });
        fs::write(
            staging.join("install-manifest.json"),
            serde_json::to_vec_pretty(&marker).map_err(|error| error.to_string())?,
        )
        .map_err(|error| format!("无法写入 DSH Runtime 安装记录：{error}"))?;
        Ok::<(), String>(())
    })();
    if let Err(error) = prepared {
        let _ = remove_owned_directory(&runtime_root, &staging);
        return Err(error);
    }

    if let Err(error) = activate_install(&runtime_root, &staging) {
        let _ = remove_owned_directory(&runtime_root, &staging);
        return Err(error);
    }
    let result = status(root, data_dir)?;
    if result["installed"] != true {
        return Err("DSH Runtime 安装后校验失败，当前版本未启用。".into());
    }
    super::send_install_event(
        channel,
        "progress",
        if bundle.is_some() {
            "安装包内的 DSH ACP Runtime 已解压并通过校验。"
        } else {
            "固定版本的 DSH ACP Runtime 已安装并通过校验。"
        },
    );
    Ok(result)
}

fn read_spec(root: &Path) -> Result<RuntimeSpec, String> {
    let manifest_path = root.join(MANIFEST_RELATIVE_PATH);
    let manifest = read_json(&manifest_path).map_err(|error| {
        format!(
            "安装包缺少 DSH Runtime 清单（{}）：{error}",
            manifest_path.display()
        )
    })?;
    if manifest["manifestVersion"].as_u64() != Some(1) {
        return Err("不支持的 DSH Runtime manifestVersion。".into());
    }
    if manifest["platform"].as_str() != Some("windows-x86_64")
        || !cfg!(all(target_os = "windows", target_arch = "x86_64"))
    {
        return Err("当前 DSH Runtime 清单只支持 Windows x64 桌面包。".into());
    }
    let node_version = required_string(&manifest, &["node", "version"])?;
    let archive_file = required_string(&manifest, &["node", "archiveFile"])?;
    let archive_url = required_string(&manifest, &["node", "archiveUrl"])?;
    let archive_sha256 = required_string(&manifest, &["node", "archiveSha256"])?;
    let npm_version = required_string(&manifest, &["node", "npmVersion"])?;
    let dsh_version = required_string(&manifest, &["dsh", "version"])?;
    let acp_version = required_string(&manifest, &["dsh", "acpVersion"])?;
    let acp_sdk_version = required_string(&manifest, &["dsh", "acpSdkVersion"])?;
    let cli_relative = required_string(&manifest, &["dsh", "cliPath"])?;
    let registry = required_string(&manifest, &["npm", "registry"])?;
    let package_json_sha256 = required_string(&manifest, &["npm", "packageJsonSha256"])?;
    let package_lock_sha256 = required_string(&manifest, &["npm", "packageLockSha256"])?;

    let expected_archive = format!("node-{node_version}-win-x64.zip");
    let expected_url = format!("https://nodejs.org/dist/{node_version}/{expected_archive}");
    if archive_file != expected_archive || archive_url != expected_url {
        return Err("Node.js Runtime 下载地址必须匹配清单固定的官方 Windows x64 归档。".into());
    }
    if dsh_version != "0.2.0-rc.2" || acp_version != dsh_version || acp_sdk_version != "1.4.0" {
        return Err("当前桌面 profile 尚未验收该 DSH/ACP 版本组合。".into());
    }
    if registry != "https://registry.npmjs.org" {
        return Err("DSH Runtime npm registry 必须匹配本 feature 验收的官方源。".into());
    }
    if cli_relative != "node_modules/@deepseek-ai/dsh/lib/bin.js" {
        return Err("DSH CLI 入口路径与已验收的 npm 包布局不一致。".into());
    }
    for (name, digest) in [
        ("Node.js archive", archive_sha256.as_str()),
        ("package.json", package_json_sha256.as_str()),
        ("package-lock.json", package_lock_sha256.as_str()),
    ] {
        if !is_sha256(digest) {
            return Err(format!("DSH Runtime 清单中的 {name} SHA-256 无效。"));
        }
    }

    let package_json = root.join(PACKAGE_RELATIVE_PATH);
    let package_lock = root.join(LOCK_RELATIVE_PATH);
    verify_sha256(&package_json, &package_json_sha256, "DSH package.json")?;
    verify_sha256(&package_lock, &package_lock_sha256, "DSH package-lock.json")?;
    let package: Value = read_json(&package_json)?;
    if package["dependencies"]["@deepseek-ai/dsh"].as_str() != Some(dsh_version.as_str()) {
        return Err("DSH package.json 未固定到 Runtime 清单指定版本。".into());
    }

    let cli_path = PathBuf::from(cli_relative);
    if !is_safe_relative_path(&cli_path) {
        return Err("DSH CLI 路径不能離开 npm Runtime 目录。".into());
    }
    Ok(RuntimeSpec {
        node_version,
        archive_file,
        archive_url,
        archive_sha256,
        npm_version,
        dsh_version,
        acp_version,
        acp_sdk_version,
        cli_path,
        registry,
        package_json_sha256,
        package_lock_sha256,
    })
}

fn read_bundle(root: &Path, spec: &RuntimeSpec) -> Result<Option<RuntimeBundle>, String> {
    let manifest_path = root.join(BUNDLE_MANIFEST_RELATIVE_PATH);
    let bundle_path = root.join("packaging/dsh-runtime").join(BUNDLE_FILE_NAME);
    let manifest_exists = manifest_path.is_file();
    let bundle_exists = bundle_path.is_file();
    if !manifest_exists && !bundle_exists {
        if cfg!(debug_assertions) {
            return Ok(None);
        }
        return Err("发行版缺少随包提供的完整 DSH Runtime 依赖。请使用完整桌面安装包。".into());
    }
    if !manifest_exists || !bundle_exists {
        return Err("DSH Runtime 离线包或其校验清单缺失；桌面安装资源不完整。".into());
    }

    let manifest = read_json(&manifest_path).map_err(|error| {
        format!(
            "无法读取 DSH Runtime 离线包清单（{}）：{error}",
            manifest_path.display()
        )
    })?;
    if manifest["bundleManifestVersion"].as_u64() != Some(1)
        || manifest["platform"].as_str() != Some("windows-x86_64")
        || manifest["bundleFile"].as_str() != Some(BUNDLE_FILE_NAME)
        || manifest["nodeVersion"].as_str() != Some(spec.node_version.as_str())
        || manifest["npmVersion"].as_str() != Some(spec.npm_version.as_str())
        || manifest["dshVersion"].as_str() != Some(spec.dsh_version.as_str())
        || manifest["acpVersion"].as_str() != Some(spec.acp_version.as_str())
        || manifest["acpSdkVersion"].as_str() != Some(spec.acp_sdk_version.as_str())
        || manifest["packageJsonSha256"].as_str() != Some(spec.package_json_sha256.as_str())
        || manifest["packageLockSha256"].as_str() != Some(spec.package_lock_sha256.as_str())
    {
        return Err("DSH Runtime 离线包清单与当前 Node、DSH/ACP 版本或锁文件不匹配。".into());
    }
    let sha256 = manifest["bundleSha256"]
        .as_str()
        .ok_or("DSH Runtime 离线包清单缺少 bundleSha256。")?
        .to_ascii_lowercase();
    if !is_sha256(&sha256) {
        return Err("DSH Runtime 离线包清单中的 SHA-256 格式无效。".into());
    }
    let size = manifest["bundleSize"]
        .as_u64()
        .filter(|size| *size > 0)
        .ok_or("DSH Runtime 离线包清单中的 bundleSize 无效。")?;
    let unpacked_size = manifest["unpackedSize"]
        .as_u64()
        .filter(|size| *size > 0)
        .ok_or("DSH Runtime 离线包清单中的 unpackedSize 无效。")?;
    let file_count = manifest["fileCount"]
        .as_u64()
        .filter(|count| *count > 0)
        .and_then(|count| usize::try_from(count).ok())
        .ok_or("DSH Runtime 离线包清单中的 fileCount 无效。")?;
    let actual_size = fs::metadata(&bundle_path)
        .map_err(|error| format!("无法读取 DSH Runtime 离线包大小：{error}"))?
        .len();
    if actual_size != size {
        return Err(format!(
            "DSH Runtime 离线包大小与清单不符：清单 {size} 字节，实际 {actual_size} 字节。"
        ));
    }
    Ok(Some(RuntimeBundle {
        path: bundle_path,
        sha256,
        size,
        unpacked_size,
        file_count,
    }))
}

fn required_string(value: &Value, path: &[&str]) -> Result<String, String> {
    let mut current = value;
    for item in path {
        current = current
            .get(*item)
            .ok_or_else(|| format!("DSH Runtime 清单缺少字段：{}", path.join(".")))?;
    }
    current
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("DSH Runtime 清单字段格式错误：{}", path.join(".")))
}

fn read_json(path: &Path) -> Result<Value, String> {
    let bytes = fs::read(path).map_err(|error| error.to_string())?;
    serde_json::from_slice(&bytes).map_err(|error| error.to_string())
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn is_safe_relative_path(path: &Path) -> bool {
    !path.as_os_str().is_empty()
        && path
            .components()
            .all(|component| matches!(component, std::path::Component::Normal(_)))
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let mut file =
        File::open(path).map_err(|error| format!("无法读取 {}：{error}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 128 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|error| format!("读取文件失败（{}）：{error}", path.display()))?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn verify_sha256(path: &Path, expected: &str, label: &str) -> Result<(), String> {
    let actual = sha256_file(path)?;
    if actual != expected.to_ascii_lowercase() {
        return Err(format!(
            "{label} SHA-256 校验失败。清单期望 {expected}，实际为 {actual}。"
        ));
    }
    Ok(())
}

fn download_node_archive(
    spec: &RuntimeSpec,
    cache_root: &Path,
    channel: &crate::agent_host::EventSink,
) -> Result<PathBuf, String> {
    let destination = cache_root.join(&spec.archive_file);
    if destination.is_file()
        && verify_sha256(&destination, &spec.archive_sha256, "Node.js archive").is_ok()
    {
        super::send_install_event(channel, "progress", "已校验本地缓存的 Node.js Runtime。");
        return Ok(destination);
    }
    if destination.exists() {
        fs::remove_file(&destination)
            .map_err(|error| format!("无法删除错误的 Node.js 缓存：{error}"))?;
    }
    let partial = cache_root.join(format!(
        "{}.download-{}",
        spec.archive_file,
        std::process::id()
    ));
    if partial.exists() {
        fs::remove_file(&partial).map_err(|error| error.to_string())?;
    }
    super::send_install_event(
        channel,
        "progress",
        format!("正在下载 Node.js {} 私有 Runtime…", spec.node_version),
    );
    let client = reqwest::blocking::Client::builder()
        .connect_timeout(Duration::from_secs(20))
        .timeout(Duration::from_secs(600))
        .build()
        .map_err(|error| format!("无法初始化安全下载连接：{error}"))?;
    let mut response = client
        .get(&spec.archive_url)
        .send()
        .map_err(|error| format!("下载 Node.js Runtime 失败：{error}"))?;
    if !response.status().is_success() {
        return Err(format!(
            "下载 Node.js Runtime 失败：HTTP {}。",
            response.status()
        ));
    }
    let expected_size = response.content_length();
    let mut output =
        File::create(&partial).map_err(|error| format!("无法建立 Node.js 下载缓存：{error}"))?;
    let mut downloaded = 0u64;
    let mut last_reported = 0u64;
    let mut buffer = [0u8; 1024 * 1024];
    loop {
        let count = response
            .read(&mut buffer)
            .map_err(|error| format!("读取 Node.js Runtime 下载流失败：{error}"))?;
        if count == 0 {
            break;
        }
        output
            .write_all(&buffer[..count])
            .map_err(|error| format!("保存 Node.js Runtime 下载失败：{error}"))?;
        downloaded += count as u64;
        if downloaded.saturating_sub(last_reported) >= 5 * 1024 * 1024 {
            let message = expected_size
                .map(|size| {
                    format!(
                        "正在下载 Node.js Runtime：{:.0}%",
                        downloaded as f64 * 100.0 / size.max(1) as f64
                    )
                })
                .unwrap_or_else(|| {
                    format!(
                        "已下载 Node.js Runtime：{:.1} MiB",
                        downloaded as f64 / 1_048_576.0
                    )
                });
            super::send_install_event(channel, "progress", message);
            last_reported = downloaded;
        }
    }
    output
        .sync_all()
        .map_err(|error| format!("无法完成 Node.js Runtime 写入：{error}"))?;
    if let Err(error) = verify_sha256(&partial, &spec.archive_sha256, "Node.js archive") {
        let _ = fs::remove_file(&partial);
        return Err(error);
    }
    fs::rename(&partial, &destination)
        .map_err(|error| format!("无法保存已校验的 Node.js Runtime：{error}"))?;
    super::send_install_event(
        channel,
        "progress",
        "Node.js Runtime 下载与 SHA-256 校验完成。",
    );
    Ok(destination)
}

fn extract_node_runtime(
    archive_path: &Path,
    spec: &RuntimeSpec,
    node_root: &Path,
) -> Result<(), String> {
    let file = File::open(archive_path).map_err(|error| error.to_string())?;
    let mut archive =
        ZipArchive::new(file).map_err(|error| format!("Node.js ZIP 无法打开：{error}"))?;
    let archive_root = spec.archive_file.trim_end_matches(".zip");
    let archive_root = Path::new(archive_root);
    let mut extracted_node = false;
    let mut extracted_npm = false;
    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|error| format!("读取 Node.js ZIP 内容失败：{error}"))?;
        let safe_name = entry
            .enclosed_name()
            .ok_or_else(|| "Node.js ZIP 中发现越界路径。".to_owned())?
            .to_path_buf();
        let relative = safe_name
            .strip_prefix(archive_root)
            .map_err(|_| "Node.js ZIP 项不在固定归档根目录下。".to_owned())?;
        let is_node = relative == Path::new("node.exe");
        let is_npm = relative.starts_with(Path::new("node_modules/npm"));
        let is_license = relative == Path::new("LICENSE");
        if !(is_node || is_npm || is_license) {
            continue;
        }
        if entry
            .unix_mode()
            .map(|mode| mode & 0o170000 == 0o120000)
            .unwrap_or(false)
        {
            return Err("Node.js ZIP 中不接受符号链接。".into());
        }
        let destination = node_root.join(relative);
        if entry.is_dir() {
            fs::create_dir_all(&destination).map_err(|error| error.to_string())?;
            continue;
        }
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let mut output = File::create(&destination).map_err(|error| error.to_string())?;
        std::io::copy(&mut entry, &mut output).map_err(|error| error.to_string())?;
        extracted_node |= is_node;
        extracted_npm |= is_npm;
    }
    if !extracted_node || !extracted_npm {
        return Err("Node.js ZIP 缺少 node.exe 或内置 npm。".into());
    }
    Ok(())
}

fn extract_runtime_bundle(
    bundle: &RuntimeBundle,
    staging: &Path,
    channel: &crate::agent_host::EventSink,
) -> Result<(), String> {
    let actual_size = fs::metadata(&bundle.path)
        .map_err(|error| format!("无法读取 DSH Runtime 离线包大小：{error}"))?
        .len();
    if actual_size != bundle.size {
        return Err(format!(
            "DSH Runtime 离线包大小已变化：清单 {} 字节，实际 {actual_size} 字节。",
            bundle.size
        ));
    }
    verify_sha256(&bundle.path, &bundle.sha256, "bundled DSH Runtime")?;
    let archive_file = File::open(&bundle.path)
        .map_err(|error| format!("无法打开安装包内的 DSH Runtime ZIP：{error}"))?;
    let mut archive = ZipArchive::new(archive_file)
        .map_err(|error| format!("安装包内的 DSH Runtime ZIP 无法读取：{error}"))?;
    let mut seen = HashSet::with_capacity(bundle.file_count);
    let mut extracted_files = 0usize;
    let mut extracted_bytes = 0u64;
    let mut last_reported_bytes = 0u64;

    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|error| format!("读取 DSH Runtime ZIP 内容失败：{error}"))?;
        let relative = entry
            .enclosed_name()
            .ok_or_else(|| "DSH Runtime ZIP 中发现越界路径。".to_owned())?
            .to_path_buf();
        if !is_safe_relative_path(&relative) {
            return Err("DSH Runtime ZIP 中发现非相对路径。".into());
        }
        let mut components = relative.components();
        let top = components
            .next()
            .map(|component| component.as_os_str())
            .ok_or_else(|| "DSH Runtime ZIP 中发现空路径。".to_owned())?;
        if top != "node" && top != "dsh" {
            return Err(format!(
                "DSH Runtime ZIP 包含预期之外的顶层路径：{}",
                relative.display()
            ));
        }
        let is_top_directory = entry.is_dir() && components.next().is_none();
        if !is_top_directory && relative.components().count() < 2 {
            return Err(format!(
                "DSH Runtime ZIP 缺少文件目录层级：{}",
                relative.display()
            ));
        }
        if !seen.insert(relative.clone()) {
            return Err(format!(
                "DSH Runtime ZIP 含有重复路径：{}",
                relative.display()
            ));
        }
        if entry
            .unix_mode()
            .map(|mode| mode & 0o170000 == 0o120000)
            .unwrap_or(false)
        {
            return Err("DSH Runtime ZIP 中不接受符号链接。".into());
        }
        let destination = staging.join(&relative);
        if entry.is_dir() {
            fs::create_dir_all(&destination)
                .map_err(|error| format!("无法建立 Runtime 目录：{error}"))?;
            continue;
        }
        let expected_size = entry.size();
        if extracted_bytes
            .checked_add(expected_size)
            .filter(|total| *total <= bundle.unpacked_size)
            .is_none()
        {
            return Err("DSH Runtime ZIP 解压大小超过清单上限。".into());
        }
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("无法建立 Runtime 文件目录：{error}"))?;
        }
        let mut output = File::create(&destination).map_err(|error| {
            format!(
                "无法写入 Runtime 文件（{}）：{error}",
                destination.display()
            )
        })?;
        let copied = std::io::copy(&mut entry, &mut output).map_err(|error| {
            format!(
                "解压 Runtime 文件失败（{}）：{error}",
                destination.display()
            )
        })?;
        if copied != expected_size {
            return Err(format!(
                "Runtime 文件解压长度不符（{}）：清单 {expected_size} 字节，实际 {copied} 字节。",
                destination.display()
            ));
        }
        extracted_bytes += copied;
        extracted_files += 1;
        if extracted_bytes.saturating_sub(last_reported_bytes) >= 32 * 1024 * 1024 {
            super::send_install_event(
                channel,
                "progress",
                format!(
                    "本地解包 DSH Runtime：{:.0}%（{:.0}/{:.0} MiB）",
                    extracted_bytes as f64 * 100.0 / bundle.unpacked_size.max(1) as f64,
                    extracted_bytes as f64 / 1_048_576.0,
                    bundle.unpacked_size as f64 / 1_048_576.0
                ),
            );
            last_reported_bytes = extracted_bytes;
        }
    }

    if extracted_files != bundle.file_count || extracted_bytes != bundle.unpacked_size {
        return Err(format!(
            "DSH Runtime ZIP 解压校验不符：文件数 {extracted_files}/{}，数据量 {extracted_bytes}/{} 字节。",
            bundle.file_count, bundle.unpacked_size
        ));
    }
    Ok(())
}

fn copy_install_inputs(source_root: &Path, destination: &Path) -> Result<(), String> {
    fs::create_dir_all(destination).map_err(|error| error.to_string())?;
    for relative in [PACKAGE_RELATIVE_PATH, LOCK_RELATIVE_PATH] {
        let name = Path::new(relative)
            .file_name()
            .ok_or("DSH 包管理文件路径无效。")?;
        fs::copy(source_root.join(relative), destination.join(name))
            .map_err(|error| format!("无法暂存固定 DSH npm 清单：{error}"))?;
    }
    Ok(())
}

fn install_npm_packages(
    spec: &RuntimeSpec,
    data_dir: &Path,
    staging: &Path,
    channel: &crate::agent_host::EventSink,
) -> Result<(), String> {
    let node_root = staging.join("node");
    let node = node_root.join("node.exe");
    let npm_cli = node_root.join("node_modules/npm/bin/npm-cli.js");
    let dsh_root = staging.join("dsh");
    let npm_user_config = data_dir.join(CACHE_FOLDER).join("empty-user.npmrc");
    let npm_global_config = data_dir.join(CACHE_FOLDER).join("empty-global.npmrc");
    fs::write(&npm_user_config, "")
        .map_err(|error| format!("无法建立隔离 npm 用户配置：{error}"))?;
    fs::write(&npm_global_config, "")
        .map_err(|error| format!("无法建立隔离 npm 全局配置：{error}"))?;
    let npm_cache = data_dir.join(CACHE_FOLDER).join("npm-cache");
    fs::create_dir_all(&npm_cache).map_err(|error| error.to_string())?;
    let actual_node = capture_node(&node, &[OsString::from("--version")], &dsh_root, &node_root)?;
    if actual_node.trim() != spec.node_version {
        return Err(format!(
            "Node.js 版本不匹配：要求 {}，实际 {}。",
            spec.node_version,
            actual_node.trim()
        ));
    }
    let actual_npm = capture_node(
        &node,
        &[npm_cli.as_os_str().to_owned(), OsString::from("--version")],
        &dsh_root,
        &node_root,
    )?;
    if actual_npm.trim() != spec.npm_version {
        return Err(format!(
            "npm 版本不匹配：要求 {}，实际 {}。",
            spec.npm_version,
            actual_npm.trim()
        ));
    }

    super::send_install_event(
        channel,
        "progress",
        "正在用私有 Node.js 安装锁定的 DSH ACP 依赖…",
    );
    let mut command = Command::new(&node);
    command
        .arg(&npm_cli)
        .args(["ci", "--registry"])
        .arg(&spec.registry)
        .args([
            "--no-audit",
            "--no-fund",
            "--no-update-notifier",
            "--progress=false",
        ])
        .current_dir(&dsh_root)
        .env("npm_config_userconfig", &npm_user_config)
        .env("npm_config_globalconfig", &npm_global_config)
        .env("npm_config_cache", &npm_cache)
        .env("npm_config_update_notifier", "false")
        .env("CI", "1")
        .env("PATH", isolated_path(&node_root)?)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("无法启动私有 npm 安装程序：{error}"))?;
    let output = Arc::new(Mutex::new(Vec::new()));
    let stdout_thread = child
        .stdout
        .take()
        .map(|stream| super::stream_install_output(stream, "npm", channel.clone(), output.clone()));
    let stderr_thread = child
        .stderr
        .take()
        .map(|stream| super::stream_install_output(stream, "npm", channel.clone(), output.clone()));
    let status = child
        .wait()
        .map_err(|error| format!("等待 DSH npm 安装结束时出错：{error}"))?;
    for handle in [stdout_thread, stderr_thread].into_iter().flatten() {
        let _ = handle.join();
    }
    if !status.success() {
        let details = output
            .lock()
            .map(|lines| lines.join("\n"))
            .unwrap_or_default();
        return Err(if details.is_empty() {
            format!("DSH npm 安装失败（退出码 {:?}）。", status.code())
        } else {
            format!("DSH npm 安装失败：\n{details}")
        });
    }
    Ok(())
}

fn isolated_path(node_root: &Path) -> Result<OsString, String> {
    let mut paths = vec![node_root.to_path_buf()];
    #[cfg(windows)]
    if let Some(system_root) = env::var_os("SystemRoot") {
        paths.push(PathBuf::from(&system_root).join("System32"));
        paths.push(PathBuf::from(system_root));
    }
    env::join_paths(paths).map_err(|error| format!("无法构造私有 Node PATH：{error}"))
}

fn capture_node(
    node: &Path,
    args: &[OsString],
    cwd: &Path,
    node_root: &Path,
) -> Result<String, String> {
    let output = Command::new(node)
        .args(args)
        .current_dir(cwd)
        .env("PATH", isolated_path(node_root)?)
        .output()
        .map_err(|error| format!("运行私有 Node.js 时失败：{error}"))?;
    if !output.status.success() {
        return Err(format!(
            "私有 Node.js 命令退出 {:?}：{}",
            output.status.code(),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

fn validate_runtime(spec: &RuntimeSpec, staging: &Path) -> Result<(), String> {
    let node_root = staging.join("node");
    let dsh_root = staging.join("dsh");
    let node = node_root.join("node.exe");
    let npm_cli = node_root.join("node_modules/npm/bin/npm-cli.js");
    let actual_node = capture_node(&node, &[OsString::from("--version")], &dsh_root, &node_root)?;
    if actual_node.trim() != spec.node_version {
        return Err(format!(
            "Node.js 版本不匹配：要求 {}，实际 {}。",
            spec.node_version,
            actual_node.trim()
        ));
    }
    let actual_npm = capture_node(
        &node,
        &[npm_cli.as_os_str().to_owned(), OsString::from("--version")],
        &dsh_root,
        &node_root,
    )?;
    if actual_npm.trim() != spec.npm_version {
        return Err(format!(
            "npm 版本不匹配：要求 {}，实际 {}。",
            spec.npm_version,
            actual_npm.trim()
        ));
    }
    for (package_path, expected, label) in [
        (
            "node_modules/@deepseek-ai/dsh/package.json",
            spec.dsh_version.as_str(),
            "DSH",
        ),
        (
            "node_modules/@deepseek-ai/dsh-acp/package.json",
            spec.acp_version.as_str(),
            "DSH ACP",
        ),
        (
            "node_modules/@deepseek-ai/dsh-acp-app/package.json",
            spec.acp_version.as_str(),
            "DSH ACP profile",
        ),
        (
            "node_modules/@agentclientprotocol/sdk/package.json",
            spec.acp_sdk_version.as_str(),
            "ACP SDK",
        ),
    ] {
        let path = dsh_root.join(package_path);
        let package =
            read_json(&path).map_err(|error| format!("DSH Runtime 缺少 {label} 包：{error}"))?;
        let actual = package["version"].as_str().unwrap_or_default();
        if actual != expected {
            return Err(format!(
                "{label} 版本不匹配：要求 {expected}，实际 {actual}。"
            ));
        }
    }
    let cli_path = dsh_root.join(&spec.cli_path);
    if !cli_path.is_file() {
        return Err(format!("DSH CLI 入口不存在：{}", cli_path.display()));
    }
    let output = capture_node(
        &node,
        &[cli_path.as_os_str().to_owned(), OsString::from("--version")],
        &dsh_root,
        &node_root,
    )?;
    if !output.contains(&spec.dsh_version) {
        return Err(format!("DSH CLI 版本探测不匹配：{output}"));
    }
    Ok(())
}

fn marker_matches(
    marker: &Value,
    spec: &RuntimeSpec,
    current: &Path,
    bundle: Option<&RuntimeBundle>,
) -> bool {
    let source_matches = match bundle {
        Some(bundle) => {
            marker["runtimeSource"].as_str() == Some("bundled")
                && marker["bundleSha256"].as_str() == Some(bundle.sha256.as_str())
        }
        None => marker["runtimeSource"].as_str() != Some("bundled"),
    };
    source_matches
        && marker["manifestVersion"].as_u64() == Some(1)
        && marker["nodeVersion"].as_str() == Some(spec.node_version.as_str())
        && marker["npmVersion"].as_str() == Some(spec.npm_version.as_str())
        && marker["dshVersion"].as_str() == Some(spec.dsh_version.as_str())
        && marker["acpVersion"].as_str() == Some(spec.acp_version.as_str())
        && marker["acpSdkVersion"].as_str() == Some(spec.acp_sdk_version.as_str())
        && marker["packageJsonSha256"].as_str() == Some(spec.package_json_sha256.as_str())
        && marker["packageLockSha256"].as_str() == Some(spec.package_lock_sha256.as_str())
        && current.join("node/node.exe").is_file()
        && current
            .join("node/node_modules/npm/bin/npm-cli.js")
            .is_file()
        && current.join("dsh").join(&spec.cli_path).is_file()
}

fn recover_previous_install(runtime_root: &Path) -> Result<(), String> {
    let current = runtime_root.join("current");
    let previous = runtime_root.join("previous");
    if !current.exists() && previous.exists() {
        fs::rename(&previous, &current)
            .map_err(|error| format!("无法恢复上一个 DSH Runtime：{error}"))?;
    }
    Ok(())
}

fn activate_install(runtime_root: &Path, staging: &Path) -> Result<(), String> {
    let current = runtime_root.join("current");
    let previous = runtime_root.join("previous");
    if previous.exists() {
        remove_owned_directory(runtime_root, &previous)?;
    }
    let had_current = current.exists();
    if had_current {
        rename_with_retry(&current, &previous)
            .map_err(|error| format!("无法备份当前 DSH Runtime 以便回滚：{error}"))?;
    }
    if let Err(error) = rename_with_retry(staging, &current) {
        if had_current {
            let _ = rename_with_retry(&previous, &current);
        }
        return Err(format!(
            "无法启用新 DSH Runtime，已保留/恢复旧版本：{error}"
        ));
    }
    Ok(())
}

fn rename_with_retry(source: &Path, destination: &Path) -> std::io::Result<()> {
    let retries = if cfg!(windows) { 6 } else { 1 };
    for attempt in 0..retries {
        match fs::rename(source, destination) {
            Ok(()) => return Ok(()),
            Err(error)
                if attempt + 1 < retries
                    && error.kind() == std::io::ErrorKind::PermissionDenied =>
            {
                std::thread::sleep(Duration::from_millis(250 * (attempt as u64 + 1)));
            }
            Err(error) => return Err(error),
        }
    }
    fs::rename(source, destination)
}

fn remove_owned_directory(parent: &Path, target: &Path) -> Result<(), String> {
    let canonical_parent = parent
        .canonicalize()
        .map_err(|error| format!("无法校验 Runtime 目录边界：{error}"))?;
    let target_parent = target
        .parent()
        .ok_or("Runtime 删除目标没有父目录。")?
        .canonicalize()
        .map_err(|error| format!("无法校验 Runtime 删除目标：{error}"))?;
    if target_parent != canonical_parent {
        return Err(format!(
            "拒绝删除 Runtime 根目录之外的路径：{}",
            target.display()
        ));
    }
    let metadata = fs::symlink_metadata(target).map_err(|error| error.to_string())?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(format!(
            "拒绝递归删除非目录或符号链接：{}",
            target.display()
        ));
    }
    fs::remove_dir_all(target).map_err(|error| format!("无法清理 Runtime 暂存目录：{error}"))
}

#[cfg(test)]
mod tests {
    use super::{
        activate_install, is_safe_relative_path, is_sha256, read_spec, recover_previous_install,
        verify_sha256,
    };
    use std::fs;
    use std::path::Path;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn accepts_only_safe_package_relative_paths() {
        assert!(is_safe_relative_path(Path::new(
            "node_modules/@deepseek-ai/dsh/lib/bin.js"
        )));
        assert!(!is_safe_relative_path(Path::new("../outside.js")));
        assert!(!is_safe_relative_path(Path::new("C:/outside.js")));
        assert!(!is_safe_relative_path(Path::new("")));
    }

    #[test]
    fn validates_sha256_shape_and_file_content() {
        let digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
        assert!(is_sha256(digest));
        assert!(!is_sha256("not-a-hash"));
        let scratch = scratch_dir();
        fs::write(scratch.join("artifact.zip"), b"abc").expect("write fixture");
        verify_sha256(&scratch.join("artifact.zip"), digest, "fixture").expect("matching hash");
        assert!(verify_sha256(&scratch.join("artifact.zip"), &"0".repeat(64), "fixture").is_err());
        fs::remove_dir_all(scratch).expect("remove test directory");
    }

    #[test]
    fn reads_the_staged_runtime_manifest_and_package_hashes() {
        if !cfg!(all(target_os = "windows", target_arch = "x86_64")) {
            return;
        }
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../legacy");
        let spec = read_spec(&root).expect("fixed DSH runtime manifest");
        assert_eq!(spec.node_version, "v22.23.3");
        assert_eq!(spec.npm_version, "10.9.9");
        assert_eq!(spec.dsh_version, "0.2.0-rc.2");
        assert_eq!(spec.acp_version, "0.2.0-rc.2");
        assert_eq!(spec.acp_sdk_version, "1.4.0");
    }

    #[test]
    fn activation_failure_restores_the_previous_runtime() {
        let scratch = scratch_dir();
        let current = scratch.join("current");
        fs::create_dir_all(&current).expect("create old runtime");
        fs::write(current.join("version.txt"), "old").expect("write old marker");

        let missing_staging = scratch.join("staging-that-does-not-exist");
        assert!(activate_install(&scratch, &missing_staging).is_err());
        assert_eq!(
            fs::read_to_string(scratch.join("current/version.txt")).expect("restored current"),
            "old"
        );
        assert!(!scratch.join("previous").exists());

        fs::remove_dir_all(scratch).expect("remove test directory");
    }

    #[test]
    fn recovers_previous_runtime_after_interrupted_activation() {
        let scratch = scratch_dir();
        let previous = scratch.join("previous");
        fs::create_dir_all(&previous).expect("create previous runtime");
        fs::write(previous.join("version.txt"), "old").expect("write previous marker");

        recover_previous_install(&scratch).expect("recover previous runtime");
        assert_eq!(
            fs::read_to_string(scratch.join("current/version.txt")).expect("recovered current"),
            "old"
        );
        assert!(!previous.exists());

        fs::remove_dir_all(scratch).expect("remove test directory");
    }

    #[test]
    fn activation_promotes_staging_and_keeps_one_previous_version() {
        let scratch = scratch_dir();
        let current = scratch.join("current");
        let staging = scratch.join("staging");
        fs::create_dir_all(&current).expect("create old runtime");
        fs::create_dir_all(&staging).expect("create staged runtime");
        fs::write(current.join("version.txt"), "old").expect("write old marker");
        fs::write(staging.join("version.txt"), "new").expect("write staged marker");

        activate_install(&scratch, &staging).expect("activate staged runtime");
        assert_eq!(
            fs::read_to_string(scratch.join("current/version.txt")).expect("active marker"),
            "new"
        );
        assert_eq!(
            fs::read_to_string(scratch.join("previous/version.txt")).expect("previous marker"),
            "old"
        );
        assert!(!staging.exists());

        fs::remove_dir_all(scratch).expect("remove test directory");
    }

    fn scratch_dir() -> std::path::PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("aimodeler-dsh-runtime-test-{stamp}"));
        fs::create_dir_all(&path).expect("create scratch directory");
        path
    }
}
