//! Native desktop settings and OpenAI-compatible provider management.
//!
//! The storage API is path based so all persistence behavior can be tested
//! against temporary directories without touching a user's configuration.

use reqwest::blocking::Client;
use reqwest::redirect::Policy;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use crate::EngineContext;

const STAGES: &[&str] = &["perception", "modeling", "review"];
const EFFORTS: &[&str] = &["none", "low", "high", "max"];
static NEXT_ID: AtomicU64 = AtomicU64::new(1);
static SETTINGS_LOCK: Mutex<()> = Mutex::new(());

fn defaults() -> Value {
    json!({
        "llm": {
            "enabled": false,
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key": "",
            "reasoning_effort": "none",
            "providers": [],
            "active_provider_id": "",
            "agent_stage_overrides": {}
        },
        "viewer": {"edges":true,"grid":true,"axes":true,"perspective":true,"max_faces":6000},
        "default_tessellation": {"cylinder_segments":48,"sphere_segments":32},
        "build": {"time_budget":30.0,"max_faces":60000},
        "online": {"enabled":true,"timeout":8},
        "last_export_dir": ""
    })
}

fn merge_objects(base: &mut Value, overlay: &Value) {
    let (Some(base), Some(overlay)) = (base.as_object_mut(), overlay.as_object()) else {
        *base = overlay.clone();
        return;
    };
    for (key, value) in overlay {
        match (base.get_mut(key), value) {
            (Some(current), Value::Object(_)) if current.is_object() => {
                merge_objects(current, value)
            }
            _ => {
                base.insert(key.clone(), value.clone());
            }
        }
    }
}

fn string_at<'a>(value: &'a Value, key: &str) -> &'a str {
    value.get(key).and_then(Value::as_str).unwrap_or("").trim()
}

fn host_label(base_url: &str) -> String {
    let host = reqwest::Url::parse(base_url)
        .ok()
        .and_then(|url| url.host_str().map(str::to_ascii_lowercase));
    let Some(host) = host else {
        return "自定义".into();
    };
    if matches!(host.as_str(), "127.0.0.1" | "localhost" | "::1") {
        return "本地 llama.cpp".into();
    }
    for (marker, label) in [
        ("siliconflow", "SiliconFlow"),
        ("deepseek", "DeepSeek 官方 API"),
        ("openai", "OpenAI"),
        ("moonshot", "Moonshot"),
        ("dashscope", "阿里云百炼"),
        ("bigmodel", "智谱 GLM"),
    ] {
        if host.contains(marker) {
            return label.into();
        }
    }
    host
}

fn stable_migrated_id(llm: &Value) -> String {
    let seed = json!({
        "base_url":llm.get("base_url"), "model":llm.get("model"),
        "reasoning_effort":llm.get("reasoning_effort")
    });
    let digest = Sha256::digest(serde_json::to_vec(&seed).unwrap_or_default());
    format!(
        "legacy-{}",
        digest[..8]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    )
}

fn generated_id() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let count = NEXT_ID.fetch_add(1, Ordering::Relaxed);
    format!("p{now:x}{count:x}")
}

fn migrate_flat_profile(llm: &Value) -> Value {
    let mut profile = Map::new();
    profile.insert("id".into(), json!(stable_migrated_id(llm)));
    profile.insert("name".into(), json!(host_label(string_at(llm, "base_url"))));
    for key in ["base_url", "model", "api_key"] {
        profile.insert(key.into(), json!(string_at(llm, key)));
    }
    let effort = string_at(llm, "reasoning_effort").to_ascii_lowercase();
    profile.insert(
        "reasoning_effort".into(),
        json!(if EFFORTS.contains(&effort.as_str()) {
            effort.as_str()
        } else {
            "none"
        }),
    );
    for key in [
        "thinking_budget",
        "dsh_context_window",
        "dsh_output_cap",
        "max_tokens",
    ] {
        if let Some(value) = llm.get(key) {
            profile.insert(key.into(), value.clone());
        }
    }
    Value::Object(profile)
}

