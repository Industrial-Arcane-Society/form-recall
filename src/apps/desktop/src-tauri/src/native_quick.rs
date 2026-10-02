//! Native Quick-mode parse, validate, OCCT build, and GLB document output.
use crate::{
    cad_plan_validation, native_model_io, native_occt::TriangleMesh, native_plan_geometry,
    native_quick_parser,
};
use serde_json::{Value, json};
use std::{
    fs,
    path::Path,
    sync::atomic::AtomicBool,
    time::{SystemTime, UNIX_EPOCH},
};

pub fn build(
    text: &str,
    revision: i64,
    manifest: &Path,
    session_dir: &Path,
) -> Result<Value, String> {
    if revision < 1 {
        return Err("Quick 文档 revision 必须是正整数。".into());
    }
    let parsed = native_quick_parser::parse(text);
    if parsed["status"] != "ready" {
        return Ok(
            json!({"state":"parse_failed","message":parsed["questions"].as_array().and_then(|q| q.first()).and_then(|v| v["message"].as_str()).unwrap_or("Quick 无法解析该描述，请补充明确尺寸。"),"warnings":parsed["warnings"],"questions":parsed["questions"],"unsupported":parsed["unsupported"]}),
        );
    }
    let plan = parsed["plan"].clone();
    let validation = cad_plan_validation::validate_plan(&plan);
    if validation["valid"] != true {
        return Err(format!(
            "Quick CADPlan 校验失败：{}",
            validation["errors"]
                .as_array()
                .map(|a| a
                    .iter()
                    .map(|e| e["message"].as_str().unwrap_or("invalid"))
                    .collect::<Vec<_>>()
                    .join("；"))
                .unwrap_or_default()
        ));
    }
    let cancelled = AtomicBool::new(false);
    let built = native_plan_geometry::build(&plan, manifest, &cancelled)?;
    let mesh = built.shape.tessellate(0.08, 0.35)?;
    if mesh.positions.is_empty() || mesh.positions.len() % 3 != 0 {
        return Err("OCCT 返回空或无效三角网格。".into());
    }
    fs::create_dir_all(session_dir).map_err(|e| format!("无法创建 Quick 输出目录：{e}"))?;
    let project = native_model_io::cadplan_to_project(&plan)?;
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let dir = session_dir
        .join("quick")
        .join(format!("r{revision}-{stamp}"));
    fs::create_dir_all(&dir).map_err(|e| format!("无法创建 Quick 模型目录：{e}"))?;
    let aimodel = dir.join("quick.aimodel");
    let stl = dir.join("quick.stl");
    let glb = dir.join("mesh.glb");
    native_model_io::save_aimodel(&aimodel, &project)?;
    native_model_io::write_binary_stl(&stl, &mesh, "native Quick")?;
    write_glb(&mesh, &glb)?;
    let mut document = project;
    if let Some(features) = document["features"].as_array_mut() {
        for (index, feature) in features.iter_mut().enumerate() {
            feature["index"] = json!(index);
            feature["description"] = json!(format!(
                "Quick {}",
                feature["kind"].as_str().unwrap_or("特征")
            ));
        }
    }
    document["revision"] = json!(revision);
    document["name"] = json!("Quick Model");
    document["source_prompt"] = json!(text);
    document["mesh_path"] = json!(glb);
    document["build_log"] = json!(["Quick parser → CADPlan 校验 → Rust/OCCT → GLB、AIMODEL、STL"]);
    document["warnings"] = parsed["warnings"].clone();
    document["output_paths"] = json!({"glb":glb,"aimodel":aimodel,"stl":stl});
    let mut report = built.report;
    report["triangle_count"] = json!(mesh.positions.len() / 3);
    report["state"] = json!("passed");
    document["stats"] = json!({"faces":0,"triangles":mesh.positions.len()/3,
        "volume":report["volume_mm3"],"size":report["dimensions_mm"]});
    document["geometry_report"] = report;
    document["state"] = json!("complete");
    Ok(document)
}

