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
    if built.report["state"] != "built" {
        return Err("Quick 导出要求完整 build；部分几何仅可用于只读诊断预览。".into());
    }
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
    let obj = dir.join("quick.obj");
    let glb = dir.join("mesh.glb");
    native_model_io::save_aimodel(&aimodel, &project)?;
    native_model_io::write_binary_stl(&stl, &mesh, "native Quick")?;
    native_model_io::write_wavefront_obj(&obj, &mesh)?;
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
    document["build_log"] = json!(["Quick parser → CADPlan 校验 → Rust/OCCT → GLB、AIMODEL、STL、OBJ"]);
    document["warnings"] = parsed["warnings"].clone();
    document["output_paths"] = json!({"glb":glb,"aimodel":aimodel,"stl":stl,"obj":obj});
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
        assert!(
            result["output_paths"]["obj"]
                .as_str()
                .is_some_and(|p| Path::new(p).is_file())
        );
        assert!(result["stats"]["triangles"].as_u64().unwrap() > 0);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    #[ignore = "requires an explicitly staged OCCT runtime; run the documented F5.12 acceptance command"]
    fn f5_12_quick_geometry_acceptance_builds_supported_hole_and_pattern_cases() {
        use serde_json::Value;

        let manifest = std::env::var_os("AIMODELER_TEST_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("F5.12 requires AIMODELER_TEST_OCCT_MANIFEST; use the documented acceptance command");
        assert!(
            manifest.is_file(),
            "F5.12 OCCT runtime manifest does not exist: {}",
            manifest.display()
        );

        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_geometry_acceptance.json"
        ))
        .expect("F5.12 acceptance fixture should be valid JSON");
        let root = std::env::temp_dir().join(format!(
            "quick-f5-12-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        let cases = fixture["cases"].as_array().expect("cases array");
        assert_eq!(cases.len(), 5, "F5.12 must cover all five fixed scenarios");
        let scenario_ids = cases
            .iter()
            .map(|case| case["id"].as_str().expect("scenario id"))
            .collect::<Vec<_>>();
        assert_eq!(
            scenario_ids,
            [
                "corner-holes",
                "pcd-holes",
                "linear-feature-pattern",
                "circular-feature-pattern",
                "center-counterbore",
            ],
            "F5.12 fixture must retain all five named acceptance scenarios"
        );
        let mut build_failures = Vec::new();

        for case in cases {
            let id = case["id"].as_str().expect("case id");
            let prompt = case["input"].as_str().expect("fixed Quick prompt");
            let parsed = native_quick_parser::parse(prompt);
            assert_eq!(parsed["status"], "ready", "{id}: parser result {parsed}");

            let plan_features = parsed["plan"]["features"]
                .as_array()
                .unwrap_or_else(|| panic!("{id}: parsed CADPlan features missing: {parsed}"));
            let expected_kinds = case["expected"]["feature_kinds"]
                .as_array()
                .expect("expected feature kinds");
            assert_eq!(plan_features.len(), expected_kinds.len(), "{id}: parsed plan features");
            for (feature, expected_kind) in plan_features.iter().zip(expected_kinds) {
                assert_eq!(feature["kind"], *expected_kind, "{id}: parsed feature kind");
            }
            let expected_dimensions = case["expected"]["dimensions_mm"]
                .as_array()
                .expect("expected bbox dimensions");
            assert_eq!(expected_dimensions.len(), 3, "{id}: expected bbox dimensions");
            assert!(
                expected_dimensions
                    .iter()
                    .all(|value| value.as_f64().is_some_and(|value| value.is_finite() && value > 0.0)),
                "{id}: fixture bbox dimensions must be finite and positive"
            );
            let expected_holes = case["expected"]["hole_count"]
                .as_u64()
                .unwrap_or_else(|| panic!("{id}: expected hole_count missing or invalid"));

            let document = match build(prompt, 1, &manifest, &root.join(id)) {
                Ok(document) => document,
                Err(error) => {
                    eprintln!("F5.12 BLOCKED {id}: {error}; parsed_features={}, expected_hole_instances={expected_holes}", plan_features.len());
                    build_failures.push(format!("{id}: {error}"));
                    continue;
                }
            };
            assert_eq!(document["state"], "complete", "{id}");

            let plan_features = document["cadplan"]["features"]
                .as_array()
                .unwrap_or_else(|| panic!("{id}: CADPlan features missing"));
            assert_eq!(plan_features.len(), expected_kinds.len(), "{id}: plan features");
            for (feature, expected_kind) in plan_features.iter().zip(expected_kinds) {
                assert_eq!(feature["kind"], *expected_kind, "{id}: feature kind");
            }

            let report = &document["geometry_report"];
            assert_eq!(report["state"], "passed", "{id}: native geometry report");
            assert_eq!(report["valid"], true, "{id}: OCCT valid geometry");
            assert_eq!(report["closed_solid_count"], 1, "{id}: closed solid count");
            let feature_results = report["feature_results"]
                .as_array()
                .unwrap_or_else(|| panic!("{id}: feature results missing"));
            assert_eq!(feature_results.len(), expected_kinds.len(), "{id}: built feature count");
            assert!(
                feature_results.iter().all(|row| row["state"] == "built"),
                "{id}: a requested feature was a no-op"
            );

            let holes = report["hole_instances"]
                .as_array()
                .unwrap_or_else(|| panic!("{id}: hole instances missing"));
            assert_eq!(
                holes.len(),
                expected_holes as usize,
                "{id}: hole instance count"
            );
            if expected_holes > 0 {
                assert_eq!(report["hole_measurement_complete"], true, "{id}: every hole needs final measurement");
                assert_eq!(report["hole_measurements_verified"], true, "{id}: final hole metrology gate");
            }
            let dimensions = report["dimensions_mm"]
                .as_array()
                .unwrap_or_else(|| panic!("{id}: bbox dimensions missing"));
            assert_eq!(dimensions.len(), 3, "{id}: bbox dimension arity");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().expect("finite bbox dimension");
                let expected = expected.as_f64().expect("expected bbox dimension");
                assert!(actual.is_finite(), "{id}: bbox axis {axis} is not finite");
                assert!((actual - expected).abs() <= 1e-4, "{id}: bbox axis {axis}: {actual} != {expected}");
            }
            let volume = report["volume_mm3"].as_f64().expect("volume");
            assert!(volume.is_finite() && volume > 0.0, "{id}: volume must be finite and positive");

            let aimodel = Path::new(document["output_paths"]["aimodel"].as_str().expect("aimodel path"));
            let stl = Path::new(document["output_paths"]["stl"].as_str().expect("STL path"));
            assert!(aimodel.is_file(), "{id}: .aimodel was not exported");
            assert!(stl.is_file(), "{id}: STL was not exported");
            let loaded = native_model_io::load_aimodel(aimodel)
                .unwrap_or_else(|error| panic!("{id}: .aimodel round-trip failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], document["cadplan"]["features"], "{id}: saved plan");
            let stl_bytes = fs::read(stl).unwrap_or_else(|error| panic!("{id}: STL read failed: {error}"));
            assert!(stl_bytes.len() >= 84, "{id}: STL header is truncated");
            let triangle_count = u32::from_le_bytes(stl_bytes[80..84].try_into().unwrap()) as usize;
            assert!(triangle_count > 0, "{id}: STL is empty");
            assert_eq!(stl_bytes.len(), 84 + triangle_count * 50, "{id}: STL length does not match triangle count");
            eprintln!("F5.12 PASS {id}: features={}, hole_instances={}, bbox_mm={:?}, volume_mm3={volume}", plan_features.len(), holes.len(), dimensions);
        }

        // A separate non-fixture control proves ordinary blind holes still
        // use the original centerline material check without a pilot pair.
        let ordinary_blind = "长方体 长100宽80高20，中心盲孔直径10mm深5mm";
        let control = build(ordinary_blind, 1, &manifest, &root.join("ordinary-blind-control"))
            .unwrap_or_else(|error| panic!("ordinary-blind control build failed: {error}"));
        let control_report = &control["geometry_report"];
        assert_eq!(control_report["state"], "passed");
        assert_eq!(control_report["valid"], true);
        assert_eq!(control_report["hole_instances"].as_array().map(Vec::len), Some(1));
        assert_eq!(control_report["hole_instances"][0]["through"], false);
        assert_eq!(control_report["hole_instances"][0]["status"], "verified_blind");
        assert_eq!(control_report["hole_measurements_verified"], true);
        eprintln!("F5.12 CONTROL ordinary-blind: original centerline route and final B-Rep depth verification passed");

        assert!(build_failures.is_empty(), "F5.12 OCCT cases did not build: {}", build_failures.join(" | "));
    }

    #[test]
    #[ignore = "requires an explicitly staged OCCT runtime; run the documented F5.14 acceptance command"]
    fn f5_14_blind_hole_depth_continuation_builds_verified_geometry() {
        let manifest = std::env::var_os("AIMODELER_TEST_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("F5.14 requires AIMODELER_TEST_OCCT_MANIFEST");
        assert!(
            manifest.is_file(),
            "F5.14 OCCT runtime manifest is missing: {}",
            manifest.display()
        );

        let prompt = "长方体 长100宽80高20，中心盲孔直径10，10mm深";
        let parsed = native_quick_parser::parse(prompt);
        assert_eq!(parsed["status"], "ready", "F5.14 parser result: {parsed}");
        assert_eq!(parsed["plan"]["features"][1]["parameters"]["depth"], 10.0);
        assert_eq!(parsed["plan"]["features"][1]["parameters"]["through"], false);

        let root = std::env::temp_dir().join(format!(
            "quick-f5-14-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        let result = build(prompt, 1, &manifest, &root)
            .unwrap_or_else(|error| panic!("F5.14 Quick build failed: {error}"));
        assert_eq!(result["state"], "complete");
        let report = &result["geometry_report"];
        assert_eq!(report["state"], "passed");
        assert_eq!(report["valid"], true);
        assert_eq!(report["closed_solid_count"], 1);
        assert_eq!(report["dimensions_mm"], json!([100.0, 80.0, 20.0]));
        assert_eq!(report["hole_measurements_verified"], true);
        let holes = report["hole_instances"].as_array().expect("hole instances");
        assert_eq!(holes.len(), 1);
        assert_eq!(holes[0]["through"], false);
        assert_eq!(holes[0]["depth_mm"], 10.0);
        assert_eq!(holes[0]["status"], "verified_blind");
        assert_eq!(holes[0]["final_measurement"]["accepted"], true);
        assert_eq!(holes[0]["final_measurement"]["expected_blind_depth"], 10.0);
        assert_eq!(
            holes[0]["final_measurement"]["measured_cylindrical_axial_length"],
            10.0
        );

        let aimodel = Path::new(
            result["output_paths"]["aimodel"]
                .as_str()
                .expect("AIMODEL path"),
        );
        let stl = Path::new(result["output_paths"]["stl"].as_str().expect("STL path"));
        assert!(aimodel.is_file(), "F5.14 AIMODEL export missing");
        assert!(stl.is_file(), "F5.14 STL export missing");
        let loaded = native_model_io::load_aimodel(aimodel)
            .unwrap_or_else(|error| panic!("F5.14 AIMODEL round-trip failed: {error}"));
        assert_eq!(loaded["cadplan"]["features"], result["cadplan"]["features"]);
        eprintln!("F5.14 PASS: centered blind hole depth continuation, B-Rep measurement, AIMODEL/STL export and reload");
    }

    #[test]
    #[ignore = "requires an explicitly staged OCCT runtime; run the documented F5.15 acceptance command"]
    fn f5_15_multiple_slots_build_legacy_centered_pitch_geometry() {
        use serde_json::Value;

        let manifest = std::env::var_os("AIMODELER_TEST_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("F5.15 requires AIMODELER_TEST_OCCT_MANIFEST");
        assert!(
            manifest.is_file(),
            "F5.15 OCCT runtime manifest is missing: {}",
            manifest.display()
        );
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_multiple_slot_contract.json"
        ))
        .expect("F5.15 acceptance fixture should be valid JSON");
        let case = &fixture["legacy_accepted"][0];
        let prompt = case["input"].as_str().expect("fixed Quick prompt");
        let parsed = native_quick_parser::parse(prompt);
        assert_eq!(parsed["status"], "ready", "F5.15 parser result: {parsed}");
        assert_eq!(parsed["plan"]["features"].as_array().map(Vec::len), Some(3));

        let root = std::env::temp_dir().join(format!(
            "quick-f5-15-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        let result = build(prompt, 1, &manifest, &root)
            .unwrap_or_else(|error| panic!("F5.15 Quick build failed: {error}"));
        assert_eq!(result["state"], "complete");
        assert_eq!(result["cadplan"]["features"].as_array().map(Vec::len), Some(3));
        let report = &result["geometry_report"];
        assert_eq!(report["state"], "passed");
        assert_eq!(report["valid"], true);
        assert_eq!(report["closed_solid_count"], 1);
        assert_eq!(report["dimensions_mm"], case["dimensions_mm"]);
        let expected_volume = case["volume_mm3"].as_f64().expect("expected volume");
        let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
        assert!((actual_volume - expected_volume).abs() <= 1e-4);
        let feature_results = report["feature_results"].as_array().expect("feature results");
        assert_eq!(feature_results.len(), 3);
        assert!(feature_results.iter().all(|feature| feature["state"] == "built"));
        for expected in case["slots"].as_array().unwrap() {
            let index = expected["feature_index"].as_u64().unwrap() as usize;
            let actual = &result["cadplan"]["features"][index];
            for (key, value) in expected["params"].as_object().unwrap() {
                assert_eq!(actual["parameters"][key], *value, "F5.15 slot {index} {key}");
            }
        }

        let aimodel = Path::new(result["output_paths"]["aimodel"].as_str().expect("AIMODEL path"));
        let stl = Path::new(result["output_paths"]["stl"].as_str().expect("STL path"));
        assert!(aimodel.is_file(), "F5.15 AIMODEL export missing");
        assert!(stl.is_file(), "F5.15 STL export missing");
        let loaded = native_model_io::load_aimodel(aimodel)
            .unwrap_or_else(|error| panic!("F5.15 AIMODEL round-trip failed: {error}"));
        assert_eq!(loaded["cadplan"]["features"], result["cadplan"]["features"]);
        eprintln!("F5.15 PASS: two legacy-positioned rectangular slots, valid B-Rep, volume={actual_volume} mm³, AIMODEL/STL export and reload");
    }

    #[test]
    #[ignore = "requires an explicitly staged OCCT runtime; run the documented F5.16 acceptance command"]
    fn f5_16_standalone_legacy_box_defaults_build_and_export() {
        use serde_json::Value;

        let manifest = std::env::var_os("AIMODELER_TEST_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("F5.16 requires AIMODELER_TEST_OCCT_MANIFEST");
        assert!(
            manifest.is_file(),
            "F5.16 OCCT runtime manifest is missing: {}",
            manifest.display()
        );
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_box_defaults_contract.json"
        ))
        .expect("F5.16 acceptance fixture should be valid JSON");
        let root = std::env::temp_dir().join(format!(
            "quick-f5-16-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["legacy_accepted"]
            .as_array()
            .expect("legacy box cases")
            .iter()
            .enumerate()
        {
            let prompt = case["input"].as_str().expect("fixed Quick prompt");
            let result = build(prompt, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("F5.16 Quick build failed for {prompt}: {error}"));
            assert_eq!(result["state"], "complete", "{prompt}");
            let report = &result["geometry_report"];
            assert_eq!(report["state"], "passed", "{prompt}");
            assert_eq!(report["valid"], true, "{prompt}");
            assert_eq!(report["closed_solid_count"], 1, "{prompt}");
            assert_eq!(report["dimensions_mm"], case["dimensions_mm"], "{prompt}");
            let expected_volume = case["volume_mm3"].as_f64().expect("expected volume");
            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            assert!(
                (actual_volume - expected_volume).abs() <= 1e-4,
                "{prompt}: {actual_volume}"
            );
            let feature = &result["cadplan"]["features"][0];
            assert_eq!(feature["kind"], "box", "{prompt}");
            for (key, expected) in ["length", "width", "height"]
                .into_iter()
                .zip(case["dimensions_mm"].as_array().unwrap())
            {
                assert_eq!(feature["parameters"][key], *expected, "{prompt}: {key}");
            }

            let aimodel = Path::new(
                result["output_paths"]["aimodel"]
                    .as_str()
                    .expect("AIMODEL path"),
            );
            let stl = Path::new(result["output_paths"]["stl"].as_str().expect("STL path"));
            assert!(aimodel.is_file(), "F5.16 AIMODEL export missing: {prompt}");
            assert!(stl.is_file(), "F5.16 STL export missing: {prompt}");
            let loaded = native_model_io::load_aimodel(aimodel)
                .unwrap_or_else(|error| panic!("F5.16 AIMODEL round-trip failed for {prompt}: {error}"));
            assert_eq!(loaded["cadplan"]["features"], result["cadplan"]["features"], "{prompt}");
            eprintln!("F5.16 PASS: {prompt} -> {:?} mm, volume={actual_volume} mm³", case["dimensions_mm"]);
        }
    }

    #[test]
    #[ignore = "requires an explicitly staged OCCT runtime; run the documented F5.17 acceptance command"]
    fn f5_17_legacy_slot_defaults_build_and_export() {
        use serde_json::Value;

        let manifest = std::env::var_os("AIMODELER_TEST_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("F5.17 requires AIMODELER_TEST_OCCT_MANIFEST");
        assert!(
            manifest.is_file(),
            "F5.17 OCCT runtime manifest is missing: {}",
            manifest.display()
        );
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_slot_defaults_contract.json"
        ))
        .expect("F5.17 acceptance fixture should be valid JSON");
        let root = std::env::temp_dir().join(format!(
            "quick-f5-17-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["legacy_accepted"]
            .as_array()
            .expect("legacy slot cases")
            .iter()
            .enumerate()
        {
            let prompt = case["input"].as_str().expect("fixed Quick prompt");
            let result = build(prompt, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("F5.17 Quick build failed for {prompt}: {error}"));
            assert_eq!(result["state"], "complete", "{prompt}");
            let report = &result["geometry_report"];
            assert_eq!(report["state"], "passed", "{prompt}");
            assert_eq!(report["valid"], true, "{prompt}");
            assert_eq!(report["closed_solid_count"], 1, "{prompt}");
            assert_eq!(report["dimensions_mm"], case["dimensions_mm"], "{prompt}");
            let expected_volume = case["volume_mm3"].as_f64().expect("expected volume");
            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            assert!((actual_volume - expected_volume).abs() <= 1e-4, "{prompt}: {actual_volume}");

            let expected_slots = case["slots"].as_array().expect("expected slots");
            let plan_features = result["cadplan"]["features"]
                .as_array()
                .expect("Quick CADPlan features");
            assert_eq!(plan_features.len(), expected_slots.len() + 1, "{prompt}");
            for expected in expected_slots {
                let feature_index = expected["feature_index"].as_u64().unwrap() as usize;
                let actual = &plan_features[feature_index];
                assert_eq!(actual["kind"], "pocket", "{prompt}: slot {feature_index}");
                for (key, value) in expected["params"].as_object().unwrap() {
                    assert_eq!(
                        actual["parameters"][key], *value,
                        "{prompt}: slot {feature_index} {key}"
                    );
                }
            }

            let aimodel = Path::new(
                result["output_paths"]["aimodel"]
                    .as_str()
                    .expect("AIMODEL path"),
            );
            let stl = Path::new(result["output_paths"]["stl"].as_str().expect("STL path"));
            assert!(aimodel.is_file(), "F5.17 AIMODEL export missing: {prompt}");
            assert!(stl.is_file(), "F5.17 STL export missing: {prompt}");
            let loaded = native_model_io::load_aimodel(aimodel)
                .unwrap_or_else(|error| {
                    panic!("F5.17 AIMODEL round-trip failed for {prompt}: {error}")
                });
            assert_eq!(
                loaded["cadplan"]["features"],
                result["cadplan"]["features"],
                "{prompt}"
            );
            eprintln!(
                "F5.17 PASS: {prompt} -> {} slot(s), volume={actual_volume} mm³",
                expected_slots.len()
            );
        }
    }

    #[test]
    #[ignore = "requires an explicitly staged OCCT runtime; run the documented F5.18 acceptance command"]
    fn f5_18_legacy_cylinder_cone_and_sphere_defaults_build_and_export() {
        use serde_json::Value;

        let manifest = std::env::var_os("AIMODELER_TEST_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("F5.18 requires AIMODELER_TEST_OCCT_MANIFEST");
        assert!(
            manifest.is_file(),
            "F5.18 OCCT runtime manifest is missing: {}",
            manifest.display()
        );
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_primitive_defaults_contract.json"
        ))
        .expect("F5.18 acceptance fixture should be valid JSON");
        let root = std::env::temp_dir().join(format!(
            "quick-f5-18-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["legacy_accepted"]
            .as_array()
            .expect("legacy primitive cases")
            .iter()
            .enumerate()
        {
            let prompt = case["input"].as_str().expect("fixed Quick prompt");
            let result = build(prompt, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("F5.18 Quick build failed for {prompt}: {error}"));
            assert_eq!(result["state"], "complete", "{prompt}");
            let report = &result["geometry_report"];
            assert_eq!(report["state"], "passed", "{prompt}");
            assert_eq!(report["valid"], true, "{prompt}");
            assert_eq!(report["closed_solid_count"], 1, "{prompt}");
            let actual_dimensions = report["dimensions_mm"]
                .as_array()
                .expect("measured bounding box");
            for (actual, expected) in actual_dimensions
                .iter()
                .zip(case["dimensions_mm"].as_array().unwrap())
            {
                let actual = actual.as_f64().expect("numeric measured dimension");
                let expected = expected.as_f64().expect("numeric expected dimension");
                assert!(
                    (actual - expected).abs() <= 1e-8,
                    "{prompt}: dimensions {actual_dimensions:?}"
                );
            }
            let expected_volume = case["volume_mm3"].as_f64().expect("expected volume");
            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            assert!(
                (actual_volume - expected_volume).abs() <= 1e-4,
                "{prompt}: {actual_volume}"
            );

            let plan_features = result["cadplan"]["features"]
                .as_array()
                .expect("Quick CADPlan features");
            assert_eq!(plan_features.len(), 1, "{prompt}");
            let feature = &plan_features[0];
            assert_eq!(feature["kind"], case["feature_kind"], "{prompt}");
            for (key, expected) in case["parameters"].as_object().unwrap() {
                assert_eq!(feature["parameters"][key], *expected, "{prompt}: {key}");
            }

            let aimodel = Path::new(
                result["output_paths"]["aimodel"]
                    .as_str()
                    .expect("AIMODEL path"),
            );
            let stl = Path::new(result["output_paths"]["stl"].as_str().expect("STL path"));
            assert!(aimodel.is_file(), "F5.18 AIMODEL export missing: {prompt}");
            assert!(stl.is_file(), "F5.18 STL export missing: {prompt}");
            let loaded = native_model_io::load_aimodel(aimodel).unwrap_or_else(|error| {
                panic!("F5.18 AIMODEL round-trip failed for {prompt}: {error}")
            });
            assert_eq!(
                loaded["cadplan"]["features"], result["cadplan"]["features"],
                "{prompt}"
            );
            eprintln!(
                "F5.18 PASS: {prompt} -> {:?} mm, volume={actual_volume} mm³",
                case["dimensions_mm"]
            );
        }
    }

    #[test]
    #[ignore = "requires an explicitly staged OCCT runtime; run the documented F5.19 acceptance command"]
    fn f5_19_legacy_extrusion_revolve_and_plain_cylinder_build_and_export() {
        use serde_json::Value;

        let manifest = std::env::var_os("AIMODELER_TEST_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("F5.19 requires AIMODELER_TEST_OCCT_MANIFEST");
        assert!(
            manifest.is_file(),
            "F5.19 OCCT runtime manifest is missing: {}",
            manifest.display()
        );
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_extrusion_revolve_contract.json"
        ))
        .expect("F5.19 acceptance fixture should be valid JSON");
        let root = std::env::temp_dir().join(format!(
            "quick-f5-19-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["legacy_accepted"]
            .as_array()
            .expect("legacy profile constructors")
            .iter()
            .enumerate()
        {
            let prompt = case["input"].as_str().expect("fixed Quick prompt");
            let result = build(prompt, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("F5.19 Quick build failed for {prompt}: {error}"));
            assert_eq!(result["state"], "complete", "{prompt}");
            let report = &result["geometry_report"];
            assert_eq!(report["state"], "passed", "{prompt}");
            assert_eq!(report["valid"], true, "{prompt}");
            assert_eq!(report["closed_solid_count"], 1, "{prompt}");
            let actual_dimensions = report["dimensions_mm"]
                .as_array()
                .expect("measured bounding box");
            for (actual, expected) in actual_dimensions
                .iter()
                .zip(case["dimensions_mm"].as_array().unwrap())
            {
                let actual = actual.as_f64().expect("numeric measured dimension");
                let expected = expected.as_f64().expect("numeric expected dimension");
                assert!(
                    (actual - expected).abs() <= 1e-8,
                    "{prompt}: dimensions {actual_dimensions:?}"
                );
            }
            let expected_volume = case["volume_mm3"].as_f64().expect("expected volume");
            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            assert!(
                (actual_volume - expected_volume).abs() <= 1e-4,
                "{prompt}: expected={expected_volume}, actual={actual_volume}"
            );
            let feature = &result["cadplan"]["features"][0];
            assert_eq!(feature["kind"], case["feature_kind"], "{prompt}");
            assert_eq!(result["cadplan"]["features"].as_array().unwrap().len(), 1);

            let aimodel = Path::new(
                result["output_paths"]["aimodel"]
                    .as_str()
                    .expect("AIMODEL path"),
            );
            let stl = Path::new(result["output_paths"]["stl"].as_str().expect("STL path"));
            assert!(aimodel.is_file(), "F5.19 AIMODEL export missing: {prompt}");
            assert!(stl.is_file(), "F5.19 STL export missing: {prompt}");
            let loaded = native_model_io::load_aimodel(aimodel).unwrap_or_else(|error| {
                panic!("F5.19 AIMODEL round-trip failed for {prompt}: {error}")
            });
            assert_eq!(
                loaded["cadplan"]["features"], result["cadplan"]["features"],
                "{prompt}"
            );
            eprintln!(
                "F5.19 PASS: {prompt} -> {:?} mm, volume={actual_volume} mm³",
                case["dimensions_mm"]
            );
        }
    }

    #[test]
    #[ignore = "requires an explicitly staged OCCT runtime; run the documented F5.20 acceptance command"]
    fn f5_20_legacy_box_corner_profiles_build_and_export() {
        use serde_json::Value;

        let manifest = std::env::var_os("AIMODELER_TEST_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("F5.20 requires AIMODELER_TEST_OCCT_MANIFEST");
        assert!(
            manifest.is_file(),
            "F5.20 OCCT runtime manifest is missing: {}",
            manifest.display()
        );
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_box_corners_contract.json"
        ))
        .expect("F5.20 acceptance fixture should be valid JSON");
        let root = std::env::temp_dir().join(format!(
            "quick-f5-20-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["legacy_accepted"]
            .as_array()
            .expect("legacy box corner profiles")
            .iter()
            .enumerate()
        {
            let prompt = case["input"].as_str().expect("fixed Quick prompt");
            let result = build(prompt, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("F5.20 Quick build failed for {prompt}: {error}"));
            assert_eq!(result["state"], "complete", "{prompt}");
            let report = &result["geometry_report"];
            assert_eq!(report["state"], "passed", "{prompt}");
            assert_eq!(report["valid"], true, "{prompt}");
            assert_eq!(report["closed_solid_count"], 1, "{prompt}");
            let actual_dimensions = report["dimensions_mm"]
                .as_array()
                .expect("measured bounding box");
            for (actual, expected) in actual_dimensions
                .iter()
                .zip(case["dimensions_mm"].as_array().unwrap())
            {
                let actual = actual.as_f64().expect("numeric measured dimension");
                let expected = expected.as_f64().expect("numeric expected dimension");
                assert!(
                    (actual - expected).abs() <= 1e-8,
                    "{prompt}: dimensions {actual_dimensions:?}"
                );
            }
            let expected_volume = case["volume_mm3"].as_f64().expect("expected volume");
            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            assert!(
                (actual_volume - expected_volume).abs() <= 1e-4,
                "{prompt}: expected={expected_volume}, actual={actual_volume}"
            );
            let feature = &result["cadplan"]["features"][0];
            assert_eq!(feature["kind"], case["feature_kind"], "{prompt}");
            assert_eq!(feature["parameters"]["corner"], case["parameters"]["corner"]);
            assert_eq!(feature["parameters"]["corner_mode"], case["parameters"]["corner_mode"]);

            let aimodel = Path::new(
                result["output_paths"]["aimodel"]
                    .as_str()
                    .expect("AIMODEL path"),
            );
            let stl = Path::new(result["output_paths"]["stl"].as_str().expect("STL path"));
            assert!(aimodel.is_file(), "F5.20 AIMODEL export missing: {prompt}");
            assert!(stl.is_file(), "F5.20 STL export missing: {prompt}");
            let loaded = native_model_io::load_aimodel(aimodel).unwrap_or_else(|error| {
                panic!("F5.20 AIMODEL round-trip failed for {prompt}: {error}")
            });
            assert_eq!(
                loaded["cadplan"]["features"], result["cadplan"]["features"],
                "{prompt}"
            );
            eprintln!(
                "F5.20 PASS: {prompt} -> {:?} mm, corner={} {}, volume={actual_volume} mm³",
                case["dimensions_mm"],
                case["parameters"]["corner"],
                case["parameters"]["corner_mode"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged OCCT runtime; run the documented F5.21 acceptance command"]
    fn f5_21_legacy_gears_build_with_optional_center_bore_and_export() {
        use serde_json::Value;

        let manifest = std::env::var_os("AIMODELER_TEST_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("F5.21 requires AIMODELER_TEST_OCCT_MANIFEST");
        assert!(
            manifest.is_file(),
            "F5.21 OCCT runtime manifest is missing: {}",
            manifest.display()
        );
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_gears_contract.json"
        ))
        .expect("F5.21 acceptance fixture should be valid JSON");
        let root = std::env::temp_dir().join(format!(
            "quick-f5-21-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["legacy_accepted"]
            .as_array()
            .expect("legacy gears")
            .iter()
            .enumerate()
        {
            let prompt = case["input"].as_str().expect("fixed Quick prompt");
            let result = build(prompt, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("F5.21 Quick build failed for {prompt}: {error}"));
            assert_eq!(result["state"], "complete", "{prompt}");
            let report = &result["geometry_report"];
            assert_eq!(report["state"], "passed", "{prompt}");
            assert_eq!(report["valid"], true, "{prompt}");
            assert_eq!(report["closed_solid_count"], 1, "{prompt}");
            let actual_dimensions = report["dimensions_mm"]
                .as_array()
                .expect("measured bounding box");
            for (actual, expected) in actual_dimensions
                .iter()
                .zip(case["dimensions_mm"].as_array().unwrap())
            {
                let actual = actual.as_f64().expect("numeric measured dimension");
                let expected = expected.as_f64().expect("numeric expected dimension");
                assert!(
                    (actual - expected).abs() <= 1e-8,
                    "{prompt}: dimensions {actual_dimensions:?}"
                );
            }
            let expected_volume = case["volume_mm3"].as_f64().expect("expected volume");
            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            assert!(
                (actual_volume - expected_volume).abs() <= 1e-4,
                "{prompt}: expected={expected_volume}, actual={actual_volume}"
            );

            let features = result["cadplan"]["features"]
                .as_array()
                .expect("CADPlan features");
            assert_eq!(features.len(), case["feature_count"].as_u64().unwrap() as usize);
            assert_eq!(features[0]["kind"], "gear", "{prompt}");
            for key in ["teeth", "module", "height", "base", "axis"] {
                assert_eq!(features[0]["parameters"][key], case["gear"][key], "{prompt}: {key}");
            }
            if let Some(bore_diameter) = case["bore_diameter"].as_f64() {
                assert_eq!(features[1]["kind"], "hole", "{prompt}");
                assert_eq!(features[1]["operation"], "cut", "{prompt}");
                assert_eq!(features[1]["parameters"]["diameter"], bore_diameter, "{prompt}");
                assert_eq!(features[1]["parameters"]["pos"], "center", "{prompt}");
                assert_eq!(features[1]["parameters"]["through"], true, "{prompt}");
            }

            let aimodel = Path::new(
                result["output_paths"]["aimodel"]
                    .as_str()
                    .expect("AIMODEL path"),
            );
            let stl = Path::new(result["output_paths"]["stl"].as_str().expect("STL path"));
            assert!(aimodel.is_file(), "F5.21 AIMODEL export missing: {prompt}");
            assert!(stl.is_file(), "F5.21 STL export missing: {prompt}");
            let loaded = native_model_io::load_aimodel(aimodel)
                .unwrap_or_else(|error| panic!("F5.21 AIMODEL round-trip failed for {prompt}: {error}"));
            assert_eq!(loaded["cadplan"]["features"], result["cadplan"]["features"], "{prompt}");
            eprintln!(
                "F5.21 PASS: {prompt} -> {:?} mm, volume={actual_volume} mm³",
                case["dimensions_mm"]
            );
        }
    }

    #[test]
    fn f5_12_failed_stl_export_preserves_existing_target() {
        let root = std::env::temp_dir().join(format!(
            "quick-f5-12-export-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        // Reuse the real exporter with invalid mesh input and an existing target.
        // The failure must leave the caller's file byte-for-byte intact.
        let protected_stl = root.join("existing-target.stl");
        fs::write(&protected_stl, b"preserve-existing-export").unwrap();
        let invalid_mesh = TriangleMesh {
            positions: vec![[f64::NAN, 0.0, 0.0]; 3],
            normals: vec![[0.0, 0.0, 1.0]; 3],
        };
        assert!(native_model_io::write_binary_stl(&protected_stl, &invalid_mesh, "invalid").is_err());
        assert_eq!(fs::read(&protected_stl).unwrap(), b"preserve-existing-export");

    }
}
