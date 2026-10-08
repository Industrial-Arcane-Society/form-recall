//! Parameter-preserving Quick mesh migration for measured EGT SW50/SW80.
//!
//! The catalogue is generated from the legacy measured profile and STEP
//! cylinder-coordinate sources. This route intentionally emits triangle mesh
//! only: it does not claim an OCCT B-Rep or CATPart mapping.

use crate::native_occt::TriangleMesh;
use delaunator::{Point, triangulate};
use serde_json::{Value, json};
use std::sync::OnceLock;

const CATALOG_JSON: &str = include_str!("native_quick_egt_sw_catalog.json");
const RING_SEGMENTS: usize = 24;
const SURFACE_GRID_MM: f64 = 12.0;
const MAX_TRIANGLES: usize = 5_000_000;

fn catalogue() -> &'static Value {
    static CATALOG: OnceLock<Value> = OnceLock::new();
    CATALOG.get_or_init(|| serde_json::from_str(CATALOG_JSON).expect("generated EGT SW catalogue is valid JSON"))
}

pub fn parse(raw: &str) -> Option<Value> {
    static MODEL_PATTERN: OnceLock<regex::Regex> = OnceLock::new();
    let pattern = MODEL_PATTERN.get_or_init(|| {
        regex::Regex::new(r"(?i)(?:\b(EGT[0-9]+)\s*[- ]?\s*)?\b(SW(?:50|80))\s*[x×*]\s*([0-9]+(?:\.[0-9]+)?)")
            .expect("EGT model expression")
    });
    let caps = pattern.captures(raw)?;
    let family = caps.get(1).map(|m| m.as_str().to_ascii_uppercase());
    let section = caps.get(2)?.as_str().to_ascii_uppercase();
    let code = caps.get(3)?.as_str().parse::<f64>().ok()?;
    if !code.is_finite() || (code - code.round()).abs() > 1e-9 {
        return None;
    }
    let code = code.round() as i64;
    let profile = catalogue()["profiles"].as_array()?.iter().find(|row| {
        row["section_code"].as_str() == Some(section.as_str())
            && row["code"].as_i64() == Some(code)
            && family.as_deref().is_none_or(|family| row["family"].as_str() == Some(family))
    })?;

    let evidence = format!(
        "实测目录 {}；截面、长度、坐标框架和孔位来自 legacy measured EGT sources。",
        profile["source_file"].as_str().unwrap_or("EGT measured STEP")
    );
    let derived = |key: &str| json!({
        "type":"derived",
        "evidence":raw,
        "note":format!("{evidence} 参数 {key} 按实测目录记录复现，不是图像估算。")
    });
    let feature = json!({
        "id":"quick-0001",
        "kind":"extrude_holes",
        "operation":"add",
        "name":format!("{} legacy Quick 矩形包络 {} × {} mm + {} 个目录孔位（mesh-only）", profile["model"].as_str().unwrap_or("EGT SW"), profile["sec_a_mm"], profile["length_mm"], profile["holes"].as_array().map_or(0, Vec::len)),
        "parameters":{
            "sec_a":profile["sec_a_mm"], "sec_b":profile["sec_b_mm"],
            "length":profile["length_mm"], "axis":profile["axis"],
            "base":profile["base_mm"], "holes":profile["holes"],
            "segments":RING_SEGMENTS,
            "ref_min":profile["ref_min_mm"], "ref_size":profile["ref_size_mm"]
        },
        "sources":{
            "sec_a":derived("sec_a"), "sec_b":derived("sec_b"),
            "length":derived("length"), "base":derived("base"),
            "holes":derived("holes[] 的 d/axis/u/v/through"),
            "segments":derived("segments"), "ref_min":derived("ref_min"),
            "ref_size":derived("ref_size")
        },
        "relations":[]
    });
    let plan = json!({
        "schema_version":"1.1",
        "name":profile["model"],
        "source_prompt":raw,
        "units":{"length":"mm","angle":"deg"},
        "coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},
        "features":[feature],
        "pending_questions":[]
    });
    Some(json!({
        "status":"ready",
        "source_text":raw,
        "plan":plan,
        "warnings":[{"code":"egt_legacy_quick_mesh_only","message":"复刻 legacy Quick 的 SW50/SW80 矩形包络与贯穿孔网格语义；包络尺寸及孔坐标来自 measured EGT 目录，但不表示真实型材内部截面。结果由 CADPlan/AIMODEL 参数重建，不生成 B-Rep 或 CATPart。"}],
        "questions":[],
        "unsupported":[],
        "compatibility":{"scope":"measured EGT SW50/SW80 mesh-only migration"}
    }))
}

