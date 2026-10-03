pub mod acp;
pub mod agent_host;
pub mod agent_model_config;
pub mod cad_plan;
pub mod cad_plan_validation;
pub mod dsh_runtime;
pub mod native_active_model;
pub mod native_bootstrap;
pub mod native_bore_metrology;
pub mod native_cad_metadata;
pub mod native_cad_session;
pub mod native_cad_tools;
pub mod native_fastener_specs;
pub mod native_feature_specs;
pub mod native_geometry_backend;
pub mod native_geometry_evidence;
pub mod native_knowledge;
pub mod native_model_io;
pub mod native_occt;
pub mod native_occt_runtime;
pub mod native_parametric_features;
pub mod native_plan_geometry;
pub mod native_quick;
pub mod native_quick_parser;
pub mod native_render;
pub mod native_rotor;
pub mod native_settings;
pub mod native_review_gate;
pub mod native_review_findings;
pub mod native_standard_features;
pub mod native_state_summary;
pub mod native_tool_bridge;
pub mod native_tool_executor;
pub mod native_tool_schemas;
pub mod native_vision_assets;
pub mod native_vision_domain;
pub mod native_vision_provider;
pub mod native_web;

use serde_json::{Value, json};
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::ipc::{Channel, Response};
use tauri::path::BaseDirectory;
use tauri::{AppHandle, Manager, State};
use tokio::sync::oneshot;

const MAX_INPUT_IMAGE_BYTES: u64 = 24 * 1024 * 1024;
const DEPENDENCY_MARKER: &str = "dependencies-installed.json";
const RUNTIME_VERSION_MARKER: &str = ".aimodeler-python-version";
const MIRRORS: [(&str, &str); 3] = [
    ("pypi", "https://pypi.org/simple"),
    ("tsinghua", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("aliyun", "https://mirrors.aliyun.com/pypi/simple/"),
];

#[derive(Default)]
struct BackendState {
    backend: Mutex<Option<Arc<Backend>>>,
}

struct Backend {
    python: PathBuf,
    child: Mutex<Child>,
    stdin: Mutex<ChildStdin>,
    pending: Mutex<HashMap<String, Pending>>,
}

struct Pending {
    result: oneshot::Sender<Result<Value, String>>,
    events: Channel<Value>,
}

struct RequestArgs {
    id: String,
    method: String,
    params: Value,
    on_event: Channel<Value>,
}

fn root_has_backend(path: &Path) -> bool {
    path.join("ai_modeler").join("__main__.py").is_file()
        && path.join("prompts").is_dir()
        && path.join("kb").is_dir()
}

fn project_root(app: &AppHandle) -> Result<(PathBuf, bool), String> {
    if let Some(root) = std::env::var_os("AIMODELER_ROOT") {
        let path = PathBuf::from(root);
        if root_has_backend(&path) {
            return path
                .canonicalize()
                .map(|root| (root, false))
                .map_err(|error| error.to_string());
        }
    }
    if let Ok(path) = app.path().resolve("app", BaseDirectory::Resource) {
        if root_has_backend(&path) {
            return path
                .canonicalize()
                .map(|root| (root, true))
                .map_err(|error| format!("无法解析已安装的 Python 源码目录：{error}"));
        }
    }
    let checkout_backend = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../legacy");
    if root_has_backend(&checkout_backend) {
        return checkout_backend
            .canonicalize()
            .map(|root| (root, false))
            .map_err(|error| error.to_string());
    }
    let mut candidates = Vec::new();
    if let Ok(current) = std::env::current_dir() {
        candidates.push(current);
    }
    if let Ok(executable) = std::env::current_exe() {
        candidates.extend(executable.ancestors().map(Path::to_path_buf));
    }
    for candidate in candidates {
        if root_has_backend(&candidate) {
            return candidate
                .canonicalize()
                .map(|root| (root, false))
                .map_err(|error| error.to_string());
        }
    }
    Err("AI Modeler 安装资源不完整：找不到内置 Python 源码。请重新安装软件；开发运行时可设置 AIMODELER_ROOT。".into())
}

fn python_command(
    app: &AppHandle,
    root: &Path,
    packaged: bool,
) -> Result<(PathBuf, Vec<String>), String> {
    if let Some(python) = std::env::var_os("AIMODELER_PYTHON") {
        return Ok((PathBuf::from(python), Vec::new()));
    }
    if packaged {
        let data_dir = app
            .path()
            .app_local_data_dir()
            .map_err(|error| format!("无法解析 AI Modeler 用户数据目录：{error}"))?;
        let python = data_dir
            .join("runtime")
            .join("python")
            .join(if cfg!(windows) {
                "python.exe"
            } else {
                "bin/python3"
            });
        if !python.is_file() {
            return Err("首次运行尚未准备好 Python 运行环境，请先完成依赖安装。".into());
        }
        Ok((python, Vec::new()))
    } else {
        let local = root.join(".venv").join("Scripts").join("python.exe");
        if local.is_file() {
            return Ok((local, Vec::new()));
        }
        let local_posix = root.join(".venv").join("bin").join("python");
        if local_posix.is_file() {
            return Ok((local_posix, Vec::new()));
        }
        if cfg!(windows) {
            Ok((PathBuf::from("python.exe"), Vec::new()))
        } else {
            Ok((PathBuf::from("python3"), Vec::new()))
        }
    }
}

fn app_data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map_err(|error| format!("无法解析 AI Modeler 用户数据目录：{error}"))
}

fn resource_root(app: &AppHandle) -> Result<PathBuf, String> {
    let explicit = std::env::var_os("AIMODELER_RESOURCE_ROOT")
        .or_else(|| std::env::var_os("AIMODELER_ROOT"))
        .map(PathBuf::from);
    let packaged = app.path().resolve("app", BaseDirectory::Resource).ok();
    let development = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../legacy");
    native_bootstrap::resolve_resource_root(
        explicit.as_deref(),
        packaged.as_deref(),
        Some(&development),
    )
}

fn private_python_available(data_dir: &Path) -> bool {
    if let Some(path) = std::env::var_os("AIMODELER_PYTHON") {
        return Path::new(&path).is_file();
    }
    data_dir
        .join("runtime")
        .join("python")
        .join(if cfg!(windows) {
            "python.exe"
        } else {
            "bin/python3"
        })
        .is_file()
}

fn catia_com_registered() -> Option<bool> {
    if !cfg!(windows) {
        return None;
    }
    std::process::Command::new("reg.exe")
        .args(["query", r"HKCR\CATIA.Application\CLSID"])
        .output()
        .ok()
        .map(|output| output.status.success())
}

#[tauri::command]
fn native_resource_status(app: AppHandle) -> Result<Value, String> {
    native_bootstrap::resource_status(&resource_root(&app)?)
}

#[tauri::command]
fn native_runtime_status(app: AppHandle) -> Result<Value, String> {
    let data_dir = app_data_dir(&app)?;
    let root = resource_root(&app)?;
    let dsh = match dsh_runtime::status(&root, &data_dir) {
        Ok(mut status) => {
            let installed = status
                .get("installed")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            status["available"] = json!(installed);
            status["state"] = json!(if installed { "ready" } else { "missing" });
            status
        }
        Err(error) => json!({"available":false,"installed":false,"state":"invalid","reason":error}),
    };
    let occt = match native_occt_runtime::available_manifest(&root) {
        Ok(Some(_)) => json!({"available":true,"state":"ready"}),
        Ok(None) => json!({"available":false,"state":"missing"}),
        Err(error) => json!({"available":false,"state":"invalid","reason":error}),
    };
    let catia = native_bootstrap::catia_readiness(catia_com_registered());
    let mut readiness = native_bootstrap::runtime_readiness(
        private_python_available(&data_dir),
        dsh,
        occt,
        catia,
    );
    readiness["appVersion"] = json!(env!("CARGO_PKG_VERSION"));
    Ok(readiness)
}

#[tauri::command(rename_all = "camelCase")]
async fn install_dsh_runtime(
    app: AppHandle,
    on_event: Channel<Value>,
) -> Result<Value, String> {
    let (sender, receiver) = oneshot::channel();
    thread::spawn(move || {
        let result = (|| {
            let root = resource_root(&app)?;
            let data_dir = app_data_dir(&app)?;
            let _ = dsh_runtime::install(&root, &data_dir, &on_event)?;
            native_runtime_status(app)
        })();
        let _ = sender.send(result);
    });
    receiver
        .await
        .map_err(|_| "DSH Runtime 安装线程意外退出。".to_owned())?
}

fn read_runtime_manifest(root: &Path) -> Result<Value, String> {
    let path = root.join("packaging").join("desktop-runtime.json");
    let contents = fs::read_to_string(&path)
        .map_err(|error| format!("安装包缺少依赖清单（{}）：{error}", path.display()))?;
    serde_json::from_str(&contents).map_err(|error| format!("无法读取依赖清单：{error}"))
}

fn dependency_status_for(root: &Path, data_dir: &Path) -> Result<Value, String> {
    let manifest = read_runtime_manifest(root)?;
    let python_installed = python_dependencies_installed(&manifest, data_dir);
    let dsh_status = dsh_runtime::status(root, data_dir)?;
    let installed = python_installed && dsh_status["installed"] == true;
    Ok(json!({
        "required": true,
        "installed": installed,
        "pythonInstalled": python_installed,
        "pythonVersion": manifest.get("pythonVersion").cloned().unwrap_or(Value::Null),
        "dshRuntime": dsh_status,
    }))
}

fn python_dependencies_installed(manifest: &Value, data_dir: &Path) -> bool {
    let expected_hash = manifest
        .get("requirementsSha256")
        .and_then(Value::as_str)
        .unwrap_or("");
    let marker_path = data_dir.join(DEPENDENCY_MARKER);
    fs::read_to_string(marker_path)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .map(|marker| {
            marker.get("requirementsSha256").and_then(Value::as_str) == Some(expected_hash)
                && marker.get("pythonVersion") == manifest.get("pythonVersion")
                && marker.get("pipVersion") == manifest.get("pipVersion")
                && data_dir.join("python-deps").is_dir()
                && data_dir
                    .join("runtime")
                    .join("python")
                    .join("python.exe")
                    .is_file()
        })
        .unwrap_or(false)
}

#[tauri::command]
fn dependency_status(app: AppHandle) -> Result<Value, String> {
    let (root, packaged) = project_root(&app)?;
    if !packaged {
        return Ok(json!({ "required": false, "installed": true }));
    }
    dependency_status_for(&root, &app_data_dir(&app)?)
}

