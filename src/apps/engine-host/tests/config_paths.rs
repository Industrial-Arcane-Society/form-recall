use serde_json::Value;
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn handshake_config(
    data: &Path,
    explicit: Option<&Path>,
    config_environment: Option<&Path>,
    legacy_data: Option<&Path>,
) -> PathBuf {
    let resources = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../legacy")
        .canonicalize()
        .unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_formrecall-engine-host"));
    command
        .arg("--resources")
        .arg(resources)
        .arg("--data")
        .arg(data)
        .env_remove("AIMODELER_CONFIG_PATH")
        .env_remove("AIMODELER_DATA_DIR")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(path) = explicit {
        command.arg("--config").arg(path);
    }
    if let Some(path) = config_environment {
        command.env("AIMODELER_CONFIG_PATH", path);
    }
    if let Some(path) = legacy_data {
        command.env("AIMODELER_DATA_DIR", path);
    }
    let mut child = command.spawn().unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(b"{\"id\":1,\"method\":\"handshake\",\"params\":{}}\n{\"id\":2,\"method\":\"shutdown\",\"params\":{}}\n")
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            panic!("configuration handshake did not finish");
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    let output = child.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "host failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let reply = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .find(|reply| reply["id"] == 1)
        .expect("handshake reply");
    assert!(reply.get("error").is_none(), "{reply}");
    PathBuf::from(reply["result"]["config_path"].as_str().unwrap())
}

#[test]
fn configuration_precedence_survives_explicit_resource_and_data_arguments() {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let scratch = Scratch(
        std::env::temp_dir().join(format!("formrecall-config-{}-{nonce}", std::process::id())),
    );
    fs::create_dir_all(&scratch.0).unwrap();
    let data = scratch.0.join("desktop-data");
    let legacy = scratch.0.join("legacy-data");
    let explicit = scratch.0.join("explicit.json");
    let environment = scratch.0.join("environment.json");
    assert_eq!(
        handshake_config(&data, None, None, None),
        data.join("config.json")
    );
    assert_eq!(
        handshake_config(&data, None, None, Some(&legacy)),
        legacy.join("config.json")
    );
    assert_eq!(
        handshake_config(&data, None, Some(&environment), Some(&legacy)),
        environment
    );
    assert_eq!(
        handshake_config(&data, Some(&explicit), Some(&environment), Some(&legacy)),
        explicit
    );
}
