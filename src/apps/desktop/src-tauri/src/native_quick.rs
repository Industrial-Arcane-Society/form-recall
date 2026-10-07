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
        let failed_feature = built.report["failed_feature_id"]
            .as_str()
            .unwrap_or("unknown");
        let failure = built.report["failure_summary"]
            .as_str()
            .unwrap_or("未提供失败原因");
        return Err(format!(
            "Quick 导出要求完整 build；特征 {failed_feature} 失败：{failure}。部分几何仅可用于只读诊断预览。"
        ));
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
    #[ignore = "requires the staged OCCT runtime; run the documented F5.22 acceptance command"]
    fn f5_22_legacy_pcd_counterbores_build_measure_and_export() {
        use serde_json::Value;

        let manifest = std::env::var_os("AIMODELER_TEST_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("F5.22 requires AIMODELER_TEST_OCCT_MANIFEST");
        assert!(manifest.is_file(), "F5.22 OCCT runtime manifest is missing: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_pcd_counterbore_contract.json"
        ))
        .expect("F5.22 PCD counterbore acceptance fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "quick-f5-22-{}-{}",
            std::process::id(),
            SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["legacy_accepted"].as_array().unwrap().iter().enumerate() {
            let prompt = case["input"].as_str().unwrap();
            let result = build(prompt, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("F5.22 Quick build failed for {prompt}: {error}"));
            assert_eq!(result["state"], "complete", "{prompt}");
            let report = &result["geometry_report"];
            assert_eq!(report["state"], "passed", "{prompt}");
            assert_eq!(report["valid"], true, "{prompt}");
            assert_eq!(report["closed_solid_count"], 1, "{prompt}");
            assert_eq!(report["dimensions_mm"], fixture["expected_geometry"]["dimensions_mm"]);
            let expected_volume = fixture["expected_geometry"]["volume_mm3"].as_f64().unwrap();
            let actual_volume = report["volume_mm3"].as_f64().unwrap();
            assert!((actual_volume - expected_volume).abs() <= 1e-4, "{prompt}: expected volume {expected_volume}, got {actual_volume}");

            let features = result["cadplan"]["features"].as_array().unwrap();
            assert_eq!(features.len(), 3, "{prompt}");
            assert_eq!(features[1]["parameters"]["through"], true, "{prompt}");
            assert_eq!(features[2]["parameters"]["through"], false, "{prompt}");
            for key in ["pos", "axis", "count", "pcd", "phase"] {
                assert_eq!(features[1]["parameters"][key], features[2]["parameters"][key], "{prompt}: counterbore must share pilot {key}");
            }
            let holes = report["hole_instances"].as_array().unwrap();
            assert_eq!(holes.len(), 8, "{prompt}: expected four pilots and four counterbores");
            assert_eq!(report["hole_measurements_verified"], true, "{prompt}: all 8 B-Rep hole measurements must pass");
            assert_eq!(holes.iter().filter(|hole| hole["through"] == true).count(), 4);
            assert_eq!(holes.iter().filter(|hole| hole["through"] == false).count(), 4);

            let aimodel = Path::new(result["output_paths"]["aimodel"].as_str().unwrap());
            let stl = Path::new(result["output_paths"]["stl"].as_str().unwrap());
            assert!(aimodel.is_file(), "{prompt}: missing AIMODEL export");
            assert!(stl.is_file(), "{prompt}: missing STL export");
            let loaded = native_model_io::load_aimodel(aimodel)
                .unwrap_or_else(|error| panic!("F5.22 AIMODEL round-trip failed for {prompt}: {error}"));
            assert_eq!(loaded["cadplan"]["features"], result["cadplan"]["features"], "{prompt}");
            eprintln!("F5.22 PASS: {prompt} -> 4 coaxial PCD counterbores, volume={actual_volume} mm³");
        }
    }

    #[test]
    #[ignore = "requires the staged OCCT runtime; run the documented F5.23 acceptance command"]
    fn f5_23_legacy_corner_counterbores_build_measure_and_export() {
        use serde_json::Value;

        let manifest = std::env::var_os("AIMODELER_TEST_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("F5.23 requires AIMODELER_TEST_OCCT_MANIFEST");
        assert!(
            manifest.is_file(),
            "F5.23 OCCT runtime manifest is missing: {}",
            manifest.display()
        );
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_corner_counterbore_contract.json"
        ))
        .expect("F5.23 corner counterbore acceptance fixture is valid JSON");
        let cases = fixture["native_expected_cases"].as_array().unwrap();
        assert_eq!(
            cases.len(),
            3,
            "F5.23 covers inline, inherited, and separate-clause margins"
        );
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        for (case_index, case) in cases.iter().enumerate() {
            let prompt = case["input"].as_str().unwrap();
            let root = std::env::temp_dir().join(format!(
                "quick-f5-23-{}-{case_index}-{}",
                std::process::id(),
                SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()
            ));
            fs::create_dir_all(&root).unwrap();
            let _cleanup = RemoveOnDrop(root.clone());

            let result = build(prompt, 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("F5.23 Quick build failed for {prompt}: {error}"));
            assert_eq!(result["state"], "complete", "{prompt}");
            let report = &result["geometry_report"];
            assert_eq!(report["state"], "passed", "{prompt}");
            assert_eq!(report["valid"], true, "{prompt}");
            assert_eq!(report["closed_solid_count"], 1, "{prompt}");
            let expected = &case["expected_geometry"];
            assert_eq!(report["dimensions_mm"], expected["dimensions_mm"]);
            let expected_volume = expected["volume_mm3"].as_f64().unwrap();
            let actual_volume = report["volume_mm3"].as_f64().unwrap();
            assert!(
                (actual_volume - expected_volume).abs() <= 1e-4,
                "{prompt}: expected volume {expected_volume}, got {actual_volume}"
            );

            let holes = report["hole_instances"].as_array().unwrap();
            assert_eq!(holes.len(), 8, "{prompt}: expected four pilots and four counterbores");
            assert_eq!(report["hole_measurements_verified"], true, "{prompt}: all B-Rep hole measurements must pass");
            assert_eq!(
                holes.iter().filter(|hole| hole["through"] == true).count(),
                expected["pilot_holes"]
            );
            assert_eq!(
                holes.iter().filter(|hole| hole["through"] == false).count(),
                expected["counterbores"]
            );

            let features = result["cadplan"]["features"].as_array().unwrap();
            assert_eq!(features.len(), 3, "{prompt}");
            assert_eq!(
                features[1]["parameters"]["margin"],
                case["pilot_margin_mm"]
            );
            assert_eq!(
                features[2]["parameters"]["margin"],
                case["counterbore_margin_mm"]
            );
            let aimodel = Path::new(result["output_paths"]["aimodel"].as_str().unwrap());
            let stl = Path::new(result["output_paths"]["stl"].as_str().unwrap());
            assert!(aimodel.is_file(), "{prompt}: missing AIMODEL export");
            assert!(stl.is_file(), "{prompt}: missing STL export");
            let loaded = native_model_io::load_aimodel(aimodel)
                .unwrap_or_else(|error| panic!("F5.23 AIMODEL round-trip failed for {prompt}: {error}"));
            assert_eq!(
                loaded["cadplan"]["features"],
                result["cadplan"]["features"],
                "{prompt}"
            );
            eprintln!("F5.23 PASS: {prompt} -> concentric four-corner pilot/counterbore pairs, volume={actual_volume} mm³");
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

#[cfg(test)]
mod acceptance_tests {
    use super::*;

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn legacy_flat_washer_build_reopens_and_exports_through_native_quick() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        let root = std::env::temp_dir().join(format!(
            "native-quick-washer-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, input) in ["平垫圈 外径20 内径8 厚度2", "平垫圈 外径20 内径8"]
            .into_iter()
            .enumerate()
        {
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");

            let dimensions = output["stats"]["size"]
                .as_array()
                .unwrap_or_else(|| panic!("{input}: missing size summary: {output}"));
            assert_eq!(dimensions.len(), 3, "{input}: {output}");
            for (actual, expected) in dimensions.iter().zip([20.0, 20.0, 2.0]) {
                assert!(
                    (actual.as_f64().unwrap() - expected).abs() < 1e-6,
                    "{input}: dimensions {dimensions:?}"
                );
            }
            let expected_volume =
                std::f64::consts::PI * (10.0_f64.powi(2) - 4.0_f64.powi(2)) * 2.0;
            let actual_volume = report["volume_mm3"].as_f64().unwrap();
            assert!(
                (actual_volume - expected_volume).abs() < 1e-5,
                "{input}: expected volume {expected_volume}, got {actual_volume}"
            );

            let holes = report["hole_instances"]
                .as_array()
                .unwrap_or_else(|| panic!("{input}: hole measurements missing: {report}"));
            assert_eq!(holes.len(), 1, "{input}: {report}");
            assert_eq!(report["hole_measurement_complete"], true, "{input}: {report}");
            assert_eq!(report["hole_measurements_verified"], true, "{input}: {report}");

            let outputs = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(
                    Path::new(outputs[name].as_str().unwrap()).is_file(),
                    "{input}: {name}"
                );
            }
            let loaded = native_model_io::load_aimodel(Path::new(
                outputs["aimodel"].as_str().unwrap(),
            ))
            .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["features"].as_array().map(Vec::len), Some(2), "{input}");
            if index == 1 {
                assert!(
                    output["warnings"].to_string().contains("legacy_washer_thickness_defaulted"),
                    "{input}: expected the legacy thickness default warning"
                );
            }
            println!(
                "F5.26 native acceptance: input={input}; dimensions_mm={}; volume_mm3={actual_volume}; triangles={}",
                serde_json::to_string(&output["stats"]["size"]).unwrap(),
                output["stats"]["triangles"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn legacy_helical_springs_build_reopen_and_export_through_native_quick() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        let root = std::env::temp_dir().join(format!(
            "native-quick-spring-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, (input, expected_bounds)) in [
            ("螺旋弹簧 外径20 线径2 高度30 圈数8", [20.0, 20.0, 30.0]),
            ("锥形弹簧 外径24 线径2 高度40 圈数6", [24.0, 24.0, 40.0]),
        ]
        .into_iter()
        .enumerate()
        {
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            let volume = report["volume_mm3"].as_f64().unwrap_or_default();
            assert!(volume.is_finite() && volume > 0.0, "{input}: {report}");

            let dimensions = output["stats"]["size"]
                .as_array()
                .unwrap_or_else(|| panic!("{input}: missing size summary: {output}"));
            assert_eq!(dimensions.len(), 3, "{input}: {output}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_bounds).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {output}"
                );
            }
            assert_eq!(output["features"].as_array().map(Vec::len), Some(1), "{input}");
            assert_eq!(output["features"][0]["kind"], "helix", "{input}: {:?}", output["features"]);

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["features"].as_array().map(Vec::len), Some(1), "{input}");
            println!(
                "F5.27 native acceptance: input={input}; dimensions_mm={}; volume_mm3={volume}; triangles={}",
                serde_json::to_string(&output["stats"]["size"]).unwrap(),
                output["stats"]["triangles"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn legacy_timing_pulleys_build_reopen_and_export_through_native_quick() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        let root = std::env::temp_dir().join(format!(
            "native-quick-timing-pulley-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, (input, expected_diameter, expected_width)) in [
            (
                "同步带轮 XL型 节圆直径48.5104 齿数30 宽度10 内径12",
                48.5104,
                10.0,
            ),
            ("同步轮 T5 节圆直径50 齿数30 宽度12 内径10", 50.0, 12.0),
            ("同步带轮 MXL型 节圆直径80 齿数120 宽度10 内径8", 80.0, 10.0),
            ("同步带轮 T2.5型 节圆直径50 齿数63 宽度10 内径8", 50.0, 10.0),
            ("同步带轮 3M型 节圆直径60 齿数63 宽度10 内径8", 60.0, 10.0),
            ("同步带轮", 80.0, 30.0),
        ]
        .into_iter()
        .enumerate()
        {
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            assert!(
                report["volume_mm3"].as_f64().unwrap_or_default() > 0.0,
                "{input}: {report}"
            );

            let dimensions = output["stats"]["size"]
                .as_array()
                .unwrap_or_else(|| panic!("{input}: missing size summary: {output}"));
            assert_eq!(dimensions.len(), 3, "{input}: {output}");
            let x = dimensions[0].as_f64().unwrap_or_default();
            let y = dimensions[1].as_f64().unwrap_or_default();
            let z = dimensions[2].as_f64().unwrap_or_default();
            assert!(
                x > expected_diameter && x < expected_diameter + 5.0,
                "{input}: {dimensions:?}"
            );
            assert!(
                y > expected_diameter && y < expected_diameter + 5.0,
                "{input}: {dimensions:?}"
            );
            assert!(
                (z - expected_width).abs() <= 1e-5,
                "{input}: {dimensions:?}"
            );
            assert_eq!(
                output["features"].as_array().map(Vec::len),
                Some(1),
                "{input}"
            );
            assert_eq!(output["features"][0]["kind"], "timing_pulley", "{input}");

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(
                    Path::new(paths[name].as_str().unwrap()).is_file(),
                    "{input}: missing {name}"
                );
            }
            let loaded =
                native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                    .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(
                loaded["features"].as_array().map(Vec::len),
                Some(1),
                "{input}: AIMODEL round-trip"
            );
            println!(
                "F5.28 native acceptance: input={input}; dimensions_mm={}; volume_mm3={}; triangles={}",
                serde_json::to_string(&output["stats"]["size"]).unwrap(),
                output["stats"]["volume"],
                output["stats"]["triangles"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn legacy_vbelt_pulleys_build_reopen_and_export_through_native_quick() {
        use serde_json::Value;

        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_vbelt_pulley_contract.json"
        ))
        .expect("V-belt Quick acceptance fixture is valid JSON");
        let cases = fixture["native_geometry_cases"].as_array().unwrap();
        assert_eq!(cases.len(), 4, "explicit A/B/Y, plus legacy-default pulley");
        let root = std::env::temp_dir().join(format!(
            "native-quick-vbelt-pulley-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in cases.iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            let actual_volume = report["volume_mm3"].as_f64().unwrap_or_default();
            assert!(
                actual_volume.is_finite() && actual_volume > 0.0,
                "{input}: {report}"
            );
            let actual_dimensions = output["stats"]["size"]
                .as_array()
                .unwrap_or_else(|| panic!("{input}: missing size summary: {output}"));
            let expected_dimensions = case["expected_dimensions_mm"].as_array().unwrap();
            assert_eq!(actual_dimensions.len(), 3, "{input}: {output}");
            for (axis, (actual, expected)) in actual_dimensions
                .iter()
                .zip(expected_dimensions)
                .enumerate()
            {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {output}"
                );
            }
            assert_eq!(
                output["features"].as_array().map(Vec::len),
                Some(1),
                "{input}"
            );
            assert_eq!(output["features"][0]["kind"], "vbelt_pulley", "{input}");
            assert_eq!(
                output["cadplan"]["features"].as_array().unwrap().len(),
                1,
                "{input}"
            );

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(
                    Path::new(paths[name].as_str().unwrap()).is_file(),
                    "{input}: missing {name}"
                );
            }
            let loaded =
                native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                    .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(
                loaded["cadplan"]["features"], output["cadplan"]["features"],
                "{input}: AIMODEL round-trip"
            );
            println!(
                "F5.29 native acceptance: input={input}; dimensions_mm={}; volume_mm3={actual_volume}; triangles={}",
                serde_json::to_string(&output["stats"]["size"]).unwrap(),
                output["stats"]["triangles"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_30_measured_socket_head_bolts_build_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_socket_head_bolt_contract.json"
        ))
        .expect("socket-head screw acceptance fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-socket-bolt-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_rust_expected"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            assert!(report["volume_mm3"].as_f64().unwrap_or_default() > 0.0, "{input}: {report}");

            let dimensions = output["stats"]["size"].as_array().expect("size summary");
            let expected = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), expected.len(), "{input}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!((actual - expected).abs() <= 1e-5, "{input}: axis {axis} = {actual}, expected {expected}; {output}");
            }

            let kinds = case["feature_kinds"].as_array().unwrap();
            let features = output["cadplan"]["features"].as_array().expect("CADPlan feature list");
            assert_eq!(features.len(), kinds.len(), "{input}");
            for (feature, expected_kind) in features.iter().zip(kinds) {
                assert_eq!(feature["kind"], *expected_kind, "{input}");
            }
            let bolt_row = report["feature_results"].as_array().unwrap().last().unwrap();
            assert_eq!(bolt_row["state"], "built", "{input}: {bolt_row}");
            assert_eq!(bolt_row["support_details"]["thread"]["visible_geometry"], true, "{input}: {bolt_row}");

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            let stl = fs::read(paths["stl"].as_str().unwrap()).unwrap();
            assert!(stl.len() >= 84, "{input}: STL header is truncated");
            let triangles = u32::from_le_bytes(stl[80..84].try_into().unwrap()) as usize;
            assert!(triangles > 0, "{input}: STL contains no triangles");
            assert_eq!(stl.len(), 84 + triangles * 50, "{input}: STL byte count is inconsistent");
            println!("F5.30 native acceptance: input={input}; dimensions_mm={}; volume_mm3={}; triangles={triangles}",
                serde_json::to_string(&output["stats"]["size"]).unwrap(), report["volume_mm3"]);
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_31_legacy_solid_shafts_build_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_solid_shaft_contract.json"
        ))
        .expect("solid-shaft Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-solid-shaft-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_rust_expected"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");

            let dimensions = output["stats"]["size"].as_array().expect("size summary");
            let expected = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {output}");
            assert_eq!(expected.len(), 3, "{input}: fixture dimensions");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {output}"
                );
            }
            let legacy_volume = case["legacy_mesh_volume_mm3"].as_f64().unwrap();
            let native_volume = report["volume_mm3"].as_f64().unwrap_or_default();
            let relative_delta = (native_volume - legacy_volume).abs() / legacy_volume;
            let max_delta = case["max_relative_volume_delta"].as_f64().unwrap();
            assert!(
                relative_delta <= max_delta,
                "{input}: volume delta {relative_delta} exceeds {max_delta}; {report}"
            );
            assert_eq!(
                output["cadplan"]["features"].as_array().map(Vec::len),
                Some(1),
                "{input}"
            );
            assert_eq!(
                output["cadplan"]["features"][0]["kind"],
                "cylinder",
                "{input}"
            );

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(
                    Path::new(paths[name].as_str().unwrap()).is_file(),
                    "{input}: missing {name}"
                );
            }
            let loaded =
                native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                    .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(
                loaded["cadplan"]["features"], output["cadplan"]["features"],
                "{input}: AIMODEL round-trip"
            );
            println!(
                "F5.31 native acceptance: input={input}; dimensions_mm={}; legacy_volume_mm3={legacy_volume}; native_volume_mm3={native_volume}; relative_delta={relative_delta}",
                serde_json::to_string(&output["stats"]["size"]).unwrap()
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_32_legacy_bearing_rings_build_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_bearing_rings_contract.json"
        ))
        .expect("bearing-ring Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-bearing-ring-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_rust_expected"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            let native_volume = report["volume_mm3"].as_f64().unwrap_or_default();
            assert!(native_volume > 0.0, "{input}: {report}");

            let dimensions = output["stats"]["size"].as_array().expect("size summary");
            let expected = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {output}");
            assert_eq!(expected.len(), 3, "{input}: fixture dimensions");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {output}"
                );
            }
            let legacy_volume = case["legacy_mesh_volume_mm3"].as_f64().unwrap();
            let relative_delta = (native_volume - legacy_volume).abs() / legacy_volume;
            let max_delta = case["max_relative_volume_delta"].as_f64().unwrap();
            assert!(
                relative_delta <= max_delta,
                "{input}: volume delta {relative_delta} exceeds {max_delta}; {report}"
            );

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), 2, "{input}");
            assert_eq!(features[0]["kind"], "cylinder", "{input}");
            assert_eq!(features[1]["kind"], "hole", "{input}");
            assert_eq!(features[1]["operation"], "cut", "{input}");

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.32 native acceptance: input={input}; dimensions_mm={}; legacy_volume_mm3={legacy_volume}; native_volume_mm3={native_volume}; relative_delta={relative_delta}",
                serde_json::to_string(&output["stats"]["size"]).unwrap()
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_33_legacy_simple_seal_rings_build_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_simple_rings_contract.json"
        ))
        .expect("simple-ring Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-simple-ring-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_rust_expected"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");

            let dimensions = output["stats"]["size"].as_array().expect("size summary");
            let expected = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {output}");
            assert_eq!(expected.len(), 3, "{input}: fixture dimensions");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {output}"
                );
            }
            let legacy_volume = case["legacy_mesh_volume_mm3"].as_f64().unwrap();
            let native_volume = report["volume_mm3"].as_f64().unwrap_or_default();
            let relative_delta = (native_volume - legacy_volume).abs() / legacy_volume;
            let max_delta = case["max_relative_volume_delta"].as_f64().unwrap();
            assert!(
                relative_delta <= max_delta,
                "{input}: volume delta {relative_delta} exceeds {max_delta}; {report}"
            );
            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), 2, "{input}");
            assert_eq!(features[0]["kind"], "cylinder", "{input}");
            assert_eq!(features[1]["kind"], "hole", "{input}");
            assert_eq!(features[1]["operation"], "cut", "{input}");

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.33 native acceptance: input={input}; dimensions_mm={}; legacy_volume_mm3={legacy_volume}; native_volume_mm3={native_volume}; relative_delta={relative_delta}",
                serde_json::to_string(&output["stats"]["size"]).unwrap()
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_34_legacy_couplings_build_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_coupling_contract.json"
        ))
        .expect("coupling Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-coupling-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_rust_expected"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");

            let dimensions = output["stats"]["size"].as_array().expect("size summary");
            let expected = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {output}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {output}"
                );
            }
            let legacy_volume = case["legacy_mesh_volume_mm3"].as_f64().unwrap();
            let native_volume = report["volume_mm3"].as_f64().unwrap_or_default();
            let relative_delta = (native_volume - legacy_volume).abs() / legacy_volume;
            let max_delta = case["max_relative_volume_delta"].as_f64().unwrap();
            assert!(
                relative_delta <= max_delta,
                "{input}: volume delta {relative_delta} exceeds {max_delta}; {report}"
            );
            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), 2, "{input}");
            assert_eq!(features[0]["kind"], "cylinder", "{input}");
            assert_eq!(features[1]["kind"], "hole", "{input}");
            assert_eq!(features[1]["operation"], "cut", "{input}");

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.34 native acceptance: input={input}; dimensions_mm={}; legacy_volume_mm3={legacy_volume}; native_volume_mm3={native_volume}; relative_delta={relative_delta}",
                serde_json::to_string(&output["stats"]["size"]).unwrap()
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_35_legacy_brake_discs_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_brake_disc_contract.json"
        ))
        .expect("brake-disc Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-brake-disc-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_expected_cases"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");

            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}"
                );
            }

            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            let analytic_volume = case["analytic_volume_mm3"].as_f64().unwrap();
            assert!(
                (actual_volume - analytic_volume).abs() <= 1e-4,
                "{input}: analytic volume {analytic_volume}, measured {actual_volume}; {report}"
            );
            let legacy_volume = case["legacy_mesh_volume_mm3"].as_f64().unwrap();
            let relative_delta = (actual_volume - legacy_volume).abs() / legacy_volume;
            assert!(
                relative_delta <= case["max_relative_legacy_volume_delta"].as_f64().unwrap(),
                "{input}: old mesh volume delta {relative_delta}; {report}"
            );

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), if expected_dimensions[0].as_f64().unwrap() > 0.0 && case["hole_diameters_mm"].as_array().unwrap().len() > 1 { 3 } else { 2 }, "{input}");
            assert_eq!(features[0]["kind"], "cylinder", "{input}");
            assert_eq!(features[0]["parameters"]["segments"], 64, "{input}");
            assert_eq!(features[1]["kind"], "hole", "{input}");
            assert_eq!(features[1]["parameters"]["pos"], "center", "{input}");
            assert_eq!(features[1]["parameters"]["segments"], 48, "{input}");
            if features.len() == 3 {
                assert_eq!(features[2]["kind"], "hole", "{input}");
                assert_eq!(features[2]["parameters"]["pos"], "pcd", "{input}");
                assert_eq!(features[2]["parameters"]["segments"], 24, "{input}");
            }

            let expected_diameters = case["hole_diameters_mm"].as_array().unwrap();
            let expected_centers = case["hole_centers_xy_mm"].as_array().unwrap();
            let holes = report["hole_instances"].as_array().expect("measured hole instances");
            assert_eq!(holes.len(), expected_diameters.len(), "{input}: {report}");
            assert_eq!(expected_centers.len(), expected_diameters.len(), "{input}: fixture hole evidence");
            assert_eq!(report["hole_measurement_complete"], true, "{input}: every hole needs final B-Rep measurement");
            assert_eq!(report["hole_measurements_verified"], true, "{input}: all holes must be measured");
            for (hole_index, (hole, (diameter, center))) in holes
                .iter()
                .zip(expected_diameters.iter().zip(expected_centers))
                .enumerate()
            {
                assert_eq!(hole["through"], true, "{input}: hole {hole_index}");
                assert_eq!(hole["status"], "verified_open", "{input}: hole {hole_index}: {hole}");
                assert!((hole["diameter_mm"].as_f64().unwrap() - diameter.as_f64().unwrap()).abs() < 1e-8, "{input}: hole {hole_index}");
                for axis in 0..2 {
                    assert!((hole["centerline_mm"][axis].as_f64().unwrap() - center[axis].as_f64().unwrap()).abs() < 1e-6, "{input}: hole {hole_index}, axis {axis}");
                }
            }

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.35 native acceptance: input={input}; dimensions_mm={}; hole_instances={}; legacy_volume_mm3={legacy_volume}; native_volume_mm3={actual_volume}; relative_delta={relative_delta}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(), holes.len()
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_36_legacy_brake_drums_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_brake_drum_contract.json"
        ))
        .expect("brake-drum Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-brake-drum-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_expected_cases"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");

            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}"
                );
            }

            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            let legacy_volume = case["legacy_mesh_volume_mm3"].as_f64().unwrap();
            let relative_delta = (actual_volume - legacy_volume).abs() / legacy_volume;
            assert!(
                relative_delta <= case["max_relative_legacy_volume_delta"].as_f64().unwrap(),
                "{input}: old mesh volume delta {relative_delta}; {report}"
            );
            if let Some(analytic_volume) = case["analytic_volume_mm3"].as_f64() {
                assert!(
                    (actual_volume - analytic_volume).abs() <= analytic_volume * 1e-10,
                    "{input}: expected no effective PCD cuts, analytic ring volume {analytic_volume}, measured {actual_volume}"
                );
            }

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), 3, "{input}: {output}");
            assert_eq!(features[0]["kind"], "cylinder", "{input}");
            assert_eq!(features[0]["parameters"]["segments"], 64, "{input}");
            assert_eq!(features[1]["kind"], "hole", "{input}");
            assert_eq!(features[1]["parameters"]["pos"], "center", "{input}");
            assert_eq!(features[1]["parameters"]["segments"], 48, "{input}");
            assert_eq!(features[2]["kind"], "hole", "{input}");
            assert_eq!(features[2]["operation"], "cut", "{input}");
            assert_eq!(features[2]["parameters"]["pos"], "pcd", "{input}");
            assert_eq!(features[2]["parameters"]["count"], 5, "{input}");
            assert_eq!(features[2]["parameters"]["segments"], 24, "{input}");

            let expected_diameters = case["hole_diameters_mm"].as_array().unwrap();
            let expected_centers = case["hole_centers_xy_mm"].as_array().unwrap();
            let holes = report["hole_instances"].as_array().expect("measured hole instances");
            let pcd_no_op = case["pcd_no_op"] == true;
            assert_eq!(report["feature_results"][2]["state"], if pcd_no_op { "no_op" } else { "built" }, "{input}: {report}");
            assert_eq!(
                holes.len(),
                if pcd_no_op { 1 } else { expected_diameters.len() },
                "{input}: {report}"
            );
            if pcd_no_op {
                assert_eq!(report["hole_measurements_verified"], true, "{input}: the center bore is measured; the no-op PCD feature is reported separately");
            } else {
                assert_eq!(expected_centers.len(), expected_diameters.len(), "{input}: fixture hole evidence");
                assert_eq!(report["hole_measurements_verified"], false, "{input}: PCD cuts merge into the center bore and cannot be certified as five independent cylinders");
                for (hole_index, hole) in holes.iter().enumerate().skip(1) {
                    assert_eq!(hole["status"], "candidate_unverified", "{input}: overlapping PCD hole {hole_index}: {hole}");
                }
            }
            for (hole_index, (hole, (diameter, center))) in holes
                .iter()
                .zip(expected_diameters.iter().zip(expected_centers))
                .enumerate()
            {
                assert_eq!(hole["through"], true, "{input}: hole {hole_index}");
                assert!((hole["diameter_mm"].as_f64().unwrap() - diameter.as_f64().unwrap()).abs() < 1e-8, "{input}: hole {hole_index}");
                for axis in 0..2 {
                    assert!((hole["centerline_mm"][axis].as_f64().unwrap() - center[axis].as_f64().unwrap()).abs() < 1e-6, "{input}: hole {hole_index}, axis {axis}");
                }
            }

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.36 native acceptance: input={input}; dimensions_mm={}; hole_instances={}; legacy_volume_mm3={legacy_volume}; native_volume_mm3={actual_volume}; relative_delta={relative_delta}; hole_measurements_verified={}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(), holes.len(), report["hole_measurements_verified"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_37_legacy_flywheels_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_flywheel_contract.json"
        ))
        .expect("flywheel Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-flywheel-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_expected_cases"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");

            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!((actual - expected).abs() <= 1e-5, "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}");
            }

            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            let analytic_volume = case["analytic_volume_mm3"].as_f64().unwrap();
            assert!((actual_volume - analytic_volume).abs() <= analytic_volume * 1e-10, "{input}: analytic volume {analytic_volume}, measured {actual_volume}; {report}");
            let legacy_volume = case["legacy_mesh_volume_mm3"].as_f64().unwrap();
            let relative_delta = (actual_volume - legacy_volume).abs() / legacy_volume;
            assert!(relative_delta <= case["max_relative_legacy_volume_delta"].as_f64().unwrap(), "{input}: old mesh volume delta {relative_delta}; {report}");

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            let expected_diameters = case["hole_diameters_mm"].as_array().unwrap();
            let expected_centers = case["hole_centers_xy_mm"].as_array().unwrap();
            let pcd_no_op = case["pcd_no_op"] == true;
            let expected_feature_count = if expected_diameters.len() > 1 || pcd_no_op { 3 } else { 2 };
            assert_eq!(features.len(), expected_feature_count, "{input}: {output}");
            assert_eq!(features[0]["kind"], "cylinder", "{input}");
            assert_eq!(features[0]["parameters"]["segments"], 64, "{input}");
            assert_eq!(features[1]["kind"], "hole", "{input}");
            assert_eq!(features[1]["parameters"]["pos"], "center", "{input}");
            assert_eq!(features[1]["parameters"]["segments"], 48, "{input}");
            if features.len() == 3 {
                assert_eq!(features[2]["kind"], "hole", "{input}");
                assert_eq!(features[2]["operation"], "cut", "{input}");
                assert_eq!(features[2]["parameters"]["pos"], "pcd", "{input}");
                assert_eq!(features[2]["parameters"]["segments"], 24, "{input}");
                assert_eq!(report["feature_results"][2]["state"], if pcd_no_op { "no_op" } else { "built" }, "{input}: {report}");
            }

            let holes = report["hole_instances"].as_array().expect("measured hole instances");
            assert_eq!(holes.len(), expected_diameters.len(), "{input}: {report}");
            assert_eq!(expected_centers.len(), expected_diameters.len(), "{input}: fixture hole evidence");
            assert_eq!(report["hole_measurements_verified"], true, "{input}: all effective holes should have final B-Rep measurements");
            for (hole_index, (hole, (diameter, center))) in holes.iter().zip(expected_diameters.iter().zip(expected_centers)).enumerate() {
                assert_eq!(hole["through"], true, "{input}: hole {hole_index}");
                assert_eq!(hole["status"], "verified_open", "{input}: hole {hole_index}: {hole}");
                assert!((hole["diameter_mm"].as_f64().unwrap() - diameter.as_f64().unwrap()).abs() < 1e-8, "{input}: hole {hole_index}");
                for axis in 0..2 {
                    assert!((hole["centerline_mm"][axis].as_f64().unwrap() - center[axis].as_f64().unwrap()).abs() < 1e-6, "{input}: hole {hole_index}, axis {axis}");
                }
            }

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.37 native acceptance: input={input}; dimensions_mm={}; hole_instances={}; legacy_volume_mm3={legacy_volume}; native_volume_mm3={actual_volume}; relative_delta={relative_delta}; pcd_no_op={pcd_no_op}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(), holes.len()
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_38_legacy_flat_annular_parts_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_flat_annular_parts_contract.json"
        ))
        .expect("flat-annular-part Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-flat-annular-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_expected_cases"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");

            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!((actual - expected).abs() <= 1e-5, "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}");
            }

            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            let analytic_volume = case["analytic_volume_mm3"].as_f64().unwrap();
            assert!((actual_volume - analytic_volume).abs() <= analytic_volume * 1e-10, "{input}: analytic volume {analytic_volume}, measured {actual_volume}; {report}");
            let legacy_volume = case["legacy_mesh_volume_mm3"].as_f64().unwrap();
            let relative_delta = (actual_volume - legacy_volume).abs() / legacy_volume;
            assert!(relative_delta <= case["max_relative_legacy_volume_delta"].as_f64().unwrap(), "{input}: legacy mesh delta {relative_delta}; {report}");

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), 2, "{input}: {output}");
            assert_eq!(features[0]["kind"], "cylinder", "{input}");
            assert_eq!(features[0]["parameters"]["segments"], 64, "{input}");
            assert_eq!(features[1]["kind"], "hole", "{input}");
            assert_eq!(features[1]["operation"], "cut", "{input}");
            assert_eq!(features[1]["parameters"]["segments"], 48, "{input}");

            let holes = report["hole_instances"].as_array().expect("measured center hole");
            let expected_diameters = case["hole_diameters_mm"].as_array().unwrap();
            let expected_centers = case["hole_centers_xy_mm"].as_array().unwrap();
            assert_eq!(holes.len(), 1, "{input}: {report}");
            assert_eq!(expected_diameters.len(), 1, "{input}: fixture");
            assert_eq!(expected_centers.len(), 1, "{input}: fixture");
            assert_eq!(report["hole_measurements_verified"], true, "{input}: {report}");
            let hole = &holes[0];
            assert_eq!(hole["through"], true, "{input}: {hole}");
            assert_eq!(hole["status"], "verified_open", "{input}: {hole}");
            assert!((hole["diameter_mm"].as_f64().unwrap() - expected_diameters[0].as_f64().unwrap()).abs() < 1e-8, "{input}: {hole}");
            for axis in 0..2 {
                assert!((hole["centerline_mm"][axis].as_f64().unwrap() - expected_centers[0][axis].as_f64().unwrap()).abs() < 1e-6, "{input}: {hole}");
            }

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.38 native acceptance: input={input}; dimensions_mm={}; hole_diameter_mm={}; legacy_volume_mm3={legacy_volume}; native_volume_mm3={actual_volume}; relative_delta={relative_delta}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(), hole["diameter_mm"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_39_legacy_flat_keys_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_flat_key_contract.json"
        ))
        .expect("flat-key Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-flat-key-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_expected_cases"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");

            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!((actual - expected).abs() <= 1e-5, "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}");
            }

            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            let expected_volume = case["volume_mm3"].as_f64().unwrap();
            assert!((actual_volume - expected_volume).abs() <= 1e-5, "{input}: volume {actual_volume}, expected {expected_volume}; {report}");
            let legacy_volume = case["legacy_mesh_volume_mm3"].as_f64().unwrap();
            let relative_delta = (actual_volume - legacy_volume).abs() / legacy_volume;
            assert!(relative_delta <= case["max_relative_legacy_volume_delta"].as_f64().unwrap(), "{input}: legacy mesh delta {relative_delta}; {report}");

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), 1, "{input}: {output}");
            assert_eq!(features[0]["kind"], "box", "{input}");
            assert_eq!(features[0]["parameters"]["length"], 8.0, "{input}");
            assert_eq!(features[0]["parameters"]["width"], 7.0, "{input}");
            assert_eq!(features[0]["parameters"]["height"], 40.0, "{input}");
            assert_eq!(features[0]["parameters"]["center"], json!([0.0, 0.0, 0.0]), "{input}");
            assert_eq!(features[0]["parameters"]["base"], false, "{input}");

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.39 native acceptance: input={input}; dimensions_mm={}; legacy_volume_mm3={legacy_volume}; native_volume_mm3={actual_volume}; relative_delta={relative_delta}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap()
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_40_legacy_bearing_seats_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_bearing_seat_contract.json"
        ))
        .expect("bearing-seat Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-bearing-seat-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_expected_cases"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");

            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!((actual - expected).abs() <= 1e-5, "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}");
            }

            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            let expected_volume = case["volume_mm3"].as_f64().unwrap();
            assert!((actual_volume - expected_volume).abs() <= 0.01, "{input}: volume {actual_volume}, expected {expected_volume}");
            let legacy_volume = case["legacy_mesh_volume_mm3"].as_f64().unwrap();
            let relative_delta = (actual_volume - legacy_volume).abs() / legacy_volume;
            assert!(relative_delta <= case["max_relative_legacy_volume_delta"].as_f64().unwrap(), "{input}: legacy mesh delta {relative_delta}; {report}");

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), 2, "{input}: {output}");
            assert_eq!(features[0]["kind"], "cylinder", "{input}");
            assert_eq!(features[0]["parameters"]["segments"], 64, "{input}");
            assert_eq!(features[1]["kind"], "hole", "{input}");
            assert_eq!(features[1]["operation"], "cut", "{input}");
            assert_eq!(features[1]["parameters"]["segments"], 48, "{input}");

            let holes = report["hole_instances"].as_array().expect("measured center bore");
            assert_eq!(holes.len(), 1, "{input}: {report}");
            assert_eq!(report["hole_measurements_verified"], true, "{input}: {report}");
            assert_eq!(holes[0]["through"], true, "{input}: {}", holes[0]);
            assert_eq!(holes[0]["status"], "verified_open", "{input}: {}", holes[0]);
            assert!((holes[0]["diameter_mm"].as_f64().unwrap() - case["inner_diameter_mm"].as_f64().unwrap()).abs() < 1e-8, "{input}: {}", holes[0]);

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.40 native acceptance: input={input}; dimensions_mm={}; inner_diameter_mm={}; legacy_volume_mm3={legacy_volume}; native_volume_mm3={actual_volume}; relative_delta={relative_delta}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                holes[0]["diameter_mm"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_41_legacy_disc_cam_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_disc_cam_contract.json"
        ))
        .expect("disc-cam Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-disc-cam-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_expected_cases"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], case["closed_solid_count"], "{input}: {report}");

            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!((actual - expected).abs() <= 1e-5, "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}");
            }

            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            let expected_volume = case["volume_mm3"].as_f64().unwrap();
            assert!((actual_volume - expected_volume).abs() <= 0.01, "{input}: volume {actual_volume}, expected {expected_volume}; {report}");
            let legacy_volume = case["legacy_mesh_volume_mm3"].as_f64().unwrap();
            let relative_delta = (actual_volume - legacy_volume).abs() / legacy_volume;
            assert!(relative_delta <= case["max_relative_legacy_volume_delta"].as_f64().unwrap(), "{input}: legacy mesh delta {relative_delta}; {report}");

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), case["feature_count"], "{input}: {output}");
            assert_eq!(features[0]["kind"], "prism", "{input}");
            assert_eq!(features[0]["parameters"]["profile"].as_array().map(Vec::len), Some(case["profile_point_count"].as_u64().unwrap() as usize), "{input}");
            assert_eq!(features[0]["parameters"]["height"], expected_dimensions[2], "{input}");

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.41 native acceptance: input={input}; dimensions_mm={}; legacy_volume_mm3={legacy_volume}; native_volume_mm3={actual_volume}; relative_delta={relative_delta}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap()
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_42_legacy_spline_shafts_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_spline_shaft_contract.json"
        ))
        .expect("spline-shaft Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-spline-shaft-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_expected_cases"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], case["closed_solid_count"], "{input}: {report}");

            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!((actual - expected).abs() <= 1e-5, "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}");
            }

            let actual_volume = report["volume_mm3"].as_f64().expect("measured volume");
            let legacy_volume = case["legacy_mesh_volume_mm3"].as_f64().unwrap();
            let relative_delta = (actual_volume - legacy_volume).abs() / legacy_volume;
            assert!(relative_delta <= case["max_relative_legacy_volume_delta"].as_f64().unwrap(), "{input}: legacy mesh delta {relative_delta}; {report}");

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), case["feature_count"], "{input}: {output}");
            assert_eq!(features[0]["kind"], "prism", "{input}");
            let teeth = case["profile_teeth"].as_u64().unwrap().to_string();
            let expected_profile = &fixture["profile_contracts"]["tooth_count"][&teeth]["points"];
            assert_eq!(features[0]["parameters"]["profile"], *expected_profile, "{input}: legacy profile");
            assert_eq!(features[0]["parameters"]["height"], expected_dimensions[2], "{input}");

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.42 native acceptance: input={input}; dimensions_mm={}; legacy_volume_mm3={legacy_volume}; native_volume_mm3={actual_volume}; relative_delta={relative_delta}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap()
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_43_legacy_spoked_handwheels_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_spoked_handwheel_contract.json"
        ))
        .expect("spoked-handwheel Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-spoked-handwheel-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["native_expected_cases"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");

            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}"
                );
            }
            let actual_volume = report["volume_mm3"].as_f64().expect("measured B-Rep volume");
            let bbox_volume = dimensions
                .iter()
                .map(|value| value.as_f64().unwrap())
                .product::<f64>();
            assert!(
                actual_volume > 0.0 && actual_volume < bbox_volume,
                "{input}: invalid volume {actual_volume}; {report}"
            );
            if let Some(expected_volume) = case["brep_volume_mm3"].as_f64() {
                assert!(
                    (actual_volume - expected_volume).abs() <= 0.01,
                    "{input}: B-Rep volume {actual_volume}, expected {expected_volume}; {report}"
                );
            }

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), case["feature_count"].as_u64().unwrap() as usize, "{input}: {output}");
            assert_eq!(features[0]["kind"], "spoked_wheel", "{input}");
            assert_eq!(features[0]["parameters"]["spokes"], case["spokes"], "{input}");

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(
                loaded["cadplan"]["features"],
                output["cadplan"]["features"],
                "{input}: AIMODEL round-trip"
            );
            println!(
                "F5.43 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={actual_volume}; legacy_unfused_mesh_volume_mm3={}; spokes={}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                case["legacy_mesh_volume_mm3"],
                case["spokes"],
                output["output_paths"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn legacy_blade_and_blisk_build_reopen_and_export_through_native_quick() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        let root = std::env::temp_dir().join(format!(
            "native-quick-blade-blisk-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");

        for (index, input) in ["叶片 长100 宽60", "叶盘 外径200 厚30 叶片数24 叶展40"]
            .into_iter()
            .enumerate()
        {
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert!(
                report["volume_mm3"].as_f64().unwrap_or_default() > 0.0,
                "{input}: {report}"
            );
            let dimensions = output["stats"]["size"]
                .as_array()
                .unwrap_or_else(|| panic!("{input}: missing size summary: {output}"));
            assert_eq!(dimensions.len(), 3, "{input}: {output}");
            assert!(
                dimensions
                    .iter()
                    .all(|value| value.as_f64().unwrap_or_default() > 0.0)
            );

            let outputs = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                let path = Path::new(outputs[name].as_str().unwrap());
                assert!(
                    path.is_file(),
                    "{input}: expected {name} at {}",
                    path.display()
                );
            }
            let loaded =
                native_model_io::load_aimodel(Path::new(outputs["aimodel"].as_str().unwrap()))
                    .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(
                loaded["features"].as_array().map(Vec::len),
                output["features"].as_array().map(Vec::len),
                "{input}: AIMODEL feature round-trip"
            );
            println!(
                "F5.24 native acceptance: input={input}; dimensions_mm={}; volume_mm3={}; triangles={}; files={:?}",
                serde_json::to_string(&output["stats"]["size"]).unwrap(),
                output["stats"]["volume"],
                output["stats"]["triangles"],
                output["output_paths"]
            );
        }
        fs::remove_dir_all(root).expect("remove native Quick acceptance outputs");
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_44_legacy_camshafts_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_camshaft_contract.json"
        ))
        .expect("camshaft Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-camshaft-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["legacy_accepted"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");

            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}"
                );
            }
            let actual_volume = report["volume_mm3"].as_f64().expect("measured B-Rep volume");
            let bbox_volume = dimensions
                .iter()
                .map(|value| value.as_f64().unwrap())
                .product::<f64>();
            assert!(
                actual_volume > 0.0 && actual_volume < bbox_volume,
                "{input}: invalid volume {actual_volume}; {report}"
            );
            if let Some(expected_volume) = case["brep_volume_mm3"].as_f64() {
                assert!(
                    (actual_volume - expected_volume).abs() <= 0.01,
                    "{input}: B-Rep volume {actual_volume}, expected {expected_volume}; {report}"
                );
            }

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), 1, "{input}: {output}");
            assert_eq!(features[0]["kind"], "camshaft", "{input}");
            let expected = case["native_expected"].as_object().unwrap();
            for (name, value) in expected {
                assert_eq!(features[0]["parameters"][name], *value, "{input}: {name}");
            }

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(
                loaded["cadplan"]["features"],
                output["cadplan"]["features"],
                "{input}: AIMODEL round-trip"
            );
            println!(
                "F5.44 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={actual_volume}; legacy_mesh_poly_count={}; legacy_unfused_mesh_volume_mm3={}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                case["legacy_mesh_poly_count"],
                case["legacy_mesh_volume_mm3"],
                output["output_paths"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_45_legacy_crankshafts_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_crankshaft_contract.json"
        ))
        .expect("crankshaft Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-crankshaft-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}"
                );
            }
            let actual_volume = report["volume_mm3"].as_f64().expect("measured B-Rep volume");
            let bbox_volume = dimensions.iter().map(|value| value.as_f64().unwrap()).product::<f64>();
            assert!(actual_volume > 0.0 && actual_volume < bbox_volume, "{input}: {report}");

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), 1, "{input}: {output}");
            assert_eq!(features[0]["kind"], "crankshaft", "{input}: {output}");
            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.45 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={actual_volume}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                output["output_paths"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_46_legacy_conrods_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_conrod_contract.json"
        ))
        .expect("conrod Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-conrod-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}"
                );
            }
            let volume = report["volume_mm3"].as_f64().expect("measured B-Rep volume");
            let bbox_volume = dimensions.iter().map(|value| value.as_f64().unwrap()).product::<f64>();
            assert!(volume > 0.0 && volume < bbox_volume, "{input}: {report}");

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), 1, "{input}: {output}");
            assert_eq!(features[0]["kind"], "conrod", "{input}: {output}");
            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.46 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={volume}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                output["output_paths"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_47_legacy_cross_joints_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_cross_joint_contract.json"
        ))
        .expect("cross-joint Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-cross-joint-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}"
                );
            }
            let volume = report["volume_mm3"].as_f64().expect("measured B-Rep volume");
            let bbox_volume = dimensions.iter().map(|value| value.as_f64().unwrap()).product::<f64>();
            assert!(volume > 0.0 && volume < bbox_volume, "{input}: {report}");
            if case.get("hole_d_mm").is_some() {
                let holes = report["hole_instances"].as_array().expect("center hole measurements");
                assert_eq!(holes.len(), 1, "{input}: {report}");
                assert_eq!(report["hole_measurement_complete"], true, "{input}: {report}");
                assert_eq!(report["hole_measurements_verified"], true, "{input}: {report}");
            }

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features[0]["kind"], "cross_joint", "{input}: {output}");
            let expected_count = if case.get("hole_d_mm").is_some() { 2 } else { 1 };
            assert_eq!(features.len(), expected_count, "{input}: {output}");
            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.47 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={volume}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                output["output_paths"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_48_legacy_pistons_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_piston_contract.json"
        ))
        .expect("piston Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-piston-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}"
                );
            }
            let volume = report["volume_mm3"].as_f64().expect("measured B-Rep volume");
            let bbox_volume = dimensions.iter().map(|value| value.as_f64().unwrap()).product::<f64>();
            assert!(volume > 0.0 && volume < bbox_volume, "{input}: {report}");

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), 1, "{input}: {output}");
            assert_eq!(features[0]["kind"], "piston", "{input}: {output}");
            assert_eq!(features[0]["parameters"]["grooves"], case["grooves"], "{input}: {output}");
            let support = &report["feature_results"][0]["support_details"];
            assert_eq!(support["grooves_built"], case["grooves"], "{input}: {support}");
            assert_eq!(
                support["pin_bore"]["diameter_mm"],
                case["pin_d_mm"],
                "{input}: {support}"
            );

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.48 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={volume}; support={support}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                output["output_paths"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_49_legacy_worms_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_worm_contract.json"
        ))
        .expect("worm Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-worm-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}"
                );
            }
            let volume = report["volume_mm3"].as_f64().expect("measured B-Rep volume");
            let bbox_volume = dimensions.iter().map(|value| value.as_f64().unwrap()).product::<f64>();
            assert!(volume > 0.0 && volume < bbox_volume, "{input}: {report}");

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            let expected_feature_count = case["feature_count"].as_u64().unwrap() as usize;
            assert_eq!(features.len(), expected_feature_count, "{input}: {output}");
            assert_eq!(features[0]["kind"], "worm", "{input}: {output}");
            let support = &report["feature_results"][0]["support_details"];
            assert_eq!(support["status"], "approximate", "{input}: {support}");
            assert_eq!(support["shaft_profile_points"], 48, "{input}: {support}");
            assert_eq!(support["occt_validation"]["solid_count"], 1, "{input}: {support}");
            assert!(support["turns"].as_f64().unwrap() >= 2.0, "{input}: {support}");
            if expected_feature_count == 2 {
                assert_eq!(features[1]["kind"], "hole", "{input}: {output}");
                assert_eq!(features[1]["operation"], "cut", "{input}: {output}");
            }

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.49 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={volume}; support={support}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                output["output_paths"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_50_legacy_scroll_discs_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_scroll_disc_contract.json"
        ))
        .expect("scroll-disc Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-scroll-disc-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}"
                );
            }
            let volume = report["volume_mm3"].as_f64().expect("measured B-Rep volume");
            let bbox_volume = dimensions.iter().map(|value| value.as_f64().unwrap()).product::<f64>();
            assert!(volume > 0.0 && volume < bbox_volume, "{input}: {report}");

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), 1, "{input}: {output}");
            assert_eq!(features[0]["kind"], "scroll_disc", "{input}: {output}");
            let support = &report["feature_results"][0]["support_details"];
            assert_eq!(support["status"], "approximate", "{input}: {support}");
            assert_eq!(support["profile_segments"], case["segments"], "{input}: {support}");
            assert_eq!(support["profile_points"], 242, "{input}: {support}");
            assert_eq!(support["curve_model"], "sampled_archimedean_polyline", "{input}: {support}");

            let paths = output["output_paths"].as_object().unwrap();
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.50 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={volume}; support={support}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                output["output_paths"]
            );
        }
    }

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_51_legacy_impellers_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_impeller_contract.json"
        ))
        .expect("impeller Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-impeller-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            let expected_dimensions = case["dimensions_mm"].as_array().unwrap();
            assert_eq!(dimensions.len(), 3, "{input}: {report}");
            for (axis, (actual, expected)) in dimensions.iter().zip(expected_dimensions).enumerate() {
                let actual = actual.as_f64().unwrap_or_default();
                let expected = expected.as_f64().unwrap();
                assert!(
                    (actual - expected).abs() <= 1e-5,
                    "{input}: bbox axis {axis} = {actual}, expected {expected}; {report}"
                );
            }
            let volume = report["volume_mm3"].as_f64().expect("measured B-Rep volume");
            let bbox_volume = dimensions.iter().map(|value| value.as_f64().unwrap()).product::<f64>();
            assert!(volume > 0.0 && volume < bbox_volume, "{input}: {report}");

            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(
                features.len(),
                case["feature_count"].as_u64().unwrap() as usize,
                "{input}: {output}"
            );
            assert_eq!(features[0]["kind"], "impeller", "{input}: {output}");
            if features.len() == 2 {
                assert_eq!(features[1]["kind"], "hole", "{input}: {output}");
                assert_eq!(features[1]["operation"], "cut", "{input}: {output}");
                assert_eq!(features[1]["parameters"]["diameter"], case["hole_diameter"], "{input}: {output}");
                assert_eq!(features[1]["parameters"]["through"], true, "{input}: {output}");
                assert_eq!(output["geometry_report"]["hole_instances"].as_array().unwrap().len(), case["hole_count"].as_u64().unwrap() as usize, "{input}: {output}");
            }
            let parameters = &features[0]["parameters"];
            for (name, expected) in [
                ("hub_d", &case["hub_d"]),
                ("tip_d", &case["tip_d"]),
                ("height", &case["height"]),
                ("blades", &case["blade_count"]),
                ("twist_deg", &case["twist_deg"]),
            ] {
                assert_eq!(&parameters[name], expected, "{input}: {name}");
            }
            let support = &report["feature_results"][0]["support_details"];
            assert_eq!(support["status"], "approximate", "{input}: {support}");
            assert_eq!(support["blade_count"], case["blade_count"], "{input}: {support}");
            assert_eq!(support["blade_t_defaulted"], true, "{input}: {support}");
            assert_eq!(support["occt_validation"]["valid"], true, "{input}: {support}");
            assert_eq!(support["occt_validation"]["closed_solid_count"], 1, "{input}: {support}");

            let paths = output["output_paths"].as_object().expect("generated output paths");
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.51 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={volume}; support={support}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                output["output_paths"]
            );
        }
    }
}

