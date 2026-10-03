//! Legacy-compatible `.aimodel` JSON and binary STL I/O.
use crate::cad_plan_validation;
use crate::native_occt::TriangleMesh;
use serde_json::{Map, Number, Value, json};
use std::fmt::Write as FmtWrite;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

const MAX_AIMODEL_BYTES: u64 = 128 * 1024 * 1024;
const MAX_TRIANGLES: usize = 5_000_000;
const MAX_OBJ_BYTES: usize = 128 * 1024 * 1024;
static TEMP_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Debug, PartialEq)]
pub struct StlStats {
    pub triangle_count: u32,
    pub bounds: [[f32; 2]; 3],
    pub signed_volume: f64,
}

/// Load the JSON object consumed by legacy `exporters.load_project` and
/// `Document.from_dict`. Unknown root and feature fields are retained.
pub fn load_aimodel(path: &Path) -> Result<Value, String> {
    let meta = fs::metadata(path).map_err(|e| format!("cannot stat project: {e}"))?;
    if meta.len() > MAX_AIMODEL_BYTES {
        return Err(".aimodel exceeds 128 MiB".into());
    }
    let bytes = fs::read(path).map_err(|e| format!("cannot read project: {e}"))?;
    let value: Value =
        serde_json::from_slice(&bytes).map_err(|e| format!("invalid .aimodel JSON: {e}"))?;
    validate_document(&value)?;
    Ok(value)
}

/// Load the executable Document view (the values produced by Python
/// `Document.from_dict(...).to_dict()`), while carrying root extension fields
/// such as the original CADPlan and feature manifest through unchanged.
pub fn load_document(path: &Path) -> Result<Value, String> {
    let raw = load_aimodel(path)?;
    let mut project = raw
        .as_object()
        .cloned()
        .ok_or(".aimodel root must be an object")?;
    let name = raw
        .get("name")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or("Part1")
        .to_owned();
    project.insert("format".into(), json!("ai-modeler"));
    project.insert("version".into(), json!(1));
    project.insert("name".into(), json!(name));
    project.insert(
        "source_prompt".into(),
        json!(
            raw.get("source_prompt")
                .and_then(Value::as_str)
                .unwrap_or("")
        ),
    );
    let rows = raw
        .get("features")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let features = rows
        .iter()
        .map(normalize_feature)
        .collect::<Result<Vec<_>, _>>()?;
    project.insert("features".into(), Value::Array(features));
    Ok(Value::Object(project))
}

/// Write the complete project object as UTF-8 JSON with same-directory
/// temp+rename semantics. The existing destination remains untouched on error.
pub fn save_aimodel(path: &Path, project: &Value) -> Result<(), String> {
    validate_document(project)?;
    let bytes = serde_json::to_vec_pretty(project).map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_AIMODEL_BYTES {
        return Err(".aimodel exceeds 128 MiB".into());
    }
    atomic_write(path, &bytes)
}

