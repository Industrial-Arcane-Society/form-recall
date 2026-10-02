//! Native Cordis tool schemas, mirrored from the legacy Python backend.
//!
//! Source of truth during extraction: `ai_modeler.agent.dsh_tool_backend.schemas()`
//! in `src/legacy/ai_modeler/agent/dsh_tool_backend.py`, which returns
//! `dsh_tool_domain.TOOLS`. Keep descriptions, inputSchema, and required fields
//! aligned when the tool contract changes.

use serde_json::Value;

const SCHEMAS_JSON: &str = r###"[{"name":"cad_get_state","description":"读取当前 CADPlan 特征树、尺寸、校验结果和未解决复核项。","inputSchema":{"type":"object","properties":{},"additionalProperties":false}},{"name":"cad_search_knowledge","description":"在本地工件知识库搜索精确型号或相似描述。精确命中会返回类别、型号、实测包围盒和可用的真实几何摘要；相似结果仅作上下文参考，不可直接复制尺寸。库外型号应明确返回未命中。可选返回带标签的真实照片/模型渲染样例图板供视觉比较。","inputSchema":{"type":"object","properties":{"query":{"type":"string","description":"工件描述或型号；纯图片查询可传空字符串。"},"limit":{"type":"integer","minimum":1,"maximum":10},"include_visual_examples":{"type":"boolean"}},"additionalProperties":false}},{"name":"cad_search_recipes","description":"搜索本地常用建模配方库，返回螺纹、孔、沉孔和标准件的推荐特征顺序、适用条件与当前能力边界。","inputSchema":{"type":"object","properties":{"query":{"type":"string","description":"操作或零件名，例如外螺纹、盲孔、内六角螺钉。"},"limit":{"type":"integer","minimum":1,"maximum":20}},"required":["query"],"additionalProperties":false}},{"name":"cad_apply_recipe","description":"一次调用展开并校验本地确定性建模配方，避免让模型逐项手拼标准件。目前可直接生成标准表覆盖的 ISO 4762/GB/T 70.1 内六角圆柱头螺钉；例如 recipe_id=fastener.socket_head_cap_screw,size=M5x8。生成后应立即预览。本地网格会按标准螺距生成可见的近似螺旋牙型；当前 CATIA 导出通道仍输出光杆，必须在结果中区分本地网格与 CATPart 的螺纹状态。","inputSchema":{"type":"object","properties":{"recipe_id":{"type":"string","enum":["fastener.socket_head_cap_screw"]},"size":{"type":"string","description":"标准型号，例如 M5x8 或 M10×30。"},"name":{"type":"string"},"source_prompt":{"type":"string"},"replace":{"type":"boolean"}},"required":["recipe_id","size"],"additionalProperties":false}},{"name":"cad_create_model","description":"新建空会话模型。已有CADPlan时，单参数/局部返修必须用增量工具；不得用本工具绕过provenance、失败守卫或未解决finding。仅当当前可信用户turn明确要求新工件或从头整体重建时才可replace=true。坐标固定为 X=左右、Y=前后厚度、Z=上下；box.length/width/height分别对应X/Y/Z。","inputSchema":{"type":"object","properties":{"name":{"type":"string"},"first_feature":{"type":"object","description":"One CADPlan feature: id, kind, operation(add/cut), name, note, parameters, sources, relations. Coordinate contract: X left-right, Y front-back, Z vertical; box length/width/height map to X/Y/Z; hole point is global XYZ and hole axis is the through direction. A changed source of type user must include the trusted user turn_id; the transport adapter consumes that field and does not store it in CADPlan.","properties":{"id":{"type":"string","minLength":1},"kind":{"type":"string","enum":["bevel_gear","box","camshaft","conrod","crankshaft","cross_joint","cylinder","gear","helical_gear","helix","hole","impeller","loft","pattern","piston","pocket","prism","revolve","rotor","scroll_disc","shell","socket_head_bolt","sphere","spoked_wheel","timing_pulley","vbelt_pulley","worm"]},"operation":{"type":"string","enum":["add","cut"]},"name":{"type":"string"},"note":{"type":"string"},"parameters":{"type":"object","description":"Geometry parameters. box: length=X, width=Y, height=Z, center=[X,Y,Z]. hole pos=point: point=[X,Y,Z], axis is the bore direction, through=true for a through-hole. cylinder/cone: base defaults true, center is the base-center point and height extends along +axis; use base=false when center means the geometric midpoint. prism: center is the profile base-plane origin; height extends along +axis. revolve about Z: profile is a closed list of [radius, axial-Z] points; center is the global axis origin. For a turned main body, create the complete profile as the first feature. sphere: center is the geometric center.","additionalProperties":true},"sources":{"type":"object","description":"Numeric parameter provenance, keyed by parameter name. For changed type=user values, include the trusted user turn_id as transport-only metadata.","additionalProperties":{"type":"object","description":"CADPlan fields are type/evidence/note; optional turn_id is temporary tool metadata and is stripped before CADPlan validation."}},"relations":{"type":"array","items":{"type":"object"}}},"required":["id","kind","operation","parameters"],"additionalProperties":false},"source_prompt":{"type":"string"},"units":{"type":"object"},"replace":{"type":"boolean"}},"required":["name","first_feature"],"additionalProperties":false}},{"name":"cad_add_feature","description":"向当前特征树增量添加一个 CADPlan 特征；数值参数必须有来源。预览和视觉复核由 Agent 按施工里程碑自行选择，不作为逐特征门禁。坐标固定为 X=左右、Y=前后厚度、Z=上下。","inputSchema":{"type":"object","properties":{"feature":{"type":"object","description":"One CADPlan feature: id, kind, operation(add/cut), name, note, parameters, sources, relations. Coordinate contract: X left-right, Y front-back, Z vertical; box length/width/height map to X/Y/Z; hole point is global XYZ and hole axis is the through direction. A changed source of type user must include the trusted user turn_id; the transport adapter consumes that field and does not store it in CADPlan.","properties":{"id":{"type":"string","minLength":1},"kind":{"type":"string","enum":["bevel_gear","box","camshaft","conrod","crankshaft","cross_joint","cylinder","gear","helical_gear","helix","hole","impeller","loft","pattern","piston","pocket","prism","revolve","rotor","scroll_disc","shell","socket_head_bolt","sphere","spoked_wheel","timing_pulley","vbelt_pulley","worm"]},"operation":{"type":"string","enum":["add","cut"]},"name":{"type":"string"},"note":{"type":"string"},"parameters":{"type":"object","description":"Geometry parameters. box: length=X, width=Y, height=Z, center=[X,Y,Z]. hole pos=point: point=[X,Y,Z], axis is the bore direction, through=true for a through-hole. cylinder/cone: base defaults true, center is the base-center point and height extends along +axis; use base=false when center means the geometric midpoint. prism: center is the profile base-plane origin; height extends along +axis. revolve about Z: profile is a closed list of [radius, axial-Z] points; center is the global axis origin. For a turned main body, create the complete profile as the first feature. sphere: center is the geometric center.","additionalProperties":true},"sources":{"type":"object","description":"Numeric parameter provenance, keyed by parameter name. For changed type=user values, include the trusted user turn_id as transport-only metadata.","additionalProperties":{"type":"object","description":"CADPlan fields are type/evidence/note; optional turn_id is temporary tool metadata and is stripped before CADPlan validation."}},"relations":{"type":"array","items":{"type":"object"}}},"required":["id","kind","operation","parameters"],"additionalProperties":false},"finding_ids":{"type":"array","items":{"type":"string"}}},"required":["feature"],"additionalProperties":false}},{"name":"cad_add_face_recess","description":"在当前实体指定的平面正面切出矩形凹入。width/height 沿该面的两个面内坐标轴，depth 沿 face_normal_axis；工具自动从对应外表面向实体内部放置切除体。预览和视觉复核由 Agent 按施工里程碑自行选择，不作为逐特征前置门禁。source_type=user 时需提供可信 turn_id，且宽、高、深数值须分别匹配用户原话。CADPlan 轴：X 左右、Y 前后、Z 上下。","inputSchema":{"type":"object","properties":{"feature_id":{"type":"string","minLength":1},"name":{"type":"string"},"face_normal_axis":{"type":"string","enum":["X","Y","Z"]},"face_side":{"type":"string","enum":["positive","negative"]},"face_center":{"type":"array","items":{"type":"number"},"minItems":2,"maxItems":2,"description":"在剩余两轴(按轴字母顺序)上的凹槽中心坐标；Y 正面用 [X,Z]。"},"width":{"type":"number","exclusiveMinimum":0},"height":{"type":"number","exclusiveMinimum":0},"depth":{"type":"number","exclusiveMinimum":0},"source_type":{"type":"string","enum":["user","image_estimate","derived","existing_model"]},"turn_id":{"type":"string","description":"source_type=user 时必填，对应当前可信用户turn。"},"evidence":{"type":"string","minLength":1},"finding_ids":{"type":"array","items":{"type":"string"}}},"required":["feature_id","face_normal_axis","face_side","face_center","width","height","depth","source_type","evidence"],"additionalProperties":false}},{"name":"cad_add_hole_array","description":"一次在同一平行正面添加一组点位孔。所有孔共用 face_normal_axis 轴向并设为贯穿；可用 face_setback 表示孔所在的局部面比外侧面后退了多少。Y 正面的 face_center 坐标为 [X,Z]。优先用它表达同一平面或平行退台面上的多孔，避免逐孔混用轴向。预览和视觉复核由 Agent 按施工里程碑自行选择，不作为逐特征前置门禁。source_type=user 时需提供可信 turn_id，孔径数值须由用户原话中对应孔径支持。","inputSchema":{"type":"object","properties":{"face_normal_axis":{"type":"string","enum":["X","Y","Z"]},"face_side":{"type":"string","enum":["positive","negative"]},"source_type":{"type":"string","enum":["user","image_estimate","derived","existing_model"]},"turn_id":{"type":"string","description":"source_type=user 时必填，对应当前可信用户turn。"},"evidence":{"type":"string","minLength":1},"finding_ids":{"type":"array","items":{"type":"string"}},"holes":{"type":"array","minItems":1,"maxItems":128,"items":{"type":"object","properties":{"id":{"type":"string","minLength":1},"name":{"type":"string"},"face_center":{"type":"array","items":{"type":"number"},"minItems":2,"maxItems":2},"diameter":{"type":"number","exclusiveMinimum":0},"face_setback":{"type":"number","minimum":0}},"required":["id","face_center","diameter"],"additionalProperties":false}}},"required":["face_normal_axis","face_side","source_type","evidence","holes"],"additionalProperties":false}},{"name":"cad_update_feature","description":"增量修改指定特征。修改数值参数时同时传入对应 source_updates；若来源为 user，在对应source_updates项内附可信用户turn_id（工具后端会在CADPlan校验前消费该临时字段）。未改参数来源默认保留；仅来源纠正必须在 provenance_corrections 中指明参数、可信turn_id及逐字证据。返修时需定位具体差异、feature_id 与 finding_ids；相似度分数不作为修改门槛。","inputSchema":{"type":"object","properties":{"feature_id":{"type":"string"},"parameter_updates":{"type":"object"},"source_updates":{"type":"object"},"provenance_corrections":{"type":"array","items":{"type":"object","properties":{"parameter":{"type":"string"},"turn_id":{"type":"string"},"evidence":{"type":"string"}},"required":["parameter","turn_id","evidence"],"additionalProperties":false}},"remove_parameters":{"type":"array","items":{"type":"string"}},"fields":{"type":"object"},"finding_ids":{"type":"array","items":{"type":"string"}}},"required":["feature_id"],"additionalProperties":false}},{"name":"cad_remove_feature","description":"从当前 CADPlan 删除一个特征。返修时需定位具体差异、feature_id 与 finding_ids。","inputSchema":{"type":"object","properties":{"feature_id":{"type":"string"},"finding_ids":{"type":"array","items":{"type":"string"}}},"required":["feature_id"],"additionalProperties":false}},{"name":"cad_get_feature_spec","description":"按需读取一个 CADPlan 特征类型的必需参数、可选参数和 CATIA 映射状态。","inputSchema":{"type":"object","properties":{"kind":{"type":"string","enum":["bevel_gear","box","camshaft","conrod","crankshaft","cross_joint","cylinder","gear","helical_gear","helix","hole","impeller","loft","pattern","piston","pocket","prism","revolve","rotor","scroll_disc","shell","socket_head_bolt","sphere","spoked_wheel","timing_pulley","vbelt_pulley","worm"]}},"required":["kind"],"additionalProperties":false}},{"name":"cad_validate_model","description":"校验 CADPlan、CATIA 转换支持状态和局部几何检查；不启动 CATIA。","inputSchema":{"type":"object","properties":{},"additionalProperties":false}},{"name":"cad_preview_model","description":"生成当前模型指定方向的本地渲染视图；当前单视图PNG会作为图片块随工具结果返回给主建模Agent，供其直接查看，UI也显示本地进度预览。","inputSchema":{"type":"object","properties":{"view":{"type":"string","enum":["iso","front","right","top","back","left","bottom","three","extended"]}},"additionalProperties":false}},{"name":"cad_get_reference_image","description":"读取会话内原图的稳定引用和文件完整性元数据。本工具会将对应的用户原图或ROI图像内容作为图片块随工具结果返回给主建模Agent，供其直接查看；需要独立结构化观察时另调用 cad_inspect_references。","inputSchema":{"type":"object","properties":{"reference_id":{"type":"string"},"reference_image_index":{"type":"integer","minimum":1},"roi_norm":{"type":"array","items":{"type":"number"},"minItems":4,"maxItems":4},"evidence_id":{"type":"string"}},"additionalProperties":false}},{"name":"cad_inspect_references","description":"按reference_id/source_sha256及ROI独立观察原图。普通调用复用当前观察缓存；只有发现观察与其它证据冲突时传refresh_reason=conflict，或明确要求重新观察时传refresh_reason=reobserve，宿主才会生成新观察版本并保留旧版本。结果是可修订模型观察，不是已核实几何。读图失败时保持unknown。","inputSchema":{"type":"object","properties":{"reference_id":{"type":"string"},"reference_image_index":{"type":"integer","minimum":1},"roi_norm":{"type":"array","items":{"type":"number"},"minItems":4,"maxItems":4},"refresh_reason":{"type":"string","enum":["conflict","reobserve"],"description":"可选。conflict表示当前观察与新证据矛盾；reobserve表示明确重新读取同一SHA/ROI。提供时绕过缓存并创建新observation版本。"}},"additionalProperties":false}},{"name":"cad_review_reference_similarity","description":"用一个不共享本轮建模对话上下文的多模态审查请求，只将指定的一张原始参考图与同视角的当前预览发给视觉模型，返回逐项证据、可定位差异、受影响特征和辅助评分。只有一张原图时自动选择；多张图时从 cad_get_state 返回的 index/reference_id 选择。按 review_scope 评价当前已完成的施工范围；分数不设硬阈值，无法定位的差异先重新看原图，不要猜改。","inputSchema":{"type":"object","properties":{"view":{"type":"string","enum":["iso","front","right","top","back","left","bottom","three","extended"]},"preview_generation":{"type":"integer","minimum":1},"reference_image_index":{"type":"integer","minimum":1,"description":"拼版中与 view 对应的原始参考图编号，从 1 开始。"},"reference_id":{"type":"string"},"review_scope":{"type":"string","description":"当前已完成的施工里程碑及 feature_id；未施工结构不能记为缺失。"}},"required":["view","preview_generation"],"additionalProperties":false}},{"name":"cad_record_visual_review","description":"仅记录主Agent自述的诊断备注，不是独立多模态复核，不计入原图覆盖或验收。真正的原图与当前模型比较必须调用 cad_review_reference_similarity。差异尽量给出稳定 evidence_id、原图事实、当前模型事实、严重度和受影响 feature_id。","inputSchema":{"type":"object","properties":{"view":{"type":"string","enum":["iso","front","right","top","back","left","bottom","three","extended"]},"preview_generation":{"type":"integer","minimum":1},"visible_evidence":{"type":"string"},"issues":{"type":"array","items":{"type":"object","properties":{"status":{"type":"string","enum":["match","mismatch","partial","unknown"]},"severity":{"type":"string","enum":["high","medium","low"]},"item":{"type":"string"},"evidence_id":{"type":"string"},"reference_evidence":{"type":"string"},"model_evidence":{"type":"string"},"affected_feature_ids":{"type":"array","items":{"type":"string"}},"suggested_change":{"type":"string"}},"required":["status","item","reference_evidence","model_evidence"],"additionalProperties":false}},"unresolved":{"type":"array","items":{"type":"string"}}},"required":["view","preview_generation","visible_evidence","issues","unresolved"],"additionalProperties":false}},{"name":"cad_finish_model","description":"保存当前 CADPlan、STL、预览和几何报告。要求 CADPlan 可校验且当前版本有预览。多模态分数是参考信息；缺少视觉复核、几何项目待检查或仍有明确差异时，以未验收候选状态保存，不阻止输出。","inputSchema":{"type":"object","properties":{"summary":{"type":"string"},"unresolved_acknowledgement":{"type":"array","items":{"type":"string"}}},"required":["summary"],"additionalProperties":false}},{"name":"cad_restore_partial_candidate","description":"恢复最近一次独立保存的未验收有效前缀CADPlan以继续编辑；不会提升为best_candidate，恢复后必须重新预览。","inputSchema":{"type":"object","properties":{},"additionalProperties":false}},{"name":"cad_restore_best_candidate","description":"当返修无进展、几何检查退化或参数来回变化时，恢复此前通过本地几何核验的最佳候选。恢复后需重新预览。","inputSchema":{"type":"object","properties":{"reason":{"type":"string","minLength":1}},"required":["reason"],"additionalProperties":false}},{"name":"cad_set_construction_milestone","description":"记录当前施工里程碑与已完成 feature_id。让视觉复核区分已建范围和尚未施工范围；按需调用，不要求每个特征都更新。","inputSchema":{"type":"object","properties":{"milestone":{"type":"string","minLength":1},"completed_feature_ids":{"type":"array","items":{"type":"string"}},"expected_feature_ids":{"type":"array","items":{"type":"string"}},"notes":{"type":"string"}},"required":["milestone"],"additionalProperties":false}},{"name":"web_search","description":"搜索外部标准、材料或工程事实。联网设置关闭时会返回明确错误。","inputSchema":{"type":"object","properties":{"query":{"type":"string","minLength":2}},"required":["query"],"additionalProperties":false}},{"name":"web_open_result","description":"打开 web_search 返回的 result_id 对应页面正文。","inputSchema":{"type":"object","properties":{"result_id":{"type":"string"}},"required":["result_id"],"additionalProperties":false}},{"name":"cad_require_external_fact","description":"记录用户当前任务明确要求核实的网页/标准/材料事实。required facts与本地几何检查分开验收。","inputSchema":{"type":"object","properties":{"fact_id":{"type":"string","minLength":1},"claim":{"type":"string","minLength":1},"impact":{"type":"string","enum":["geometry","informational"]}},"required":["fact_id","claim","impact"],"additionalProperties":false}},{"name":"cad_attach_external_fact_evidence","description":"将已打开页面中的逐字原文证据绑定到先前登记的required external fact；仅网页正文包含的精确摘录才算已提供来源证据。","inputSchema":{"type":"object","properties":{"fact_id":{"type":"string","minLength":1},"result_id":{"type":"string","minLength":1},"quote":{"type":"string","minLength":12}},"required":["fact_id","result_id","quote"],"additionalProperties":false}}]"###;