#[cfg(test)]
mod f5_52_legacy_rotor_native_acceptance {
    use super::build;
    use serde_json::Value;
    use std::{fs, path::Path, time::SystemTime};

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_52_legacy_rotors_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_rotor_contract.json"
        ))
        .expect("rotor Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-rotor-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            let dimensions = report["dimensions_mm"].as_array().expect("measured dimensions");
            for (axis, (actual, expected)) in dimensions
                .iter()
                .zip(case["dimensions_mm"].as_array().unwrap())
                .enumerate()
            {
                assert!(
                    (actual.as_f64().unwrap() - expected.as_f64().unwrap()).abs() <= 1e-5,
                    "{input}: bbox axis {axis} differs: {report}"
                );
            }
            let volume = report["volume_mm3"].as_f64().expect("measured B-Rep volume");
            assert!(volume > 0.0, "{input}: {report}");
            let features = output["cadplan"]["features"].as_array().expect("CADPlan features");
            assert_eq!(features.len(), case["feature_count"].as_u64().unwrap() as usize, "{input}");
            assert_eq!(features[0]["kind"], "rotor", "{input}");
            for name in ["radius", "height", "bore_d"] {
                assert_eq!(features[0]["parameters"][name], case[name], "{input}: {name}");
            }
            let paths = output["output_paths"].as_object().expect("generated output paths");
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = crate::native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.52 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={volume}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                output["output_paths"]
            );
        }
    }
}