pub fn is_mesh_only_plan(plan: &Value) -> bool {
    let Some(features) = plan["features"].as_array() else { return false; };
    if features.len() != 1
        || plan["units"]["length"] != "mm"
        || plan["units"]["angle"] != "deg"
        || features[0]["kind"] != "extrude_holes"
        || features[0]["operation"] != "add"
        || features[0]["enabled"] == false
    {
        return false;
    }
    let parameters = &features[0]["parameters"];
    catalogue()["profiles"].as_array().is_some_and(|rows| rows.iter().any(|row| {
        parameters["sec_a"] == row["sec_a_mm"]
            && parameters["sec_b"] == row["sec_b_mm"]
            && parameters["length"] == row["length_mm"]
            && parameters["axis"] == row["axis"]
            && parameters["base"] == row["base_mm"]
            && parameters["holes"] == row["holes"]
            && parameters["ref_min"] == row["ref_min_mm"]
            && parameters["ref_size"] == row["ref_size_mm"]
            && parameters["segments"] == RING_SEGMENTS
    }))
}

#[derive(Clone)]
struct Hole {
    axis: usize,
    u: f64,
    v: f64,
    radius: f64,
    ring: Vec<[f64; 3]>,
}

pub fn build(parameters: &Value) -> Result<TriangleMesh, String> {
    let sec_a = number(parameters, "sec_a")?;
    let sec_b = number(parameters, "sec_b")?;
    let length = number(parameters, "length")?;
    let base = number(parameters, "base")?;
    let axis = axis_index(parameters["axis"].as_str().ok_or("EGT axis is missing")?)?;
    let segments = parameters["segments"].as_u64().unwrap_or(24) as usize;
    if sec_a <= 0.0 || sec_b <= 0.0 || length <= 0.0 || segments != RING_SEGMENTS {
        return Err("EGT SW mesh requires positive section/length and 24 ring segments".into());
    }
    let ref_min = vec3(parameters.get("ref_min").ok_or("EGT ref_min is missing")?)?;
    let ref_size = vec3(parameters.get("ref_size").ok_or("EGT ref_size is missing")?)?;
    let shift = std::array::from_fn::<_, 3, _>(|i| -(ref_min[i] + ref_size[i] / 2.0));
    let mut ranges = [[0.0, 0.0]; 3];
    ranges[axis] = [base, base + length];
    let cross_axes = (0..3).filter(|i| *i != axis).collect::<Vec<_>>();
    ranges[cross_axes[0]] = [-sec_a / 2.0, sec_a / 2.0];
    ranges[cross_axes[1]] = [-sec_b / 2.0, sec_b / 2.0];

    let rows = parameters["holes"].as_array().ok_or("EGT holes must be an array")?;
    if rows.is_empty() || rows.len() > 545 {
        return Err("EGT SW hole count must be in 1..=545".into());
    }
    let mut holes = Vec::with_capacity(rows.len());
    for row in rows {
        let hole_axis = axis_index(row["axis"].as_str().ok_or("EGT hole axis is missing")?)?;
        let diameter = row["d"].as_f64().ok_or("EGT hole diameter is missing")?;
        let u = row["u"].as_f64().ok_or("EGT hole u is missing")?;
        let v = row["v"].as_f64().ok_or("EGT hole v is missing")?;
        if diameter <= 0.0 || row["through"] != true {
            return Err("EGT SW mesh accepts only positive through holes".into());
        }
        let axes = (0..3).filter(|i| *i != hole_axis).collect::<Vec<_>>();
        let u_axis = axes[0];
        let v_axis = axes[1];
        let radius = diameter / 2.0;
        let center_u = u + shift[u_axis];
        let center_v = v + shift[v_axis];
        if center_u - radius < ranges[u_axis][0] - 1e-6
            || center_u + radius > ranges[u_axis][1] + 1e-6
            || center_v - radius < ranges[v_axis][0] - 1e-6
            || center_v + radius > ranges[v_axis][1] + 1e-6
        {
            return Err("EGT hole crosses the extrusion side boundary".into());
        }
        let mut center = [0.0; 3];
        center[u_axis] = center_u;
        center[v_axis] = center_v;
        let ring = (0..segments).map(|i| {
            let t = std::f64::consts::TAU * i as f64 / segments as f64;
            let mut point = center;
            point[u_axis] = center_u + radius * t.cos();
            point[v_axis] = center_v + radius * t.sin();
            point
        }).collect::<Vec<_>>();
        holes.push(Hole { axis: hole_axis, u: center_u, v: center_v, radius, ring });
    }
    // Legacy Quick does not boolean-union crossing tunnels. Preserve its raw
    // surface mesh and report measured crossings separately instead of
    // rejecting historically successful inputs.

    let mut mesh = TriangleMesh { positions: Vec::new(), normals: Vec::new() };
    for face_axis in 0..3 {
        let inner = (0..3).filter(|i| *i != face_axis).collect::<Vec<_>>();
        let (u_values, v_values) = (grid(ranges[inner[0]][0], ranges[inner[0]][1]), grid(ranges[inner[1]][0], ranges[inner[1]][1]));
        for sign in [-1.0, 1.0] {
            let coordinate = if sign < 0.0 { ranges[face_axis][0] } else { ranges[face_axis][1] };
            let face_holes = holes.iter().filter(|hole| hole.axis == face_axis).collect::<Vec<_>>();
            let points = surface_points(&u_values, &v_values, [inner[0], inner[1]], &face_holes)?;
            let triangulation = triangulate(&points.iter().map(|p| Point { x: p[0], y: p[1] }).collect::<Vec<_>>());
            let axis_hand = if face_axis == 1 { -1.0 } else { 1.0 };
            for tri in triangulation.triangles.chunks_exact(3) {
                let a = points[tri[0]];
                let b = points[tri[1]];
                let c = points[tri[2]];
                let centroid = [(a[0] + b[0] + c[0]) / 3.0, (a[1] + b[1] + c[1]) / 3.0];
                if face_holes.iter().any(|hole| {
                    let x = centroid[0] - hole.u;
                    let y = centroid[1] - hole.v;
                    x * x + y * y <= (hole.radius * 1.0000001).powi(2)
                }) { continue; }
                let mut p = [a, b, c].map(|q| {
                    let mut point = [0.0; 3]; point[inner[0]] = q[0]; point[inner[1]] = q[1]; point[face_axis] = coordinate; point
                });
                let orient = if sign * axis_hand >= 0.0 { 1.0 } else { -1.0 };
                // delaunator returns the opposite winding from the legacy
                // SciPy triangulation for this projected point order.
                if orient > 0.0 { p.swap(1, 2); }
                push_triangle(&mut mesh, p[0], p[1], p[2])?;
            }
        }
    }
    for hole in &holes {
        let range = ranges[hole.axis];
        let axis_hand = if hole.axis == 1 { -1.0 } else { 1.0 };
        for i in 0..segments {
            let j = (i + 1) % segments;
            let mut a0 = hole.ring[i]; a0[hole.axis] = range[0];
            let mut a1 = hole.ring[i]; a1[hole.axis] = range[1];
            let mut b0 = hole.ring[j]; b0[hole.axis] = range[0];
            let mut b1 = hole.ring[j]; b1[hole.axis] = range[1];
            let quad = if axis_hand > 0.0 { [a0, a1, b1, b0] } else { [a0, b0, b1, a1] };
            push_triangle(&mut mesh, quad[0], quad[1], quad[2])?;
            push_triangle(&mut mesh, quad[0], quad[2], quad[3])?;
        }
    }
    if mesh.positions.is_empty() || mesh.positions.len() / 3 > MAX_TRIANGLES {
        return Err("EGT SW mesh is empty or exceeds the triangle limit".into());
    }
    Ok(mesh)
}

