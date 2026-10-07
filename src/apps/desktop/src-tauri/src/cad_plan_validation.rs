//! Static CADPlan validation and schema, ported from
//! `src/legacy/ai_modeler/sdk/cadplan.py` (Python source contract version 1.1).
//!
//! This module validates plan data and reports static CATIA/local-backend support
//! mappings. It does not build geometry, check a BREP, or invoke CATIA.

use serde_json::{Map, Value, json};
use std::collections::{BTreeSet, HashMap};

pub const CADPLAN_SCHEMA_VERSION: &str = "1.1";

#[derive(Clone, Copy)]
struct Spec {
    kind: &'static str,
    required: &'static str,
    params: &'static str,
    catia_status: &'static str,
    catia_reason: &'static str,
    local_status: &'static str,
    local_reason: &'static str,
}

// CADPlan v1.1 registry mirrored from cadplan.py FEATURE_SPECS and
// occ_kernel.GEOMETRY_SUPPORT. Parameter names are space-separated identifiers.
const SPECS: &[Spec] = &[
    Spec {
        kind: "box",
        required: "length width height",
        params: "length width height center axis base corner corner_mode stack",
        catia_status: "exact",
        catia_reason: "矩形草图加凸台；当前 CATIA 映射仅支持 Z 轴拉伸。",
        local_status: "exact",
        local_reason: "OCCT 棱柱；带圆角/倒角时沿用离散轮廓近似",
    },
    Spec {
        kind: "cylinder",
        required: "radius height",
        params: "radius radius2 height center axis base segments",
        catia_status: "exact",
        catia_reason: "center 默认表示轴向底面中心(base=true)，height 沿 axis 正向拉伸；base=false 时 center 表示几何中点。等半径时为圆草图加凸台；圆锥台会近似，非 Z 轴圆锥台暂不支持。",
        local_status: "exact",
        local_reason: "解析圆柱或圆锥面",
    },
    Spec {
        kind: "socket_head_bolt",
        required: "shank_d thread_pitch length head_d head_h hex_s hex_t",
        params: "shank_d thread_pitch length head_d head_h hex_s hex_t center segments",
        catia_status: "approximate",
        catia_reason: "本地网格按 thread_pitch 生成可见的近似 ISO 公制螺旋牙型；整段杆部带牙，未建牙端倒角/退刀槽。当前 CATIA 导出器仍以光杆凸台和六角凹槽表达，不会创建原生或装饰螺纹。",
        local_status: "approximate",
        local_reason: "当前参数化网格转封闭 facet-BREP",
    },
    Spec {
        kind: "hex_bolt",
        required: "shank_d length across_flats head_h thread_pitch",
        params: "shank_d length across_flats head_h thread_pitch center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 映射为六角头草图 Pad 与圆杆 Pad；螺纹只按装饰表示，不是可见几何或 CATIA 原生螺纹。",
        local_status: "approximate",
        local_reason: "六角头为解析棱柱；螺杆可见外螺纹由离散网格转封闭 facet-BREP。",
    },
    Spec {
        kind: "sphere",
        required: "radius",
        params: "radius center segments rings",
        catia_status: "approximate",
        catia_reason: "CATIA 导出器以多层圆盘近似球体。",
        local_status: "exact",
        local_reason: "OCCT 解析球面",
    },
    Spec {
        kind: "prism",
        required: "profile height",
        params: "profile height center axis",
        catia_status: "exact",
        catia_reason: "闭合多边形草图加凸台；当前仅支持 Z 轴。",
        local_status: "exact",
        local_reason: "多边形轮廓棱柱",
    },
    Spec {
        kind: "revolve",
        required: "profile angle",
        params: "profile angle center axis segments",
        catia_status: "approximate",
        catia_reason: "本地 OCCT BREP 按闭合[半径,轴向坐标]轮廓精确回转，并将新增回转体与已有实体做布尔合并；CATIA 导出器仍用多层圆盘近似，非 CATIA 原生回转特征。",
        local_status: "exact",
        local_reason: "简单闭合半径-轴向轮廓的 OCCT 解析回转",
    },
    Spec {
        kind: "gear",
        required: "teeth module height",
        params: "teeth module height center axis base",
        catia_status: "exact",
        catia_reason: "齿廓转换为多边形草图并拉伸；当前仅支持 Z 轴。",
        local_status: "approximate",
        local_reason: "梯形齿廓为当前简化齿形，几何由多边形棱柱表达",
    },
    Spec {
        kind: "loft",
        required: "root_chord tip_chord span thickness",
        params: "root_chord tip_chord span thickness camber camber_pos twist sweep sections points rotate translate",
        catia_status: "approximate",
        catia_reason: "CATIA 中以根部截面拉伸近似多截面放样。",
        local_status: "approximate",
        local_reason: "翼型截面离散后由 OCCT 放样",
    },
    Spec {
        kind: "hole",
        required: "diameter through pos",
        params: "diameter through pos point x y count pcd phase depth margin axis segments entry_point direction",
        catia_status: "exact",
        catia_reason: "圆形切除；through=true 时按当前零件包围盒生成贯穿深度。盲孔可用 entry_point 与 direction 显式指定入口点和沿全局轴的切入方向；未指定时沿用正轴端面默认语义。",
        local_status: "exact",
        local_reason: "圆柱刀具体与 BREP 布尔切除",
    },
    Spec {
        kind: "pocket",
        required: "length width depth",
        params: "length width depth x y axis side corner corner_mode",
        catia_status: "exact",
        catia_reason: "矩形/圆角矩形凹槽；当前仅支持 Z 轴。",
        local_status: "exact",
        local_reason: "矩形/离散圆角轮廓与 BREP 布尔切除",
    },
    Spec {
        kind: "pattern",
        required: "mode count",
        params: "mode count step axis angle full center",
        catia_status: "exact",
        catia_reason: "转换为显式复制的 CATIA 操作；relations 示例：[{\"type\":\"pattern_source\",\"feature_id\":\"紧邻前一特征的 id\"}]。operation 必须与源特征相同：阵列切孔用 cut，阵列加料用 add。",
        local_status: "exact",
        local_reason: "按 CADPlan 来源特征执行实体平移/旋转变换",
    },
    Spec {
        kind: "helix",
        required: "radius wire_r height turns",
        params: "radius wire_r height turns center axis taper seg_per_turn wire_seg taper_start",
        catia_status: "unsupported",
        catia_reason: "弹簧/螺旋不能由当前草图、凸台和凹槽通道表达。",
        local_status: "approximate",
        local_reason: "现有螺旋网格转封闭 facet-BREP",
    },
    Spec {
        kind: "spoked_wheel",
        required: "outer_r rim_w hub_r height spokes spoke_w",
        params: "outer_r rim_w hub_r height spokes spoke_w center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 仅生成轮缘与轮毂，辐条需要补建。",
        local_status: "exact",
        local_reason: "解析轮缘与轮毂回转体、显式拉伸辐条并集成一个闭合实体",
    },
    Spec {
        kind: "vbelt_pulley",
        required: "pitch_d grooves height groove_type",
        params: "pitch_d grooves height groove_type outer_d groove_depth bore_d center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 生成光轮盘体；V 形回转槽需要在 CATIA 中补建。",
        local_status: "approximate",
        local_reason: "标准槽轴向截面原生 OCCT 回转；槽根间距为保证剖面简单可能近似调整，输入轮宽不足时明确拒绝",
    },
    Spec {
        kind: "timing_pulley",
        required: "pitch_d teeth height tooth_type",
        params: "pitch_d teeth height tooth_type bore_d center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 生成盘体，齿形需要在 CATIA 中补建。",
        local_status: "approximate",
        local_reason: "现有同步带轮配方转封闭 facet-BREP",
    },
    Spec {
        kind: "worm",
        required: "radius height thread_r wire_r turns",
        params: "radius height center center2 thread_r wire_r axis height2 turns segments seg_per_turn wire_seg",
        catia_status: "approximate",
        catia_reason: "CATIA 仅生成光轴，螺旋齿需要补建。",
        local_status: "approximate",
        local_reason: "现有蜗杆配方转封闭 facet-BREP",
    },
    Spec {
        kind: "helical_gear",
        required: "teeth module height",
        params: "teeth module height helix_deg bore_d center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 以直齿轮近似，省略螺旋角。",
        local_status: "approximate",
        local_reason: "保留当前省略螺旋角的直齿近似并转 facet-BREP",
    },
    Spec {
        kind: "bevel_gear",
        required: "teeth module height cone_deg",
        params: "teeth module height cone_deg bore_d center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 以直齿圆柱轮廓近似，省略锥面。",
        local_status: "approximate",
        local_reason: "现有锥齿轮网格配方转 facet-BREP",
    },
    Spec {
        kind: "crankshaft",
        required: "length main_d pin_d stroke throws",
        params: "length main_d pin_d stroke throws center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 按 X 轴布置主轴颈、交替偏置连杆颈和哑铃形曲柄臂(主轴圆 + 连杆颈圆外公切凸包)，曲柄臂外形被简化。",
        local_status: "approximate",
        local_reason: "解析主轴颈/偏心连杆颈与矩形曲柄臂并集，曲柄臂外形近似",
    },
    Spec {
        kind: "camshaft",
        required: "length journal_d base_d lift lobes lobe_deg",
        params: "length journal_d base_d lift lobes lobe_deg center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 凸轮沿轴向等分布置，相位需核对。",
        local_status: "approximate",
        local_reason: "现有凸轮轴网格配方转 facet-BREP",
    },
    Spec {
        kind: "impeller",
        required: "hub_d tip_d height blades twist_deg",
        params: "hub_d tip_d height blades blade_t twist_deg center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 仅生成轮毂和轮盘，扭转叶片需要补建。",
        local_status: "approximate",
        local_reason: "现有叶轮网格配方转 facet-BREP",
    },
    Spec {
        kind: "piston",
        required: "diameter height pin_d grooves",
        params: "diameter height pin_d grooves center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 以圆柱体近似，销孔和活塞环槽需要补建。",
        local_status: "exact",
        local_reason: "闭合多段线剖面原生 OCCT 回转，横向销孔解析布尔切除",
    },
    Spec {
        kind: "conrod",
        required: "length big_d small_d big_bore small_bore shank_w thick",
        params: "length big_d small_d big_bore small_bore shank_w thick center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 以大头、小头和直杆身近似，杆身过渡被简化。",
        local_status: "approximate",
        local_reason: "解析大小头环与外公切梯形杆身并集，工字截面近似",
    },
    Spec {
        kind: "cross_joint",
        required: "span journal_d",
        params: "span journal_d center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 仅生成单轴，另一根横轴需要补建。",
        local_status: "approximate",
        local_reason: "现有万向节网格配方转 facet-BREP",
    },
    Spec {
        kind: "scroll_disc",
        required: "outer_d height wall_t turns",
        params: "outer_d height wall_t turns center segments",
        catia_status: "approximate",
        catia_reason: "CATIA 仅生成底盘，螺旋壁需要补建。",
        local_status: "approximate",
        local_reason: "简单多边形涡旋壁原生拉伸并与背盘合并；壁面以多段线近似渐开线",
    },
    Spec {
        kind: "rotor",
        required: "radius n height bore_d",
        params: "radius n height bore_d center",
        catia_status: "exact",
        catia_reason: "转子轮廓、厚度与中心孔映射为可编辑草图和凹槽。",
        local_status: "approximate",
        local_reason: "圆角三角转子离散轮廓原生拉伸并按需切中心孔",
    },
    Spec {
        kind: "shell",
        required: "thickness",
        params: "thickness offset_mode removed_faces",
        catia_status: "unsupported",
        catia_reason: "当前 Document/CATIA 转换器没有抽壳实现。",
        local_status: "unavailable",
        local_reason: "旧 v1.0 thickness-only shell 不会自动迁移；1.1 仅原生执行显式 planar 子集。",
    },
];

