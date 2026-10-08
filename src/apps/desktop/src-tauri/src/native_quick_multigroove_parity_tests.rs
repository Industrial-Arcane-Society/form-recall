//! F5.97 retained legacy Quick geometry parity for a three-groove pulley.
use super::build;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{fs, path::Path, time::SystemTime};

const LEGACY_STL: &[u8] = include_bytes!("../tests/fixtures/quick_legacy_multigroove_raw.stl");
const LEGACY_REPORT: &str =
    include_str!("../tests/fixtures/quick_legacy_multigroove_mesh_oracle.json");
const BBOX_TOLERANCE_MM: f64 = 0.001;
const SURFACE_DISTANCE_TOLERANCE_MM: f64 = 0.08;
const SECTION_DISTANCE_TOLERANCE_MM: f64 = 0.08;
const BVH_LEAF_TRIANGLES: usize = 8;
const SECTION_SAMPLES_PER_SEGMENT: usize = 5;

#[derive(Clone, Copy)]
struct Aabb {
    min: [f64; 3],
    max: [f64; 3],
}

#[derive(Clone, Copy)]
struct Triangle {
    points: [[f64; 3]; 3],
    bounds: Aabb,
    centroid: [f64; 3],
}

enum BvhKind {
    Leaf(Vec<usize>),
    Branch {
        left: Box<BvhNode>,
        right: Box<BvhNode>,
    },
}

struct BvhNode {
    bounds: Aabb,
    kind: BvhKind,
}

struct DirectionalDistance {
    sample_count: usize,
    max_mm: f64,
    rms_mm: f64,
}

struct SectionDistance {
    segment_count: usize,
    sample_count: usize,
    max_mm: f64,
    rms_mm: f64,
}

struct RemoveOnDrop(std::path::PathBuf);

impl Drop for RemoveOnDrop {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn f5_97_legacy_multigroove_quick_surface_parity() {
    let fixture: Value = serde_json::from_str(LEGACY_REPORT)
        .expect("legacy multigroove oracle report is valid JSON");
    let prompt = "多槽带轮 内径25 基准直径100 宽度30 槽数3";
    assert_eq!(
        fixture["fixture_kind"],
        "actual_legacy_python_quick_document_build_binary_stl"
    );
    assert_eq!(
        fixture.pointer("/source/input").and_then(Value::as_str),
        Some(prompt)
    );
    assert_eq!(
        fixture
            .pointer("/source/entrypoint")
            .and_then(Value::as_str),
        Some(
            "Parser().parse(input) -> ParseResult.doc.build() -> ai_modeler.core.export.exporters.export_stl(binary=True)"
        )
    );
    assert_eq!(
        fixture
            .pointer("/source/parser_ok")
            .and_then(Value::as_bool),
        Some(true)
    );
    assert_eq!(
        fixture
            .pointer("/build/polygon_count")
            .and_then(Value::as_u64),
        Some(1536)
    );
    assert_eq!(
        fixture
            .pointer("/stl/triangles_in_binary_stl")
            .and_then(Value::as_u64),
        Some(3072)
    );
    assert_eq!(
        format!("{:x}", Sha256::digest(LEGACY_STL)),
        fixture
            .pointer("/stl/sha256")
            .and_then(Value::as_str)
            .expect("legacy STL SHA-256")
    );

    let legacy_mesh = parse_binary_stl(LEGACY_STL);
    assert_eq!(legacy_mesh.len(), 3072);

    let root = std::env::temp_dir().join(format!(
        "f5-97-multigroove-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .expect("clock is after Unix epoch")
            .as_nanos()
    ));
    fs::create_dir_all(&root).expect("create isolated Quick output directory");
    let _cleanup = RemoveOnDrop(root.clone());
    let output = build(prompt, 1, Path::new("unused-occt-manifest.json"), &root)
        .unwrap_or_else(|error| panic!("F5.97 Rust Quick build failed: {error}"));

    assert_eq!(output["state"], "complete", "{output}");
    let feature = output
        .pointer("/cadplan/features/0")
        .expect("Quick result has the pulley CADPlan feature");
    assert_eq!(feature["kind"], "vbelt_pulley", "{feature}");
    assert_eq!(feature["operation"], "add", "{feature}");
    assert_eq!(
        feature.pointer("/parameters/pitch_d"),
        Some(&Value::from(100.0))
    );
    assert_eq!(
        feature.pointer("/parameters/grooves"),
        Some(&Value::from(3))
    );
    assert_eq!(
        feature.pointer("/parameters/bore_d"),
        Some(&Value::from(25.0))
    );