#[cfg(test)]
mod f5_53_legacy_valve_native_acceptance {
    use super::build;
    use serde_json::Value;
    use std::{fs, path::Path, time::SystemTime};

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_53_legacy_valves_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_valve_contract.json"
        ))
        .expect("valve Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-valve-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            for (axis, (actual, expected)) in report["dimensions_mm"]
                .as_array()
                .unwrap()
                .iter()
                .zip(case["dimensions_mm"].as_array().unwrap())
                .enumerate()
            {
                assert!(
                    (actual.as_f64().unwrap() - expected.as_f64().unwrap()).abs() <= 1e-5,
                    "{input}: bbox axis {axis} differs: {report}"
                );
            }
            assert!(report["volume_mm3"].as_f64().unwrap_or_default() > 0.0, "{input}: {report}");
            let features = output["cadplan"]["features"].as_array().unwrap();
            assert_eq!(features.len(), case["feature_count"].as_u64().unwrap() as usize, "{input}");
            assert_eq!(features[0]["kind"], "cylinder", "{input}");
            assert_eq!(features[1]["kind"], "cylinder", "{input}");
            assert_eq!(features[0]["parameters"]["height"], case["length"], "{input}");
            assert_eq!(features[0]["parameters"]["radius"], case["rod_d"].as_f64().unwrap() / 2.0, "{input}");
            assert_eq!(features[1]["parameters"]["radius"], case["head_d"].as_f64().unwrap() / 2.0, "{input}");
            assert_eq!(features[1]["parameters"]["height"], case["head_height"], "{input}");
            assert_eq!(features[0]["parameters"]["center"], serde_json::json!([0.0, 0.0, 0.0]), "{input}");
            assert_eq!(features[1]["parameters"]["center"], serde_json::json!([0.0, 0.0, 0.0]), "{input}");
            let paths = output["output_paths"].as_object().expect("generated output paths");
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = crate::native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.53 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                report["volume_mm3"],
                output["output_paths"]
            );
        }
    }
}