const LENGTH_KEYS: &[&str] = &[
    "length",
    "width",
    "height",
    "radius",
    "radius2",
    "corner",
    "wire_r",
    "sec_a",
    "sec_b",
    "base",
    "margin",
    "diameter",
    "depth",
    "pcd",
    "outer_r",
    "rim_w",
    "hub_r",
    "pitch_d",
    "bore_d",
    "thread_r",
    "height2",
    "pin_d",
    "stroke",
    "main_d",
    "journal_d",
    "base_d",
    "lift",
    "hub_d",
    "tip_d",
    "big_d",
    "small_d",
    "big_bore",
    "small_bore",
    "shank_w",
    "thick",
    "span",
    "shank_d",
    "thread_pitch",
    "head_d",
    "head_h",
    "across_flats",
    "hex_s",
    "hex_t",
    "root_chord",
    "tip_chord",
    "sweep",
    "groove_depth",
    "outer_d",
    "wall_t",
    "thickness",
    "module",
    "spoke_w",
    "blade_t",
    "x",
    "y",
    "z",
];
const LENGTH_ARRAY_KEYS: &[&str] = &[
    "center",
    "center2",
    "translate",
    "step",
    "point",
    "entry_point",
    "profile",
];
const ANGLE_KEYS: &[&str] = &[
    "angle",
    "phase",
    "twist",
    "twist_deg",
    "helix_deg",
    "cone_deg",
    "lobe_deg",
];
const DIMENSIONLESS_KEYS: &[&str] = &["turns", "taper", "taper_start", "camber", "camber_pos"];
const NONNEGATIVE_KEYS: &[&str] = &[
    "corner",
    "margin",
    "bore_d",
    "groove_depth",
    "sweep",
    "lift",
    "radius2",
];
const INTEGER_KEYS: &[&str] = &[
    "teeth",
    "count",
    "grooves",
    "spokes",
    "blades",
    "lobes",
    "n",
    "throws",
    "segments",
    "rings",
    "sections",
    "points",
    "seg_per_turn",
    "wire_seg",
];
const BOOLEAN_KEYS: &[&str] = &["base", "through", "full", "stack"];
const STRING_KEYS: &[&str] = &[
    "axis",
    "corner_mode",
    "mode",
    "pos",
    "groove_type",
    "tooth_type",
    "direction",
    "offset_mode",
    "side",
];

fn has(key: &str, values: &[&str]) -> bool {
    values.contains(&key)
}
fn spec_for(kind: &str) -> Option<&'static Spec> {
    SPECS.iter().find(|spec| spec.kind == kind)
}
fn words(value: &'static str) -> Vec<&'static str> {
    value.split_whitespace().collect()
}

fn item(code: &str, path: String, message: impl Into<String>) -> Value {
    json!({"code":code,"path":path,"message":message.into()})
}
fn confirmation(fid: &str, parameter: &str, question: impl Into<String>) -> Value {
    json!({"feature_id":fid,"parameter":parameter,"question":question.into()})
}
fn append_error(errors: &mut Vec<Value>, code: &str, path: String, message: impl Into<String>) {
    errors.push(item(code, path, message));
}
fn append_confirmation(
    rows: &mut Vec<Value>,
    fid: &str,
    parameter: &str,
    question: impl Into<String>,
) {
    rows.push(confirmation(fid, parameter, question));
}

/// Return the current CADPlan JSON Schema document, generated from the same static registry.
pub fn schema_document() -> Value {
    schema_document_impl()
}

/// Return the oneOf schema and support annotation for a public feature kind.
pub fn feature_schema(kind: &str) -> Option<Value> {
    let spec = spec_for(kind)?;
    Some(feature_schema_for(spec))
}

/// Validate supplied values for a feature parameter map using the same checks
/// used by full CADPlan validation. This is intentionally partial: it validates
/// only the provided keys and does not require the feature's required fields to
/// be complete yet.
pub fn validate_feature_parameter_values(
    kind: &str,
    values: &Map<String, Value>,
) -> Result<(), String> {
    let spec = spec_for(kind).ok_or_else(|| format!("未知 CADPlan 特征类型：{kind}。"))?;
    let allowed = words(spec.params);
    for (key, value) in values {
        if !allowed.contains(&key.as_str()) {
            return Err(format!("特征类型{kind}不支持参数{key}。"));
        }
        let mut errors = Vec::new();
        validate_parameter(key, value, "$.parameters", &mut errors);
        if let Some(message) = errors.first().and_then(|error| error["message"].as_str()) {
            return Err(format!("参数{key}无效：{message}"));
        }
    }
    Ok(())
}

/// Static support descriptor for a public feature kind.
pub fn feature_spec(kind: &str) -> Option<Value> {
    let spec = spec_for(kind)?;
    Some(
        json!({"kind":spec.kind,"status":spec.catia_status,"reason":spec.catia_reason,
        "local_geometry_status":spec.local_status,"local_geometry_reason":spec.local_reason,
        "required_parameters":words(spec.required)}),
    )
}

/// Validate one JSON CADPlan. Result fields and state names mirror Python v1.1.
pub fn validate_plan(plan: &Value) -> Value {
    let Some(data) = plan.as_object() else {
        return json!({"valid":false,"state":"invalid","errors":[item("plan_load_error","$".into(),"CADPlan 顶层必须是 JSON 对象。")],"confirmations":[],"support":[],"geometry_support":[],"warnings":[]});
    };
    let mut errors = Vec::<Value>::new();
    let mut confirmations = Vec::<Value>::new();
    let mut warnings = Vec::<Value>::new();
    let mut geometry_support = Vec::<Value>::new();

    for key in data.keys() {
        if ![
            "schema_version",
            "name",
            "source_prompt",
            "units",
            "coordinate_system",
            "features",
            "pending_questions",
        ]
        .contains(&key.as_str())
        {
            append_error(
                &mut errors,
                "unknown_field",
                format!("$.{key}"),
                "CADPlan 不支持此字段。",
            );
        }
    }
    let schema_version = data
        .get("schema_version")
        .and_then(Value::as_str)
        .unwrap_or("");
    if !["1.0", CADPLAN_SCHEMA_VERSION].contains(&schema_version) {
        append_error(
            &mut errors,
            "unsupported_schema_version",
            "$.schema_version".into(),
            "只支持 schema_version=1.0 或 1.1。",
        );
    }
    if !data
        .get("name")
        .and_then(Value::as_str)
        .is_some_and(|v| !v.trim().is_empty())
    {
        append_error(
            &mut errors,
            "missing_name",
            "$.name".into(),
            "必须提供非空模型名称。",
        );
    }
    if data.get("source_prompt").is_some_and(|v| !v.is_string()) {
        append_error(
            &mut errors,
            "invalid_source_prompt",
            "$.source_prompt".into(),
            "source_prompt 必须为字符串。",
        );
    }

    validate_units(data.get("units"), &mut errors);
    validate_coordinates(data.get("coordinate_system"), &mut errors);
    let feature_rows = data.get("features").and_then(Value::as_array);
    if feature_rows.is_none_or(Vec::is_empty) {
        append_error(
            &mut errors,
            "missing_features",
            "$.features".into(),
            "至少需要一个特征。",
        );
    }
    let features = feature_rows.cloned().unwrap_or_default();
    let mut support = Vec::<Value>::new();
    let mut ids = HashMap::<String, usize>::new();
    let mut has_add = false;

    for (index, feature_value) in features.iter().enumerate() {
        let path = format!("$.features[{index}]");
        let Some(feature) = feature_value.as_object() else {
            append_error(
                &mut errors,
                "invalid_feature",
                path,
                "特征必须是 JSON 对象。",
            );
            continue;
        };
        for key in feature.keys() {
            if ![
                "id",
                "kind",
                "operation",
                "name",
                "parameters",
                "sources",
                "relations",
                "note",
                "enabled",
                "selection_binding",
                "geometry_variant",
            ]
            .contains(&key.as_str())
            {
                append_error(
                    &mut errors,
                    "unknown_field",
                    format!("{path}.{key}"),
                    "特征不支持此字段。",
                );
            }
        }
        let fid = match feature.get("id").and_then(Value::as_str) {
            Some(id) if !id.trim().is_empty() => {
                if ids.contains_key(id) {
                    append_error(
                        &mut errors,
                        "duplicate_feature_id",
                        format!("{path}.id"),
                        format!("特征 id 重复：{id}。"),
                    );
                } else {
                    ids.insert(id.to_owned(), index);
                }
                id.to_owned()
            }
            _ => {
                append_error(
                    &mut errors,
                    "missing_feature_id",
                    format!("{path}.id"),
                    "每个特征必须有稳定 id。",
                );
                format!("index-{index}")
            }
        };
        if feature
            .get("enabled")
            .is_some_and(|value| !value.is_boolean())
        {
            append_error(
                &mut errors,
                "invalid_enabled",
                format!("{path}.enabled"),
                "enabled 必须是布尔值。",
            );
        }
        let Some(kind) = feature.get("kind").and_then(Value::as_str) else {
            append_error(
                &mut errors,
                "unsupported_feature_kind",
                format!("{path}.kind"),
                "未知或非公开特征类型；支持类型见 CADPlan Schema。",
            );
            continue;
        };
        let Some(spec) = spec_for(kind) else {
            append_error(
                &mut errors,
                "unsupported_feature_kind",
                format!("{path}.kind"),
                "未知或非公开特征类型；支持类型见 CADPlan Schema。",
            );
            continue;
        };
        if let Some(variant) = feature.get("geometry_variant") {
            let valid_variant = (kind == "conrod" && variant.as_str() == Some("legacy_quick_rectangular_shank"))
                || (kind == "crankshaft" && variant.as_str() == Some("legacy_quick_dumbbell_cheeks"));
            if !valid_variant {
                append_error(&mut errors, "invalid_geometry_variant",
                    format!("{path}.geometry_variant"),
                    "geometry_variant 仅允许 conrod/legacy_quick_rectangular_shank 或 crankshaft/legacy_quick_dumbbell_cheeks。",
                );
            }
        }
        let operation = feature
            .get("operation")
            .and_then(Value::as_str)
            .unwrap_or("");
        if operation != "add" && operation != "cut" {
            append_error(
                &mut errors,
                "invalid_operation",
                format!("{path}.operation"),
                "operation 必须是 add 或 cut。",
            );
        }
        for text_key in ["name", "note"] {
            if feature.get(text_key).is_some_and(|v| !v.is_string()) {
                append_error(
                    &mut errors,
                    "invalid_feature_text",
                    format!("{path}.{text_key}"),
                    format!("{text_key} 必须为字符串。"),
                );
            }
        }
        if operation == "cut" && !has_add {
            append_confirmation(
                &mut confirmations,
                &fid,
                "operation",
                "该切除特征之前没有可切除的实体；请确认前置实体。",
            );
        }
        if operation == "add" && feature.get("enabled").and_then(Value::as_bool) != Some(false) {
            has_add = true;
        }

        let parameters = feature.get("parameters").and_then(Value::as_object);
        if parameters.is_none() {
            append_confirmation(
                &mut confirmations,
                &fid,
                "parameters",
                "请提供该特征的参数对象。",
            );
        }
        let params = parameters.cloned().unwrap_or_default();
        for key in params.keys() {
            if !words(spec.params).contains(&key.as_str()) {
                append_error(
                    &mut errors,
                    "unknown_parameter",
                    format!("{path}.parameters.{key}"),
                    "该特征不支持此参数；未知参数不会传给 Document。",
                );
            }
        }
        for key in conditional_required(kind, &params) {
            if params.get(key).is_none_or(Value::is_null) {
                append_confirmation(
                    &mut confirmations,
                    &fid,
                    key,
                    format!("请补充必需参数 {key}。"),
                );
            }
        }
        for (key, value) in &params {
            validate_parameter(key, value, &path, &mut errors);
        }
        validate_hole(kind, &params, &path, &mut errors);
        validate_vbelt(kind, &params, &path, &mut errors);
        validate_pattern(kind, &params, &path, &mut errors);
        validate_shell_feature(
            feature,
            &params,
            &fid,
            &path,
            schema_version,
            &features,
            index,
            &ids,
            &mut errors,
            &mut confirmations,
        );
        validate_sources(
            feature.get("sources"),
            &params,
            &fid,
            &path,
            &mut errors,
            &mut confirmations,
        );
        validate_relations(
            feature.get("relations"),
            &features,
            index,
            &ids,
            kind,
            operation,
            &fid,
            &path,
            &mut errors,
            &mut confirmations,
        );

        let (status, reason) = catia_support(spec, &params);
        support.push(json!({"feature_id":fid,"kind":kind,"status":status,"reason":reason}));
        let (local_status, local_reason) = local_support(
            spec,
            &params,
            kind,
            feature.get("relations"),
            feature.get("selection_binding"),
            feature.get("geometry_variant").and_then(Value::as_str),
            schema_version,
        );
        geometry_support.push(json!({"feature_id":fid,"kind":kind,"status":local_status,"reason":local_reason,"backend":"occt_brep"}));
        if local_status == "approximate" {
            warnings.push(json!({"code":"LOCAL_BREP_APPROXIMATION","feature_id":fid,"kind":kind,"message":local_reason}));
        } else if local_status == "unavailable" {
            warnings.push(json!({"code":"LOCAL_BREP_UNAVAILABLE","feature_id":fid,"kind":kind,"message":local_reason}));
        }
        if status == "approximate" {
            warnings.push(
                json!({"code":"CATIA_APPROXIMATION","feature_id":fid,"kind":kind,"message":reason}),
            );
        } else if status == "unsupported" {
            warnings.push(
                json!({"code":"CATIA_UNSUPPORTED","feature_id":fid,"kind":kind,"message":reason}),
            );
        }
    }

    if let Some(pending) = data.get("pending_questions").filter(|v| truthy(v)) {
        if let Some(rows) = pending.as_array() {
            for (index, question) in rows.iter().enumerate() {
                let q = question
                    .as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| question.to_string());
                append_confirmation(
                    &mut confirmations,
                    "",
                    &format!("pending_questions[{index}]"),
                    q,
                );
            }
        } else {
            append_error(
                &mut errors,
                "invalid_pending_questions",
                "$.pending_questions".into(),
                "pending_questions 必须是数组。",
            );
        }
    }
    let state = if !errors.is_empty() {
        "invalid"
    } else if !confirmations.is_empty() {
        "needs_confirmation"
    } else {
        "valid"
    };
    json!({"valid":state=="valid","state":state,"errors":errors,"confirmations":confirmations,"support":support,"geometry_support":geometry_support,"warnings":warnings,"schema_version":schema_version})
}

