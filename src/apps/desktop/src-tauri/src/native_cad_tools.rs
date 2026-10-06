//! Native Cordis CAD write and preview adapter. The DSH Agent chooses tools.
use crate::{
    cad_plan_validation,
    native_cad_session::{self, SessionCommand},
    native_fastener_specs::{self, find_bolt_size_quote},
    native_geometry_backend, native_vision_assets,
};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{fs, path::PathBuf, sync::atomic::AtomicBool};

const FACE_TOOL_SOURCE_TYPES: &[&str] = &["image_estimate", "user", "derived"];

pub struct Context {
    pub project_root: PathBuf,
    pub session_dir: PathBuf,
    pub runtime_manifest: Option<PathBuf>,
    pub user_turn_id: Option<String>,
}
pub struct ToolOutput {
    pub state: Value,
    pub value: Value,
    pub images: Vec<Value>,
}

pub fn supports(name: &str) -> bool {
    matches!(
        name,
        "cad_create_model"
            | "cad_apply_recipe"
            | "cad_add_feature"
            | "cad_update_feature"
            | "cad_remove_feature"
            | "cad_add_face_recess"
            | "cad_add_hole_array"
            | "cad_validate_model"
            | "cad_preview_model"
            | "cad_restore_best_candidate"
            | "cad_get_reference_image"
            | "cad_finish_model"
    )
}

