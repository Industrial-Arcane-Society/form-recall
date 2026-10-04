use ai_modeler_ui_lib::dsh_runtime::{self, RuntimePaths};
use serde_json::{Value, json};
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};
use tauri::ipc::{Channel, InvokeResponseBody};

fn read_json(path: &Path) -> Result<Value, String> {
    let bytes = fs::read(path).map_err(|error| format!("无法读取 {}：{error}", path.display()))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("无法解析 {}：{error}", path.display()))
}

fn require_bool(value: &Value, key: &str, expected: bool) -> Result<(), String> {
    if value.get(key).and_then(Value::as_bool) == Some(expected) {
        Ok(())
    } else {
        Err(format!("运行时状态字段 {key} 不符合预期 {expected}。"))
    }
}

fn require_string(value: &Value, key: &str, expected: &str) -> Result<(), String> {
    if value.get(key).and_then(Value::as_str) == Some(expected) {
        Ok(())
    } else {
        Err(format!("安装清单字段 {key} 不匹配。"))
    }
}

fn require_version(actual: &str, expected: &str, label: &str) -> Result<(), String> {
    if actual == expected {
        Ok(())
    } else {
        Err(format!("{label} 版本不匹配。"))
    }
}

fn manifest_string<'a>(value: &'a Value, path: &[&str]) -> Result<&'a str, String> {
    let field = path.join(".");
    path.iter()
        .try_fold(value, |current, key| current.get(*key))
        .and_then(Value::as_str)
        .ok_or_else(|| format!("Runtime manifest 缺少 {field}。"))
}

fn isolated_node_output(
    runtime: &RuntimePaths,
    data_dir: &Path,
    node_args: &[OsString],
) -> Result<String, String> {
    let system_root = std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .ok_or_else(|| "缺少 Windows SystemRoot 环境变量。".to_owned())?;
    let system32 = system_root.join("System32");
    let home = data_dir.join("dsh-home");
    fs::create_dir_all(&home).map_err(|error| format!("无法创建隔离 DSH_HOME：{error}"))?;

    let output = Command::new(&runtime.node)
        .args(node_args)
        .current_dir(&runtime.root)
        .env_clear()
        .env("PATH", &system32)
        .env("SystemRoot", &system_root)
        .env("WINDIR", &system_root)
        .env("TEMP", data_dir)
        .env("TMP", data_dir)
        .env("USERPROFILE", data_dir)
        .env("HOME", data_dir)
        .env("DSH_HOME", &home)
        .output()
        .map_err(|error| format!("无法启动应用私有 Node.js：{error}"))?;

    if !output.status.success() {
        return Err(format!(
            "应用私有 Node.js 版本检查失败，退出码 {:?}。",
            output.status.code()
        ));
    }
    String::from_utf8(output.stdout)
        .map(|output| output.trim().to_owned())
        .map_err(|_| "应用私有 Node.js 版本输出不是 UTF-8。".to_owned())
}

fn validate_install_manifest(
    marker: &Value,
    runtime_manifest: &Value,
    bundle_sha256: &str,
) -> Result<u64, String> {
    require_string(marker, "runtimeSource", "bundled")?;
    require_string(marker, "bundleSha256", bundle_sha256)?;
    for (marker_key, manifest_path) in [
        ("nodeVersion", &["node", "version"][..]),
        ("npmVersion", &["node", "npmVersion"][..]),
        ("dshVersion", &["dsh", "version"][..]),
        ("acpVersion", &["dsh", "acpVersion"][..]),
        ("acpSdkVersion", &["dsh", "acpSdkVersion"][..]),
        ("packageJsonSha256", &["npm", "packageJsonSha256"][..]),
        ("packageLockSha256", &["npm", "packageLockSha256"][..]),
    ] {
        let expected = manifest_string(runtime_manifest, manifest_path)?;
        require_string(marker, marker_key, expected)?;
    }
    marker["installedAt"]
        .as_u64()
        .ok_or_else(|| "安装清单缺少有效 installedAt。".to_owned())
}