/// Return preflight status for strict CATIA export. No geometry/CATIA work occurs.
pub fn strict_preflight(plan: &Value) -> Value {
    let validation = validate_plan(plan);
    let state = validation["state"].as_str().unwrap_or("invalid");
    if state != "valid" {
        return json!({"state":state,"catia_executed":false,"validation":validation,
            "errors":validation["errors"],"confirmations":validation["confirmations"],
            "dimensions":null,"catia_operations":[],"output_paths":{}});
    }
    let blocked: Vec<Value> = validation["support"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|row| row["status"] != "exact")
        .cloned()
        .collect();
    if !blocked.is_empty() {
        return json!({"state":"strict_blocked","catia_executed":false,"validation":validation,
            "dimensions":null,"catia_operations":[],"warnings":validation["warnings"],
            "blocked_features":blocked,"output_paths":{}});
    }
    json!({"state":"strict_ready","catia_executed":false,"validation":validation,
        "dimensions":null,"catia_operations":[],"blocked_features":[],"output_paths":{}})
}

fn validate_units(units: Option<&Value>, errors: &mut Vec<Value>) {
    let Some(units) = units.and_then(Value::as_object) else {
        append_error(
            errors,
            "missing_units",
            "$.units".into(),
            "必须声明长度和角度单位。",
        );
        return;
    };
    for key in units.keys() {
        if key != "length" && key != "angle" {
            append_error(
                errors,
                "unknown_field",
                format!("$.units.{key}"),
                "单位对象只接受 length 和 angle。",
            );
        }
    }
    if !units
        .get("length")
        .and_then(Value::as_str)
        .is_some_and(|v| ["mm", "cm", "m", "in"].contains(&v))
    {
        append_error(
            errors,
            "unsupported_length_unit",
            "$.units.length".into(),
            "长度单位只支持 mm、cm、m、in。",
        );
    }
    if !units
        .get("angle")
        .and_then(Value::as_str)
        .is_some_and(|v| ["deg", "rad"].contains(&v))
    {
        append_error(
            errors,
            "unsupported_angle_unit",
            "$.units.angle".into(),
            "角度单位只支持 deg、rad。",
        );
    }
}

fn origin_is_zero(value: &Value) -> bool {
    value
        .as_array()
        .is_some_and(|rows| rows.len() == 3 && rows.iter().all(|v| number(v) == Some(0.0)))
}

fn validate_coordinates(coord: Option<&Value>, errors: &mut Vec<Value>) {
    let Some(coord) = coord.and_then(Value::as_object) else {
        append_error(
            errors,
            "missing_coordinate_system",
            "$.coordinate_system".into(),
            "必须声明右手全局直角坐标系。",
        );
        return;
    };
    for key in coord.keys() {
        if !["type", "handedness", "axes", "origin"].contains(&key.as_str()) {
            append_error(
                errors,
                "unknown_field",
                format!("$.coordinate_system.{key}"),
                "坐标系对象不支持此字段。",
            );
        }
    }
    if coord.get("type") != Some(&json!("global_cartesian"))
        || coord.get("handedness") != Some(&json!("right"))
    {
        append_error(
            errors,
            "unsupported_coordinate_system",
            "$.coordinate_system".into(),
            "当前只支持右手 global_cartesian 坐标系。",
        );
    }
    if coord.get("axes") != Some(&json!(["X", "Y", "Z"])) {
        append_error(
            errors,
            "unsupported_axis_order",
            "$.coordinate_system.axes".into(),
            "坐标轴顺序必须为 [X,Y,Z]。",
        );
    }
    if !coord.get("origin").is_some_and(origin_is_zero) {
        append_error(
            errors,
            "unsupported_origin",
            "$.coordinate_system.origin".into(),
            "当前 Document 使用原点 [0,0,0]；计划坐标请先换算到全局原点。",
        );
    }
}

fn conditional_required(kind: &str, params: &Map<String, Value>) -> Vec<&'static str> {
    let Some(spec) = spec_for(kind) else {
        return Vec::new();
    };
    let mut required = words(spec.required);
    if kind == "hole" {
        match params.get("pos").and_then(Value::as_str) {
            Some("point") => required.push("point"),
            Some("pcd") => {
                required.extend(["pcd", "count"]);
            }
            Some("corners" | "center") => {}
            _ => required.push("pos"),
        }
        if params.get("through") == Some(&Value::Bool(false)) {
            required.push("depth");
        }
    } else if kind == "pattern" {
        match params.get("mode").and_then(Value::as_str) {
            Some("linear") => required.push("step"),
            Some("circular") => required.extend(["axis", "angle", "center"]),
            _ => required.push("mode"),
        }
    }
    let mut seen = BTreeSet::new();
    required
        .into_iter()
        .filter(|key| seen.insert(*key))
        .collect()
}

fn walk_numbers(value: &Value, path: &str, errors: &mut Vec<Value>) {
    match value {
        Value::Array(rows) => {
            for (index, row) in rows.iter().enumerate() {
                walk_numbers(row, &format!("{path}[{index}]"), errors);
            }
        }
        Value::Object(rows) => {
            for (key, row) in rows {
                walk_numbers(row, &format!("{path}.{key}"), errors);
            }
        }
        _ => {}
    }
}
fn is_number(value: &Value) -> bool {
    value.is_number()
}
fn finite_number(value: &Value) -> bool {
    value.as_f64().is_some_and(f64::is_finite)
}
fn number(value: &Value) -> Option<f64> {
    value.as_f64()
}
fn valid_vec(value: &Value, len: usize) -> bool {
    value.as_array().is_some_and(|rows| {
        rows.len() == len && rows.iter().all(|v| is_number(v) && finite_number(v))
    })
}