fn edge_use_counts(mesh: &TriangleMesh) -> std::collections::BTreeMap<([i64; 3], [i64; 3]), usize> {
    fn key(point: [f64; 3]) -> [i64; 3] {
        point.map(|component| (component * 100_000_000.0).round() as i64)
    }
    let mut uses = std::collections::BTreeMap::new();
    for triangle in mesh.positions.chunks_exact(3) {
        for (a, b) in [(triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])] {
            let (a, b) = (key(a), key(b));
            let edge = if a <= b { (a, b) } else { (b, a) };
            *uses.entry(edge).or_insert(0) += 1;
        }
    }
    uses
}

pub fn mesh_edge_report(mesh: &TriangleMesh) -> Value {
    let edges = edge_use_counts(mesh);
    let boundary_edges = edges.values().filter(|count| **count == 1).count();
    let nonmanifold_edges = edges.values().filter(|count| **count > 2).count();
    json!({
        "unique_edges":edges.len(),
        "boundary_edges":boundary_edges,
        "nonmanifold_edges":nonmanifold_edges,
        "edge_manifold":boundary_edges == 0 && nonmanifold_edges == 0
    })
}

fn surface_points(u: &[f64], v: &[f64], inner: [usize; 2], holes: &[&Hole]) -> Result<Vec<[f64; 2]>, String> {
    let mut points = std::collections::BTreeMap::<(i64, i64), [f64; 2]>::new();
    let mut add = |p: [f64; 2]| { points.entry(((p[0] * 1e9).round() as i64, (p[1] * 1e9).round() as i64)).or_insert(p); };
    for vv in [v[0], v[v.len() - 1]] { for uu in u { add([*uu, vv]); } }
    for uu in [u[0], u[u.len() - 1]] { for vv in v { add([uu, *vv]); } }
    for hole in holes { for p in &hole.ring { add([p[inner[0]], p[inner[1]]]); } }
    let points = points.into_values().collect::<Vec<_>>();
    if points.len() < 3 { return Err("EGT face triangulation needs at least 3 points".into()); }
    Ok(points)
}