pub fn run(
    state: &Value,
    name: &str,
    args: &Value,
    ctx: &Context,
    cancelled: &AtomicBool,
) -> Result<ToolOutput, String> {
    check_args(
        args,
        match name {
            "cad_create_model" => &["name", "first_feature", "source_prompt", "units", "replace"],
            "cad_apply_recipe" => &["recipe_id", "size", "name", "source_prompt", "replace"],
            "cad_add_feature" => &["feature", "finding_ids"],
            "cad_update_feature" => &[
                "feature_id",
                "parameter_updates",
                "source_updates",
                "remove_parameters",
                "fields",
                "finding_ids",
            ],
            "cad_remove_feature" => &["feature_id", "finding_ids"],
            "cad_add_face_recess" => &[
                "feature_id",
                "name",
                "face_normal_axis",
                "face_side",
                "face_center",
                "width",
                "height",
                "depth",
                "source_type",
                "turn_id",
                "evidence",
                "finding_ids",
            ],
            "cad_add_hole_array" => &[
                "face_normal_axis",
                "face_side",
                "source_type",
                "turn_id",
                "evidence",
                "finding_ids",
                "holes",
            ],
            "cad_validate_model" => &[],
            "cad_preview_model" => &["view", "reference_id"],
            "cad_restore_best_candidate" => &["reason"],
            "cad_get_reference_image" => &[
                "reference_id",
                "reference_image_index",
                "roi_norm",
                "evidence_id",
            ],
            "cad_finish_model" => &["summary", "unresolved_acknowledgement"],
            _ => return Err(format!("未知原生 CAD 工具：{name}")),
        },
    )?;
    let comparison_reference = if name == "cad_preview_model" {
        args["reference_id"]
            .as_str()
            .map(|reference_id| {
                native_vision_assets::reference_input(
                    &ctx.session_dir.join("reference-images.json"),
                    Some(reference_id),
                    None,
                    None,
                )
            })
            .transpose()?
    } else {
        None
    };
    if name == "cad_finish_model" {
        let summary = string(args, "summary")?;
        let references = ctx.session_dir.join("reference-images.json");
        let turns = ctx.session_dir.join("user-turns.json");
        let (assessed, _) = crate::native_review_gate::aggregate(state, &references, &turns)?;
        let (mut next, dispatch) =
            native_cad_session::finish_assessment(&assessed, &references, &turns)?;
        let output_paths = next["output_paths"].clone();
        let root = ctx.session_dir.canonicalize().map_err(|e| e.to_string())?;
        for path in output_paths
            .as_object()
            .ok_or("当前模型输出未生成。")?
            .values()
        {
            let path = PathBuf::from(path.as_str().ok_or("输出路径不是字符串。")?)
                .canonicalize()
                .map_err(|e| e.to_string())?;
            if !path.starts_with(&root) || !path.is_file() {
                return Err("完成模型只允许引用当前会话中真实存在的文件。".into());
            }
        }
        next = native_cad_session::complete_export(
            &next,
            native_cad_session::ExportOutcome {
                revision: dispatch.revision,
                geometry_fingerprint: dispatch.geometry_fingerprint,
                output_paths,
            },
        )?;
        let evidence = crate::native_finish_evidence::current_snapshot(&next, &references, &turns)?;
        next["finish_summary"]["evidence_fingerprint"] = json!(evidence.fingerprint);
        next["agent_finish_description"] = json!(summary);
        return Ok(ToolOutput {
            value: json!({"finish":next["finish_summary"],"output_paths":next["output_paths"],"catia_executed":false}),
            state: next,
            images: vec![],
        });
    }
    if matches!(name, "cad_add_face_recess" | "cad_add_hole_array")
        && state["plan"]["units"]["length"] != "mm"
    {
        return Err("面便利工具当前只支持毫米计划；请用通用feature工具明确单位建模。".into());
    }
    if name == "cad_get_reference_image" {
        let index = args
            .get("reference_image_index")
            .map(|v| {
                v.as_u64()
                    .filter(|n| *n > 0)
                    .ok_or("reference_image_index须为正整数。")
            })
            .transpose()?
            .map(|n| n as usize);
        let reference = native_vision_assets::reference_input(
            &ctx.session_dir.join("reference-images.json"),
            args["reference_id"].as_str(),
            index,
            args.get("roi_norm").filter(|v| !v.is_null()),
        )?;
        let mut image = cordis_image_result(&reference.image.mime, &reference.image.bytes);
        image["reference_id"] = json!(reference.image.reference_id);
        image["source_sha256"] = json!(reference.image.source_sha256);
        image["transport_sha256"] = json!(reference.image.transport_sha256);
        image["roi_norm"] = json!(reference.image.roi_norm);
        return Ok(ToolOutput {
            state: state.clone(),
            value: reference.metadata,
            images: vec![image],
        });
    }
    if name == "cad_validate_model" {
        return Ok(ToolOutput {
            state: state.clone(),
            value: cad_plan_validation::validate_plan(
                state.get("plan").ok_or("当前没有CADPlan。")?,
            ),
            images: vec![],
        });
    }
    if name == "cad_preview_model" {
        let view = args["view"].as_str().unwrap_or("iso");
        if !["iso", "front", "right", "top", "back", "left", "bottom"].contains(&view) {
            return Err(
                "原生模型预览当前只支持 iso/front/right/top/back/left/bottom 单视图。".into(),
            );
        }
    }
    let mut commands = Vec::new();
    let mut initial = state.clone();
    if matches!(name, "cad_create_model" | "cad_apply_recipe")
        && args.get("replace").and_then(Value::as_bool) == Some(true)
        && initial["plan"].is_object()
    {
        let previous = initial.clone();
        initial = json!({"plan":null,"revision":previous["revision"],"actions":previous["actions"],
            "previous_model":previous,"reference_inventory":state["reference_inventory"],
            "has_references":state["has_references"]});
    }
    match name {
        "cad_create_model" => {
            if let Some(units) = args.get("units") {
                validate_units(units)?;
            }
            if args
                .get("source_prompt")
                .is_some_and(|value| !value.is_string())
            {
                return Err("source_prompt须为字符串。".into());
            }
            if args.get("replace").is_some_and(|value| !value.is_boolean()) {
                return Err("replace须为布尔值。".into());
            }
            commands.push(SessionCommand::Create {
                name: string(args, "name")?,
                first_feature: feature_with_turn_id(
                    required(args, "first_feature")?.clone(),
                    ctx.user_turn_id.as_deref(),
                )?,
                source_prompt: args["source_prompt"].as_str().unwrap_or("").into(),
                units: args.get("units").cloned(),
            });
        }
        "cad_apply_recipe" => {
            if args.get("replace").is_some_and(|value| !value.is_boolean()) {
                return Err("replace须为布尔值。".into());
            }
            let (name, source_prompt, feature) = recipe_feature(args, ctx)?;
            commands.push(SessionCommand::Create {
                name,
                first_feature: feature,
                source_prompt,
                units: None,
            });
        }
        "cad_add_feature" => commands.push(SessionCommand::Add {
            feature: feature_with_turn_id(
                required(args, "feature")?.clone(),
                ctx.user_turn_id.as_deref(),
            )?,
            finding_ids: strings(args, "finding_ids")?,
        }),
        "cad_update_feature" => {
            let feature_id = string(args, "feature_id")?;
            let parameter_updates = optional_object(args, "parameter_updates")?;
            let source_updates = optional_object(args, "source_updates")?;
            let fields = optional_object(args, "fields")?;
            let remove_parameters = strings(args, "remove_parameters")?;
            validate_update_contract(
                state,
                &feature_id,
                &parameter_updates,
                &source_updates,
                &remove_parameters,
                &fields,
            )?;
            commands.push(SessionCommand::Update {
                feature_id,
                parameter_updates,
                source_updates: sources_with_turn_id(source_updates, ctx.user_turn_id.as_deref())?,
                remove_parameters,
                fields,
                finding_ids: strings(args, "finding_ids")?,
            });
        }
        "cad_remove_feature" => commands.push(SessionCommand::Remove {
            feature_id: string(args, "feature_id")?,
            finding_ids: strings(args, "finding_ids")?,
        }),
        "cad_restore_best_candidate" => {
            let _reason = string(args, "reason")?;
            commands.push(SessionCommand::RestoreBest);
        }
        "cad_preview_model" => commands.push(SessionCommand::RequestPreview { build: false }),
        "cad_add_face_recess" => commands.push(SessionCommand::Add {
            feature: face_recess(state, args, ctx.user_turn_id.as_deref())?,
            finding_ids: strings(args, "finding_ids")?,
        }),
        "cad_add_hole_array" => {
            for feature in hole_array(state, args, ctx.user_turn_id.as_deref())? {
                commands.push(SessionCommand::Add {
                    feature,
                    finding_ids: strings(args, "finding_ids")?,
                });
            }
        }
        _ => return Err(format!("不支持原生CAD工具：{name}")),
    }
    let original_revision = state["revision"].as_i64().unwrap_or(0);
    let mut next = initial;
    for command in commands {
        next = native_cad_session::apply(&next, command)?.state;
    }
    let revision = next["revision"].as_i64().ok_or("当前版本无效。")?;
    let changed = revision != original_revision;
    let mut images = vec![];
    if changed || name == "cad_preview_model" {
        let manifest = ctx
            .runtime_manifest
            .as_ref()
            .ok_or("原生OCCT runtime尚未配置，拒绝调用Python几何fallback。")?;
        let generation = state
            .get("preview_generation")
            .and_then(Value::as_u64)
            .unwrap_or(0)
            .checked_add(1)
            .ok_or("预览generation已溢出。")?;
        let output = native_geometry_backend::build_preview(
            &next["plan"],
            revision,
            generation,
            manifest,
            &ctx.session_dir,
            cancelled,
        )?;
        next = native_cad_session::complete_preview(&next, output.outcome)?.state;
        next["previews"] = json!(output.views);
        next["preview_generation"] = json!(generation);
    }
    let view = args["view"].as_str().unwrap_or("iso");
    let partial_preview = next["last_preview"]["local_geometry_checks"]["state"] == "partial";
    let mut value = json!({"state":if partial_preview {"candidate_partial_not_accepted"} else {"candidate"},
        "partial_candidate_diagnostic_only":partial_preview,
        "revision":revision,"changed":changed,"geometry_fingerprint":next["last_preview"]["geometry_fingerprint"],
        "preview_generation":next["preview_generation"],"preview":next["last_preview"],"views":next["previews"],
        "features":next["plan"]["features"],"output_paths":next["output_paths"],"catia_executed":false});
    if name == "cad_preview_model" {
        let row = next["previews"]
            .as_array()
            .and_then(|rows| rows.iter().find(|r| r["view"] == view))
            .ok_or("view必须为iso/front/right/top/back/left/bottom。")?;
        let path = row["preview_image_path"].as_str().ok_or("视图路径缺失。")?;
        let bytes = fs::read(path).map_err(|e| e.to_string())?;
        let mut image = cordis_image_result("image/png", &bytes);
        image["view"] = json!(view);
        image["revision"] = json!(revision);
        image["preview_generation"] = next["preview_generation"].clone();
        image["preview_sha256"] = row["preview_sha256"].clone();
        if let Some(reference) = comparison_reference.as_ref() {
            append_comparison_reference(
                &mut images,
                &mut value,
                reference,
                view,
                revision,
                &next["preview_generation"],
                &row["preview_sha256"],
            )?;
        }
        images.push(image);
    }
    Ok(ToolOutput {
        state: next,
        value,
        images,
    })
}