pub fn active_model_candidate(text: &str, document: &Value) -> Result<Value, String> {
    let plan = document
        .get("cadplan")
        .filter(|plan| plan.is_object())
        .cloned()
        .ok_or("Quick 结果缺少 CADPlan。")?;
    let revision = document["revision"]
        .as_i64()
        .ok_or("Quick revision 无效。")?;
    let turn_id = format!("quick-turn-{revision}");
    let mut provenance = Vec::new();
    for feature in plan["features"].as_array().into_iter().flatten() {
        for (parameter, source) in feature["sources"].as_object().into_iter().flatten() {
            if source["type"] == "user"
                && let Some(value) = feature["parameters"].get(parameter)
            {
                provenance.push(json!({"feature_id":feature["id"],"parameter":parameter,
                    "turn_id":turn_id,"value":value,"source":source}));
            }
        }
    }
    let state = json!({"plan":plan,"revision":revision,"preview_revision":revision,
        "preview_generation":1,"output_paths":document["output_paths"],"finish_summary":null,
        "last_preview":{"revision":revision,"geometry_fingerprint":crate::cad_plan::geometry_fingerprint(&document["cadplan"])? ,
            "preview_generation":1,"execution_engine":"rust_c_abi_occt","local_geometry_checks":document["geometry_report"]},
        "actions":[{"kind":"model","tool":"quick_build","revision":revision,"execution_engine":"rust",
            "user_source_provenance":provenance}],"geometry_signatures":[],"previews":[]});
    let references = json!({"schema_version":1,"session_id":"quick","images":[]});
    let turns = json!({"schema_version":1,"session_id":"quick","turns":[
        {"turn_id":turn_id,"raw_user_prompt":text,"reference_ids":[]} ]});
    let mut candidate =
        crate::native_active_model::candidate_from_cad_state("quick", &state, &references, &turns)?;
    candidate["document"]["warnings"] = document
        .get("warnings")
        .cloned()
        .unwrap_or_else(|| json!([]));
    candidate["document"]["geometry_report"] = document
        .get("geometry_report")
        .cloned()
        .unwrap_or(Value::Null);
    candidate["document"]["state"] = json!("complete");
    Ok(candidate)
}