fn normalize_profile(profile: &mut Value) -> Result<(), String> {
    let object = profile.as_object_mut().ok_or("供应商档案必须是对象。")?;
    let id = object
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    if id.is_empty() {
        return Err("供应商档案缺少有效 ID。".into());
    }
    object.insert("id".into(), json!(id));
    for key in ["name", "base_url", "model", "api_key"] {
        let value = object
            .get(key)
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_owned();
        object.insert(key.into(), json!(value));
    }
    let effort = object
        .get("reasoning_effort")
        .and_then(Value::as_str)
        .unwrap_or("none")
        .trim()
        .to_ascii_lowercase();
    object.insert(
        "reasoning_effort".into(),
        json!(if EFFORTS.contains(&effort.as_str()) {
            effort.as_str()
        } else {
            "none"
        }),
    );
    Ok(())
}

fn normalize_settings(mut settings: Value) -> Result<Value, String> {
    if !settings.is_object() {
        return Err("设置根节点必须是 JSON 对象。".into());
    }
    let mut merged = defaults();
    merge_objects(&mut merged, &settings);
    settings = merged;
    let llm = settings
        .get_mut("llm")
        .and_then(Value::as_object_mut)
        .ok_or("模型设置 llm 必须是对象。")?;

    if llm.get("providers").is_some_and(|value| !value.is_array()) {
        return Err("供应商档案列表必须是数组；原设置未更改。".into());
    }
    let mut profiles = llm
        .get("providers")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if profiles.is_empty() {
        profiles.push(migrate_flat_profile(&Value::Object(llm.clone())));
    }
    let mut ids = BTreeSet::new();
    for profile in &mut profiles {
        normalize_profile(profile)?;
        let id = string_at(profile, "id").to_owned();
        if !ids.insert(id.clone()) {
            return Err("供应商档案 ID 必须唯一。".into());
        }
    }
    let llm_snapshot = Value::Object(llm.clone());
    let active = string_at(&llm_snapshot, "active_provider_id");
    let active = if ids.contains(active) {
        active.to_owned()
    } else {
        profiles
            .first()
            .map(|row| string_at(row, "id").to_owned())
            .unwrap_or_default()
    };

    let overrides = llm
        .entry("agent_stage_overrides")
        .or_insert_with(|| json!({}));
    if !overrides.is_object() {
        return Err("Agent 阶段覆盖必须是对象。".into());
    }
    for stage in STAGES {
        if overrides.get(*stage).is_none() {
            overrides[*stage] = json!({"provider_id":"","reasoning_effort":""});
        }
        if !overrides[*stage].is_object() {
            return Err(format!("{stage} 阶段覆盖必须是对象。"));
        }
    }

    llm.insert("providers".into(), Value::Array(profiles.clone()));
    llm.insert("active_provider_id".into(), json!(active));
    let selected = profiles
        .iter()
        .find(|row| string_at(row, "id") == active)
        .or_else(|| profiles.first());
    if let Some(selected) = selected {
        for key in ["base_url", "model", "api_key", "reasoning_effort"] {
            llm.insert(
                key.into(),
                selected.get(key).cloned().unwrap_or_else(|| json!("")),
            );
        }
        for key in ["thinking_budget", "dsh_context_window", "dsh_output_cap"] {
            match selected.get(key) {
                Some(value) => {
                    llm.insert(key.into(), value.clone());
                }
                None => {
                    llm.remove(key);
                }
            }
        }
    }
    llm.remove("thinking");
    Ok(settings)
}

fn read_existing(path: &Path) -> Result<Option<Value>, String> {
    match fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(|_| "现有设置文件不是有效 JSON；为保护原文件，已拒绝覆盖。".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err("无法读取现有设置文件；为保护原文件，已取消操作。".into()),
    }
}

pub fn load_settings(path: &Path) -> Result<Value, String> {
    let _guard = SETTINGS_LOCK.lock().map_err(|_| "设置存储锁已损坏。")?;
    let existing = read_existing(path)?.unwrap_or_else(defaults);
    normalize_settings(existing)
}

