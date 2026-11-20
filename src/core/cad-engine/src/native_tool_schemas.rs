//! Native Cordis tool schemas, mirrored from the legacy Python backend.
//!
//! Source of truth during extraction: `ai_modeler.agent.dsh_tool_backend.schemas()`
//! in `src/legacy/ai_modeler/agent/dsh_tool_backend.py`, which returns
//! `dsh_tool_domain.TOOLS`. Keep descriptions, inputSchema, and required fields
//! aligned when the tool contract changes.

use serde_json::Value;

const SCHEMAS_JSON: &str = r###"[{"name":"cad_get_state","description":"读取当前 CADPlan 特征树、尺寸、校验结果和未解决复核项。","inputSchema":{"type":"object","properties":{},"additionalProperties":false}},{"name":"cad_search_knowledge","description":"在本地工件知识库搜索精确型号或相似描述。精确命中会返回类别、型号、实测包围盒和可用的真实几何摘要；相似结果仅作上下文参考，不可直接复制尺寸。库外型号应明确返回未命中。可选返回带标签的真实照片/模型渲染样例图板供视觉比较。","inputSchema":{"type":"object","properties":{"query":{"type":"string","description":"工件描述或型号；纯图片查询可传空字符串。"},"limit":{"type":"integer","minimum":1,"maximum":10},"include_visual_examples":{"type":"boolean"}},"additionalProperties":false}},{"name":"cad_search_recipes","description":"搜索本地常用建模配方库，返回螺纹、孔、沉孔和标准件的推荐特征顺序、适用条件与当前能力边界。","inputSchema":{"type":"object","properties":{"query":{"type":"string","description":"操作或零件名，例如外螺纹、盲孔、内六角螺钉。"},"limit":{"type":"integer","minimum":1,"maximum":20}},"required":["query"],"additionalProperties":false}},{"name":"cad_apply_recipe","description":"一次调用展开并校验本地确定性建模配方，避免让模型逐项手拼标准件。目前可直接生成标准表覆盖的 ISO 4762/GB/T 70.1 内六角圆柱头螺钉；例如 recipe_id=fastener.socket_head_cap_screw,size=M5x8。生成后应立即预览。本地网格会按标准螺距生成可见的近似螺旋牙型；当前 CATIA 导出通道仍输出光杆，必须在结果中区分本地网格与 CATPart 的螺纹状态。","inputSchema":{"type":"object","properties":{"recipe_id":{"type":"string","enum":["fastener.socket_head_cap_screw"]},"size":{"type":"string","description":"标准型号，例如 M5x8 或 M10×30。"},"name":{"type":"string"},"source_prompt":{"type":"string"},"replace":{"type":"boolean"}},"required":["recipe_id","size"],"additionalProperties":false}},{"name":"cad_create_model","description":"新建空会话模型。已有CADPlan时，单参数/局部返修必须用增量工具；不得用本工具绕过provenance、失败守卫或未解决finding。仅当当前可信用户turn明确要求新工件或从头整体重建时才可replace=true。坐标固定为 X=左右、Y=前后厚度、Z=上下；box.length/width/height分别对应X/Y/Z。first_feature中每个数值参数（包括center）都必须有同名sources项；即使center=[0,0,0]也要注明来源。 每个 kind 的本地精确/近似/不支持状态和变体参数边界以 cad_get_feature_spec.native_execution 为准；写入前必须读取并遵循，不以通用 schema 可表达推断后端支持。","inputSchema":{"type":"object","properties":{"name":{"type":"string"},"first_feature":{"type":"object","description":"One CADPlan feature: id, kind, operation(add/cut), name, note, parameters, sources, relations. Every numeric parameter, including center, requires a same-named source entry (for example parameters.center requires sources.center, even when center=[0,0,0]). Coordinate contract: X left-right, Y front-back, Z vertical; box length/width/height map to X/Y/Z; hole point is global XYZ and hole axis is the through direction. A changed source of type user must include the trusted user turn_id; the transport adapter consumes that field and does not store it in CADPlan.","properties":{"id":{"type":"string","minLength":1},"kind":{"type":"string","enum":["bevel_gear","box","camshaft","conrod","crankshaft","cross_joint","cylinder","gear","helical_gear","helix","hex_bolt","hole","impeller","loft","pattern","piston","pocket","prism","revolve","rotor","scroll_disc","shell","socket_head_bolt","sphere","spoked_wheel","timing_pulley","vbelt_pulley","worm"]},"operation":{"type":"string","enum":["add","cut"]},"name":{"type":"string"},"note":{"type":"string"},"parameters":{"type":"object","description":"Geometry parameters. box: length=X, width=Y, height=Z, center=[X,Y,Z]. hole pos=point: point=[X,Y,Z], axis is the bore direction, through=true for a through-hole. cylinder/cone: base defaults true, center is the base-center point and height extends along +axis; use base=false when center means the geometric midpoint. prism: center is the profile base-plane origin; height extends along +axis. revolve about Z: profile is a closed list of [radius, axial-Z] points; center is the global axis origin. For a turned main body, create the complete profile as the first feature. sphere: center is the geometric center.","additionalProperties":true},"sources":{"type":"object","description":"Sources apply only to numeric keys in parameters: every numeric parameter, including center, requires a same-named entry. Never add sources for kind, name, operation, note, relations, or other metadata. Each value must be a strict provenance object with type in user/image_estimate/derived/existing_model and optional evidence/note/turn_id; image_estimate requires evidence, existing_model requires note, and turn_id is allowed only for user values. The Host binds the trusted user turn_id; any supplied turn_id must match the current input and remains temporary metadata. The Cordis bridge validates this shape before dispatch.","additionalProperties":true},"relations":{"type":"array","description":"Only relations to features already defined in the CADPlan are valid.","items":{"type":"object","properties":{"type":{"type":"string","enum":["depends_on","pattern_source"]},"feature_id":{"type":"string","minLength":1}},"required":["type","feature_id"],"additionalProperties":false}}},"required":["id","kind","operation","parameters","sources"],"additionalProperties":false},"source_prompt":{"type":"string"},"units":{"type":"object","properties":{"length":{"type":"string","enum":["mm","cm","m","in"]},"angle":{"type":"string","enum":["deg","rad"]}},"required":["length","angle"],"additionalProperties":false},"replace":{"type":"boolean"}},"required":["name","first_feature"],"additionalProperties":false}},{"name":"cad_add_feature","description":"向当前特征树增量添加一个 CADPlan 特征；每个数值参数（包括center）都必须有同名sources项。预览和视觉复核由 Agent 按施工里程碑自行选择，不作为逐特征门禁。坐标固定为 X=左右、Y=前后厚度、Z=上下。 每个 kind 的本地精确/近似/不支持状态和变体参数边界以 cad_get_feature_spec.native_execution 为准；写入前必须读取并遵循，不以通用 schema 可表达推断后端支持。","inputSchema":{"type":"object","properties":{"feature":{"type":"object","description":"One CADPlan feature: id, kind, operation(add/cut), name, note, parameters, sources, relations. Every numeric parameter, including center, requires a same-named source entry (for example parameters.center requires sources.center, even when center=[0,0,0]). Coordinate contract: X left-right, Y front-back, Z vertical; box length/width/height map to X/Y/Z; hole point is global XYZ and hole axis is the through direction. A changed source of type user must include the trusted user turn_id; the transport adapter consumes that field and does not store it in CADPlan.","properties":{"id":{"type":"string","minLength":1},"kind":{"type":"string","enum":["bevel_gear","box","camshaft","conrod","crankshaft","cross_joint","cylinder","gear","helical_gear","helix","hex_bolt","hole","impeller","loft","pattern","piston","pocket","prism","revolve","rotor","scroll_disc","shell","socket_head_bolt","sphere","spoked_wheel","timing_pulley","vbelt_pulley","worm"]},"operation":{"type":"string","enum":["add","cut"]},"name":{"type":"string"},"note":{"type":"string"},"parameters":{"type":"object","description":"Geometry parameters. box: length=X, width=Y, height=Z, center=[X,Y,Z]. hole pos=point: point=[X,Y,Z], axis is the bore direction, through=true for a through-hole. cylinder/cone: base defaults true, center is the base-center point and height extends along +axis; use base=false when center means the geometric midpoint. prism: center is the profile base-plane origin; height extends along +axis. revolve about Z: profile is a closed list of [radius, axial-Z] points; center is the global axis origin. For a turned main body, create the complete profile as the first feature. sphere: center is the geometric center.","additionalProperties":true},"sources":{"type":"object","description":"Sources apply only to numeric keys in parameters: every numeric parameter, including center, requires a same-named entry. Never add sources for kind, name, operation, note, relations, or other metadata. Each value must be a strict provenance object with type in user/image_estimate/derived/existing_model and optional evidence/note/turn_id; image_estimate requires evidence, existing_model requires note, and turn_id is allowed only for user values. The Host binds the trusted user turn_id; any supplied turn_id must match the current input and remains temporary metadata. The Cordis bridge validates this shape before dispatch.","additionalProperties":true},"relations":{"type":"array","description":"Only relations to features already defined in the CADPlan are valid.","items":{"type":"object","properties":{"type":{"type":"string","enum":["depends_on","pattern_source"]},"feature_id":{"type":"string","minLength":1}},"required":["type","feature_id"],"additionalProperties":false}}},"required":["id","kind","operation","parameters","sources"],"additionalProperties":false},"finding_ids":{"type":"array","items":{"type":"string"}}},"required":["feature"],"additionalProperties":false}},{"name":"cad_add_face_recess","description":"在当前实体指定的平面正面切出矩形凹入。width/height 沿该面的两个面内坐标轴，depth 沿 face_normal_axis；工具自动从对应外表面向实体内部放置切除体。预览和视觉复核由 Agent 按施工里程碑自行选择，不作为逐特征前置门禁。source_type=user 时需提供可信 turn_id，且宽、高、深数值须分别匹配用户原话。CADPlan 轴：X 左右、Y 前后、Z 上下。","inputSchema":{"type":"object","properties":{"feature_id":{"type":"string","minLength":1},"name":{"type":"string"},"face_normal_axis":{"type":"string","enum":["X","Y","Z"]},"face_side":{"type":"string","enum":["positive","negative"]},"face_center":{"type":"array","items":{"type":"number"},"minItems":2,"maxItems":2,"description":"在剩余两轴(按轴字母顺序)上的凹槽中心坐标；Y 正面用 [X,Z]。"},"width":{"type":"number","exclusiveMinimum":0,"description":"凹槽沿第一个面内轴的尺寸；按实际接触的工件边缘取值，图示开口到达边缘时延伸至边缘，不留遮挡桥。"},"height":{"type":"number","exclusiveMinimum":0,"description":"凹槽沿第二个面内轴的尺寸；图示开口到达工件顶部或底部时延伸至该边缘，不留遮挡桥。"},"depth":{"type":"number","exclusiveMinimum":0,"description":"沿face_normal_axis进入实体的深度。前面凹入若图中可见后壁，按图估深但保留清晰可见的材料后壁；禁止近乎全厚薄皮或通槽。"},"source_type":{"type":"string","enum":["image_estimate","user","derived"]},"turn_id":{"type":"string","description":"source_type=user 时必填，对应当前可信用户turn。"},"evidence":{"type":"string","minLength":1},"finding_ids":{"type":"array","items":{"type":"string"}}},"required":["feature_id","face_normal_axis","face_side","face_center","width","height","depth","source_type","evidence"],"additionalProperties":false}},{"name":"cad_add_hole_array","description":"一次在同一平行支承面添加一组点位孔；多个孔组或不同支承面须分开调用，每次仅表达一面。所有孔共用 face_normal_axis 轴向并设为贯穿；可用 face_setback 表示孔所在的局部面比外侧面后退了多少。Y 正面的 face_center 坐标为 [X,Z]，仅放在各 holes[] 项内。完成前按原图核对全部可见孔组和各组成员数。预览和视觉复核由 Agent 按施工里程碑自行选择，不作为逐特征前置门禁。source_type=user 时需提供可信 turn_id，孔径数值须由用户原话中对应孔径支持。","inputSchema":{"type":"object","properties":{"face_normal_axis":{"type":"string","enum":["X","Y","Z"]},"face_side":{"type":"string","enum":["positive","negative"]},"source_type":{"type":"string","enum":["image_estimate","user","derived"]},"turn_id":{"type":"string","description":"source_type=user 时必填，对应当前可信用户turn。"},"evidence":{"type":"string","minLength":1},"finding_ids":{"type":"array","items":{"type":"string"}},"holes":{"type":"array","minItems":1,"maxItems":4096,"items":{"type":"object","properties":{"id":{"type":"string","minLength":1},"name":{"type":"string"},"face_center":{"type":"array","items":{"type":"number"},"minItems":2,"maxItems":2,"description":"此孔在支承面内的中心；仅能放在holes[]此项，不能放在工具顶层。不同支承面分开调用。"},"diameter":{"type":"number","exclusiveMinimum":0,"description":"每个孔独立填写直径；同组图像可见大小不同时，不得给整组复制同一个直径。"},"face_setback":{"type":"number","minimum":0}},"required":["id","face_center","diameter"],"additionalProperties":false}}},"required":["face_normal_axis","face_side","source_type","evidence","holes"],"additionalProperties":false}},{"name":"cad_update_feature","description":"返修现有特征优先使用本工具，仅修改有证据支持的参数；用 parameter_updates 更新数值并用 source_updates 给每个改动数值提供同名来源，fields 仅改非几何元数据；不得使用 field_updates。若来源为 user，在对应source_updates项内附可信用户turn_id（工具后端会在CADPlan校验前消费该临时字段）。未改参数来源默认保留；仅来源纠正也用 source_updates，并在对应项内附可信 turn_id 及逐字证据。返修时需定位具体差异、feature_id 与当前revision上仍为open且影响该特征的finding_ids；不得用旧finding回退历史几何。相似度分数不作为修改门槛。","inputSchema":{"type":"object","properties":{"feature_id":{"type":"string","minLength":1},"parameter_updates":{"type":"object","description":"仅更新几何参数，键和值必须符合所选特征的 CADPlan schema；所有变更的数值参数必须在 source_updates 中提供同名来源。参数名与范围由 native executor 校验。","additionalProperties":true},"source_updates":{"type":"object","description":"仅对应parameter_updates中的数值键提供来源；user 来源由 Host 按当前 turn 绑定可信 turn_id。每个值必须是 provenance 对象，type 为 user/image_estimate/derived/existing_model；可带 evidence/note；turn_id 仅供 user 使用。image_estimate 必须有 evidence，existing_model 必须有 note。","additionalProperties":true},"remove_parameters":{"type":"array","items":{"type":"string"}},"fields":{"type":"object","description":"仅修改名称、备注等非几何元数据；不用于数值参数，也不存在 field_updates 参数。","properties":{"name":{"type":"string"},"note":{"type":"string"}},"additionalProperties":false},"finding_ids":{"type":"array","items":{"type":"string"}}},"required":["feature_id"],"additionalProperties":false}},{"name":"cad_remove_feature","description":"仅当特征本身确实不应存在时才从当前 CADPlan 删除。返修单个参数优先使用 cad_update_feature。会拒绝没有当前revision上仍为open、明确影响该特征的finding_ids支持的历史几何回退；不得通过删除再重建绕过该保护。","inputSchema":{"type":"object","properties":{"feature_id":{"type":"string","minLength":1},"finding_ids":{"type":"array","items":{"type":"string"}}},"required":["feature_id"],"additionalProperties":false}},{"name":"cad_get_feature_spec","description":"按需读取一个 CADPlan 特征类型的必需/可选参数、CATIA映射，以及本地Rust几何精确/近似/不支持状态、支持与拒绝的变体和参数边界。","inputSchema":{"type":"object","properties":{"kind":{"type":"string","enum":["bevel_gear","box","camshaft","conrod","crankshaft","cross_joint","cylinder","gear","helical_gear","helix","hex_bolt","hole","impeller","loft","pattern","piston","pocket","prism","revolve","rotor","scroll_disc","shell","socket_head_bolt","sphere","spoked_wheel","timing_pulley","vbelt_pulley","worm"]}},"required":["kind"],"additionalProperties":false}},{"name":"cad_validate_model","description":"校验 CADPlan、CATIA 转换支持状态和局部几何检查；不启动 CATIA。","inputSchema":{"type":"object","properties":{},"additionalProperties":false}},{"name":"cad_preview_model","description":"生成当前模型指定方向的本地渲染视图；仅支持iso/front/right/top/back/left/bottom；成功结果返回该预览的preview_generation。当前单视图PNG会作为图片块随工具结果返回给主建模Agent。直接对照原图时，多图会话传入已由Host登记的reference_id；单图会话省略时自动配对唯一原图。结果按顺序附带原图原始字节与当前单视图，校验原图SHA且不改写证据。UI也显示本地进度预览。几何修改后应先重设当前施工里程碑，再生成新预览；复核必须使用紧邻复核前本工具成功返回的preview_generation。","inputSchema":{"type":"object","properties":{"reference_id":{"type":"string","description":"多张原图时使用cad_get_state返回的reference_id选择；仅一张原图时可省略并由Host自动配对。"},"view":{"type":"string","enum":["iso","front","right","top","back","left","bottom"]}},"additionalProperties":false}},{"name":"cad_get_reference_image","description":"读取会话内原图的稳定引用和文件完整性元数据。本工具会将对应的用户原图或ROI图像内容作为图片块随工具结果返回给主建模Agent，供其直接查看；需要独立结构化观察时另调用 cad_inspect_references。","inputSchema":{"type":"object","properties":{"reference_id":{"type":"string"},"reference_image_index":{"type":"integer","minimum":1},"roi_norm":{"type":"array","items":{"type":"number","minimum":0,"maximum":1},"minItems":4,"maxItems":4,"description":"归一化[x,y,width,height]，不是[x1,y1,x2,y2]；各值在0..1，且x+width<=1、y+height<=1。省略或null表示全图。"},"evidence_id":{"type":"string"}},"additionalProperties":false}},{"name":"cad_inspect_references","description":"按reference_id/source_sha256及ROI独立观察原图。普通调用复用当前观察缓存；只有发现观察与其它证据冲突时传refresh_reason=conflict，或明确要求重新观察时传refresh_reason=reobserve，宿主才会生成新观察版本并保留旧版本。结果是可修订模型观察，不是已核实几何。读图失败时保持unknown。","inputSchema":{"type":"object","properties":{"reference_id":{"type":"string"},"reference_image_index":{"type":"integer","minimum":1},"roi_norm":{"type":"array","items":{"type":"number","minimum":0,"maximum":1},"minItems":4,"maxItems":4,"description":"归一化[x,y,width,height]，不是[x1,y1,x2,y2]；各值在0..1，且x+width<=1、y+height<=1。省略或null表示全图。"},"refresh_reason":{"type":"string","enum":["conflict","reobserve"],"description":"可选。conflict表示当前观察与新证据矛盾；reobserve表示明确重新读取同一SHA/ROI。提供时绕过缓存并创建新observation版本。"}},"additionalProperties":false}},{"name":"cad_review_reference_similarity","description":"用一个不共享本轮建模对话上下文的多模态审查请求，只将指定的一张原始参考图与同视角的当前预览发给视觉模型，返回逐项证据、可定位差异、受影响特征和辅助评分；视图仅支持iso/front/right/top/back/left/bottom。只有一张原图时自动选择；多张图时从 cad_get_state 返回的 index/reference_id 选择。几何每次修改后必须重设施工里程碑并生成新预览；preview_generation必须使用紧邻本次复核前成功cad_preview_model返回的当前值，不能复用旧generation。按 review_scope 评价当前已完成的施工范围；分数不设硬阈值，无法定位的差异先重新看原图，不要猜改。","inputSchema":{"type":"object","properties":{"view":{"type":"string","enum":["iso","front","right","top","back","left","bottom"]},"preview_generation":{"type":"integer","minimum":1},"reference_image_index":{"type":"integer","minimum":1,"description":"拼版中与 view 对应的原始参考图编号，从 1 开始。"},"reference_id":{"type":"string"},"review_scope":{"type":"string","description":"当前已完成的施工里程碑及 feature_id；未施工结构不能记为缺失。"}},"required":["view","preview_generation"],"additionalProperties":false}},{"name":"cad_record_visual_review","description":"仅记录主Agent自述的诊断备注，不是独立多模态复核，不计入原图覆盖或验收。真正的原图与当前模型比较必须调用 cad_review_reference_similarity。差异尽量给出稳定 evidence_id、原图事实、当前模型事实、严重度和受影响 feature_id。","inputSchema":{"type":"object","properties":{"view":{"type":"string","enum":["iso","front","right","top","back","left","bottom","three","extended"]},"preview_generation":{"type":"integer","minimum":1},"visible_evidence":{"type":"string"},"issues":{"type":"array","items":{"type":"object","properties":{"status":{"type":"string","enum":["match","mismatch","partial","unknown"]},"severity":{"type":"string","enum":["high","medium","low"]},"item":{"type":"string"},"evidence_id":{"type":"string"},"reference_evidence":{"type":"string"},"model_evidence":{"type":"string"},"affected_feature_ids":{"type":"array","items":{"type":"string"}},"suggested_change":{"type":"string"}},"required":["status","item","reference_evidence","model_evidence"],"additionalProperties":false}},"unresolved":{"type":"array","items":{"type":"string"}}},"required":["view","preview_generation","visible_evidence","issues","unresolved"],"additionalProperties":false}},{"name":"cad_finish_model","description":"保存当前 CADPlan、STL、预览和几何报告。调用前对照观察核对全部可见支承面孔组数及各组成员数；有可修finding先修改并复核，无法安全解决时才以unresolved_acknowledgement标明未解决项。要求 CADPlan 可校验且当前版本有预览；此工具可保存未验收候选。","inputSchema":{"type":"object","properties":{"summary":{"type":"string"},"unresolved_acknowledgement":{"type":"array","items":{"type":"string"}}},"required":["summary"],"additionalProperties":false}},{"name":"cad_restore_best_candidate","description":"当返修无进展、几何检查退化或参数来回变化时，恢复此前通过本地几何核验的最佳候选。恢复后需重新预览。","inputSchema":{"type":"object","properties":{"reason":{"type":"string","minLength":1}},"required":["reason"],"additionalProperties":false}},{"name":"cad_set_construction_milestone","description":"将当前施工范围登记到调用时的几何revision。任何几何写入都会使旧里程碑失效；几何修改后、复核前必须重新登记已完成/预期feature_id，然后生成新预览并使用其返回的当前preview_generation复核。让视觉复核区分已建范围和尚未施工范围；不要求每个特征都单独登记。","inputSchema":{"type":"object","properties":{"milestone":{"type":"string","minLength":1},"completed_feature_ids":{"type":"array","items":{"type":"string"}},"expected_feature_ids":{"type":"array","items":{"type":"string"}},"notes":{"type":"string"}},"required":["milestone","completed_feature_ids","expected_feature_ids"],"additionalProperties":false}},{"name":"web_search","description":"搜索外部标准、材料或工程事实。联网设置关闭时会返回明确错误。","inputSchema":{"type":"object","properties":{"query":{"type":"string","minLength":2}},"required":["query"],"additionalProperties":false}},{"name":"web_open_result","description":"打开 web_search 返回的 result_id 对应页面正文。","inputSchema":{"type":"object","properties":{"result_id":{"type":"string"}},"required":["result_id"],"additionalProperties":false}},{"name":"cad_require_external_fact","description":"记录用户当前任务明确要求核实的网页/标准/材料事实。required facts与本地几何检查分开验收。","inputSchema":{"type":"object","properties":{"fact_id":{"type":"string","minLength":1},"claim":{"type":"string","minLength":1},"impact":{"type":"string","enum":["geometry","informational"]}},"required":["fact_id","claim","impact"],"additionalProperties":false}},{"name":"cad_attach_external_fact_evidence","description":"将已打开页面中的逐字原文证据绑定到先前登记的required external fact；仅网页正文包含的精确摘录才算已提供来源证据。","inputSchema":{"type":"object","properties":{"fact_id":{"type":"string","minLength":1},"result_id":{"type":"string","minLength":1},"quote":{"type":"string","minLength":12}},"required":["fact_id","result_id","quote"],"additionalProperties":false}}]"###;