pub(crate) fn write_glb(mesh: &TriangleMesh, path: &Path) -> Result<(), String> {
    use serde_json::json;
    if mesh.positions.is_empty()
        || mesh.positions.len() % 3 != 0
        || mesh.positions.len() != mesh.normals.len()
    {
        return Err("OCCT mesh 顶点/法线数量无效。".into());
    }
    if mesh
        .positions
        .iter()
        .chain(mesh.normals.iter())
        .flatten()
        .any(|v| !v.is_finite() || (*v as f32).is_infinite())
    {
        return Err("OCCT mesh 含非有限或超出 GLB float32 范围的坐标。".into());
    }
    let mut binary = Vec::with_capacity(mesh.positions.len() * 24);
    for p in &mesh.positions {
        for v in p {
            binary.extend_from_slice(&(*v as f32).to_le_bytes());
        }
    }
    let position_bytes = binary.len();
    for p in &mesh.normals {
        for v in p {
            binary.extend_from_slice(&(*v as f32).to_le_bytes());
        }
    }
    let normal_bytes = binary.len().saturating_sub(position_bytes);
    if position_bytes == 0 || normal_bytes != position_bytes {
        return Err("OCCT mesh 的顶点法线数量不匹配。".into());
    }
    let mins = [0, 1, 2].map(|i| {
        mesh.positions
            .iter()
            .map(|p| p[i])
            .fold(f64::INFINITY, f64::min)
    });
    let maxs = [0, 1, 2].map(|i| {
        mesh.positions
            .iter()
            .map(|p| p[i])
            .fold(f64::NEG_INFINITY, f64::max)
    });
    let doc = json!({"asset":{"version":"2.0","generator":"AI Modeler native"},"scene":0,"scenes":[{"nodes":[0]}],"nodes":[{"mesh":0}],"meshes":[{"primitives":[{"attributes":{"POSITION":0,"NORMAL":1},"mode":4}]}],"buffers":[{"byteLength":binary.len()}],"bufferViews":[{"buffer":0,"byteOffset":0,"byteLength":position_bytes,"target":34962},{"buffer":0,"byteOffset":position_bytes,"byteLength":normal_bytes,"target":34962}],"accessors":[{"bufferView":0,"componentType":5126,"count":mesh.positions.len(),"type":"VEC3","min":mins,"max":maxs},{"bufferView":1,"componentType":5126,"count":mesh.normals.len(),"type":"VEC3"}]});
    let mut json_bytes = serde_json::to_vec(&doc).map_err(|e| e.to_string())?;
    while json_bytes.len() % 4 != 0 {
        json_bytes.push(b' ');
    }
    while binary.len() % 4 != 0 {
        binary.push(0);
    }
    let total = 12usize
        .checked_add(8 + json_bytes.len())
        .and_then(|n| n.checked_add(8 + binary.len()))
        .ok_or("GLB 文件过大。")?;
    if total > u32::MAX as usize {
        return Err("GLB 超过 4 GiB 限制。".into());
    }
    let mut out = Vec::with_capacity(total);
    out.extend_from_slice(b"glTF");
    out.extend_from_slice(&2u32.to_le_bytes());
    out.extend_from_slice(&(total as u32).to_le_bytes());
    out.extend_from_slice(&(json_bytes.len() as u32).to_le_bytes());
    out.extend_from_slice(&0x4E4F534Au32.to_le_bytes());
    out.extend_from_slice(&json_bytes);
    out.extend_from_slice(&(binary.len() as u32).to_le_bytes());
    out.extend_from_slice(&0x004E4942u32.to_le_bytes());
    out.extend_from_slice(&binary);
    native_model_io::atomic_write(path, &out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn missing_explicit_dimensions_returns_clarification_without_kernel() {
        let parsed = native_quick_parser::parse("做一个长方体");
        assert_eq!(parsed["status"], "needs_clarification");
        assert!(
            parsed["questions"]
                .as_array()
                .is_some_and(|q| !q.is_empty())
        );
    }

    #[test]
    fn writes_glb_2_binary_container() {
        let root = std::env::temp_dir().join(format!(
            "quick-glb-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("mesh.glb");
        let mesh = TriangleMesh {
            positions: vec![[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]],
            normals: vec![[0., 0., 1.]; 3],
        };
        write_glb(&mesh, &path).unwrap();
        let bytes = fs::read(&path).unwrap();
        assert_eq!(&bytes[..4], b"glTF");
        assert_eq!(u32::from_le_bytes(bytes[4..8].try_into().unwrap()), 2);
        assert_eq!(
            u32::from_le_bytes(bytes[8..12].try_into().unwrap()) as usize,
            bytes.len()
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn builds_explicit_box_through_local_occt_when_runtime_is_staged() {
        let manifest =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("target/f4-occt/runtime-manifest.json");
        if !manifest.is_file() {
            return;
        }
        let root = std::env::temp_dir().join(format!(
            "quick-build-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let result = build("创建 100×60×20毫米 长方体", 7, &manifest, &root).unwrap();
        assert_eq!(result["revision"], 7);
        assert_eq!(result["features"].as_array().unwrap().len(), 1);
        assert!(
            result["mesh_path"]
                .as_str()
                .is_some_and(|p| Path::new(p).is_file())
        );
        assert!(
            result["output_paths"]["aimodel"]
                .as_str()
                .is_some_and(|p| Path::new(p).is_file())
        );
        assert!(
            result["output_paths"]["stl"]
                .as_str()
                .is_some_and(|p| Path::new(p).is_file())
        );
        assert!(result["stats"]["triangles"].as_u64().unwrap() > 0);
        fs::remove_dir_all(root).unwrap();
    }
}