    let geometry = &output["geometry_report"];
    assert_eq!(geometry["state"], "mesh_only", "{geometry}");
    assert_eq!(geometry["mesh_only"], true, "{geometry}");
    assert_eq!(
        geometry["geometry_support"], "legacy_mesh_only",
        "{geometry}"
    );
    assert_eq!(
        geometry["execution_engine"], "rust_legacy_quick_triangle_mesh",
        "{geometry}"
    );
    assert_eq!(geometry["valid"], Value::Null, "{geometry}");
    assert_eq!(geometry["solid_count"], Value::Null, "{geometry}");
    assert_eq!(geometry["closed_solid_count"], Value::Null, "{geometry}");
    assert_eq!(geometry["brep_available"], false, "{geometry}");
    assert_eq!(geometry["catpart_available"], false, "{geometry}");

    let rust_stl_path = Path::new(
        output
            .pointer("/output_paths/stl")
            .and_then(Value::as_str)
            .expect("mesh-only Quick result exposes its STL"),
    );
    let rust_stl_bytes = fs::read(rust_stl_path).expect("read actual Rust Quick binary STL");
    let rust_mesh = parse_binary_stl(&rust_stl_bytes);
    assert_eq!(rust_mesh.len(), 3072, "Rust STL triangle count");
    assert_eq!(
        geometry["triangle_count"].as_u64(),
        Some(rust_mesh.len() as u64),
        "{geometry}"
    );

    let legacy_bounds = mesh_bounds(&legacy_mesh);
    let rust_bounds = mesh_bounds(&rust_mesh);
    let legacy_dimensions = dimensions(legacy_bounds);
    let rust_dimensions = dimensions(rust_bounds);
    let expected_dimensions = fixture
        .pointer("/build/bbox_size_mm")
        .and_then(Value::as_array)
        .expect("legacy bbox in oracle report");
    for axis in 0..3 {
        assert!(
            (legacy_dimensions[axis] - expected_dimensions[axis].as_f64().unwrap()).abs()
                <= BBOX_TOLERANCE_MM,
            "legacy STL axis {axis} bbox mismatch: {:?} vs {:?}",
            legacy_dimensions,
            expected_dimensions
        );
        assert!(
            (legacy_dimensions[axis] - rust_dimensions[axis]).abs() <= BBOX_TOLERANCE_MM,
            "legacy/Rust STL bbox differs on axis {axis}: {:?} vs {:?}",
            legacy_dimensions,
            rust_dimensions
        );
    }

    let legacy_bvh = build_bvh(&legacy_mesh);
    let rust_bvh = build_bvh(&rust_mesh);
    let legacy_to_rust = sampled_surface_distance(&legacy_mesh, &rust_bvh, &rust_mesh);
    let rust_to_legacy = sampled_surface_distance(&rust_mesh, &legacy_bvh, &legacy_mesh);
    assert_eq!(legacy_to_rust.sample_count, legacy_mesh.len() * 4);
    assert_eq!(rust_to_legacy.sample_count, rust_mesh.len() * 4);
    assert!(
        legacy_to_rust.max_mm <= SURFACE_DISTANCE_TOLERANCE_MM,
        "legacy->Rust sampled surface distance max {:.6} mm RMS {:.6} mm exceeds {:.3} mm",
        legacy_to_rust.max_mm,
        legacy_to_rust.rms_mm,
        SURFACE_DISTANCE_TOLERANCE_MM
    );
    assert!(
        rust_to_legacy.max_mm <= SURFACE_DISTANCE_TOLERANCE_MM,
        "Rust->legacy sampled surface distance max {:.6} mm RMS {:.6} mm exceeds {:.3} mm",
        rust_to_legacy.max_mm,
        rust_to_legacy.rms_mm,
        SURFACE_DISTANCE_TOLERANCE_MM
    );