/// Return the model-facing schemas for the native Cordis CADPlan tools.
pub fn native_tool_schemas() -> Result<Vec<Value>, String> {
    serde_json::from_str(SCHEMAS_JSON)
        .map_err(|error| format!("内置 Cordis 工具 schema 无效：{error}"))
}

#[cfg(test)]
mod tests {
    use super::native_tool_schemas;
    use serde_json::{json, Value};
    use std::collections::HashSet;

    #[test]
    fn exposes_all_tools_with_unique_names_and_valid_schema_shapes() {
        let schemas = native_tool_schemas().expect("embedded schemas must parse");
        assert_eq!(schemas.len(), 25);

        let mut names = HashSet::new();
        for schema in &schemas {
            assert!(schema.is_object(), "each tool schema must be an object");
            let name = schema
                .get("name")
                .and_then(|v| v.as_str())
                .expect("each tool must have a string name");
            assert!(!name.trim().is_empty(), "tool name must not be empty");
            assert!(names.insert(name), "duplicate tool name: {name}");
            assert!(
                schema
                    .get("description")
                    .and_then(|v| v.as_str())
                    .is_some_and(|v| !v.trim().is_empty()),
                "{name} needs a description"
            );
            let input = schema.get("inputSchema").expect("inputSchema is required");
            assert_eq!(
                input.get("type").and_then(|v| v.as_str()),
                Some("object"),
                "{name} inputSchema must be an object schema"
            );
            assert!(
                input.get("properties").is_some_and(Value::is_object),
                "{name} inputSchema must have object properties"
            );
            if let Some(required) = input.get("required") {
                let required = required.as_array().expect("required must be an array");
                let properties = input.get("properties").and_then(Value::as_object).unwrap();
                for field in required {
                    let field = field.as_str().expect("required entries must be strings");
                    assert!(
                        properties.contains_key(field),
                        "{name} requires undeclared property {field}"
                    );
                }
            }
        }
        assert_eq!(names.len(), schemas.len());
    }