fn atomic_write(path: &Path, contents: &[u8]) -> Result<(), String> {
    let parent = path.parent().ok_or("设置路径缺少父目录。")?;
    fs::create_dir_all(parent).map_err(|_| "无法创建设置目录。")?;
    let temp_path = PathBuf::from(format!("{}.tmp-{}", path.display(), generated_id()));
    let result = (|| -> Result<(), String> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp_path)
            .map_err(|_| "无法创建临时设置文件。")?;
        file.write_all(contents)
            .map_err(|_| "无法写入临时设置文件。")?;
        file.sync_all().map_err(|_| "无法同步临时设置文件。")?;
        drop(file);

        if path.exists() {
            let backup = PathBuf::from(format!("{}.bak-{}", path.display(), generated_id()));
            fs::rename(path, &backup).map_err(|_| "无法安全替换设置文件；原设置已保留。")?;
            match fs::rename(&temp_path, path) {
                Ok(()) => {
                    let _ = fs::remove_file(backup);
                }
                Err(_) => {
                    return match fs::rename(&backup, path) {
                        Ok(()) => Err("无法安装新的设置文件；原设置已恢复。".into()),
                        Err(_) => {
                            Err("无法安装新的设置文件；原设置保留在同目录备份文件中。".into())
                        }
                    };
                }
            }
        } else {
            fs::rename(&temp_path, path).map_err(|_| "无法安装新的设置文件。")?;
        }
        if let Ok(directory) = File::open(parent) {
            let _ = directory.sync_all();
        }
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp_path);
    }
    result
}

pub fn save_settings(path: &Path, incoming: Value) -> Result<Value, String> {
    let _guard = SETTINGS_LOCK.lock().map_err(|_| "设置存储锁已损坏。")?;
    if !incoming.is_object() {
        return Err("设置根节点必须是 JSON 对象；原设置未更改。".into());
    }
    let existing = read_existing(path)?.unwrap_or_else(defaults);
    let mut merged = existing;
    merge_objects(&mut merged, &incoming);
    let normalized = normalize_settings(merged)?;
    let bytes =
        serde_json::to_vec_pretty(&normalized).map_err(|_| "无法序列化设置；原设置未更改。")?;
    atomic_write(path, &bytes)?;
    Ok(normalized)
}

#[derive(Clone, Copy)]
struct Template {
    id: &'static str,
    label: &'static str,
    note: &'static str,
    base_url: &'static str,
    model: &'static str,
    api_key: &'static str,
    effort: &'static str,
}
const TEMPLATES: &[Template] = &[
    Template {
        id: "siliconflow",
        label: "SiliconFlow",
        note: "Kimi-K2.7-Code 等 OpenAI 兼容模型",
        base_url: "https://api.siliconflow.cn/v1",
        model: "moonshotai/Kimi-K2.7-Code",
        api_key: "",
        effort: "none",
    },
    Template {
        id: "deepseek",
        label: "DeepSeek 官方 API",
        note: "按建模与审查阶段设置推理强度",
        base_url: "https://api.deepseek.com/v1",
        model: "deepseek-flash",
        api_key: "",
        effort: "low",
    },
    Template {
        id: "openai",
        label: "OpenAI",
        note: "OpenAI 兼容模型接口",
        base_url: "https://api.openai.com/v1",
        model: "gpt-4o-mini",
        api_key: "",
        effort: "none",
    },
    Template {
        id: "local",
        label: "本地 llama.cpp",
        note: "本地兼容服务",
        base_url: "http://127.0.0.1:8090/v1",
        model: "deepseek",
        api_key: "sk-local",
        effort: "none",
    },
    Template {
        id: "custom",
        label: "自定义（OpenAI 兼容）",
        note: "填写兼容接口地址和模型",
        base_url: "",
        model: "",
        api_key: "",
        effort: "none",
    },
];

pub fn provider_templates() -> Vec<Value> {
    TEMPLATES
        .iter()
        .map(|row| json!({"id":row.id,"label":format!("{} — {}", row.label, row.note),"name":row.label,"note":row.note}))
        .collect()
}

pub fn provider_template(id: &str) -> Value {
    let row = TEMPLATES
        .iter()
        .find(|row| row.id == id)
        .unwrap_or_else(|| {
            TEMPLATES
                .iter()
                .find(|row| row.id == "custom")
                .expect("custom template")
        });
    json!({"id":generated_id(),"name":row.label,"base_url":row.base_url,"model":row.model,
        "api_key":row.api_key,"reasoning_effort":row.effort})
}

