//! Native multimodal provider requests for the DSH CAD tools.
//!
//! This module does not resolve reference paths or decide whether a preview is
//! current. The native tool domain must supply verified image bytes and a
//! system-authored prompt. This layer turns those messages into an
//! OpenAI-compatible Chat Completions or Responses request and records only
//! bounded, non-secret transport telemetry.

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use reqwest::{Client, Url};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::fmt;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const MAX_IMAGE_BYTES: usize = 20 * 1024 * 1024;
const MAX_REQUEST_BYTES: usize = 64 * 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
const MAX_TIMEOUT: Duration = Duration::from_secs(150);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const USER_AGENT: &str = "AI-Modeler/0.2 native-vision";

static REQUEST_SEQUENCE: AtomicU64 = AtomicU64::new(1);

#[derive(Clone)]
pub struct ProviderConfig {
    pub base_url: String,
    pub model: String,
    pub api_key: String,
    pub effort: String,
    pub thinking_budget: Option<u64>,
    pub max_output_tokens: u64,
}

impl fmt::Debug for ProviderConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ProviderConfig")
            .field("base_url", &self.base_url)
            .field("model", &self.model)
            .field("api_key", &"[redacted]")
            .field("effort", &self.effort)
            .field("thinking_budget", &self.thinking_budget)
            .field("max_output_tokens", &self.max_output_tokens)
            .finish()
    }
}

#[derive(Clone, Debug)]
pub struct VerifiedImage {
    pub bytes: Vec<u8>,
    pub mime: String,
    pub reference_id: Option<String>,
    /// SHA-256 of the immutable original reference, if this is a reference.
    /// It differs from the transmitted hash for a cropped ROI.
    pub source_sha256: Option<String>,
    /// Expected SHA-256 of the exact bytes in `bytes`.
    pub transport_sha256: String,
    pub roi_norm: Option<Value>,
    /// Host-verified preview identity, when this image is a local model render.
    pub preview: Option<Value>,
}

#[derive(Clone, Debug)]
pub enum VisionPart {
    Text(String),
    Image(usize),
}

#[derive(Clone, Debug)]
pub struct VisionMessage {
    pub role: String,
    pub content: Vec<VisionPart>,
}

#[derive(Clone, Debug)]
pub struct RequestContext {
    pub task: String,
    pub turn_id: Option<String>,
    pub telemetry_path: Option<PathBuf>,
    pub review_complexity: Option<String>,
    pub timeout: Duration,
}

#[derive(Clone, Debug)]
pub struct VisionResult {
    /// Assistant's visible text only. Reasoning content is never returned.
    pub text: String,
    pub parsed_json: Value,
    pub usage: Option<Value>,
    pub profile: Value,
    pub protocol: String,
    pub response_id: Option<String>,
    pub finish_reason: String,
    pub request_id: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VisionError {
    pub kind: &'static str,
    pub message: String,
    /// Narrow, sanitized fields from a provider's structured `error` object.
    /// Never contains the raw response body or model output/reasoning.
    pub provider_error: Option<ProviderErrorDetails>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProviderErrorDetails {
    pub response_status: Option<String>,
    pub code: Option<String>,
    pub error_type: Option<String>,
    pub message: Option<String>,
}

impl VisionError {
    fn new(kind: &'static str, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
            provider_error: None,
        }
    }

    fn from_provider_error(details: ProviderErrorDetails) -> Self {
        let mut parts = Vec::new();
        if let Some(error_type) = details.error_type.as_deref() {
            parts.push(format!("type={error_type}"));
        }
        if let Some(code) = details.code.as_deref() {
            parts.push(format!("code={code}"));
        }
        let summary = if parts.is_empty() {
            "Responses API 返回生成失败状态".to_owned()
        } else {
            format!("Responses API 返回生成失败状态（{}）", parts.join(", "))
        };
        let message = match details.message.as_deref() {
            Some(provider_message) if !provider_message.is_empty() => {
                format!("{summary}：{provider_message}")
            }
            _ if parts.is_empty() => {
                format!("{summary}，服务未返回可用的 error.type、error.code 或 error.message。")
            }
            _ => summary,
        };
        Self {
            kind: "provider_error",
            message,
            provider_error: Some(details),
        }
    }
}

impl fmt::Display for VisionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.kind, self.message)
    }
}

impl std::error::Error for VisionError {}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Protocol {
    ChatCompletions,
    Responses,
}

impl Protocol {
    fn name(self) -> &'static str {
        match self {
            Self::ChatCompletions => "chat_completions",
            Self::Responses => "responses",
        }
    }

    fn endpoint(self) -> &'static str {
        match self {
            Self::ChatCompletions => "chat/completions",
            Self::Responses => "responses",
        }
    }
}

struct Route {
    endpoint: Url,
    protocol: Protocol,
    host: String,
    vision_supported: Option<bool>,
    reasoning_control: &'static str,
    supported_efforts: Vec<&'static str>,
    capability_source: Option<&'static str>,
}

#[derive(Clone)]
struct WireImage {
    image_index: usize,
    transport_sha256: String,
    byte_length: usize,
    source_sha256: Option<String>,
    reference_id: Option<String>,
    roi_norm: Option<Value>,
    preview: Option<Value>,
}

pub struct NativeVisionProvider {
    config: ProviderConfig,
    client: Client,
    route: Route,
}

impl NativeVisionProvider {
    pub fn new(config: ProviderConfig) -> Result<Self, VisionError> {
        if config.model.trim().is_empty() {
            return Err(VisionError::new("invalid_config", "模型名称不能为空。"));
        }
        if config.max_output_tokens == 0 {
            return Err(VisionError::new(
                "invalid_config",
                "max_output_tokens 必须是正整数。",
            ));
        }
        let route = resolve_route(&config.base_url, &config.model)?;
        let client = Client::builder()
            .connect_timeout(CONNECT_TIMEOUT)
            .user_agent(USER_AGENT)
            .build()
            .map_err(|_| VisionError::new("client_error", "无法初始化视觉请求客户端。"))?;
        Ok(Self {
            config,
            client,
            route,
        })
    }