fn validate_parameter(key: &str, value: &Value, path: &str, errors: &mut Vec<Value>) {
    let ppath = format!("{path}.parameters.{key}");
    walk_numbers(value, &ppath, errors);
    if has(key, BOOLEAN_KEYS) && !value.is_boolean() {
        append_error(
            errors,
            "invalid_parameter_type",
            ppath.clone(),
            "此参数必须为布尔值。",
        );
    } else if has(key, INTEGER_KEYS) {
        if !value.as_i64().is_some() && !value.as_u64().is_some() {
            append_error(
                errors,
                "invalid_parameter_type",
                ppath.clone(),
                "此参数必须为整数。",
            );
        } else if value.as_i64().is_some_and(|v| v < 1) || value.as_u64().is_some_and(|v| v < 1) {
            append_error(
                errors,
                "invalid_parameter_value",
                ppath.clone(),
                "此参数必须至少为 1。",
            );
        }
    } else if has(key, STRING_KEYS) && !value.is_string() {
        append_error(
            errors,
            "invalid_parameter_type",
            ppath.clone(),
            "此参数必须为字符串。",
        );
    } else if !has(key, BOOLEAN_KEYS)
        && (has(key, LENGTH_KEYS) || has(key, ANGLE_KEYS) || has(key, DIMENSIONLESS_KEYS))
        && !is_number(value)
    {
        append_error(
            errors,
            "invalid_parameter_type",
            ppath.clone(),
            "此参数必须为有限数值。",
        );
    }
    if let Some(s) = value.as_str() {
        if let Some(choices) = enum_values(key) {
            let candidate = if key == "axis" {
                s.to_ascii_uppercase()
            } else {
                s.to_owned()
            };
            if !choices.contains(&candidate.as_str()) {
                append_error(
                    errors,
                    "invalid_parameter_value",
                    ppath.clone(),
                    format!("可选值为：{}。", choices.join("、")),
                );
            }
        }
    }
    if let Some(v) = number(value) {
        if ["teeth", "segments", "n", "points"].contains(&key)
            && v.fract() == 0.0
            && v < if key == "teeth" { 6.0 } else { 3.0 }
        {
            append_error(
                errors,
                "invalid_parameter_value",
                ppath.clone(),
                format!("{key} 的值不足以生成有效轮廓。"),
            );
        }
        if ["rings", "sections"].contains(&key) && v.fract() == 0.0 && v < 2.0 {
            append_error(
                errors,
                "invalid_parameter_value",
                ppath.clone(),
                format!("{key} 至少为 2。"),
            );
        }
        let positive = has(key, LENGTH_KEYS)
            && !["x", "y", "z", "base"].contains(&key)
            && !has(key, NONNEGATIVE_KEYS);
        if positive && v <= 0.0 {
            append_error(
                errors,
                "non_positive_dimension",
                ppath.clone(),
                "该尺寸/计数必须大于 0。",
            );
        }
        if has(key, NONNEGATIVE_KEYS) && v < 0.0 {
            append_error(
                errors,
                "negative_dimension",
                ppath.clone(),
                "该尺寸不能小于 0。",
            );
        }
    }
    if key == "center" && !valid_vec(value, 3) {
        append_error(
            errors,
            "invalid_vector",
            ppath.clone(),
            "center 必须是三个有限数值 [x,y,z]。",
        );
    }
    if ["point", "entry_point", "step", "translate", "center2"].contains(&key)
        && !valid_vec(value, 3)
    {
        append_error(
            errors,
            "invalid_vector",
            ppath.clone(),
            format!("{key} 必须是三个有限数值 [x,y,z]。"),
        );
    }
    if key == "profile"
        && !value
            .as_array()
            .is_some_and(|rows| rows.len() >= 3 && rows.iter().all(|row| valid_vec(row, 2)))
    {
        append_error(
            errors,
            "invalid_profile",
            ppath.clone(),
            "profile 必须至少包含 3 个二维有限数值点。",
        );
    }
    if key == "rotate" {
        let valid = value.as_array().is_some_and(|rows| {
            rows.len() == 2
                && rows[0]
                    .as_str()
                    .is_some_and(|v| ["X", "Y", "Z"].contains(&v))
                && is_number(&rows[1])
                && finite_number(&rows[1])
        });
        if !valid {
            append_error(
                errors,
                "invalid_rotation",
                ppath.clone(),
                "rotate 必须是 [X|Y|Z, 角度]。",
            );
        }
    }
}

fn enum_values(key: &str) -> Option<Vec<&'static str>> {
    let values: &[&str] = match key {
        "axis" => &["X", "Y", "Z"],
        "corner_mode" => &["fillet", "chamfer"],
        "mode" => &["linear", "circular"],
        "pos" => &["center", "corners", "pcd", "point"],
        "groove_type" => &[
            "Y", "Z", "A", "B", "C", "D", "E", "SPZ", "SPA", "SPB", "SPC",
        ],
        "tooth_type" => &[
            "MXL", "XL", "L", "H", "XH", "XXH", "T2.5", "T5", "T10", "T20", "3M", "5M", "8M", "14M",
        ],
        "side" | "direction" => &["positive", "negative"],
        "offset_mode" => &["inward", "outward"],
        _ => return None,
    };
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    Some(sorted)
}

fn validate_hole(kind: &str, params: &Map<String, Value>, path: &str, errors: &mut Vec<Value>) {
    if kind != "hole" {
        return;
    }
    let pos = params.get("pos").and_then(Value::as_str).unwrap_or("");
    if pos == "pcd"
        && params
            .get("count")
            .and_then(Value::as_i64)
            .is_some_and(|v| v < 2)
    {
        append_error(
            errors,
            "invalid_hole_pattern",
            format!("{path}.parameters.count"),
            "PCD 孔数至少为 2。",
        );
    }
    let has_entry = params.contains_key("entry_point");
    let has_direction = params.contains_key("direction");
    if has_entry != has_direction {
        append_error(
            errors,
            "incomplete_hole_entry",
            format!("{path}.parameters"),
            "显式盲孔入口必须同时提供 entry_point 与 direction。",
        );
    }
    if (has_entry || has_direction) && params.get("through") != Some(&Value::Bool(false)) {
        append_error(
            errors,
            "hole_entry_requires_blind",
            format!("{path}.parameters"),
            "entry_point/direction 只适用于 through=false 的盲孔。",
        );
    }
    if (has_entry || has_direction) && pos != "point" {
        append_error(
            errors,
            "hole_entry_requires_point",
            format!("{path}.parameters.pos"),
            "显式盲孔入口要求 pos=point，以便核对入口与孔轴位置。",
        );
    }
    if has_entry
        && has_direction
        && valid_vec(params.get("entry_point").unwrap(), 3)
        && valid_vec(params.get("point").unwrap_or(&Value::Null), 3)
    {
        let axis = params
            .get("axis")
            .and_then(Value::as_str)
            .unwrap_or("Z")
            .to_ascii_uppercase();
        let plane = match axis.as_str() {
            "X" => Some([1, 2]),
            "Y" => Some([0, 2]),
            "Z" => Some([0, 1]),
            _ => None,
        };
        if let Some([i, j]) = plane {
            let ep = params["entry_point"].as_array().unwrap();
            let pp = params["point"].as_array().unwrap();
            if [i, j]
                .iter()
                .any(|k| (number(&ep[*k]).unwrap() - number(&pp[*k]).unwrap()).abs() > 1e-6)
            {
                append_error(
                    errors,
                    "hole_entry_point_mismatch",
                    format!("{path}.parameters.entry_point"),
                    "entry_point 必须与 point 位于同一孔轴线上；两者的轴法平面坐标必须一致。",
                );
            }
        }
    }
}

fn validate_pattern(kind: &str, params: &Map<String, Value>, path: &str, errors: &mut Vec<Value>) {
    if kind != "pattern" {
        return;
    }
    let mode = params.get("mode").and_then(Value::as_str).unwrap_or("");
    if mode != "linear" && mode != "circular" {
        append_error(
            errors,
            "invalid_pattern_mode",
            format!("{path}.parameters.mode"),
            "阵列模式必须是 linear 或 circular。",
        );
    }
    if number(params.get("count").unwrap_or(&Value::Null))
        .is_some_and(|n| n.fract() == 0.0 && n < 1.0)
    {
        append_error(
            errors,
            "invalid_pattern_count",
            format!("{path}.parameters.count"),
            "阵列数量必须大于 0。",
        );
    }
}

fn validate_shell_feature(
    feature: &Map<String, Value>,
    params: &Map<String, Value>,
    feature_id: &str,
    path: &str,
    schema_version: &str,
    features: &[Value],
    index: usize,
    ids: &HashMap<String, usize>,
    errors: &mut Vec<Value>,
    confirmations: &mut Vec<Value>,
) {
    if feature.get("kind").and_then(Value::as_str) != Some("shell") {
        if feature.contains_key("selection_binding") {
            append_error(
                errors,
                "selection_binding_only_for_shell",
                format!("{path}.selection_binding"),
                "selection_binding 仅允许用于 shell 特征。",
            );
        }
        return;
    }
    if schema_version == "1.0" {
        append_confirmation(
            confirmations,
            feature_id,
            "shell_migration",
            "CADPlan 1.0 shell 只有 thickness，不能推断开口面、方向或目标；请在 1.1 中重新枚举并显式选择。",
        );
        return;
    }
    if feature.get("operation").and_then(Value::as_str) != Some("cut") {
        append_error(
            errors,
            "shell_requires_cut_operation",
            format!("{path}.operation"),
            "显式 shell 必须使用 operation=cut。",
        );
    }
    for key in ["thickness", "offset_mode", "removed_faces"] {
        if !params.contains_key(key) {
            append_error(
                errors,
                "incomplete_explicit_shell",
                format!("{path}.parameters.{key}"),
                format!(
                    "CADPlan 1.1 shell 必须显式包含 {key}；旧 thickness-only shell 不会自动迁移。"
                ),
            );
        }
    }
    if !params
        .get("offset_mode")
        .and_then(Value::as_str)
        .is_some_and(|value| ["inward", "outward"].contains(&value))
    {
        append_error(
            errors,
            "invalid_shell_offset_mode",
            format!("{path}.parameters.offset_mode"),
            "shell.offset_mode 必须显式为 inward 或 outward。",
        );
    }
    let targets: Vec<&Value> = feature
        .get("relations")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|row| row.get("type").and_then(Value::as_str) == Some("shell_target"))
        .collect();
    let target_id = targets
        .first()
        .and_then(|row| row.get("feature_id"))
        .and_then(Value::as_str);
    if targets.len() != 1 || target_id.is_none() {
        append_error(
            errors,
            "invalid_shell_target_relation",
            format!("{path}.relations"),
            "shell 必须通过唯一 shell_target relation 显式指定目标 feature。",
        );
    } else if let Some(target_index) = ids.get(target_id.unwrap()) {
        let current_head = features[..index]
            .iter()
            .rev()
            .find(|row| row.get("enabled").and_then(Value::as_bool) != Some(false))
            .and_then(|row| row.get("id"))
            .and_then(Value::as_str);
        if *target_index >= index || current_head != target_id {
            append_error(
                errors,
                "stale_shell_target_relation",
                format!("{path}.relations"),
                "shell_target 必须指向当前累计实体末端的前序 feature。",
            );
        }
    } else {
        append_error(
            errors,
            "invalid_shell_target_relation",
            format!("{path}.relations"),
            "shell_target 必须指向此前已定义的 feature。",
        );
    }
    match params.get("removed_faces").and_then(Value::as_array) {
        Some(selectors) if !selectors.is_empty() && selectors.len() <= 256 => {
            let mut seen = BTreeSet::new();
            for (index, selector) in selectors.iter().enumerate() {
                let selector = selector.as_str().unwrap_or("");
                let hash = selector.strip_prefix("shell-face-v1:").unwrap_or("");
                if hash.len() != 64
                    || !hash
                        .bytes()
                        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
                {
                    append_error(
                        errors,
                        "invalid_shell_face_selector",
                        format!("{path}.parameters.removed_faces[{index}]"),
                        "shell.removed_faces 必须使用 cad_list_shell_faces 返回的 opaque selector。",
                    );
                }
                if !seen.insert(selector) {
                    append_error(
                        errors,
                        "duplicate_shell_face_selector",
                        format!("{path}.parameters.removed_faces[{index}]"),
                        "shell.removed_faces 不允许重复 selector 或多重匹配。",
                    );
                }
            }
        }
        _ => append_error(
            errors,
            "invalid_shell_removed_faces",
            format!("{path}.parameters.removed_faces"),
            "shell.removed_faces 须为1到256个 opaque selector。",
        ),
    }
    match feature.get("selection_binding") {
        Some(Value::Object(binding)) => {
            if binding.keys().any(|key| {
                ![
                    "revision",
                    "geometry_fingerprint",
                    "kernel_version",
                    "inventory_version",
                    "target_feature_id",
                ]
                .contains(&key.as_str())
            }) {
                append_error(
                    errors,
                    "unknown_shell_selection_binding_field",
                    format!("{path}.selection_binding"),
                    "selection_binding 只接受 revision、geometry_fingerprint、kernel_version、inventory_version、target_feature_id。",
                );
            }
            if binding
                .get("revision")
                .and_then(Value::as_i64)
                .is_none_or(|revision| revision < 0)
            {
                append_error(
                    errors,
                    "invalid_shell_selection_revision",
                    format!("{path}.selection_binding.revision"),
                    "selection_binding.revision 须为非负整数。",
                );
            }
            let valid_fingerprint = binding
                .get("geometry_fingerprint")
                .and_then(Value::as_str)
                .is_some_and(|value| {
                    value.len() == 64
                        && value
                            .bytes()
                            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
                });
            if !valid_fingerprint {
                append_error(
                    errors,
                    "invalid_shell_selection_fingerprint",
                    format!("{path}.selection_binding.geometry_fingerprint"),
                    "selection_binding.geometry_fingerprint 须为64位小写SHA-256。",
                );
            }
            if !binding
                .get("kernel_version")
                .and_then(Value::as_str)
                .is_some_and(|value| !value.is_empty())
            {
                append_error(
                    errors,
                    "invalid_shell_selection_kernel_version",
                    format!("{path}.selection_binding.kernel_version"),
                    "selection_binding.kernel_version 须为非空字符串。",
                );
            }
            if binding.get("inventory_version").and_then(Value::as_u64)
                != Some(crate::native_shell::INVENTORY_VERSION)
            {
                append_error(
                    errors,
                    "unsupported_shell_inventory_version",
                    format!("{path}.selection_binding.inventory_version"),
                    "selection_binding.inventory_version 不受支持。",
                );
            }
            if binding.get("target_feature_id").and_then(Value::as_str) != target_id {
                append_error(
                    errors,
                    "shell_target_binding_mismatch",
                    format!("{path}.selection_binding.target_feature_id"),
                    "selection_binding.target_feature_id 必须与唯一 shell_target relation 一致。",
                );
            }
        }
        _ => append_error(
            errors,
            "missing_shell_selection_binding",
            format!("{path}.selection_binding"),
            format!(
                "shell 特征 {feature_id} 必须保存 cad_list_shell_faces 返回的 selection_binding。"
            ),
        ),
    }
}