pub fn fetch_models(base_url: &str, api_key: &str) -> Result<Vec<String>, String> {
    let base = base_url.trim().trim_end_matches('/');
    if base.is_empty() {
        return Err("接口地址为空，请先填写。".into());
    }
    let mut url = reqwest::Url::parse(base).map_err(|_| "接口地址无效。")?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || url.username() != ""
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("接口地址必须是没有凭据或查询参数的 HTTP(S) URL。".into());
    }
    let path = format!("{}/models", url.path().trim_end_matches('/'));
    url.set_path(&path);
    let client = Client::builder()
        .timeout(Duration::from_secs(15))
        .redirect(Policy::none())
        .build()
        .map_err(|_| "无法初始化模型列表请求。")?;
    let mut request = client
        .get(url)
        .header(reqwest::header::ACCEPT, "application/json");
    if !api_key.trim().is_empty() {
        request = request.bearer_auth(api_key.trim());
    }
    let response = request.send().map_err(|_| "无法连接模型接口。")?;
    if !response.status().is_success() {
        return Err(format!(
            "模型接口请求失败（HTTP {}）。",
            response.status().as_u16()
        ));
    }
    let mut body_bytes = Vec::new();
    response
        .take(2 * 1024 * 1024 + 1)
        .read_to_end(&mut body_bytes)
        .map_err(|_| "无法读取模型接口响应。")?;
    if body_bytes.len() > 2 * 1024 * 1024 {
        return Err("模型接口响应超过 2 MiB 限制。".into());
    }
    let body: Value =
        serde_json::from_slice(&body_bytes).map_err(|_| "模型接口返回的不是有效 JSON。")?;
    let mut ids = BTreeSet::new();
    if let Some(items) = body
        .get("data")
        .and_then(Value::as_array)
        .or_else(|| body.get("models").and_then(Value::as_array))
    {
        for item in items {
            let id = item
                .as_str()
                .or_else(|| item.get("id").and_then(Value::as_str))
                .unwrap_or("")
                .trim();
            if !id.is_empty() {
                ids.insert(id.to_owned());
            }
        }
    }
    if ids.is_empty() {
        return Err("响应里没有模型列表（/models 格式不兼容）。".into());
    }
    Ok(ids.into_iter().collect())
}

fn app_config_path(app: &EngineContext) -> Result<PathBuf, String> { Ok(app.config_path.clone()) }

pub fn native_settings_load(app: EngineContext) -> Result<Value, String> {
    load_settings(&app_config_path(&app)?)
}

pub fn native_settings_save(app: EngineContext, settings: Value) -> Result<Value, String> {
    save_settings(&app_config_path(&app)?, settings)
}

pub fn native_provider_templates() -> Vec<Value> {
    provider_templates()
}

pub fn native_provider_template(id: String) -> Value {
    provider_template(&id)
}