/// Return the model-facing schemas for the native Cordis CADPlan tools.
pub fn native_tool_schemas() -> Result<Vec<Value>, String> {
    let mut schemas: Vec<Value> = serde_json::from_str(SCHEMAS_JSON)
        .map_err(|error| format!("内置 Cordis 工具 schema 无效：{error}"))?;
    let selection_binding = serde_json::json!({
        "type":"object",
        "description":"原样复制 cad_list_shell_faces 当前 revision、target feature 和几何 fingerprint 返回的 selection_binding；不得自行构造或复用旧版本。",
        "properties":{
            "revision":{"type":"integer","minimum":0},
            "geometry_fingerprint":{"type":"string","pattern":"^[0-9a-f]{64}$"},
            "kernel_version":{"type":"string","minLength":1},
            "inventory_version":{"const":3},
            "target_feature_id":{"type":"string","minLength":1}
        },
        "required":["revision","geometry_fingerprint","kernel_version","inventory_version","target_feature_id"],
        "additionalProperties":false
    });
    for feature_path in [
        "/inputSchema/properties/first_feature",
        "/inputSchema/properties/feature",
    ] {
        let feature = schemas
            .iter_mut()
            .find(|schema| {
                schema["name"]
                    == if feature_path.ends_with("first_feature") {
                        "cad_create_model"
                    } else {
                        "cad_add_feature"
                    }
            })
            .and_then(|schema| schema.pointer_mut(feature_path))
            .ok_or_else(|| format!("缺少工具 feature schema：{feature_path}"))?;
        feature["properties"]["selection_binding"] = selection_binding.clone();
        feature["properties"]["relations"]["items"]["properties"]["type"]["enum"] =
            serde_json::json!(["depends_on", "pattern_source", "shell_target"]);
        feature["allOf"] = serde_json::json!([{
            "if":{"properties":{"kind":{"const":"shell"}},"required":["kind"]},
            "then":{
                "required":["selection_binding"],
                "properties":{
                    "operation":{"const":"cut"},
                    "parameters":{
                        "required":["thickness","offset_mode","removed_faces"],
                        "properties":{
                            "thickness":{"type":"number","exclusiveMinimum":0.0},
                            "offset_mode":{"type":"string","enum":["inward","outward"]},
                            "removed_faces":{"type":"array","minItems":1,"maxItems":256,"uniqueItems":true,
                                "items":{"type":"string","pattern":"^shell-face-v1:[0-9a-f]{64}$"}}
                        }
                    },
                    "relations":{
                        "type":"array",
                        "items":{"properties":{"type":{"enum":["depends_on","pattern_source","shell_target"]}}},
                        "contains":{"type":"object","properties":{"type":{"const":"shell_target"}},
                            "required":["type","feature_id"]},
                        "minContains":1,
                        "maxContains":1
                    }
                }}
        }]);
    }
    schemas.push(serde_json::json!({
        "name":"cad_list_shell_faces",
        "description":"读取当前 plan-head 所代表的唯一有效闭合实体面清单。返回唯一 shell_target feature id、revision/fingerprint/kernel 绑定及每个可选面的 opaque selector；只允许平面和具有完整 360° 覆盖的单片圆柱面。每次 shell 操作只能选择一个圆柱面，且不可将它与其它面混选；部分圆柱与其它曲面不可选。创建 shell 时必须把当前 target_feature_id 写成唯一 shell_target relation，原样复制 selection_binding 与 removed_faces selector；任何几何修改后都须重新枚举。",
        "inputSchema":{"type":"object","properties":{},"additionalProperties":false}
    }));
    Ok(schemas)
}

