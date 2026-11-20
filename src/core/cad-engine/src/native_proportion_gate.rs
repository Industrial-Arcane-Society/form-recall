//! Conservative, host-computed 2D body-envelope proportion evidence.
//!
//! Measurements are dimensionless projected silhouette ratios. They are never interpreted
//! as absolute dimensions. A comparison is made only for an exact, current reference/preview
//! pair whose persisted vision observation marks the pair comparable.
use image::{DynamicImage, ImageDecoder, ImageReader, Limits, RgbaImage};
use serde_json::{json, Value};
use sha2::Digest;
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Cursor,
    path::Path,
};

const MAX_IMAGE_BYTES: usize = 20 * 1024 * 1024;
const MAX_PIXELS: u64 = 16_777_216;
const BACKGROUND_COLOR_BINS: usize = 32 * 32 * 32;
const MAX_BACKGROUND_PALETTE: usize = 1024;

#[derive(Clone, Copy, Debug)]
struct Interval {
    low: f64,
    high: f64,
}

/// Compare reference rows with exact current image pairs already observed by native vision.
pub(crate) fn evaluate(state: &Value, reference_manifest_path: &Path, revision: i64) -> Value {
    let refs: Value = match std::fs::read(reference_manifest_path)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
    {
        Some(value) => value,
        None => {
            return json!({"status":"unknown","reason":"reference_manifest_unavailable","comparisons":[]} );
        }
    };
    let rows = refs
        .get("images")
        .and_then(Value::as_array)
        .or_else(|| refs.as_array());
    let Some(rows) = rows else {
        return json!({"status":"unknown","reason":"reference_rows_invalid","comparisons":[]});
    };
    if rows.is_empty() {
        return json!({"status":"not_applicable","comparisons":[]});
    }
    let previews = state.get("previews").and_then(Value::as_array);
    let history = state
        .get("reference_similarity_history")
        .and_then(Value::as_array);
    let generation = state.get("preview_generation").and_then(Value::as_u64);
    let fingerprint = state
        .pointer("/last_preview/geometry_fingerprint")
        .and_then(Value::as_str);
    let mut comparisons = Vec::new();
    for row in rows {
        let id = row.get("id").and_then(Value::as_str).unwrap_or("unknown");
        let Some(reference_path) = row.get("path").and_then(Value::as_str) else {
            comparisons.push(json!({"reference_id":id,"status":"unknown","reason":"reference_image_path_missing"}));
            continue;
        };
        let reference_bytes = match read_image_bytes(Path::new(reference_path)) {
            Ok(bytes) => bytes,
            Err(reason) => {
                comparisons.push(json!({"reference_id":id,"status":"unknown","reason":reason}));
                continue;
            }
        };
        let reference_hash = format!("{:x}", sha2::Sha256::digest(&reference_bytes));
        if row
            .get("sha256")
            .and_then(Value::as_str)
            .is_none_or(|expected| !reference_hash.eq_ignore_ascii_case(expected))
        {
            comparisons.push(
                json!({"reference_id":id,"status":"unknown","reason":"reference_hash_mismatch"}),
            );
            continue;
        }
        let reference = match measure(&reference_bytes) {
            Ok(measurement) => measurement,
            Err(reason) => {
                comparisons.push(json!({"reference_id":id,"status":"unknown","reason":reason}));
                continue;
            }
        };
        let source_sha = row
            .get("sha256")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let current_rows: Vec<&Value> = history
            .into_iter()
            .flatten()
            .filter(|observation| {
                observation.get("reference_id").and_then(Value::as_str) == Some(id)
                    && observation
                        .get("source_sha256")
                        .and_then(Value::as_str)
                        .is_some_and(|v| v.eq_ignore_ascii_case(source_sha))
                    && observation.get("revision").and_then(Value::as_i64) == Some(revision)
                    && observation
                        .get("preview_generation")
                        .and_then(Value::as_u64)
                        == generation
                    && observation
                        .get("geometry_fingerprint")
                        .or_else(|| observation.get("geometry_signature"))
                        .and_then(Value::as_str)
                        == fingerprint
                    && observation.get("roi_norm").is_none_or(Value::is_null)
                    && observation
                        .get("partial_candidate_diagnostic_only")
                        .and_then(Value::as_bool)
                        != Some(true)
            })
            .collect();
        let mut latest_by_view: BTreeMap<String, &Value> = BTreeMap::new();
        for observation in &current_rows {
            let view = observation
                .get("view")
                .and_then(Value::as_str)
                .unwrap_or_default();
            if view.is_empty() {
                continue;
            }
            let timestamp = observation
                .get("recorded_at")
                .and_then(Value::as_u64)
                .unwrap_or(0);
            if latest_by_view.get(view).is_none_or(|old| {
                timestamp >= old.get("recorded_at").and_then(Value::as_u64).unwrap_or(0)
            }) {
                latest_by_view.insert(view.to_owned(), observation);
            }
        }
        let comparable_rows: Vec<&Value> = latest_by_view
            .into_values()
            .filter(|observation| {
                observation.get("view_comparable").and_then(Value::as_bool) == Some(true)
            })
            .collect();
        let mut compared = 0;
        for observation in comparable_rows {
            let view = observation
                .get("view")
                .and_then(Value::as_str)
                .unwrap_or("");
            if !matches!(
                view,
                "front" | "right" | "top" | "back" | "left" | "bottom" | "iso"
            ) {
                continue;
            }
            let preview = previews.into_iter().flatten().find(|candidate| {
                candidate.get("revision").and_then(Value::as_i64) == Some(revision)
                    && candidate.get("preview_generation").and_then(Value::as_u64) == generation
                    && candidate
                        .get("geometry_fingerprint")
                        .and_then(Value::as_str)
                        == fingerprint
                    && candidate.get("view").and_then(Value::as_str) == Some(view)
                    && candidate.get("preview_sha256").and_then(Value::as_str)
                        == observation.get("preview_sha256").and_then(Value::as_str)
                    && candidate.get("partial").and_then(Value::as_bool) != Some(true)
            });
            let Some(preview_path) = preview
                .and_then(|p| p.get("preview_image_path"))
                .and_then(Value::as_str)
            else {
                continue;
            };
            let Some(projection) = preview
                .and_then(|p| p.pointer("/render_options/projection"))
                .and_then(Value::as_str)
            else {
                continue;
            };
            let preview_bytes = match read_image_bytes(Path::new(preview_path)) {
                Ok(bytes) => bytes,
                Err(reason) => {
                    comparisons.push(
                        json!({"reference_id":id,"view":view,"status":"unknown","reason":reason}),
                    );
                    continue;
                }
            };
            let preview_hash = format!("{:x}", sha2::Sha256::digest(&preview_bytes));
            if observation
                .get("preview_sha256")
                .and_then(Value::as_str)
                .is_none_or(|expected| !preview_hash.eq_ignore_ascii_case(expected))
            {
                comparisons.push(json!({"reference_id":id,"view":view,"status":"unknown","reason":"preview_hash_mismatch"}));
                continue;
            }
            let candidate = match measure(&preview_bytes) {
                Ok(measurement) => measurement,
                Err(reason) => {
                    comparisons.push(
                        json!({"reference_id":id,"view":view,"status":"unknown","reason":reason}),
                    );
                    continue;
                }
            };
            let disjoint = reference.high < candidate.low || candidate.high < reference.low;
            comparisons.push(json!({"reference_id":id,"view":view,"projection":projection,"status":if disjoint {"mismatch"} else {"consistent"},
                "metric":"projected_body_width_over_height","reference_interval": [reference.low,reference.high],
                "preview_interval":[candidate.low,candidate.high],"uncertainty":"threshold_sweep_and_8_percent_margin"}));
            compared += 1;
        }
        if compared == 0 {
            let reason = if current_rows.iter().any(|observation| {
                observation.get("view_comparable").and_then(Value::as_bool) == Some(false)
            }) {
                "current_pair_not_comparable"
            } else {
                "current_comparable_pair_missing"
            };
            comparisons.push(json!({"reference_id":id,"status":"unknown","reason":reason}));
        }
    }
    let has_mismatch = comparisons.iter().any(|x| x["status"] == "mismatch");
    let all_comparable =
        !comparisons.is_empty() && comparisons.iter().all(|x| x["status"] == "consistent");
    json!({"status":if has_mismatch {"mismatch"} else if all_comparable {"passed"} else {"unknown"},"comparisons":comparisons})
}