#[cfg(test)]
mod f5_54_legacy_spacer_pad_native_acceptance {
    use super::build;
    use serde_json::Value;
    use std::{fs, path::Path, time::SystemTime};

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_54_legacy_spacer_pads_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(
            manifest.is_file(),
            "missing OCCT manifest: {}",
            manifest.display()
        );
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_spacer_pad_contract.json"
        ))
        .expect("spacer-pad Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-spacer-pad-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"]
            .as_array()
            .unwrap()
            .iter()
            .enumerate()
        {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            for (axis, (actual, expected)) in report["dimensions_mm"]
                .as_array()
                .unwrap()
                .iter()
                .zip(case["dimensions_mm"].as_array().unwrap())
                .enumerate()
            {
                assert!(
                    (actual.as_f64().unwrap() - expected.as_f64().unwrap()).abs() <= 1e-5,
                    "{input}: bbox axis {axis} differs: {report}"
                );
            }
            assert!(
                report["volume_mm3"].as_f64().unwrap_or_default() > 0.0,
                "{input}: {report}"
            );
            let features = output["cadplan"]["features"].as_array().unwrap();
            assert_eq!(
                features.len(),
                case["feature_count"].as_u64().unwrap() as usize,
                "{input}"
            );
            assert_eq!(features[0]["kind"], "box", "{input}: {output}");
            assert_eq!(features[0]["operation"], "add", "{input}: {output}");
            let paths = output["output_paths"]
                .as_object()
                .expect("generated output paths");
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(
                    Path::new(paths[name].as_str().unwrap()).is_file(),
                    "{input}: missing {name}"
                );
            }
            let loaded =
                crate::native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                    .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(
                loaded["cadplan"]["features"], output["cadplan"]["features"],
                "{input}: AIMODEL round-trip"
            );
            println!(
                "F5.54 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                report["volume_mm3"],
                output["output_paths"]
            );
        }
    }
}