#[cfg(test)]
mod tests {
    use super::native_tool_schemas;
    use serde_json::{Value, json};
    use std::collections::HashSet;

    const SYSTEM_PROMPT: &str = include_str!("../../../legacy/prompts/dsh_agent/system.md");

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
    fn construction_milestone_schema_requires_explicit_scope_arrays() {
        let schemas = native_tool_schemas().expect("embedded schemas must parse");
        let milestone = schemas
            .iter()
            .find(|schema| schema["name"] == "cad_set_construction_milestone")
            .expect("construction milestone tool must exist");
        let required = milestone["inputSchema"]["required"]
            .as_array()
            .expect("milestone required fields must be an array");

        for field in ["completed_feature_ids", "expected_feature_ids"] {
            assert!(
                required.iter().any(|value| value == field),
                "{field} must be required"
            );
        }
    }

    #[test]
    fn native_mutation_schemas_match_executor_contracts() {
        let schemas = native_tool_schemas().expect("embedded schemas must parse");
        let tool = |name: &str| {
            schemas
                .iter()
                .find(|schema| schema["name"] == name)
                .unwrap_or_else(|| panic!("missing native tool {name}"))
        };

        let face_list = tool("cad_list_shell_faces");
        assert_eq!(face_list["inputSchema"]["properties"], json!({}));
        for (tool_name, field_name) in [
            ("cad_create_model", "first_feature"),
            ("cad_add_feature", "feature"),
        ] {
            let feature = &tool(tool_name)["inputSchema"]["properties"][field_name];
            assert!(feature["properties"].get("selection_binding").is_some());
            let relation_types = feature["properties"]["relations"]["items"]["properties"]["type"]
                ["enum"]
                .as_array()
                .unwrap();
            assert!(relation_types.iter().any(|kind| kind == "shell_target"));
            assert_eq!(
                feature["allOf"][0]["then"]["required"],
                json!(["selection_binding"])
            );
            assert_eq!(
                feature["allOf"][0]["then"]["properties"]["parameters"]["required"],
                json!(["thickness", "offset_mode", "removed_faces"])
            );
            assert_eq!(
                feature["allOf"][0]["then"]["properties"]["parameters"]["properties"]["offset_mode"]
                    ["enum"],
                json!(["inward", "outward"])
            );
        }

        let create = &tool("cad_create_model")["inputSchema"]["properties"];
        assert_eq!(
            create["units"]["properties"]["length"]["enum"],
            json!(["mm", "cm", "m", "in"])
        );
        assert_eq!(
            create["units"]["properties"]["angle"]["enum"],
            json!(["deg", "rad"])
        );
        assert_eq!(create["units"]["required"], json!(["length", "angle"]));
        assert_eq!(create["units"]["additionalProperties"], false);
        let first = &create["first_feature"];
        assert!(
            first["required"]
                .as_array()
                .unwrap()
                .contains(&json!("sources"))
        );
        assert_eq!(first["properties"]["sources"]["additionalProperties"], true);
        assert_eq!(
            first["properties"]["relations"]["items"]["required"],
            json!(["type", "feature_id"])
        );
        assert_eq!(
            first["properties"]["relations"]["items"]["additionalProperties"],
            false
        );

        let add = &tool("cad_add_feature")["inputSchema"]["properties"]["feature"];
        assert!(
            add["required"]
                .as_array()
                .unwrap()
                .contains(&json!("sources"))
        );
        assert_eq!(add["properties"]["sources"]["additionalProperties"], true);
        assert_eq!(
            add["properties"]["relations"]["items"]["additionalProperties"],
            false
        );

        let update = &tool("cad_update_feature")["inputSchema"]["properties"];
        assert_eq!(update["parameter_updates"]["type"], "object");
        assert_eq!(update["parameter_updates"]["additionalProperties"], true);
        assert_eq!(update["source_updates"]["type"], "object");
        assert_eq!(update["source_updates"]["additionalProperties"], true);
        assert_eq!(update["fields"]["properties"].as_object().unwrap().len(), 2);
        assert_eq!(update["fields"]["additionalProperties"], false);
        assert!(update.get("provenance_corrections").is_none());

        let remove = &tool("cad_remove_feature")["inputSchema"];
        assert_eq!(remove["properties"]["feature_id"]["minLength"], 1);
        assert!(
            !remove["required"]
                .as_array()
                .unwrap()
                .contains(&json!("finding_ids"))
        );
        assert!(
            !schemas
                .iter()
                .any(|schema| schema["name"] == "cad_restore_partial_candidate")
        );
        assert!(!crate::native_cad_tools::supports(
            "cad_restore_partial_candidate"
        ));
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
        assert!(preview.contains("单图会话省略时自动配对唯一原图"));
        assert!(!preview.contains("不向主对话传图片bytes"));
    }