    /// Send one independent inventory or similarity-review request.
    ///
    /// The request is asynchronous and bounded by `context.timeout` (at most
    /// 150s). Cancellation is polled while waiting for response headers and
    /// each response body chunk, so setting the flag drops the active reqwest
    /// future without waiting for the provider timeout.
    pub async fn call(
        &self,
        messages: &[VisionMessage],
        images: &[VerifiedImage],
        context: &RequestContext,
        cancelled: &AtomicBool,
    ) -> Result<VisionResult, VisionError> {
        if cancelled.load(Ordering::Acquire) {
            return Err(VisionError::new("cancelled", "视觉请求已取消。"));
        }
        if messages.is_empty() {
            return Err(VisionError::new(
                "invalid_request",
                "视觉请求缺少 messages。",
            ));
        }
        if images.is_empty() {
            return Err(VisionError::new(
                "invalid_request",
                "视觉请求必须附带已核验图片。",
            ));
        }
        if self.route.vision_supported == Some(false) {
            return Err(VisionError::new(
                "vision_unsupported",
                "当前已知模型配置不支持图像输入。",
            ));
        }
        let (wire_messages, wire_images) = self.build_messages(messages, images)?;
        if wire_images.is_empty() {
            return Err(VisionError::new(
                "invalid_request",
                "messages 中没有实际引用任何图片。",
            ));
        }
        let profile = self.profile(context);
        let payload = self.payload(wire_messages, &profile);
        let body = serde_json::to_vec(&payload)
            .map_err(|_| VisionError::new("invalid_request", "无法序列化视觉请求。"))?;
        if body.len() > MAX_REQUEST_BYTES {
            return Err(VisionError::new(
                "request_too_large",
                "视觉请求超过 64 MiB。",
            ));
        }
        let timeout = context.timeout.min(MAX_TIMEOUT);
        if timeout.is_zero() {
            return Err(VisionError::new("timeout", "视觉请求时间预算已耗尽。"));
        }
        let request_id = next_request_id();
        let started = Instant::now();
        self.write_telemetry(
            context,
            json!({
                "kind":"provider_wire_request",
                "request_id":request_id,
                "wire_observed":true,
                "provider_host":self.route.host,
                "protocol_path":self.route.endpoint.path(),
                "protocol":self.route.protocol.name(),
                "model":self.config.model,
                "task":context.task,
                "request_fields":{
                    "reasoning_effort":profile["reasoning_effort"],
                    "enable_thinking":profile["enable_thinking"],
                    "thinking_budget":profile["thinking_budget"],
                    "thinking_budget_source":profile["thinking_budget_source"],
                    "thinking_budget_hard_limit_known":profile["thinking_budget_hard_limit_known"],
                    "output_token_fields":profile["output_token_fields"],
                    "response_format":profile["response_format"],
                    "stream":false,
                },
                "image_count":wire_images.len(),
                "images":wire_images.iter().map(WireImage::to_value).collect::<Vec<_>>(),
                "capture_layer":"rust_native_vision",
            }),
        );

        let request = self
            .client
            .post(self.route.endpoint.clone())
            .timeout(timeout)
            .header(reqwest::header::ACCEPT, "application/json")
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .bearer_auth(self.config.api_key.as_str())
            .body(body)
            .send();
        let response = tokio::select! {
            result = request => result,
            _ = wait_for_cancel(cancelled) => {
                self.write_error(context, &request_id, started, "cancelled", None);
                return Err(VisionError::new("cancelled", "视觉请求已取消。"));
            }
        };

        let response = match response {
            Ok(response) => response,
            Err(error) => {
                let kind = if error.is_timeout() {
                    "timeout"
                } else {
                    "network_error"
                };
                let message = if error.is_timeout() {
                    "视觉模型请求超时。"
                } else {
                    "无法连接视觉模型服务。"
                };
                self.write_error(context, &request_id, started, kind, None);
                return Err(VisionError::new(kind, message));
            }
        };
        let status = response.status();
        self.write_telemetry(
            context,
            json!({
                "kind":"provider_wire_response_headers",
                "request_id":request_id,
                "status_code":status.as_u16(),
                "headers_elapsed_ms":started.elapsed().as_millis() as u64,
                "capture_layer":"rust_native_vision",
            }),
        );
        let response_bytes = match read_limited(response, MAX_RESPONSE_BYTES, cancelled).await {
            Ok(bytes) => bytes,
            Err(kind) => {
                self.write_error(context, &request_id, started, kind, Some(status.as_u16()));
                if kind == "cancelled" {
                    return Err(VisionError::new("cancelled", "视觉请求已取消。"));
                }
                let message = match kind {
                    "timeout" => "视觉模型响应读取超时。",
                    "response_too_large" => "视觉模型响应超过 4 MiB。",
                    _ => "视觉模型响应无法读取。",
                };
                return Err(VisionError::new(kind, message));
            }
        };
        if !status.is_success() {
            // Do not include the provider error body: some gateways echo request
            // content or credentials in their diagnostics.
            self.write_error(
                context,
                &request_id,
                started,
                "http_error",
                Some(status.as_u16()),
            );
            return Err(VisionError::new(
                "http_error",
                format!("视觉模型服务返回 HTTP {}。", status.as_u16()),
            ));
        }
        if cancelled.load(Ordering::Acquire) {
            self.write_error(
                context,
                &request_id,
                started,
                "cancelled",
                Some(status.as_u16()),
            );
            return Err(VisionError::new("cancelled", "视觉请求已取消。"));
        }
        let response_json: Value = serde_json::from_slice(&response_bytes).map_err(|_| {
            self.write_error(
                context,
                &request_id,
                started,
                "invalid_json",
                Some(status.as_u16()),
            );
            VisionError::new("invalid_json", "视觉模型响应不是有效 JSON。")
        })?;
        let (text, finish_reason, response_id, usage) = match self.route.protocol {
            Protocol::ChatCompletions => parse_chat_response(&response_json),
            Protocol::Responses => {
                parse_responses_response(&response_json, self.config.api_key.as_str())
            }
        }
        .map_err(|error| {
            self.write_error_details(
                context,
                &request_id,
                started,
                error.kind,
                Some(status.as_u16()),
                error.provider_error.as_ref(),
            );
            error
        })?;
        if matches!(finish_reason.as_str(), "length" | "incomplete") {
            self.write_telemetry(
                context,
                json!({
                    "kind":"provider_wire_response_complete",
                    "request_id":request_id,
                    "total_response_ms":started.elapsed().as_millis() as u64,
                    "usage":usage,
                    "finish_reason":finish_reason,
                    "response_id":response_id,
                    "json_parse":if finish_reason == "length" { "truncated" } else { "incomplete" },
                    "capture_layer":"rust_native_vision",
                }),
            );
            return if finish_reason == "length" {
                Err(VisionError::new(
                    "truncated_response",
                    "Responses API 因 max_output_tokens 限制未完成输出。",
                ))
            } else {
                Err(VisionError::new(
                    "incomplete_response",
                    "Responses API 返回未完成状态，原因不是 max_output_tokens。",
                ))
            };
        }
        let parsed_json = extract_json_object(&text).ok_or_else(|| {
            self.write_telemetry(
                context,
                json!({
                    "kind":"provider_wire_response_complete",
                    "request_id":request_id,
                    "total_response_ms":started.elapsed().as_millis() as u64,
                    "usage":usage,
                    "finish_reason":finish_reason,
                    "response_id":response_id,
                    "json_parse":"invalid_object",
                    "capture_layer":"rust_native_vision",
                }),
            );
            VisionError::new("invalid_model_json", "视觉模型未返回可解析的 JSON 对象。")
        })?;
        self.write_telemetry(
            context,
            json!({
                "kind":"provider_wire_response_complete",
                "request_id":request_id,
                "total_response_ms":started.elapsed().as_millis() as u64,
                "usage":usage,
                "finish_reason":finish_reason,
                "response_id":response_id,
                "json_parse":"ok",
                "capture_layer":"rust_native_vision",
            }),
        );
        Ok(VisionResult {
            text,
            parsed_json,
            usage,
            profile,
            protocol: self.route.protocol.name().to_owned(),
            response_id,
            finish_reason,
            request_id,
        })
    }