fn grid(lo: f64, hi: f64) -> Vec<f64> {
    let n = (((hi - lo) / SURFACE_GRID_MM).ceil() as usize).max(1);
    (0..=n).map(|i| lo + (hi - lo) * i as f64 / n as f64).collect()
}

pub fn geometry_diagnostics(parameters: &Value, mesh: &TriangleMesh) -> Result<Value, String> {
    let sec_a = number(parameters, "sec_a")?;
    let sec_b = number(parameters, "sec_b")?;
    let length = number(parameters, "length")?;
    let base = number(parameters, "base")?;
    let axis = axis_index(parameters["axis"].as_str().ok_or("EGT axis is missing")?)?;
    let ref_min = vec3(parameters.get("ref_min").ok_or("EGT ref_min is missing")?)?;
    let ref_size = vec3(parameters.get("ref_size").ok_or("EGT ref_size is missing")?)?;
    let shift = std::array::from_fn::<_, 3, _>(|i| -(ref_min[i] + ref_size[i] / 2.0));
    let mut ranges = [[0.0, 0.0]; 3];
    ranges[axis] = [base, base + length];
    let cross_axes = (0..3).filter(|i| *i != axis).collect::<Vec<_>>();
    ranges[cross_axes[0]] = [-sec_a / 2.0, sec_a / 2.0];
    ranges[cross_axes[1]] = [-sec_b / 2.0, sec_b / 2.0];
    let rows = parameters["holes"].as_array().ok_or("EGT holes must be an array")?;
    let mut holes = Vec::<(usize, [f64; 3], f64)>::with_capacity(rows.len());
    for row in rows {
        let hole_axis = axis_index(row["axis"].as_str().ok_or("EGT hole axis is missing")?)?;
        let axes = (0..3).filter(|i| *i != hole_axis).collect::<Vec<_>>();
        let mut center = [0.0; 3];
        center[axes[0]] = row["u"].as_f64().ok_or("EGT hole u is missing")? + shift[axes[0]];
        center[axes[1]] = row["v"].as_f64().ok_or("EGT hole v is missing")? + shift[axes[1]];
        let radius = row["d"].as_f64().ok_or("EGT hole diameter is missing")? / 2.0;
        holes.push((hole_axis, center, radius));
    }
    let mut intersecting_pairs = 0usize;
    for i in 0..holes.len() {
        for j in i + 1..holes.len() {
            let mut distance_squared = 0.0;
            for dimension in 0..3 {
                let delta = match (holes[i].0 == dimension, holes[j].0 == dimension) {
                    (true, true) => interval_gap(ranges[dimension], ranges[dimension]),
                    (true, false) => point_interval_gap(holes[j].1[dimension], ranges[dimension]),
                    (false, true) => point_interval_gap(holes[i].1[dimension], ranges[dimension]),
                    (false, false) => holes[i].1[dimension] - holes[j].1[dimension],
                };
                distance_squared += delta * delta;
            }
            if distance_squared < (holes[i].2 + holes[j].2 - 1e-7).powi(2) {
                intersecting_pairs += 1;
            }
        }
    }
    let edge_report = mesh_edge_report(mesh);
    Ok(json!({
        "hole_count":holes.len(),
        "intersecting_hole_pairs":intersecting_pairs,
        "self_intersection_risk":intersecting_pairs > 0,
        "mesh_edges":edge_report
    }))
}

