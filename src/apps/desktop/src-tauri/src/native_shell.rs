//! Explicit, revision-bound planar-face shell selectors for native OCCT.
//!
//! Face ordinals are private bridge details. Model-facing CADPlan features store
//! only opaque selector strings issued by `cad_list_shell_faces`.
use crate::native_occt::{CylindricalFace, FaceSurfaceType, Shape};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::HashSet;

pub const INVENTORY_VERSION: u64 = 3;
const SELECTOR_DOMAIN: &[u8] = b"ai-modeler-shell-face-selector-v1\0";

#[derive(Debug, Clone)]
pub struct SelectorGrant {
    pub revision: i64,
    pub geometry_fingerprint: String,
    pub kernel_version: String,
    pub target_feature_id: String,
    pub ordinal: usize,
    pub surface_type: FaceSurfaceType,
    pub cylinder: Option<CylindricalFace>,
}

fn selectable_cylinder(cylinder: &CylindricalFace) -> bool {
    let angular_span = cylinder.angular_span[1] - cylinder.angular_span[0];
    let axial_span = cylinder.axial_span[1] - cylinder.axial_span[0];
    cylinder.radius.is_finite()
        && cylinder.radius > 0.0
        && angular_span.is_finite()
        && (angular_span - std::f64::consts::TAU).abs() <= 1.0e-7
        && axial_span.is_finite()
        && axial_span > 1.0e-9
}

pub fn binding(
    revision: i64,
    geometry_fingerprint: &str,
    kernel_version: &str,
    target_feature_id: &str,
) -> Value {
    json!({
        "revision": revision,
        "geometry_fingerprint": geometry_fingerprint,
        "kernel_version": kernel_version,
        "inventory_version": INVENTORY_VERSION,
        "target_feature_id": target_feature_id
    })
}