fn python_g(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() { "-0" } else { "0" }.into();
    }
    let scientific = format!("{value:.5e}");
    let (mantissa, exponent) = scientific.split_once('e').expect("scientific float");
    let exponent: i32 = exponent.parse().expect("scientific exponent");
    let mantissa = mantissa.trim_end_matches('0').trim_end_matches('.');
    if exponent < -4 || exponent >= 6 {
        let sign = if exponent >= 0 { "+" } else { "-" };
        format!("{mantissa}e{sign}{:02}", exponent.abs())
    } else {
        let decimals = (5 - exponent).max(0) as usize;
        let fixed = format!("{value:.decimals$}");
        if fixed.contains('.') {
            fixed.trim_end_matches('0').trim_end_matches('.').to_owned()
        } else {
            fixed
        }
    }
}
fn validate_vbelt(kind: &str, params: &Map<String, Value>, path: &str, errors: &mut Vec<Value>) {
    if kind != "vbelt_pulley"
        || !(truthy(params.get("groove_type").unwrap_or(&Value::Null))
            || truthy(params.get("outer_d").unwrap_or(&Value::Null)))
    {
        return;
    }
    let Some(pitch) = number(params.get("pitch_d").unwrap_or(&Value::Null)) else {
        return;
    };
    let Some(grooves) = number(params.get("grooves").unwrap_or(&Value::Null)) else {
        return;
    };
    let Some(height) = number(params.get("height").unwrap_or(&Value::Null)) else {
        return;
    };
    let g = params
        .get("groove_type")
        .and_then(Value::as_str)
        .unwrap_or("Z")
        .to_ascii_uppercase();
    let (bd, hf, e): (f64, f64, f64) = match g.as_str() {
        "Y" => (5.3, 4.7, 8.0),
        "Z" | "SPZ" => (8.5, 7.0, 12.0),
        "A" | "SPA" => (11.0, 8.7, 15.0),
        "B" | "SPB" => (14.0, 10.8, 19.0),
        "C" | "SPC" => (19.0, 14.3, 25.5),
        "D" => (27.0, 19.9, 37.0),
        "E" => (32.0, 23.4, 44.5),
        _ => return,
    };
    let angle: f64 = if pitch <= 60.0 {
        32.0
    } else if pitch <= 118.0 {
        34.0
    } else if pitch <= 190.0 {
        36.0
    } else {
        38.0
    };
    let root = bd + 2.0 * hf * (angle.to_radians() / 2.0).tan();
    let min_width = (grooves - 1.0) * e.max(root + 0.5) + root + 0.5;
    if height + 1e-8 < min_width {
        append_error(
            errors,
            "vbelt_width_below_profile_minimum",
            format!("{path}.parameters.height"),
            format!(
                "输入轮宽 {} mm 无法容纳 {} 个标准 V 槽；无自交剖面的最小轮宽为 {} mm。",
                python_g(height),
                grooves as i64,
                python_g(min_width)
            ),
        );
    }
}

fn validate_sources(
    sources_value: Option<&Value>,
    params: &Map<String, Value>,
    fid: &str,
    path: &str,
    errors: &mut Vec<Value>,
    confirmations: &mut Vec<Value>,
) {
    let sources = sources_value.and_then(Value::as_object);
    if sources.is_none() && params.values().any(contains_number) {
        append_confirmation(
            confirmations,
            fid,
            "sources",
            "请为数值参数标明来源：user、image_estimate、derived 或 existing_model。",
        );
    }
    let sources = sources.cloned().unwrap_or_default();
    for (key, value) in params {
        if !contains_number(value) {
            continue;
        }
        match sources.get(key) {
            None | Some(Value::Null) => append_confirmation(
                confirmations,
                fid,
                key,
                format!("请标明参数 {key} 的数值来源。"),
            ),
            Some(Value::Object(source)) => {
                if source.get("type").and_then(Value::as_str) == Some("image_estimate")
                    && !source
                        .get("evidence")
                        .and_then(Value::as_str)
                        .is_some_and(|v| !v.trim().is_empty())
                {
                    append_confirmation(
                        confirmations,
                        fid,
                        key,
                        format!("图像估算参数 {key} 请补充视图/位置证据。"),
                    );
                }
            }
            _ => {}
        }
    }
    for (key, source) in &sources {
        let spath = format!("{path}.sources.{key}");
        if !params.contains_key(key) {
            append_error(
                errors,
                "orphan_parameter_source",
                spath,
                "来源字段必须对应一个参数。",
            );
            continue;
        }
        let Some(source) = source.as_object() else {
            append_error(
                errors,
                "invalid_parameter_source",
                spath,
                "参数来源必须是对象。",
            );
            continue;
        };
        let stype = source.get("type").and_then(Value::as_str).unwrap_or("");
        if !["user", "image_estimate", "derived", "existing_model"].contains(&stype) {
            append_error(
                errors,
                "invalid_parameter_source_type",
                format!("{spath}.type"),
                "来源 type 必须是 user、image_estimate、derived 或 existing_model。",
            );
        }
        for key in source.keys() {
            if !["type", "evidence", "note"].contains(&key.as_str()) {
                append_error(
                    errors,
                    "unknown_field",
                    format!("{spath}.{key}"),
                    "来源对象只接受 type、evidence 和 note。",
                );
            }
        }
        if source
            .get("evidence")
            .is_some_and(|v| !v.is_null() && !v.is_string())
        {
            append_error(
                errors,
                "invalid_source_evidence",
                format!("{spath}.evidence"),
                "evidence 必须是字符串。",
            );
        }
        if source
            .get("note")
            .is_some_and(|v| !v.is_null() && !v.is_string())
        {
            append_error(
                errors,
                "invalid_source_note",
                format!("{spath}.note"),
                "note 必须是字符串。",
            );
        }
        if stype == "existing_model"
            && !source
                .get("note")
                .and_then(Value::as_str)
                .is_some_and(|v| !v.trim().is_empty())
        {
            append_confirmation(
                confirmations,
                fid,
                key,
                format!("继承参数 {key} 请说明来自当前模型特征树；原始来源未知时请明确注明。"),
            );
        }
    }
}

fn contains_number(value: &Value) -> bool {
    match value {
        Value::Number(_) => true,
        Value::Array(rows) => rows.iter().any(contains_number),
        Value::Object(rows) => rows.values().any(contains_number),
        _ => false,
    }
}
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(v) => *v,
        Value::Number(v) => v.as_f64().is_some_and(|n| n != 0.0),
        Value::String(v) => !v.is_empty(),
        Value::Array(v) => !v.is_empty(),
        Value::Object(v) => !v.is_empty(),
    }
}