fn read_image_bytes(path: &Path) -> Result<Vec<u8>, &'static str> {
    let metadata = std::fs::metadata(path).map_err(|_| "image_unavailable")?;
    if metadata.len() > MAX_IMAGE_BYTES as u64 {
        return Err("image_over_byte_limit");
    }
    let bytes = std::fs::read(path).map_err(|_| "image_unavailable")?;
    if bytes.len() > MAX_IMAGE_BYTES {
        return Err("image_over_byte_limit");
    }
    Ok(bytes)
}

fn measure(bytes: &[u8]) -> Result<Interval, &'static str> {
    let format = image::guess_format(bytes).map_err(|_| "image_format_invalid")?;
    let mut reader = ImageReader::with_format(Cursor::new(bytes), format);
    let mut limits = Limits::default();
    limits.max_image_width = Some(16_384);
    limits.max_image_height = Some(16_384);
    limits.max_alloc = Some(128 * 1024 * 1024);
    reader.limits(limits);
    let mut decoder = reader.into_decoder().map_err(|error| match error {
        image::ImageError::Limits(_) => "image_decoder_limit_exceeded",
        _ => "image_decode_failed",
    })?;
    let (width, height) = decoder.dimensions();
    if width == 0 || height == 0 || u64::from(width) * u64::from(height) > MAX_PIXELS {
        return Err("image_over_dimension_limit");
    }
    let orientation = decoder
        .orientation()
        .map_err(|_| "image_orientation_invalid")?;
    let mut decoded = DynamicImage::from_decoder(decoder).map_err(|_| "image_decode_failed")?;
    decoded.apply_orientation(orientation);
    let rgba = decoded.to_rgba8();
    drop(decoded);
    let alpha = rgba.as_raw().chunks_exact(4).map(|p| p[3]);
    let mut min_alpha = u8::MAX;
    for value in alpha {
        min_alpha = min_alpha.min(value);
    }
    if rgba.width() < 8 || rgba.height() < 8 {
        return Err("image_too_small");
    }
    // An opaque RGBA image is equivalent to RGB. Real transparency is usable only when
    // it forms a near-continuous frame around a substantially opaque subject.
    let alpha_intervals = if min_alpha < 250 {
        transparent_alpha_intervals(&rgba)?
    } else {
        None
    };
    if let Some(intervals) = alpha_intervals {
        return Ok(intervals);
    }
    let background_lut = background_distance_lut(&rgba)?;
    let mut ratios = Vec::new();
    for threshold in [24.0, 38.0, 54.0] {
        ratios.push(segment_body_ratio(&rgba, &background_lut, threshold as u8)?);
    }
    let low = ratios.iter().copied().fold(f64::INFINITY, f64::min) * 0.92;
    let high = ratios.iter().copied().fold(f64::NEG_INFINITY, f64::max) * 1.08;
    Ok(Interval { low, high })
}