/// Convert a validated CADPlan v1.0 using the same unit scaling and legacy
/// Feature serialization rules as `cadplan._to_document`, retaining the full
/// source plan and per-feature source maps beside the executable Document.
pub fn cadplan_to_project(plan: &Value) -> Result<Value, String> {
    let validation = cad_plan_validation::validate_plan(plan);
    if validation.get("valid").and_then(Value::as_bool) != Some(true) {
        return Err(format!("CADPlan validation failed: {validation}"));
    }
    let length = match plan["units"]["length"].as_str().unwrap_or("") {
        "mm" => 1.0,
        "cm" => 10.0,
        "m" => 1000.0,
        "in" => 25.4,
        _ => return Err("unsupported CADPlan length unit".into()),
    };
    let angle = match plan["units"]["angle"].as_str().unwrap_or("") {
        "deg" => 1.0,
        "rad" => 180.0 / std::f64::consts::PI,
        _ => return Err("unsupported CADPlan angle unit".into()),
    };
    let mut docs = Vec::new();
    let mut manifest = Map::new();
    for item in plan["features"]
        .as_array()
        .ok_or("CADPlan features must be an array")?
    {
        let kind = item["kind"]
            .as_str()
            .ok_or("CADPlan feature kind missing")?;
        let id = item["id"].as_str().ok_or("CADPlan feature id missing")?;
        let source_params = item["parameters"]
            .as_object()
            .ok_or("CADPlan feature parameters must be an object")?;
        let mut params = Map::new();
        for (key, value) in source_params {
            let converted = if key == "rotate" {
                scale_rotate(value, angle)?
            } else if key == "thickness" && kind == "loft" {
                value.clone()
            } else if key == "thickness" && kind == "shell"
                || length_key(key)
                || length_array_key(key)
            {
                scale_value(value, length)?
            } else if angle_key(key) {
                scale_value(value, angle)?
            } else {
                value.clone()
            };
            params.insert(key.clone(), converted);
        }
        if kind == "hole" && params.get("pos").and_then(Value::as_str) == Some("point") {
            let point = params
                .get("point")
                .and_then(Value::as_array)
                .ok_or("hole point must have three coordinates")?;
            if point.len() != 3 {
                return Err("hole point must have three coordinates".into());
            }
            let (x, y) = match params
                .get("axis")
                .and_then(Value::as_str)
                .unwrap_or("Z")
                .to_ascii_uppercase()
                .as_str()
            {
                "Z" => (point[0].clone(), point[1].clone()),
                "X" => (point[1].clone(), point[2].clone()),
                _ => (point[0].clone(), point[2].clone()),
            };
            params.insert("x".into(), x);
            params.insert("y".into(), y);
        }
        if kind == "box" && params.contains_key("center") && !source_params.contains_key("base") {
            params.insert("base".into(), Value::Bool(false));
        }
        let sources = item
            .get("sources")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let mut provenance = Map::new();
        for (key, source) in &sources {
            if let Some(t) = source.get("type").and_then(Value::as_str) {
                provenance.insert(key.clone(), Value::String(t.into()));
            }
        }
        if kind == "hole" {
            if let Some(t) = sources
                .get("point")
                .and_then(|x| x.get("type"))
                .and_then(Value::as_str)
            {
                provenance.insert("x".into(), json!(t));
                provenance.insert("y".into(), json!(t));
            }
        }
        let relations = item
            .get("relations")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let name = item
            .get("name")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .unwrap_or_else(|| kind_label(kind).to_owned());
        let op = item["operation"]
            .as_str()
            .ok_or("CADPlan feature operation missing")?;
        let enabled = item.get("enabled").cloned().unwrap_or(json!(true));
        if !enabled.is_boolean() {
            return Err(format!("CADPlan feature {id} enabled must be a boolean"));
        }
        docs.push(json!({"kind":kind,"params":params,"op":op,"name":name,"note":item.get("note").cloned().unwrap_or(json!("")),
            "feature_id":id,"relations":relations,"enabled":enabled,"provenance":provenance}));
        manifest.insert(id.into(),json!({"kind":kind,"operation":op,"name":name,"parameters":item["parameters"],"sources":sources,"relations":relations}));
    }
    let project = json!({"format":"ai-modeler","version":1,"name":plan["name"],"source_prompt":plan.get("source_prompt").cloned().unwrap_or(json!("")),"features":docs,
        "cadplan_schema_version":plan["schema_version"],"cadplan":plan,"feature_manifest":manifest});
    validate_document(&project)?;
    Ok(project)
}