    #[test]
    fn observation_refresh_contract_is_explicit_and_cache_default_is_preserved() {
        let schemas = native_tool_schemas().expect("embedded schemas must parse");
        let inspect = schemas
            .iter()
            .find(|schema| schema["name"] == "cad_inspect_references")
            .unwrap();
        assert!(
            inspect["description"]
                .as_str()
                .unwrap()
                .contains("普通调用复用当前观察缓存")
        );
        assert_eq!(
            inspect["inputSchema"]["properties"]["refresh_reason"]["enum"],
            json!(["conflict", "reobserve"])
        );
    }

    #[test]
    fn run05_failure_contracts_are_explicit_in_prompt_and_tool_schemas() {
        let schemas = native_tool_schemas().expect("embedded schemas must parse");
        let description = |name: &str| {
            schemas
                .iter()
                .find(|schema| schema["name"] == name)
                .and_then(|schema| schema["description"].as_str())
                .unwrap_or_else(|| panic!("missing description for {name}"))
        };

        let create = schemas
            .iter()
            .find(|schema| schema["name"] == "cad_create_model")
            .unwrap();
        let create_feature = &create["inputSchema"]["properties"]["first_feature"];
        assert!(create["description"].as_str().unwrap().contains("center"));
        assert!(create_feature["description"].as_str().unwrap().contains(
            "Every numeric parameter, including center, requires a same-named source entry"
        ));

        let add = schemas
            .iter()
            .find(|schema| schema["name"] == "cad_add_feature")
            .unwrap();
        assert!(
            add["inputSchema"]["properties"]["feature"]["description"]
                .as_str()
                .unwrap()
                .contains(
                    "Every numeric parameter, including center, requires a same-named source entry"
                )
        );
        assert!(description("cad_update_feature").contains("优先使用本工具"));
        assert!(description("cad_remove_feature").contains("不得通过删除再重建绕过该保护"));

        for name in ["cad_get_reference_image", "cad_inspect_references"] {
            let schema = schemas
                .iter()
                .find(|schema| schema["name"] == name)
                .unwrap();
            assert!(
                schema["inputSchema"]["properties"]["roi_norm"]["description"]
                    .as_str()
                    .unwrap()
                    .contains("归一化[x,y,width,height]")
            );
            assert!(
                schema["inputSchema"]["properties"]["roi_norm"]["description"]
                    .as_str()
                    .unwrap()
                    .contains("省略或null表示全图")
            );
        }
        assert!(
            description("cad_set_construction_milestone")
                .contains("任何几何写入都会使旧里程碑失效")
        );
        assert!(
            description("cad_review_reference_similarity")
                .contains("紧邻本次复核前成功cad_preview_model返回的当前值")
        );
        assert!(description("cad_preview_model").contains("返回该预览的preview_generation"));

        let hole_array = schemas
            .iter()
            .find(|schema| schema["name"] == "cad_add_hole_array")
            .unwrap();
        let diameter_description = hole_array["inputSchema"]["properties"]["holes"]["items"]
            ["properties"]["diameter"]["description"]
            .as_str()
            .unwrap();
        assert!(diameter_description.contains("每个孔独立填写直径"));
        assert!(diameter_description.contains("不得给整组复制同一个直径"));

        let recess = schemas
            .iter()
            .find(|schema| schema["name"] == "cad_add_face_recess")
            .unwrap();
        assert!(
            recess["inputSchema"]["properties"]["width"]["description"]
                .as_str()
                .unwrap()
                .contains("按实际接触的工件边缘取值")
        );
        assert!(
            recess["inputSchema"]["properties"]["height"]["description"]
                .as_str()
                .unwrap()
                .contains("顶部或底部时延伸至该边缘，不留遮挡桥")
        );

        for required_prompt_text in [
            "归一化 `[x,y,width,height]`（四项均在 0..1 内",
            "省略或传 `null` 表示全图",
            "每个数值参数都要有来源，包含 `center` 等坐标参数",
            "每次几何写入后，在复核前依次重新登记当前施工范围、生成新预览",
            "只使用该次预览工具结果返回的 `preview_generation`",
            "返修现有特征时优先调用 `cad_update_feature`",
            "不能复用旧 finding 绕过守卫",
            "`cad_add_hole_array.holes[]` 中每个孔独立填写直径",
            "图示开口到达顶部、底部或其他边缘时，必须延伸至该边缘，不能留下遮挡桥",
            "预览与独立复核仅支持单视图 `iso/front/right/top/back/left/bottom`",
            "针对每张目标原图及其需要核实的每个视角，分别调用 `cad_review_reference_similarity`",
            "每次审查后调用 `cad_get_state` 检查宿主记录的 findings",
            "只有宿主明确返回 `accepted` 才能声称验收通过",
            "有证据支持、可定位的 finding，可继续进行至多一次针对性修正",
            "没有可行动 finding、修正无进展或重复、关键拓扑仍无法由输入区分",
            "保留/保存 `candidate_not_verified` 并结束本轮",
            "不引入固定短轮数截断",
        ] {
            assert!(
                SYSTEM_PROMPT.contains(required_prompt_text),
                "missing prompt contract: {required_prompt_text}"
            );
        }
    }