    fn build_messages(
        &self,
        messages: &[VisionMessage],
        images: &[VerifiedImage],
    ) -> Result<(Value, Vec<WireImage>), VisionError> {
        let mut used = vec![false; images.len()];
        let mut wire_images = Vec::new();
        let mut result = Vec::with_capacity(messages.len());
        for message in messages {
            if !matches!(message.role.as_str(), "system" | "developer" | "user") {
                return Err(VisionError::new(
                    "invalid_request",
                    "多模态复核只允许 system/developer/user 消息，不启用工具调用。",
                ));
            }
            let mut content = Vec::with_capacity(message.content.len());
            for part in &message.content {
                match part {
                    VisionPart::Text(text) => content.push(match self.route.protocol {
                        Protocol::ChatCompletions => json!({"type":"text","text":text}),
                        Protocol::Responses => json!({"type":"input_text","text":text}),
                    }),
                    VisionPart::Image(index) => {
                        if message.role != "user" {
                            return Err(VisionError::new(
                                "invalid_request",
                                "参考图片只能附在 user 消息中。",
                            ));
                        }
                        let image = images.get(*index).ok_or_else(|| {
                            VisionError::new("invalid_request", "图像引用超出图片清单范围。")
                        })?;
                        let hash = validate_image(image)?;
                        used[*index] = true;
                        wire_images.push(WireImage::from_image(*index, &hash, image));
                        let uri =
                            format!("data:{};base64,{}", image.mime, BASE64.encode(&image.bytes));
                        content.push(match self.route.protocol {
                            Protocol::ChatCompletions => json!({
                                "type":"image_url",
                                "image_url":{"url":uri,"detail":"high"}
                            }),
                            Protocol::Responses => json!({
                                "type":"input_image",
                                "image_url":uri,
                                "detail":"high"
                            }),
                        });
                    }
                }
            }
            let value = match self.route.protocol {
                Protocol::ChatCompletions => json!({"role":message.role,"content":content}),
                Protocol::Responses => json!({"role":message.role,"content":content}),
            };
            result.push(value);
        }
        if used.iter().any(|value| !value) {
            return Err(VisionError::new(
                "invalid_request",
                "VerifiedImage 清单中存在未放入请求的图片。",
            ));
        }
        Ok((Value::Array(result), wire_images))
    }

    fn profile(&self, context: &RequestContext) -> Value {
        let effort = normalize_effort(&self.config.effort, self.route.protocol);
        let (enable_thinking, thinking_budget, budget_source) =
            self.thinking_values(context, &effort);
        let response_format = if self.route.protocol == Protocol::ChatCompletions
            && self.route.host.contains("siliconflow")
        {
            Some(json!({"type":"json_object"}))
        } else {
            None
        };
        json!({
            "provider_family": if self.route.host == "api.deepseek.com" { "deepseek" }
                else if self.route.host.contains("siliconflow") { "siliconflow" }
                else { "openai_compatible" },
            "protocol":self.route.protocol.name(),
            "model":self.config.model,
            "reasoning_effort":effort,
            "reasoning_control":self.route.reasoning_control,
            "reasoning_supported":if self.route.reasoning_control == "unknown" { Value::Null }
                else { json!(true) },
            "supported_efforts":self.route.supported_efforts,
            "vision_supported":self.route.vision_supported,
            "capability_source":self.route.capability_source,
            "enable_thinking":enable_thinking,
            "thinking_budget":thinking_budget,
            "thinking_budget_source":budget_source,
            "thinking_budget_hard_limit_known":if thinking_budget.is_some() { json!(false) }
                else { Value::Null },
            "output_token_fields":match self.route.protocol {
                Protocol::ChatCompletions => json!({"max_tokens":self.config.max_output_tokens}),
                Protocol::Responses => json!({"max_output_tokens":self.config.max_output_tokens}),
            },
            "response_format":response_format,
            "task_kind":context.task,
            "review_complexity":context.review_complexity,
        })
    }

    fn thinking_values(
        &self,
        context: &RequestContext,
        effort: &str,
    ) -> (Option<bool>, Option<u64>, &'static str) {
        if self.route.reasoning_control != "toggle_and_budget" {
            return (None, None, "none");
        }
        let enabled = effort != "none";
        if !enabled {
            return (Some(false), None, "disabled");
        }
        if let Some(budget) = self.config.thinking_budget {
            return (Some(true), Some(budget.clamp(128, 32_768)), "user_config");
        }
        let budget = match (context.task.as_str(), context.review_complexity.as_deref()) {
            ("source_inventory", _) => 1_024,
            (
                _,
                Some(
                    "evidence_conflict" | "ambiguous_topology" | "multi_view_conflict" | "complex",
                ),
            ) => 4_096,
            ("vision_review" | "reference_similarity", _) => 2_048,
            _ => 2_048,
        };
        (Some(true), Some(budget), "task_profile_soft_default")
    }

    fn payload(&self, messages: Value, profile: &Value) -> Value {
        let mut payload = match self.route.protocol {
            Protocol::ChatCompletions => json!({
                "model":self.config.model,
                "messages":messages,
                "temperature":0.0,
                "max_tokens":self.config.max_output_tokens,
                "stream":false,
            }),
            Protocol::Responses => json!({
                "model":self.config.model,
                "input":messages,
                "max_output_tokens":self.config.max_output_tokens,
                "temperature":0.0,
                "stream":false,
            }),
        };
        if self.route.protocol == Protocol::Responses && self.route.reasoning_control == "effort" {
            payload["reasoning"] = json!({"effort":profile["reasoning_effort"]});
        }
        if self.route.protocol == Protocol::ChatCompletions {
            if let Some(format) = profile.get("response_format").filter(|v| !v.is_null()) {
                payload["response_format"] = format.clone();
            }
            if let Some(enabled) = profile.get("enable_thinking").and_then(Value::as_bool) {
                payload["enable_thinking"] = json!(enabled);
            } else if !matches!(
                profile.get("reasoning_effort").and_then(Value::as_str),
                None | Some("")
            ) {
                payload["reasoning_effort"] = profile["reasoning_effort"].clone();
            }
            if let Some(budget) = profile.get("thinking_budget").and_then(Value::as_u64) {
                payload["thinking_budget"] = json!(budget);
            }
        }
        payload
    }

    fn write_error(
        &self,
        context: &RequestContext,
        request_id: &str,
        started: Instant,
        kind: &str,
        status: Option<u16>,
    ) {
        self.write_error_details(context, request_id, started, kind, status, None);
    }

    fn write_error_details(
        &self,
        context: &RequestContext,
        request_id: &str,
        started: Instant,
        kind: &str,
        status: Option<u16>,
        provider_error: Option<&ProviderErrorDetails>,
    ) {
        let mut row = json!({
            "kind":"provider_wire_error",
            "request_id":request_id,
            "status_code":status,
            "elapsed_ms":started.elapsed().as_millis() as u64,
            "error_name":kind,
            "provider_host":self.route.host,
            "protocol":self.route.protocol.name(),
            "model":self.config.model,
            "capture_layer":"rust_native_vision",
        });
        if let Some(details) = provider_error {
            row["provider_response_status"] = json!(details.response_status);
            row["provider_error_code"] = json!(details.code);
            row["provider_error_type"] = json!(details.error_type);
            row["provider_error_message"] = json!(details.message);
            row["provider_error_fields_source"] = json!("response.error");
        }
        self.write_telemetry(context, row);
    }

    fn write_telemetry(&self, context: &RequestContext, mut row: Value) {
        let Some(path) = context.telemetry_path.as_ref() else {
            return;
        };
        if let Some(object) = row.as_object_mut() {
            object.insert("turn_id".into(), json!(context.turn_id));
            object.insert("at_ms".into(), json!(now_epoch_ms()));
        }
        let mut bytes = match serde_json::to_vec(&row) {
            Ok(bytes) => bytes,
            Err(_) => return,
        };
        bytes.push(b'\n');
        if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
            let _ = file.write_all(&bytes);
        }
    }
}

impl WireImage {
    fn from_image(index: usize, hash: &str, image: &VerifiedImage) -> Self {
        Self {
            image_index: index,
            transport_sha256: hash.to_owned(),
            byte_length: image.bytes.len(),
            source_sha256: image.source_sha256.clone(),
            reference_id: image.reference_id.clone(),
            roi_norm: image.roi_norm.clone(),
            preview: image.preview.clone(),
        }
    }