fn validate_relations(
    relations_value: Option<&Value>,
    features: &[Value],
    index: usize,
    ids: &HashMap<String, usize>,
    kind: &str,
    operation: &str,
    fid: &str,
    path: &str,
    errors: &mut Vec<Value>,
    confirmations: &mut Vec<Value>,
) {
    let relations = match relations_value {
        None => &[][..],
        Some(Value::Array(rows)) => rows.as_slice(),
        Some(_) => {
            append_error(
                errors,
                "invalid_relations",
                format!("{path}.relations"),
                "relations 必须是数组，例如：[{\"type\":\"pattern_source\",\"feature_id\":\"上一个特征ID\"}]。",
            );
            &[]
        }
    };
    let mut pattern_sources = Vec::<String>::new();
    let relation_error_message = if kind == "shell" {
        "关系项必须使用 type 字段（depends_on、pattern_source、shell_target）和 feature_id 字段，例如：{\"type\":\"pattern_source\",\"feature_id\":\"上一个特征ID\"}。"
    } else {
        "关系项必须使用 type 字段（depends_on、pattern_source）和 feature_id 字段，例如：{\"type\":\"pattern_source\",\"feature_id\":\"上一个特征ID\"}。"
    };
    for (ri, relation) in relations.iter().enumerate() {
        let rpath = format!("{path}.relations[{ri}]");
        let Some(row) = relation.as_object() else {
            append_error(
                errors,
                "invalid_relation",
                rpath,
                relation_error_message,
            );
            continue;
        };
        let rtype = row.get("type").and_then(Value::as_str).unwrap_or("");
        if rtype != "depends_on"
            && rtype != "pattern_source"
            && !(kind == "shell" && rtype == "shell_target")
        {
            append_error(
                errors,
                "invalid_relation",
                rpath,
                relation_error_message,
            );
            continue;
        }
        for key in row.keys() {
            if key != "type" && key != "feature_id" {
                append_error(
                    errors,
                    "unknown_field",
                    format!("{rpath}.{key}"),
                    "关系对象只接受 type 和 feature_id。",
                );
            }
        }
        let target = row.get("feature_id").and_then(Value::as_str).unwrap_or("");
        if !ids
            .get(target)
            .is_some_and(|target_index| *target_index < index)
        {
            append_error(
                errors,
                "invalid_relation_target",
                format!("{rpath}.feature_id"),
                "feature_id 必须填写此前定义特征的 id；pattern_source 还必须指向紧邻的前一个特征。",
            );
        }
        if rtype == "pattern_source" {
            pattern_sources.push(target.into());
        }
    }
    if kind == "pattern" {
        if pattern_sources.len() != 1 {
            append_confirmation(
                confirmations,
                fid,
                "relations",
                "阵列必须用唯一的 pattern_source 关系指定源特征。",
            );
        } else if index == 0
            || features
                .get(index.wrapping_sub(1))
                .and_then(|r| r.get("id"))
                .and_then(Value::as_str)
                != Some(pattern_sources[0].as_str())
        {
            append_error(
                errors,
                "pattern_source_order",
                format!("{path}.relations"),
                "当前 Document 阵列只复制紧邻的前一特征；请调整顺序或 source 关系。",
            );
        } else {
            let source = features[index - 1]
                .get("operation")
                .and_then(Value::as_str)
                .unwrap_or("");
            if source != operation && (source == "add" || source == "cut") {
                append_error(
                    errors,
                    "pattern_operation_mismatch",
                    format!("{path}.operation"),
                    format!(
                        "阵列 operation 必须与 pattern_source 源特征「{}」一致：源操作是 {source}，阵列也必须用 {source}。切孔阵列必须为 cut；若写 add，程序会把孔的圆柱刀具加成实体。",
                        pattern_sources[0]
                    ),
                );
            }
        }
    }
}