fn interval_gap(a: [f64; 2], b: [f64; 2]) -> f64 {
    if a[1] < b[0] { a[1] - b[0] } else if b[1] < a[0] { a[0] - b[1] } else { 0.0 }
}

fn point_interval_gap(point: f64, interval: [f64; 2]) -> f64 {
    if point < interval[0] { point - interval[0] } else if point > interval[1] { point - interval[1] } else { 0.0 }
}

fn push_triangle(mesh: &mut TriangleMesh, a: [f64; 3], b: [f64; 3], c: [f64; 3]) -> Result<(), String> {
    let ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    let ac = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
    let cross = [ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0]];
    let norm = (cross[0]*cross[0] + cross[1]*cross[1] + cross[2]*cross[2]).sqrt();
    if !norm.is_finite() || norm <= 1e-12 { return Err("EGT mesh produced a degenerate triangle".into()); }
    let normal = [cross[0]/norm,cross[1]/norm,cross[2]/norm];
    mesh.positions.extend([a,b,c]);
    mesh.normals.extend([normal;3]);
    Ok(())
}

fn axis_index(axis: &str) -> Result<usize, String> {
    match axis.to_ascii_uppercase().as_str() { "X"=>Ok(0),"Y"=>Ok(1),"Z"=>Ok(2),_=>Err("EGT axis must be X/Y/Z".into()) }
}
fn number(value: &Value, key: &str) -> Result<f64, String> { value[key].as_f64().filter(|v| v.is_finite()).ok_or_else(||format!("EGT parameter {key} must be finite")) }
fn vec3(value: &Value) -> Result<[f64;3], String> { let a=value.as_array().filter(|a|a.len()==3).ok_or("EGT vector must have three values")?; Ok([a[0].as_f64().ok_or("invalid vector")?,a[1].as_f64().ok_or("invalid vector")?,a[2].as_f64().ok_or("invalid vector")?]) }
