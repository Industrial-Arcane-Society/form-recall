//! Version-controlled CAD feature parameter contracts, migrated from CADPlan v1.
use serde_json::{json, Value};
const SPECS: &str = r###"{"box":{"kind":"box","required_parameters":["length","width","height"],"optional_parameters":["center","axis","base","corner","corner_mode"],"catia_mapping":"exact","catia_note":"矩形草图加凸台；当前 CATIA 映射仅支持 Z 轴拉伸。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"cylinder":{"kind":"cylinder","required_parameters":["radius","height"],"optional_parameters":["radius2","center","axis","base","segments"],"catia_mapping":"exact","catia_note":"center 默认表示轴向底面中心(base=true)，height 沿 axis 正向拉伸；base=false 时 center 表示几何中点。等半径时为圆草图加凸台；圆锥台会近似，非 Z 轴圆锥台暂不支持。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{"center":"base-center point when base=true (default); geometric midpoint when base=false","height":"extends along positive axis when base=true; straddles center when base=false","base_default":true}},"socket_head_bolt":{"kind":"socket_head_bolt","required_parameters":["shank_d","thread_pitch","length","head_d","head_h","hex_s","hex_t"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"本地网格按 thread_pitch 生成可见的近似 ISO 公制螺旋牙型；整段杆部带牙，未建牙端倒角/退刀槽。当前 CATIA 导出器仍以光杆凸台和六角凹槽表达，不会创建原生或装饰螺纹。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"sphere":{"kind":"sphere","required_parameters":["radius"],"optional_parameters":["center","segments","rings"],"catia_mapping":"approximate","catia_note":"CATIA 导出器以多层圆盘近似球体。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{"center":"geometric center"}},"prism":{"kind":"prism","required_parameters":["profile","height"],"optional_parameters":["center","axis"],"catia_mapping":"exact","catia_note":"闭合多边形草图加凸台；当前仅支持 Z 轴。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{"center":"origin of the profile's base plane","height":"extends along positive axis"}},"revolve":{"kind":"revolve","required_parameters":["profile","angle"],"optional_parameters":["center","axis","segments"],"catia_mapping":"approximate","catia_note":"本地 OCCT BREP 按闭合[半径,轴向坐标]轮廓精确回转，并将新增回转体与已有实体做布尔合并；CATIA 导出器仍用多层圆盘近似，非 CATIA 原生回转特征。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{"profile":"closed [radius, axial-Z] points when axis=Z","center":"global origin on the revolve axis","order":"can be added to an existing body when the OCCT backend can perform a valid union; legacy tessellated/approximate export paths may have stricter Boolean limits"}},"gear":{"kind":"gear","required_parameters":["teeth","module","height"],"optional_parameters":["center","axis","base"],"catia_mapping":"exact","catia_note":"齿廓转换为多边形草图并拉伸；当前仅支持 Z 轴。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"loft":{"kind":"loft","required_parameters":["root_chord","tip_chord","span","thickness"],"optional_parameters":["camber","camber_pos","twist","sweep","sections","points","rotate","translate"],"catia_mapping":"approximate","catia_note":"CATIA 中以根部截面拉伸近似多截面放样。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"hole":{"kind":"hole","required_parameters":["diameter","through","pos"],"optional_parameters":["point","x","y","count","pcd","phase","depth","margin","axis","segments","entry_point","direction"],"catia_mapping":"exact","catia_note":"圆形切除；through=true 时按当前零件包围盒生成贯穿深度。盲孔可用 entry_point 与 direction 显式指定入口点和沿全局轴的切入方向；未指定时沿用正轴端面默认语义。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"pocket":{"kind":"pocket","required_parameters":["length","width","depth"],"optional_parameters":["x","y","axis","side","corner","corner_mode"],"catia_mapping":"exact","catia_note":"矩形/圆角矩形凹槽；当前仅支持 Z 轴。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"pattern":{"kind":"pattern","required_parameters":["mode","count"],"optional_parameters":["step","axis","angle","full","center"],"catia_mapping":"exact","catia_note":"转换为显式复制的 CATIA 操作；relations 示例：[{\"type\":\"pattern_source\",\"feature_id\":\"紧邻前一特征的 id\"}]。operation 必须与源特征相同：阵列切孔用 cut，阵列加料用 add。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"helix":{"kind":"helix","required_parameters":["radius","wire_r","height","turns"],"optional_parameters":["center","axis","taper","seg_per_turn","wire_seg","taper_start"],"catia_mapping":"unsupported","catia_note":"弹簧/螺旋不能由当前草图、凸台和凹槽通道表达。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"spoked_wheel":{"kind":"spoked_wheel","required_parameters":["outer_r","rim_w","hub_r","height","spokes","spoke_w"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 仅生成轮缘与轮毂，辐条需要补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"vbelt_pulley":{"kind":"vbelt_pulley","required_parameters":["pitch_d","grooves","height","groove_type"],"optional_parameters":["outer_d","groove_depth","bore_d","center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 生成光轮盘体；V 形回转槽需要在 CATIA 中补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"timing_pulley":{"kind":"timing_pulley","required_parameters":["pitch_d","teeth","height","tooth_type"],"optional_parameters":["bore_d","center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 生成盘体，齿形需要在 CATIA 中补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"worm":{"kind":"worm","required_parameters":["radius","height","thread_r","wire_r","turns"],"optional_parameters":["center","center2","axis","height2","segments","seg_per_turn","wire_seg"],"catia_mapping":"approximate","catia_note":"CATIA 仅生成光轴，螺旋齿需要补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"helical_gear":{"kind":"helical_gear","required_parameters":["teeth","module","height"],"optional_parameters":["helix_deg","bore_d","center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 以直齿轮近似，省略螺旋角。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"bevel_gear":{"kind":"bevel_gear","required_parameters":["teeth","module","height","cone_deg"],"optional_parameters":["bore_d","center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 以直齿圆柱轮廓近似，省略锥面。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{"teeth":"CADPlan requires an integer >= 6; direct legacy geometry helper clamps counts below 6","module":"positive length in the plan length unit; normalized to mm","height":"positive thickness centered on global Z through center","cone_deg":"finite angle in the plan angle unit, clamped by the legacy recipe to 5..75 degrees","bore_d":"diameter; omitted or 0 selects legacy implicit bore max(0.5 mm, 0.28 times pitch radius); positive values are capped at a bore radius of 0.7 times pitch radius","center":"geometric midpoint in global XYZ; omitted means the origin","segments":"Optional integer >= 3 by CADPlan; legacy geometry uses max(4, min(requested, floor(teeth/2))), default 12"}},"crankshaft":{"kind":"crankshaft","required_parameters":["length","main_d","pin_d","stroke","throws"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 按 X 轴布置主轴颈、交替偏置连杆颈和哑铃形曲柄臂(主轴圆 + 连杆颈圆外公切凸包)，曲柄臂外形被简化。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"camshaft":{"kind":"camshaft","required_parameters":["length","journal_d","base_d","lift","lobes","lobe_deg"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 凸轮沿轴向等分布置，相位需核对。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"impeller":{"kind":"impeller","required_parameters":["hub_d","tip_d","height","blades","blade_t","twist_deg"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 仅生成轮毂和轮盘，扭转叶片需要补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"piston":{"kind":"piston","required_parameters":["diameter","height","pin_d","grooves"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 以圆柱体近似，销孔和活塞环槽需要补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"conrod":{"kind":"conrod","required_parameters":["length","big_d","small_d","big_bore","small_bore","shank_w","thick"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 以大头、小头和直杆身近似，杆身过渡被简化。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{},"native_execution":{"status":"implemented_approximate","geometry_support":"approximate","scope":"local_candidate_geometry","note":"Rust/OCCT builds analytic annular heads with an approximate external-tangent trapezoidal web. Web contact and I-section details are simplified; consult support_details. CATIA mapping remains approximate."}},"cross_joint":{"kind":"cross_joint","required_parameters":["span","journal_d"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 仅生成单轴，另一根横轴需要补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{"center":"global XYZ center of the hub","segments":"legacy facet tessellation setting; ignored by the native analytic-cylinder builder"}},"scroll_disc":{"kind":"scroll_disc","required_parameters":["outer_d","height","wall_t","turns"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 仅生成底盘，螺旋壁需要补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"rotor":{"kind":"rotor","required_parameters":["radius","n","height","bore_d"],"optional_parameters":["center"],"catia_mapping":"exact","catia_note":"转子轮廓、厚度与中心孔映射为可编辑草图和凹槽。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"shell":{"kind":"shell","required_parameters":["thickness"],"optional_parameters":[],"catia_mapping":"unsupported","catia_note":"当前 Document/CATIA 转换器没有抽壳实现。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}}}"###;