#[cfg(test)]
mod f5_55_legacy_spring_washer_native_acceptance {
    use super::build;
    use serde_json::Value;
    use std::{fs, path::Path, time::SystemTime};

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_55_legacy_spring_washers_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_spring_washer_contract.json"
        ))
        .expect("spring-washer Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-spring-washer-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            for (axis, (actual, expected)) in report["dimensions_mm"]
                .as_array()
                .unwrap()
                .iter()
                .zip(case["dimensions_mm"].as_array().unwrap())
                .enumerate()
            {
                assert!(
                    (actual.as_f64().unwrap() - expected.as_f64().unwrap()).abs() <= 1e-5,
                    "{input}: bbox axis {axis} differs: {report}"
                );
            }
            assert!(report["volume_mm3"].as_f64().unwrap_or_default() > 0.0, "{input}: {report}");
            let features = output["cadplan"]["features"].as_array().unwrap();
            assert_eq!(features.len(), case["feature_count"].as_u64().unwrap() as usize, "{input}");
            assert_eq!(features[0]["kind"], "cylinder", "{input}: {output}");
            assert_eq!(features[1]["kind"], "hole", "{input}: {output}");
            assert_eq!(features[1]["operation"], "cut", "{input}: {output}");
            assert_eq!(features[1]["parameters"]["through"], true, "{input}: {output}");
            let paths = output["output_paths"].as_object().expect("generated output paths");
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = crate::native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.55 native acceptance: input={input}; dimensions_mm={}; native_brep_volume_mm3={}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                report["volume_mm3"],
                output["output_paths"]
            );
        }
    }
}