    let section_contract = fixture
        .pointer("/build/section_curve_contract")
        .expect("measured full cross-section contract in oracle report");
    let section_y = section_contract
        .pointer("/plane_y_mm")
        .and_then(Value::as_f64)
        .expect("fixed section plane y coordinate");
    assert_eq!(
        section_contract
            .pointer("/samples_per_segment")
            .and_then(Value::as_u64),
        Some(SECTION_SAMPLES_PER_SEGMENT as u64)
    );
    assert_eq!(
        section_contract
            .pointer("/distance_tolerance_mm")
            .and_then(Value::as_f64),
        Some(SECTION_DISTANCE_TOLERANCE_MM)
    );
    let section_legacy = section_segments(&legacy_mesh, section_y);
    let section_rust = section_segments(&rust_mesh, section_y);
    assert_eq!(
        section_contract
            .pointer("/observed_legacy_intersection_segments")
            .and_then(Value::as_u64),
        Some(section_legacy.len() as u64)
    );
    assert!(
        section_legacy.len() > 64,
        "legacy section is not sufficiently resolved"
    );
    assert!(
        section_rust.len() > 80,
        "Rust section is not sufficiently resolved"
    );
    let section_legacy_to_rust = section_curve_distance(&section_legacy, &section_rust);
    let section_rust_to_legacy = section_curve_distance(&section_rust, &section_legacy);
    assert!(
        section_legacy_to_rust.max_mm <= SECTION_DISTANCE_TOLERANCE_MM,
        "legacy->Rust full section curve max {:.6} mm RMS {:.6} mm exceeds {:.3} mm",
        section_legacy_to_rust.max_mm,
        section_legacy_to_rust.rms_mm,
        SECTION_DISTANCE_TOLERANCE_MM
    );
    assert!(
        section_rust_to_legacy.max_mm <= SECTION_DISTANCE_TOLERANCE_MM,
        "Rust->legacy full section curve max {:.6} mm RMS {:.6} mm exceeds {:.3} mm",
        section_rust_to_legacy.max_mm,
        section_rust_to_legacy.rms_mm,
        SECTION_DISTANCE_TOLERANCE_MM
    );

    let section_probes = fixture
        .pointer("/build/surface_section_contract/section_probe_points_mm")
        .and_then(Value::as_array)
        .expect("measured section probes in oracle report");
    assert_eq!(
        section_probes.len(),
        4,
        "one bore probe and three groove-root center probes"
    );
    for (index, probe) in section_probes.iter().enumerate() {
        let probe =
            json_point(probe).unwrap_or_else(|| panic!("invalid section probe #{index}: {probe}"));
        let legacy_distance = nearest_distance_mm(probe, &legacy_bvh, &legacy_mesh);
        let rust_distance = nearest_distance_mm(probe, &rust_bvh, &rust_mesh);
        assert!(
            legacy_distance <= SECTION_DISTANCE_TOLERANCE_MM,
            "oracle section probe #{index} is not on its own mesh: {:?}, distance={legacy_distance:.6} mm",
            probe
        );
        assert!(
            rust_distance <= SECTION_DISTANCE_TOLERANCE_MM,
            "Rust center-section/groove-root probe #{index} missed: {:?}, distance={rust_distance:.6} mm",
            probe
        );
    }

