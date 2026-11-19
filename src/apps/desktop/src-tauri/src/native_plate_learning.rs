//! Learn symmetric four-hole plate layouts from independently measured B-Reps.
use crate::{
    cad_plan, native_active_model, native_geometry_backend, native_model_io, native_plan_geometry,
};
use serde_json::{Value, json};
use std::{
    fs::{self, OpenOptions},
    io::ErrorKind,
    path::Path,
    sync::atomic::AtomicBool,
};

const TOLERANCE_MM: f64 = 0.02;
const MAX_SAMPLES: usize = 128;
const STORE: &str = "plate-layout-samples.json";

fn number(value: &Value) -> Result<f64, String> {
    value
        .as_f64()
        .filter(|n| n.is_finite())
        .ok_or_else(|| "尺寸或计量数据无效。".into())
}

fn name(group: &str) -> Result<&str, String> {
    let group = group.trim();
    if group.is_empty() || group.chars().count() > 60 || group.chars().any(char::is_control) {
        return Err("请输入 1–60 个字符的安装板样本组名称。".into());
    }
    Ok(group)
}

fn rows(data_dir: &Path) -> Result<Vec<Value>, String> {
    let path = data_dir.join(STORE);
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(vec![]),
        Err(error) => return Err(format!("读取安装板样本失败：{error}")),
    };
    let journal: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    journal["samples"]
        .as_array()
        .filter(|rows| journal["schema"] == 1 && rows.len() <= MAX_SAMPLES)
        .cloned()
        .ok_or_else(|| "安装板样本文件格式无效。".into())
}

fn fits(observed: &[(f64, f64)], target: f64) -> Result<(f64, &'static str), String> {
    if observed.len() < 2
        || observed
            .iter()
            .all(|(span, _)| (span - observed[0].0).abs() <= TOLERANCE_MM)
    {
        return Err("这一轴还需要至少两种跨度不同的已测样本。".into());
    }
    let fixed = observed.iter().map(|(_, margin)| margin).sum::<f64>() / observed.len() as f64;
    let ratio = observed
        .iter()
        .map(|(span, margin)| span * margin)
        .sum::<f64>()
        / observed.iter().map(|(span, _)| span * span).sum::<f64>();
    let fixed_ok = observed
        .iter()
        .all(|(_, m)| (m - fixed).abs() <= TOLERANCE_MM);
    let ratio_ok = observed
        .iter()
        .all(|(span, m)| (m - ratio * span).abs() <= TOLERANCE_MM);
    match (fixed_ok, ratio_ok) {
        (true, false) => Ok((fixed, "固定边距")),
        (false, true) => Ok((ratio * target, "按跨度比例")),
        (true, true) if (fixed - ratio * target).abs() <= TOLERANCE_MM => {
            Ok((fixed, "两条规则对当前跨度的预测一致"))
        }
        (true, true) => Err("当前样本无法区分固定边距和比例规则，请再提供不同跨度的样本。".into()),
        _ => Err("该组样本的边距不符合固定值或跨度比例，请核对分组和源模型。".into()),
    }
}