    #[test]
    fn preview_and_review_view_schemas_do_not_exceed_native_preview_executor() {
        use std::sync::atomic::AtomicBool;

        let schemas = native_tool_schemas().expect("embedded schemas must parse");
        let supported = json!(["iso", "front", "right", "top", "back", "left", "bottom"]);
        let enum_values = |name: &str| {
            schemas
                .iter()
                .find(|schema| schema["name"] == name)
                .unwrap()["inputSchema"]["properties"]["view"]["enum"]
                .clone()
        };
        assert_eq!(enum_values("cad_preview_model"), supported);
        assert_eq!(enum_values("cad_review_reference_similarity"), supported);
        let preview_schema = schemas
            .iter()
            .find(|schema| schema["name"] == "cad_preview_model")
            .unwrap();
        assert_eq!(
            preview_schema["inputSchema"]["properties"]["reference_id"]["type"],
            "string"
        );
        assert!(preview_schema["inputSchema"]["required"].is_null());
        assert!(
            preview_schema["description"]
                .as_str()
                .unwrap()
                .contains("原始字节与当前单视图")
        );

        let description = |name: &str| {
            schemas
                .iter()
                .find(|schema| schema["name"] == name)
                .unwrap()["description"]
                .as_str()
                .unwrap()
        };
        for tool in ["cad_preview_model", "cad_review_reference_similarity"] {
            assert!(description(tool).contains("iso/front/right/top/back/left/bottom"));
        }

        let context = crate::native_cad_tools::Context {
            project_root: Default::default(),
            session_dir: Default::default(),
            runtime_manifest: None,
            user_turn_id: None,
        };
        for view in supported
            .as_array()
            .unwrap()
            .iter()
            .filter_map(Value::as_str)
        {
            let result = crate::native_cad_tools::run(
                &json!({}),
                "cad_preview_model",
                &json!({"view":view}),
                &context,
                &AtomicBool::new(false),
            );
            let error = match result {
                Ok(_) => panic!("empty state should stop preview before geometry build"),
                Err(error) => error,
            };
            assert!(
                !error.contains("原生模型预览当前只支持"),
                "schema view {view} was rejected by native executor: {error}"
            );
            assert!(crate::native_render::View::parse(view).is_ok());
        }
        for view in ["three", "extended"] {
            let result = crate::native_cad_tools::run(
                &json!({}),
                "cad_preview_model",
                &json!({"view":view}),
                &context,
                &AtomicBool::new(false),
            );
            assert!(matches!(result, Err(error) if error.contains("原生模型预览当前只支持")));
        }
    }