fn required<'a>(v: &'a Value, key: &str) -> Result<&'a Value, String> {
    v.get(key)
        .filter(|v| !v.is_null())
        .ok_or_else(|| format!("缺少{key}。"))
}

/// Serialize image results using the Cordis attachment bridge's camelCase wire contract.
fn cordis_image_result(media_type: &str, bytes: &[u8]) -> Value {
    json!({"mediaType":media_type,"data":STANDARD.encode(bytes)})
}

fn append_comparison_reference(
    images: &mut Vec<Value>,
    value: &mut Value,
    reference: &native_vision_assets::ReferenceInput,
    view: &str,
    revision: i64,
    preview_generation: &Value,
    preview_sha256: &Value,
) -> Result<(), String> {
    images.push(cordis_image_result(
        &reference.image.mime,
        &reference.image.bytes,
    ));
    value["comparison_attachments"] = json!({
        "reference_id":reference.image.reference_id,
        "reference_sha256":reference.image.source_sha256,
        "order":["original_reference","current_model_single_view"],
        "view":view,
        "revision":revision,
        "preview_generation":preview_generation,
        "preview_sha256":preview_sha256
    });
    Ok(())
}

fn check_args(value: &Value, allowed: &[&str]) -> Result<(), String> {
    let object = value.as_object().ok_or("工具参数必须为 JSON 对象。")?;
    if let Some(key) = object.keys().find(|key| !allowed.contains(&key.as_str())) {
        return Err(format!("原生工具收到未声明参数 {key}。"));
    }
    Ok(())
}

fn optional_object(value: &Value, key: &str) -> Result<Value, String> {
    match value.get(key) {
        None => Ok(Value::Null),
        Some(Value::Object(_)) => Ok(value[key].clone()),
        Some(_) => Err(format!("{key}须为JSON对象。")),
    }
}

fn validate_units(units: &Value) -> Result<(), String> {
    let object = units.as_object().ok_or("units须为JSON对象。")?;
    if object.keys().any(|key| key != "length" && key != "angle") {
        return Err("units只接受length和angle。".into());
    }
    if !object
        .get("length")
        .and_then(Value::as_str)
        .is_some_and(|unit| ["mm", "cm", "m", "in"].contains(&unit))
    {
        return Err("units.length须为mm、cm、m或in。".into());
    }
    if !object
        .get("angle")
        .and_then(Value::as_str)
        .is_some_and(|unit| ["deg", "rad"].contains(&unit))
    {
        return Err("units.angle须为deg或rad。".into());
    }
    Ok(())
}

fn validate_feature_relations(feature: &Value) -> Result<(), String> {
    let Some(value) = feature.get("relations") else {
        return Ok(());
    };
    let rows = value.as_array().ok_or("relations须为数组。")?;
    for (index, row) in rows.iter().enumerate() {
        let relation = row
            .as_object()
            .ok_or_else(|| format!("relations[{index}]须为对象。"))?;
        if relation
            .keys()
            .any(|key| key != "type" && key != "feature_id")
        {
            return Err(format!("relations[{index}]只接受type和feature_id。"));
        }
        if !relation
            .get("type")
            .and_then(Value::as_str)
            .is_some_and(|kind| ["depends_on", "pattern_source"].contains(&kind))
        {
            return Err(format!(
                "relations[{index}].type须为depends_on或pattern_source。"
            ));
        }
        if !relation
            .get("feature_id")
            .and_then(Value::as_str)
            .is_some_and(|id| !id.trim().is_empty())
        {
            return Err(format!("relations[{index}].feature_id须为非空字符串。"));
        }
    }
    Ok(())
}

fn validate_source_values(sources: &Value, optional: bool) -> Result<(), String> {
    if optional && sources.is_null() {
        return Ok(());
    }
    let rows = sources
        .as_object()
        .ok_or("sources须为参数名到来源对象的JSON对象。")?;
    for (parameter, source) in rows {
        let source = source
            .as_object()
            .ok_or_else(|| format!("sources.{parameter}须为来源对象。"))?;
        if source
            .keys()
            .any(|key| !["type", "evidence", "note", "turn_id"].contains(&key.as_str()))
        {
            return Err(format!(
                "sources.{parameter}只接受type、evidence、note和临时turn_id。"
            ));
        }
        let source_type = source
            .get("type")
            .and_then(Value::as_str)
            .filter(|kind| ["user", "image_estimate", "derived", "existing_model"].contains(kind))
            .ok_or_else(|| format!("sources.{parameter}.type无效。"))?;
        for key in ["evidence", "note", "turn_id"] {
            if source.get(key).is_some_and(|value| !value.is_string()) {
                return Err(format!("sources.{parameter}.{key}须为字符串。"));
            }
        }
        if source_type == "image_estimate"
            && !source.get("evidence").and_then(Value::as_str).is_some()
        {
            return Err(format!(
                "sources.{parameter}.evidence是image_estimate必填项。"
            ));
        }
        if source_type == "existing_model" && !source.get("note").and_then(Value::as_str).is_some()
        {
            return Err(format!("sources.{parameter}.note是existing_model必填项。"));
        }
        if source.contains_key("turn_id") && source_type != "user" {
            return Err("turn_id只能绑定type=user的参数来源。".into());
        }
    }
    Ok(())
}

fn validate_update_contract(
    state: &Value,
    feature_id: &str,
    parameter_updates: &Value,
    source_updates: &Value,
    remove_parameters: &[String],
    fields: &Value,
) -> Result<(), String> {
    let parameters = if parameter_updates.is_null() {
        None
    } else {
        Some(
            parameter_updates
                .as_object()
                .ok_or("parameter_updates须为JSON对象。")?,
        )
    };
    let sources = if source_updates.is_null() {
        None
    } else {
        validate_source_values(source_updates, false)?;
        Some(
            source_updates
                .as_object()
                .ok_or("source_updates须为JSON对象。")?,
        )
    };
    if let Some(fields) = fields.as_object() {
        for (key, value) in fields {
            if !["name", "note"].contains(&key.as_str()) {
                return Err(format!("fields只允许name和note，收到未声明字段{key}。"));
            }
            if !value.is_string() {
                return Err(format!("fields.{key}须为字符串。"));
            }
        }
    } else if !fields.is_null() {
        return Err("fields须为JSON对象。".into());
    }
    let Some(plan) = state.get("plan").filter(|value| value.is_object()) else {
        return Ok(());
    };
    let Some(feature) = plan
        .get("features")
        .and_then(Value::as_array)
        .and_then(|features| {
            features
                .iter()
                .find(|feature| feature.get("id").and_then(Value::as_str) == Some(feature_id))
        })
    else {
        return Ok(());
    };
    let kind = feature
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let schema = cad_plan_validation::feature_schema(kind)
        .ok_or_else(|| format!("特征类型{kind}没有CADPlan参数schema。"))?;
    let allowed_parameters = schema
        .pointer("/properties/parameters/properties")
        .and_then(Value::as_object)
        .ok_or("CADPlan特征schema缺少parameters属性。")?;
    if let Some(updates) = parameters {
        cad_plan_validation::validate_feature_parameter_values(kind, updates)?;
    }
    for key in remove_parameters {
        if !allowed_parameters.contains_key(key) {
            return Err(format!("特征类型{kind}不支持移除参数{key}。"));
        }
    }
    if let Some(sources) = sources {
        let current_parameters = feature.get("parameters").and_then(Value::as_object);
        for key in sources.keys() {
            if !current_parameters.is_some_and(|map| map.contains_key(key))
                && !parameters.is_some_and(|map| map.contains_key(key))
            {
                return Err(format!(
                    "source_updates.{key}没有对应的现有或本次更新参数。"
                ));
            }
        }
    }
    Ok(())
}