    eprintln!(
        "F5.97 PASS: triangles={} bbox_delta_mm={:?}; bidirectional surface samples={} max_mm=({:.8},{:.8}) rms_mm=({:.8},{:.8}); full_section_y={:.5}mm segments=({},{}) samples={} max_mm=({:.8},{:.8}) rms_mm=({:.8},{:.8}); section_probes={} tol_mm={:.3}",
        rust_mesh.len(),
        std::array::from_fn::<_, 3, _>(
            |axis| (legacy_dimensions[axis] - rust_dimensions[axis]).abs()
        ),
        legacy_to_rust.sample_count,
        legacy_to_rust.max_mm,
        rust_to_legacy.max_mm,
        legacy_to_rust.rms_mm,
        rust_to_legacy.rms_mm,
        section_y,
        section_legacy_to_rust.segment_count,
        section_rust_to_legacy.segment_count,
        section_legacy_to_rust.sample_count + section_rust_to_legacy.sample_count,
        section_legacy_to_rust.max_mm,
        section_rust_to_legacy.max_mm,
        section_legacy_to_rust.rms_mm,
        section_rust_to_legacy.rms_mm,
        section_probes.len(),
        SECTION_DISTANCE_TOLERANCE_MM
    );
}

fn section_segments(triangles: &[Triangle], plane_y: f64) -> Vec<[[f64; 2]; 2]> {
    const ON_PLANE_EPSILON: f64 = 1e-8;
    let mut segments = Vec::new();
    for triangle in triangles {
        let mut intersections = Vec::with_capacity(3);
        for edge in 0..3 {
            let start = triangle.points[edge];
            let end = triangle.points[(edge + 1) % 3];
            let start_delta = start[1] - plane_y;
            let end_delta = end[1] - plane_y;
            if start_delta.abs() <= ON_PLANE_EPSILON {
                intersections.push([start[0], start[2]]);
            }
            if start_delta * end_delta < 0.0 {
                let amount = start_delta / (start_delta - end_delta);
                intersections.push([
                    start[0] + (end[0] - start[0]) * amount,
                    start[2] + (end[2] - start[2]) * amount,
                ]);
            }
        }
        intersections.dedup_by(|left, right| {
            (left[0] - right[0]).abs() <= ON_PLANE_EPSILON
                && (left[1] - right[1]).abs() <= ON_PLANE_EPSILON
        });
        if intersections.len() < 2 {
            continue;
        }
        let mut farthest = (0, 1, 0.0_f64);
        for left in 0..intersections.len() {
            for right in left + 1..intersections.len() {
                let distance = squared_distance_2d(intersections[left], intersections[right]);
                if distance > farthest.2 {
                    farthest = (left, right, distance);
                }
            }
        }
        if farthest.2 > 1e-20 {
            segments.push([intersections[farthest.0], intersections[farthest.1]]);
        }
    }
    segments
}

fn section_curve_distance(source: &[[[f64; 2]; 2]], target: &[[[f64; 2]; 2]]) -> SectionDistance {
    assert!(!source.is_empty(), "source cross-section is empty");
    assert!(!target.is_empty(), "target cross-section is empty");
    let mut max_squared = 0.0_f64;
    let mut sum_squared = 0.0_f64;
    let mut sample_count = 0;
    for segment in source {
        for sample in 0..SECTION_SAMPLES_PER_SEGMENT {
            let amount = sample as f64 / (SECTION_SAMPLES_PER_SEGMENT - 1) as f64;
            let point = [
                segment[0][0] + (segment[1][0] - segment[0][0]) * amount,
                segment[0][1] + (segment[1][1] - segment[0][1]) * amount,
            ];
            let nearest = target
                .iter()
                .map(|other| point_segment_distance_squared_2d(point, *other))
                .fold(f64::INFINITY, f64::min);
            max_squared = max_squared.max(nearest);
            sum_squared += nearest;
            sample_count += 1;
        }
    }
    SectionDistance {
        segment_count: source.len(),
        sample_count,
        max_mm: max_squared.sqrt(),
        rms_mm: (sum_squared / sample_count as f64).sqrt(),
    }
}

fn point_segment_distance_squared_2d(point: [f64; 2], segment: [[f64; 2]; 2]) -> f64 {
    let edge = [segment[1][0] - segment[0][0], segment[1][1] - segment[0][1]];
    let length_squared = edge[0] * edge[0] + edge[1] * edge[1];
    let relative = [point[0] - segment[0][0], point[1] - segment[0][1]];
    let amount = if length_squared > 1e-24 {
        ((relative[0] * edge[0] + relative[1] * edge[1]) / length_squared).clamp(0.0, 1.0)
    } else {
        0.0
    };
    let delta = [
        relative[0] - edge[0] * amount,
        relative[1] - edge[1] * amount,
    ];
    delta[0] * delta[0] + delta[1] * delta[1]
}

fn squared_distance_2d(left: [f64; 2], right: [f64; 2]) -> f64 {
    let dx = left[0] - right[0];
    let dy = left[1] - right[1];
    dx * dx + dy * dy
}

fn parse_binary_stl(bytes: &[u8]) -> Vec<Triangle> {
    assert!(bytes.len() >= 84, "binary STL is shorter than its header");
    let count = u32::from_le_bytes(bytes[80..84].try_into().unwrap()) as usize;
    assert_eq!(
        bytes.len(),
        84 + count * 50,
        "binary STL byte count does not match facet count"
    );
    (0..count)
        .map(|index| {
            let record = 84 + index * 50;
            let mut points = [[0.0; 3]; 3];
            for (vertex, point) in points.iter_mut().enumerate() {
                for (axis, coordinate) in point.iter_mut().enumerate() {
                    let offset = record + 12 + vertex * 12 + axis * 4;
                    *coordinate =
                        f32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap()) as f64;
                }
            }
            let min = std::array::from_fn(|axis| {
                points
                    .iter()
                    .map(|point| point[axis])
                    .fold(f64::INFINITY, f64::min)
            });
            let max = std::array::from_fn(|axis| {
                points
                    .iter()
                    .map(|point| point[axis])
                    .fold(f64::NEG_INFINITY, f64::max)
            });
            let centroid = std::array::from_fn(|axis| {
                points.iter().map(|point| point[axis]).sum::<f64>() / 3.0
            });
            Triangle {
                points,
                bounds: Aabb { min, max },
                centroid,
            }
        })
        .collect()
}