    #[test]
    fn run07_provenance_hole_group_update_recess_and_finish_contracts_are_explicit() {
        let schemas = native_tool_schemas().expect("embedded schemas must parse");
        let tool = |name: &str| {
            schemas
                .iter()
                .find(|schema| schema["name"] == name)
                .unwrap()
        };

        for (name, feature_key) in [
            ("cad_create_model", "first_feature"),
            ("cad_add_feature", "feature"),
        ] {
            let feature = &tool(name)["inputSchema"]["properties"][feature_key];
            let sources = feature["properties"]["sources"]["description"]
                .as_str()
                .unwrap();
            assert!(sources.contains("only to numeric keys in parameters"));
            assert!(sources.contains("Never add sources for kind, name, operation, note"));
            assert!(sources.contains("image_estimate requires evidence"));
            assert!(sources.contains("existing_model requires note"));
            assert!(sources.contains("Host binds the trusted user turn_id"));
        }

        let hole_schema = &tool("cad_add_hole_array")["inputSchema"];
        let hole_properties = hole_schema["properties"].as_object().unwrap();
        assert!(!hole_properties.contains_key("face_center"));
        assert_eq!(hole_schema["additionalProperties"], false);
        let hole_item = &hole_properties["holes"]["items"];
        assert!(hole_item["properties"].get("face_center").is_some());
        assert!(
            hole_item["properties"]["face_center"]["description"]
                .as_str()
                .unwrap()
                .contains("仅能放在holes[]此项")
        );
        assert!(
            tool("cad_add_hole_array")["description"]
                .as_str()
                .unwrap()
                .contains("完成前按原图核对全部可见孔组和各组成员数")
        );

        let update = tool("cad_update_feature");
        let update_properties = update["inputSchema"]["properties"].as_object().unwrap();
        assert!(!update_properties.contains_key("field_updates"));
        assert_eq!(update["inputSchema"]["additionalProperties"], false);
        assert!(
            update_properties["parameter_updates"]["description"]
                .as_str()
                .unwrap()
                .contains("仅更新几何参数")
        );
        assert!(
            update_properties["source_updates"]["description"]
                .as_str()
                .unwrap()
                .contains("parameter_updates中的数值键")
        );
        assert!(
            update_properties["fields"]["description"]
                .as_str()
                .unwrap()
                .contains("仅修改名称、备注等非几何元数据")
        );

        assert!(
            tool("cad_add_face_recess")["inputSchema"]["properties"]["depth"]["description"]
                .as_str()
                .unwrap()
                .contains("保留清晰可见的材料后壁")
        );
        let finish = tool("cad_finish_model")["description"].as_str().unwrap();
        assert!(finish.contains("核对全部可见支承面孔组数及各组成员数"));
        assert!(finish.contains("有可修finding先修改并复核"));
        assert!(finish.contains("无法安全解决时才以unresolved_acknowledgement"));

        for required_prompt_text in [
            "sources 只对应 `parameters` 中的数值键",
            "禁止给 `kind`、`name`、`operation`、`note` 等元数据添加来源",
            "`face_center` 仅放在各 `holes[]` 成员中",
            "finish 前按原图核对所有可见孔组及每组成员数与 CADPlan 一致",
            "`fields` 只改名称/备注等元数据，禁止使用 `field_updates`",
            "深度按图估算并保留清晰材料后壁",
            "仍有可修 finding 时先修改并复核，只有无法安全解决才将未解决项带入候选保存",
        ] {
            assert!(
                SYSTEM_PROMPT.contains(required_prompt_text),
                "missing prompt contract: {required_prompt_text}"
            );
        }
    }
}