fn transparent_alpha_intervals(image: &image::RgbaImage) -> Result<Option<Interval>, &'static str> {
    let (width, height) = image.dimensions();
    let alpha_at = |x: u32, y: u32| image.get_pixel(x, y)[3];
    let edge_is_transparent = (0..width)
        .all(|x| alpha_at(x, 0) <= 16 && alpha_at(x, height - 1) <= 16)
        && (0..height).all(|y| alpha_at(0, y) <= 16 && alpha_at(width - 1, y) <= 16);
    if !edge_is_transparent {
        return Err("silhouette_alpha_background_ambiguous");
    }
    let opaque = image.pixels().filter(|p| p[3] >= 240).count();
    if opaque < (u64::from(width) * u64::from(height) / 200) as usize {
        return Err("silhouette_alpha_foreground_ambiguous");
    }
    let thresholds = [16, 64, 128];
    let mut ratios = Vec::new();
    for threshold in thresholds {
        let mut mask = vec![0u8; (width * height) as usize];
        for (index, pixel) in image.pixels().enumerate() {
            mask[index] = u8::from(pixel[3] >= threshold);
        }
        ratios.push(component_body_ratio(&mut mask, width, height)?);
    }
    let low = ratios.iter().copied().fold(f64::INFINITY, f64::min) * 0.92;
    let high = ratios.iter().copied().fold(f64::NEG_INFINITY, f64::max) * 1.08;
    Ok(Some(Interval { low, high }))
}