/// Write the OCCT triangle stream in the binary STL layout used by the legacy
/// exporter (80-byte header, little-endian count, 50-byte records). Degenerate
/// triangles are dropped as in legacy `_safe_tris`.
pub fn write_binary_stl(path: &Path, mesh: &TriangleMesh, name: &str) -> Result<StlStats, String> {
    if mesh.positions.is_empty()
        || mesh.positions.len() % 3 != 0
        || mesh.normals.len() != mesh.positions.len()
        || mesh.positions.len() / 3 > MAX_TRIANGLES
    {
        return Err("invalid or oversized mesh for STL".into());
    }
    if mesh
        .positions
        .iter()
        .chain(mesh.normals.iter())
        .flatten()
        .any(|v| !v.is_finite())
    {
        return Err("mesh contains non-finite values".into());
    }
    let mut records = Vec::<([f32; 3], [[f32; 3]; 3])>::new();
    let mut bounds = [[f32::INFINITY, f32::NEG_INFINITY]; 3];
    let mut volume = 0.0;
    for tri in mesh.positions.chunks_exact(3) {
        let a = as_f32(tri[0])?;
        let b = as_f32(tri[1])?;
        let c = as_f32(tri[2])?;
        let face_cross = cross(sub(tri[1], tri[0]), sub(tri[2], tri[0]));
        let len = dot(face_cross, face_cross).sqrt();
        if len < 1e-12 {
            continue;
        }
        let normal = as_f32(face_cross.map(|v| v / len))?;
        for p in [a, b, c] {
            for axis in 0..3 {
                bounds[axis][0] = bounds[axis][0].min(p[axis]);
                bounds[axis][1] = bounds[axis][1].max(p[axis]);
            }
        }
        let a64 = a.map(f64::from);
        let b64 = b.map(f64::from);
        let c64 = c.map(f64::from);
        volume += dot(a64, cross(sub(b64, a64), sub(c64, a64))) / 6.0;
        records.push((normal, [a, b, c]));
    }
    let count = u32::try_from(records.len()).map_err(|_| "STL triangle count exceeds u32")?;
    if count == 0 {
        return Err("mesh has no non-degenerate triangles".into());
    }
    let mut bytes = Vec::with_capacity(84 + records.len() * 50);
    let label = format!("AI Modeler STL - {name}")
        .chars()
        .map(|ch| if ch.is_ascii() { ch as u8 } else { b'?' })
        .collect::<Vec<_>>();
    let mut header = [0u8; 80];
    header[..label.len().min(80)].copy_from_slice(&label[..label.len().min(80)]);
    bytes.extend_from_slice(&header);
    bytes.extend_from_slice(&count.to_le_bytes());
    for (normal, vertices) in records {
        for v in normal {
            bytes.extend_from_slice(&v.to_le_bytes())
        }
        for vertex in vertices {
            for v in vertex {
                bytes.extend_from_slice(&v.to_le_bytes())
            }
        }
        bytes.extend_from_slice(&0u16.to_le_bytes());
    }
    atomic_write(path, &bytes)?;
    Ok(StlStats {
        triangle_count: count,
        bounds,
        signed_volume: volume,
    })
}