fn snapshot_from_report(report: &Value) -> Result<([f64; 3], [f64; 2], f64), String> {
    if report["valid"] != true
        || report["closed_solid_count"] != 1
        || report["hole_measurements_verified"] != true
    {
        return Err("需要一个有效闭合实体和通过 OCCT 计量的通孔。".into());
    }
    let d = &report["dimensions_mm"];
    let size = [number(&d[0])?, number(&d[1])?, number(&d[2])?];
    let bounds = &report["bounds_mm"];
    let center = [
        (number(&bounds[0])? + number(&bounds[3])?) / 2.0,
        (number(&bounds[1])? + number(&bounds[4])?) / 2.0,
    ];
    let holes = report["hole_instances"]
        .as_array()
        .filter(|holes| {
            holes.len() == 4
                && holes.iter().all(|hole| {
                    hole["status"] == "verified_open"
                        && hole["axis"] == "Z"
                        && hole["through"] == true
                })
        })
        .ok_or("仅支持四个沿 Z 轴的等径通孔。")?;
    let mut coordinates = Vec::new();
    let radius =
        number(&holes[0]["final_measurement"]["matched_cylindrical_faces"][0]["actual_radius"])?;
    for hole in holes {
        let cylinder = &hole["final_measurement"]["matched_cylindrical_faces"][0];
        if (number(&cylinder["actual_radius"])? - radius).abs() > TOLERANCE_MM {
            return Err("四个孔的实测孔径不一致。".into());
        }
        coordinates.push([
            number(&cylinder["axis_origin"][0])? - center[0],
            number(&cylinder["axis_origin"][1])? - center[1],
        ]);
    }
    let span = [
        coordinates.iter().map(|p| p[0].abs()).sum::<f64>() / 4.0,
        coordinates.iter().map(|p| p[1].abs()).sum::<f64>() / 4.0,
    ];
    for x in [-1.0, 1.0] {
        for y in [-1.0, 1.0] {
            if coordinates
                .iter()
                .filter(|p| {
                    (p[0] - x * span[0]).abs() <= TOLERANCE_MM
                        && (p[1] - y * span[1]).abs() <= TOLERANCE_MM
                })
                .count()
                != 1
            {
                return Err("实测孔阵列须以板面中心为基准，每个象限各一个孔。".into());
            }
        }
    }
    let expected_volume =
        (size[0] * size[1] - 4.0 * std::f64::consts::PI * radius * radius) * size[2];
    if (number(&report["volume_mm3"])? - expected_volume).abs()
        > 0.001_f64.max(expected_volume.abs() * 1e-8)
    {
        return Err("实测体积与普通矩形板减四个圆孔不符。".into());
    }
    Ok((
        size,
        [size[0] / 2.0 - span[0], size[1] / 2.0 - span[1]],
        radius * 2.0,
    ))
}

pub fn learn(data_dir: &Path, manifest: &Path, group: &str, plan: &Value) -> Result<Value, String> {
    let group = name(group)?;
    if plan["units"]["length"] != "mm" {
        return Err("安装板样本须使用毫米单位。".into());
    }
    let features = plan["features"]
        .as_array()
        .ok_or("样本缺少 CADPlan 特征。")?;
    if features
        .first()
        .is_none_or(|f| f["kind"] != "box" || f["operation"] != "add")
        || !(2..=5).contains(&features.len())
        || !features[1..]
            .iter()
            .all(|f| f["kind"] == "hole" && f["operation"] == "cut")
    {
        return Err("样本须由一块矩形板和普通通孔构成。".into());
    }
    let built = native_plan_geometry::build(plan, manifest, &AtomicBool::new(false))?;
    let (size, margins, diameter) = snapshot_from_report(&built.report)?;
    let sample = json!({"group":group,"geometry_fingerprint":built.report["geometry_fingerprint"],
        "size_mm":size,"margins_mm":margins,"diameter_mm":diameter});
    fs::create_dir_all(data_dir).map_err(|e| e.to_string())?;
    let lock = OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(false)
        .open(data_dir.join("plate-layout.lock"))
        .map_err(|e| e.to_string())?;
    lock.lock().map_err(|e| e.to_string())?;
    let mut samples = rows(data_dir)?;
    let duplicate = samples.iter().any(|row| {
        row["group"] == group && row["geometry_fingerprint"] == sample["geometry_fingerprint"]
    });
    if !duplicate {
        if samples.len() >= MAX_SAMPLES {
            return Err("安装板样本库已达到 128 条上限。".into());
        }
        samples.push(sample.clone());
        let bytes = serde_json::to_vec_pretty(&json!({"schema":1,"samples":samples}))
            .map_err(|e| e.to_string())?;
        native_model_io::atomic_write(&data_dir.join(STORE), &bytes)?;
    }
    let n = samples.iter().filter(|row| row["group"] == group).count();
    Ok(json!({"group":group,"samples":n,"sample":sample,"duplicate":duplicate}))
}