fn binding_fields(binding: &Value) -> Result<(i64, &str, &str, &str), String> {
    let object = binding
        .as_object()
        .ok_or("selection_binding 须为 JSON 对象。")?;
    if object.keys().any(|key| {
        ![
            "revision",
            "geometry_fingerprint",
            "kernel_version",
            "inventory_version",
            "target_feature_id",
        ]
        .contains(&key.as_str())
    }) {
        return Err("selection_binding 只接受 revision、geometry_fingerprint、kernel_version、inventory_version、target_feature_id。".into());
    }
    let revision = binding
        .get("revision")
        .and_then(Value::as_i64)
        .filter(|value| *value >= 0)
        .ok_or("selection_binding.revision 须为非负整数。")?;
    let fingerprint = binding
        .get("geometry_fingerprint")
        .and_then(Value::as_str)
        .filter(|value| is_sha256(value))
        .ok_or("selection_binding.geometry_fingerprint 须为64位小写SHA-256。")?;
    let kernel_version = binding
        .get("kernel_version")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or("selection_binding.kernel_version 须为非空字符串。")?;
    if binding.get("inventory_version").and_then(Value::as_u64) != Some(INVENTORY_VERSION) {
        return Err("selection_binding.inventory_version 不受支持。".into());
    }
    let target_feature_id = binding
        .get("target_feature_id")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or("selection_binding.target_feature_id 须为非空字符串。")?;
    Ok((revision, fingerprint, kernel_version, target_feature_id))
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

/// A stable, opaque id bound to one revision, geometry, kernel and local face.
pub fn selector_id(binding: &Value, ordinal: usize) -> Result<String, String> {
    let (revision, fingerprint, kernel_version, target_feature_id) = binding_fields(binding)?;
    let mut hash = Sha256::new();
    hash.update(SELECTOR_DOMAIN);
    hash.update(revision.to_string().as_bytes());
    hash.update([0]);
    hash.update(fingerprint.as_bytes());
    hash.update([0]);
    hash.update(kernel_version.as_bytes());
    hash.update([0]);
    hash.update(target_feature_id.as_bytes());
    hash.update([0]);
    hash.update(ordinal.to_string().as_bytes());
    Ok(format!("shell-face-v1:{:x}", hash.finalize()))
}

pub fn validate_request_feature(
    feature: &Value,
    current_revision: i64,
    current_fingerprint: &str,
    current_kernel_version: &str,
    current_target_feature_id: &str,
    grants: &std::collections::HashMap<String, SelectorGrant>,
) -> Result<(), String> {
    if feature.get("kind").and_then(Value::as_str) != Some("shell") {
        return Ok(());
    }
    if feature.get("operation").and_then(Value::as_str) != Some("cut") {
        return Err("显式 shell 必须使用 operation=cut。".into());
    }
    let parameters = feature
        .get("parameters")
        .and_then(Value::as_object)
        .ok_or("shell.parameters 须为 JSON 对象。")?;
    let required = ["thickness", "offset_mode", "removed_faces"];
    if let Some(name) = required.iter().find(|key| !parameters.contains_key(**key)) {
        return Err(format!(
            "显式 shell 缺少必需参数 {name}；旧 thickness-only shell 不会自动迁移。"
        ));
    }
    if parameters
        .get("thickness")
        .and_then(Value::as_f64)
        .is_none_or(|v| !v.is_finite() || v <= 0.0)
    {
        return Err("shell.thickness 须为正有限长度。".into());
    }
    if !parameters
        .get("offset_mode")
        .and_then(Value::as_str)
        .is_some_and(|value| ["inward", "outward"].contains(&value))
    {
        return Err("shell.offset_mode 须明确为 inward 或 outward。".into());
    }
    let thickness = parameters.get("thickness").and_then(Value::as_f64).unwrap();
    let mut selected_cylinders = 0usize;
    let shell_targets: Vec<&Value> = feature
        .get("relations")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|row| row.get("type").and_then(Value::as_str) == Some("shell_target"))
        .collect();
    if shell_targets.len() != 1
        || shell_targets[0].get("feature_id").and_then(Value::as_str)
            != Some(current_target_feature_id)
    {
        return Err(
            "显式 shell 必须以唯一 shell_target relation 指向当前枚举的 plan-head feature。".into(),
        );
    }
    let selectors = parameters
        .get("removed_faces")
        .and_then(Value::as_array)
        .filter(|rows| !rows.is_empty() && rows.len() <= 256)
        .ok_or("shell.removed_faces 须含1到256个面清单工具返回的opaque selector。")?;
    let binding = feature
        .get("selection_binding")
        .ok_or("显式 shell 必须包含由 cad_list_shell_faces 返回的 selection_binding。")?;
    let (bound_revision, bound_fingerprint, bound_kernel, bound_target) = binding_fields(binding)?;
    if bound_revision != current_revision
        || bound_fingerprint != current_fingerprint
        || bound_kernel != current_kernel_version
        || bound_target != current_target_feature_id
    {
        return Err(
            "shell 面选择已过期：必须从当前几何 revision 重新调用 cad_list_shell_faces。".into(),
        );
    }
    let mut unique = HashSet::new();
    for selector in selectors {
        let selector = selector
            .as_str()
            .filter(|value| value.starts_with("shell-face-v1:"))
            .ok_or("removed_faces 中只能包含面清单工具返回的opaque selector。")?;
        if !unique.insert(selector) {
            return Err("shell.removed_faces 包含重复 selector；不允许多重匹配。".into());
        }
        let grant = grants
            .get(selector)
            .ok_or("shell selector 未由当前会话的 cad_list_shell_faces 工具返回。")?;
        if grant.revision != current_revision
            || grant.geometry_fingerprint != current_fingerprint
            || grant.kernel_version != current_kernel_version
            || grant.target_feature_id != current_target_feature_id
        {
            return Err("shell 面选择来自过期的 revision 或几何 fingerprint。".into());
        }
        match grant.surface_type {
            FaceSurfaceType::Plane => {}
            FaceSurfaceType::Cylinder => {
                selected_cylinders += 1;
                let cylinder = grant
                    .cylinder
                    .as_ref()
                    .ok_or("shell 圆柱面选择缺少 OCCT 几何证据。")?;
                if !selectable_cylinder(cylinder) {
                    return Err("shell 仅支持完整 360°、具有正轴向范围的圆柱面。".into());
                }
                if thickness >= cylinder.radius {
                    return Err("shell 圆柱面厚度必须小于圆柱半径。".into());
                }
            }
            FaceSurfaceType::Unsupported => {
                return Err("shell 曲面类型不受支持；仅允许平面或完整 360° 圆柱面。".into());
            }
        }
    }
    if selected_cylinders > 0 && selectors.len() != 1 {
        return Err("一个 shell 操作只能移除一个完整圆柱面；曲面选择不能与其它面混合。".into());
    }
    Ok(())
}