pub fn native_provider_fetch_models(base_url: String, api_key: String) -> Result<Value, String> {
    fetch_models(&base_url, &api_key).map(|models| json!({"models":models}))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    fn temp_path(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "ai-modeler-settings-test-{}-{}",
            generated_id(),
            name
        ));
        fs::create_dir_all(&root).unwrap();
        root.join("config.json")
    }

    #[test]
    fn empty_config_loads_defaults_with_a_stable_migrated_profile() {
        let path = temp_path("empty");
        let first = load_settings(&path).unwrap();
        let second = load_settings(&path).unwrap();
        assert_eq!(
            first["llm"]["providers"][0]["id"],
            second["llm"]["providers"][0]["id"]
        );
        assert_eq!(first["llm"]["model"], "gpt-4o-mini");
        assert_eq!(first["viewer"]["max_faces"], 6000);
        let _ = fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn migrates_flat_provider_preserves_unknown_fields_and_round_trips_overrides() {
        let path = temp_path("migrate");
        let input = json!({"unknown_root":{"keep":true},"llm":{"base_url":"https://example.test/v1","model":"m1","api_key":"fixture-key","reasoning_effort":"high","vendor_extra":{"x":2},"thinking_budget":4096}});
        let saved = save_settings(&path, input).unwrap();
        assert_eq!(saved["unknown_root"]["keep"], true);
        assert_eq!(saved["llm"]["vendor_extra"]["x"], 2);
        assert_eq!(saved["llm"]["providers"][0]["api_key"], "fixture-key");
        let migrated_id = saved["llm"]["providers"][0]["id"].clone();
        assert_eq!(saved["llm"]["active_provider_id"], migrated_id);

        let mut changed = saved.clone();
        changed["llm"]["providers"].as_array_mut().unwrap().push(json!({"id":"p2","name":"second","base_url":"https://two.test/v1","model":"m2","api_key":"fixture-two","profile_unknown":9}));
        changed["llm"]["active_provider_id"] = json!("p2");
        changed["llm"]["agent_stage_overrides"]["modeling"] =
            json!({"provider_id":"p2","reasoning_effort":"max","custom_stage_field":true});
        let saved_again = save_settings(&path, changed).unwrap();
        let loaded = load_settings(&path).unwrap();
        assert_eq!(loaded["llm"]["providers"].as_array().unwrap().len(), 2);
        assert_eq!(loaded["llm"]["base_url"], "https://two.test/v1");
        assert_eq!(loaded["llm"]["providers"][1]["profile_unknown"], 9);
        assert_eq!(
            loaded["llm"]["agent_stage_overrides"]["modeling"]["provider_id"],
            "p2"
        );
        assert_eq!(
            loaded["llm"]["agent_stage_overrides"]["modeling"]["custom_stage_field"],
            true
        );
        assert_eq!(
            saved_again["llm"]["agent_stage_overrides"]["modeling"]["reasoning_effort"],
            "max"
        );
        let _ = fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn failed_save_does_not_overwrite_existing_bytes() {
        let path = temp_path("failure");
        fs::write(&path, b"{broken-config").unwrap();
        let before = fs::read(&path).unwrap();
        assert!(save_settings(&path, json!({"llm":{"model":"changed"}})).is_err());
        assert_eq!(fs::read(&path).unwrap(), before);
        let _ = fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn invalid_profile_update_does_not_replace_a_valid_config() {
        let path = temp_path("invalid-update");
        let original = json!({"unknown":{"retain":"value"},"llm":{"base_url":"https://original.test/v1","model":"original-model","api_key":"fixture-key"}});
        save_settings(&path, original).unwrap();
        let before = fs::read(&path).unwrap();
        let invalid = json!({"llm":{"providers":[
            {"id":"duplicate","name":"one","base_url":"https://one.test/v1","model":"m1","api_key":""},
            {"id":"duplicate","name":"two","base_url":"https://two.test/v1","model":"m2","api_key":""}
        ]}});
        assert!(save_settings(&path, invalid).is_err());
        assert_eq!(fs::read(&path).unwrap(), before);
        let _ = fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn provider_template_listing_and_unknown_template_are_safe() {
        let rows = provider_templates();
        assert!(rows.iter().any(|row| row["id"] == "deepseek"));
        let custom = provider_template("unknown-template");
        assert_eq!(custom["base_url"], "");
        assert!(!custom["id"].as_str().unwrap().is_empty());
    }

    fn fixture_server(status: &str, body: &'static str) -> (String, thread::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let status = status.to_owned();
        let worker = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0u8; 4096];
            let count = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..count]).to_string();
            let response = format!(
                "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            stream.write_all(response.as_bytes()).unwrap();
            request
        });
        (format!("http://{address}/v1"), worker)
    }

    #[test]
    fn fetch_models_uses_bearer_fixture_key_and_parses_openai_format() {
        let (base, worker) = fixture_server(
            "200 OK",
            r#"{"data":[{"id":"z-model"},{"id":"a-model"},{"id":"a-model"}]}"#,
        );
        let models = fetch_models(&base, "fixture-only-key").unwrap();
        let request = worker.join().unwrap();
        assert_eq!(models, vec!["a-model", "z-model"]);
        assert!(request.starts_with("GET /v1/models "));
        assert!(
            request
                .to_ascii_lowercase()
                .contains("authorization: bearer fixture-only-key")
        );
    }

    #[test]
    fn fetch_models_failure_does_not_expose_response_body_or_fixture_key() {
        let (base, worker) = fixture_server(
            "401 Unauthorized",
            r#"{"error":"fixture-only-key rejected"}"#,
        );
        let error = fetch_models(&base, "fixture-only-key").unwrap_err();
        let _request = worker.join().unwrap();
        assert!(error.contains("HTTP 401"));
        assert!(!error.contains("fixture-only-key"));
    }
}