#[cfg(test)]
mod f5_56_legacy_bent_part_native_acceptance {
    use super::build;
    use serde_json::Value;
    use std::{fs, path::Path, time::SystemTime};

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_56_legacy_bent_parts_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_bent_part_contract.json"
        ))
        .expect("bent-part Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-bent-part-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            for (axis, (actual, expected)) in report["dimensions_mm"]
                .as_array()
                .unwrap()
                .iter()
                .zip(case["dimensions_mm"].as_array().unwrap())
                .enumerate()
            {
                assert!(
                    (actual.as_f64().unwrap() - expected.as_f64().unwrap()).abs() <= 1e-5,
                    "{input}: bbox axis {axis} differs: {report}"
                );
            }
            assert!(
                (report["volume_mm3"].as_f64().unwrap() - case["volume_mm3"].as_f64().unwrap()).abs()
                    <= 1e-4,
                "{input}: volume differs: {report}"
            );
            let features = output["cadplan"]["features"].as_array().unwrap();
            assert_eq!(features.len(), case["feature_count"].as_u64().unwrap() as usize, "{input}");
            assert_eq!(features[0]["kind"], "prism", "{input}: {output}");
            if features.len() == 2 {
                assert_eq!(features[1]["kind"], "hole", "{input}: {output}");
                assert_eq!(features[1]["operation"], "cut", "{input}: {output}");
                assert_eq!(features[1]["parameters"]["pos"], "center", "{input}: {output}");
                if case["hole_expected_noop"] == true {
                    let hole_result = &report["feature_results"][1];
                    assert_eq!(hole_result["state"], "no_op", "{input}: {report}");
                    assert!(
                        (hole_result["volume_before_mm3"].as_f64().unwrap()
                            - hole_result["volume_after_mm3"].as_f64().unwrap())
                        .abs()
                            <= 1e-4,
                        "{input}: center bore unexpectedly changed the stock volume: {report}"
                    );
                    assert!(
                        hole_result["warning"].as_str().is_some_and(|warning| warning.contains("没有移除材料")),
                        "{input}: missing explicit no-op warning: {report}"
                    );
                }
            }
            let paths = output["output_paths"].as_object().expect("generated output paths");
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = crate::native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.56 native acceptance: input={input}; dimensions_mm={}; volume_mm3={}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                report["volume_mm3"],
                output["output_paths"]
            );
        }
    }
}

