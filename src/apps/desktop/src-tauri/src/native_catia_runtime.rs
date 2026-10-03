//! Private, on-demand CPython runtime for the optional CATIA COM adapter.
//!
//! Quick, Agent, rendering and model I/O use Rust. The runtime and pywin32
//! payload are copied into the user's data directory only when CATIA export or
//! push is requested. No PATH Python or Node executable is consulted here.

use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::fs;
use std::io::Cursor;
use std::path::{Component, Path, PathBuf};
use zip::ZipArchive;

const MARKER: &str = ".catia-python-runtime.json";

fn sha256(bytes: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(bytes);
    format!("{:x}", digest.finalize())
}

fn safe_relative(value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    if value.is_empty()
        || path.is_absolute()
        || path.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::Prefix(_) | Component::RootDir
            )
        })
    {
        return Err(format!("CATIA Python 清单包含不安全路径：{value}"));
    }
    Ok(path.to_path_buf())
}

fn verify_python_bundle(python_root: &Path, manifest: &Value) -> Result<(), String> {
    let files = manifest["pythonFiles"]
        .as_array()
        .filter(|files| !files.is_empty())
        .ok_or("CATIA Python 清单缺少嵌入式运行时文件哈希。")?;
    for row in files {
        let relative = row["path"]
            .as_str()
            .ok_or("CATIA Python 文件清单缺少路径。")?;
        let path = python_root.join(safe_relative(relative)?);
        let bytes = fs::read(&path)
            .map_err(|error| format!("CATIA Python 资源缺失（{}）：{error}", path.display()))?;
        if row["size"].as_u64() != Some(bytes.len() as u64)
            || row["sha256"].as_str() != Some(sha256(&bytes).as_str())
        {
            return Err(format!("CATIA Python 资源校验失败：{relative}"));
        }
    }
    Ok(())
}