fn run(resource_root: &Path, data_dir: &Path) -> Result<Value, String> {
    if !resource_root.is_dir() {
        return Err("app-resource-root 必须是现有目录。".into());
    }
    if data_dir.exists() {
        return Err("为证明冷安装，请提供一个尚不存在的隔离数据目录。".into());
    }

    let runtime_resources = resource_root.join("packaging/dsh-runtime");
    let runtime_manifest = read_json(&runtime_resources.join("runtime-manifest.json"))?;
    let bundle_manifest = read_json(&runtime_resources.join("runtime-bundle-manifest.json"))?;
    let bundle_sha256 = manifest_string(&bundle_manifest, &["bundleSha256"])?;
    let bundle_file = manifest_string(&bundle_manifest, &["bundleFile"])?;
    let bundle_size = bundle_manifest["bundleSize"]
        .as_u64()
        .ok_or_else(|| "DSH Runtime bundle manifest 缺少有效 bundleSize。".to_owned())?;
    let actual_bundle_size = fs::metadata(runtime_resources.join(bundle_file))
        .map_err(|error| format!("无法读取随包 Runtime ZIP 大小：{error}"))?
        .len();
    if actual_bundle_size != bundle_size {
        return Err("随包 Runtime ZIP 大小与 bundle manifest 不匹配。".into());
    }

    let initial = dsh_runtime::status(resource_root, data_dir)?;
    require_bool(&initial, "bundled", true)?;
    require_bool(&initial, "installed", false)?;

    let progress_events = Arc::new(AtomicUsize::new(0));
    let progress_counter = Arc::clone(&progress_events);
    let channel = Channel::<Value>::new(move |_message: InvokeResponseBody| {
        progress_counter.fetch_add(1, Ordering::Relaxed);
        Ok(())
    });

    let first_install = dsh_runtime::install(resource_root, data_dir, &channel)?;
    require_bool(&first_install, "bundled", true)?;
    require_bool(&first_install, "installed", true)?;

    let runtime = dsh_runtime::resolve(resource_root, data_dir)?;
    if !runtime.node.is_file() || !runtime.dsh_cli.is_file() {
        return Err("resolve 返回的私有 Node 或 DSH CLI 文件不存在。".into());
    }
    let marker_path = runtime.root.join("install-manifest.json");
    let marker_before = read_json(&marker_path)?;
    let installed_at = validate_install_manifest(&marker_before, &runtime_manifest, bundle_sha256)?;

    let node_version = isolated_node_output(&runtime, data_dir, &[OsString::from("--version")])?;
    let npm_cli = runtime.root.join("node/node_modules/npm/bin/npm-cli.js");
    let npm_version = isolated_node_output(
        &runtime,
        data_dir,
        &[npm_cli.into_os_string(), OsString::from("--version")],
    )?;
    let dsh_version = isolated_node_output(
        &runtime,
        data_dir,
        &[
            runtime.dsh_cli.as_os_str().to_owned(),
            OsString::from("--version"),
        ],
    )?;

    require_version(
        &node_version,
        manifest_string(&runtime_manifest, &["node", "version"])?,
        "Node.js",
    )?;
    require_version(
        &npm_version,
        manifest_string(&runtime_manifest, &["node", "npmVersion"])?,
        "npm",
    )?;
    require_version(
        &dsh_version,
        manifest_string(&runtime_manifest, &["dsh", "version"])?,
        "DSH",
    )?;

    let second_install = dsh_runtime::install(resource_root, data_dir, &channel)?;
    require_bool(&second_install, "bundled", true)?;
    require_bool(&second_install, "installed", true)?;
    let marker_after = read_json(&marker_path)?;
    let second_installed_at =
        validate_install_manifest(&marker_after, &runtime_manifest, bundle_sha256)?;
    if installed_at != second_installed_at {
        return Err("第二次 install 改写了已验证安装，幂等性检查失败。".into());
    }
    let final_status = dsh_runtime::status(resource_root, data_dir)?;
    require_bool(&final_status, "bundled", true)?;
    require_bool(&final_status, "installed", true)?;

    Ok(json!({
        "status": "ok",
        "path": "dsh_runtime::status -> install -> resolve -> version checks -> idempotent install",
        "initial": {"bundled": true, "installed": false},
        "final": {"bundled": true, "installed": true},
        "runtimeSource": "bundled",
        "bundleSha256": bundle_sha256,
        "bundleSize": bundle_size,
        "nodeVersion": node_version,
        "npmVersion": npm_version,
        "dshVersion": dsh_version,
        "repeatInstallIdempotent": true,
        "progressEvents": progress_events.load(Ordering::Relaxed),
    }))
}

fn main() {
    let mut args = std::env::args_os().skip(1);
    let resource_root = args.next().map(PathBuf::from);
    let data_dir = args.next().map(PathBuf::from);
    if args.next().is_some() || resource_root.is_none() || data_dir.is_none() {
        eprintln!("usage: dsh-runtime-coldstart <app-resource-root> <new-isolated-data-dir>");
        std::process::exit(2);
    }

    match run(&resource_root.unwrap(), &data_dir.unwrap()) {
        Ok(report) => println!("{report}"),
        Err(error) => {
            eprintln!("DSH Runtime cold-start acceptance failed: {error}");
            std::process::exit(1);
        }
    }
}
