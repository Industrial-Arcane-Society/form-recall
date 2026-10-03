//! Version-controlled CAD feature parameter contracts, migrated from CADPlan v1.
use serde_json::{Value, json};
const SPECS: &str = r###"{"box":{"kind":"box","required_parameters":["length","width","height"],"optional_parameters":["center","axis","base","corner","corner_mode"],"catia_mapping":"exact","catia_note":"矩形草图加凸台；当前 CATIA 映射仅支持 Z 轴拉伸。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"cylinder":{"kind":"cylinder","required_parameters":["radius","height"],"optional_parameters":["radius2","center","axis","base","segments"],"catia_mapping":"exact","catia_note":"center 默认表示轴向底面中心(base=true)，height 沿 axis 正向拉伸；base=false 时 center 表示几何中点。等半径时为圆草图加凸台；圆锥台会近似，非 Z 轴圆锥台暂不支持。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{"center":"base-center point when base=true (default); geometric midpoint when base=false","height":"extends along positive axis when base=true; straddles center when base=false","base_default":true}},"socket_head_bolt":{"kind":"socket_head_bolt","required_parameters":["shank_d","thread_pitch","length","head_d","head_h","hex_s","hex_t"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"本地网格按 thread_pitch 生成可见的近似 ISO 公制螺旋牙型；整段杆部带牙，未建牙端倒角/退刀槽。当前 CATIA 导出器仍以光杆凸台和六角凹槽表达，不会创建原生或装饰螺纹。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"sphere":{"kind":"sphere","required_parameters":["radius"],"optional_parameters":["center","segments","rings"],"catia_mapping":"approximate","catia_note":"CATIA 导出器以多层圆盘近似球体。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{"center":"geometric center"}},"prism":{"kind":"prism","required_parameters":["profile","height"],"optional_parameters":["center","axis"],"catia_mapping":"exact","catia_note":"闭合多边形草图加凸台；当前仅支持 Z 轴。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{"center":"origin of the profile's base plane","height":"extends along positive axis"}},"revolve":{"kind":"revolve","required_parameters":["profile","angle"],"optional_parameters":["center","axis","segments"],"catia_mapping":"approximate","catia_note":"本地 OCCT BREP 按闭合[半径,轴向坐标]轮廓精确回转，并将新增回转体与已有实体做布尔合并；CATIA 导出器仍用多层圆盘近似，非 CATIA 原生回转特征。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{"profile":"closed [radius, axial-Z] points when axis=Z","center":"global origin on the revolve axis","order":"can be added to an existing body when the OCCT backend can perform a valid union; legacy tessellated/approximate export paths may have stricter Boolean limits"}},"gear":{"kind":"gear","required_parameters":["teeth","module","height"],"optional_parameters":["center","axis","base"],"catia_mapping":"exact","catia_note":"齿廓转换为多边形草图并拉伸；当前仅支持 Z 轴。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"loft":{"kind":"loft","required_parameters":["root_chord","tip_chord","span","thickness"],"optional_parameters":["camber","camber_pos","twist","sweep","sections","points","rotate","translate"],"catia_mapping":"approximate","catia_note":"CATIA 中以根部截面拉伸近似多截面放样。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"hole":{"kind":"hole","required_parameters":["diameter","through","pos"],"optional_parameters":["point","x","y","count","pcd","phase","depth","margin","axis","segments","entry_point","direction"],"catia_mapping":"exact","catia_note":"圆形切除；through=true 时按当前零件包围盒生成贯穿深度。盲孔可用 entry_point 与 direction 显式指定入口点和沿全局轴的切入方向；未指定时沿用正轴端面默认语义。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"pocket":{"kind":"pocket","required_parameters":["length","width","depth"],"optional_parameters":["x","y","axis","side","corner","corner_mode"],"catia_mapping":"exact","catia_note":"矩形/圆角矩形凹槽；当前仅支持 Z 轴。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"pattern":{"kind":"pattern","required_parameters":["mode","count"],"optional_parameters":["step","axis","angle","full","center"],"catia_mapping":"exact","catia_note":"转换为显式复制的 CATIA 操作；relations 示例：[{\"type\":\"pattern_source\",\"feature_id\":\"紧邻前一特征的 id\"}]。operation 必须与源特征相同：阵列切孔用 cut，阵列加料用 add。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"helix":{"kind":"helix","required_parameters":["radius","wire_r","height","turns"],"optional_parameters":["center","axis","taper","seg_per_turn","wire_seg","taper_start"],"catia_mapping":"unsupported","catia_note":"弹簧/螺旋不能由当前草图、凸台和凹槽通道表达。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"spoked_wheel":{"kind":"spoked_wheel","required_parameters":["outer_r","rim_w","hub_r","height","spokes","spoke_w"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 仅生成轮缘与轮毂，辐条需要补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"vbelt_pulley":{"kind":"vbelt_pulley","required_parameters":["pitch_d","grooves","height","groove_type"],"optional_parameters":["outer_d","groove_depth","bore_d","center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 生成光轮盘体；V 形回转槽需要在 CATIA 中补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"timing_pulley":{"kind":"timing_pulley","required_parameters":["pitch_d","teeth","height","tooth_type"],"optional_parameters":["bore_d","center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 生成盘体，齿形需要在 CATIA 中补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"worm":{"kind":"worm","required_parameters":["radius","height","thread_r","wire_r","turns"],"optional_parameters":["center","center2","height2","segments","seg_per_turn","wire_seg"],"catia_mapping":"approximate","catia_note":"CATIA 仅生成光轴，螺旋齿需要补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"helical_gear":{"kind":"helical_gear","required_parameters":["teeth","module","height"],"optional_parameters":["helix_deg","bore_d","center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 以直齿轮近似，省略螺旋角。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"bevel_gear":{"kind":"bevel_gear","required_parameters":["teeth","module","height","cone_deg"],"optional_parameters":["bore_d","center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 以直齿圆柱轮廓近似，省略锥面。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"crankshaft":{"kind":"crankshaft","required_parameters":["length","main_d","pin_d","stroke","throws"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 按 X 轴布置主轴颈、交替偏置连杆颈和哑铃形曲柄臂(主轴圆 + 连杆颈圆外公切凸包)，曲柄臂外形被简化。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"camshaft":{"kind":"camshaft","required_parameters":["length","journal_d","base_d","lift","lobes","lobe_deg"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 凸轮沿轴向等分布置，相位需核对。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"impeller":{"kind":"impeller","required_parameters":["hub_d","tip_d","height","blades","blade_t","twist_deg"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 仅生成轮毂和轮盘，扭转叶片需要补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"piston":{"kind":"piston","required_parameters":["diameter","height","pin_d","grooves"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 以圆柱体近似，销孔和活塞环槽需要补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"conrod":{"kind":"conrod","required_parameters":["length","big_d","small_d","big_bore","small_bore","shank_w","thick"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 以大头、小头和直杆身近似，杆身过渡被简化。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{},"native_execution":{"status":"implemented_approximate","geometry_support":"approximate","scope":"local_candidate_geometry","note":"Rust/OCCT builds analytic annular heads with an approximate external-tangent trapezoidal web. Web contact and I-section details are simplified; consult support_details. CATIA mapping remains approximate."}},"cross_joint":{"kind":"cross_joint","required_parameters":["span","journal_d"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 仅生成单轴，另一根横轴需要补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{"center":"global XYZ center of the hub","segments":"legacy facet tessellation setting; ignored by the native analytic-cylinder builder"}},"scroll_disc":{"kind":"scroll_disc","required_parameters":["outer_d","height","wall_t","turns"],"optional_parameters":["center","segments"],"catia_mapping":"approximate","catia_note":"CATIA 仅生成底盘，螺旋壁需要补建。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"rotor":{"kind":"rotor","required_parameters":["radius","n","height","bore_d"],"optional_parameters":["center"],"catia_mapping":"exact","catia_note":"转子轮廓、厚度与中心孔映射为可编辑草图和凹槽。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}},"shell":{"kind":"shell","required_parameters":["thickness"],"optional_parameters":[],"catia_mapping":"unsupported","catia_note":"当前 Document/CATIA 转换器没有抽壳实现。","coordinate_contract":"CADPlan/global preview: X=left-right (box.length), Y=front-back thickness (box.width), Z=vertical (box.height); box center is global XYZ. The front orthographic view looks from +Y with screen horizontal X and vertical Z. For cylinder/cone, base defaults to true: center is the axial base-center point and height extends in the positive axis direction; set base=false only when center is the geometric midpoint. For prism, center is the profile's base-plane origin and height extends in the positive axis direction. For revolve about Z, profile points are [radius, axial-Z] and center is the global axis origin. Sphere center is its geometric center. A hole point is global XYZ and its axis is the bore direction.","parameter_semantics":{}}}"###;

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
    result["native_execution"] = native_execution_contract(kind);
    Ok(result)
}