fn copy_directory(source: &Path, destination: &Path) -> Result<(), String> {
    fs::create_dir_all(destination).map_err(|error| {
        format!(
            "无法创建 Python 运行目录（{}）：{error}",
            destination.display()
        )
    })?;
    for entry in fs::read_dir(source).map_err(|error| {
        format!(
            "无法读取内置 Python 运行时（{}）：{error}",
            source.display()
        )
    })? {
        let entry = entry.map_err(|error| error.to_string())?;
        let source_path = entry.path();
        let destination_path = destination.join(entry.file_name());
        let kind = entry.file_type().map_err(|error| error.to_string())?;
        if kind.is_dir() {
            copy_directory(&source_path, &destination_path)?;
        } else if kind.is_file() {
            fs::copy(&source_path, &destination_path).map_err(|error| {
                format!(
                    "复制 Python 运行文件失败（{}）：{error}",
                    source_path.display()
                )
            })?;
        }
    }
    Ok(())
}

fn prepare_private_python(
    app: &AppHandle,
    root: &Path,
    data_dir: &Path,
    manifest: &Value,
) -> Result<PathBuf, String> {
    let python_version = manifest
        .get("pythonVersion")
        .and_then(Value::as_str)
        .ok_or("依赖清单缺少 Python 版本。")?;
    let runtime_root = data_dir.join("runtime").join("python");
    let python = runtime_root.join("python.exe");
    let version_marker = runtime_root.join(RUNTIME_VERSION_MARKER);
    let existing_version = fs::read_to_string(&version_marker).unwrap_or_default();
    if !python.is_file() || existing_version.trim() != python_version {
        if runtime_root.exists() {
            fs::remove_dir_all(&runtime_root)
                .map_err(|error| format!("无法更新用户 Python 运行时：{error}"))?;
        }
        let bundled = app
            .path()
            .resolve("python", BaseDirectory::Resource)
            .map_err(|error| format!("无法定位安装包中的 Python 运行时：{error}"))?;
        if !bundled.join("python.exe").is_file() {
            return Err(format!(
                "安装包缺少 CPython 运行时（{}）。请重新安装软件。",
                bundled.display()
            ));
        }
        copy_directory(&bundled, &runtime_root)?;
        fs::write(&version_marker, python_version)
            .map_err(|error| format!("无法记录 Python 运行时版本：{error}"))?;
    }

    let dependencies = data_dir.join("python-deps");
    let stdlib_zip = format!(
        "python{}.zip",
        python_version
            .split('.')
            .take(2)
            .collect::<Vec<_>>()
            .join("")
    );
    let search_paths = [
        stdlib_zip,
        ".".to_owned(),
        "Lib\\site-packages".to_owned(),
        dependencies.to_string_lossy().into_owned(),
        root.to_string_lossy().into_owned(),
        "import site".to_owned(),
    ];
    let pth = runtime_root.join(format!(
        "python{}._pth",
        python_version
            .split('.')
            .take(2)
            .collect::<Vec<_>>()
            .join("")
    ));
    fs::write(&pth, format!("{}\r\n", search_paths.join("\r\n")))
        .map_err(|error| format!("无法配置内置 Python 搜索路径（{}）：{error}", pth.display()))?;
    Ok(python)
}

fn send_install_event(channel: &Channel<Value>, kind: &str, message: impl Into<String>) {
    let _ = channel.send(json!({ "kind": kind, "message": message.into() }));
}

fn mirror_url(mirror: &str) -> Result<&'static str, String> {
    MIRRORS
        .iter()
        .find(|(key, _)| *key == mirror)
        .map(|(_, url)| *url)
        .ok_or_else(|| "请选择官方 PyPI、清华大学或阿里云镜像。".into())
}

fn stream_install_output<R: Read + Send + 'static>(
    stream: R,
    label: &'static str,
    channel: Channel<Value>,
    output: Arc<Mutex<Vec<String>>>,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        for line in BufReader::new(stream).lines().flatten() {
            let line = line.trim().to_owned();
            if line.is_empty() {
                continue;
            }
            if let Ok(mut lines) = output.lock() {
                if lines.len() >= 40 {
                    lines.remove(0);
                }
                lines.push(if label.is_empty() {
                    line.clone()
                } else {
                    format!("{label}: {line}")
                });
            }
            send_install_event(&channel, "progress", line);
        }
    })
}

fn write_sitecustomize(path: &Path) -> Result<(), String> {
    let source = r#"
"""Load bundled Windows extension DLLs before importing native packages."""
import os
from pathlib import Path

_DLL_DIRECTORY_HANDLES = []
_site_packages = Path(__file__).resolve().parent
_dll_directories = [_site_packages, _site_packages / "OCP"]
_dll_directories.extend(path for path in _site_packages.glob("*.libs") if path.is_dir())
for _directory in _dll_directories:
    if _directory.is_dir():
        try:
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(_directory)))
        except (AttributeError, OSError):
            pass
"#;
    fs::write(path.join("sitecustomize.py"), source)
        .map_err(|error| format!("无法配置 Python 扩展库搜索路径：{error}"))
}

fn install_dependencies_sync(
    app: AppHandle,
    mirror: String,
    channel: Channel<Value>,
) -> Result<Value, String> {
    let mirror = mirror_url(&mirror)?;
    let (root, packaged) = project_root(&app)?;
    if !packaged {
        return Err("依赖安装向导仅适用于已安装版本。".into());
    }
    let data_dir = app_data_dir(&app)?;
    fs::create_dir_all(&data_dir)
        .map_err(|error| format!("无法创建用户数据目录（{}）：{error}", data_dir.display()))?;
    let manifest = read_runtime_manifest(&root)?;
    if python_dependencies_installed(&manifest, &data_dir) {
        send_install_event(
            &channel,
            "progress",
            "Python 依赖已就绪；正在检查 DSH ACP Runtime…",
        );
        let runtime_status = dsh_runtime::install(&root, &data_dir, &channel)?;
        send_install_event(
            &channel,
            "complete",
            "Python 与 DSH Runtime 均已就绪，正在启动工作区…",
        );
        return Ok(json!({
            "installed": true,
            "pythonVersion": manifest.get("pythonVersion").cloned().unwrap_or(Value::Null),
            "dshRuntime": runtime_status,
        }));
    }
    let requirements_hash = manifest
        .get("requirementsSha256")
        .and_then(Value::as_str)
        .ok_or("依赖清单缺少 requirementsSha256。")?
        .to_owned();
    send_install_event(&channel, "progress", "正在准备随软件附带的 Python 运行时…");
    let python = prepare_private_python(&app, &root, &data_dir, &manifest)?;
    let bootstrap = app
        .path()
        .resolve("bootstrap", BaseDirectory::Resource)
        .map_err(|error| format!("无法定位 pip 引导资源：{error}"))?;
    let pip_wheel_name = manifest
        .get("pipWheel")
        .and_then(Value::as_str)
        .ok_or("依赖清单缺少 pipWheel。")?;
    let pip_wheel = bootstrap.join(pip_wheel_name);
    if !pip_wheel.is_file() {
        return Err(format!(
            "安装包缺少 pip 引导文件（{}）。请重新安装软件。",
            pip_wheel.display()
        ));
    }
    let site_packages = python
        .parent()
        .unwrap_or(Path::new("."))
        .join("Lib")
        .join("site-packages");
    fs::create_dir_all(&site_packages).map_err(|error| error.to_string())?;
    let old_pip = site_packages.join("pip");
    if old_pip.exists() {
        fs::remove_dir_all(old_pip).map_err(|error| format!("无法更新 pip 引导程序：{error}"))?;
    }
    for entry in fs::read_dir(&site_packages).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        let name = entry.file_name().to_string_lossy().to_ascii_lowercase();
        if name.starts_with("pip-") && name.ends_with(".dist-info") {
            let path = entry.path();
            if path.is_dir() {
                fs::remove_dir_all(path)
                    .map_err(|error| format!("无法更新 pip 元数据：{error}"))?;
            } else {
                fs::remove_file(path).map_err(|error| format!("无法更新 pip 元数据：{error}"))?;
            }
        }
    }
    let extract = Command::new(&python)
        .args([
            "-s",
            "-c",
            "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])",
        ])
        .arg(&pip_wheel)
        .arg(&site_packages)
        .current_dir(&root)
        .env("PYTHONNOUSERSITE", "1")
        .output()
        .map_err(|error| format!("无法启动内置 Python（{}）：{error}", python.display()))?;
    if !extract.status.success() {
        return Err(format!(
            "无法初始化 pip：{}",
            String::from_utf8_lossy(&extract.stderr).trim()
        ));
    }

    let requirements = root
        .join("packaging")
        .join("desktop-requirements-win-x64.txt");
    if !requirements.is_file() {
        return Err(format!(
            "安装包缺少 Python 依赖清单（{}）。",
            requirements.display()
        ));
    }
    let staging = data_dir.join(format!("python-deps.installing-{}", std::process::id()));
    if staging.exists() {
        fs::remove_dir_all(&staging).map_err(|error| error.to_string())?;
    }
    fs::create_dir_all(&staging).map_err(|error| error.to_string())?;
    let report = data_dir.join("python-install-report.json");
    if report.exists() {
        fs::remove_file(&report).map_err(|error| error.to_string())?;
    }
    let mut command = Command::new(&python);
    command
        .args([
            "-s",
            "-m",
            "pip",
            "install",
            "--isolated",
            "--no-input",
            "--disable-pip-version-check",
            "--progress-bar",
            "off",
            "--no-compile",
            "--only-binary=:all:",
            "--index-url",
        ])
        .arg(mirror)
        .arg("--cache-dir")
        .arg(data_dir.join("pip-cache"))
        .arg("--target")
        .arg(&staging)
        .arg("--report")
        .arg(&report)
        .arg("-r")
        .arg(&requirements)
        .current_dir(&data_dir)
        .env("PYTHONNOUSERSITE", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    send_install_event(&channel, "progress", "正在下载并安装建模、图片和 CAD 依赖…");
    let mut child = command
        .spawn()
        .map_err(|error| format!("无法启动依赖安装程序：{error}"))?;
    let output = Arc::new(Mutex::new(Vec::new()));
    let stdout_thread = child
        .stdout
        .take()
        .map(|stream| stream_install_output(stream, "", channel.clone(), output.clone()));
    let stderr_thread = child
        .stderr
        .take()
        .map(|stream| stream_install_output(stream, "", channel.clone(), output.clone()));
    let status = child
        .wait()
        .map_err(|error| format!("等待依赖安装结束时出错：{error}"))?;
    if let Some(handle) = stdout_thread {
        let _ = handle.join();
    }
    if let Some(handle) = stderr_thread {
        let _ = handle.join();
    }
    if !status.success() {
        let details = output
            .lock()
            .map(|lines| lines.join("\n"))
            .unwrap_or_default();
        let _ = fs::remove_dir_all(&staging);
        return Err(if details.is_empty() {
            format!(
                "Python 依赖安装失败（退出码 {:?}）。请检查网络或更换镜像后重试。",
                status.code()
            )
        } else {
            format!(
                "Python 依赖安装失败。请检查网络或更换镜像后重试。\n{}",
                details
            )
        });
    }
    write_sitecustomize(&staging)?;
    let dependencies = data_dir.join("python-deps");
    let previous = data_dir.join("python-deps.previous");
    if previous.exists() {
        fs::remove_dir_all(&previous).map_err(|error| error.to_string())?;
    }
    if dependencies.exists() {
        fs::rename(&dependencies, &previous)
            .map_err(|error| format!("无法替换旧依赖目录：{error}"))?;
    }
    if let Err(error) = fs::rename(&staging, &dependencies) {
        if previous.exists() {
            let _ = fs::rename(&previous, &dependencies);
        }
        return Err(format!("无法启用已安装的依赖：{error}"));
    }
    if previous.exists() {
        let _ = fs::remove_dir_all(previous);
    }

    let marker = json!({
        "requirementsSha256": requirements_hash,
        "pythonVersion": manifest.get("pythonVersion").cloned().unwrap_or(Value::Null),
        "pipVersion": manifest.get("pipVersion").cloned().unwrap_or(Value::Null),
        "mirror": mirror,
        "installedAt": SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs(),
    });
    let marker_temp = data_dir.join(format!("{DEPENDENCY_MARKER}.tmp-{}", std::process::id()));
    fs::write(
        &marker_temp,
        serde_json::to_vec_pretty(&marker).map_err(|error| error.to_string())?,
    )
    .map_err(|error| format!("无法保存依赖安装状态：{error}"))?;
    let marker_path = data_dir.join(DEPENDENCY_MARKER);
    if marker_path.exists() {
        fs::remove_file(&marker_path).map_err(|error| error.to_string())?;
    }
    fs::rename(&marker_temp, &marker_path)
        .map_err(|error| format!("无法完成依赖安装状态写入：{error}"))?;
    let runtime_status = dsh_runtime::install(&root, &data_dir, &channel)?;
    send_install_event(&channel, "complete", "依赖安装完成，正在启动工作区…");
    Ok(json!({
        "installed": true,
        "pythonVersion": manifest.get("pythonVersion").cloned().unwrap_or(Value::Null),
        "dshRuntime": runtime_status,
    }))
}