pub fn feature_spec(kind: &str) -> Result<Value, String> {
    let specs: Value = serde_json::from_str(SPECS).map_err(|error| error.to_string())?;
    let mut result = specs
        .get(kind)
        .cloned()
        .ok_or_else(|| format!("未知特征类型：{kind}"))?;
    if ["box", "prism", "pocket"].contains(&kind) {
        result["parameter_semantics"]["profile_basis_by_axis"] = serde_json::json!({
            "X":{"u":"Y","v":"Z","extrusion":"X"},
            "Y":{"u":"Z","v":"X","extrusion":"Y"},
            "Z":{"u":"X","v":"Y","extrusion":"Z"}
        });
        result["parameter_semantics"]["profile_dimensions"] = serde_json::json!(
            "length follows profile u, width follows profile v; axis=Y means length along Z and width along X. This differs from hole/pocket location x/y, which follow ascending global plane axes."
        );
    }
    if kind == "box" {
        result["parameter_semantics"]["base_default_with_center"] = serde_json::json!(false);
        result["parameter_semantics"]["base_default_without_center"] = serde_json::json!(true);
    }
    if ["hole", "pocket"].contains(&kind) {
        result["parameter_semantics"]["position_basis_by_axis"] = serde_json::json!({
            "X":["Y","Z"],"Y":["X","Z"],"Z":["X","Y"]
        });
    }
    if kind == "shell" {
        result["optional_parameters"] = json!(["offset_mode", "removed_faces"]);
        result["native_execution"] = json!({
            "status":"implemented_exact",
            "geometry_support":"exact",
            "scope":"local_candidate_geometry",
            "supported_variants":[
                "explicit planar face selection from the current face inventory",
                "one complete 360-degree cylindrical face from the current face inventory",
                "one current unique closed solid",
                "explicit inward or outward offset",
                "cylindrical shell thickness below the selected cylinder radius"
            ],
            "unsupported_variants":[
                "partial cylindrical faces, conical faces and other curved removed faces",
                "mixing a cylindrical face with any other removed face",
                "stale or ambiguous selectors",
                "offset failure or result other than one valid closed solid",
                "legacy thickness-only shell"
            ],
            "parameter_limits":{
                "thickness":">0 in CADPlan length units",
                "offset_mode":"required inward|outward; never inferred or flipped",
                "shell_target":"one relation to the enumerated current plan-head feature",
                "removed_faces":"1..=256 planar selectors; a shell with a cylindrical face requires exactly one selector",
                "selection_binding":"current revision, target id, fingerprint, kernel and inventory version"
            },
            "note":"Native OCCT shell is exact only for explicit planar faces or one complete 360-degree cylindrical face on the current unique closed solid. Partial cylinders, other curved faces, mixed curved/planar selections, stale or ambiguous selectors, and offset failures are rejected. Legacy thickness-only plans remain readable but are unsupported and never auto-migrated. CATIA export remains unsupported."
        });
    } else {
        result["native_execution"] = native_execution_contract(kind);
    }
    Ok(result)
}