/// Per-kind native geometry capability contract surfaced to `cad_get_feature_spec`.
/// Backend implementation membership is shared with preflight; variant limits
/// below describe the additional gates applied by preflight/build.
fn native_execution_contract(kind: &str) -> Value {
    let implemented = crate::native_plan_geometry::supports_native_kind(kind);
    let approximate = matches!(kind, "gear" | "socket_head_bolt" | "conrod" | "cross_joint" | "rotor");
    let (supported_variants, unsupported_variants, parameter_limits, note) = match kind {
        "rotor" => (
            json!(["global Z extrusion centered on center", "explicit bore_d=0 or enclosed circular bore"]),
            json!(["axis other than Z", "base convention", "bore intersecting discrete outer profile"]),
            json!({"radius":">0 explicit", "height":">0 explicit", "bore_d":">=0 explicit; radius smaller than actual polygon clearance", "n":"explicit integer 12..=4096"}),
            "Approximate legacy rounded-triangle radial profile (fixed flank=0.82, cosine exponent=6), polygonal OCCT prism and analytic central bore. Not an exact Reuleaux curve or production engine rotor.",
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
        "socket_head_bolt" => (json!(["native screw mesh geometry", "centered on global Z axis"]), json!([]), json!({"shank_d":">0", "thread_pitch":">0", "length":">0", "head_d":">0", "head_h":">0", "hex_s":">0", "hex_t":">0 and <=head_h", "segments":"integer clamped to 32..=48"}), "Local screw thread mesh is approximate; CATIA export remains a smooth shank."),
        "spoked_wheel" => (json!(["global-Z radial spokes"]), json!(["axis other than Z", "spokes<2 or >512"]), json!({"spokes":"2..=512", "outer_r":">0", "rim_w":">0 and outer_r-rim_w > hub_r", "hub_r":">0", "height":">0", "spoke_w":">0 and <2*hub_r"}), "Native analytic rim, hub and radial prism spokes; CATIA mapping remains approximate."),
        "piston" => (json!(["legacy closed profile plus X-axis pin bore"]), json!([]), json!({"diameter":">2 mm because legacy inner profile radius has a 1 mm floor", "height":">0", "pin_d":">=0; bore radius clamped to 0.75 body radius and 0.45 height", "grooves":"finite value >=0 and <=256; truncated toward zero"}), "Native analytic build; CATIA mapping remains approximate."),
        "camshaft" => (json!(["global X journal shaft", "48-point 120-degree harmonic cam profiles", "phased cams distributed evenly along shaft"]), json!(["axis other than X", "lobes outside 1..=512", "lobe width exceeding shaft length"]), json!({"length":">=2 mm", "journal_d":">=2 mm", "base_d":">=2 mm", "lift":">=0 mm", "lobes":"integer 1..=512", "lobe_deg":"finite; angle units honored", "center":"finite global XYZ"}), "Exact local analytic OCCT B-Rep for the legacy Document cam profile; CATIA mapping remains approximate."),
        "conrod" => (json!(["annular heads with tangent web approximation"]), json!([]), json!({"length":">0 and long enough for tangent web", "big_d":">big_bore", "small_d":">small_bore", "big_bore":">0", "small_bore":">0", "shank_w":">0", "thick":">0"}), "Analytic annular heads with approximate external-tangent trapezoidal web; web contact and I-section details are simplified."),
        "cross_joint" => (json!(["hub plus four journals on global +/-X|+/-Y"]), json!([]), json!({"span":">0 and large enough for hub plus positive journal length", "journal_d":">0", "center":"finite global XYZ"}), "Native analytic construction; legacy faceted-volume equality is not claimed."),
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
    fn camshaft_is_advertised_native_exact_while_catia_mapping_stays_approximate() {
        let spec = feature_spec("camshaft").unwrap();
        assert_eq!(spec["native_execution"]["status"], "implemented_exact");
        assert_eq!(spec["native_execution"]["geometry_support"], "exact");
        assert_eq!(spec["catia_mapping"], "approximate");
        assert!(spec["native_execution"]["supported_variants"].as_array().unwrap().len() >= 3);
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
        assert_eq!(supported_count, 16);
        assert_eq!(kinds.len(), specs.as_object().unwrap().len());
    }
}