#[tauri::command(rename_all = "camelCase")]
async fn install_dependencies(
    app: AppHandle,
    mirror: String,
    on_event: Channel<Value>,
) -> Result<Value, String> {
    let (sender, receiver) = oneshot::channel();
    thread::spawn(move || {
        let result = install_dependencies_sync(app, mirror, on_event);
        let _ = sender.send(result);
    });
    receiver
        .await
        .map_err(|_| "依赖安装线程意外退出。".to_owned())?
}

fn ensure_backend(state: &BackendState, app: &AppHandle) -> Result<Arc<Backend>, String> {
    let mut slot = state
        .backend
        .lock()
        .map_err(|_| "Python bridge 锁已损坏。")?;
    if let Some(backend) = slot.as_ref().cloned() {
        let running = {
            let mut child = backend
                .child
                .lock()
                .map_err(|_| "Python 子进程锁已损坏。")?;
            matches!(child.try_wait(), Ok(None))
        };
        if running {
            return Ok(backend);
        }
        *slot = None;
    }

    let (root, packaged) = project_root(app)?;
    let (python, prefix_args) = python_command(app, &root, packaged)?;
    let mut command = Command::new(&python);
    command
        .args(prefix_args)
        .args(["-s", "-m", "ai_modeler", "--ui-bridge"])
        .current_dir(&root)
        .env("AIMODELER_ROOT", &root)
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if packaged {
        let data_dir = app_data_dir(app)?;
        fs::create_dir_all(&data_dir).map_err(|error| {
            format!(
                "无法创建 AI Modeler 用户数据目录（{}）：{error}",
                data_dir.display()
            )
        })?;
        command
            .env("AIMODELER_DATA_DIR", &data_dir)
            .env("AIMODELER_CONFIG_PATH", data_dir.join("config.json"))
            .env("AIMODELER_PACKAGED_DESKTOP", "1");
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn().map_err(|error| {
        format!(
            "无法启动 Python 后端（{}）：{}。请检查 Python 3.9+、项目依赖和启动目录。",
            python.display(),
            error
        )
    })?;
    let stdin = child.stdin.take().ok_or("无法连接 Python 后端输入管道。")?;
    let stdout = child
        .stdout
        .take()
        .ok_or("无法读取 Python 后端输出管道。")?;
    let stderr = child
        .stderr
        .take()
        .ok_or("无法读取 Python 后端日志管道。")?;
    let backend = Arc::new(Backend {
        python,
        child: Mutex::new(child),
        stdin: Mutex::new(stdin),
        pending: Mutex::new(HashMap::new()),
    });
    spawn_stdout_reader(stdout, Arc::downgrade(&backend));
    spawn_stderr_reader(stderr);
    *slot = Some(backend.clone());
    Ok(backend)
}

fn spawn_stdout_reader(stdout: impl Read + Send + 'static, backend: std::sync::Weak<Backend>) {
    thread::Builder::new().name("ai-modeler-python-out".into()).spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            let Ok(line) = line else { break };
            let Some(backend) = backend.upgrade() else { return };
            let message: Value = match serde_json::from_str(&line) {
                Ok(value) => value,
                Err(error) => {
                    eprintln!("Python bridge emitted invalid JSON: {error}; line={line}");
                    continue;
                }
            };
            if let Some(event) = message.get("event").and_then(Value::as_str) {
                let request_id = message.get("request_id").and_then(Value::as_str).unwrap_or("");
                let data = message.get("data").cloned().unwrap_or(Value::Null);
                let pending = backend.pending.lock().ok()
                    .and_then(|map| map.get(request_id).map(|item| item.events.clone()));
                if let Some(channel) = pending {
                    let _ = channel.send(json!({
                        "event": event,
                        "request_id": request_id,
                        "data": data,
                    }));
                }
                continue;
            }
            let request_id = message.get("id").and_then(Value::as_str).unwrap_or("");
            let kind = message.get("type").and_then(Value::as_str).unwrap_or("");
            let pending = backend.pending.lock().ok().and_then(|mut map| map.remove(request_id));
            if let Some(pending) = pending {
                let response = if kind == "result" {
                    Ok(message.get("result").cloned().unwrap_or(Value::Null))
                } else {
                    let error = message.get("error").cloned().unwrap_or(Value::Null);
                    Err(error.get("message").and_then(Value::as_str)
                        .unwrap_or("Python 后端返回未知错误。").to_owned())
                };
                let _ = pending.result.send(response);
            }
        }
        if let Some(backend) = backend.upgrade() {
            let diagnostic = format!(
                "Python 后端进程已退出。请在项目目录运行 `{} -m ai_modeler --selftest` 检查依赖；详细诊断可在启动桌面程序的终端中查看。",
                backend.python.display()
            );
            if let Ok(mut map) = backend.pending.lock() {
                for (_, pending) in map.drain() {
                    let _ = pending.result.send(Err(diagnostic.clone()));
                }
            }
        }
    }).expect("failed to create Python stdout reader");
}

fn spawn_stderr_reader(stderr: impl Read + Send + 'static) {
    thread::Builder::new()
        .name("ai-modeler-python-log".into())
        .spawn(move || {
            for line in BufReader::new(stderr).lines().flatten() {
                eprintln!("[python] {line}");
            }
        })
        .expect("failed to create Python stderr reader");
}

async fn send_backend_call(
    state: &BackendState,
    app: &AppHandle,
    args: RequestArgs,
) -> Result<Value, String> {
    let backend = ensure_backend(state, app)?;
    let (sender, receiver) = oneshot::channel();
    {
        let mut pending = backend
            .pending
            .lock()
            .map_err(|_| "Python bridge 请求表已损坏。")?;
        if pending.contains_key(&args.id) {
            return Err("请求 ID 重复。".into());
        }
        pending.insert(
            args.id.clone(),
            Pending {
                result: sender,
                events: args.on_event,
            },
        );
    }
    let message = json!({"id": args.id, "method": args.method, "params": args.params});
    let encoded = serde_json::to_vec(&message).map_err(|error| error.to_string())?;
    let write_result = {
        let mut stdin = backend
            .stdin
            .lock()
            .map_err(|_| "Python bridge 输入管道已损坏。")?;
        stdin
            .write_all(&encoded)
            .and_then(|_| stdin.write_all(b"\n"))
            .and_then(|_| stdin.flush())
    };
    if let Err(error) = write_result {
        if let Ok(mut pending) = backend.pending.lock() {
            pending.remove(&args.id);
        }
        return Err(format!("无法向 Python 后端发送请求：{error}"));
    }
    receiver.await.map_err(|_| "Python 后端关闭了请求通道。")?
}

#[tauri::command(rename_all = "camelCase")]
async fn backend_call(
    state: State<'_, BackendState>,
    app: AppHandle,
    id: String,
    method: String,
    params: Value,
    on_event: Channel<Value>,
) -> Result<Value, String> {
    send_backend_call(
        &state,
        &app,
        RequestArgs {
            id,
            method,
            params,
            on_event,
        },
    )
    .await
}

#[tauri::command]
async fn quick_build(app: AppHandle, text: String, revision: i64) -> Result<Value, String> {
    let resources = resource_root(&app)?;
    let manifest = native_occt_runtime::resolve_manifest(&resources)?;
    let data_dir = app_data_dir(&app)?;
    let session_dir = data_dir.join("native-quick");
    let active_root = native_active_model::root_from_data_dir(&data_dir);
    let expected_generation = native_active_model::generation(&active_root)?;
    let next_generation = expected_generation
        .checked_add(1)
        .ok_or("活动模型 generation 已溢出。")?;
    tokio::task::spawn_blocking(move || {
        let document = native_quick::build(&text, revision, &manifest, &session_dir)?;
        if document["state"] == "parse_failed" {
            return Ok(document);
        }
        let mut candidate = native_quick::active_model_candidate(&text, &document)?;
        candidate["cad_state"]["active_model_generation"] = json!(next_generation);
        let active = native_active_model::commit(&active_root, expected_generation, &candidate)?;
        Ok(active["document"].clone())
    })
    .await
    .map_err(|error| format!("Quick 原生构建线程失败：{error}"))?
}

#[tauri::command]
fn active_model_snapshot(app: AppHandle) -> Result<Value, String> {
    let data_dir = app_data_dir(&app)?;
    let root = native_active_model::root_from_data_dir(&data_dir);
    Ok(native_active_model::load(&root)?.unwrap_or(Value::Null))
}