fn mesh_bounds(triangles: &[Triangle]) -> Aabb {
    let mut bounds = Aabb {
        min: [f64::INFINITY; 3],
        max: [f64::NEG_INFINITY; 3],
    };
    for triangle in triangles {
        for axis in 0..3 {
            bounds.min[axis] = bounds.min[axis].min(triangle.bounds.min[axis]);
            bounds.max[axis] = bounds.max[axis].max(triangle.bounds.max[axis]);
        }
    }
    bounds
}

fn dimensions(bounds: Aabb) -> [f64; 3] {
    std::array::from_fn(|axis| bounds.max[axis] - bounds.min[axis])
}

fn build_bvh(triangles: &[Triangle]) -> BvhNode {
    assert!(!triangles.is_empty(), "cannot index an empty surface mesh");
    let mut indices: Vec<usize> = (0..triangles.len()).collect();
    build_bvh_node(triangles, &mut indices)
}

fn build_bvh_node(triangles: &[Triangle], indices: &mut [usize]) -> BvhNode {
    let bounds = indices.iter().fold(
        Aabb {
            min: [f64::INFINITY; 3],
            max: [f64::NEG_INFINITY; 3],
        },
        |mut bounds, index| {
            for axis in 0..3 {
                bounds.min[axis] = bounds.min[axis].min(triangles[*index].bounds.min[axis]);
                bounds.max[axis] = bounds.max[axis].max(triangles[*index].bounds.max[axis]);
            }
            bounds
        },
    );
    if indices.len() <= BVH_LEAF_TRIANGLES {
        return BvhNode {
            bounds,
            kind: BvhKind::Leaf(indices.to_vec()),
        };
    }

    let mut centroid_bounds = Aabb {
        min: [f64::INFINITY; 3],
        max: [f64::NEG_INFINITY; 3],
    };
    for index in indices.iter() {
        for axis in 0..3 {
            centroid_bounds.min[axis] =
                centroid_bounds.min[axis].min(triangles[*index].centroid[axis]);
            centroid_bounds.max[axis] =
                centroid_bounds.max[axis].max(triangles[*index].centroid[axis]);
        }
    }
    let axis = (0..3)
        .max_by(|left, right| {
            (centroid_bounds.max[*left] - centroid_bounds.min[*left])
                .total_cmp(&(centroid_bounds.max[*right] - centroid_bounds.min[*right]))
        })
        .unwrap();
    indices.sort_unstable_by(|left, right| {
        triangles[*left].centroid[axis].total_cmp(&triangles[*right].centroid[axis])
    });
    let middle = indices.len() / 2;
    let (left_indices, right_indices) = indices.split_at_mut(middle);
    BvhNode {
        bounds,
        kind: BvhKind::Branch {
            left: Box::new(build_bvh_node(triangles, left_indices)),
            right: Box::new(build_bvh_node(triangles, right_indices)),
        },
    }
}

fn sampled_surface_distance(
    source: &[Triangle],
    target_bvh: &BvhNode,
    target: &[Triangle],
) -> DirectionalDistance {
    let mut sample_count = 0;
    let mut max_squared = 0.0_f64;
    let mut sum_squared = 0.0_f64;
    for triangle in source {
        let [a, b, c] = triangle.points;
        let samples = [a, b, c, triangle.centroid];
        for point in samples {
            let squared = nearest_squared_distance(point, target_bvh, target, f64::INFINITY);
            max_squared = max_squared.max(squared);
            sum_squared += squared;
            sample_count += 1;
        }
    }
    DirectionalDistance {
        sample_count,
        max_mm: max_squared.sqrt(),
        rms_mm: (sum_squared / sample_count as f64).sqrt(),
    }
}

fn nearest_distance_mm(point: [f64; 3], bvh: &BvhNode, triangles: &[Triangle]) -> f64 {
    nearest_squared_distance(point, bvh, triangles, f64::INFINITY).sqrt()
}