    fn to_value(&self) -> Value {
        let preview = self.preview.as_ref();
        json!({
            "image_index":self.image_index,
            "transport_sha256":self.transport_sha256,
            "byte_length":self.byte_length,
            "source_sha256":self.source_sha256,
            "source_reference_ids":self.reference_id.iter().cloned().collect::<Vec<_>>(),
            "source_kind":if preview.is_some() { "tool_preview" }
                else if self.roi_norm.is_some() { "reference_roi" }
                else if self.reference_id.is_some() { "user_reference" }
                else { "unknown" },
            "roi_norm":self.roi_norm,
            "preview_revisions":preview.and_then(|v| v.get("revision")).map(|v| json!([v])).unwrap_or_else(|| json!([])),
            "preview_generations":preview.and_then(|v| v.get("preview_generation")).map(|v| json!([v])).unwrap_or_else(|| json!([])),
            "preview_views":preview.and_then(|v| v.get("view")).map(|v| json!([v])).unwrap_or_else(|| json!([])),
            "preview_geometry_signatures":preview.and_then(|v| v.get("geometry_signature")).map(|v| json!([v])).unwrap_or_else(|| json!([])),
            "partial_preview":preview.and_then(|v| v.get("partial")).and_then(Value::as_bool).unwrap_or(false),
            "source_mapping":"exact-transport-hash-match",
        })
    }
}

fn resolve_route(base_url: &str, model: &str) -> Result<Route, VisionError> {
    let mut base = Url::parse(base_url.trim())
        .map_err(|_| VisionError::new("invalid_config", "API 地址不是有效 URL。"))?;
    if !matches!(base.scheme(), "http" | "https") || base.host_str().is_none() {
        return Err(VisionError::new(
            "invalid_config",
            "API 地址必须是 HTTP 或 HTTPS URL。",
        ));
    }
    if !base.username().is_empty() || base.password().is_some() {
        return Err(VisionError::new(
            "invalid_config",
            "请将 API 密钥放在密钥字段，不要嵌入 API 地址。",
        ));
    }
    let host = base.host_str().unwrap_or_default().to_ascii_lowercase();
    let model_lower = model.trim().to_ascii_lowercase();
    let mut path = base.path().trim_end_matches('/').to_owned();
    let protocol = if path.to_ascii_lowercase().ends_with("/responses") {
        path.truncate(path.len() - "/responses".len());
        Protocol::Responses
    } else if path.to_ascii_lowercase().ends_with("/chat/completions") {
        path.truncate(path.len() - "/chat/completions".len());
        Protocol::ChatCompletions
    } else if host == "api.deepseek.com"
        && matches!(model_lower.as_str(), "deepseek-flash" | "deepseek-v4-pro")
    {
        Protocol::Responses
    } else {
        Protocol::ChatCompletions
    };
    base.set_path(&path);
    let root_path = base.path().trim_end_matches('/');
    let endpoint_path = format!("{}/{}", root_path, protocol.endpoint());
    let mut endpoint = base.clone();
    endpoint.set_path(&endpoint_path);
    let vision_supported = if host == "api.deepseek.com"
        && protocol == Protocol::Responses
        && model_lower == "deepseek-flash"
    {
        Some(true)
    } else if host == "api.deepseek.com"
        && protocol == Protocol::Responses
        && model_lower == "deepseek-v4-pro"
    {
        Some(false)
    } else if host.contains("siliconflow") && model_lower == "moonshotai/kimi-k2.7-code" {
        Some(true)
    } else {
        None
    };
    let reasoning_control = if protocol == Protocol::Responses && host == "api.deepseek.com" {
        "effort"
    } else if host.contains("siliconflow") && model_lower.starts_with("moonshotai/kimi-") {
        "toggle_and_budget"
    } else {
        "unknown"
    };
    let supported_efforts = match reasoning_control {
        "effort" => vec!["none", "low", "high", "max"],
        "toggle_and_budget" => vec!["none", "low"],
        _ => Vec::new(),
    };
    let capability_source = match (host.as_str(), protocol, model_lower.as_str()) {
        ("api.deepseek.com", Protocol::Responses, "deepseek-flash" | "deepseek-v4-pro") => {
            Some("official DeepSeek Responses model route")
        }
        (_, Protocol::ChatCompletions, "moonshotai/kimi-k2.7-code")
            if host.contains("siliconflow") =>
        {
            Some("official SiliconFlow Kimi-K2.7-Code image route")
        }
        _ => None,
    };
    Ok(Route {
        endpoint,
        protocol,
        host,
        vision_supported,
        reasoning_control,
        supported_efforts,
        capability_source,
    })
}

fn normalize_effort(value: &str, protocol: Protocol) -> String {
    let normalized = value.trim().to_ascii_lowercase();
    let effort = match normalized.as_str() {
        "disabled" | "off" | "false" => "none",
        "xhigh" => "max",
        "ultra" => "max",
        "" => "none",
        other => other,
    };
    match protocol {
        Protocol::Responses => match effort {
            "none" | "low" | "high" | "max" => effort.to_owned(),
            "minimal" => "low".to_owned(),
            "medium" => "high".to_owned(),
            "auto" => "high".to_owned(),
            _ => "none".to_owned(),
        },
        Protocol::ChatCompletions => effort.to_owned(),
    }
}

fn validate_image(image: &VerifiedImage) -> Result<String, VisionError> {
    if !matches!(
        image.mime.as_str(),
        "image/png" | "image/jpeg" | "image/webp" | "image/gif"
    ) {
        return Err(VisionError::new(
            "invalid_image",
            "图片 MIME 类型不受支持。",
        ));
    }
    if image.bytes.is_empty() || image.bytes.len() > MAX_IMAGE_BYTES {
        return Err(VisionError::new("invalid_image", "图片为空或超过 20 MiB。"));
    }
    let digest = hex_sha256(&image.bytes);
    if image.transport_sha256.to_ascii_lowercase() != digest {
        return Err(VisionError::new(
            "image_hash_mismatch",
            "传输图片 SHA-256 与实际图片字节不一致。",
        ));
    }
    if image.roi_norm.is_none() && image.preview.is_none() {
        if let Some(source_sha256) = image.source_sha256.as_deref() {
            if source_sha256.to_ascii_lowercase() != digest {
                return Err(VisionError::new(
                    "image_hash_mismatch",
                    "原图 SHA-256 与实际图片字节不一致。",
                ));
            }
        }
    }
    if let Some(preview) = &image.preview {
        if let Some(expected) = preview.get("pixel_sha256").and_then(Value::as_str) {
            if expected.to_ascii_lowercase() != digest {
                return Err(VisionError::new(
                    "image_hash_mismatch",
                    "预览像素 SHA-256 与实际图片字节不一致。",
                ));
            }
        }
    }
    Ok(digest)
}

fn parse_chat_response(
    value: &Value,
) -> Result<(String, String, Option<String>, Option<Value>), VisionError> {
    let choice = value
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| VisionError::new("invalid_response", "Chat 响应缺少 choices[0]。"))?;
    let message = choice.get("message").unwrap_or(&Value::Null);
    let text = extract_message_text(message.get("content").unwrap_or(&Value::Null));
    let finish = choice
        .get("finish_reason")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_owned();
    let response_id = value.get("id").and_then(Value::as_str).map(str::to_owned);
    let usage = safe_usage(value.get("usage"));
    Ok((
        text,
        if finish == "length" {
            "length".into()
        } else {
            finish
        },
        response_id,
        usage,
    ))
}