#[tauri::command(rename_all = "camelCase")]
fn active_model_new(app: AppHandle, name: String) -> Result<Value, String> {
    let data_dir = app_data_dir(&app)?;
    let root = native_active_model::root_from_data_dir(&data_dir);
    let expected_generation = native_active_model::generation(&root)?;
    let next_generation = expected_generation
        .checked_add(1)
        .ok_or("活动模型 generation 已溢出。")?;
    let current = native_active_model::load(&root)?;
    let revision = current
        .as_ref()
        .and_then(|record| record["revision"].as_i64())
        .unwrap_or(0)
        .checked_add(1)
        .ok_or("CAD revision 已溢出。")?;
    let project = json!({"format":"ai-modeler","version":1,"name":name,"source_prompt":"",
        "features":[],"current_path":null,"mesh_path":null,"output_paths":{},"stats":{},"build_log":["新建原生空模型"]});
    let candidate = json!({"origin":"native_ui","revision":revision,"cadplan":null,"geometry_fingerprint":null,
        "document":project,"output_paths":{},"cad_state":{"plan":null,"revision":revision,"actions":[],"output_paths":{},
            "active_model_generation":next_generation},"reference_manifest":null,"user_turns_manifest":null,
        "finish_summary":null,"geometric_status":"not_built","visual_status":"not_reviewed"});
    let active = native_active_model::commit(&root, expected_generation, &candidate)?;
    Ok(active["document"].clone())
}

#[tauri::command(rename_all = "camelCase")]
fn active_model_save(app: AppHandle, path: String) -> Result<Value, String> {
    let data_dir = app_data_dir(&app)?;
    let root = native_active_model::root_from_data_dir(&data_dir);
    let record = native_active_model::load(&root)?.ok_or("当前没有原生活动模型可保存。")?;
    let committed = save_active_model_record(&root, &record, Path::new(&path))?;
    Ok(committed["document"].clone())
}

fn save_active_model_record(root: &Path, record: &Value, path: &Path) -> Result<Value, String> {
    let plan = record.get("cadplan").filter(|value| value.is_object());
    let mut project = if let Some(plan) = plan {
        native_model_io::cadplan_to_project(plan)?
    } else {
        json!({"format":"ai-modeler","version":1,"name":record["document"]["name"],
            "source_prompt":record["document"]["source_prompt"],"features":[],"stats":{},"build_log":[]})
    };
    if let Some(legacy) = record
        .pointer("/cad_state/legacy_source_document")
        .filter(|value| value.is_object())
    {
        let reserved_root = [
            "format",
            "version",
            "name",
            "source_prompt",
            "features",
            "current_path",
            "mesh_path",
            "stats",
            "build_log",
            "cadplan",
        ];
        if let (Some(src), Some(dst)) = (legacy.as_object(), project.as_object_mut()) {
            for (key, value) in src {
                if !reserved_root.contains(&key.as_str()) {
                    dst.insert(key.clone(), value.clone());
                }
            }
        }
        if let (Some(src), Some(dst)) = (
            legacy["features"].as_array(),
            project["features"].as_array_mut(),
        ) {
            let reserved_feature = [
                "kind",
                "params",
                "op",
                "name",
                "note",
                "feature_id",
                "relations",
                "enabled",
                "provenance",
                "index",
                "description",
            ];
            for (old, new) in src.iter().zip(dst.iter_mut()) {
                if let (Some(old), Some(new)) = (old.as_object(), new.as_object_mut()) {
                    for (key, value) in old {
                        if !reserved_feature.contains(&key.as_str()) {
                            new.insert(key.clone(), value.clone());
                        }
                    }
                }
            }
        }
    }
    project["current_path"] = json!(path.to_string_lossy());
    let bytes = serialize_active_model_project(&project)?;
    let expected_generation = record["generation"]
        .as_u64()
        .ok_or("活动模型 generation 无效。")?;
    let next_generation = expected_generation
        .checked_add(1)
        .ok_or("活动模型 generation 已溢出。")?;
    let mut candidate = record.clone();
    candidate["document"]["current_path"] = json!(path.to_string_lossy());
    candidate["document"]["output_paths"]["aimodel"] = json!(path.to_string_lossy());
    candidate["saved_project_path"] = json!(path.to_string_lossy());
    candidate["output_paths"]["aimodel"] = json!(path.to_string_lossy());
    candidate["cad_state"]["output_paths"]["aimodel"] = json!(path.to_string_lossy());
    candidate["cad_state"]["saved_project_path"] = json!(path.to_string_lossy());
    candidate["cad_state"]["active_model_generation"] = json!(next_generation);
    native_active_model::commit_with_file(root, expected_generation, &candidate, path, &bytes)
}

fn serialize_active_model_project(project: &Value) -> Result<Vec<u8>, String> {
    let object = project
        .as_object()
        .ok_or(".aimodel 根节点必须是 JSON 对象")?;
    if let Some(features) = object.get("features") {
        let features = features.as_array().ok_or(".aimodel features 必须是数组")?;
        for (index, feature) in features.iter().enumerate() {
            if !feature.is_object() {
                return Err(format!("feature {index} 必须是对象"));
            }
            if feature
                .get("params")
                .is_some_and(|value| !value.is_object())
            {
                return Err(format!("feature {index} params 必须是对象"));
            }
        }
    }
    if object.get("name").is_some_and(|value| !value.is_string()) {
        return Err(".aimodel name 必须是字符串".into());
    }
    let bytes = serde_json::to_vec_pretty(project).map_err(|error| error.to_string())?;
    if bytes.len() as u64 > 128 * 1024 * 1024 {
        return Err(".aimodel 超过 128 MiB。".into());
    }
    Ok(bytes)
}

#[tauri::command(rename_all = "camelCase")]
fn active_model_export(app: AppHandle, kind: String, path: String) -> Result<(), String> {
    let data_dir = app_data_dir(&app)?;
    let root = native_active_model::root_from_data_dir(&data_dir);
    let record = native_active_model::load(&root)?.ok_or("当前没有原生活动模型可导出。")?;
    let source = match kind.as_str() {
        "stl" => record["output_paths"]["stl"].as_str(),
        "obj" => record["output_paths"]["obj"].as_str(),
        "report" => {
            if let Some(report_path) = record["output_paths"]["geometry_report"].as_str() {
                return fs::copy(report_path, &path)
                    .map(|_| ())
                    .map_err(|error| format!("导出几何报告失败：{error}"));
            }
            let report = record["document"]["geometry_report"].clone();
            if report.is_null() {
                return Err("活动模型没有几何报告。".into());
            }
            return native_model_io::atomic_write(
                Path::new(&path),
                serde_json::to_string_pretty(&report)
                    .map_err(|e| e.to_string())?
                    .as_bytes(),
            );
        }
        "catia" => return Err("原生活动模型尚无 CATIA 脚本导出器。".into()),
        _ => return Err(format!("不支持的原生导出类型：{kind}")),
    }
    .ok_or_else(|| format!("活动模型缺少 {kind} 输出路径。"))?;
    fs::copy(source, &path)
        .map(|_| ())
        .map_err(|error| format!("导出 {kind} 失败：{error}"))
}