fn catia_support(spec: &Spec, params: &Map<String, Value>) -> (&'static str, String) {
    let axis = params
        .get("axis")
        .and_then(Value::as_str)
        .unwrap_or("Z")
        .to_ascii_uppercase();
    let kind = spec.kind;
    let mut status = spec.catia_status;
    if status != "exact" {
        if ["revolve", "loft"].contains(&kind) && axis != "Z" {
            status = "unsupported";
        }
        return (status, catia_reason(spec, status, &axis, params));
    }
    if ["box", "prism", "gear", "pocket", "pattern"].contains(&kind) && axis != "Z" {
        status = "unsupported";
    }
    if kind == "pocket"
        && params
            .get("side")
            .and_then(Value::as_str)
            .unwrap_or("positive")
            .eq_ignore_ascii_case("negative")
    {
        status = "unsupported";
    }
    if kind == "pattern"
        && params.get("mode").and_then(Value::as_str) == Some("circular")
        && axis != "Z"
    {
        status = "unsupported";
    }
    if kind == "cylinder" {
        if let (Some(r1), Some(r2)) = (
            number(params.get("radius").unwrap_or(&Value::Null)),
            number(
                params
                    .get("radius2")
                    .unwrap_or(params.get("radius").unwrap_or(&Value::Null)),
            ),
        ) {
            if (r1 - r2).abs() > 1e-9 {
                status = if axis == "Z" {
                    "approximate"
                } else {
                    "unsupported"
                };
            }
        }
    }
    (status, catia_reason(spec, status, &axis, params))
}
fn catia_reason(spec: &Spec, status: &str, axis: &str, params: &Map<String, Value>) -> String {
    if status == "unsupported"
        && !["helix", "shell"].contains(&spec.kind)
        && axis != "Z"
        && !["helix", "shell"].contains(&spec.kind)
    {
        return format!("{axis} 轴上的该特征当前不能由 CATIA 转换器可靠表达。");
    }
    if spec.kind == "pocket"
        && params
            .get("side")
            .and_then(Value::as_str)
            .unwrap_or("positive")
            .eq_ignore_ascii_case("negative")
    {
        return "本地 BREP 支持从负轴面切槽；当前 CATIA 转换器只表达正轴面凹槽。".into();
    }
    if spec.kind == "cylinder" && status == "approximate" {
        return "圆锥台以多层圆盘近似。".into();
    }
    if spec.kind == "cylinder" && status == "unsupported" {
        return format!("{axis} 轴上的圆锥台当前不支持。");
    }
    if spec.kind == "pattern" && status == "unsupported" {
        return "当前 CATIA 显式阵列只支持 Z 轴阵列。".into();
    }
    spec.catia_reason.into()
}
fn local_support(
    spec: &Spec,
    params: &Map<String, Value>,
    kind: &str,
    relations: Option<&Value>,
    selection_binding: Option<&Value>,
    geometry_variant: Option<&str>,
    schema_version: &str,
) -> (&'static str, &'static str) {
    let mut status = spec.local_status;
    let mut reason = spec.local_reason;
    if kind == "conrod" && geometry_variant == Some("legacy_quick_rectangular_shank") {
        reason = "解析大小头环与旧 Quick 全长矩形杆身并集；工字截面和锻造过渡仍为近似";
    }
    if kind == "crankshaft" && geometry_variant == Some("legacy_quick_dumbbell_cheeks") {
        reason = "解析主轴颈与偏心连杆颈，并以旧 Quick 哑铃形曲柄颊截面沿 X 棱柱化；倒角和锻造过渡仍为近似";
    }
    if ["box", "pocket"].contains(&kind)
        && number(params.get("corner").unwrap_or(&Value::Null)).is_some_and(|v| v > 0.0)
    {
        status = "approximate";
        reason = if kind == "box" {
            "圆角/倒角来自离散轮廓，主体由 OCCT 棱柱生成。"
        } else {
            "圆角/倒角轮廓已离散后执行 OCCT BREP 切除。"
        };
    }
    if kind == "pattern"
        && !relations
            .and_then(Value::as_array)
            .is_some_and(|rows| rows.iter().any(|row| row["type"] == "pattern_source"))
    {
        status = "unavailable";
        reason = "CADPlan pattern 必须声明唯一 pattern_source 关系。";
    }
    if kind == "shell" {
        let explicit = schema_version == "1.1"
            && ["offset_mode", "removed_faces"]
                .iter()
                .all(|key| params.contains_key(*key))
            && relations.and_then(Value::as_array).is_some_and(|rows| {
                rows.iter()
                    .filter(|row| row.get("type").and_then(Value::as_str) == Some("shell_target"))
                    .count()
                    == 1
            })
            && selection_binding.is_some();
        if explicit {
            status = "exact";
            reason = "原生 OCCT shell：仅支持显式 shell_target、当前 revision/fingerprint 绑定的平面移除面和显式 inward/outward 偏置。";
        } else {
            status = "unavailable";
            reason = "旧 v1.0 thickness-only shell 不支持自动迁移；CADPlan 1.1 shell 须重新枚举面并显式指定 shell_target 与 offset_mode。";
        }
    }
    (status, reason)
}
fn schema_document_impl() -> Value {
    let alternatives: SVec = SPECS.iter().map(feature_schema_for).collect();
    let units = json!({"type":"object","properties":{"length":{"type":"string","enum":["mm","cm","m","in"]},"angle":{"type":"string","enum":["deg","rad"]}},"required":["length","angle"],"additionalProperties":false});
    let mut schema = json!({
        "$schema":"https://json-schema.org/draft/2020-12/schema",
        "$id":"https://ai-modeler.local/schemas/cadplan-1.1.schema.json",
        "title":"AI Modeler CADPlan",
        "description":"Provider-neutral, data-only plan for validated CATIA V5 construction. CADPlan 1.0 remains readable; its thickness-only shell requires confirmation and is never migrated by guessing.",
        "type":"object",
        "properties":{
            "schema_version":{"type":"string","enum":["1.0","1.1"]},
            "name":{"type":"string","minLength":1},
            "source_prompt":{"type":"string"},
            "units":units,
            "coordinate_system":{"type":"object","properties":{"type":{"const":"global_cartesian"},"handedness":{"const":"right"},"axes":{"const":["X","Y","Z"]},"origin":{"const":[0,0,0]}},"required":["type","handedness","axes","origin"],"additionalProperties":false},
            "features":{"type":"array","minItems":1,"items":{"oneOf":alternatives}},
            "pending_questions":{"type":"array","items":{"type":"string"}}
        },
        "required":["schema_version","name","units","coordinate_system","features"],
        "additionalProperties":false,
        "$defs":{
            "source":{"type":"object","properties":{"type":{"type":"string","enum":["user","image_estimate","derived","existing_model"]},"evidence":{"type":"string"},"note":{"type":"string"}},"required":["type"],"additionalProperties":false,"allOf":[{"if":{"properties":{"type":{"const":"image_estimate"}},"required":["type"]},"then":{"required":["evidence"]}},{"if":{"properties":{"type":{"const":"existing_model"}},"required":["type"]},"then":{"required":["note"]}}]},
            "relation":{"type":"object","properties":{"type":{"type":"string","enum":["depends_on","pattern_source","shell_target"]},"feature_id":{"type":"string","minLength":1}},"required":["type","feature_id"],"additionalProperties":false}
        },
        "x-feature-support-matrix":support_matrix()
    });
    schema["allOf"] = json!([{
        "if":{"properties":{"schema_version":{"const":"1.1"}},"required":["schema_version"]},
        "then":{"properties":{"features":{"items":{"allOf":[{
            "if":{"properties":{"kind":{"const":"shell"}},"required":["kind"]},
            "then":{
                "required":["selection_binding"],
                "properties":{
                    "operation":{"const":"cut"},
                    "parameters":{"required":["thickness","offset_mode","removed_faces"]},
                    "relations":{
                        "contains":{"type":"object","properties":{"type":{"const":"shell_target"}},"required":["type","feature_id"]},
                        "minContains":1,
                        "maxContains":1
                    }
                }
            }
        }]}}}}
    }]);
    schema
}
type SVec = Vec<Value>;
fn support_matrix() -> Value {
    let mut map = Map::new();
    for spec in SPECS {
        map.insert(spec.kind.into(),json!({"status":spec.catia_status,"reason":spec.catia_reason,"required_parameters":words(spec.required)}));
    }
    Value::Object(map)
}
fn feature_schema_for(spec: &Spec) -> Value {
    let props = parameter_property_map();
    let mut pp = Map::new();
    for key in words(spec.params) {
        if let Some(v) = props.get(key) {
            pp.insert(key.to_owned(), v.clone());
        }
    }
    if spec.kind == "shell" {
        pp.insert(
            "offset_mode".into(),
            json!({"type":"string","enum":["inward","outward"]}),
        );
        pp.insert(
            "removed_faces".into(),
            json!({"type":"array","minItems":1,"maxItems":256,
            "items":{"type":"string","pattern":"^shell-face-v1:[0-9a-f]{64}$"},"uniqueItems":true}),
        );
    }
    let mut conditional_required: Vec<&str> = words(spec.required);
    if spec.kind == "hole" {
        conditional_required = vec!["diameter", "through", "pos"];
    } else if spec.kind == "pattern" {
        conditional_required = vec!["mode", "count"];
    }
    let mut all_of = Vec::<Value>::new();
    if spec.kind == "hole" {
        all_of.extend([
            json!({"if":{"properties":{"parameters":{"properties":{"pos":{"const":"point"}},"required":["pos"]}}},"then":{"properties":{"parameters":{"required":["point"]}}}}),
            json!({"if":{"properties":{"parameters":{"properties":{"pos":{"const":"pcd"}},"required":["pos"]}}},"then":{"properties":{"parameters":{"required":["pcd","count"]}}}}),
            json!({"if":{"properties":{"parameters":{"properties":{"through":{"const":false}},"required":["through"]}}},"then":{"properties":{"parameters":{"required":["depth"]}}}}),
            json!({"if":{"properties":{"parameters":{"required":["entry_point"]}}},"then":{"properties":{"parameters":{"required":["direction","point","pos","through"],"properties":{"pos":{"const":"point"},"through":{"const":false}}}}}}),
            json!({"if":{"properties":{"parameters":{"required":["direction"]}}},"then":{"properties":{"parameters":{"required":["entry_point","point","pos","through"],"properties":{"pos":{"const":"point"},"through":{"const":false}}}}}}),
        ]);
    }
    if spec.kind == "pattern" {
        all_of.extend([json!({"if":{"properties":{"parameters":{"properties":{"mode":{"const":"linear"}},"required":["mode"]}}},"then":{"properties":{"parameters":{"required":["step"]}}}}),json!({"if":{"properties":{"parameters":{"properties":{"mode":{"const":"circular"}},"required":["mode"]}}},"then":{"properties":{"parameters":{"required":["axis","angle","center"]}}}})]);
    }
    for key in words(spec.params) {
        if is_provenance_parameter(key) {
            all_of.push(json!({"if":{"required":["parameters"],"properties":{"parameters":{"required":[key]}}},"then":{"required":["sources"],"properties":{"sources":{"required":[key]}}}}));
        }
    }
    let kind_enum = vec![
        json!({"const":spec.kind,"x-catia-support-status":spec.catia_status,"x-catia-support-note":spec.catia_reason}),
    ];
    let mut feature_properties = json!({"id":{"type":"string","minLength":1},"kind":kind_enum[0],"operation":{"type":"string","enum":["add","cut"]},"name":{"type":"string"},"note":{"type":"string"},"enabled":{"type":"boolean","default":true},"parameters":{"type":"object","properties":pp,"required":conditional_required,"additionalProperties":false},"sources":{"type":"object","propertyNames":{"enum":words(spec.params)},"additionalProperties":{"$ref":"#/$defs/source"}},"relations":{"type":"array","items":{"$ref":"#/$defs/relation"}}});
    if spec.kind == "conrod" {
        feature_properties["geometry_variant"] = json!({"type":"string","const":"legacy_quick_rectangular_shank"});
    }
    if spec.kind == "crankshaft" {
        feature_properties["geometry_variant"] = json!({"type":"string","const":"legacy_quick_dumbbell_cheeks"});
    }
    if spec.kind == "shell" {
        feature_properties["selection_binding"] = json!({"type":"object","properties":{
            "revision":{"type":"integer","minimum":0},
            "geometry_fingerprint":{"type":"string","pattern":"^[0-9a-f]{64}$"},
            "kernel_version":{"type":"string","minLength":1},
            "inventory_version":{"const":3},
            "target_feature_id":{"type":"string","minLength":1}},
            "required":["revision","geometry_fingerprint","kernel_version","inventory_version","target_feature_id"],"additionalProperties":false});
        feature_properties["relations"] = json!({"type":"array","items":{"type":"object","properties":{
            "type":{"type":"string","enum":["depends_on","pattern_source","shell_target"]},
            "feature_id":{"type":"string","minLength":1}},
            "required":["type","feature_id"],"additionalProperties":false}});
    }
    json!({"type":"object","properties":feature_properties,"required":["id","kind","operation","parameters","sources"],"additionalProperties":false,"allOf":all_of})
}
fn is_provenance_parameter(key: &str) -> bool {
    has(key, LENGTH_KEYS)
        || has(key, ANGLE_KEYS)
        || has(key, DIMENSIONLESS_KEYS)
        || has(key, INTEGER_KEYS)
        || has(key, LENGTH_ARRAY_KEYS)
        || key == "rotate"
}
fn parameter_property_map() -> Map<String, Value> {
    let mut out = Map::new();
    for key in LENGTH_KEYS
        .iter()
        .chain(ANGLE_KEYS)
        .chain(DIMENSIONLESS_KEYS)
    {
        let mut v = json!({"type":"number"});
        if has(key, LENGTH_KEYS) && !has(key, &["x", "y", "z", "base"]) {
            if has(key, NONNEGATIVE_KEYS) {
                v["minimum"] = json!(0);
            } else {
                v["exclusiveMinimum"] = json!(0);
            }
        } else if has(key, NONNEGATIVE_KEYS) {
            v["minimum"] = json!(0);
        }
        out.insert((*key).into(), v);
    }
    for key in INTEGER_KEYS {
        let mut v = json!({"type":"integer","minimum":1});
        if ["teeth", "segments", "n", "points"].contains(key) {
            v["minimum"] = json!(if *key == "teeth" { 6 } else { 3 });
        }
        if ["rings", "sections"].contains(key) {
            v["minimum"] = json!(2);
        }
        out.insert((*key).into(), v);
    }
    for key in BOOLEAN_KEYS {
        out.insert((*key).into(), json!({"type":"boolean"}));
    }
    for key in STRING_KEYS {
        out.insert((*key).into(), json!({"type":"string"}));
    }
    for key in [
        "center",
        "center2",
        "translate",
        "step",
        "point",
        "entry_point",
    ] {
        out.insert(key.into(),json!({"type":"array","prefixItems":[{"type":"number"},{"type":"number"},{"type":"number"}],"minItems":3,"maxItems":3,"items":false}));
    }
    out.insert("profile".into(),json!({"type":"array","minItems":3,"items":{"type":"array","prefixItems":[{"type":"number"},{"type":"number"}],"minItems":2,"maxItems":2,"items":false}}));
    out.insert("rotate".into(),json!({"type":"array","prefixItems":[{"type":"string","enum":["X","Y","Z"]},{"type":"number"}],"minItems":2,"maxItems":2,"items":false}));
    for (key, vals) in [
        ("axis", vec!["X", "Y", "Z"]),
        ("corner_mode", vec!["chamfer", "fillet"]),
        ("mode", vec!["circular", "linear"]),
        ("pos", vec!["center", "corners", "pcd", "point"]),
        (
            "groove_type",
            vec![
                "A", "B", "C", "D", "E", "SPZ", "SPA", "SPB", "SPC", "Y", "Z",
            ],
        ),
        (
            "tooth_type",
            vec![
                "14M", "3M", "5M", "8M", "H", "L", "MXL", "T10", "T2.5", "T20", "T5", "XL", "XH",
                "XXH",
            ],
        ),
        ("side", vec!["negative", "positive"]),
        ("direction", vec!["negative", "positive"]),
    ] {
        out.insert(key.into(), json!({"type":"string","enum":vals}));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    fn base(feature: Value) -> Value {
        json!({"schema_version":"1.0","name":"fixture","source_prompt":"","units":{"length":"mm","angle":"deg"},"coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},"features":[feature]})
    }
    fn feature(kind: &str, params: Value, sources: Value) -> Value {
        json!({"id":"f1","kind":kind,"operation":"add","parameters":params,"sources":sources,"relations":[]})
    }
    #[test]
    fn python_percent_g_format_uses_six_significant_digits() {
        assert_eq!(python_g(2.0), "2");
        assert_eq!(python_g(200000.0), "200000");
        assert_eq!(python_g(39.043306), "39.0433");
        assert_eq!(python_g(0.0001), "0.0001");
        assert_eq!(python_g(0.00001), "1e-05");
        assert_eq!(python_g(-0.0), "-0");
    }

    #[test]
    fn python_geometry_report_support_rows_match_golden() {
        // Golden support rows copied from temp/evidence-archive/artifacts/cadplan/20261001-135817-SDK-test/SDK-test.geometry-report.json.
        let mut plan = base(feature(
            "box",
            json!({"length":100,"width":80,"height":60}),
            json!({"length":{"type":"user"},"width":{"type":"user"},"height":{"type":"user"}}),
        ));
        plan["features"][0]["id"] = json!("base");
        let mut shell = feature(
            "shell",
            json!({"thickness":2}),
            json!({"thickness":{"type":"user"}}),
        );
        shell["id"] = json!("target");
        plan["features"].as_array_mut().unwrap().push(shell);
        let result = validate_plan(&plan);
        assert_eq!(
            result["support"],
            json!([
                {"feature_id":"base","kind":"box","status":"exact","reason":"矩形草图加凸台；当前 CATIA 映射仅支持 Z 轴拉伸。"},
                {"feature_id":"target","kind":"shell","status":"unsupported","reason":"当前 Document/CATIA 转换器没有抽壳实现。"}
            ])
        );
        assert!(result["warnings"]
            .as_array()
            .unwrap()
            .iter()
            .any(|row| row["code"] == "LOCAL_BREP_UNAVAILABLE" && row["feature_id"] == "target"));
        assert!(
            result["warnings"]
                .as_array()
                .unwrap()
                .iter()
                .any(|row| row["code"] == "CATIA_UNSUPPORTED" && row["feature_id"] == "target")
        );
    }
    #[test]
    fn guards_units_coordinate_and_unknown_keys() {
        let mut value = base(feature(
            "box",
            json!({"length":1,"width":1,"height":1,"extra":4}),
            json!({"length":{"type":"user"},"width":{"type":"user"},"height":{"type":"user"}}),
        ));
        value["units"]["length"] = json!("ft");
        value["coordinate_system"]["axes"] = json!(["Y", "X", "Z"]);
        let report = validate_plan(&value);
        let codes: Vec<_> = report["errors"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|r| r["code"].as_str())
            .collect();
        assert!(codes.contains(&"unsupported_length_unit"));
        assert!(codes.contains(&"unsupported_axis_order"));
        assert!(codes.contains(&"unknown_parameter"));
    }
    #[test]
    fn conditional_hole_pattern_and_provenance_confirmation() {
        let h = base(feature(
            "hole",
            json!({"diameter":4,"through":false,"pos":"point","point":[0,0,0]}),
            json!({"diameter":{"type":"image_estimate"}}),
        ));
        let r = validate_plan(&h);
        assert_eq!(r["state"], "needs_confirmation");
        let qs = r["confirmations"].as_array().unwrap();
        assert!(qs.iter().any(|q| q["parameter"] == "depth"));
        assert!(qs.iter().any(|q| q["parameter"] == "diameter"));
        let p = base(feature(
            "pattern",
            json!({"mode":"linear","count":3}),
            json!({"count":{"type":"user"}}),
        ));
        assert!(
            validate_plan(&p)["confirmations"]
                .as_array()
                .unwrap()
                .iter()
                .any(|q| q["parameter"] == "step")
        );
    }
    #[test]
    fn relation_precedence_and_strict_support_gate() {
        let first = feature(
            "box",
            json!({"length":2,"width":2,"height":2}),
            json!({"length":{"type":"user"},"width":{"type":"user"},"height":{"type":"user"}}),
        );
        let mut revolve = feature(
            "revolve",
            json!({"profile":[[1,0],[2,0],[2,1]],"angle":360}),
            json!({"angle":{"type":"user"},"profile":{"type":"derived"}}),
        );
        let mut p = base(first);
        revolve["id"] = json!("f2");
        p["features"].as_array_mut().unwrap().push(revolve);
        let r = validate_plan(&p);
        assert_eq!(r["state"], "valid");
        assert_eq!(strict_preflight(&p)["state"], "strict_blocked");
        let mut bad = p.clone();
        bad["features"][1]["relations"] = json!([{"type":"depends_on","feature_id":"missing"}]);
        assert!(
            validate_plan(&bad)["errors"]
                .as_array()
                .unwrap()
                .iter()
                .any(|e| e["code"] == "invalid_relation_target")
        );
    }
    fn json_semantic_eq(left: &Value, right: &Value) -> bool {
        match (left, right) {
            (Value::Number(a), Value::Number(b)) => a.as_f64() == b.as_f64(),
            (Value::Array(a), Value::Array(b)) => {
                a.len() == b.len() && a.iter().zip(b).all(|(x, y)| json_semantic_eq(x, y))
            }
            (Value::Object(a), Value::Object(b)) => {
                a.len() == b.len()
                    && a.iter().all(|(key, value)| {
                        b.get(key)
                            .is_some_and(|other| json_semantic_eq(value, other))
                    })
            }
            _ => left == right,
        }
    }

    #[test]
    fn python_validation_and_strict_preflight_full_golden_corpus() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../examples/fixtures/cad_plan_validation_golden.json"
        ))
        .unwrap();
        assert_eq!(fixture["fixture_format"], "cadplan-validation-golden-v1");
        assert_eq!(
            fixture["feature_kinds"],
            json!(SPECS.iter().map(|spec| spec.kind).collect::<Vec<_>>())
        );
        let cases = fixture["cases"].as_array().expect("cases array");
        assert_eq!(cases.len(), 27);
        for case in cases {
            let name = case["name"].as_str().unwrap_or("unnamed");
            let actual = validate_plan(&case["plan"]);
            assert!(
                json_semantic_eq(&actual, &case["expected"]),
                "validate_plan mismatch in case {name}\nexpected: {expected}\nactual: {actual}",
                expected = case["expected"],
                actual = actual
            );
            let strict = strict_preflight(&case["plan"]);
            assert!(
                json_semantic_eq(&strict, &case["strict_expected"]),
                "strict preflight mismatch in case {name}\nexpected: {expected}\nactual: {actual}",
                expected = case["strict_expected"],
                actual = strict
            );
        }
    }
    #[test]
    fn quick_crankshaft_dumbbell_geometry_variant_is_scoped_and_schema_declared() {
        let mut plan = base(feature(
            "crankshaft",
            json!({"length":200,"main_d":50,"pin_d":42,"stroke":86,"throws":2,"center":[0,0,0],"segments":32}),
            json!({"length":{"type":"user"},"main_d":{"type":"user"},"pin_d":{"type":"user"},"stroke":{"type":"user"},"throws":{"type":"user"},"center":{"type":"derived"},"segments":{"type":"derived"}}),
        ));
        plan["features"][0]["geometry_variant"] = json!("legacy_quick_dumbbell_cheeks");
        assert_eq!(validate_plan(&plan)["valid"], json!(true));
        assert_eq!(feature_schema("crankshaft").unwrap()["properties"]["geometry_variant"]["const"], json!("legacy_quick_dumbbell_cheeks"));
        plan["features"][0]["geometry_variant"] = json!("unexpected");
        assert_eq!(validate_plan(&plan)["state"], json!("invalid"));
        let mut wrong_kind = base(feature("box",
            json!({"length":100,"width":60,"height":20}),
            json!({"length":{"type":"user"},"width":{"type":"user"},"height":{"type":"user"}}),
        ));
        wrong_kind["features"][0]["geometry_variant"] = json!("legacy_quick_dumbbell_cheeks");
        assert_eq!(validate_plan(&wrong_kind)["state"], json!("invalid"));
    }

    #[test]
    fn quick_conrod_geometry_variant_is_scoped_and_schema_declared() {
        let mut plan = base(feature(
            "conrod",
            json!({"length":80,"big_d":30,"small_d":18,"big_bore":20,"small_bore":10,"shank_w":10,"thick":8,"center":[0,0,0],"segments":32}),
            json!({"length":{"type":"user"},"big_d":{"type":"user"},"small_d":{"type":"user"},"big_bore":{"type":"user"},"small_bore":{"type":"user"},"shank_w":{"type":"user"},"thick":{"type":"user"},"center":{"type":"derived"},"segments":{"type":"derived"}}),
        ));
        plan["features"][0]["geometry_variant"] = json!("legacy_quick_rectangular_shank");
        assert_eq!(validate_plan(&plan)["valid"], json!(true));
        assert_eq!(feature_schema("conrod").unwrap()["properties"]["geometry_variant"]["const"], json!("legacy_quick_rectangular_shank"));
        plan["features"][0]["geometry_variant"] = json!("unexpected");
        assert_eq!(validate_plan(&plan)["state"], json!("invalid"));
        plan["features"][0]["kind"] = json!("box");
        assert_eq!(validate_plan(&plan)["state"], json!("invalid"));
    }

    #[test]
    fn schema_contains_all_28_public_feature_kinds_and_source_guards() {
        let schema = schema_document();
        assert_eq!(
            schema["$id"],
            "https://ai-modeler.local/schemas/cadplan-1.1.schema.json"
        );
        assert_eq!(
            schema["properties"]["schema_version"]["enum"],
            json!(["1.0", "1.1"])
        );
        let alternatives = schema
            .pointer("/properties/features/items/oneOf")
            .unwrap()
            .as_array()
            .unwrap();
        assert_eq!(alternatives.len(), 28);
        assert!(feature_schema("shell").is_some());
        assert!(feature_schema("not_public").is_none());
        assert!(schema.to_string().contains("image_estimate"));
        assert_eq!(SPECS.len(), 28);
    }
    #[test]
    fn shell_v1_migration_requires_confirmation_and_v11_schema_requires_explicit_contract() {
        let mut legacy = base(feature(
            "shell",
            json!({"thickness":2}),
            json!({"thickness":{"type":"user"}}),
        ));
        let legacy_report = validate_plan(&legacy);
        assert_eq!(legacy_report["state"], "needs_confirmation");
        assert_eq!(legacy_report["schema_version"], "1.0");
        assert!(
            legacy_report["confirmations"]
                .as_array()
                .unwrap()
                .iter()
                .any(|item| item["parameter"] == "shell_migration")
        );

        legacy["schema_version"] = json!("1.1");
        let mut explicit = feature(
            "shell",
            json!({"thickness":2,"offset_mode":"inward","removed_faces":[format!("shell-face-v1:{}", "a".repeat(64))]}),
            json!({"thickness":{"type":"user"}}),
        );
        explicit["id"] = json!("shell-1");
        explicit["operation"] = json!("cut");
        explicit["relations"] = json!([{"type":"shell_target","feature_id":"base"}]);
        explicit["selection_binding"] = json!({
            "revision":4,
            "geometry_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "kernel_version":"7.9.3",
            "inventory_version":3,
            "target_feature_id":"base"
        });
        let mut plan = base(feature(
            "box",
            json!({"length":100,"width":80,"height":60}),
            json!({"length":{"type":"user"},"width":{"type":"user"},"height":{"type":"user"}}),
        ));
        plan["schema_version"] = json!("1.1");
        plan["features"][0]["id"] = json!("base");
        plan["features"]
            .as_array_mut()
            .unwrap()
            .push(explicit.clone());
        assert_eq!(validate_plan(&plan)["state"], "valid");
        let missing_direction = {
            let mut value = plan.clone();
            value["features"][1]["parameters"]
                .as_object_mut()
                .unwrap()
                .remove("offset_mode");
            value
        };
        assert_eq!(validate_plan(&missing_direction)["state"], "invalid");
        let stale_target = {
            let mut value = plan.clone();
            value["features"][1]["selection_binding"]["target_feature_id"] = json!("other");
            value
        };
        assert_eq!(validate_plan(&stale_target)["state"], "invalid");

        let schema = schema_document();
        let shell_schema = schema["properties"]["features"]["items"]["oneOf"]
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["properties"]["kind"]["const"] == "shell")
            .unwrap();
        assert_eq!(
            shell_schema["properties"]["selection_binding"]["properties"]["inventory_version"]["const"],
            3
        );
        assert_eq!(
            shell_schema["properties"]["parameters"]["properties"]["removed_faces"]["uniqueItems"],
            true
        );
        assert!(schema["allOf"].to_string().contains("shell_target"));
    }
    #[test]
    fn boolean_base_matches_actual_python_validation() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../examples/fixtures/cadplan_base_validation_golden.json"
        ))
        .unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            assert!(
                json_semantic_eq(&validate_plan(&case["plan"]), &case["expected"]),
                "base validation differs for {}",
                case["name"]
            );
        }
    }

    #[test]
    fn cylinder_zero_radius2_is_valid_for_a_pointed_cone() {
        let cone = base(feature(
            "cylinder",
            json!({"radius":25,"radius2":0,"height":50,"center":[0,0,0],"base":true,"axis":"Z","segments":48}),
            json!({"radius":{"type":"derived"},"radius2":{"type":"derived"},"height":{"type":"derived"},"center":{"type":"derived"},"segments":{"type":"derived"}}),
        ));
        assert_eq!(validate_plan(&cone)["state"], "valid");

        let mut invalid = cone.clone();
        invalid["features"][0]["parameters"]["radius2"] = json!(-0.1);
        let report = validate_plan(&invalid);
        assert_eq!(report["state"], "invalid");
        assert!(report["errors"].to_string().contains("negative_dimension"));

        let schema = feature_schema("cylinder").unwrap();
        assert_eq!(
            schema["properties"]["parameters"]["properties"]["radius2"]["minimum"],
            0
        );
    }
}