fn parse_responses_response(
    value: &Value,
    api_key: &str,
) -> Result<(String, String, Option<String>, Option<Value>), VisionError> {
    let status = value.get("status").and_then(Value::as_str);
    let provider_error = value
        .get("error")
        .filter(|error| !error.is_null() && error.is_object());
    if status == Some("failed") || provider_error.is_some() {
        let error = provider_error.unwrap_or(&Value::Null);
        let details = ProviderErrorDetails {
            response_status: value
                .get("status")
                .and_then(Value::as_str)
                .map(|status| sanitize_provider_label(status, 32)),
            code: error
                .get("code")
                .and_then(Value::as_str)
                .map(|code| sanitize_provider_label(code, 96)),
            error_type: error
                .get("type")
                .and_then(Value::as_str)
                .map(|error_type| sanitize_provider_label(error_type, 96)),
            message: error
                .get("message")
                .and_then(Value::as_str)
                .map(|message| sanitize_provider_message(message, api_key)),
        };
        return Err(VisionError::from_provider_error(details));
    }
    let response_id = value.get("id").and_then(Value::as_str).map(str::to_owned);
    let usage = safe_usage(value.get("usage"));
    let mut text = String::new();
    if let Some(output_text) = value.get("output_text").and_then(Value::as_str) {
        text.push_str(output_text);
    } else if let Some(output) = value.get("output").and_then(Value::as_array) {
        for item in output {
            if item.get("type").and_then(Value::as_str) != Some("message") {
                continue;
            }
            if let Some(parts) = item.get("content").and_then(Value::as_array) {
                for part in parts {
                    match part.get("type").and_then(Value::as_str) {
                        Some("output_text") => {
                            text.push_str(part.get("text").and_then(Value::as_str).unwrap_or(""));
                        }
                        Some("refusal") => {
                            return Err(VisionError::new(
                                "provider_refusal",
                                "视觉模型拒绝了本次请求。",
                            ));
                        }
                        _ => {}
                    }
                }
            }
        }
    }
    let incomplete_reason = value
        .pointer("/incomplete_details/reason")
        .or_else(|| value.pointer("/status_details/reason"))
        .and_then(Value::as_str);
    let finish = if incomplete_reason == Some("max_output_tokens") {
        "length"
    } else if status == Some("incomplete") {
        "incomplete"
    } else {
        "stop"
    };
    if text.is_empty() && finish == "stop" {
        return Err(VisionError::new(
            "invalid_response",
            "Responses API 未返回可见文本。",
        ));
    }
    Ok((text, finish.into(), response_id, usage))
}

fn sanitize_provider_label(value: &str, max_chars: usize) -> String {
    value
        .chars()
        .filter(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '_' | '-' | '.' | ':')
        })
        .take(max_chars)
        .collect()
}

fn sanitize_provider_message(value: &str, api_key: &str) -> String {
    const MAX_MESSAGE_BYTES: usize = 512;
    let mut compact = value
        .chars()
        .map(|character| {
            if character.is_control() || character.is_whitespace() {
                ' '
            } else {
                character
            }
        })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    if api_key.chars().count() >= 4 {
        compact = compact.replace(api_key, "[redacted credential]");
    }
    let safe = compact
        .split_whitespace()
        .map(|token| {
            let lower = token.to_ascii_lowercase();
            if lower.contains("data:") && lower.contains("base64,") {
                "[redacted image payload]"
            } else if is_opaque_payload_token(token) {
                "[redacted opaque payload]"
            } else {
                token
            }
        })
        .collect::<Vec<_>>()
        .join(" ");
    let mut end = safe.len().min(MAX_MESSAGE_BYTES);
    while !safe.is_char_boundary(end) {
        end -= 1;
    }
    safe[..end].to_owned()
}

fn is_opaque_payload_token(token: &str) -> bool {
    if token.len() < 128 {
        return false;
    }
    let allowed = token
        .chars()
        .filter(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '+' | '/' | '=' | '_' | '-')
        })
        .count();
    allowed * 10 >= token.len() * 9
}

fn extract_message_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Array(parts) => parts
            .iter()
            .filter_map(|part| {
                if matches!(
                    part.get("type").and_then(Value::as_str),
                    Some("text" | "output_text")
                ) {
                    part.get("text").and_then(Value::as_str)
                } else {
                    None
                }
            })
            .collect::<Vec<_>>()
            .join(""),
        _ => String::new(),
    }
}

fn extract_json_object(text: &str) -> Option<Value> {
    let trimmed = text.trim();
    if let Ok(value) = serde_json::from_str::<Value>(trimmed) {
        return if value.is_object() { Some(value) } else { None };
    }
    let unfenced = trimmed
        .strip_prefix("```json")
        .or_else(|| trimmed.strip_prefix("```JSON"))
        .or_else(|| trimmed.strip_prefix("```"))
        .unwrap_or(trimmed)
        .trim_start();
    let bytes = unfenced.as_bytes();
    let mut start = None;
    let mut depth = 0usize;
    let mut in_string = false;
    let mut escaped = false;
    for (index, byte) in bytes.iter().enumerate() {
        if in_string {
            if escaped {
                escaped = false;
            } else if *byte == b'\\' {
                escaped = true;
            } else if *byte == b'"' {
                in_string = false;
            }
            continue;
        }
        match byte {
            b'"' => in_string = true,
            b'{' => {
                if start.is_none() {
                    start = Some(index);
                }
                depth += 1;
            }
            b'}' if start.is_some() => {
                depth = depth.saturating_sub(1);
                if depth == 0 {
                    let value =
                        serde_json::from_slice::<Value>(&bytes[start.unwrap()..=index]).ok()?;
                    return if value.is_object() { Some(value) } else { None };
                }
            }
            _ => {}
        }
    }
    None
}

fn safe_usage(value: Option<&Value>) -> Option<Value> {
    let value = value?.as_object()?;
    let aliases = [
        ("input_tokens", &["input_tokens", "prompt_tokens"][..]),
        ("output_tokens", &["output_tokens", "completion_tokens"][..]),
        ("total_tokens", &["total_tokens"][..]),
        ("reasoning_tokens", &["reasoning_tokens"][..]),
    ];
    let mut result = Map::new();
    for (target, sources) in aliases {
        if let Some(count) = sources
            .iter()
            .find_map(|key| non_negative_count(value.get(*key)))
        {
            result.insert(target.into(), json!(count));
        }
    }
    let mut cache = Map::new();
    for section in [
        "prompt_tokens_details",
        "input_tokens_details",
        "completion_tokens_details",
        "output_tokens_details",
        "cache_details",
    ] {
        if let Some(row) = value.get(section).and_then(Value::as_object) {
            if !result.contains_key("reasoning_tokens") {
                if let Some(count) = row
                    .get("reasoning_tokens")
                    .and_then(|value| non_negative_count(Some(value)))
                {
                    result.insert("reasoning_tokens".into(), json!(count));
                }
            }
            for name in [
                "cached_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "cache_creation_tokens",
                "cache_read_input_tokens",
                "cache_write_input_tokens",
                "cache_creation_input_tokens",
                "cached_input_tokens",
                "cache_hit_tokens",
                "cache_miss_tokens",
                "reasoning_tokens",
            ] {
                if let Some(count) = non_negative_count(row.get(name)) {
                    if name != "reasoning_tokens" {
                        cache.insert(format!("{section}.{name}"), json!(count));
                    }
                }
            }
        }
    }
    if !cache.is_empty() {
        result.insert("cache_token_details".into(), Value::Object(cache));
    }
    if result.is_empty() {
        None
    } else {
        Some(Value::Object(result))
    }
}

fn non_negative_count(value: Option<&Value>) -> Option<u64> {
    let number = value?.as_f64()?;
    if !number.is_finite() || number < 0.0 || number.fract() != 0.0 || number > u64::MAX as f64 {
        return None;
    }
    Some(number as u64)
}

async fn wait_for_cancel(cancelled: &AtomicBool) {
    loop {
        if cancelled.load(Ordering::Acquire) {
            return;
        }
        tokio::time::sleep(Duration::from_millis(30)).await;
    }
}