#[tauri::command(rename_all = "camelCase")]
async fn active_model_export_snapshot(
    app: AppHandle,
    path: String,
    view_set: String,
) -> Result<(), String> {
    let views: &[&str] = match view_set.as_str() {
        "single" => &["iso"],
        "four" => &["iso", "front", "right", "top"],
        "three" => &["front", "right", "top"],
        "extended" => &["iso", "front", "right", "top", "back", "left", "bottom"],
        _ => return Err(format!("不支持的原生视图集：{view_set}")),
    };
    let data_dir = app_data_dir(&app)?;
    let root = native_active_model::root_from_data_dir(&data_dir);
    let record = native_active_model::load(&root)?.ok_or("当前没有原生活动模型可截图。")?;
    let plan = record
        .get("cadplan")
        .filter(|value| value.is_object())
        .ok_or("空模型没有原生快照。")?
        .clone();
    let expected_generation = record["generation"]
        .as_u64()
        .ok_or("活动模型 generation 无效。")?;
    let revision = record["revision"]
        .as_i64()
        .ok_or("活动模型 revision 无效。")?;
    let (legacy_root, _) = project_root(&app)?;
    let manifest = native_occt_runtime::resolve_manifest(&legacy_root)?;
    let session_dir = data_dir.join("native-active-model-snapshot");
    let plan_for_build = plan.clone();
    let built = tokio::task::spawn_blocking(move || {
        let cancel = std::sync::atomic::AtomicBool::new(false);
        native_geometry_backend::build_preview(
            &plan_for_build,
            revision,
            expected_generation,
            &manifest,
            &session_dir,
            &cancel,
        )
    })
    .await
    .map_err(|error| format!("原生快照构建线程失败：{error}"))??;
    if built.outcome.geometry_fingerprint != record["geometry_fingerprint"].as_str().unwrap_or("") {
        return Err("活动模型 fingerprint 与当前 OCCT 快照不符，拒绝导出。".into());
    }
    if native_active_model::generation(&root)? != expected_generation {
        return Err("快照生成期间活动模型已更新；旧 revision 图像未导出，请重试。".into());
    }
    let paths = built
        .views
        .iter()
        .filter(|row| views.contains(&row["view"].as_str().unwrap_or("")))
        .filter_map(|row| row["preview_image_path"].as_str())
        .map(PathBuf::from)
        .collect::<Vec<_>>();
    if paths.len() != views.len() {
        return Err("原生快照视图缺失。".into());
    }
    if paths.len() == 1 {
        let bytes = fs::read(&paths[0]).map_err(|error| format!("读取原生快照失败：{error}"))?;
        if native_active_model::generation(&root)? != expected_generation {
            return Err("保存时活动模型已更新，旧 revision 快照未写出。".into());
        }
        return native_model_io::atomic_write(Path::new(&path), &bytes);
    }
    let tiles = paths
        .iter()
        .map(|path| {
            image::open(path)
                .map(|image| image.to_rgba8())
                .map_err(|error| format!("无法读取原生视图图像：{error}"))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let tile_w = tiles[0].width();
    let tile_h = tiles[0].height();
    let (cols, rows) = match view_set.as_str() {
        "four" => (2, 2),
        "three" => (3, 1),
        _ => (4, 2),
    };
    let mut canvas = image::RgbaImage::from_pixel(
        tile_w * cols,
        tile_h * rows,
        image::Rgba([255, 255, 255, 255]),
    );
    for (index, tile) in tiles.iter().enumerate() {
        image::imageops::overlay(
            &mut canvas,
            tile,
            i64::from((index as u32 % cols) * tile_w),
            i64::from((index as u32 / cols) * tile_h),
        );
    }
    let mut bytes = Vec::new();
    image::DynamicImage::ImageRgba8(canvas)
        .write_to(
            &mut std::io::Cursor::new(&mut bytes),
            image::ImageFormat::Png,
        )
        .map_err(|error| format!("编码多视图 PNG 失败：{error}"))?;
    if native_active_model::generation(&root)? != expected_generation {
        return Err("保存时活动模型已更新，旧 revision 多视图未写出。".into());
    }
    native_model_io::atomic_write(Path::new(&path), &bytes)
}

#[tauri::command(rename_all = "camelCase")]
async fn active_model_import_project(
    app: AppHandle,
    path: String,
    confirmed: bool,
) -> Result<Value, String> {
    if !confirmed {
        return Err("导入旧工程需要先确认显式 CADPlan 转换。".into());
    }
    let source_path = PathBuf::from(path);
    let metadata =
        fs::metadata(&source_path).map_err(|error| format!("无法读取工程文件：{error}"))?;
    if metadata.len() > 60 * 1024 * 1024 {
        return Err("旧工程超过 60 MiB，无法在活动记录中无损保留原始 JSON。".into());
    }
    let source_document = native_model_io::load_document(&source_path)?;
    let mut plan = if source_document.get("cadplan").is_some() {
        let plan = source_document
            .get("cadplan")
            .filter(|value| value.is_object())
            .ok_or("旧工程包含非对象 CADPlan；拒绝忽略并生成不同几何。")?;
        let validation = cad_plan_validation::validate_plan(plan);
        if validation["valid"] != true {
            return Err(format!(
                "工程内已有 CADPlan 但校验失败；不会忽略它并另造模型：{validation}"
            ));
        }
        let generated = native_model_io::cadplan_to_project(plan)?;
        let original_features = source_document["features"]
            .as_array()
            .ok_or("带 CADPlan 的工程缺少 features 数组。")?;
        let generated_features = generated["features"]
            .as_array()
            .ok_or("CADPlan 转换结果缺少 features 数组。")?;
        if original_features.len() != generated_features.len() {
            return Err(
                "旧工程 features 数量与 CADPlan 映射结果不同；拒绝导入以避免静默丢失。".into(),
            );
        }
        for (index, (old, new)) in original_features.iter().zip(generated_features).enumerate() {
            for key in ["kind", "op", "params"] {
                if old.get(key) != new.get(key) {
                    return Err(format!(
                        "旧工程第 {index} 个特征的 {key} 与 CADPlan 不一致；拒绝静默改写。"
                    ));
                }
            }
        }
        plan.clone()
    } else {
        legacy_document_to_cadplan(&source_document)?
    };
    plan["name"] = source_document
        .get("name")
        .cloned()
        .unwrap_or_else(|| json!("Imported Model"));
    let validation = cad_plan_validation::validate_plan(&plan);
    if validation["valid"] != true {
        return Err(format!("旧工程 CADPlan 显式转换校验失败：{validation}"));
    }
    let data_dir = app_data_dir(&app)?;
    let active_root = native_active_model::root_from_data_dir(&data_dir);
    let expected_generation = native_active_model::generation(&active_root)?;
    let preview_generation = expected_generation
        .checked_add(1)
        .ok_or("活动模型 generation 已溢出。")?;
    let current_revision = native_active_model::load(&active_root)?
        .and_then(|row| row["revision"].as_i64())
        .unwrap_or(0);
    let revision = current_revision
        .checked_add(1)
        .ok_or("CAD revision 已溢出。")?
        .max(1);
    let (legacy_root, _) = project_root(&app)?;
    let manifest = native_occt_runtime::resolve_manifest(&legacy_root)?;
    let session_dir = data_dir.join("native-active-model-import");
    let plan_for_build = plan.clone();
    let preview = tokio::task::spawn_blocking(move || {
        let cancel = std::sync::atomic::AtomicBool::new(false);
        native_geometry_backend::build_preview(
            &plan_for_build,
            revision,
            preview_generation,
            &manifest,
            &session_dir,
            &cancel,
        )
    })
    .await
    .map_err(|error| format!("旧工程原生构建线程失败：{error}"))??;
    let state = json!({"plan":plan,"revision":revision,"preview_revision":revision,
        "preview_generation":preview_generation,"output_paths":preview.outcome.output_paths,
        "active_model_generation":preview_generation,
        "last_preview":preview.outcome.preview,"actions":[{"kind":"model","tool":"explicit_legacy_import",
        "revision":revision,"execution_engine":"rust_c_abi_occt"}],"finish_summary":null,
        "legacy_source_document":source_document,"legacy_source_path":source_path,"saved_project_path":source_path});
    let references = json!({"schema_version":1,"session_id":"legacy-import","images":[]});
    let turns = json!({"schema_version":1,"session_id":"legacy-import","turns":[]});
    let candidate = native_active_model::candidate_from_cad_state(
        "legacy_import",
        &state,
        &references,
        &turns,
    )?;
    let active = native_active_model::commit(&active_root, expected_generation, &candidate)?;
    Ok(active["document"].clone())
}

fn legacy_document_to_cadplan(document: &Value) -> Result<Value, String> {
    let features = document["features"]
        .as_array()
        .ok_or("旧工程 features 不是数组。")?;
    if features.is_empty() {
        return Err("空旧工程无需 CADPlan 几何转换；请使用新建零件。".into());
    }
    let mut converted = Vec::with_capacity(features.len());
    for (index, feature) in features.iter().enumerate() {
        let kind = feature["kind"]
            .as_str()
            .ok_or_else(|| format!("旧工程特征 {index} 缺少 kind，不能无损映射。"))?;
        let spec = native_feature_specs::feature_spec(kind)
            .map_err(|_| format!("旧工程特征 {index} 类型 {kind} 没有 CADPlan 映射。"))?;
        let params = feature
            .get("params")
            .filter(|value| value.is_object())
            .ok_or_else(|| format!("旧工程特征 {index} params 不是对象。"))?;
        let required = spec["required_parameters"]
            .as_array()
            .ok_or("原生特征参数规范不完整。")?;
        let optional = spec["optional_parameters"]
            .as_array()
            .ok_or("原生特征参数规范不完整。")?;
        let allowed = required
            .iter()
            .chain(optional.iter())
            .filter_map(Value::as_str)
            .collect::<std::collections::HashSet<_>>();
        if let Some(key) = params
            .as_object()
            .into_iter()
            .flatten()
            .map(|(key, _)| key)
            .find(|key| !allowed.contains(key.as_str()))
        {
            return Err(format!(
                "旧工程特征 {index} 含参数 {key}，CADPlan 映射会丢失它；导入已拒绝。"
            ));
        }
        if let Some(key) = required
            .iter()
            .filter_map(Value::as_str)
            .find(|key| !params.get(*key).is_some())
        {
            return Err(format!(
                "旧工程特征 {index} 缺少必需参数 {key}，无法精确转换。"
            ));
        }
        if feature.get("enabled").and_then(Value::as_bool) == Some(false) {
            return Err(format!(
                "旧工程特征 {index} 为禁用状态；CADPlan 没有无损 enabled 映射。"
            ));
        }
        let operation = match feature["op"].as_str().unwrap_or("add") {
            "add" => "add",
            "cut" => "cut",
            other => return Err(format!("旧工程特征 {index} operation {other} 无法映射。")),
        };
        let id = feature["feature_id"]
            .as_str()
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .unwrap_or_else(|| format!("legacy-{index}-{kind}"));
        let sources = params.as_object().into_iter().flatten().map(|(key, _)| (key.clone(), json!({"type":"existing_model","note":"从旧 AIMODEL 数值字段显式导入，原始文档保存在活动模型中。"}))).collect::<serde_json::Map<_, _>>();
        converted.push(json!({"id":id,"kind":kind,"operation":operation,"name":feature.get("name").cloned().unwrap_or_else(|| json!(kind)),
            "parameters":params,"sources":sources,"relations":feature.get("relations").cloned().unwrap_or_else(|| json!([]))}));
    }
    Ok(
        json!({"schema_version":"1.0","name":document.get("name").cloned().unwrap_or_else(|| json!("Imported Model")),
        "source_prompt":document.get("source_prompt").and_then(Value::as_str).unwrap_or(""),"units":{"length":"mm","angle":"deg"},
        "coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},
        "features":converted,"pending_questions":[]}),
    )
}

#[tauri::command(rename_all = "camelCase")]
async fn active_model_update_feature(
    app: AppHandle,
    index: usize,
    params: Value,
    name: Option<String>,
    action: Option<String>,
    delta: Option<i32>,
    enabled: Option<bool>,
) -> Result<Value, String> {
    if !params.is_object() {
        return Err("特征参数必须是对象。".into());
    }
    let data_dir = app_data_dir(&app)?;
    let root = native_active_model::root_from_data_dir(&data_dir);
    let record = native_active_model::load(&root)?.ok_or("当前没有原生活动模型。")?;
    let expected_generation = record["generation"]
        .as_u64()
        .ok_or("活动模型 generation 无效。")?;
    let preview_generation = expected_generation
        .checked_add(1)
        .ok_or("活动模型 generation 已溢出。")?;
    let revision = record["revision"]
        .as_i64()
        .ok_or("活动模型 revision 无效。")?
        .checked_add(1)
        .ok_or("CAD revision 已溢出。")?;
    let mut plan = record
        .get("cadplan")
        .filter(|value| value.is_object())
        .cloned()
        .ok_or("此旧工程没有 CADPlan；必须先进行显式导入转换。")?;
    let action = action.as_deref().unwrap_or("update");
    if action == "toggle" || enabled == Some(false) {
        return Err(
            "CADPlan v1 没有 enabled/disabled 状态，不能无损切换特征启停；请删除特征或清空模型。"
                .into(),
        );
    }
    let params_are_changed = params.as_object().is_some_and(|changes| {
        plan.pointer(&format!("/features/{index}/parameters"))
            .and_then(Value::as_object)
            .is_some_and(|existing| {
                changes
                    .iter()
                    .any(|(key, value)| existing.get(key) != Some(value))
            })
    });
    if action == "update"
        && params_are_changed
        && (plan.pointer("/units/length").and_then(Value::as_str) != Some("mm")
            || plan.pointer("/units/angle").and_then(Value::as_str) != Some("deg"))
    {
        return Err("UI 数值以 mm/deg 显示；当前 CADPlan 单位不同，拒绝可能失真的修改。".into());
    }
    let rows = plan["features"]
        .as_array()
        .ok_or("CADPlan features 无效。")?;
    let row = rows
        .get(index)
        .ok_or("特征序号超出 CADPlan 范围。")?
        .clone();
    let feature_id = row["id"].as_str().ok_or("特征 id 无效。")?.to_owned();
    let mut sources = serde_json::Map::new();
    match action {
        "update" => {
            for (key, value) in params.as_object().into_iter().flatten() {
                if !row["parameters"].get(key).is_some() {
                    return Err(format!("参数 {key} 不属于所选 CADPlan 特征。"));
                }
                if row["parameters"].get(key) != Some(value)
                    && (value.is_number()
                        || value
                            .as_array()
                            .is_some_and(|items| items.iter().any(Value::is_number)))
                {
                    sources.insert(key.clone(), json!({"type":"user","evidence":format!("用户在原生特征面板明确输入 {key}={value}")}));
                }
            }
            let fields = name
                .map(|value| json!({"name":value}))
                .unwrap_or(Value::Null);
            plan = crate::cad_plan::update(
                &plan,
                &feature_id,
                &params,
                &Value::Object(sources.clone()),
                &[],
                &fields,
            )?;
        }
        "delete" => plan = crate::cad_plan::remove(&plan, &feature_id)?,
        "move" => {
            let rows = plan["features"]
                .as_array_mut()
                .ok_or("CADPlan features 无效。")?;
            let target = index as i64 + i64::from(delta.unwrap_or(0));
            if target < 0 || target >= rows.len() as i64 {
                return Err("特征无法移动到列表边界之外。".into());
            }
            rows.swap(index, target as usize);
        }
        _ => return Err(format!("不支持的原生特征操作：{action}")),
    }
    if plan["features"].as_array().is_some_and(Vec::is_empty) {
        let empty = json!({"format":"ai-modeler","version":1,"name":record["document"]["name"],
            "source_prompt":record["document"]["source_prompt"],"features":[],"current_path":record["saved_project_path"],
            "mesh_path":null,"output_paths":{},"stats":{},"build_log":["删除最后一个特征，活动模型为空"]});
        let mut candidate = json!({"origin":record["origin"],"revision":revision,"cadplan":null,"geometry_fingerprint":null,
            "document":empty,"output_paths":{},"cad_state":record["cad_state"],"reference_manifest":record["reference_manifest"],
            "user_turns_manifest":record["user_turns_manifest"],"saved_project_path":record["saved_project_path"],
            "finish_summary":null,"geometric_status":"not_built","visual_status":"not_reviewed"});
        if candidate["cad_state"].is_object() {
            candidate["cad_state"]["plan"] = Value::Null;
            candidate["cad_state"]["revision"] = json!(revision);
            candidate["cad_state"]["output_paths"] = json!({});
            candidate["cad_state"]["active_model_generation"] = json!(preview_generation);
        }
        let root_for_commit = root.clone();
        return tokio::task::spawn_blocking(move || {
            native_active_model::commit(&root_for_commit, expected_generation, &candidate)
        })
        .await
        .map_err(|error| format!("活动模型删除提交线程失败：{error}"))?
        .map(|committed| committed["document"].clone());
    }
    let validation = cad_plan_validation::validate_plan(&plan);
    if validation["valid"] != true {
        return Err(format!("修改后的 CADPlan 校验失败：{validation}"));
    }
    let fingerprint = cad_plan::geometry_fingerprint(&plan)?;
    let (legacy_root, _) = project_root(&app)?;
    let manifest = native_occt_runtime::resolve_manifest(&legacy_root)?;
    let cancel = std::sync::atomic::AtomicBool::new(false);
    let session_dir = data_dir.join("native-active-model-edit");
    let build_plan = plan.clone();
    let preview = tokio::task::spawn_blocking(move || {
        native_geometry_backend::build_preview(
            &build_plan,
            revision,
            preview_generation,
            &manifest,
            &session_dir,
            &cancel,
        )
    })
    .await
    .map_err(|error| format!("原生特征重建线程失败：{error}"))??;
    if preview.outcome.geometry_fingerprint != fingerprint {
        return Err("重建后的 geometry fingerprint 不匹配，拒绝提交。".into());
    }
    let mut state = record
        .get("cad_state")
        .cloned()
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({}));
    state["plan"] = plan.clone();
    state["revision"] = json!(revision);
    state["active_model_generation"] = json!(preview_generation);
    state["output_paths"] = preview.outcome.output_paths.clone();
    state["preview_revision"] = json!(revision);
    state["preview_generation"] = json!(preview_generation);
    state["last_preview"] = preview.outcome.preview.clone();
    state["finish_summary"] = Value::Null;
    let turn_id = format!("ui-edit-{revision}");
    let mut turns = record
        .get("user_turns_manifest")
        .cloned()
        .filter(Value::is_object)
        .unwrap_or_else(
            || json!({"schema_version":1,"session_id":"native-active-model","turns":[]}),
        );
    turns["turns"].as_array_mut().ok_or("user turns manifest 无效。")?.push(json!({
        "turn_id":turn_id,"raw_user_prompt":"用户在原生特征面板明确编辑模型参数。","reference_ids":[]}));
    let mut provenance = Vec::new();
    for (key, source) in &sources {
        provenance.push(
            json!({"feature_id":feature_id,"parameter":key,"turn_id":turn_id,
            "value":plan["features"][index]["parameters"][key],"source":source}),
        );
    }
    let actions = state
        .as_object_mut()
        .ok_or("CAD state 必须是对象。")?
        .entry("actions")
        .or_insert_with(|| json!([]))
        .as_array_mut()
        .ok_or("CAD actions 必须是数组。")?;
    actions.push(json!({"kind":"model","tool":format!("ui_{action}"),
        "revision":revision,"execution_engine":"rust_c_abi_occt","user_source_provenance":provenance}));
    let candidate = native_active_model::candidate_from_cad_state(
        record["origin"].as_str().unwrap_or("native-ui"),
        &state,
        &record["reference_manifest"],
        &turns,
    )?;
    let root_for_commit = root.clone();
    tokio::task::spawn_blocking(move || {
        native_active_model::commit(&root_for_commit, expected_generation, &candidate)
    })
    .await
    .map_err(|error| format!("活动模型提交线程失败：{error}"))?
    .map(|committed| committed["document"].clone())
}

async fn run_agent_send<F>(send_turn: F) -> Result<Value, String>
where
    F: FnOnce() -> Result<Value, String> + Send + 'static,
{
    let (sender, receiver) = oneshot::channel();
    thread::Builder::new()
        .name("ai-modeler-agent-host".into())
        .spawn(move || {
            let _ = sender.send(send_turn());
        })
        .map_err(|error| format!("无法启动 Rust Agent Host 线程：{error}"))?;
    receiver
        .await
        .map_err(|_| "Rust Agent Host 线程意外退出。".to_owned())?
}

#[tauri::command(rename_all = "camelCase")]
async fn agent_send(
    state: State<'_, agent_host::AgentHostState>,
    app: AppHandle,
    request_id: String,
    text: String,
    images: Vec<String>,
    llm_config: Value,
    online_enabled: bool,
    on_event: Channel<Value>,
) -> Result<Value, String> {
    let host = state.inner().clone();
    run_agent_send(move || {
        agent_host::send_turn(
            &host,
            app,
            request_id,
            text,
            images,
            llm_config,
            online_enabled,
            on_event,
        )
    })
    .await
}

#[tauri::command]
fn agent_cancel(state: State<'_, agent_host::AgentHostState>) -> Result<Value, String> {
    agent_host::cancel(state.inner())
}

#[tauri::command]
async fn agent_reset(state: State<'_, agent_host::AgentHostState>) -> Result<Value, String> {
    let host = state.inner().clone();
    let (sender, receiver) = oneshot::channel();
    thread::Builder::new()
        .name("ai-modeler-agent-reset".into())
        .spawn(move || {
            let _ = sender.send(agent_host::reset(&host));
        })
        .map_err(|error| format!("无法关闭 DSH 会话：{error}"))?;
    receiver
        .await
        .map_err(|_| "Rust Agent Host reset 线程意外退出。".to_owned())?
}

#[tauri::command]
fn agent_history(app: AppHandle) -> Result<Value, String> {
    agent_host::history(&app)
}

fn asset_root() -> Result<PathBuf, String> {
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("APPDATA").map(PathBuf::from))
        .ok_or("找不到 Windows 用户数据目录。")?;
    Ok(base.join("AI Modeler").join("ui-preview"))
}

