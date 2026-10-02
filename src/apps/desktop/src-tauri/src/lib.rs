pub mod acp;
pub mod agent_host;
pub mod cad_plan;
pub mod cad_plan_validation;
pub mod dsh_runtime;
pub mod native_cad_session;
pub mod native_review_gate;
pub mod native_feature_specs;
pub mod native_geometry_evidence;
pub mod native_bore_metrology;
pub mod native_knowledge;
pub mod native_model_io;
pub mod native_occt;
pub mod native_occt_runtime;
pub mod native_plan_geometry;
pub mod native_standard_features;
pub mod native_render;
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
    let (sender, receiver) = oneshot::channel();
    thread::Builder::new()
        .name("ai-modeler-agent-host".into())
        .spawn(move || {
            let result = agent_host::send_turn(
                &host,
                app,
                request_id,
                text,
                images,
                llm_config,
                online_enabled,
                on_event,
            );
            let _ = sender.send(result);
        })
        .map_err(|error| format!("无法启动 Rust Agent Host 线程：{error}"))?;
    receiver
        .await
        .map_err(|_| "Rust Agent Host 线程意外退出。".to_owned())?
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
            backend_call,
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