fn string(v: &Value, key: &str) -> Result<String, String> {
    required(v, key)?
        .as_str()
        .filter(|s| !s.trim().is_empty())
        .map(str::to_owned)
        .ok_or_else(|| format!("{key}须为非空字符串。"))
}
fn number(v: &Value, key: &str) -> Result<f64, String> {
    required(v, key)?
        .as_f64()
        .filter(|n| n.is_finite())
        .ok_or_else(|| format!("{key}须为有限数值。"))
}
fn strings(v: &Value, key: &str) -> Result<Vec<String>, String> {
    match v.get(key) {
        None | Some(Value::Null) => Ok(vec![]),
        Some(Value::Array(rows)) => rows
            .iter()
            .map(|s| {
                s.as_str()
                    .map(str::to_owned)
                    .ok_or("数组必须只包含字符串。".into())
            })
            .collect(),
        _ => Err(format!("{key}须为字符串数组。")),
    }
}

fn feature_with_turn_id(mut feature: Value, turn_id: Option<&str>) -> Result<Value, String> {
    if !feature.is_object() {
        return Err("feature须为JSON对象。".into());
    }
    validate_feature_relations(&feature)?;
    let kind = feature
        .get("kind")
        .and_then(Value::as_str)
        .ok_or("feature.kind须为字符串。")?;
    let parameters = feature
        .get("parameters")
        .and_then(Value::as_object)
        .ok_or("feature.parameters须为JSON对象。")?;
    cad_plan_validation::validate_feature_parameter_values(kind, parameters)?;
    let parameter_names = parameters
        .keys()
        .cloned()
        .collect::<std::collections::HashSet<_>>();
    let sources = feature
        .get_mut("sources")
        .ok_or("feature.sources为必填来源对象。")?;
    validate_source_values(sources, false)?;
    if let Some(source_map) = sources.as_object() {
        if source_map.keys().any(|key| !parameter_names.contains(key)) {
            return Err("feature.sources不得引用parameters中不存在的参数。".into());
        }
    }
    if let Some(sources) = sources.as_object_mut() {
        for source in sources.values_mut() {
            bind_source_turn_id(source, turn_id)?;
        }
    }
    Ok(feature)
}

fn sources_with_turn_id(mut sources: Value, turn_id: Option<&str>) -> Result<Value, String> {
    validate_source_values(&sources, true)?;
    if let Some(sources) = sources.as_object_mut() {
        for source in sources.values_mut() {
            bind_source_turn_id(source, turn_id)?;
        }
    }
    Ok(sources)
}

fn bind_source_turn_id(source: &mut Value, turn_id: Option<&str>) -> Result<(), String> {
    let Some(object) = source.as_object_mut() else {
        return Ok(());
    };
    if object.get("type").and_then(Value::as_str) != Some("user") {
        if object.contains_key("turn_id") {
            return Err("turn_id 只能绑定 type=user 的参数来源。".into());
        }
        return Ok(());
    }
    let supplied = object.get("turn_id").and_then(Value::as_str);
    if let Some(trusted) = turn_id.filter(|value| !value.trim().is_empty()) {
        if supplied.is_some_and(|value| value != trusted) {
            return Err("参数来源 turn_id 与当前用户输入不匹配。".into());
        }
        object.insert("turn_id".into(), json!(trusted));
    } else if !supplied.is_some_and(|value| !value.trim().is_empty()) {
        return Err("用户来源缺少由 Host 绑定的可信 turn_id。".into());
    }
    Ok(())
}