fn input_image_extension(path: &Path) -> Result<String, String> {
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    if !["png", "jpg", "jpeg", "webp", "bmp", "gif"].contains(&extension.as_str()) {
        return Err("图片格式不支持，请选择 PNG、JPG、WEBP、BMP 或 GIF。".into());
    }
    Ok(extension)
}

fn validate_input_image_size(size: u64) -> Result<(), String> {
    if size == 0 || size > MAX_INPUT_IMAGE_BYTES {
        return Err("图片为空或超过 24 MiB。".into());
    }
    Ok(())
}

fn staged_input_image_path(root: &Path, prefix: &str, extension: &str) -> Result<PathBuf, String> {
    fs::create_dir_all(root).map_err(|error| format!("无法创建图片缓存目录：{error}"))?;
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    Ok(root.join(format!(
        "{prefix}-{}-{nanos}.{extension}",
        std::process::id()
    )))
}

fn stage_input_image_file(source: &Path, root: &Path) -> Result<PathBuf, String> {
    let source = source
        .canonicalize()
        .map_err(|error| format!("无法读取所选图片：{error}"))?;
    if !source.is_file() {
        return Err("所选路径不是图片文件。".into());
    }
    let extension = input_image_extension(&source)?;
    let metadata = fs::metadata(&source).map_err(|error| format!("无法读取图片信息：{error}"))?;
    validate_input_image_size(metadata.len())?;
    let target = staged_input_image_path(root, "reference", &extension)?;
    match fs::copy(&source, &target) {
        Ok(copied) => {
            if let Err(error) = validate_input_image_size(copied) {
                let _ = fs::remove_file(&target);
                return Err(error);
            }
        }
        Err(error) => {
            let _ = fs::remove_file(&target);
            return Err(format!("无法暂存参考图片：{error}"));
        }
    }
    Ok(target)
}