/// Per-kind native geometry capability contract surfaced to `cad_get_feature_spec`.
/// Backend implementation membership is shared with preflight; variant limits
/// below describe the additional gates applied by preflight/build.
fn native_execution_contract(kind: &str) -> Value {
    let implemented = crate::native_plan_geometry::supports_native_kind(kind);
    let approximate = matches!(
        kind,
        "gear"
            | "helical_gear"
            | "bevel_gear"
            | "socket_head_bolt"
            | "conrod"
            | "cross_joint"
            | "rotor"
            | "crankshaft"
            | "timing_pulley"
            | "vbelt_pulley"
            | "scroll_disc"
            | "helix"
            | "worm"
            | "loft"
            | "impeller"
    );
    let (supported_variants, unsupported_variants, parameter_limits, note) = match kind {
        "rotor" => (
            json!(["global Z extrusion centered on center", "explicit bore_d=0 or enclosed circular bore"]),
            json!(["axis other than Z", "base convention", "bore intersecting discrete outer profile"]),
            json!({"radius":">0 explicit", "height":">0 explicit", "bore_d":">=0 explicit; radius smaller than actual polygon clearance", "n":"explicit integer 12..=4096"}),
            "Approximate legacy rounded-triangle radial profile (fixed flank=0.82, cosine exponent=6), polygonal OCCT prism and analytic central bore. Not an exact Reuleaux curve or production engine rotor.",
        ),
        "vbelt_pulley" => (
            json!(["standard Y/Z/A/B/C/D/E groove profile", "single global-Z analytic OCCT revolve", "explicit bore or legacy default bore", "width at or above legacy simple-profile minimum"]),
            json!(["outer_d inconsistent with pitch_d and standard groove", "groove_depth inconsistent with standard ha+hf", "oversized bore requiring legacy silent clamp", "large spoke-style pulley", "axis other than Z", "base=true", "standard=false legacy cylindrical-groove approximation"]),
            json!({"pitch_d":">0 explicit", "grooves":"integer 1..=64", "height":">= legacy profile minimum width", "groove_type":"required Y/Z/A/B/C/D/E; SPZ/SPA/SPB/SPC aliases", "bore_d":"0 selects legacy default; positive and < 0.92*pitch_d", "outer_d":"must equal pitch_d+2*ha", "groove_depth":"must equal standard ha+hf", "center":"finite global XYZ", "axis":"Z only", "segments":"accepted by legacy schema; no geometric effect on the analytic B-Rep"}),
            "Native analytic OCCT revolve reproduces the retained std_pulley fallback-table axial profile and strict-width CADPlan path. Groove spacing, web and hub dimensions follow Document formulas. Geometry is approximate to the engineering standard and does not include spokes; CATIA still exports a smooth disk and omits V grooves.",
        ),
        "box" => (json!(["axis=X|Y|Z", "corner=0"]), json!(["corner!=0"]), json!({"length":">0", "width":">0", "height":">0"}), "Analytic OCCT box; nonzero corner is rejected by native preflight."),
        "cylinder" => (json!(["cylinder", "frustum", "axis=X|Y|Z", "base=true|false"]), json!([]), json!({"radius":">0", "radius2":">0 when supplied", "height":">0"}), "Analytic cylinder/frustum; dimensions must be positive."),
        "sphere" => (json!(["analytic sphere"]), json!([]), json!({"radius":">0"}), "Analytic sphere; dimensions must be positive."),
        "hole" => (json!(["through", "blind depth with inferred positive-axis material entry", "axis=X|Y|Z"]), json!(["explicit entry_point", "explicit blind entry direction"]), json!({"diameter":">0", "blind depth":">0 and smaller than local material thickness"}), "Cylindrical cuts are sampled and final B-Rep bores measured; explicit entry_point is rejected."),
        "pocket" => (json!(["axis=X|Y|Z", "side=positive|negative", "corner=0"]), json!(["corner!=0"]), json!({"length":">0", "width":">0", "depth":">0 and smaller than stock thickness"}), "Rectangular blind pocket; nonzero corner and depth reaching through stock are rejected."),
        "prism" => (json!(["closed 2D profile", "axis=X|Y|Z"]), json!([]), json!({"height":">0", "profile":"valid closed polygon accepted by OCCT"}), "Closed-profile analytic prism; profile validity is checked by OCCT."),
        "revolve" => (json!(["closed radius-axial profile", "axis=X|Y|Z"]), json!([]), json!({"angle":"valid OCCT revolution angle", "profile":"valid closed radius-axial polygon"}), "OCCT revolution; CATIA mapping remains approximate."),
        "pattern" => (json!(["mode=linear", "mode=circular"]), json!(["other modes"]), json!({"count":"1..=10000", "transformed tools":"<=100000", "transformed hole records":"<=100000", "linear step":"finite XYZ", "circular angle":"finite; axis and center required/validated"}), "Transforms the immediately preceding related feature; per-feature instance and transformed-tool resource caps apply."),
        "gear" => (json!(["axis=X|Y|Z", "trapezoidal tooth profile"]), json!([]), json!({"teeth":"6..=10000", "module":">0", "height":">0", "base":"boolean when supplied"}), "Native trapezoidal tooth profile is approximate; build report carries support details."),
        "helical_gear" => (json!(["global-Z centered legacy straight trapezoidal profile", "bore_d=0 means no bore", "explicit bore_d>0 creates the legacy 40-sided through bore"]), json!(["true helical twist (helix_deg is ignored by the retained Document geometry)", "axis other than global Z"]), json!({"teeth":"6..=10000", "module":">0", "height":">0", "helix_deg":"finite; accepted but ignored by the retained legacy geometry path", "bore_d":"0=no bore; >0 explicit 40-sided through bore", "segments":"accepted by schema; has no geometric effect in the straight Document path"}), "The retained CADPlan/Document B-Rep path builds a centered straight trapezoidal gear and omits helix_deg; tooth form remains approximate. CATIA export is also approximate and does not preserve helix angle."),
        "bevel_gear" => (json!(["global-Z centered legacy tapered mesh recipe", "trapezoidal tooth profile", "40-segment implicit or explicit center bore"]), json!(["teeth below 6 or supplied segments below 3 (CADPlan validation)", "bore intersecting the actual discrete tooth-root clearance", "facet count above 100000", "triangles below the OCCT sewing tolerance"]), json!({"teeth":"CADPlan integer >=6; the legacy geometry helper also clamps direct lower inputs to 6", "module":">0", "height":">0", "cone_deg":"finite; legacy clamp 5..=75 degrees", "bore_d":">=0; 0 selects the legacy implicit bore; positive bore is capped at radius 0.7*pitch_radius and checked against actual polygon clearance", "segments":"CADPlan integer >=3 when supplied; legacy max(4, min(requested, floor(teeth/2))); default 12", "center":"finite global XYZ"}), "Rust sews the current Document/CADPlan trapezoidal, linearly tapered mesh into a faceted B-Rep. It is not an involute gear or analytic cone; CATIA mapping remains approximate and omits the taper."),
        "socket_head_bolt" => (json!(["native screw mesh geometry", "centered on global Z axis"]), json!([]), json!({"shank_d":">0", "thread_pitch":">0", "length":">0", "head_d":">0", "head_h":">0", "hex_s":">0", "hex_t":">0 and <=head_h", "segments":"integer clamped to 32..=48"}), "Local screw thread mesh is approximate; CATIA export remains a smooth shank."),
        "spoked_wheel" => (json!(["global-Z radial spokes"]), json!(["axis other than Z", "spokes<2 or >512"]), json!({"spokes":"2..=512", "outer_r":">0", "rim_w":">0 and outer_r-rim_w > hub_r", "hub_r":">0", "height":">0", "spoke_w":">0 and <2*hub_r"}), "Native analytic rim, hub and radial prism spokes; CATIA mapping remains approximate."),
        "piston" => (json!(["legacy closed profile plus X-axis pin bore"]), json!([]), json!({"diameter":">2 mm because legacy inner profile radius has a 1 mm floor", "height":">0", "pin_d":">=0; bore radius clamped to 0.75 body radius and 0.45 height", "grooves":"finite value >=0 and <=256; truncated toward zero"}), "Native analytic build; CATIA mapping remains approximate."),
        "camshaft" => (json!(["global X journal shaft", "48-point 120-degree harmonic cam profiles", "phased cams distributed evenly along shaft"]), json!(["axis other than X", "lobes outside 1..=512", "lobe width exceeding shaft length"]), json!({"length":">=2 mm", "journal_d":">=2 mm", "base_d":">=2 mm", "lift":">=0 mm", "lobes":"integer 1..=512", "lobe_deg":"finite; angle units honored", "center":"finite global XYZ"}), "Exact local analytic OCCT B-Rep for the legacy Document cam profile; CATIA mapping remains approximate."),
        "crankshaft" => (json!(["global X main shaft", "alternating eccentric pin journals", "two rectangular overlapping webs per throw", "segments is accepted but ignored by the analytic B-Rep"]), json!(["throws outside 1..=128"]), json!({"length":">0 mm", "main_d":">0 mm", "pin_d":">0 mm", "stroke":">0 mm", "throws":"integer 1..=128", "segments":"integer when supplied; ignored", "center":"finite global XYZ"}), "Native analytic cylinders and rectangular webs reproduce the legacy CADPlan B-Rep recipe; the feature remains approximate and the mesh-only Document cheek recipe is a different representation."),
        "conrod" => (json!(["annular heads with tangent web approximation"]), json!([]), json!({"length":">0 and long enough for tangent web", "big_d":">big_bore", "small_d":">small_bore", "big_bore":">0", "small_bore":">0", "shank_w":">0", "thick":">0"}), "Analytic annular heads with approximate external-tangent trapezoidal web; web contact and I-section details are simplified."),
        "cross_joint" => (json!(["hub plus four journals on global +/-X|+/-Y"]), json!([]), json!({"span":">0 and large enough for hub plus positive journal length", "journal_d":">0", "center":"finite global XYZ"}), "Native analytic construction; legacy faceted-volume equality is not claimed."),
        "scroll_disc" => (
            json!(["global-Z sampled Archimedean spiral wall", "analytic cylindrical backplate", "one fused closed solid", "origin or finite translated center"]),
            json!(["outer_d<4 mm", "height<1 mm", "wall_t<0.5 mm", "turns<0.5", "inner spiral radius <= wall_t/2", "neighboring turns self-intersect or touch", "segments outside 24..=4096", "fewer than 24 profile samples per turn", "disjoint fusion or invalid/non-closed B-Rep"]),
            json!({"outer_d":">=4 mm", "height":">=1 mm", "wall_t":">=0.5 mm and smaller than minimum normal turn spacing", "turns":">=0.5; profile requires at least 24 samples per turn", "segments":"integer 24..=4096; defaults to 120", "center":"finite global XYZ; all dimensions are normalized to mm"}),
            "Native OCCT prism of the legacy polygonal Archimedean spiral fused with the legacy analytic cylindrical backplate. The profile is an approximation of a continuous spiral. Bounds and volume are compared against the same legacy sampled B-Rep; tessellated surface-area estimate is allowed 0.6% relative error. CATIA export omits the spiral wall.",
        ),
        "helix" => (
            json!(["global X/Y/Z axis", "legacy radial-axial circular section layout", "constant-radius or tapered centerline", "one sewn valid closed faceted B-Rep"]),
            json!(["turns below 0.25", "adjacent-turn axial pitch <= wire diameter", "wire radius reaching sampled centerline curvature radius", "tapered end centerline radius <= 0", "seg_per_turn outside 6..=512", "wire_seg outside 3..=64", "triangle face count above 100000", "sewing that does not yield one valid closed solid"]),
            json!({"radius":">0 mm", "wire_r":">0 mm", "height":">0 mm; finite axial displacement", "turns":">=0.25", "axis":"X/Y/Z; default Z", "center":"finite global XYZ; default origin", "taper":"finite 0..=1 radius reduction fraction; final centerline radius must remain positive", "taper_start":"finite 0..=0.9 normalized path location", "seg_per_turn":"integer 6..=512; default 14", "wire_seg":"integer 3..=64; default 8", "triangle_faces":"2*wire_seg*(round(turns*seg_per_turn)+1) <= 100000", "self_intersection":"axial pitch > 2*wire_r and sampled minimum centerline curvature radius > wire_r"}),
            "Rust preserves the legacy sampled radial/axial circle-section mesh and sews it into a single OCCT facet-BREP. This is approximate tessellated geometry, not an analytic sweep. CATIA mapping remains unsupported.",
        ),
        "worm" => (
            json!(["global-Z coaxial polygonal shaft and centerline", "legacy sampled circular helical ridge", "radial tangency or positive engagement", "one valid closed fused solid"]),
            json!(["axis other than Z", "center and center2 with different X/Y axis", "radial gap between shaft and helix", "buried thread crown", "no axial overlap", "fuse result with more than one solid", "shaft segments outside 8..=512"]),
            json!({"radius":">0 mm shaft radius", "height":">0 mm shaft height", "thread_r":">0 mm helix centerline radius", "wire_r":">0 mm helix wire radius", "turns":">=0.25", "height2":">0 mm; defaults to height", "center":"finite global XYZ shaft base; default origin", "center2":"finite global XYZ helix start; defaults to center and must share its X/Y axis", "axis":"global Z only; omitted means Z", "segments":"integer 8..=512; default 48", "seg_per_turn":"integer 6..=512; default 14", "wire_seg":"integer 3..=64; default 8", "engagement":"thread_r - wire_r <= radius and thread_r + wire_r > radius; shaft/helix must overlap axially"}),
            "Rust builds the legacy faceted shaft prism and reuses native helix to build the sampled ridge, then performs an actual OCCT fuse. It rejects non-Z, non-coaxial, disjoint, buried, invalid, or multi-solid results. The helical ridge remains approximate facet-BREP and CATIA continues to export only the smooth shaft.",
        ),
        "loft" => (
            json!(["legacy NACA four-digit airfoil point layout", "root-to-tip chord interpolation", "spanwise sweep and twist", "optional global XYZ rigid rotation and translation", "one valid closed OCCT faceted B-Rep"]),
            json!(["thickness ratio outside 0.01..=0.4", "camber outside -0.2..=0.2", "camber_pos outside 0.05..=0.95", "sections outside 2..=64", "points outside 8..=128", "twist magnitude above 720 degrees", "OCCT sewing without one valid closed solid"]),
            json!({"root_chord":">0 mm", "tip_chord":">0 mm", "span":">0 mm", "thickness":"dimensionless NACA thickness ratio 0.01..=0.4", "camber":"dimensionless -0.2..=0.2; defaults to 0.05", "camber_pos":"0.05..=0.95; defaults to 0.4", "twist":"finite angle, magnitude <=720 degrees; angle units honored", "sweep":">=0 mm; length units normalized", "sections":"integer 2..=64; defaults to 8", "points":"integer 8..=128 per surface; defaults to 26", "rotate":"optional [X|Y|Z, angle]; angle units honored", "translate":"finite global XYZ; length units normalized", "triangle_faces":"2*sections*(2*points)+2*(2*points-2) <=100000"}),
            "Rust reproduces the retained legacy NACA section loops, chord/span/twist/sweep layout and triangulates side faces plus caps before OCCT sewing. The result is a valid closed faceted B-Rep approximation, not an analytic ThruSections loft; CATIA mapping remains the legacy root-section extrusion approximation.",
        ),
        "impeller" => (
            json!(["global-Z explicit impeller dimensions and blade count", "legacy analytic hub cone and revolved backplate", "legacy twisted rectangular blade sections", "one valid closed OCCT fused solid"]),
            json!(["hub_d below 2 mm or tip_d < 1.05*hub_d", "height below 1 mm", "blade_t <= 0", "blade count outside 3..=128", "segments outside 4..=128", "triangle face budget above 100000", "invalid/non-closed/disconnected fusion"]),
            json!({"hub_d":">=2 mm; legacy 1 mm hub-radius floor is rejected", "tip_d":">=1.05*hub_d; rejects legacy minimum tip-radius fallback", "height":">=1 mm; legacy height floor is rejected", "blades":"integer 3..=128", "blade_t":">0 mm; OCP uses min(height, blade_t) for blade axial span", "twist_deg":"finite with absolute value <=720 degrees", "center":"finite global XYZ; omitted means origin", "segments":"integer 4..=128; defaults to legacy 12", "triangle_faces":"blades*(8*segments+4) <=100000"}),
            "Rust matches the retained OCP hub cone, analytic backplate, twisted rectangular section placement and dimension scaling. Blade sides are triangulated and sewn into facet-BREP before OCCT fusion; this is approximate, not an analytic ThruSections surface. CATIA mapping remains hub/backplate only.",
        ),
        "timing_pulley" => (json!(["global-Z centered legacy trapezoidal tooth prisms", "MXL, XL, T2.5, T5, 3M profiles with radial tooth/blank overlap", "implicit legacy center bore or explicit bore_d", "teeth 6..=512"]), json!(["L, H, XH, XXH, T10, T20, 5M, 8M, 14M have no overlap between legacy tooth inner edge and blank root wall", "teeth above 512", "negative bore or bore reaching the effective tooth-root wall", "tooth-tip circumferential pitch smaller than tooth-tip width", "unknown tooth_type strings", "segments outside 3..=512"]), json!({"pitch_d":">0; legacy pitch radius floor=2 mm", "height":">0", "teeth":"integer 6..=512", "tooth_type":"MXL, XL, T2.5, T5, 3M (the other legacy profiles are rejected because they cannot guarantee a single solid)", "bore_d":">=0; 0 uses legacy implicit bore; explicit bore floored to 2 mm diameter", "segments":"3..=512 integer; retained for legacy baseline but ignored by analytic OCCT blank", "center":"finite global XYZ"}), "Supported legacy trapezoidal tooth prisms overlap the analytic annular revolve. Taller profiles whose legacy prism starts outside the blank root are rejected because a single closed solid cannot be guaranteed. The retained Document/CADPlan baseline is a faceted mesh; this analytic blank intentionally replaces its segmented boundary, so geometry remains approximate with a bounded blank-volume difference."),
        _ => (json!([]), json!(["all native geometry variants"]), json!({}), "No native Rust geometry builder is registered for this kind."),
    };
    json!({
        "status": if !implemented {"not_implemented"} else if approximate {"implemented_approximate"} else {"implemented_exact"},
        "geometry_support": if !implemented {"unsupported"} else if approximate {"approximate"} else {"exact"},
        "scope": if implemented {"local_candidate_geometry"} else {"rust_plan_geometry"},
        "supported_variants": supported_variants,
        "unsupported_variants": unsupported_variants,
        "parameter_limits": parameter_limits,
        "note": note
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shell_advertises_only_the_explicit_planar_and_full_cylinder_native_subset() {
        let spec = feature_spec("shell").unwrap();
        assert_eq!(
            spec["optional_parameters"],
            serde_json::json!(["offset_mode", "removed_faces"])
        );
        assert_eq!(spec["native_execution"]["status"], "implemented_exact");
        assert_eq!(spec["native_execution"]["geometry_support"], "exact");
        assert!(
            spec["native_execution"]["unsupported_variants"]
                .as_array()
                .unwrap()
                .iter()
                .any(|item| item.as_str().is_some_and(|value| value.contains("partial cylindrical")))
        );
        assert!(
            spec["native_execution"]["supported_variants"]
                .as_array()
                .unwrap()
                .iter()
                .any(|item| item.as_str().is_some_and(|value| value.contains("360-degree cylindrical")))
        );
        assert_eq!(spec["catia_mapping"], "unsupported");
    }

    #[test]
    fn camshaft_is_advertised_native_exact_while_catia_mapping_stays_approximate() {
        let spec = feature_spec("camshaft").unwrap();
        assert_eq!(spec["native_execution"]["status"], "implemented_exact");
        assert_eq!(spec["native_execution"]["geometry_support"], "exact");
        assert_eq!(spec["catia_mapping"], "approximate");
        assert!(
            spec["native_execution"]["supported_variants"]
                .as_array()
                .unwrap()
                .len()
                >= 3
        );
    }

    #[test]
    fn crankshaft_is_advertised_as_native_approximate_with_matching_catia_limit() {
        let spec = feature_spec("crankshaft").unwrap();
        assert_eq!(
            spec["native_execution"]["status"],
            "implemented_approximate"
        );
        assert_eq!(spec["native_execution"]["geometry_support"], "approximate");
        assert_eq!(spec["catia_mapping"], "approximate");
        assert_eq!(
            spec["required_parameters"],
            json!(["length", "main_d", "pin_d", "stroke", "throws"])
        );
    }

    #[test]
    fn helical_gear_is_native_approximate_and_catia_remains_straight_gear_approximation() {
        let spec = feature_spec("helical_gear").unwrap();
        assert_eq!(spec["catia_mapping"], "approximate");
        assert_eq!(
            spec["native_execution"]["status"],
            "implemented_approximate"
        );
        assert_eq!(spec["native_execution"]["geometry_support"], "approximate");
        assert_eq!(
            spec["native_execution"]["scope"],
            "local_candidate_geometry"
        );
        assert!(spec["native_execution"]["note"]
            .as_str()
            .unwrap()
            .contains("does not preserve helix angle"));
        assert!(spec["native_execution"]["parameter_limits"]["helix_deg"]
            .as_str()
            .unwrap()
            .contains("ignored"));
    }

    #[test]
    fn helix_is_native_approximate_but_catia_export_remains_unsupported() {
        let spec = feature_spec("helix").unwrap();
        assert_eq!(spec["catia_mapping"], "unsupported");
        assert_eq!(spec["native_execution"]["status"], "implemented_approximate");
        assert_eq!(spec["native_execution"]["geometry_support"], "approximate");
        assert_eq!(spec["native_execution"]["scope"], "local_candidate_geometry");
        assert!(spec["native_execution"]["parameter_limits"]["triangle_faces"]
            .as_str().unwrap().contains("100000"));
        assert!(spec["native_execution"]["unsupported_variants"]
            .as_array().unwrap().iter().any(|item| item
                .as_str().is_some_and(|text| text.contains("curvature radius"))));
    }

    #[test]
    fn worm_is_native_approximate_and_keeps_catia_smooth_shaft_boundary() {
        let spec = feature_spec("worm").unwrap();
        assert_eq!(spec["catia_mapping"], "approximate");
        assert_eq!(spec["native_execution"]["status"], "implemented_approximate");
        assert_eq!(spec["native_execution"]["geometry_support"], "approximate");
        assert_eq!(spec["native_execution"]["scope"], "local_candidate_geometry");
        assert!(spec["native_execution"]["parameter_limits"]["axis"]
            .as_str().unwrap().contains("global Z only"));
        assert!(spec["native_execution"]["unsupported_variants"]
            .as_array().unwrap().iter().any(|item| item
                .as_str().is_some_and(|text| text.contains("more than one solid"))));
        assert!(spec["catia_note"].as_str().unwrap().contains("光轴"));
    }

    #[test]
    fn loft_is_native_approximate_and_reports_legacy_catia_boundary() {
        let spec = feature_spec("loft").unwrap();
        assert_eq!(spec["catia_mapping"], "approximate");
        assert_eq!(spec["native_execution"]["status"], "implemented_approximate");
        assert_eq!(spec["native_execution"]["geometry_support"], "approximate");
        assert_eq!(spec["native_execution"]["scope"], "local_candidate_geometry");
        assert!(spec["native_execution"]["parameter_limits"]["thickness"]
            .as_str().unwrap().contains("0.01..=0.4"));
        assert!(spec["native_execution"]["unsupported_variants"]
            .as_array().unwrap().iter().any(|item| item
                .as_str().is_some_and(|text| text.contains("one valid closed solid"))));
        assert!(spec["native_execution"]["note"]
            .as_str().unwrap().contains("not an analytic ThruSections loft"));
        assert!(spec["catia_note"].as_str().unwrap().contains("根部截面拉伸近似"));
    }

    #[test]
    fn bevel_gear_is_advertised_as_native_approximate_and_keeps_catia_boundary() {
        let spec = feature_spec("bevel_gear").unwrap();
        assert_eq!(
            spec["native_execution"]["status"],
            "implemented_approximate"
        );
        assert_eq!(spec["native_execution"]["geometry_support"], "approximate");
        assert_eq!(spec["catia_mapping"], "approximate");
        assert_eq!(
            spec["required_parameters"],
            json!(["teeth", "module", "height", "cone_deg"])
        );
        assert_eq!(
            spec["optional_parameters"],
            json!(["bore_d", "center", "segments"])
        );
        assert!(spec["parameter_semantics"]["bore_d"]
            .as_str()
            .unwrap()
            .contains("implicit bore"));
        assert!(spec["native_execution"]["note"]
            .as_str()
            .unwrap()
            .contains("not an involute gear"));
    }
    #[test]
    fn coordinate_and_mapping_contracts_are_present_for_every_registered_kind() {
        let specs: Value = serde_json::from_str(SPECS).unwrap();
        let schemas = crate::native_tool_schemas::native_tool_schemas().unwrap();
        let kinds = schemas
            .iter()
            .find(|tool| tool["name"] == "cad_get_feature_spec")
            .unwrap()["inputSchema"]["properties"]["kind"]["enum"]
            .as_array()
            .unwrap();
        assert_eq!(specs.as_object().unwrap().len(), kinds.len());
        for kind in kinds {
            let result = feature_spec(kind.as_str().unwrap()).unwrap();
            assert!(result["required_parameters"].is_array());
            assert!(result["optional_parameters"].is_array());
            assert!(["exact", "approximate", "unsupported"]
                .contains(&result["catia_mapping"].as_str().unwrap()));
            assert!(result["coordinate_contract"]
                .as_str()
                .unwrap()
                .contains("+Y"));
        }
        assert!(feature_spec("unknown").is_err());
        let conrod = feature_spec("conrod").unwrap();
        assert_eq!(
            conrod["native_execution"]["status"],
            "implemented_approximate"
        );
        assert_eq!(
            conrod["native_execution"]["geometry_support"],
            "approximate"
        );
        assert_eq!(
            conrod["native_execution"]["scope"],
            "local_candidate_geometry"
        );
        assert!(conrod["native_execution"]["note"]
            .as_str()
            .unwrap()
            .contains("external-tangent trapezoidal web"));
        let cross_joint = feature_spec("cross_joint").unwrap();
        assert_eq!(
            cross_joint["native_execution"]["status"],
            "implemented_approximate"
        );
        assert_eq!(
            cross_joint["native_execution"]["geometry_support"],
            "approximate"
        );
        assert_eq!(
            cross_joint["native_execution"]["scope"],
            "local_candidate_geometry"
        );
        assert_eq!(
            cross_joint["required_parameters"],
            serde_json::json!(["span", "journal_d"])
        );
        assert_eq!(
            cross_joint["optional_parameters"],
            serde_json::json!(["center", "segments"])
        );
        assert!(cross_joint["parameter_semantics"]["segments"]
            .as_str()
            .unwrap()
            .contains("ignored by the native analytic-cylinder builder"));
        assert!(cross_joint["native_execution"]["note"]
            .as_str()
            .unwrap()
            .contains("legacy faceted-volume equality is not claimed"));
        assert_eq!(
            feature_spec("cylinder").unwrap()["parameter_semantics"]["base_default"],
            true
        );
        assert_eq!(
            feature_spec("box").unwrap()["parameter_semantics"]["profile_basis_by_axis"]["Y"]["u"],
            "Z"
        );
        assert_eq!(
            feature_spec("pocket").unwrap()["parameter_semantics"]["position_basis_by_axis"]["Y"],
            serde_json::json!(["X", "Z"])
        );
    }

    #[test]
    fn native_capability_contract_matches_registry_preflight_and_variant_shape() {
        let specs: Value = serde_json::from_str(SPECS).unwrap();
        let schemas = crate::native_tool_schemas::native_tool_schemas().unwrap();
        let kinds = schemas
            .iter()
            .find(|tool| tool["name"] == "cad_get_feature_spec")
            .unwrap()["inputSchema"]["properties"]["kind"]["enum"]
            .as_array()
            .unwrap();
        let mut supported_count = 0;
        for kind_value in kinds {
            let kind = kind_value.as_str().unwrap();
            assert!(
                specs.get(kind).is_some(),
                "schema kind {kind} lacks feature spec"
            );
            let contract = feature_spec(kind).unwrap()["native_execution"].clone();
            let backend_support = crate::native_plan_geometry::supports_native_kind(kind);
            assert_eq!(
                backend_support,
                contract["status"] != "not_implemented",
                "native capability disagrees with preflight/build dispatch for {kind}"
            );
            assert!(contract["supported_variants"].is_array(), "{kind}");
            assert!(contract["unsupported_variants"].is_array(), "{kind}");
            assert!(contract["parameter_limits"].is_object(), "{kind}");
            if backend_support {
                supported_count += 1;
                assert!(
                    contract["supported_variants"].as_array().unwrap().len() > 0,
                    "{kind}"
                );
                assert_ne!(contract["geometry_support"], "unsupported", "{kind}");
            } else {
                assert_eq!(contract["geometry_support"], "unsupported", "{kind}");
                assert!(
                    !contract["unsupported_variants"]
                        .as_array()
                        .unwrap()
                        .is_empty(),
                    "{kind}"
                );
            }
        }
        assert_eq!(supported_count, 27);
        assert_eq!(kinds.len(), specs.as_object().unwrap().len());
    }

    #[test]
    fn native_capability_registry_has_an_explicit_state_for_all_public_kinds() {
        const IMPLEMENTED: &[&str] = &[
            "box",
            "cylinder",
            "sphere",
            "hole",
            "pocket",
            "prism",
            "revolve",
            "pattern",
            "gear",
            "helical_gear",
            "bevel_gear",
            "socket_head_bolt",
            "spoked_wheel",
            "piston",
            "conrod",
            "cross_joint",
            "rotor",
            "camshaft",
            "crankshaft",
            "timing_pulley",
            "vbelt_pulley",
            "scroll_disc",
            "helix",
            "worm",
            "loft",
            "impeller",
            "shell",
        ];
        const UNSUPPORTED: &[&str] = &[];
        const APPROXIMATE: &[&str] = &[
            "gear",
            "helical_gear",
            "bevel_gear",
            "socket_head_bolt",
            "conrod",
            "cross_joint",
            "rotor",
            "crankshaft",
            "timing_pulley",
            "vbelt_pulley",
            "scroll_disc",
            "helix",
            "worm",
            "loft",
            "impeller",
        ];

        let schemas = crate::native_tool_schemas::native_tool_schemas().unwrap();
        let kinds = schemas
            .iter()
            .find(|tool| tool["name"] == "cad_get_feature_spec")
            .unwrap()["inputSchema"]["properties"]["kind"]["enum"]
            .as_array()
            .unwrap();
        let actual: std::collections::HashSet<_> =
            kinds.iter().map(|kind| kind.as_str().unwrap()).collect();
        let expected: std::collections::HashSet<_> = IMPLEMENTED
            .iter()
            .chain(UNSUPPORTED.iter())
            .copied()
            .collect();
        assert_eq!(
            actual, expected,
            "the native contract must cover all 27 schema kinds"
        );
        assert_eq!(actual.len(), 27);
        assert_eq!(IMPLEMENTED.len(), 27);
        assert_eq!(UNSUPPORTED.len(), 0);

        for kind in IMPLEMENTED {
            let contract = feature_spec(kind).unwrap()["native_execution"].clone();
            let expected_status = if APPROXIMATE.contains(kind) {
                "implemented_approximate"
            } else {
                "implemented_exact"
            };
            assert_eq!(contract["status"], expected_status, "{kind}");
            assert!(
                crate::native_plan_geometry::supports_native_kind(kind),
                "{kind}"
            );
            assert_eq!(contract["scope"], "local_candidate_geometry", "{kind}");
            assert!(
                !contract["supported_variants"]
                    .as_array()
                    .unwrap()
                    .is_empty(),
                "{kind}"
            );
            assert!(
                contract["parameter_limits"]
                    .as_object()
                    .is_some_and(|limits| !limits.is_empty()),
                "{kind} needs parameter limits"
            );
        }

        for kind in UNSUPPORTED {
            let contract = feature_spec(kind).unwrap()["native_execution"].clone();
            assert_eq!(contract["status"], "not_implemented", "{kind}");
            assert_eq!(contract["geometry_support"], "unsupported", "{kind}");
            assert_eq!(contract["scope"], "rust_plan_geometry", "{kind}");
            assert!(contract["supported_variants"]
                .as_array()
                .unwrap()
                .is_empty());
            assert!(
                !contract["unsupported_variants"]
                    .as_array()
                    .unwrap()
                    .is_empty(),
                "{kind}"
            );
            assert!(
                !crate::native_plan_geometry::supports_native_kind(kind),
                "{kind}"
            );

            // A schema-valid request for an unsupported kind must be stopped
            // by native preflight before build() tries to load OCCT.
            let plan = unsupported_kind_plan(kind);
            assert_eq!(
                crate::cad_plan_validation::validate_plan(&plan)["state"],
                "valid",
                "unsupported-kind probe must be valid CADPlan: {kind}"
            );
            let preflight_error = crate::native_plan_geometry::preflight(&plan).unwrap_err();
            assert!(preflight_error.contains(kind), "{kind}: {preflight_error}");
            let build_error = crate::native_plan_geometry::build(
                &plan,
                std::path::Path::new("native-contract-no-runtime.json"),
                &std::sync::atomic::AtomicBool::new(false),
            )
            .err()
            .unwrap();
            assert_eq!(build_error, preflight_error, "{kind}");
        }
    }

    #[test]
    fn advertised_native_parameter_limits_are_exercised_by_preflight_and_builder() {
        struct Probe {
            kind: &'static str,
            valid: Value,
            invalid: Value,
            limit_key: &'static str,
            limit_text: &'static str,
            error_text: &'static str,
        }

        // Each row is a valid request plus a boundary violation. Keeping the
        // executable probe separate from the prose contract catches drift in
        // either the advertised limit or the actual validator/builder gate.
        let probes = [
            Probe {
                kind: "box",
                valid: json!({"length":10,"width":8,"height":6}),
                invalid: json!({"length":10,"width":8,"height":6,"corner":1}),
                limit_key: "",
                limit_text: "corner!=0",
                error_text: "圆角矩形",
            },
            Probe {
                kind: "shell",
                valid: json!({"thickness":2,"offset_mode":"inward",
                    "removed_faces":[format!("shell-face-v1:{}", "a".repeat(64))]}),
                invalid: json!({"thickness":2,"offset_mode":"sideways",
                    "removed_faces":[format!("shell-face-v1:{}", "a".repeat(64))]}),
                limit_key: "offset_mode",
                limit_text: "required inward|outward",
                error_text: "必须显式为 inward 或 outward",
            },
            Probe {
                kind: "cylinder",
                valid: json!({"radius":4,"height":8}),
                invalid: json!({"radius":0,"height":8}),
                limit_key: "radius",
                limit_text: ">0",
                error_text: "radius",
            },
            Probe {
                kind: "sphere",
                valid: json!({"radius":4}),
                invalid: json!({"radius":0}),
                limit_key: "radius",
                limit_text: ">0",
                error_text: "radius",
            },
            Probe {
                kind: "hole",
                valid: json!({"diameter":2,"through":true,"pos":"point","point":[0,0,0]}),
                invalid: json!({"diameter":0,"through":true,"pos":"point","point":[0,0,0]}),
                limit_key: "diameter",
                limit_text: ">0",
                error_text: "diameter",
            },
            Probe {
                kind: "pocket",
                valid: json!({"length":4,"width":4,"depth":2}),
                invalid: json!({"length":4,"width":4,"depth":0}),
                limit_key: "depth",
                limit_text: "smaller than stock thickness",
                error_text: "depth",
            },
            Probe {
                kind: "prism",
                valid: json!({"profile":[[0,0],[8,0],[8,6],[0,6]],"height":4}),
                invalid: json!({"profile":[[0,0],[8,0],[8,6],[0,6]],"height":0}),
                limit_key: "height",
                limit_text: ">0",
                error_text: "height",
            },
            Probe {
                kind: "revolve",
                valid: json!({"profile":[[2,0],[4,0],[4,6],[2,6]],"angle":360}),
                invalid: json!({"profile":[[2,0],[4,0],[4,6],[2,6]],"angle":0}),
                limit_key: "angle",
                limit_text: "valid OCCT revolution angle",
                error_text: "angle",
            },
            Probe {
                kind: "loft",
                valid: json!({"root_chord":30,"tip_chord":16.5,"span":60,"thickness":0.12,"camber":0.05,"camber_pos":0.4,"twist":18,"sweep":0,"sections":8,"points":26}),
                invalid: json!({"root_chord":30,"tip_chord":16.5,"span":60,"thickness":0.41,"camber":0.05,"camber_pos":0.4,"twist":18,"sweep":0,"sections":8,"points":26}),
                limit_key: "thickness",
                limit_text: "0.01..=0.4",
                error_text: "0.01..=0.4",
            },
            Probe {
                kind: "impeller",
                valid: json!({"hub_d":20,"tip_d":60,"height":15,"blades":6,"blade_t":2,"twist_deg":25}),
                invalid: json!({"hub_d":20,"tip_d":60,"height":15,"blades":129,"blade_t":2,"twist_deg":25}),
                limit_key: "blades",
                limit_text: "3..=128",
                error_text: "3..=128",
            },
            Probe {
                kind: "impeller",
                valid: json!({"hub_d":20,"tip_d":60,"height":15,"blades":6,"blade_t":2,"twist_deg":25}),
                invalid: json!({"hub_d":20,"tip_d":20.5,"height":15,"blades":6,"blade_t":2,"twist_deg":25}),
                limit_key: "tip_d",
                limit_text: ">=1.05*hub_d",
                error_text: "tip_d>=1.05*hub_d",
            },
            Probe {
                kind: "pattern",
                valid: json!({"mode":"linear","count":2,"step":[5,0,0]}),
                invalid: json!({"mode":"linear","count":10001,"step":[5,0,0]}),
                limit_key: "count",
                limit_text: "1..=10000",
                error_text: "10001",
            },
            Probe {
                kind: "gear",
                valid: json!({"teeth":12,"module":1,"height":4}),
                invalid: json!({"teeth":10001,"module":1,"height":4}),
                limit_key: "teeth",
                limit_text: "6..=10000",
                error_text: "10001",
            },
            Probe {
                kind: "helical_gear",
                valid: json!({"teeth":12,"module":1,"height":4}),
                invalid: json!({"teeth":10001,"module":1,"height":4}),
                limit_key: "teeth",
                limit_text: "6..=10000",
                error_text: "10001",
            },
            Probe {
                kind: "bevel_gear",
                valid: json!({"teeth":20,"module":1,"height":4,"cone_deg":30}),
                invalid: json!({"teeth":1000,"module":1,"height":4,"cone_deg":30,"segments":100}),
                limit_key: "",
                limit_text: "facet count above 100000",
                error_text: "facet-BREP",
            },
            Probe {
                kind: "socket_head_bolt",
                valid: json!({"shank_d":6,"thread_pitch":1,"length":10,"head_d":10,"head_h":4,"hex_s":5,"hex_t":2}),
                invalid: json!({"shank_d":6,"thread_pitch":1,"length":10,"head_d":10,"head_h":4,"hex_s":5,"hex_t":5}),
                limit_key: "hex_t",
                limit_text: "<=head_h",
                error_text: "hex_t must not exceed head_h",
            },
            Probe {
                kind: "spoked_wheel",
                valid: json!({"outer_r":20,"rim_w":3,"hub_r":5,"height":8,"spokes":4,"spoke_w":3}),
                invalid: json!({"outer_r":20,"rim_w":3,"hub_r":5,"height":8,"spokes":513,"spoke_w":3}),
                limit_key: "spokes",
                limit_text: "2..=512",
                error_text: "512",
            },
            Probe {
                kind: "piston",
                valid: json!({"diameter":20,"height":20,"pin_d":4,"grooves":3}),
                invalid: json!({"diameter":20,"height":20,"pin_d":4,"grooves":257}),
                limit_key: "grooves",
                limit_text: "<=256",
                error_text: "256",
            },
            Probe {
                kind: "conrod",
                valid: json!({"length":60,"big_d":20,"small_d":14,"big_bore":10,"small_bore":6,"shank_w":8,"thick":5}),
                invalid: json!({"length":60,"big_d":20,"small_d":14,"big_bore":20,"small_bore":6,"shank_w":8,"thick":5}),
                limit_key: "big_d",
                limit_text: ">big_bore",
                error_text: "孔径必须小于",
            },
            Probe {
                kind: "cross_joint",
                valid: json!({"span":20,"journal_d":6}),
                invalid: json!({"span":0,"journal_d":6}),
                limit_key: "span",
                limit_text: ">0",
                error_text: "span",
            },
            Probe {
                kind: "rotor",
                valid: json!({"radius":20,"n":48,"height":8,"bore_d":0}),
                invalid: json!({"radius":20,"n":11,"height":8,"bore_d":0}),
                limit_key: "n",
                limit_text: "12..=4096",
                error_text: "12..=4096",
            },
            Probe {
                kind: "vbelt_pulley",
                valid: json!({"pitch_d":80,"grooves":1,"height":20,"groove_type":"Y"}),
                invalid: json!({"pitch_d":80,"grooves":65,"height":1000,"groove_type":"Y"}),
                limit_key: "grooves",
                limit_text: "1..=64",
                error_text: "1..=64",
            },
            Probe {
                kind: "camshaft",
                valid: json!({"length":80,"journal_d":10,"base_d":14,"lift":3,"lobes":2,"lobe_deg":0}),
                invalid: json!({"length":80,"journal_d":10,"base_d":14,"lift":3,"lobes":513,"lobe_deg":0}),
                limit_key: "lobes",
                limit_text: "1..=512",
                error_text: "1..=512",
            },
            Probe {
                kind: "crankshaft",
                valid: json!({"length":80,"main_d":10,"pin_d":8,"stroke":16,"throws":2}),
                invalid: json!({"length":80,"main_d":10,"pin_d":8,"stroke":16,"throws":129}),
                limit_key: "throws",
                limit_text: "1..=128",
                error_text: "1..=128",
            },
            Probe {
                kind: "timing_pulley",
                valid: json!({"pitch_d":40,"teeth":12,"height":10,"tooth_type":"XL"}),
                invalid: json!({"pitch_d":40,"teeth":513,"height":10,"tooth_type":"XL"}),
                limit_key: "teeth",
                limit_text: "6..=512",
                error_text: "6..=512",
            },
            Probe {
                kind: "scroll_disc",
                valid: json!({"outer_d":60,"height":8,"wall_t":2,"turns":2.5}),
                invalid: json!({"outer_d":60,"height":8,"wall_t":8,"turns":2.5}),
                limit_key: "wall_t",
                limit_text: "smaller than minimum normal turn spacing",
                error_text: "自交或相切",
            },
            Probe {
                kind: "helix",
                valid: json!({"radius":8,"wire_r":1,"height":20,"turns":3}),
                invalid: json!({"radius":8,"wire_r":1,"height":3,"turns":3}),
                limit_key: "self_intersection",
                limit_text: "axial pitch > 2*wire_r",
                error_text: "自交或相切",
            },
            Probe {
                kind: "helix",
                valid: json!({"radius":8,"wire_r":1,"height":20,"turns":3}),
                invalid: json!({"radius":0.5,"wire_r":1.1,"height":9.42477796076938,"turns":3}),
                limit_key: "self_intersection",
                limit_text: "curvature radius > wire_r",
                error_text: "最小曲率半径",
            },
            Probe {
                kind: "helix",
                valid: json!({"radius":8,"wire_r":1,"height":20,"turns":3}),
                invalid: json!({"radius":8,"wire_r":1,"height":200,"turns":3,"seg_per_turn":512,"wire_seg":64}),
                limit_key: "triangle_faces",
                limit_text: "100000",
                error_text: "超过 OCCT 上限",
            },
            Probe {
                kind: "worm",
                valid: json!({"radius":10,"height":60,"thread_r":12.5,"wire_r":2.5,"height2":50,"turns":3.75,"center":[0,0,-30],"center2":[0,0,-25],"segments":48,"seg_per_turn":16,"wire_seg":8}),
                invalid: json!({"radius":10,"height":60,"thread_r":14,"wire_r":2.5,"height2":50,"turns":3.75,"center":[0,0,-30],"center2":[0,0,-25],"segments":48,"seg_per_turn":16,"wire_seg":8}),
                limit_key: "engagement",
                limit_text: "thread_r - wire_r <= radius",
                error_text: "径向间隙",
            },
            Probe {
                kind: "worm",
                valid: json!({"radius":10,"height":60,"thread_r":12.5,"wire_r":2.5,"height2":50,"turns":3.75,"center":[0,0,-30],"center2":[0,0,-25],"segments":48,"seg_per_turn":16,"wire_seg":8}),
                invalid: json!({"radius":10,"height":60,"thread_r":12.5,"wire_r":2.5,"height2":50,"turns":3.75,"axis":"X","center":[0,0,-30],"center2":[0,0,-25],"segments":48,"seg_per_turn":16,"wire_seg":8}),
                limit_key: "axis",
                limit_text: "global Z only",
                error_text: "全局 Z 轴",
            },
            Probe {
                kind: "worm",
                valid: json!({"radius":10,"height":60,"thread_r":12.5,"wire_r":2.5,"height2":50,"turns":3.75,"center":[0,0,-30],"center2":[0,0,-25],"segments":48,"seg_per_turn":16,"wire_seg":8}),
                invalid: json!({"radius":10,"height":60,"thread_r":12.5,"wire_r":2.5,"height2":50,"turns":3.75,"center":[0,0,-30],"center2":[0.01,0,-25],"segments":48,"seg_per_turn":16,"wire_seg":8}),
                limit_key: "center2",
                limit_text: "must share its X/Y axis",
                error_text: "同一全局 Z 轴",
            },
        ];

        let schemas = crate::native_tool_schemas::native_tool_schemas().unwrap();
        let public_kinds = schemas
            .iter()
            .find(|tool| tool["name"] == "cad_get_feature_spec")
            .unwrap()["inputSchema"]["properties"]["kind"]["enum"]
            .as_array()
            .unwrap();
        let expected_probes: std::collections::HashSet<_> = public_kinds
            .iter()
            .filter_map(Value::as_str)
            .filter(|kind| crate::native_plan_geometry::supports_native_kind(kind))
            .collect();
        let actual_probes: std::collections::HashSet<_> =
            probes.iter().map(|probe| probe.kind).collect();
        assert_eq!(
            actual_probes, expected_probes,
            "every registered native builder kind needs an executable parameter-boundary probe"
        );

        for probe in probes {
            let capability = feature_spec(probe.kind).unwrap()["native_execution"].clone();
            if probe.limit_key.is_empty() {
                assert!(
                    capability["unsupported_variants"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .any(|item| item
                            .as_str()
                            .is_some_and(|text| text.contains(probe.limit_text))),
                    "{} unsupported_variants must publish {:?}",
                    probe.kind,
                    probe.limit_text
                );
            } else {
                let limit = capability["parameter_limits"][probe.limit_key]
                    .as_str()
                    .unwrap_or_else(|| {
                        panic!("{} has no limit for {}", probe.kind, probe.limit_key)
                    });
                assert!(
                    limit.contains(probe.limit_text),
                    "{} {}: {limit}",
                    probe.kind,
                    probe.limit_key
                );
            }

            let valid = contract_probe_plan(probe.kind, &probe.valid);
            assert_eq!(
                crate::native_plan_geometry::preflight(&valid).unwrap()["state"],
                "valid",
                "valid {} probe",
                probe.kind
            );
            let invalid = contract_probe_plan(probe.kind, &probe.invalid);
            let preflight = crate::native_plan_geometry::preflight(&invalid);
            if probe.kind == "socket_head_bolt" || probe.kind == "revolve" {
                // These constraints are enforced after CADPlan preflight by
                // the geometry builder itself.
                assert_eq!(
                    preflight.unwrap()["state"],
                    "valid",
                    "{} is a builder-time limit",
                    probe.kind
                );
                let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("target/f4-occt/runtime-manifest.json");
                if !manifest.is_file() {
                    continue;
                }
                let error = crate::native_plan_geometry::build(
                    &invalid,
                    &manifest,
                    &std::sync::atomic::AtomicBool::new(false),
                )
                .err()
                .unwrap();
                assert!(error.contains(probe.error_text), "{}: {error}", probe.kind);
            } else {
                let error = preflight.unwrap_err();
                assert!(error.contains(probe.error_text), "{}: {error}", probe.kind);
                let build_error = crate::native_plan_geometry::build(
                    &invalid,
                    std::path::Path::new("native-contract-no-runtime.json"),
                    &std::sync::atomic::AtomicBool::new(false),
                )
                .err()
                .unwrap();
                assert_eq!(build_error, error, "{}", probe.kind);
            }
        }
    }

    #[test]
    fn native_pocket_depth_limit_is_checked_against_stock_before_candidate_build() {
        let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target/f4-occt/runtime-manifest.json");
        if !manifest.is_file() {
            return;
        }
        let stock = json!({"id":"stock","kind":"box","operation":"add",
            "parameters":{"length":20,"width":20,"height":20},
            "sources":{"length":{"type":"user"},"width":{"type":"user"},"height":{"type":"user"}}});
        let pocket = json!({"id":"pocket-1","kind":"pocket","operation":"cut",
            "parameters":{"length":10,"width":10,"depth":20,"x":0,"y":0,"axis":"Z","side":"positive"},
            "sources":{"length":{"type":"user"},"width":{"type":"user"},"depth":{"type":"user"},"x":{"type":"user"},"y":{"type":"user"}}});
        let plan = crate::cad_plan::create("pocket depth boundary", stock, "contract probe", None);
        let plan = crate::cad_plan::add(&plan, &pocket).unwrap();
        let contract = feature_spec("pocket").unwrap()["native_execution"]["parameter_limits"]
            ["depth"]
            .as_str()
            .unwrap()
            .to_owned();
        assert!(contract.contains("smaller than stock thickness"));
        assert_eq!(
            crate::native_plan_geometry::preflight(&plan).unwrap()["state"],
            "valid"
        );
        let error = crate::native_plan_geometry::build(
            &plan,
            &manifest,
            &std::sync::atomic::AtomicBool::new(false),
        )
        .err()
        .unwrap();
        assert!(error.contains("盲槽深度必须小于实体轴向厚度"), "{error}");
    }

    fn contains_number(value: &Value) -> bool {
        value.is_number()
            || value
                .as_array()
                .is_some_and(|items| items.iter().any(contains_number))
    }

    fn contract_feature(id: &str, kind: &str, parameters: &Value, relations: Value) -> Value {
        let sources: serde_json::Map<String, Value> = parameters
            .as_object()
            .unwrap()
            .iter()
            .filter(|(_, value)| contains_number(value))
            .map(|(key, _)| {
                (
                    key.clone(),
                    json!({"type":"user","note":"native capability contract test"}),
                )
            })
            .collect();
        json!({"id":id,"kind":kind,"operation":"add","parameters":parameters,"sources":sources,"relations":relations})
    }

    fn contract_probe_plan(kind: &str, parameters: &Value) -> Value {
        if kind == "pattern" {
            let seed = contract_feature(
                "seed",
                "box",
                &json!({"length":4,"width":4,"height":4}),
                json!([]),
            );
            let pattern = contract_feature(
                "pattern-1",
                kind,
                parameters,
                json!([{"type":"pattern_source","feature_id":"seed"}]),
            );
            let plan =
                crate::cad_plan::create("native capability contract", seed, "contract probe", None);
            return crate::cad_plan::add(&plan, &pattern).unwrap();
        }
        if kind == "shell" {
            let seed = contract_feature(
                "seed",
                "box",
                &json!({"length":10,"width":8,"height":6}),
                json!([]),
            );
            let mut plan = crate::cad_plan::create(
                "native shell capability contract",
                seed,
                "contract probe",
                None,
            );
            let fingerprint = crate::cad_plan::geometry_fingerprint(&plan).unwrap();
            let mut shell = contract_feature(
                "shell-probe",
                kind,
                parameters,
                json!([{"type":"shell_target","feature_id":"seed"}]),
            );
            shell["operation"] = json!("cut");
            shell["selection_binding"] =
                crate::native_shell::binding(0, &fingerprint, "7.9.3", "seed");
            plan = crate::cad_plan::add(&plan, &shell).unwrap();
            return plan;
        }
        let feature = contract_feature("probe-1", kind, parameters, json!([]));
        crate::cad_plan::create(
            "native capability contract",
            feature,
            "contract probe",
            None,
        )
    }

    fn unsupported_kind_plan(kind: &str) -> Value {
        let parameters = match kind {
            "helix" => json!({"radius":8,"wire_r":1,"height":20,"turns":3}),
            "worm" => json!({"radius":8,"height":30,"thread_r":2,"wire_r":1,"turns":3}),
            "shell" => json!({"thickness":2}),
            _ => panic!("not an explicitly unsupported kind: {kind}"),
        };
        let feature = contract_feature("unsupported-probe", kind, &parameters, json!([]));
        crate::cad_plan::create(
            "unsupported capability contract",
            feature,
            "contract probe",
            None,
        )
    }
}
