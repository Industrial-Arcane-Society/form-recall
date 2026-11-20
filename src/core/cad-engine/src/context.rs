use crate::native_bootstrap;
use std::path::PathBuf;

/// Paths are supplied by the application host. The engine owns no windows.
#[derive(Clone, Debug)]
pub struct EngineContext {
    pub resource_root: PathBuf,
    pub data_dir: PathBuf,
    pub config_path: PathBuf,
    pub bundle_root: PathBuf,
    pub packaged: bool,
}

impl EngineContext {
    pub fn from_environment() -> Result<Self, String> {
        let executable = std::env::current_exe().map_err(|error| error.to_string())?;
        let bundle_root = executable.parent().ok_or("程序目录不可用")?.to_path_buf();
        let explicit = std::env::var_os("AIMODELER_RESOURCE_ROOT")
            .or_else(|| std::env::var_os("AIMODELER_ROOT"))
            .map(PathBuf::from);
        let packaged_root = bundle_root.join("app");
        let development = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../legacy");
        let resource_root = native_bootstrap::resolve_resource_root(
            explicit.as_deref(),
            Some(&packaged_root),
            Some(&development),
        )?;
        let packaged = resource_root == packaged_root.canonicalize().unwrap_or(packaged_root);
        let data_dir = std::env::var_os("AIMODELER_DESKTOP_DATA_DIR")
            .map(PathBuf::from)
            .or_else(|| {
                std::env::var_os("LOCALAPPDATA")
                    .map(|value| PathBuf::from(value).join("com.aimodeler.desktop"))
            })
            .ok_or("无法定位用户数据目录。")?;
        if !data_dir.is_absolute() {
            return Err("用户数据目录必须为绝对路径。".into());
        }
        let config_path = std::env::var_os("AIMODELER_CONFIG_PATH")
            .map(PathBuf::from)
            .or_else(|| {
                std::env::var_os("AIMODELER_DATA_DIR")
                    .map(|value| PathBuf::from(value).join("config.json"))
            })
            .unwrap_or_else(|| {
                let development_config = development.join("config.json");
                if !packaged && development_config.is_file() {
                    development_config
                } else {
                    data_dir.join("config.json")
                }
            });
        Ok(Self {
            resource_root,
            data_dir,
            config_path,
            bundle_root,
            packaged,
        })
    }
}