#[cfg(windows)]
fn is_packaged_local_cache_asset(
    canonical_local_app_data: &Path,
    logical_root_suffix: &Path,
    requested: &Path,
    relative: &Path,
) -> bool {
    use std::path::Component;

    let relative_components = relative.components().collect::<Vec<_>>();
    if relative_components.is_empty()
        || relative_components
            .iter()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return false;
    }

    let requested_components = requested
        .components()
        .map(|component| component.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    let local_components = canonical_local_app_data
        .components()
        .map(|component| component.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    if requested_components.len() <= local_components.len()
        || !requested_components
            .iter()
            .zip(&local_components)
            .all(|(actual, expected)| actual.eq_ignore_ascii_case(expected))
    {
        return false;
    }

    let virtual_components = &requested_components[local_components.len()..];
    if virtual_components.len() <= relative_components.len() {
        return false;
    }
    let root_component_count = virtual_components.len() - relative_components.len();
    let redirected_root = &virtual_components[..root_component_count];
    let suffix_components = logical_root_suffix
        .components()
        .map(|component| component.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>();

    if redirected_root.len() != 4 + suffix_components.len()
        || !redirected_root[0].eq_ignore_ascii_case("Packages")
        || redirected_root[1].is_empty()
        || !redirected_root[2].eq_ignore_ascii_case("LocalCache")
        || !redirected_root[3].eq_ignore_ascii_case("Local")
        || !redirected_root[4..]
            .iter()
            .zip(&suffix_components)
            .all(|(actual, expected)| actual.eq_ignore_ascii_case(expected))
    {
        return false;
    }

    let requested_relative = &virtual_components[root_component_count..];
    requested_relative
        .iter()
        .zip(&relative_components)
        .all(|(actual, expected)| {
            actual.eq_ignore_ascii_case(&expected.as_os_str().to_string_lossy())
        })
}

#[tauri::command]
fn read_asset(path: String) -> Result<Response, String> {
    let logical_root = asset_root()?;
    let allowed = logical_root
        .canonicalize()
        .map_err(|error| format!("预览资源目录尚未创建：{error}"))?;
    let logical_requested = PathBuf::from(path);
    let relative = logical_requested
        .strip_prefix(&logical_root)
        .map_err(|_| "拒绝读取预览目录之外的文件。")?;
    let requested = logical_requested
        .canonicalize()
        .map_err(|error| error.to_string())?;

    let mut allowed_location = requested.starts_with(&allowed);
    #[cfg(windows)]
    if !allowed_location {
        if let Some(local_app_data) = std::env::var_os("LOCALAPPDATA").map(PathBuf::from) {
            if let (Ok(canonical_local_app_data), Ok(logical_root_suffix)) = (
                local_app_data.canonicalize(),
                logical_root.strip_prefix(&local_app_data),
            ) {
                allowed_location = is_packaged_local_cache_asset(
                    &canonical_local_app_data,
                    logical_root_suffix,
                    &requested,
                    relative,
                );
            }
        }
    }

    if !allowed_location || !requested.is_file() {
        return Err("拒绝读取预览目录之外的文件。".into());
    }
    let metadata = fs::metadata(&requested).map_err(|error| error.to_string())?;
    if metadata.len() > 256 * 1024 * 1024 {
        return Err("预览资源超过 256 MiB 限制。".into());
    }
    let bytes = fs::read(requested).map_err(|error| error.to_string())?;
    Ok(Response::new(bytes))
}

#[tauri::command]
fn save_input_image(name: String, data: Vec<u8>) -> Result<String, String> {
    validate_input_image_size(data.len() as u64).map_err(|error| format!("剪贴板{error}"))?;
    let extension = input_image_extension(Path::new(&name))?;
    let root = asset_root()?.join("incoming");
    let target = staged_input_image_path(&root, "clipboard", &extension)?;
    fs::write(&target, data).map_err(|error| format!("无法保存剪贴板图片：{error}"))?;
    Ok(target.to_string_lossy().into_owned())
}

#[tauri::command(rename_all = "camelCase")]
fn stage_input_image(source_path: String) -> Result<String, String> {
    let source = PathBuf::from(source_path);
    let target = stage_input_image_file(&source, &asset_root()?.join("incoming"))?;
    Ok(target.to_string_lossy().into_owned())
}

fn shutdown_backend(state: &BackendState) {
    let backend = state.backend.lock().ok().and_then(|mut slot| slot.take());
    let Some(backend) = backend else { return };
    let request = json!({"id":"tauri-exit", "method":"app.shutdown", "params":{}});
    if let Ok(mut stdin) = backend.stdin.lock() {
        let _ = writeln!(stdin, "{}", request);
        let _ = stdin.flush();
    }
    let deadline = Instant::now() + Duration::from_millis(900);
    if let Ok(mut child) = backend.child.lock() {
        while Instant::now() < deadline {
            if matches!(child.try_wait(), Ok(Some(_))) {
                return;
            }
            thread::sleep(Duration::from_millis(30));
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(BackendState::default())
        .manage(agent_host::AgentHostState::default())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            dependency_status,
            install_dependencies,
            native_resource_status,
            native_runtime_status,
            install_dsh_runtime,
            backend_call,
            quick_build,
            active_model_snapshot,
            active_model_new,
            active_model_save,
            active_model_export,
            active_model_update_feature,
            active_model_import_project,
            active_model_export_snapshot,
            native_settings::native_settings_load,
            native_settings::native_settings_save,
            native_settings::native_provider_templates,
            native_settings::native_provider_template,
            native_settings::native_provider_fetch_models,
            agent_send,
            agent_cancel,
            agent_reset,
            agent_history,
            read_asset,
            save_input_image,
            stage_input_image
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                shutdown_backend(&window.state::<BackendState>());
                agent_host::shutdown(&window.state::<agent_host::AgentHostState>());
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running AI Modeler desktop application");
}

#[cfg(test)]
mod input_image_tests {
    #[cfg(windows)]
    use super::is_packaged_local_cache_asset;
    use super::{MAX_INPUT_IMAGE_BYTES, stage_input_image_file};
    use std::fs;
    use std::io::Write;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn scratch_dir() -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "ai-modeler-image-test-{}-{stamp}",
            std::process::id()
        ));
        fs::create_dir_all(&path).expect("create scratch directory");
        path
    }

    #[test]
    fn stages_image_from_unicode_path_with_spaces() {
        let scratch = scratch_dir();
        let source = scratch.join("参考 零件.png");
        fs::write(&source, b"preview bytes").expect("write source image");
        let staged_root = scratch.join("cache").join("incoming");

        let staged = stage_input_image_file(&source, &staged_root).expect("stage image");

        assert!(staged.starts_with(&staged_root));
        assert_eq!(
            staged.extension().and_then(|value| value.to_str()),
            Some("png")
        );
        assert_eq!(
            fs::read(&staged).expect("read staged image"),
            b"preview bytes"
        );
        fs::remove_dir_all(scratch).expect("remove scratch directory");
    }

    #[test]
    fn rejects_unsupported_image_extension() {
        let scratch = scratch_dir();
        let source = scratch.join("reference.txt");
        fs::write(&source, b"not an image").expect("write source file");

        let error = stage_input_image_file(&source, &scratch.join("cache"))
            .expect_err("unsupported format must fail");

        assert!(error.contains("格式不支持"));
        fs::remove_dir_all(scratch).expect("remove scratch directory");
    }

    #[test]
    fn rejects_image_larger_than_clipboard_limit() {
        let scratch = scratch_dir();
        let source = scratch.join("large.png");
        let mut file = fs::File::create(&source).expect("create large image");
        file.set_len(MAX_INPUT_IMAGE_BYTES + 1)
            .expect("set large file length");
        file.flush().expect("flush large image");

        let error = stage_input_image_file(&source, &scratch.join("cache"))
            .expect_err("oversized image must fail");

        assert!(error.contains("24 MiB"));
        fs::remove_dir_all(scratch).expect("remove scratch directory");
    }

    #[cfg(windows)]
    #[test]
    fn accepts_only_matching_packaged_local_cache_redirects() {
        let local_app_data = PathBuf::from(r"C:\Users\tester\AppData\Local");
        let logical_root_suffix = PathBuf::from(r"AI Modeler\ui-preview");
        let relative = PathBuf::from(r"ai-modeler-session\mesh-r1.glb");
        let redirected = PathBuf::from(
            r"C:\Users\tester\AppData\Local\Packages\OpenAI.Codex_test\LocalCache\Local\AI Modeler\ui-preview\ai-modeler-session\mesh-r1.glb",
        );

        assert!(is_packaged_local_cache_asset(
            &local_app_data,
            &logical_root_suffix,
            &redirected,
            &relative,
        ));

        let outside = PathBuf::from(
            r"C:\Users\tester\AppData\Local\Packages\OpenAI.Codex_test\LocalCache\Local\Temp\mesh-r1.glb",
        );
        assert!(!is_packaged_local_cache_asset(
            &local_app_data,
            &logical_root_suffix,
            &outside,
            &relative,
        ));
    }
}

#[cfg(test)]
mod agent_ipc_integration_tests {
    use super::*;
    use crate::agent_host::TestSetup;
    use serde_json::{Value, json};
    use std::ffi::OsString;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::sync::{Arc, Mutex};
    use std::time::{SystemTime, UNIX_EPOCH};

    #[derive(Clone)]
    struct FixtureSetup(TestSetup);

    // Tauri's production AppHandle alias is Wry-backed and cannot be supplied
    // by MockRuntime. Keep this command as a thin argument adapter: it calls
    // the same threaded runner and AgentHost core, substituting only fake ACP
    // runtime paths and app-data for the test fixture.
    #[tauri::command(rename = "agent_send", rename_all = "camelCase")]
    async fn agent_send_fixture(
        state: State<'_, agent_host::AgentHostState>,
        setup: State<'_, FixtureSetup>,
        request_id: String,
        text: String,
        images: Vec<String>,
        llm_config: Value,
        online_enabled: bool,
        on_event: Channel<Value>,
    ) -> Result<Value, String> {
        let host = state.inner().clone();
        let setup = setup.0.clone();
        run_agent_send(move || {
            agent_host::send_turn_with_fixture(
                &host,
                setup,
                request_id,
                text,
                images,
                llm_config,
                online_enabled,
                on_event,
            )
        })
        .await
    }

    struct TestRoot(PathBuf);

    impl TestRoot {
        fn new() -> Self {
            let stamp = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "aimodeler-agent-ipc-test-{}-{stamp}",
                std::process::id()
            ));
            fs::create_dir_all(&path).expect("create isolated test root");
            Self(path)
        }
    }

    impl Drop for TestRoot {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn agent_send_routes_fake_acp_updates_through_tauri_channel() {
        let scratch = TestRoot::new();
        let app_data = scratch.0.join("app-data");
        fs::create_dir_all(&app_data).expect("create isolated app data directory");

        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../legacy")
            .canonicalize()
            .expect("resolve checkout legacy resources");
        let fake_runtime_root = scratch.0.join("fake-runtime");
        let fake_cli = fake_runtime_root.join("dsh/lib/bin.js");
        let fake_node_modules = fake_runtime_root.join("dsh/node_modules");
        fs::create_dir_all(fake_cli.parent().unwrap()).expect("create fake CLI parent");
        fs::create_dir_all(&fake_node_modules).expect("create fake DSH modules directory");
        fs::create_dir_all(fake_runtime_root.join("node")).expect("create fake node directory");
        fs::write(
            &fake_cli,
            "fixture ACP process is supplied by the Rust test harness\n",
        )
        .expect("create fake CLI marker");
        let runtime = crate::dsh_runtime::RuntimePaths {
            root: fake_runtime_root.clone(),
            node: std::env::current_exe().expect("resolve test executable"),
            dsh_cli: fake_cli,
            home: scratch.0.join("dsh-home"),
        };
        let release_file = scratch.0.join("release-fake-acp.prompt");
        let host_state = agent_host::AgentHostState::default();

        let plan = cad_plan::create(
            "IPC fixture model",
            json!({"id":"fixture-body","kind":"box","operation":"add","parameters":{"length":20,"width":10,"height":5},"sources":{
                "length":{"type":"user","evidence":"20 mm"},"width":{"type":"user","evidence":"10 mm"},"height":{"type":"user","evidence":"5 mm"}}}),
            "fixture prompt",
            None,
        );
        let cad_state = json!({"plan":plan,"revision":1,"actions":[],"output_paths":{}});
        let candidate = native_active_model::candidate_from_cad_state(
            "agent",
            &cad_state,
            &json!({"schema_version":1,"images":[]}),
            &json!({"schema_version":1,"turns":[]}),
        )
        .expect("make isolated candidate model");
        let active_model_root = native_active_model::root_from_data_dir(&app_data);
        let captured_events = Arc::new(Mutex::new(Vec::<Value>::new()));
        let sink_events = Arc::clone(&captured_events);
        let sink_candidate = candidate;
        let sink_cad_state = cad_state;
        let sink_active_model_root = active_model_root;
        let sink_app_data = app_data.clone();
        let sink_release_file = release_file.clone();

        let setup = FixtureSetup(TestSetup {
            root,
            data_dir: app_data.clone(),
            runtime,
            dsh_args: vec![
                OsString::from("--quiet"),
                OsString::from("--exact"),
                OsString::from("acp::tests::fake_acp_child_process"),
            ],
            dsh_env: vec![
                (
                    OsString::from("AIMODELER_ACP_TEST_FIXTURE"),
                    OsString::from("1"),
                ),
                (
                    OsString::from("AIMODELER_ACP_TEST_RELEASE_FILE"),
                    release_file.clone().into_os_string(),
                ),
            ],
        });

        let app = tauri::test::mock_builder()
            .manage(host_state.clone())
            .manage(setup)
            .channel_interceptor(move |_webview, _callback, index, body| {
                let value = match body {
                    tauri::ipc::InvokeResponseBody::Json(value) => {
                        serde_json::from_str::<Value>(value).expect("decode AgentHost event JSON")
                    }
                    tauri::ipc::InvokeResponseBody::Raw(_) => {
                        panic!("AgentHost events are sent as JSON")
                    }
                };
                if value["event"] == "agent.message"
                    && value["request_id"] == "headless-agent-ipc"
                    && !sink_release_file.exists()
                {
                    let committed =
                        native_active_model::commit(&sink_active_model_root, 0, &sink_candidate)
                            .expect("publish fixture CAD transaction");
                    let mut committed_cad_state = sink_cad_state.clone();
                    committed_cad_state["active_model_generation"] =
                        committed["generation"].clone();
                    let session_meta: Value = serde_json::from_slice(
                        &fs::read(sink_app_data.join("agent-host/current-session.json"))
                            .expect("Agent Host must persist the active session path"),
                    )
                    .expect("session metadata must be valid JSON");
                    let session_dir = Path::new(
                        session_meta["cwd"]
                            .as_str()
                            .expect("session metadata has workspace path"),
                    );
                    fs::write(
                        session_dir.join("cad-state.json"),
                        serde_json::to_vec(&committed_cad_state)
                            .expect("serialize fixture CAD state"),
                    )
                    .expect("publish session CAD state");
                    fs::write(&sink_release_file, b"ready").expect("release fake ACP child");
                }
                sink_events
                    .lock()
                    .expect("event sink lock")
                    .push(json!({"index":index,"message":value}));
                true
            })
            .invoke_handler(tauri::generate_handler![agent_send_fixture])
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .expect("build headless Tauri MockRuntime app");
        let webview = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
            .build()
            .expect("build MockRuntime webview");
        let request = tauri::webview::InvokeRequest {
            cmd: "agent_send".into(),
            callback: tauri::ipc::CallbackFn(0),
            error: tauri::ipc::CallbackFn(1),
            url: "http://tauri.localhost"
                .parse()
                .expect("parse local mock URL"),
            body: tauri::ipc::InvokeBody::Json(json!({
                "requestId":"headless-agent-ipc",
                "text":"fixture prompt reaches ACP",
                "images":[],
                "llmConfig":{"base_url":"https://api.deepseek.com/responses","model":"deepseek-flash","api_key":"fixture","reasoning_effort":"none"},
                "onlineEnabled":false,
                "onEvent":"__CHANNEL__:97"
            })),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.to_owned(),
        };
        let response = tauri::test::get_ipc_response(&webview, request)
            .expect("invoke shared agent_send core through MockRuntime");
        let result = response
            .deserialize::<Value>()
            .expect("deserialize AgentHost command result");

        let events = captured_events.lock().expect("event sink lock").clone();
        assert_eq!(result["turn_status"], "completed");
        assert_eq!(result["reply"], "fixture prompt reaches ACP");
        assert!(events.iter().any(|row| {
            row["message"]["event"] == "agent.message"
                && row["message"]["request_id"] == "headless-agent-ipc"
                && row["message"]["data"]["text"] == "fixture prompt reaches ACP"
        }));
        assert!(events.iter().any(|event| {
            event["message"]["event"] == "agent.thought"
                && event["message"]["data"]["text"] == "fixture thought"
        }));
        assert!(events.iter().any(|event| {
            event["message"]["event"] == "agent.tool"
                && event["message"]["data"]["tool_call_id"] == "fixture-tool-1"
                && event["message"]["data"]["status"] == "running"
        }));
        assert!(events.iter().any(|event| {
            event["message"]["event"] == "agent.status"
                && event["message"]["data"]["message"] == "DSH 正在处理本轮请求。"
        }));
        let model_changed = events
            .iter()
            .find(|event| event["message"]["event"] == "model.changed")
            .expect("active-model commit should emit model.changed through Tauri Channel");
        assert_eq!(model_changed["message"]["request_id"], "headless-agent-ipc");
        assert_eq!(model_changed["message"]["data"]["revision"], 1);
        assert_eq!(
            model_changed["message"]["data"]["name"],
            "IPC fixture model"
        );
        let terminal = events
            .iter()
            .find(|event| event["message"]["event"] == "agent.turn_complete")
            .expect("turn completion should be emitted through Tauri Channel");
        assert!(
            terminal["index"].as_u64().unwrap() > model_changed["index"].as_u64().unwrap(),
            "terminal event must follow the geometry commit event"
        );
        assert_eq!(terminal["message"]["data"]["turn_status"], "completed");
        assert_eq!(terminal["message"]["data"]["stop_reason"], "end_turn");
        assert_eq!(terminal["message"]["data"]["current_model"]["exists"], true);
        assert_eq!(
            terminal["message"]["data"]["current_model"]["status"],
            "candidate"
        );
        assert_eq!(terminal["message"]["data"]["current_model"]["revision"], 1);

        println!(
            "AIMODELER_AGENT_IPC_CAPTURE={}",
            json!({"result":result,"events":events})
        );

        agent_host::shutdown(&host_state);
        drop(scratch);
    }
}