/// Build a small RGB5 lookup table from all sampled border colors. Unlike four-corner
/// sampling, this models gradients and repeated CAD viewport colors such as grid lines.
fn background_distance_lut(image: &RgbaImage) -> Result<Vec<u8>, &'static str> {
    let (width, height) = image.dimensions();
    let inset_x = 2;
    let inset_y = 2;
    let mut edge_colors = BTreeMap::<u16, u8>::new();
    for x in 0..width {
        for (y, edge) in [(inset_y, 1), (height - 1 - inset_y, 2)] {
            let p = image.get_pixel(x, y);
            let key = ((p[0] >> 3) as u16) << 10 | ((p[1] >> 3) as u16) << 5 | (p[2] >> 3) as u16;
            *edge_colors.entry(key).or_default() |= edge;
        }
    }
    for y in inset_y + 1..height.saturating_sub(inset_y + 1) {
        for (x, edge) in [(inset_x, 4), (width - 1 - inset_x, 8)] {
            let p = image.get_pixel(x, y);
            let key = ((p[0] >> 3) as u16) << 10 | ((p[1] >> 3) as u16) << 5 | (p[2] >> 3) as u16;
            *edge_colors.entry(key).or_default() |= edge;
        }
    }
    // A color confined to a single edge is likely a cropped object entering the frame;
    // requiring cross-edge support keeps it out of the background palette so the later
    // edge-connected component test can report crop ambiguity.
    let palette: BTreeSet<u16> = edge_colors
        .into_iter()
        .filter_map(|(color, edges)| (edges.count_ones() >= 2).then_some(color))
        .collect();
    if palette.is_empty() || palette.len() > MAX_BACKGROUND_PALETTE {
        return Err("silhouette_background_palette_ambiguous");
    }
    let mut lut = vec![u8::MAX; BACKGROUND_COLOR_BINS];
    for color in 0..BACKGROUND_COLOR_BINS {
        let rgb = [
            ((color >> 10) & 31) as i32 * 8 + 4,
            ((color >> 5) & 31) as i32 * 8 + 4,
            (color & 31) as i32 * 8 + 4,
        ];
        let mut best = 255.0f64;
        for &bg in &palette {
            let bg_rgb = [
                ((bg >> 10) & 31) as i32 * 8 + 4,
                ((bg >> 5) & 31) as i32 * 8 + 4,
                (bg & 31) as i32 * 8 + 4,
            ];
            let d2 = (0..3)
                .map(|c| (rgb[c] - bg_rgb[c]).pow(2) as f64)
                .sum::<f64>();
            best = best.min(d2.sqrt());
        }
        lut[color] = best.min(255.0) as u8;
    }
    Ok(lut)
}

fn segment_body_ratio(
    image: &RgbaImage,
    background_lut: &[u8],
    threshold: u8,
) -> Result<f64, &'static str> {
    let (width, height) = image.dimensions();
    let mut mask = Vec::with_capacity((width * height) as usize);
    for p in image.pixels() {
        let key = ((p[0] >> 3) as usize) << 10 | ((p[1] >> 3) as usize) << 5 | (p[2] >> 3) as usize;
        mask.push(u8::from(background_lut[key] > threshold));
    }
    component_body_ratio(&mut mask, width, height)
}