pub fn plan_for_plate(
    size: [f64; 3],
    diameter: f64,
    margins: [f64; 2],
    title: &str,
) -> Result<Value, String> {
    if size
        .iter()
        .chain(&margins)
        .chain(&[diameter])
        .any(|v| !v.is_finite() || *v <= 0.0)
        || (0..2)
            .any(|i| margins[i] <= diameter / 2.0 || size[i] / 2.0 - margins[i] <= diameter / 2.0)
    {
        return Err("尺寸无效，或预测孔位会相交、穿出板边。".into());
    }
    let source = |keys: &[&str]| {
        Value::Object(
            keys.iter()
                .map(|key| {
                    (
                        (*key).into(),
                        json!({"type":"derived",
            "note":"安装板布局输入与实测样本规则"}),
                    )
                })
                .collect(),
        )
    };
    let mut plan = cad_plan::create(
        title,
        json!({"id":"plate","kind":"box","operation":"add","name":"矩形板",
            "parameters":{"length":size[0],"width":size[1],"height":size[2]},
            "sources":source(&["length","width","height"])}),
        title,
        None,
    );
    for (i, (x, y)) in [(-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0)]
        .into_iter()
        .enumerate()
    {
        let point = [
            x * (size[0] / 2.0 - margins[0]),
            y * (size[1] / 2.0 - margins[1]),
            0.0,
        ];
        plan["features"].as_array_mut().expect("CADPlan features").push(json!({
            "id":format!("hole-{}", i+1),"kind":"hole","operation":"cut","name":format!("通孔 {}", i+1),
            "parameters":{"axis":"Z","pos":"point","point":point,"diameter":diameter,"through":true},
            "sources":source(&["point","diameter"]),"relations":[]
        }));
    }
    Ok(plan)
}

pub fn build_transfer(
    data_dir: &Path,
    manifest: &Path,
    group: &str,
    size: [f64; 3],
    diameter: f64,
) -> Result<Value, String> {
    let group = name(group)?;
    let samples: Vec<Value> = rows(data_dir)?
        .into_iter()
        .filter(|row| row["group"] == group)
        .collect();
    let axis = |i: usize| -> Result<(f64, &'static str), String> {
        let inputs = samples
            .iter()
            .map(|row| Ok((number(&row["size_mm"][i])?, number(&row["margins_mm"][i])?)))
            .collect::<Result<Vec<_>, String>>()?;
        fits(&inputs, size[i]).map_err(|error| format!("{} 轴：{error}", ["X", "Y"][i]))
    };
    let (x, x_rule) = axis(0)?;
    let (y, y_rule) = axis(1)?;
    let plan = plan_for_plate(size, diameter, [x, y], &format!("{group} · 迁移板"))?;
    let root = native_active_model::root_from_data_dir(data_dir);
    let current = native_active_model::load(&root)?;
    let generation = current
        .as_ref()
        .and_then(|row| row["generation"].as_u64())
        .unwrap_or(0);
    let revision = current
        .as_ref()
        .and_then(|row| row["revision"].as_i64())
        .unwrap_or(0)
        .checked_add(1)
        .ok_or("CAD revision 已溢出。")?;
    let next = generation.checked_add(1).ok_or("活动模型版本已溢出。")?;
    let outcome = native_geometry_backend::build_preview(
        &plan,
        revision,
        1,
        manifest,
        &data_dir.join("plate-layout"),
        &AtomicBool::new(false),
    )?
    .outcome;
    let report = &outcome.preview["local_geometry_checks"];
    let (actual_size, actual_margins, actual_diameter) = snapshot_from_report(report)?;
    if actual_size
        .iter()
        .zip(size)
        .any(|(a, b)| (a - b).abs() > TOLERANCE_MM)
        || actual_margins
            .iter()
            .zip([x, y])
            .any(|(a, b)| (a - b).abs() > TOLERANCE_MM)
        || (actual_diameter - diameter).abs() > TOLERANCE_MM
    {
        return Err("实际几何与迁移得到的尺寸或孔位不一致。".into());
    }
    let transfer = json!({"group":group,"samples":samples.len(),"rules":[x_rule,y_rule],
        "predicted_margins_mm":[x,y],"measured_margins_mm":actual_margins,"measured_diameter_mm":actual_diameter,
        "geometry_report":report});
    let mut state = crate::native_cad_session::complete_preview(
        &json!({"revision":revision,"plan":plan,"active_model_generation":next}),
        outcome,
    )?
    .state;
    state["plate_layout_learning"] = transfer.clone();
    let mut candidate = native_active_model::candidate_from_cad_state(
        "plate_layout_learning",
        &state,
        &Value::Null,
        &Value::Null,
    )?;
    candidate["document"]["plate_layout_learning"] = transfer;
    let active = native_active_model::commit(&root, generation, &candidate)?;
    Ok(active["document"].clone())
}