#[cfg(test)]
mod native_active_model_import_tests {
    use super::legacy_document_to_cadplan;
    use serde_json::json;

    fn legacy_box() -> serde_json::Value {
        json!({"name":"legacy box","source_prompt":"box 20 x 10 x 5 mm","features":[{
            "kind":"box","op":"add","feature_id":"body","name":"Body",
            "params":{"length":20,"width":10,"height":5},"enabled":true
        }]})
    }

    #[test]
    fn maps_supported_legacy_feature_and_marks_values_as_existing_model() {
        let plan = legacy_document_to_cadplan(&legacy_box()).expect("supported box maps");
        assert_eq!(plan["units"], json!({"length":"mm","angle":"deg"}));
        assert_eq!(plan["features"][0]["id"], "body");
        assert_eq!(
            plan["features"][0]["parameters"],
            json!({"length":20,"width":10,"height":5})
        );
        assert_eq!(
            plan["features"][0]["sources"]["length"]["type"],
            "existing_model"
        );
        assert_eq!(
            crate::cad_plan_validation::validate_plan(&plan)["valid"],
            true
        );
    }

    #[test]
    fn refuses_legacy_fields_that_cannot_be_mapped_without_loss() {
        let mut document = legacy_box();
        document["features"][0]["params"]["legacy_opaque"] = json!(17);
        assert!(
            legacy_document_to_cadplan(&document)
                .unwrap_err()
                .contains("会丢失")
        );
        let mut disabled = legacy_box();
        disabled["features"][0]["enabled"] = json!(false);
        assert!(
            legacy_document_to_cadplan(&disabled)
                .unwrap_err()
                .contains("禁用状态")
        );
    }
}

#[cfg(test)]
mod native_active_model_save_tests {
    use super::save_active_model_record;
    use crate::{native_active_model, native_model_io};
    use serde_json::{Value, json};
    use std::{
        fs,
        path::PathBuf,
        time::{SystemTime, UNIX_EPOCH},
    };

    fn temp_root(label: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "active-save-{label}-{}-{stamp}",
            std::process::id()
        ))
    }

    fn candidate(origin: &str) -> Value {
        let plan = crate::cad_plan::create(
            "save test",
            json!({"id":"body","kind":"box","operation":"add","parameters":{"length":12,"width":8,"height":4},"sources":{
                "length":{"type":"user","evidence":"12 mm"},"width":{"type":"user","evidence":"8 mm"},"height":{"type":"user","evidence":"4 mm"}}}),
            "save test",
            None,
        );
        let document = native_model_io::cadplan_to_project(&plan).unwrap();
        let fingerprint = crate::cad_plan::geometry_fingerprint(&plan).unwrap();
        json!({"origin":origin,"revision":1,"geometry_fingerprint":fingerprint,"cadplan":plan,
            "document":document,"output_paths":{},"cad_state":{"plan":plan,"revision":1,"output_paths":{},"saved_project_path":null,"active_model_generation":1},
            "finish_summary":null,"geometric_status":"not_built","visual_status":"not_reviewed"})
    }

    #[test]
    fn production_save_logic_commits_project_and_rejects_stale_generation_before_target_write() {
        let root = temp_root("transaction");
        let destination_dir = temp_root("destination");
        fs::create_dir_all(&destination_dir).unwrap();
        let target = destination_dir.join("saved.aimodel");
        let first = native_active_model::commit(&root, 0, &candidate("quick")).unwrap();
        let written = save_active_model_record(&root, &first, &target).unwrap();
        assert_eq!(written["generation"], 2);
        let saved_project = native_model_io::load_aimodel(&target).unwrap();
        assert_eq!(
            saved_project["current_path"],
            target.to_string_lossy().as_ref()
        );
        assert_eq!(
            written["output_paths"]["aimodel"],
            target.to_string_lossy().as_ref()
        );
        assert_eq!(
            written["document"]["output_paths"]["aimodel"],
            target.to_string_lossy().as_ref()
        );
        assert_eq!(
            written["cad_state"]["output_paths"]["aimodel"],
            target.to_string_lossy().as_ref()
        );
        assert_eq!(native_active_model::load(&root).unwrap().unwrap(), written);

        let stale = written.clone();
        let newer = native_active_model::commit(&root, 2, &candidate("agent")).unwrap();
        let existing_target = destination_dir.join("existing.aimodel");
        fs::write(&existing_target, b"keep old bytes").unwrap();
        let error = save_active_model_record(&root, &stale, &existing_target).unwrap_err();
        assert!(error.contains("活动模型已变化"));
        assert_eq!(fs::read(&existing_target).unwrap(), b"keep old bytes");
        assert_eq!(native_active_model::load(&root).unwrap().unwrap(), newer);
        assert_eq!(fs::read_dir(&destination_dir).unwrap().count(), 2);

        fs::remove_dir_all(root).unwrap();
        fs::remove_dir_all(destination_dir).unwrap();
    }
}