pub fn inventory(
    shape: &Shape,
    revision: i64,
    geometry_fingerprint: &str,
    kernel_version: &str,
    target_feature_id: &str,
) -> Result<(Value, Vec<(String, SelectorGrant)>), String> {
    let analysis = shape.analyze()?;
    if analysis.valid != 1 || analysis.solid_count != 1 || analysis.closed_solid_count != 1 {
        return Err("cad_list_shell_faces 只允许当前唯一有效闭合实体。".into());
    }
    let binding = binding(
        revision,
        geometry_fingerprint,
        kernel_version,
        target_feature_id,
    );
    let faces = shape.faces()?;
    let mut grants = Vec::new();
    let mut rows = Vec::with_capacity(faces.len());
    for face in faces {
        let cylinder_selectable = face.surface_type == FaceSurfaceType::Cylinder
            && face.cylinder.as_ref().is_some_and(selectable_cylinder);
        let selectable = face.surface_type == FaceSurfaceType::Plane || cylinder_selectable;
        let selector = if selectable {
            let selector = selector_id(&binding, face.ordinal)?;
            grants.push((
                selector.clone(),
                SelectorGrant {
                    revision,
                    geometry_fingerprint: geometry_fingerprint.into(),
                    kernel_version: kernel_version.into(),
                    target_feature_id: target_feature_id.into(),
                    ordinal: face.ordinal,
                    surface_type: face.surface_type,
                    cylinder: face.cylinder,
                },
            ));
            Value::String(selector)
        } else {
            Value::Null
        };
        rows.push(json!({
            "selector": selector,
            "selectable": selectable,
            "surface_type": match face.surface_type {
                FaceSurfaceType::Plane => "planar",
                FaceSurfaceType::Cylinder if cylinder_selectable => "cylindrical_full_turn",
                FaceSurfaceType::Cylinder | FaceSurfaceType::Unsupported => "unsupported"
            },
            "area_mm2": face.area,
            "center_mm": face.center,
            "oriented_normal": face.oriented_normal,
            "cylinder": face.cylinder.map(|cylinder| json!({
                "radius_mm":cylinder.radius,
                "axis_origin_mm":cylinder.axis_origin,
                "axis_direction":cylinder.axis_direction,
                "axial_span_mm":cylinder.axial_span,
                "angular_span_rad":cylinder.angular_span
            })),
            "unsupported_reason": if selectable {Value::Null} else if face.surface_type == FaceSurfaceType::Cylinder {json!("仅支持完整 360°、具有正轴向范围的圆柱面")} else if face.surface_type == FaceSurfaceType::Plane {Value::Null} else {json!("仅支持平面或完整 360° 圆柱面")}
        }));
    }
    Ok((
        json!({
            "state":"face_inventory",
            "revision":revision,
            "geometry_fingerprint":geometry_fingerprint,
            "kernel_version":kernel_version,
            "selection_binding":binding,
            "target":"current_unique_closed_solid",
            "target_feature_id":target_feature_id,
            "supported_surface_types":["planar","cylindrical_full_turn"],
            "faces":rows
        }),
        grants,
    ))
}