/// Write the same triangle stream as a Wavefront OBJ mesh in model millimeters.
/// Each triangle owns three vertices and matching normals, so face indices are
/// deterministic and do not depend on vertex deduplication or material files.
pub fn write_wavefront_obj(path: &Path, mesh: &TriangleMesh) -> Result<usize, String> {
    if mesh.positions.is_empty()
        || mesh.positions.len() % 3 != 0
        || mesh.normals.len() != mesh.positions.len()
        || mesh.positions.len() / 3 > MAX_TRIANGLES
    {
        return Err("invalid or oversized mesh for OBJ".into());
    }
    if mesh
        .positions
        .iter()
        .chain(mesh.normals.iter())
        .flatten()
        .any(|value| !value.is_finite())
    {
        return Err("mesh contains non-finite values".into());
    }

    let mut text = String::new();
    text.push_str("# AI Modeler native mesh; units: mm\no AI_Modeler\n");
    for position in &mesh.positions {
        writeln!(
            &mut text,
            "v {} {} {}",
            position[0], position[1], position[2]
        )
        .map_err(|error| error.to_string())?;
        if text.len() > MAX_OBJ_BYTES {
            return Err("OBJ exceeds 128 MiB limit".into());
        }
    }
    for normal in &mesh.normals {
        writeln!(&mut text, "vn {} {} {}", normal[0], normal[1], normal[2])
            .map_err(|error| error.to_string())?;
        if text.len() > MAX_OBJ_BYTES {
            return Err("OBJ exceeds 128 MiB limit".into());
        }
    }
    for first in (1..=mesh.positions.len()).step_by(3) {
        writeln!(
            &mut text,
            "f {0}//{0} {1}//{1} {2}//{2}",
            first,
            first + 1,
            first + 2
        )
        .map_err(|error| error.to_string())?;
        if text.len() > MAX_OBJ_BYTES {
            return Err("OBJ exceeds 128 MiB limit".into());
        }
    }
    let byte_len = text.len();
    atomic_write(path, text.as_bytes())?;
    Ok(byte_len)
}