fn recipe_feature(args: &Value, ctx: &Context) -> Result<(String, String, Value), String> {
    if string(args, "recipe_id")? != "fastener.socket_head_cap_screw" {
        return Err("当前 Rust 配方执行器只支持 fastener.socket_head_cap_screw。".into());
    }
    let size = string(args, "size")?;
    let (diameter, length) = native_fastener_specs::parse_bolt_size(&size)?;
    let standard_path = ctx.project_root.join("kb/bolt_std.json");
    let data: Value = serde_json::from_slice(
        &fs::read(&standard_path).map_err(|error| format!("读取螺钉标准表失败：{error}"))?,
    )
    .map_err(|error| format!("螺钉标准表 JSON 无效：{error}"))?;
    let spec_key = format!("M{}", diameter as u64);
    let row = data["bolts"]
        .get(&spec_key)
        .ok_or_else(|| format!("标准表中没有 {spec_key}；未猜测替代尺寸。"))?;
    let value = |key: &str| -> Result<f64, String> {
        row.get(key)
            .and_then(Value::as_f64)
            .filter(|value| value.is_finite() && *value > 0.0)
            .ok_or_else(|| format!("标准表 {spec_key} 缺少有效参数 {key}。"))
    };
    let turn_id = ctx
        .user_turn_id
        .as_deref()
        .filter(|value| !value.trim().is_empty())
        .ok_or("执行用户标准规格需要当前可信 user turn。")?;
    let turns_path = ctx.session_dir.join("user-turns.json");
    let turns: Value = serde_json::from_slice(
        &fs::read(&turns_path).map_err(|error| format!("读取当前用户轮次失败：{error}"))?,
    )
    .map_err(|error| format!("当前用户轮次 JSON 无效：{error}"))?;
    let prompt = turns["turns"]
        .as_array()
        .and_then(|rows| {
            rows.iter()
                .find(|row| row["turn_id"].as_str() == Some(turn_id))
        })
        .and_then(|row| row["raw_user_prompt"].as_str())
        .ok_or("当前 turn 不存在可信用户原文。")?;
    let size_evidence = find_bolt_size_quote(prompt, diameter, length)
        .ok_or("当前用户原文中找不到与配方规格一致的逐字 M 规格；拒绝标记为用户给定。")?;
    let model = format!("{}×{}", spec_key, length);
    let standard = data["standard"].as_str().unwrap_or("ISO 4762 / GB/T 70.1");
    let size_source = json!({"type":"user","evidence":size_evidence,"turn_id":turn_id});
    let derived_source = |description: String| json!({"type":"derived","evidence":description});
    let parameters = json!({
        "shank_d":diameter,
        "thread_pitch":value("p")?,
        "length":length,
        "head_d":value("dk")?,
        "head_h":value("k")?,
        "hex_s":value("s")?,
        "hex_t":value("t")?,
        "center":[0.0,0.0,0.0],
        "segments":48
    });
    let sources = json!({
        "shank_d":size_source.clone(),
        "length":size_source,
        "thread_pitch":derived_source(format!("{standard} 标准表 {spec_key} 粗牙螺距。")),
        "head_d":derived_source(format!("{standard} 标准表 {spec_key}。")),
        "head_h":derived_source(format!("{standard} 标准表 {spec_key}。")),
        "hex_s":derived_source(format!("{standard} 标准表 {spec_key}。")),
        "hex_t":derived_source(format!("{standard} 标准表 {spec_key}。")),
        "center":{"type":"derived","evidence":"标准件轴线采用 CADPlan 全局 Z 轴、原点定位约定。"},
        "segments":{"type":"derived","evidence":"本地近似螺纹网格离散精度。"}
    });
    let length_series = data["length_series"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let nearest = length_series
        .iter()
        .filter_map(Value::as_f64)
        .min_by(|a, b| (a - length).abs().total_cmp(&(b - length).abs()));
    let length_note = nearest
        .filter(|value| (*value - length).abs() > 1e-9)
        .map(|value| format!("长度 {length} mm 不在标准系列内；最近为 {value} mm。"))
        .unwrap_or_default();
    let feature = json!({
        "id":"socket-head-cap-screw","kind":"socket_head_bolt","operation":"add",
        "name":format!("内六角圆柱头螺钉 {model}"),
        "note":format!("{standard}；右旋近似螺纹，螺距 {} mm。CATIA 导出仍需单独核验螺纹。 {length_note}",value("p")?),
        "parameters":parameters,"sources":sources
    });
    let name = args["name"]
        .as_str()
        .filter(|v| !v.trim().is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| format!("内六角圆柱头螺钉 {model}"));
    let source_prompt = args["source_prompt"]
        .as_str()
        .filter(|v| !v.trim().is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| format!("标准件配方：内六角圆柱头螺钉 {model}"));
    Ok((name, source_prompt, feature))
}

fn pair(value: &Value) -> Result<[f64; 2], String> {
    let rows = value
        .as_array()
        .filter(|a| a.len() == 2)
        .ok_or("面内坐标须有两个数值。")?;
    Ok([
        rows[0]
            .as_f64()
            .filter(|v| v.is_finite())
            .ok_or("面内坐标非有限数。")?,
        rows[1]
            .as_f64()
            .filter(|v| v.is_finite())
            .ok_or("面内坐标非有限数。")?,
    ])
}
fn axes(args: &Value) -> Result<(usize, [usize; 2], f64), String> {
    let axis = match args["face_normal_axis"]
        .as_str()
        .unwrap_or("")
        .to_ascii_uppercase()
        .as_str()
    {
        "X" => 0,
        "Y" => 1,
        "Z" => 2,
        _ => return Err("面法线必须是X/Y/Z。".into()),
    };
    let sign = match args["face_side"].as_str() {
        Some("positive") => 1.,
        Some("negative") => -1.,
        _ => return Err("face_side必须为positive/negative。".into()),
    };
    Ok((
        axis,
        match axis {
            0 => [1, 2],
            1 => [0, 2],
            _ => [0, 1],
        },
        sign,
    ))
}
fn bounds(state: &Value) -> Result<[f64; 6], String> {
    let rows = state["last_preview"]["local_geometry_checks"]["bounds_mm"]
        .as_array()
        .filter(|a| a.len() == 6)
        .ok_or("面工具需要当前实体的真实包围盒；请先预览。")?;
    let mut result = [0.; 6];
    for (i, row) in rows.iter().enumerate() {
        result[i] = row
            .as_f64()
            .filter(|v| v.is_finite())
            .ok_or("包围盒无效。")?;
    }
    Ok(result)
}
fn source(args: &Value, user_turn_id: Option<&str>) -> Result<Value, String> {
    let kind = args["source_type"].as_str().unwrap_or("image_estimate");
    if !FACE_TOOL_SOURCE_TYPES.contains(&kind) {
        return Err("面工具source_type无效。".into());
    }
    let mut source = json!({"type":kind,"evidence":string(args,"evidence")?});
    if kind == "user" {
        let turn_id = match user_turn_id.filter(|value| !value.trim().is_empty()) {
            Some(value) => value.to_owned(),
            None => string(args, "turn_id")?,
        };
        source["turn_id"] = json!(turn_id);
    }
    Ok(source)
}
fn face_recess(state: &Value, args: &Value, user_turn_id: Option<&str>) -> Result<Value, String> {
    let (axis, plane, sign) = axes(args)?;
    let b = bounds(state)?;
    let uv = pair(&args["face_center"])?;
    let (width, height, depth) = (
        number(args, "width")?,
        number(args, "height")?,
        number(args, "depth")?,
    );
    if width <= 0. || height <= 0. || depth <= 0. || depth >= b[axis + 3] - b[axis] {
        return Err("凹槽须为正尺寸且保留连续背壁。".into());
    }
    for (i, size) in [width, height].iter().enumerate() {
        let a = plane[i];
        if uv[i] - size / 2. < b[a] - 1e-6 || uv[i] + size / 2. > b[a + 3] + 1e-6 {
            return Err("凹槽面内范围超出实体。".into());
        }
    }
    let mut center = [0.; 3];
    let mut dimensions = [0.; 3];
    center[plane[0]] = uv[0];
    center[plane[1]] = uv[1];
    dimensions[plane[0]] = width;
    dimensions[plane[1]] = height;
    dimensions[axis] = depth + 0.001;
    center[axis] = if sign > 0. { b[axis + 3] } else { b[axis] } - sign * (depth - 0.001) / 2.;
    let shared = source(args, user_turn_id)?;
    let keys = ["length", "width", "height"];
    let mut sources =
        json!({"center":{"type":"derived","evidence":"由真实表面和凹槽深度计算全局切除中心。"}});
    sources[keys[plane[0]]] = shared.clone();
    sources[keys[plane[1]]] = shared;
    sources[keys[axis]] = json!({"type":"derived","evidence":"凹槽法线深度加0.001毫米布尔余量。"});
    Ok(
        json!({"id":string(args,"feature_id")?,"kind":"box","operation":"cut","name":args["name"].as_str().unwrap_or("Face recess"),
        "parameters":{"length":dimensions[0],"width":dimensions[1],"height":dimensions[2],"center":center},"sources":sources}),
    )
}
fn hole_array(
    state: &Value,
    args: &Value,
    user_turn_id: Option<&str>,
) -> Result<Vec<Value>, String> {
    let (axis, plane, sign) = axes(args)?;
    let b = bounds(state)?;
    let shared = source(args, user_turn_id)?;
    let rows = args["holes"]
        .as_array()
        .filter(|a| !a.is_empty() && a.len() <= 4096)
        .ok_or("holes须为非空数组，最多4096孔。")?;
    let mut ids = std::collections::HashSet::new();
    let mut result = Vec::new();
    for row in rows {
        let id = string(row, "id")?;
        if !ids.insert(id.clone()) {
            return Err("孔数组id重复；整批不应用。".into());
        }
        let uv = pair(&row["face_center"])?;
        let diameter = number(row, "diameter")?;
        let setback = row
            .get("face_setback")
            .map(|_| number(row, "face_setback"))
            .transpose()?
            .unwrap_or(0.);
        if diameter <= 0. || setback < 0. || setback >= b[axis + 3] - b[axis] {
            return Err("孔径或退台值无效。".into());
        }
        let mut point = [0.; 3];
        point[plane[0]] = uv[0];
        point[plane[1]] = uv[1];
        point[axis] = if sign > 0. { b[axis + 3] } else { b[axis] } - sign * setback;
        for a in plane {
            if point[a] - diameter / 2. < b[a] - 1e-6 || point[a] + diameter / 2. > b[a + 3] + 1e-6
            {
                return Err("孔边缘超出实体面。".into());
            }
        }
        result.push(json!({"id":id,"kind":"hole","operation":"cut","parameters":{"diameter":diameter,"through":true,"pos":"point","point":point,"axis":(["X","Y","Z"][axis])},
            "sources":{"diameter":shared,"point":{"type":"derived","evidence":"面内孔位和退台量映射为全局XYZ。"}}}));
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preview_comparison_attaches_original_bytes_before_single_view_with_sha_binding() {
        use image::ImageFormat;
        use sha2::{Digest, Sha256};
        use std::io::Cursor;

        let root = std::env::temp_dir().join(format!(
            "native-preview-paired-reference-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        let original = root.join("original.png");
        let mut encoded = Cursor::new(Vec::new());
        image::DynamicImage::ImageRgb8(image::RgbImage::from_pixel(2, 1, image::Rgb([7, 11, 13])))
            .write_to(&mut encoded, ImageFormat::Png)
            .unwrap();
        let original_bytes = encoded.into_inner();
        fs::write(&original, &original_bytes).unwrap();
        let sha = format!("{:x}", Sha256::digest(&original_bytes));
        let id = format!("sha256:{sha}");
        fs::write(
            root.join("reference-images.json"),
            json!({"schema_version":1,"images":[{
                "id":id,"sha256":sha,"path":original,"filename":"original.png",
                "media_type":"image/png"
            }]})
            .to_string(),
        )
        .unwrap();

        let mut images = Vec::new();
        let mut value = json!({"state":"candidate"});
        append_comparison_reference(
            &mut images,
            &mut value,
            &native_vision_assets::reference_input(
                &root.join("reference-images.json"),
                Some(&id),
                None,
                None,
            )
            .unwrap(),
            "front",
            9,
            &json!(4),
            &json!("preview-sha"),
        )
        .unwrap();
        images.push(cordis_image_result("image/png", b"current-preview"));

        assert_eq!(images.len(), 2);
        assert_eq!(images[0]["data"], STANDARD.encode(&original_bytes));
        assert_eq!(images[0]["mediaType"], "image/png");
        assert_eq!(images[1]["data"], STANDARD.encode(b"current-preview"));
        assert_eq!(value["comparison_attachments"]["reference_id"], id);
        assert_eq!(value["comparison_attachments"]["reference_sha256"], sha);
        assert_eq!(
            value["comparison_attachments"]["order"],
            json!(["original_reference", "current_model_single_view"])
        );
        assert_eq!(value["comparison_attachments"]["view"], "front");
        assert_eq!(value["comparison_attachments"]["preview_generation"], 4);
        assert!(
            native_vision_assets::reference_input(
                &root.join("reference-images.json"),
                Some("sha256:invented"),
                None,
                None,
            )
            .is_err()
        );
        fs::remove_dir_all(root).unwrap();
    }

    fn box_feature() -> Value {
        json!({
            "id":"body","kind":"box","operation":"add",
            "parameters":{"length":10,"width":8,"height":5},
            "sources":{
                "length":{"type":"image_estimate","evidence":"front ratio"},
                "width":{"type":"image_estimate","evidence":"side ratio"},
                "height":{"type":"image_estimate","evidence":"front ratio"}
            },
            "relations":[]
        })
    }

    #[test]
    fn create_and_update_dynamic_maps_use_the_authoritative_cadplan_parameter_validator() {
        let feature = box_feature();
        validate_units(&json!({"length":"mm","angle":"deg"})).unwrap();
        validate_feature_relations(&feature).unwrap();
        let first_feature = feature_with_turn_id(feature.clone(), None).unwrap();
        let created = native_cad_session::apply(
            &json!({"revision":0,"plan":null,"actions":[]}),
            SessionCommand::Create {
                name: "fixture".into(),
                first_feature,
                source_prompt: "fixture".into(),
                units: Some(json!({"length":"mm","angle":"deg"})),
            },
        )
        .unwrap()
        .state;
        let source_updates = json!({"length":{"type":"image_estimate","evidence":"updated ratio"}});
        validate_update_contract(
            &created,
            "body",
            &json!({"length":12}),
            &source_updates,
            &[],
            &Value::Null,
        )
        .unwrap();
        let updated = native_cad_session::apply(
            &created,
            SessionCommand::Update {
                feature_id: "body".into(),
                parameter_updates: json!({"length":12}),
                source_updates: source_updates.clone(),
                remove_parameters: vec![],
                fields: Value::Null,
                finding_ids: vec![],
            },
        )
        .unwrap()
        .state;
        assert_eq!(updated["plan"]["features"][0]["parameters"]["length"], 12);
        assert_eq!(
            updated["plan"]["features"][0]["sources"]["length"]["evidence"],
            "updated ratio"
        );
        assert_eq!(
            updated["plan"]["features"][0]["sources"]["width"]["evidence"],
            "side ratio"
        );

        assert!(
            validate_update_contract(
                &created,
                "body",
                &json!({"unknown_dimension":12}),
                &Value::Null,
                &[],
                &Value::Null,
            )
            .unwrap_err()
            .contains("不支持参数")
        );
        assert!(
            validate_update_contract(
                &created,
                "body",
                &json!({"length":"12"}),
                &Value::Null,
                &[],
                &Value::Null,
            )
            .unwrap_err()
            .contains("有限数值")
        );
        assert!(
            validate_update_contract(
                &created,
                "body",
                &json!({"length":0}),
                &Value::Null,
                &[],
                &Value::Null,
            )
            .unwrap_err()
            .contains("必须大于 0")
        );
    }

    #[test]
    fn create_rejects_invalid_units_relations_sources_and_parameter_types() {
        assert!(validate_units(&json!({"length":"ft","angle":"deg"})).is_err());

        let mut bad_relation = box_feature();
        bad_relation["relations"] =
            json!([{"type":"depends_on","feature_id":"prior","extra":true}]);
        assert!(
            validate_feature_relations(&bad_relation)
                .unwrap_err()
                .contains("只接受type和feature_id")
        );

        let mut bad_parameter = box_feature();
        bad_parameter["parameters"]["length"] = json!("wide");
        assert!(
            feature_with_turn_id(bad_parameter, None)
                .unwrap_err()
                .contains("有限数值")
        );

        let mut bad_source = box_feature();
        bad_source["sources"]["length"] = json!({"type":"image_estimate","evidence":3});
        assert!(
            feature_with_turn_id(bad_source, None)
                .unwrap_err()
                .contains("evidence须为字符串")
        );

        let mut extra_source = box_feature();
        extra_source["sources"]["unmodeled"] = json!({"type":"derived"});
        assert!(
            feature_with_turn_id(extra_source, None)
                .unwrap_err()
                .contains("不得引用parameters")
        );
    }

    #[test]
    fn partial_restore_is_not_a_public_native_executor_tool() {
        assert!(!supports("cad_restore_partial_candidate"));
        let error = run(
            &json!({"revision":0,"plan":null}),
            "cad_restore_partial_candidate",
            &json!({"reason":"fixture"}),
            &Context {
                project_root: PathBuf::from("unused"),
                session_dir: PathBuf::from("unused"),
                runtime_manifest: None,
                user_turn_id: None,
            },
            &AtomicBool::new(false),
        )
        .err()
        .unwrap();
        assert!(error.contains("未知原生 CAD 工具"));
    }

    #[test]
    fn image_results_use_the_cordis_attachment_wire_contract() {
        let image = cordis_image_result("image/png", b"png-bytes");
        let serialized = serde_json::to_string(&image).unwrap();
        let wire: Value = serde_json::from_str(&serialized).unwrap();

        assert_eq!(wire["mediaType"], "image/png");
        assert_eq!(wire["data"], STANDARD.encode(b"png-bytes"));
        assert!(wire.get("mime_type").is_none());
    }

    #[test]
    fn face_tool_schema_matches_source_type_and_hole_array_executor_limits() {
        let schemas = crate::native_tool_schemas::native_tool_schemas().unwrap();
        for tool_name in ["cad_add_face_recess", "cad_add_hole_array"] {
            let tool = schemas
                .iter()
                .find(|tool| tool["name"] == tool_name)
                .unwrap();
            let schema_types = tool["inputSchema"]["properties"]["source_type"]["enum"]
                .as_array()
                .unwrap()
                .iter()
                .map(|value| value.as_str().unwrap())
                .collect::<Vec<_>>();
            assert_eq!(schema_types, FACE_TOOL_SOURCE_TYPES);
        }
        let hole_array = schemas
            .iter()
            .find(|tool| tool["name"] == "cad_add_hole_array")
            .unwrap();
        assert_eq!(
            hole_array["inputSchema"]["properties"]["holes"]["maxItems"],
            4096
        );

        for accepted in FACE_TOOL_SOURCE_TYPES {
            assert!(source(&json!({"source_type":accepted,"evidence":"verified evidence","turn_id":"turn-1"}), None).is_ok());
        }
        assert!(
            source(
                &json!({"source_type":"existing_model","evidence":"evidence"}),
                None
            )
            .is_err()
        );
    }

    #[test]
    fn rejects_undeclared_arguments_before_reading_session_state() {
        let error = run(
            &json!({}),
            "cad_validate_model",
            &json!({"guess":true}),
            &Context {
                project_root: PathBuf::from("unused"),
                session_dir: PathBuf::from("unused"),
                runtime_manifest: None,
                user_turn_id: None,
            },
            &AtomicBool::new(false),
        )
        .err()
        .unwrap();
        assert!(error.contains("未声明参数 guess"));
    }

    #[test]
    fn unsupported_composite_preview_fails_before_build_or_writing() {
        let root = std::env::temp_dir().join(format!("native-cad-preview-{}", std::process::id()));
        let error = run(
            &json!({}),
            "cad_preview_model",
            &json!({"view":"three"}),
            &Context {
                project_root: PathBuf::from("unused"),
                session_dir: root.clone(),
                runtime_manifest: None,
                user_turn_id: None,
            },
            &AtomicBool::new(false),
        )
        .err()
        .unwrap();
        assert!(error.contains("只支持"));
        assert!(!root.exists());
    }

    #[test]
    fn cad_preview_model_publishes_only_host_attested_partial_multiview_diagnostics() {
        let Some(manifest) = std::env::var_os("AIMODELER_OCCT_MANIFEST").map(PathBuf::from) else {
            eprintln!("skipped: AIMODELER_OCCT_MANIFEST is not configured");
            return;
        };
        let mut plan: Value = serde_json::from_str(include_str!(
            "../examples/fixtures/native_geometry_plan.json"
        ))
        .unwrap();
        let features = plan["features"].as_array().unwrap().clone();
        let mut body = features[0].clone();
        let mut failed_hole = features[2].clone();
        failed_hole["id"] = json!("failed-hole");
        failed_hole["name"] = json!("failed-hole");
        failed_hole["parameters"]["point"] = json!([10000, 10000, 10000]);
        for feature in [&mut body, &mut failed_hole] {
            if let Some(sources) = feature.get_mut("sources").and_then(Value::as_object_mut) {
                for source in sources.values_mut() {
                    let evidence = source
                        .get("note")
                        .and_then(Value::as_str)
                        .unwrap_or("synthetic OCCT integration fixture")
                        .to_owned();
                    source["type"] = json!("image_estimate");
                    source["evidence"] = json!(evidence);
                }
            }
        }
        plan["features"] = json!([body, failed_hole]);
        let mut first_failure = plan.clone();
        let mut first_cut = first_failure["features"][1].clone();
        first_cut["operation"] = json!("cut");
        first_failure["features"] = json!([first_cut]);
        assert!(crate::native_plan_geometry::build(
            &first_failure,
            &manifest,
            &AtomicBool::new(false)
        )
        .is_err(), "a first-feature failure has no accumulated solid to preview");
        let first_failure_dir = std::env::temp_dir().join(format!(
            "native-cad-first-feature-failure-{}",
            std::process::id()
        ));
        let first_failure_state = json!({"plan":first_failure,"revision":7,
            "preview_generation":12,"actions":[],"output_paths":{}});
        assert!(run(
            &first_failure_state,
            "cad_preview_model",
            &json!({"view":"front"}),
            &Context {
                project_root: PathBuf::from("unused"),
                session_dir: first_failure_dir.clone(),
                runtime_manifest: Some(manifest.clone()),
                user_turn_id: None,
            },
            &AtomicBool::new(false),
        )
        .is_err());
        assert!(!first_failure_dir.join("native-output").exists());

        let mut disjoint_add = plan["features"][0].clone();
        disjoint_add["id"] = json!("disjoint-add");
        disjoint_add["operation"] = json!("add");
        disjoint_add["parameters"]["center"] = json!([500, 500, 500]);
        let mut invalid_candidate = plan.clone();
        invalid_candidate["features"] = json!([plan["features"][0].clone(), disjoint_add]);
        let safe_prefix = crate::native_plan_geometry::build(
            &invalid_candidate,
            &manifest,
            &AtomicBool::new(false),
        )
        .unwrap();
        assert_eq!(safe_prefix.report["state"], "partial");
        assert_eq!(safe_prefix.report["failed_feature_id"], "disjoint-add");
        assert_eq!(safe_prefix.report["applied_feature_ids"], json!(["stock"]));
        assert_eq!(safe_prefix.report["valid"], true);
        assert_eq!(safe_prefix.report["solid_count"], 1);
        assert_eq!(safe_prefix.report["closed_solid_count"], 1);
        let fingerprint = crate::cad_plan::geometry_fingerprint(&plan).unwrap();
        let session_dir = std::env::temp_dir().join(format!(
            "native-cad-partial-preview-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let state = json!({"plan":plan,"revision":7,"preview_generation":12,"actions":[],
            "output_paths":{},"partial_preview":null});
        let result = run(
            &state,
            "cad_preview_model",
            &json!({"view":"front"}),
            &Context {
                project_root: PathBuf::from("unused"),
                session_dir: session_dir.clone(),
                runtime_manifest: Some(manifest),
                user_turn_id: None,
            },
            &AtomicBool::new(false),
        )
        .unwrap();

        assert_eq!(result.state["revision"], 7);
        assert_eq!(result.state["preview_generation"], 13);
        assert_eq!(result.value["state"], "candidate_partial_not_accepted");
        assert_eq!(result.value["partial_candidate_diagnostic_only"], true);
        assert_eq!(result.state["partial_preview"]["revision"], 7);
        assert_eq!(result.state["partial_preview"]["preview_generation"], 13);
        assert_eq!(
            result.state["partial_preview"]["requested_geometry_fingerprint"],
            json!(fingerprint)
        );
        assert_eq!(result.state["partial_preview"]["applied_feature_ids"], json!(["stock"]));
        assert_eq!(result.state["partial_preview"]["failed_feature_id"], "failed-hole");
        assert_eq!(result.state["partial_preview"]["accepted"], false);
        assert_eq!(result.state["partial_preview"]["partial_geometry_checks"]["host_attestation"]["issuer"], "native_cad_session.complete_preview");
        let views = result.state["previews"].as_array().unwrap();
        assert_eq!(views.len(), 7);
        assert!(views.iter().all(|row| row["revision"] == 7
            && row["preview_generation"] == 13
            && row["geometry_fingerprint"] == fingerprint
            && row["partial"] == true
            && row["partial_candidate_diagnostic_only"] == true));
        assert!(views.iter().all(|row| {
            let path = PathBuf::from(row["preview_image_path"].as_str().unwrap());
            fs::metadata(path).is_ok_and(|meta| meta.len() > 0)
        }));
        assert!(result.state["output_paths"].get("stl").is_none());
        assert!(result.state["output_paths"].get("glb").is_none());
        assert!(result.state["output_paths"].get("aimodel").is_none());
        fs::create_dir_all(&session_dir).unwrap();
        let references = session_dir.join("reference-images.json");
        let turns = session_dir.join("user-turns.json");
        fs::write(&references, json!({"schema_version":1,"images":[]}).to_string()).unwrap();
        fs::write(&turns, json!({"schema_version":1,"turns":[]}).to_string()).unwrap();
        let (aggregated, _) = crate::native_review_gate::aggregate(
            &result.state,
            &references,
            &turns,
        )
        .unwrap();
        let (_, dispatch) = native_cad_session::finish_assessment(&aggregated, &references, &turns)
            .unwrap();
        assert_eq!(dispatch.finish_state, "candidate_partial_not_accepted");
        assert_eq!(dispatch.report["accepted"], false);
        // A later geometry revision must invalidate both partial diagnostic
        // attribution and finish evidence, even if old preview rows remain.
        let mut stale_revision = result.state.clone();
        stale_revision["revision"] = json!(8);
        let (_, stale_review) =
            crate::native_review_gate::aggregate(&stale_revision, &references, &turns).unwrap();
        assert_eq!(
            stale_review["result"]["evidence"]["geometry"]["binding_valid"],
            false
        );
        assert!(
            native_cad_session::finish_assessment(&stale_revision, &references, &turns).is_err()
        );
        fs::remove_dir_all(session_dir).unwrap();
    }
}