/// Remove border-connected residual background, reject substantial subject contact with
/// the frame, and measure the largest body component plus nearby substantial components.
fn component_body_ratio(mask: &mut [u8], width: u32, height: u32) -> Result<f64, &'static str> {
    let n = mask.len();
    let mut queue: Vec<u32> = Vec::new();
    let mut components = Vec::new();
    for start in 0..n {
        if mask[start] != 1 {
            continue;
        }
        mask[start] = 2;
        queue.clear();
        queue.push(start as u32);
        let mut min_x = width;
        let mut min_y = height;
        let mut max_x = 0;
        let mut max_y = 0;
        let mut touches_edge = false;
        let mut head = 0;
        while head < queue.len() {
            let idx = queue[head] as usize;
            head += 1;
            let x = (idx as u32) % width;
            let y = (idx as u32) / width;
            min_x = min_x.min(x);
            min_y = min_y.min(y);
            max_x = max_x.max(x);
            max_y = max_y.max(y);
            touches_edge |= x == 0 || y == 0 || x + 1 == width || y + 1 == height;
            let neighbors = [
                if x > 0 { Some(idx - 1) } else { None },
                if x + 1 < width { Some(idx + 1) } else { None },
                if y > 0 {
                    Some(idx - width as usize)
                } else {
                    None
                },
                if y + 1 < height {
                    Some(idx + width as usize)
                } else {
                    None
                },
            ];
            for next in neighbors.into_iter().flatten() {
                if mask[next] == 1 {
                    mask[next] = 2;
                    queue.push(next as u32);
                }
            }
        }
        if touches_edge && queue.len() >= n / 500 {
            return Err("silhouette_crop_ambiguous");
        }
        if !touches_edge {
            components.push((queue.len(), min_x, min_y, max_x, max_y));
        }
    }
    let Some(largest) = components.iter().map(|c| c.0).max() else {
        return Err("silhouette_segmentation_ambiguous");
    };
    if largest < n / 200 {
        return Err("silhouette_segmentation_ambiguous");
    }
    let primary_index = components
        .iter()
        .enumerate()
        .max_by_key(|(_, c)| c.0)
        .map(|(index, _)| index)
        .unwrap();
    let primary = &components[primary_index];
    let (mut min_x, mut min_y, mut max_x, mut max_y) = (primary.1, primary.2, primary.3, primary.4);
    let primary_width = max_x - min_x + 1;
    let primary_height = max_y - min_y + 1;
    let gap_limit = (primary_width.max(primary_height) as f64 * 0.08).max(2.0) as u32;
    for (index, c) in components.iter().enumerate() {
        if index == primary_index || c.0 < (largest as f64 * 0.03) as usize {
            continue;
        }
        let dx = if c.3 < min_x {
            min_x - c.3
        } else if c.1 > max_x {
            c.1 - max_x
        } else {
            0
        };
        let dy = if c.4 < min_y {
            min_y - c.4
        } else if c.2 > max_y {
            c.2 - max_y
        } else {
            0
        };
        if dx.max(dy) <= gap_limit {
            min_x = min_x.min(c.1);
            min_y = min_y.min(c.2);
            max_x = max_x.max(c.3);
            max_y = max_y.max(c.4);
        } else {
            return Err("silhouette_components_ambiguous");
        }
    }
    let w = max_x - min_x + 1;
    let h = max_y - min_y + 1;
    if w >= width * 98 / 100 || h >= height * 98 / 100 {
        return Err("silhouette_crop_ambiguous");
    }
    Ok(w as f64 / h as f64)
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{Rgb, RgbImage};
    use std::time::{SystemTime, UNIX_EPOCH};
    #[test]
    fn explicit_same_view_compares_dimensionless_envelopes_and_unlabeled_stays_unknown() {
        let root = std::env::temp_dir().join(format!(
            "proportion-gate-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&root).unwrap();
        let reference = root.join("ref.png");
        let preview = root.join("preview.png");
        let draw = |path: &Path, width: u32| {
            let mut im = RgbImage::from_pixel(100, 100, Rgb([255, 255, 255]));
            for y in 15..85 {
                for x in 50 - width / 2..50 + width / 2 {
                    im.put_pixel(x, y, Rgb([20, 20, 20]));
                }
            }
            im.save(path).unwrap();
        };
        draw(&reference, 40);
        draw(&preview, 40);
        let manifest = root.join("refs.json");
        let reference_sha = format!(
            "{:x}",
            sha2::Sha256::digest(std::fs::read(&reference).unwrap())
        );
        let preview_sha = format!(
            "{:x}",
            sha2::Sha256::digest(std::fs::read(&preview).unwrap())
        );
        std::fs::write(
            &manifest,
            serde_json::to_vec(
                &json!({"images":[{"id":"r","path":reference,"sha256":reference_sha}]}),
            )
            .unwrap(),
        )
        .unwrap();
        let pair = json!({"revision":2,"recorded_at":1,"preview_generation":3,"geometry_fingerprint":"fp","geometry_signature":"fp","view":"front","view_comparable":true,"reference_id":"r","source_sha256":reference_sha,"preview_sha256":preview_sha,"roi_norm":null});
        let state = json!({"revision":2,"preview_generation":3,"last_preview":{"geometry_fingerprint":"fp"},"previews":[{"revision":2,"preview_generation":3,"geometry_fingerprint":"fp","view":"front","preview_image_path":preview,"preview_sha256":preview_sha,"render_options":{"projection":"orthographic"}}],"reference_similarity_history":[pair.clone()]});
        assert_eq!(evaluate(&state, &manifest, 2)["status"], "passed");
        let narrow = root.join("narrow.png");
        draw(&narrow, 12);
        let narrow_sha = format!(
            "{:x}",
            sha2::Sha256::digest(std::fs::read(&narrow).unwrap())
        );
        let mut mismatch_pair = pair.clone();
        mismatch_pair["preview_sha256"] = json!(narrow_sha);
        let mismatching = json!({"revision":2,"preview_generation":3,"last_preview":{"geometry_fingerprint":"fp"},"previews":[{"revision":2,"preview_generation":3,"geometry_fingerprint":"fp","view":"front","preview_image_path":narrow,"preview_sha256":narrow_sha,"render_options":{"projection":"orthographic"}}],"reference_similarity_history":[mismatch_pair]});
        assert_eq!(evaluate(&mismatching, &manifest, 2)["status"], "mismatch");
        let mut stale = state.clone();
        stale["preview_generation"] = json!(4);
        assert_eq!(evaluate(&stale, &manifest, 2)["status"], "unknown");
        let mut stale = state.clone();
        stale["last_preview"]["geometry_fingerprint"] = json!("new-fp");
        assert_eq!(evaluate(&stale, &manifest, 2)["status"], "unknown");
        let mut stale = state.clone();
        stale["previews"][0]["view"] = json!("right");
        assert_eq!(evaluate(&stale, &manifest, 2)["status"], "unknown");
        let no_label = root.join("unknown.json");
        std::fs::write(
            &no_label,
            br#"{"images":[{"id":"r","path":"missing.png"}]}"#,
        )
        .unwrap();
        assert_eq!(evaluate(&state, &no_label, 2)["status"], "unknown");
        let mut not_comparable = state.clone();
        let mut newer_non_comparable = pair.clone();
        newer_non_comparable["recorded_at"] = json!(2);
        newer_non_comparable["view_comparable"] = json!(false);
        not_comparable["reference_similarity_history"]
            .as_array_mut()
            .unwrap()
            .push(newer_non_comparable);
        assert_eq!(evaluate(&not_comparable, &manifest, 2)["status"], "unknown");
        let oversized = root.join("oversized.bin");
        std::fs::File::create(&oversized)
            .unwrap()
            .set_len(MAX_IMAGE_BYTES as u64 + 1)
            .unwrap();
        assert_eq!(read_image_bytes(&oversized), Err("image_over_byte_limit"));
        std::fs::remove_dir_all(root).unwrap();
    }

    fn png_bytes(image: DynamicImage) -> Vec<u8> {
        let mut bytes = Cursor::new(Vec::new());
        image.write_to(&mut bytes, image::ImageFormat::Png).unwrap();
        bytes.into_inner()
    }

    fn assert_body_ratio(bytes: &[u8], expected: f64) {
        let interval = measure(bytes).expect("synthetic body should be measurable");
        let center = (interval.low + interval.high) / 2.0;
        assert!(
            (center - expected).abs() < expected * 0.14,
            "interval={interval:?}, expected={expected}"
        );
    }

    #[test]
    fn segmented_background_handles_gradient_grid_alpha_crop_and_small_marker() {
        // A CAD-style vertical gradient with grid lines that touch every image edge.
        // The opaque body occludes the grid as a CAD renderer would.
        let (w, h) = (160, 140);
        let mut grid = RgbImage::new(w, h);
        for y in 0..h {
            let shade = (210i32 - y as i32 / 2) as u8;
            for x in 0..w {
                let line = x % 20 <= 1 || y % 18 <= 1;
                grid.put_pixel(
                    x,
                    y,
                    if line {
                        Rgb([235, 235, 242])
                    } else {
                        Rgb([shade, shade, shade])
                    },
                );
            }
        }
        for y in 30..110 {
            for x in 50..110 {
                grid.put_pixel(x, y, Rgb([30, 35, 45]));
            }
        }
        assert_body_ratio(&png_bytes(DynamicImage::ImageRgb8(grid)), 0.75);

        // Smooth gradient without texture must also leave the central envelope intact.
        let mut gradient = RgbImage::new(w, h);
        for y in 0..h {
            for x in 0..w {
                let shade = (220 - (y * 80 / h) as u8).saturating_add((x / 12) as u8);
                gradient.put_pixel(x, y, Rgb([shade, shade, shade]));
            }
        }
        for y in 30..110 {
            for x in 50..110 {
                gradient.put_pixel(x, y, Rgb([25, 30, 40]));
            }
        }
        assert_body_ratio(&png_bytes(DynamicImage::ImageRgb8(gradient)), 0.75);

        // RGBA with an all-opaque channel is ordinary RGB, not ambiguous transparency.
        let mut opaque = image::RgbaImage::from_pixel(w, h, image::Rgba([200, 200, 200, 255]));
        for y in 30..110 {
            for x in 50..110 {
                opaque.put_pixel(x, y, image::Rgba([25, 30, 40, 255]));
            }
        }
        assert_body_ratio(&png_bytes(DynamicImage::ImageRgba8(opaque)), 0.75);

        // Genuine transparency provides a direct silhouette. Include antialiased alpha
        // around the subject so the threshold sweep expresses its edge uncertainty.
        let mut transparent = image::RgbaImage::from_pixel(w, h, image::Rgba([80, 90, 100, 0]));
        for y in 30..110 {
            for x in 50..110 {
                transparent.put_pixel(x, y, image::Rgba([25, 30, 40, 255]));
            }
        }
        for x in 50..110 {
            transparent.put_pixel(x, 29, image::Rgba([25, 30, 40, 96]));
        }
        assert_body_ratio(&png_bytes(DynamicImage::ImageRgba8(transparent)), 0.75);

        // A body that continues into the frame is not a valid complete envelope.
        let mut cropped = RgbImage::from_pixel(w, h, Rgb([200, 200, 200]));
        for y in 25..115 {
            for x in 0..75 {
                cropped.put_pixel(x, y, Rgb([20, 25, 35]));
            }
        }
        assert!(matches!(
            measure(&png_bytes(DynamicImage::ImageRgb8(cropped))),
            Err("silhouette_crop_ambiguous")
        ));

        // A disconnected small axis marker must not expand the body bounding box.
        let mut marker = RgbImage::from_pixel(w, h, Rgb([200, 200, 200]));
        for y in 30..110 {
            for x in 50..110 {
                marker.put_pixel(x, y, Rgb([20, 25, 35]));
            }
        }
        for y in 120..130 {
            for x in 8..18 {
                marker.put_pixel(x, y, Rgb([10, 20, 240]));
            }
        }
        assert_body_ratio(&png_bytes(DynamicImage::ImageRgb8(marker)), 0.75);
    }
}