fn validate_document(v: &Value) -> Result<(), String> {
    let o = v.as_object().ok_or(".aimodel root must be a JSON object")?;
    if let Some(features) = o.get("features") {
        let rows = features
            .as_array()
            .ok_or(".aimodel features must be an array")?;
        for (i, f) in rows.iter().enumerate() {
            if !f.is_object() {
                return Err(format!("feature {i} must be an object"));
            }
            if f.get("params").is_some_and(|x| !x.is_object()) {
                return Err(format!("feature {i} params must be an object"));
            }
        }
    }
    if o.get("name").is_some_and(|x| !x.is_string()) {
        return Err(".aimodel name must be a string".into());
    }
    Ok(())
}
fn normalize_feature(value: &Value) -> Result<Value, String> {
    let mut feature = value
        .as_object()
        .cloned()
        .ok_or("legacy feature must be an object")?;
    let kind = feature
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or("box")
        .to_owned();
    feature.insert("kind".into(), json!(kind));
    if !feature.contains_key("params") {
        feature.insert("params".into(), json!({}));
    }
    if !feature.contains_key("op") {
        feature.insert("op".into(), json!("add"));
    }
    if !feature
        .get("name")
        .is_some_and(|v| v.as_str().is_some_and(|s| !s.is_empty()))
    {
        feature.insert("name".into(), json!(kind_label(&kind)));
    }
    if !feature.get("note").is_some_and(Value::is_string) {
        feature.insert("note".into(), json!(""));
    }
    if !feature.contains_key("feature_id") {
        feature.insert("feature_id".into(), Value::Null);
    }
    if !feature.get("relations").is_some_and(Value::is_array) {
        feature.insert("relations".into(), json!([]));
    }
    if !feature.get("enabled").is_some_and(Value::is_boolean) {
        feature.insert("enabled".into(), json!(true));
    }
    if !feature.get("provenance").is_some_and(Value::is_object) {
        feature.insert("provenance".into(), json!({}));
    }
    validate_document(&Value::Object(feature.clone()))?;
    Ok(Value::Object(feature))
}
pub(crate) fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    if !parent.is_dir() {
        return Err("output parent directory must already exist".into());
    }
    let file = path
        .file_name()
        .ok_or("output path needs a filename")?
        .to_string_lossy();
    let id = TEMP_ID.fetch_add(1, Ordering::Relaxed);
    let temp = parent.join(format!(".{file}.native-{}-{id}.tmp", std::process::id()));
    let result = (|| {
        let mut f = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)
            .map_err(|e| format!("cannot create temporary output: {e}"))?;
        f.write_all(bytes)
            .map_err(|e| format!("cannot write temporary output: {e}"))?;
        f.sync_all()
            .map_err(|e| format!("cannot sync temporary output: {e}"))?;
        drop(f);
        fs::rename(&temp, path).map_err(|e| format!("atomic output rename failed: {e}"))
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result
}
fn scale_rotate(v: &Value, factor: f64) -> Result<Value, String> {
    if let Some(a) = v.as_array() {
        if a.len() == 2 && a[0].is_string() {
            return Ok(json!([a[0], scaled_number(&a[1], factor)?]));
        }
    }
    Ok(v.clone())
}
fn scale_value(v: &Value, factor: f64) -> Result<Value, String> {
    match v {
        Value::Number(n) => scaled_number(&Value::Number(n.clone()), factor),
        Value::Array(a) => Ok(Value::Array(
            a.iter()
                .map(|x| scale_value(x, factor))
                .collect::<Result<_, _>>()?,
        )),
        _ => Ok(v.clone()),
    }
}
fn scaled_number(v: &Value, f: f64) -> Result<Value, String> {
    if let Some(n) = v.as_f64() {
        let x = n * f;
        if !x.is_finite() {
            return Err("unit conversion produced a non-finite value".into());
        }
        Number::from_f64(x)
            .map(Value::Number)
            .ok_or("invalid converted number".into())
    } else {
        Ok(v.clone())
    }
}
fn length_key(k: &str) -> bool {
    matches!(
        k,
        "length"
            | "width"
            | "height"
            | "radius"
            | "radius2"
            | "corner"
            | "wire_r"
            | "sec_a"
            | "sec_b"
            | "base"
            | "margin"
            | "diameter"
            | "depth"
            | "pcd"
            | "outer_r"
            | "rim_w"
            | "hub_r"
            | "pitch_d"
            | "bore_d"
            | "thread_r"
            | "height2"
            | "pin_d"
            | "stroke"
            | "main_d"
            | "journal_d"
            | "base_d"
            | "lift"
            | "hub_d"
            | "tip_d"
            | "big_d"
            | "small_d"
            | "big_bore"
            | "small_bore"
            | "shank_w"
            | "thick"
            | "span"
            | "shank_d"
            | "thread_pitch"
            | "head_d"
            | "head_h"
            | "hex_s"
            | "hex_t"
            | "root_chord"
            | "tip_chord"
            | "sweep"
            | "groove_depth"
            | "outer_d"
            | "wall_t"
            | "thickness"
            | "module"
            | "spoke_w"
            | "blade_t"
            | "x"
            | "y"
            | "z"
    )
}
fn length_array_key(k: &str) -> bool {
    matches!(
        k,
        "center" | "center2" | "translate" | "step" | "point" | "entry_point" | "profile"
    )
}
fn angle_key(k: &str) -> bool {
    matches!(
        k,
        "angle" | "phase" | "twist" | "twist_deg" | "helix_deg" | "cone_deg" | "lobe_deg"
    )
}
fn kind_label(k: &str) -> &str {
    match k {
        "box" => "长方体",
        "cylinder" => "圆柱/圆台",
        "socket_head_bolt" => "内六角圆柱头螺钉",
        "sphere" => "球体",
        "prism" => "轮廓拉伸",
        "revolve" => "回转体",
        "gear" => "齿轮",
        "loft" => "放样体",
        "hole" => "孔",
        "pocket" => "凹槽",
        "pattern" => "阵列",
        "helix" => "螺旋体",
        "spoked_wheel" => "辐条轮",
        "vbelt_pulley" => "V带轮",
        "timing_pulley" => "同步带轮",
        "worm" => "蜗杆",
        "helical_gear" => "斜齿轮",
        "bevel_gear" => "锥齿轮",
        "crankshaft" => "曲轴",
        "camshaft" => "凸轮轴",
        "impeller" => "叶轮",
        "piston" => "活塞",
        "conrod" => "连杆",
        "cross_joint" => "万向节",
        "scroll_disc" => "涡旋盘",
        "rotor" => "三角转子",
        "shell" => "抽壳",
        _ => k,
    }
}
fn as_f32(p: [f64; 3]) -> Result<[f32; 3], String> {
    let out = p.map(|v| v as f32);
    if out.iter().any(|v| !v.is_finite()) {
        Err("mesh coordinate cannot be represented as STL float32".into())
    } else {
        Ok(out)
    }
}
fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
fn cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cadplan_document_conversion_preserves_plan_sources_and_unit_semantics() {
        let plan = json!({"schema_version":"1.0","name":"cm part","source_prompt":"test","units":{"length":"cm","angle":"rad"},"coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},"features":[{"id":"body","kind":"box","operation":"add","enabled":false,"parameters":{"length":2,"width":3,"height":4,"center":[1,2,3]},"sources":{"length":{"type":"user","evidence":"two cm"},"width":{"type":"user","evidence":"three cm"},"height":{"type":"user","evidence":"four cm"},"center":{"type":"derived","evidence":"origin"}}}]});
        let project = cadplan_to_project(&plan).unwrap();
        assert_eq!(project["features"][0]["params"]["length"], 20.0);
        assert_eq!(project["features"][0]["params"]["center"][2], 30.0);
        assert_eq!(project["features"][0]["params"]["base"], false);
        assert_eq!(project["features"][0]["enabled"], false);
        assert_eq!(project["features"][0]["provenance"]["length"], "user");
        assert_eq!(project["cadplan"], plan);
        assert_eq!(
            project["feature_manifest"]["body"]["sources"]["length"]["evidence"],
            "two cm"
        );
    }
    #[test]
    fn binary_stl_header_bounds_and_volume_are_well_formed() {
        let positions = vec![
            [0., 0., 0.],
            [1., 0., 0.],
            [0., 1., 0.],
            [0., 0., 0.],
            [0., 1., 0.],
            [0., 0., 1.],
            [0., 0., 0.],
            [0., 0., 1.],
            [1., 0., 0.],
            [1., 0., 0.],
            [0., 1., 0.],
            [0., 0., 1.],
        ];
        let mesh = TriangleMesh {
            normals: vec![[0., 0., 1.]; positions.len()],
            positions,
        };
        let path = std::env::temp_dir().join(format!(
            "aimodeler-native-stl-{}.stl",
            TEMP_ID.fetch_add(1, Ordering::Relaxed)
        ));
        let stats = write_binary_stl(&path, &mesh, "tetra").unwrap();
        let b = fs::read(&path).unwrap();
        assert_eq!(b.len(), 84 + 4 * 50);
        assert_eq!(u32::from_le_bytes(b[80..84].try_into().unwrap()), 4);
        assert_eq!(stats.bounds, [[0., 1.], [0., 1.], [0., 1.]]);
        assert!((stats.signed_volume - 1.0 / 6.0).abs() < 1e-12);
        let _ = fs::remove_file(path);
    }
    #[test]
    fn wavefront_obj_keeps_mm_coordinates_normals_and_triangle_faces() {
        let positions = vec![[0., 0., 0.], [1.25, 0., 0.], [0., 2., 0.]];
        let mesh = TriangleMesh {
            normals: vec![[0., 0., 1.]; positions.len()],
            positions,
        };
        let path = std::env::temp_dir().join(format!(
            "aimodeler-native-{}.obj",
            TEMP_ID.fetch_add(1, Ordering::Relaxed)
        ));
        let byte_len = write_wavefront_obj(&path, &mesh).unwrap();
        let text = fs::read_to_string(&path).unwrap();
        assert_eq!(byte_len, text.len());
        assert!(text.starts_with("# AI Modeler native mesh; units: mm\no AI_Modeler\n"));
        assert!(text.contains("v 1.25 0 0\n"));
        assert_eq!(
            text.lines().filter(|line| line.starts_with("v ")).count(),
            3
        );
        assert_eq!(
            text.lines().filter(|line| line.starts_with("vn ")).count(),
            3
        );
        assert_eq!(
            text.lines()
                .filter(|line| line.starts_with("f "))
                .collect::<Vec<_>>(),
            ["f 1//1 2//2 3//3"]
        );
        let _ = fs::remove_file(path);
    }
    #[test]
    fn invalid_obj_mesh_does_not_replace_existing_output() {
        let path = std::env::temp_dir().join(format!(
            "aimodeler-native-invalid-{}.obj",
            TEMP_ID.fetch_add(1, Ordering::Relaxed)
        ));
        fs::write(&path, b"keep").unwrap();
        let bad = TriangleMesh {
            positions: vec![[f64::INFINITY, 0., 0.]; 3],
            normals: vec![[0., 0., 1.]; 3],
        };
        assert!(write_wavefront_obj(&path, &bad).is_err());
        assert_eq!(fs::read(&path).unwrap(), b"keep");
        let _ = fs::remove_file(path);
    }
    #[test]
    fn invalid_project_and_nonfinite_stl_fail_before_destination_changes() {
        assert!(cadplan_to_project(&json!({"schema_version":"wrong"})).is_err());
        let path = std::env::temp_dir().join(format!(
            "aimodeler-native-invalid-{}.stl",
            TEMP_ID.fetch_add(1, Ordering::Relaxed)
        ));
        fs::write(&path, b"keep").unwrap();
        let bad = TriangleMesh {
            positions: vec![[f64::NAN, 0., 0.]; 3],
            normals: vec![[0., 0., 1.]; 3],
        };
        assert!(write_binary_stl(&path, &bad, "bad").is_err());
        assert_eq!(fs::read(&path).unwrap(), b"keep");
        let _ = fs::remove_file(path);
    }

    #[test]
    fn aimodel_roundtrip_keeps_legacy_defaults_and_unknown_cadplan_metadata() {
        let path = std::env::temp_dir().join(format!(
            "aimodeler-native-project-{}.aimodel",
            TEMP_ID.fetch_add(1, Ordering::Relaxed)
        ));
        let project = json!({"format":"ai-modeler","version":1,"name":"legacy","source_prompt":"goal",
            "features":[{"kind":"box","params":{"length":10,"width":5,"height":2,"base":false},"op":"add","name":"part","note":"","feature_id":"body","relations":[],"enabled":false,"provenance":{"length":"user"}}],
            "cadplan_schema_version":"1.0","cadplan":{"schema_version":"1.0","future_extension":{"keep":true}}});
        save_aimodel(&path, &project).unwrap();
        let loaded = load_aimodel(&path).unwrap();
        assert_eq!(loaded, project);
        let invalid = json!({"name":"bad","features":null});
        assert!(save_aimodel(&path, &invalid).is_err());
        assert_eq!(load_aimodel(&path).unwrap(), project);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn document_loader_applies_legacy_feature_defaults_without_dropping_plan() {
        let path = std::env::temp_dir().join(format!(
            "aimodeler-native-defaults-{}.aimodel",
            TEMP_ID.fetch_add(1, Ordering::Relaxed)
        ));
        let old = json!({"features":[{"params":{"radius":4}}],"cadplan":{"schema_version":"1.0"}});
        save_aimodel(&path, &old).unwrap();
        let doc = load_document(&path).unwrap();
        assert_eq!(doc["format"], "ai-modeler");
        assert_eq!(doc["version"], 1);
        assert_eq!(doc["name"], "Part1");
        assert_eq!(doc["features"][0]["kind"], "box");
        assert_eq!(doc["features"][0]["op"], "add");
        assert_eq!(doc["features"][0]["enabled"], true);
        assert_eq!(doc["features"][0]["provenance"], json!({}));
        assert_eq!(doc["cadplan"]["schema_version"], "1.0");
        let _ = fs::remove_file(path);
    }
}