fn nearest_squared_distance(
    point: [f64; 3],
    node: &BvhNode,
    triangles: &[Triangle],
    best_squared: f64,
) -> f64 {
    let lower_bound = point_aabb_distance_squared(point, node.bounds);
    if lower_bound >= best_squared {
        return best_squared;
    }
    match &node.kind {
        BvhKind::Leaf(indices) => indices.iter().fold(best_squared, |best, index| {
            best.min(point_triangle_distance_squared(
                point,
                triangles[*index].points,
            ))
        }),
        BvhKind::Branch { left, right } => {
            let left_distance = point_aabb_distance_squared(point, left.bounds);
            let right_distance = point_aabb_distance_squared(point, right.bounds);
            if left_distance <= right_distance {
                let best = nearest_squared_distance(point, left, triangles, best_squared);
                nearest_squared_distance(point, right, triangles, best)
            } else {
                let best = nearest_squared_distance(point, right, triangles, best_squared);
                nearest_squared_distance(point, left, triangles, best)
            }
        }
    }
}

fn point_aabb_distance_squared(point: [f64; 3], bounds: Aabb) -> f64 {
    let mut distance = 0.0;
    for axis in 0..3 {
        let delta = if point[axis] < bounds.min[axis] {
            bounds.min[axis] - point[axis]
        } else if point[axis] > bounds.max[axis] {
            point[axis] - bounds.max[axis]
        } else {
            0.0
        };
        distance += delta * delta;
    }
    distance
}

fn point_triangle_distance_squared(point: [f64; 3], triangle: [[f64; 3]; 3]) -> f64 {
    let [a, b, c] = triangle;
    let ab = sub(b, a);
    let ac = sub(c, a);
    let normal = cross(ab, ac);
    if dot(normal, normal) <= 1e-24 {
        return point_segment_distance_squared(point, a, b)
            .min(point_segment_distance_squared(point, b, c))
            .min(point_segment_distance_squared(point, c, a));
    }

    let ap = sub(point, a);
    let d1 = dot(ab, ap);
    let d2 = dot(ac, ap);
    if d1 <= 0.0 && d2 <= 0.0 {
        return dot(ap, ap);
    }

    let bp = sub(point, b);
    let d3 = dot(ab, bp);
    let d4 = dot(ac, bp);
    if d3 >= 0.0 && d4 <= d3 {
        return dot(bp, bp);
    }

    let vc = d1 * d4 - d3 * d2;
    if vc <= 0.0 && d1 >= 0.0 && d3 <= 0.0 {
        let v = d1 / (d1 - d3);
        let delta = sub(point, add(a, scale(ab, v)));
        return dot(delta, delta);
    }

    let cp = sub(point, c);
    let d5 = dot(ab, cp);
    let d6 = dot(ac, cp);
    if d6 >= 0.0 && d5 <= d6 {
        return dot(cp, cp);
    }

    let vb = d5 * d2 - d1 * d6;
    if vb <= 0.0 && d2 >= 0.0 && d6 <= 0.0 {
        let w = d2 / (d2 - d6);
        let delta = sub(point, add(a, scale(ac, w)));
        return dot(delta, delta);
    }

    let va = d3 * d6 - d5 * d4;
    if va <= 0.0 && (d4 - d3) >= 0.0 && (d5 - d6) >= 0.0 {
        let edge = sub(c, b);
        let w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
        let delta = sub(point, add(b, scale(edge, w)));
        return dot(delta, delta);
    }

    let denominator = 1.0 / (va + vb + vc);
    let v = vb * denominator;
    let w = vc * denominator;
    let closest = add(a, add(scale(ab, v), scale(ac, w)));
    let delta = sub(point, closest);
    dot(delta, delta)
}

fn point_segment_distance_squared(point: [f64; 3], start: [f64; 3], end: [f64; 3]) -> f64 {
    let edge = sub(end, start);
    let length_squared = dot(edge, edge);
    let amount = if length_squared > 1e-24 {
        (dot(sub(point, start), edge) / length_squared).clamp(0.0, 1.0)
    } else {
        0.0
    };
    let delta = sub(point, add(start, scale(edge, amount)));
    dot(delta, delta)
}

fn json_point(value: &Value) -> Option<[f64; 3]> {
    let values = value.as_array()?;
    Some([
        values.first()?.as_f64()?,
        values.get(1)?.as_f64()?,
        values.get(2)?.as_f64()?,
    ])
}

fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

fn add(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}

fn scale(vector: [f64; 3], amount: f64) -> [f64; 3] {
    [vector[0] * amount, vector[1] * amount, vector[2] * amount]
}

fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