async fn read_limited(
    response: reqwest::Response,
    limit: usize,
    cancelled: &AtomicBool,
) -> Result<Vec<u8>, &'static str> {
    let mut bytes = Vec::new();
    let mut response = response;
    loop {
        let chunk = tokio::select! {
            chunk = response.chunk() => match chunk {
                Ok(chunk) => chunk,
                Err(error) if error.is_timeout() => return Err("timeout"),
                Err(_) => return Err("response_read_error"),
            },
            _ = wait_for_cancel(cancelled) => return Err("cancelled"),
        };
        let Some(chunk) = chunk else {
            break;
        };
        if bytes.len().saturating_add(chunk.len()) > limit {
            return Err("response_too_large");
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

fn hex_sha256(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn next_request_id() -> String {
    let seq = REQUEST_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    format!("native-{}-{}-{seq}", std::process::id(), now_epoch_ms())
}

fn now_epoch_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::Arc;
    use std::thread;

    struct MockReply {
        path: String,
        body: Value,
        status: u16,
        headers_delay: Duration,
        body_delay: Duration,
    }

    fn mock_server(reply: MockReply) -> (String, thread::JoinHandle<(String, Value, String)>) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let handle = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(3)))
                .unwrap();
            let mut request = Vec::new();
            let mut chunk = [0u8; 8192];
            let header_end;
            loop {
                let count = stream.read(&mut chunk).unwrap_or(0);
                if count == 0 {
                    panic!("client closed before sending request");
                }
                request.extend_from_slice(&chunk[..count]);
                if let Some(pos) = request.windows(4).position(|window| window == b"\r\n\r\n") {
                    header_end = pos + 4;
                    break;
                }
                assert!(
                    request.len() < MAX_REQUEST_BYTES,
                    "headers unexpectedly large"
                );
            }
            let headers = String::from_utf8_lossy(&request[..header_end]).to_string();
            let content_length = headers
                .lines()
                .find_map(|line| {
                    line.split_once(':')
                        .filter(|(key, _)| key.eq_ignore_ascii_case("content-length"))
                })
                .and_then(|(_, value)| value.trim().parse::<usize>().ok())
                .unwrap_or(0);
            while request.len() < header_end + content_length {
                let count = stream.read(&mut chunk).unwrap_or(0);
                if count == 0 {
                    break;
                }
                request.extend_from_slice(&chunk[..count]);
            }
            let payload: Value =
                serde_json::from_slice(&request[header_end..header_end + content_length]).unwrap();
            let body = serde_json::to_vec(&reply.body).unwrap();
            let status = reply.status;
            let reason = if (200..300).contains(&status) {
                "OK"
            } else {
                "Error"
            };
            let response = format!(
                "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            );
            if !reply.headers_delay.is_zero() {
                thread::sleep(reply.headers_delay);
            }
            let _ = stream.write_all(response.as_bytes());
            let split = body.len() / 2;
            let _ = stream.write_all(&body[..split]);
            let _ = stream.flush();
            if !reply.body_delay.is_zero() {
                thread::sleep(reply.body_delay);
            }
            let _ = stream.write_all(&body[split..]);
            (headers, payload, reply.path)
        });
        (format!("http://{address}"), handle)
    }

    fn run<F: std::future::Future>(future: F) -> F::Output {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(future)
    }

    fn context(telemetry: PathBuf, task: &str) -> RequestContext {
        RequestContext {
            task: task.to_owned(),
            turn_id: Some("turn-test-1".into()),
            telemetry_path: Some(telemetry),
            review_complexity: None,
            timeout: Duration::from_secs(3),
        }
    }

    fn image() -> VerifiedImage {
        let bytes = b"verified tiny image".to_vec();
        VerifiedImage {
            transport_sha256: hex_sha256(&bytes),
            source_sha256: Some(hex_sha256(&bytes)),
            bytes,
            mime: "image/png".into(),
            reference_id: Some("sha256:test".into()),
            roi_norm: None,
            preview: None,
        }
    }

    fn request_messages() -> Vec<VisionMessage> {
        vec![
            VisionMessage {
                role: "system".into(),
                content: vec![VisionPart::Text("return json".into())],
            },
            VisionMessage {
                role: "user".into(),
                content: vec![VisionPart::Text("inspect".into()), VisionPart::Image(0)],
            },
        ]
    }

    fn temp_file(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "native-vision-{label}-{}-{}.jsonl",
            std::process::id(),
            now_epoch_ms()
        ))
    }

    #[test]
    fn chat_completions_sends_high_detail_image_and_safe_image_telemetry() {
        let output = temp_file("chat");
        let json_text = r#"{"summary":"ok","visible_evidence":[]}"#;
        let (base, server) = mock_server(MockReply {
            path: "/v1/chat/completions".into(),
            body: json!({
                "id":"chatcmpl-test",
                "choices":[{"message":{"content":json_text},"finish_reason":"stop"}],
                "usage":{"prompt_tokens":12,"completion_tokens":4,"total_tokens":16}
            }),
            status: 200,
            headers_delay: Duration::ZERO,
            body_delay: Duration::ZERO,
        });
        let mut config = ProviderConfig {
            base_url: format!("{base}/v1"),
            model: "custom-vlm".into(),
            api_key: "secret-chat-key".into(),
            effort: "none".into(),
            thinking_budget: None,
            max_output_tokens: 2400,
        };
        let provider = NativeVisionProvider::new(config.clone()).unwrap();
        let result = run(provider.call(
            &request_messages(),
            &[image()],
            &context(output.clone(), "source_inventory"),
            &AtomicBool::new(false),
        ))
        .unwrap();
        assert_eq!(result.protocol, "chat_completions");
        assert_eq!(result.parsed_json["summary"], "ok");
        assert_eq!(result.usage.as_ref().unwrap()["input_tokens"], 12);
        let (headers, payload, path) = server.join().unwrap();
        assert!(headers.starts_with("POST /v1/chat/completions "));
        assert!(
            headers
                .to_ascii_lowercase()
                .contains("authorization: bearer secret-chat-key")
        );
        assert_eq!(path, "/v1/chat/completions");
        assert_eq!(payload["stream"], false);
        assert_eq!(payload["max_tokens"], 2400);
        assert_eq!(
            payload["messages"][1]["content"][1]["image_url"]["detail"],
            "high"
        );
        let telemetry = std::fs::read_to_string(&output).unwrap();
        assert!(telemetry.contains("rust_native_vision"));
        assert!(telemetry.contains(&hex_sha256(b"verified tiny image")));
        assert!(!telemetry.contains("secret-chat-key"));
        let _ = std::fs::remove_file(output);
        config.api_key.clear();
    }

    #[test]
    fn responses_sends_input_image_and_parses_visible_output_only() {
        let (base, server) = mock_server(MockReply {
            path: "/v1/responses".into(),
            body: json!({
                "id":"resp-test", "status":"completed",
                "error":null,
                "output":[
                    {"type":"reasoning","content":[{"type":"reasoning_text","text":"private reasoning"}]},
                    {"type":"message","content":[{"type":"output_text","text":"{\"view_comparable\":true}"}]}
                ],
                "usage":{"input_tokens":20,"output_tokens":7,"total_tokens":27,"output_tokens_details":{"reasoning_tokens":3}}
            }),
            status: 200,
            headers_delay: Duration::ZERO,
            body_delay: Duration::ZERO,
        });
        let mut provider = NativeVisionProvider::new(ProviderConfig {
            base_url: format!("{base}/v1/responses"),
            model: "deepseek-flash".into(),
            api_key: "secret-responses-key".into(),
            effort: "low".into(),
            thinking_budget: None,
            max_output_tokens: 4096,
        })
        .unwrap();
        provider.route.host = "api.deepseek.com".into();
        provider.route.vision_supported = Some(true);
        provider.route.reasoning_control = "effort";
        provider.route.supported_efforts = vec!["none", "low", "high", "max"];
        let result = run(provider.call(
            &request_messages(),
            &[image()],
            &context(PathBuf::new(), "vision_review"),
            &AtomicBool::new(false),
        ))
        .unwrap();
        assert_eq!(result.protocol, "responses");
        assert_eq!(result.parsed_json["view_comparable"], true);
        assert!(!result.text.contains("private reasoning"));
        assert_eq!(result.usage.as_ref().unwrap()["reasoning_tokens"], 3);
        let (headers, payload, path) = server.join().unwrap();
        assert!(headers.starts_with("POST /v1/responses "));
        assert_eq!(path, "/v1/responses");
        assert_eq!(payload["reasoning"]["effort"], "low");
        assert_eq!(payload["input"][1]["content"][1]["type"], "input_image");
        assert_eq!(payload["input"][1]["content"][1]["detail"], "high");
    }

    #[test]
    fn completed_responses_error_null_with_output_message_is_success() {
        let (base, server) = mock_server(MockReply {
            path: "/v1/responses".into(),
            body: json!({
                "id":"resp-error-null",
                "status":"completed",
                "error":null,
                "output":[{
                    "type":"message",
                    "content":[{"type":"output_text","text":"{\"summary\":\"null is not failure\"}"}]
                }]
            }),
            status: 200,
            headers_delay: Duration::ZERO,
            body_delay: Duration::ZERO,
        });
        let provider = NativeVisionProvider::new(ProviderConfig {
            base_url: format!("{base}/v1/responses"),
            model: "custom-vlm".into(),
            api_key: "secret".into(),
            effort: "none".into(),
            thinking_budget: None,
            max_output_tokens: 2400,
        })
        .unwrap();
        let result = run(provider.call(
            &request_messages(),
            &[image()],
            &context(PathBuf::new(), "vision_review"),
            &AtomicBool::new(false),
        ))
        .unwrap();
        assert_eq!(result.parsed_json["summary"], "null is not failure");
        assert_eq!(result.response_id.as_deref(), Some("resp-error-null"));
        let _ = server.join().unwrap();
    }

    #[test]
    fn incomplete_responses_without_text_are_distinguished_from_max_token_truncation() {
        let max_tokens = parse_responses_response(
            &json!({
                "status":"incomplete",
                "error":null,
                "incomplete_details":{"reason":"max_output_tokens"},
                "output":[]
            }),
            "secret",
        )
        .unwrap();
        assert_eq!(max_tokens.0, "");
        assert_eq!(max_tokens.1, "length");

        let other_incomplete = parse_responses_response(
            &json!({
                "status":"incomplete",
                "error":null,
                "incomplete_details":{"reason":"content_filter"},
                "output":[]
            }),
            "secret",
        )
        .unwrap();
        assert_eq!(other_incomplete.0, "");
        assert_eq!(other_incomplete.1, "incomplete");

        let completed_without_text = parse_responses_response(
            &json!({"status":"completed","error":null,"output":[]}),
            "secret",
        )
        .unwrap_err();
        assert_eq!(completed_without_text.kind, "invalid_response");
    }

    #[test]
    fn incomplete_max_output_tokens_without_text_reports_truncation_not_invalid_json() {
        let (base, server) = mock_server(MockReply {
            path: "/v1/responses".into(),
            body: json!({
                "status":"incomplete",
                "error":null,
                "incomplete_details":{"reason":"max_output_tokens"},
                "output":[]
            }),
            status: 200,
            headers_delay: Duration::ZERO,
            body_delay: Duration::ZERO,
        });
        let provider = NativeVisionProvider::new(ProviderConfig {
            base_url: format!("{base}/v1/responses"),
            model: "custom-vlm".into(),
            api_key: "secret".into(),
            effort: "none".into(),
            thinking_budget: None,
            max_output_tokens: 2400,
        })
        .unwrap();
        let error = run(provider.call(
            &request_messages(),
            &[image()],
            &context(PathBuf::new(), "vision_review"),
            &AtomicBool::new(false),
        ))
        .unwrap_err();
        assert_eq!(error.kind, "truncated_response");
        assert!(error.message.contains("max_output_tokens"));
        let _ = server.join().unwrap();
    }

    #[test]
    fn full_endpoint_suffix_is_not_duplicated_and_chat_json_mode_is_provider_specific() {
        let (base, server) = mock_server(MockReply {
            path: "/v1/chat/completions".into(),
            body: json!({"choices":[{"message":{"content":"{\"ok\":true}"},"finish_reason":"stop"}]}),
            status: 200,
            headers_delay: Duration::ZERO,
            body_delay: Duration::ZERO,
        });
        let mut provider = NativeVisionProvider::new(ProviderConfig {
            base_url: format!("{base}/v1/chat/completions"),
            model: "moonshotai/Kimi-K2.7-Code".into(),
            api_key: "secret".into(),
            effort: "low".into(),
            thinking_budget: Some(1024),
            max_output_tokens: 2400,
        })
        .unwrap();
        provider.route.host = "api.siliconflow.cn".into();
        provider.route.vision_supported = Some(true);
        provider.route.reasoning_control = "toggle_and_budget";
        provider.route.supported_efforts = vec!["none", "low"];
        let result = run(provider.call(
            &request_messages(),
            &[image()],
            &context(PathBuf::new(), "source_inventory"),
            &AtomicBool::new(false),
        ))
        .unwrap();
        assert_eq!(result.profile["vision_supported"], true);
        let (headers, payload, path) = server.join().unwrap();
        assert!(headers.starts_with("POST /v1/chat/completions "));
        assert_eq!(path, "/v1/chat/completions");
        assert_eq!(payload["response_format"]["type"], "json_object");
        assert_eq!(payload["enable_thinking"], true);
        assert_eq!(payload["thinking_budget"], 1024);
    }

    #[test]
    fn provider_error_body_never_leaks_api_key_to_error_or_telemetry() {
        let output = temp_file("error");
        let secret = "do-not-leak-credential";
        let (base, server) = mock_server(MockReply {
            path: "/chat/completions".into(),
            body: json!({"error":{"message":format!("request used {secret}")}}),
            status: 401,
            headers_delay: Duration::ZERO,
            body_delay: Duration::ZERO,
        });
        let provider = NativeVisionProvider::new(ProviderConfig {
            base_url: base,
            model: "custom-vlm".into(),
            api_key: secret.into(),
            effort: "none".into(),
            thinking_budget: None,
            max_output_tokens: 1000,
        })
        .unwrap();
        let error = run(provider.call(
            &request_messages(),
            &[image()],
            &context(output.clone(), "source_inventory"),
            &AtomicBool::new(false),
        ))
        .unwrap_err();
        assert_eq!(error.kind, "http_error");
        assert!(!error.to_string().contains(secret));
        let (headers, _, _) = server.join().unwrap();
        assert!(
            headers
                .to_ascii_lowercase()
                .contains("authorization: bearer do-not-leak-credential")
        );
        let telemetry = std::fs::read_to_string(&output).unwrap();
        assert!(!telemetry.contains(secret));
        let _ = std::fs::remove_file(output);
    }

    #[test]
    fn responses_failed_status_preserves_bounded_sanitized_error_fields() {
        let output = temp_file("responses-failed");
        let secret = "secret-responses-credential";
        let base64_blob = "A".repeat(180);
        let reasoning_marker = "must-not-log-model-reasoning";
        let (base, server) = mock_server(MockReply {
            path: "/v1/responses".into(),
            body: json!({
                "id":"resp-failed-test",
                "status":"failed",
                "error":{
                    "type":"server_error",
                    "code":"invalid_request",
                    "message":format!("Rejected credential {secret}; data:image/png;base64,{base64_blob}")
                },
                "output":[{"type":"reasoning","content":[{"type":"reasoning_text","text":reasoning_marker}]}]
            }),
            status: 200,
            headers_delay: Duration::ZERO,
            body_delay: Duration::ZERO,
        });
        let provider = NativeVisionProvider::new(ProviderConfig {
            base_url: format!("{base}/v1/responses"),
            model: "deepseek-flash".into(),
            api_key: secret.into(),
            effort: "none".into(),
            thinking_budget: None,
            max_output_tokens: 2400,
        })
        .unwrap();
        let ctx = context(output.clone(), "vision_review");
        let error = run(provider.call(
            &request_messages(),
            &[image()],
            &ctx,
            &AtomicBool::new(false),
        ))
        .unwrap_err();
        assert_eq!(error.kind, "provider_error");
        assert!(error.message.contains("server_error"));
        assert!(error.message.contains("invalid_request"));
        assert!(!error.message.contains(secret));
        assert!(!error.message.contains(&base64_blob));
        let details = error.provider_error.as_ref().unwrap();
        assert_eq!(details.response_status.as_deref(), Some("failed"));
        assert_eq!(details.error_type.as_deref(), Some("server_error"));
        assert_eq!(details.code.as_deref(), Some("invalid_request"));
        assert!(
            details
                .message
                .as_deref()
                .unwrap()
                .contains("[redacted credential]")
        );
        assert!(
            details
                .message
                .as_deref()
                .unwrap()
                .contains("[redacted image payload]")
        );
        let (_, _, _) = server.join().unwrap();
        let telemetry = std::fs::read_to_string(&output).unwrap();
        assert!(!telemetry.contains(secret));
        assert!(!telemetry.contains(&base64_blob));
        assert!(!telemetry.contains(reasoning_marker));
        let error_event: Value = telemetry
            .lines()
            .filter_map(|line| serde_json::from_str(line).ok())
            .find(|row: &Value| row["kind"] == "provider_wire_error")
            .unwrap();
        assert_eq!(error_event["status_code"], 200);
        assert_eq!(error_event["provider_response_status"], "failed");
        assert_eq!(error_event["provider_error_type"], "server_error");
        assert_eq!(error_event["provider_error_code"], "invalid_request");
        assert_eq!(
            error_event["provider_error_fields_source"],
            "response.error"
        );
        assert!(
            error_event["provider_error_message"]
                .as_str()
                .unwrap()
                .contains("[redacted image payload]")
        );
        let _ = std::fs::remove_file(output);
    }

    #[test]
    fn mismatched_transport_hash_is_rejected_before_network_request() {
        let bytes = b"unverified".to_vec();
        let image = VerifiedImage {
            bytes,
            mime: "image/png".into(),
            reference_id: Some("sha256:wrong".into()),
            source_sha256: Some("0000".into()),
            transport_sha256: "1111".into(),
            roi_norm: None,
            preview: None,
        };
        let provider = NativeVisionProvider::new(ProviderConfig {
            base_url: "http://127.0.0.1:1".into(),
            model: "custom-vlm".into(),
            api_key: "secret".into(),
            effort: "none".into(),
            thinking_budget: None,
            max_output_tokens: 1000,
        })
        .unwrap();
        let error = run(provider.call(
            &request_messages(),
            &[image],
            &context(PathBuf::new(), "source_inventory"),
            &AtomicBool::new(false),
        ))
        .unwrap_err();
        assert_eq!(error.kind, "image_hash_mismatch");
    }

    #[test]
    fn invalid_or_truncated_json_is_rejected_and_unknown_vision_capability_stays_unknown() {
        let (base, server) = mock_server(MockReply {
            path: "/chat/completions".into(),
            body: json!({"choices":[{"message":{"content":"not-json"},"finish_reason":"stop"}]}),
            status: 200,
            headers_delay: Duration::ZERO,
            body_delay: Duration::ZERO,
        });
        let provider = NativeVisionProvider::new(ProviderConfig {
            base_url: base,
            model: "unknown-vlm".into(),
            api_key: "secret".into(),
            effort: "none".into(),
            thinking_budget: None,
            max_output_tokens: 1000,
        })
        .unwrap();
        assert!(provider.route.vision_supported.is_none());
        let error = run(provider.call(
            &request_messages(),
            &[image()],
            &context(PathBuf::new(), "source_inventory"),
            &AtomicBool::new(false),
        ))
        .unwrap_err();
        assert_eq!(error.kind, "invalid_model_json");
        let _ = server.join().unwrap();
        assert_eq!(
            extract_json_object("prefix {\"ok\":true} trailing").unwrap()["ok"],
            true
        );
        assert!(extract_json_object("{\"a\":").is_none());
    }

    #[test]
    fn cancellation_interrupts_slow_response_headers() {
        let (base, server) = mock_server(MockReply {
            path: "/chat/completions".into(),
            body: json!({"choices":[{"message":{"content":"{\"ok\":true}"},"finish_reason":"stop"}]}),
            status: 200,
            headers_delay: Duration::from_millis(900),
            body_delay: Duration::ZERO,
        });
        let provider = NativeVisionProvider::new(ProviderConfig {
            base_url: base,
            model: "custom-vlm".into(),
            api_key: "secret".into(),
            effort: "none".into(),
            thinking_budget: None,
            max_output_tokens: 1000,
        })
        .unwrap();
        let cancelled = Arc::new(AtomicBool::new(false));
        let cancel_flag = Arc::clone(&cancelled);
        let cancel_thread = thread::spawn(move || {
            thread::sleep(Duration::from_millis(80));
            cancel_flag.store(true, Ordering::Release);
        });
        let mut ctx = context(PathBuf::new(), "source_inventory");
        ctx.telemetry_path = None;
        let started = Instant::now();
        let error =
            run(provider.call(&request_messages(), &[image()], &ctx, &cancelled)).unwrap_err();
        let elapsed = started.elapsed();
        assert_eq!(error.kind, "cancelled");
        assert!(
            elapsed < Duration::from_millis(500),
            "cancel took {elapsed:?}"
        );
        cancel_thread.join().unwrap();
        let _ = server.join().unwrap();
    }

    #[test]
    fn cancellation_interrupts_slow_response_body() {
        let (base, server) = mock_server(MockReply {
            path: "/chat/completions".into(),
            body: json!({"choices":[{"message":{"content":"{\"ok\":true}"},"finish_reason":"stop"}]}),
            status: 200,
            headers_delay: Duration::ZERO,
            body_delay: Duration::from_millis(900),
        });
        let provider = NativeVisionProvider::new(ProviderConfig {
            base_url: base,
            model: "custom-vlm".into(),
            api_key: "secret".into(),
            effort: "none".into(),
            thinking_budget: None,
            max_output_tokens: 1000,
        })
        .unwrap();
        let cancelled = Arc::new(AtomicBool::new(false));
        let cancel_flag = Arc::clone(&cancelled);
        let cancel_thread = thread::spawn(move || {
            thread::sleep(Duration::from_millis(80));
            cancel_flag.store(true, Ordering::Release);
        });
        let mut ctx = context(PathBuf::new(), "source_inventory");
        ctx.telemetry_path = None;
        let started = Instant::now();
        let error =
            run(provider.call(&request_messages(), &[image()], &ctx, &cancelled)).unwrap_err();
        let elapsed = started.elapsed();
        assert_eq!(error.kind, "cancelled");
        assert!(
            elapsed < Duration::from_millis(500),
            "cancel took {elapsed:?}"
        );
        cancel_thread.join().unwrap();
        let _ = server.join().unwrap();
    }
}
