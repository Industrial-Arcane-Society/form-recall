use formrecall_cad_engine::{
    EngineContext,
    agent_host::{AgentHostState, EventSink},
    rpc,
};
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    io::{self, BufRead, Read, Write},
    path::PathBuf,
    sync::{Arc, Mutex},
    thread,
};

fn write(output: &Mutex<io::Stdout>, value: Value) {
    if let Ok(mut stream) = output.lock() {
        let _ = serde_json::to_writer(&mut *stream, &value);
        let _ = stream.write_all(b"\n");
        let _ = stream.flush();
    }
}

fn context() -> Result<EngineContext, String> {
    let mut arguments = std::env::args_os().skip(1);
    let mut resources = None;
    let mut data = None;
    let mut config = None;
    while let Some(option) = arguments.next() {
        let target = match option.to_str() {
            Some("--resources") => &mut resources,
            Some("--data") => &mut data,
            Some("--config") => &mut config,
            _ => {
                return Err(
                    "用法 formrecall-engine-host --resources PATH --data PATH [--config PATH]"
                        .into(),
                );
            }
        };
        *target = Some(PathBuf::from(arguments.next().ok_or("命令行参数缺少路径")?));
    }
    if resources.is_none() && data.is_none() && config.is_none() {
        return EngineContext::from_environment();
    }
    let resource_root = formrecall_cad_engine::native_bootstrap::validate_resource_root(
        &resources.ok_or("缺少 --resources")?,
    )?;
    let data_dir = data.ok_or("缺少 --data")?;
    if !data_dir.is_absolute() {
        return Err("--data 必须为绝对路径".into());
    }
    let bundle_root = std::env::current_exe()
        .map_err(|e| e.to_string())?
        .parent()
        .ok_or("程序目录无效")?
        .to_path_buf();
    let packaged = bundle_root.join("app").canonicalize().ok().as_ref() == Some(&resource_root);
    let config_path = config
        .or_else(|| std::env::var_os("AIMODELER_CONFIG_PATH").map(PathBuf::from))
        .or_else(|| {
            std::env::var_os("AIMODELER_DATA_DIR")
                .map(|directory| PathBuf::from(directory).join("config.json"))
        })
        .unwrap_or_else(|| data_dir.join("config.json"));
    Ok(EngineContext {
        resource_root,
        data_dir,
        config_path,
        bundle_root,
        packaged,
    })
}

fn main() {
    if let Err(error) = serve() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

fn serve() -> Result<(), String> {
    let context = context()?;
    let runtime = Arc::new(
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .map_err(|e| e.to_string())?,
    );
    let state = AgentHostState::default();
    let output = Arc::new(Mutex::new(io::stdout()));
    let writes = Arc::new(Mutex::new(()));
    let inflight = Arc::new(Mutex::new(HashSet::new()));
    let mut workers = Vec::new();
    let mut input = io::stdin().lock();
    loop {
        let mut bytes = Vec::new();
        // A 24 MiB image represented as a JSON byte array can occupy 96 MiB.
        // Limit allocation while reading, then drain an oversized frame so the
        // next request remains aligned to the JSONL boundary.
        const MAX_REQUEST_BYTES: usize = 128 * 1024 * 1024;
        let count = input
            .by_ref()
            .take((MAX_REQUEST_BYTES + 1) as u64)
            .read_until(b'\n', &mut bytes)
            .map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        if bytes.len() > MAX_REQUEST_BYTES {
            if bytes.last() != Some(&b'\n') {
                input.skip_until(b'\n').map_err(|e| e.to_string())?;
            }
            write(&output, json!({"id":null,"error":"请求超过 128 MiB"}));
            continue;
        }
        let request: Value = match serde_json::from_slice(&bytes) {
            Ok(value) => value,
            Err(error) => {
                write(
                    &output,
                    json!({"id":null,"error":format!("无效 JSON 请求 {error}")}),
                );
                continue;
            }
        };
        let Some(id) = request["id"].as_u64() else {
            write(&output, json!({"id":null,"error":"id 必须是 uint64"}));
            continue;
        };
        let Some(method) = request["method"].as_str().map(str::to_owned) else {
            write(&output, json!({"id":id,"error":"缺少 method"}));
            continue;
        };
        if method == "shutdown" {
            formrecall_cad_engine::agent_host::shutdown(&state);
            write(&output, json!({"id":id,"result":{"closed":true}}));
            break;
        }
        let params = request.get("params").cloned().unwrap_or_else(|| json!({}));
        if !params.is_object() {
            write(&output, json!({"id":id,"error":"params 必须是对象"}));
            continue;
        }
        {
            let mut active = inflight.lock().map_err(|_| "请求锁损坏")?;
            if active.contains(&id) {
                write(&output, json!({"id":id,"error":"请求 id 重复"}));
                continue;
            }
            // Cancellation retains capacity while long tasks are running.
            if active.len() >= 32 && method != "agent_cancel" {
                write(&output, json!({"id":id,"error":"正在处理的请求过多"}));
                continue;
            }
            active.insert(id);
        }
        let (context, state, output, writes, inflight, runtime) = (
            context.clone(),
            state.clone(),
            output.clone(),
            writes.clone(),
            inflight.clone(),
            runtime.clone(),
        );
        workers.retain(|worker: &thread::JoinHandle<()>| !worker.is_finished());
        let worker = thread::Builder::new()
            .name(format!("engine-request-{id}"))
            .stack_size(16 * 1024 * 1024)
            .spawn(move || {
                let event_output = output.clone();
                let events = EventSink::new(move |data| {
                    write(
                        &event_output,
                        json!({"event":"agent","request_id":id,"data":data}),
                    )
                });
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    let _write_guard = if rpc::mutates_document(&method) {
                        Some(
                            writes
                                .try_lock()
                                .map_err(|_| "模型正在更新，请等待当前操作完成。")?,
                        )
                    } else {
                        None
                    };
                    runtime.block_on(rpc::dispatch(context, &state, &method, params, events))
                }))
                .unwrap_or_else(|_| Err("核心命令异常终止".into()));
                let reply = match result {
                    Ok(result) => json!({"id":id,"result":result}),
                    Err(error) => json!({"id":id,"error":error}),
                };
                write(&output, reply);
                if let Ok(mut active) = inflight.lock() {
                    active.remove(&id);
                }
            })
            .map_err(|e| e.to_string())?;
        workers.push(worker);
    }
    formrecall_cad_engine::agent_host::shutdown(&state);
    for worker in workers {
        let _ = worker.join();
    }
    Ok(())
}