fn copy_tree(source: &Path, destination: &Path) -> Result<(), String> {
    fs::create_dir_all(destination)
        .map_err(|error| format!("无法创建应用私有 CATIA Python 目录：{error}"))?;
    for item in fs::read_dir(source).map_err(|error| error.to_string())? {
        let item = item.map_err(|error| error.to_string())?;
        let source_path = item.path();
        let target_path = destination.join(item.file_name());
        let file_type = item.file_type().map_err(|error| error.to_string())?;
        if file_type.is_symlink() {
            return Err(format!(
                "拒绝复制符号链接形式的 Python 资源：{}",
                source_path.display()
            ));
        }
        if file_type.is_dir() {
            copy_tree(&source_path, &target_path)?;
        } else if file_type.is_file() {
            fs::copy(&source_path, &target_path).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

fn extract_wheel(wheel: &[u8], destination: &Path) -> Result<(), String> {
    let mut archive = ZipArchive::new(Cursor::new(wheel))
        .map_err(|error| format!("pywin32 wheel 不是有效 ZIP：{error}"))?;
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).map_err(|error| error.to_string())?;
        let relative = entry
            .enclosed_name()
            .ok_or("pywin32 wheel 含有越界文件路径。")?
            .to_path_buf();
        let path = destination.join(relative);
        if entry.is_dir() {
            fs::create_dir_all(&path).map_err(|error| error.to_string())?;
            continue;
        }
        let parent = path.parent().ok_or("pywin32 wheel 文件缺少父目录。")?;
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        let mut output = fs::File::create(&path).map_err(|error| error.to_string())?;
        std::io::copy(&mut entry, &mut output).map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn write_sitecustomize(site_packages: &Path) -> Result<(), String> {
    let path = site_packages.join("sitecustomize.py");
    let source = r#"
"""Load packaged pywin32 DLLs without machine-wide PATH changes."""
import os
from pathlib import Path

_site = Path(__file__).resolve().parent
_dll_dirs = [_site, _site / "pywin32_system32", _site / "win32", _site / "win32" / "lib"]
_handles = []
for _directory in _dll_dirs:
    if _directory.is_dir():
        try:
            _handles.append(os.add_dll_directory(str(_directory)))
        except (AttributeError, OSError):
            pass
"#;
    fs::write(path, source).map_err(|error| format!("无法配置 pywin32 DLL 搜索目录：{error}"))
}

fn remove_owned_runtime(path: &Path, runtime_parent: &Path) -> Result<(), String> {
    if !path.exists() {
        return Ok(());
    }
    let parent = runtime_parent
        .canonicalize()
        .map_err(|error| format!("无法校验 CATIA Python 所有权目录：{error}"))?;
    let resolved = path
        .canonicalize()
        .map_err(|error| format!("无法解析旧 CATIA Python 运行时：{error}"))?;
    if !resolved.starts_with(&parent) || resolved == parent {
        return Err(format!(
            "拒绝清理所有权范围外的 CATIA Python 路径：{}",
            path.display()
        ));
    }
    let metadata = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    if metadata.file_type().is_symlink() {
        return Err(format!(
            "拒绝递归删除符号链接形式的 CATIA Python 路径：{}",
            path.display()
        ));
    }
    fs::remove_dir_all(path).map_err(|error| format!("无法替换 CATIA Python 运行时：{error}"))
}

/// Prepare and return the packaged runtime's `python.exe` after validating all
/// shipped files. `app_root` contains the explicit CATIA-only requirements file.
pub fn prepare(
    app_root: &Path,
    python_bundle: &Path,
    catia_bootstrap: &Path,
    data_dir: &Path,
) -> Result<PathBuf, String> {
    let manifest_path = catia_bootstrap.join("catia-runtime-manifest.json");
    let manifest: Value = serde_json::from_slice(
        &fs::read(&manifest_path)
            .map_err(|error| format!("安装包缺少 CATIA 运行时清单：{error}"))?,
    )
    .map_err(|error| format!("CATIA 运行时清单格式错误：{error}"))?;
    if manifest["manifestVersion"].as_u64() != Some(1)
        || manifest["platform"].as_str() != Some("windows-x86_64")
        || manifest["pythonVersion"].as_str() != Some("3.13.16")
        || manifest["pywin32Version"].as_str() != Some("312")
    {
        return Err("CATIA Python 运行时版本或平台清单不受支持。".into());
    }
    verify_python_bundle(python_bundle, &manifest)?;

    let requirements_file = manifest["requirementsFile"]
        .as_str()
        .ok_or("CATIA 运行时清单缺少 requirementsFile。")?;
    if requirements_file != "desktop-catia-requirements-win-x64.txt" {
        return Err("CATIA Python 依赖清单路径不受支持。".into());
    }
    let requirements = fs::read(app_root.join("packaging").join(requirements_file))
        .map_err(|error| format!("CATIA Python 依赖清单缺失：{error}"))?;
    if manifest["requirementsSha256"].as_str() != Some(sha256(&requirements).as_str()) {
        return Err("CATIA Python 依赖清单哈希不匹配。".into());
    }

    let wheel_name = manifest["pywin32Wheel"]
        .as_str()
        .ok_or("CATIA 运行时清单缺少 pywin32 wheel 名称。")?;
    if wheel_name != "pywin32-312-cp313-cp313-win_amd64.whl" {
        return Err("CATIA pywin32 wheel 文件名与固定版本不匹配。".into());
    }
    let wheel_path = catia_bootstrap.join(wheel_name);
    let wheel =
        fs::read(&wheel_path).map_err(|error| format!("安装包缺少 pywin32 COM 依赖：{error}"))?;
    if manifest["pywin32WheelSha256"].as_str() != Some(sha256(&wheel).as_str()) {
        return Err("pywin32 wheel SHA-256 校验失败。".into());
    }

    let version = manifest["pythonVersion"].as_str().unwrap();
    let runtime_parent = data_dir.join("runtime");
    fs::create_dir_all(&runtime_parent)
        .map_err(|error| format!("无法创建用户私有 CATIA 运行时目录：{error}"))?;
    let runtime = runtime_parent.join("catia-python");
    let python = runtime.join("python.exe");
    let marker_path = runtime.join(MARKER);
    let marker: Value = fs::read(&marker_path)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or(Value::Null);
    let current = python.is_file()
        && marker["pythonVersion"].as_str() == Some(version)
        && marker["requirementsSha256"] == manifest["requirementsSha256"]
        && marker["pywin32WheelSha256"] == manifest["pywin32WheelSha256"];
    if current {
        return Ok(python);
    }

    remove_owned_runtime(&runtime, &runtime_parent)?;
    fs::create_dir_all(&runtime).map_err(|error| error.to_string())?;
    copy_tree(python_bundle, &runtime)?;
    if !runtime.join("python.exe").is_file() {
        return Err("嵌入式 Python 资源缺少 python.exe。".into());
    }
    let site_packages = runtime.join("Lib").join("site-packages");
    fs::create_dir_all(&site_packages).map_err(|error| error.to_string())?;
    extract_wheel(&wheel, &site_packages)?;
    write_sitecustomize(&site_packages)?;
    let marker_value = json!({
        "pythonVersion": version,
        "requirementsSha256": manifest["requirementsSha256"],
        "pywin32WheelSha256": manifest["pywin32WheelSha256"],
    });
    fs::write(
        &marker_path,
        serde_json::to_vec_pretty(&marker_value).map_err(|e| e.to_string())?,
    )
    .map_err(|error| format!("无法保存 CATIA Python 安装校验信息：{error}"))?;
    Ok(python)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::time::{SystemTime, UNIX_EPOCH};
    use zip::write::SimpleFileOptions;

    fn scratch() -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("ai-modeler-catia-runtime-{stamp}"));
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn validates_and_extracts_only_the_pinned_com_wheel_on_demand() {
        let root = scratch();
        let app = root.join("app");
        let python = root.join("bundled-python");
        let bootstrap = root.join("catia-bootstrap");
        let data = root.join("user-data");
        fs::create_dir_all(app.join("packaging")).unwrap();
        fs::create_dir_all(&python).unwrap();
        fs::create_dir_all(&bootstrap).unwrap();
        fs::write(python.join("python.exe"), b"fixture-python").unwrap();
        fs::write(python.join("python313.zip"), b"stdlib").unwrap();
        fs::write(
            python.join("python313._pth"),
            b"python313.zip\r\n.\r\nLib\\site-packages\r\nimport site\r\n",
        )
        .unwrap();
        let requirements = b"pywin32==312\n";
        fs::write(
            app.join("packaging/desktop-catia-requirements-win-x64.txt"),
            requirements,
        )
        .unwrap();
        let mut wheel_bytes = Vec::new();
        {
            let cursor = Cursor::new(&mut wheel_bytes);
            let mut archive = zip::ZipWriter::new(cursor);
            archive
                .start_file("win32com/__init__.py", SimpleFileOptions::default())
                .unwrap();
            archive.write_all(b"# wheel fixture").unwrap();
            archive.finish().unwrap();
        }
        let wheel_name = "pywin32-312-cp313-cp313-win_amd64.whl";
        fs::write(bootstrap.join(wheel_name), &wheel_bytes).unwrap();
        let file_rows = ["python.exe", "python313.zip", "python313._pth"]
            .into_iter()
            .map(|name| {
                let bytes = fs::read(python.join(name)).unwrap();
                json!({"path":name,"size":bytes.len(),"sha256":sha256(&bytes)})
            })
            .collect::<Vec<_>>();
        let manifest = json!({
            "manifestVersion":1,"platform":"windows-x86_64","pythonVersion":"3.13.16",
            "pywin32Version":"312","pywin32Wheel":wheel_name,"pywin32WheelSha256":sha256(&wheel_bytes),
            "requirementsFile":"desktop-catia-requirements-win-x64.txt","requirementsSha256":sha256(requirements),
            "pythonFiles":file_rows
        });
        fs::write(
            bootstrap.join("catia-runtime-manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();

        let executable = prepare(&app, &python, &bootstrap, &data).unwrap();
        assert!(executable.is_file());
        assert!(
            data.join("runtime/catia-python/Lib/site-packages/win32com/__init__.py")
                .is_file()
        );
        assert!(!data.join("runtime/python/python.exe").exists());
        assert_eq!(
            prepare(&app, &python, &bootstrap, &data).unwrap(),
            executable
        );
        fs::write(bootstrap.join(wheel_name), b"tampered wheel").unwrap();
        assert!(prepare(&app, &python, &bootstrap, &data).is_err());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn refuses_tampered_python_runtime_bundle_or_wheel_before_materializing() {
        let root = scratch();
        let python = root.join("python");
        fs::create_dir_all(&python).unwrap();
        fs::write(python.join("python.exe"), b"bad").unwrap();
        let manifest = json!({"pythonFiles":[{"path":"python.exe","size":99,"sha256":"00"}]});
        assert!(verify_python_bundle(&python, &manifest).is_err());
        assert!(safe_relative("../outside").is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