    #[test]
    fn visual_tool_descriptions_match_image_blocks_returned_to_the_agent() {
        let schemas = native_tool_schemas().expect("embedded schemas must parse");
        let description = |name: &str| {
            schemas
                .iter()
                .find(|schema| schema["name"] == name)
                .and_then(|schema| schema["description"].as_str())
                .expect("visual tool description must exist")
        };

        let reference = description("cad_get_reference_image");
        assert!(reference.contains("用户原图"));
        assert!(reference.contains("图片块"));
        assert!(!reference.contains("不传回图片bytes"));

        let preview = description("cad_preview_model");
        assert!(preview.contains("当前单视图PNG"));
        assert!(preview.contains("图片块"));
        assert!(!preview.contains("不向主对话传图片bytes"));
    }

    #[test]
    fn observation_refresh_contract_is_explicit_and_cache_default_is_preserved() {
        let schemas = native_tool_schemas().expect("embedded schemas must parse");
        let inspect = schemas
            .iter()
            .find(|schema| schema["name"] == "cad_inspect_references")
            .unwrap();
        assert!(inspect["description"]
            .as_str()
            .unwrap()
            .contains("普通调用复用当前观察缓存"));
        assert_eq!(
            inspect["inputSchema"]["properties"]["refresh_reason"]["enum"],
            json!(["conflict", "reobserve"])
        );
    }
}