#[cfg(test)]
mod f5_57_legacy_base_plate_native_acceptance {
    use super::build;
    use serde_json::Value;
    use std::{fs, path::Path, time::SystemTime};

    #[test]
    #[ignore = "requires the staged local OCCT runtime manifest"]
    fn f5_57_legacy_base_plates_build_measure_reopen_and_export() {
        let manifest = std::env::var_os("AIMODELER_OCCT_MANIFEST")
            .map(std::path::PathBuf::from)
            .expect("set AIMODELER_OCCT_MANIFEST to the staged OCCT runtime-manifest.json");
        assert!(manifest.is_file(), "missing OCCT manifest: {}", manifest.display());
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/quick_legacy_base_plate_contract.json"
        ))
        .expect("base-plate Python oracle fixture is valid JSON");
        let root = std::env::temp_dir().join(format!(
            "native-quick-base-plate-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system clock")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create native Quick output root");
        struct RemoveOnDrop(std::path::PathBuf);
        impl Drop for RemoveOnDrop {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveOnDrop(root.clone());

        for (index, case) in fixture["geometry_acceptance"].as_array().unwrap().iter().enumerate() {
            let input = case["input"].as_str().unwrap();
            let output = build(input, index as i64 + 1, &manifest, &root)
                .unwrap_or_else(|error| panic!("{input}: native Quick build failed: {error}"));
            assert_eq!(output["state"], "complete", "{input}: {output}");
            let report = &output["geometry_report"];
            assert_eq!(report["state"], "passed", "{input}: {report}");
            assert_eq!(report["valid"], true, "{input}: {report}");
            assert_eq!(report["closed_solid_count"], 1, "{input}: {report}");
            for (axis, (actual, expected)) in report["dimensions_mm"]
                .as_array()
                .unwrap()
                .iter()
                .zip(case["dimensions_mm"].as_array().unwrap())
                .enumerate()
            {
                assert!(
                    (actual.as_f64().unwrap() - expected.as_f64().unwrap()).abs() <= 1e-5,
                    "{input}: bbox axis {axis} differs: {report}"
                );
            }
            assert!(
                (report["volume_mm3"].as_f64().unwrap() - case["volume_mm3"].as_f64().unwrap()).abs()
                    <= 1e-3,
                "{input}: volume differs: {report}"
            );
            let features = output["cadplan"]["features"].as_array().unwrap();
            assert_eq!(features.len(), case["feature_count"].as_u64().unwrap() as usize, "{input}: {output}");
            assert_eq!(features[0]["kind"], "box", "{input}: {output}");
            assert_eq!(features[0]["parameters"]["base"], case["base"], "{input}: {output}");
            if features.len() == 2 {
                assert_eq!(features[1]["kind"], "hole", "{input}: {output}");
                assert_eq!(features[1]["operation"], "cut", "{input}: {output}");
                assert_eq!(report["feature_results"][1]["state"], "built", "{input}: {report}");
            }
            let paths = output["output_paths"].as_object().expect("generated output paths");
            for name in ["glb", "aimodel", "stl", "obj"] {
                assert!(Path::new(paths[name].as_str().unwrap()).is_file(), "{input}: missing {name}");
            }
            let loaded = crate::native_model_io::load_aimodel(Path::new(paths["aimodel"].as_str().unwrap()))
                .unwrap_or_else(|error| panic!("{input}: AIMODEL reload failed: {error}"));
            assert_eq!(loaded["cadplan"]["features"], output["cadplan"]["features"], "{input}: AIMODEL round-trip");
            println!(
                "F5.57 native acceptance: input={input}; dimensions_mm={}; volume_mm3={}; files={:?}",
                serde_json::to_string(&report["dimensions_mm"]).unwrap(),
                report["volume_mm3"],
                output["output_paths"]
            );
        }
    }
}