/// Return the explicit feature id whose result is the current plan-head solid.
pub fn current_target_feature_id(plan: &Value) -> Result<String, String> {
    plan.get("features")
        .and_then(Value::as_array)
        .and_then(|features| {
            features
                .iter()
                .rev()
                .find(|feature| feature.get("enabled").and_then(Value::as_bool) != Some(false))
        })
        .and_then(|feature| feature.get("id").and_then(Value::as_str))
        .filter(|feature_id| !feature_id.trim().is_empty())
        .map(str::to_owned)
        .ok_or_else(|| "CADPlan 当前没有可绑定的有效 plan-head feature。".into())
}

/// Re-resolve deterministic opaque selectors against the exact pre-shell shape.
/// This is also checked independently of the session cache during every build.
pub fn resolve_removed_faces(
    shape: &Shape,
    binding: &Value,
    selectors: &[Value],
    expected_fingerprint: &str,
    kernel_version: &str,
    expected_target_feature_id: &str,
    thickness: f64,
) -> Result<Vec<usize>, String> {
    let (_, bound_fingerprint, bound_kernel, bound_target) = binding_fields(binding)?;
    if bound_fingerprint != expected_fingerprint
        || bound_kernel != kernel_version
        || bound_target != expected_target_feature_id
    {
        return Err(
            "shell selector 的前置 target、几何 fingerprint 或 OCCT kernel version 已失配。".into(),
        );
    }
    let mut unique = HashSet::new();
    let faces = shape.faces()?;
    let mut ordinals = Vec::with_capacity(selectors.len());
    let mut selected_cylinders = 0usize;
    for selector in selectors {
        let selector = selector
            .as_str()
            .ok_or("shell selector 须为opaque字符串。")?;
        if !unique.insert(selector) {
            return Err("shell.removed_faces 含重复 selector。".into());
        }
        let mut matches = faces.iter().filter(|face| {
            selector_id(binding, face.ordinal).is_ok_and(|candidate| candidate == selector)
        });
        let face = matches
            .next()
            .ok_or("shell selector 与当前面清单零匹配或已过期。")?;
        if matches.next().is_some() {
            return Err("shell selector 与当前面清单多重匹配。".into());
        }
        match face.surface_type {
            FaceSurfaceType::Plane => {}
            FaceSurfaceType::Cylinder => {
                selected_cylinders += 1;
                let cylinder = face
                    .cylinder
                    .as_ref()
                    .ok_or("shell 圆柱选择缺少 OCCT 几何证据。")?;
                if !selectable_cylinder(cylinder) {
                    return Err("shell 仅支持完整 360°、具有正轴向范围的圆柱面。".into());
                }
                if !thickness.is_finite() || thickness <= 0.0 || thickness >= cylinder.radius {
                    return Err("shell 圆柱面厚度必须为正且小于圆柱半径。".into());
                }
            }
            FaceSurfaceType::Unsupported => {
                return Err("shell 只支持平面或完整 360° 圆柱面。".into());
            }
        }
        ordinals.push(face.ordinal);
    }
    if ordinals.is_empty() {
        return Err("shell 必须至少移除一个面。".into());
    }
    if selected_cylinders > 0 && ordinals.len() != 1 {
        return Err("一个 shell 操作只能移除一个完整圆柱面；曲面选择不能与其它面混合。".into());
    }
    Ok(ordinals)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::native_occt::{Kernel, ShellDirection};
    use std::path::PathBuf;

    fn cylinder(radius: f64, angular_span: [f64; 2], axial_span: [f64; 2]) -> CylindricalFace {
        CylindricalFace {
            radius,
            axis_origin: [0.0, 0.0, 0.0],
            axis_direction: [0.0, 0.0, 1.0],
            axial_span,
            angular_span,
            surface_point: [radius, 0.0, 0.0],
            oriented_normal: [1.0, 0.0, 0.0],
        }
    }

    #[test]
    fn curved_shell_allowlist_requires_one_full_turn_and_positive_axial_extent() {
        assert!(selectable_cylinder(&cylinder(
            4.0,
            [0.0, std::f64::consts::TAU],
            [0.0, 20.0]
        )));
        assert!(!selectable_cylinder(&cylinder(
            4.0,
            [0.0, std::f64::consts::PI],
            [0.0, 20.0]
        )));
        assert!(!selectable_cylinder(&cylinder(
            4.0,
            [0.0, std::f64::consts::TAU],
            [2.0, 2.0]
        )));
        assert!(!selectable_cylinder(&cylinder(
            f64::NAN,
            [0.0, std::f64::consts::TAU],
            [0.0, 20.0]
        )));
    }

    #[test]
    fn current_revision_cylindrical_shell_builds_one_valid_measured_solid_when_occt_is_staged() {
        let Some(manifest) = std::env::var_os("AIMODELER_OCCT_MANIFEST").map(PathBuf::from) else {
            eprintln!("skipped: AIMODELER_OCCT_MANIFEST is not configured");
            return;
        };
        let kernel = Kernel::load_manifest(&manifest).unwrap();
        let stock = kernel.make_box([20.0, 20.0, 20.0], [0.0; 3]).unwrap();
        let cutter = kernel
            .cylinder(4.0, 30.0, [0.0, 0.0, -15.0], [0.0, 0.0, 1.0])
            .unwrap();
        let bored = kernel.cut(&stock, &[&cutter]).unwrap();
        let fingerprint = "a".repeat(64);
        let revision = 7;
        let target = "bored";
        let (face_inventory, grant_rows) =
            inventory(&bored, revision, &fingerprint, &kernel.version(), target).unwrap();
        let face_rows = face_inventory["faces"].as_array().unwrap();
        let cylinder_rows: Vec<&Value> = face_rows
            .iter()
            .filter(|row| row["surface_type"] == "cylindrical_full_turn")
            .collect();
        assert_eq!(cylinder_rows.len(), 1);
        assert_eq!(cylinder_rows[0]["cylinder"]["radius_mm"], 4.0);
        assert_eq!(
            cylinder_rows[0]["cylinder"]["angular_span_rad"][1]
                .as_f64()
                .unwrap()
                - cylinder_rows[0]["cylinder"]["angular_span_rad"][0]
                    .as_f64()
                    .unwrap(),
            std::f64::consts::TAU
        );
        let selector = cylinder_rows[0]["selector"].as_str().unwrap();
        let grants: std::collections::HashMap<String, SelectorGrant> =
            grant_rows.into_iter().collect();
        let shell_binding = face_inventory["selection_binding"].clone();
        let feature = json!({
            "id":"shell-1", "kind":"shell", "operation":"cut",
            "parameters":{"thickness":1.0,"offset_mode":"inward","removed_faces":[selector]},
            "selection_binding":shell_binding,
            "relations":[{"type":"shell_target","feature_id":target}]
        });
        validate_request_feature(
            &feature,
            revision,
            &fingerprint,
            &kernel.version(),
            target,
            &grants,
        )
        .unwrap();
        let planar_selector = face_rows
            .iter()
            .find(|row| row["surface_type"] == "planar")
            .unwrap()["selector"]
            .clone();
        let mut mixed_feature = feature.clone();
        mixed_feature["parameters"]["removed_faces"] = json!([selector, planar_selector]);
        let mixed_error = validate_request_feature(
            &mixed_feature,
            revision,
            &fingerprint,
            &kernel.version(),
            target,
            &grants,
        )
        .unwrap_err();
        assert!(
            mixed_error.contains("不能与其它面混合"),
            "unexpected mixed-face error: {mixed_error}"
        );
        let ordinals = resolve_removed_faces(
            &bored,
            &feature["selection_binding"],
            &feature["parameters"]["removed_faces"]
                .as_array()
                .unwrap()
                .clone(),
            &fingerprint,
            &kernel.version(),
            target,
            1.0,
        )
        .unwrap();
        assert_eq!(ordinals.len(), 1);
        assert!(
            validate_request_feature(
                &feature,
                revision + 1,
                &fingerprint,
                &kernel.version(),
                target,
                &grants,
            )
            .unwrap_err()
            .contains("过期")
        );
        let slot = kernel.make_box([8.0, 2.0, 20.0], [6.0, 0.0, 0.0]).unwrap();
        let opened_bore = kernel.cut(&bored, &[&slot]).unwrap();
        let partial_binding = binding(revision, &"b".repeat(64), &kernel.version(), target);
        let (partial_inventory, _) = inventory(
            &opened_bore,
            revision,
            &"b".repeat(64),
            &kernel.version(),
            target,
        )
        .unwrap();
        let partial_row = partial_inventory["faces"]
            .as_array()
            .unwrap()
            .iter()
            .find(|row| {
                row["cylinder"].is_object()
                    && row["surface_type"] == "unsupported"
                    && row["selectable"] == false
            })
            .expect("slot must trim at least one cylindrical face below full angular coverage");
        assert_eq!(partial_row["selector"], Value::Null);
        let partial_face = opened_bore
            .faces()
            .unwrap()
            .into_iter()
            .find(|face| {
                face.cylinder
                    .as_ref()
                    .is_some_and(|cylinder| !selectable_cylinder(cylinder))
            })
            .unwrap();
        let forged_partial_selector = selector_id(&partial_binding, partial_face.ordinal).unwrap();
        assert!(
            resolve_removed_faces(
                &opened_bore,
                &partial_binding,
                &[Value::String(forged_partial_selector)],
                &"b".repeat(64),
                &kernel.version(),
                target,
                1.0,
            )
            .unwrap_err()
            .contains("完整 360°")
        );
        assert!(
            resolve_removed_faces(
                &bored,
                &feature["selection_binding"],
                &feature["parameters"]["removed_faces"]
                    .as_array()
                    .unwrap()
                    .clone(),
                &fingerprint,
                &kernel.version(),
                target,
                4.0,
            )
            .unwrap_err()
            .contains("小于圆柱半径")
        );

        let result = kernel
            .make_thick_solid(&bored, &ordinals, 1.0, ShellDirection::Inward)
            .unwrap();
        let analysis = result.analyze().unwrap();
        assert_eq!(analysis.valid, 1);
        assert_eq!(analysis.solid_count, 1);
        assert_eq!(analysis.closed_solid_count, 1);
        let input_volume = bored.volume().unwrap();
        let output_volume = result.volume().unwrap();
        assert!(output_volume.is_finite() && output_volume > 0.0);
        assert!((output_volume - input_volume).abs() > 1e-6);
        let bounds = result.bounds().unwrap();
        assert!(bounds.xmin.is_finite() && bounds.xmax.is_finite());
        assert!(bounds.ymin.is_finite() && bounds.ymax.is_finite());
        assert!(bounds.zmin.is_finite() && bounds.zmax.is_finite());
        assert!(
            bounds.xmin < bounds.xmax && bounds.ymin < bounds.ymax && bounds.zmin < bounds.zmax
        );
        assert!(bounds.xmin >= -10.000001 && bounds.xmax <= 10.000001);
        assert!(bounds.ymin >= -10.000001 && bounds.ymax <= 10.000001);
        assert!(bounds.zmin >= -10.000001 && bounds.zmax <= 10.000001);
    }

    #[test]
    fn opaque_selector_is_bound_to_revision_geometry_and_kernel() {
        let a = binding(4, &"a".repeat(64), "7.9.3", "stock");
        let same = binding(4, &"a".repeat(64), "7.9.3", "stock");
        let next_revision = binding(5, &"a".repeat(64), "7.9.3", "stock");
        let other_geometry = binding(4, &"b".repeat(64), "7.9.3", "stock");
        assert_eq!(selector_id(&a, 2).unwrap(), selector_id(&same, 2).unwrap());
        assert_ne!(
            selector_id(&a, 2).unwrap(),
            selector_id(&next_revision, 2).unwrap()
        );
        assert_ne!(
            selector_id(&a, 2).unwrap(),
            selector_id(&other_geometry, 2).unwrap()
        );
        assert_ne!(
            selector_id(&a, 2).unwrap(),
            selector_id(&binding(4, &"a".repeat(64), "7.9.3", "other-stock"), 2).unwrap()
        );
        assert!(!selector_id(&a, 2).unwrap().contains(":2"));
    }
}
