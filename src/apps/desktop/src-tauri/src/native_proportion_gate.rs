//! Conservative, host-computed 2D body-envelope proportion evidence.
//!
//! Measurements are dimensionless projected silhouette ratios. They are never interpreted
//! as absolute dimensions. A comparison is made only for an exact, current reference/preview
//! pair whose persisted vision observation marks the pair comparable.
use image::{DynamicImage, ImageDecoder, ImageReader, Limits};
use serde_json::{Value, json};
use sha2::Digest;
use std::{collections::BTreeMap, io::Cursor, path::Path};

const MAX_IMAGE_BYTES: usize = 20 * 1024 * 1024;
const MAX_PIXELS: u64 = 16_777_216;

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
    if decoded.color().has_alpha() {
        return Err("silhouette_alpha_background_ambiguous");
    }
    decoded.apply_orientation(orientation);
    let image = decoded.to_rgb8();
    if image.width() < 8 || image.height() < 8 {
        return Err("image_too_small");
    }
    // Estimate a flat background from the four corners; reject textured/ambiguous scenes.
    let corners = [
        (0, 0),
        (image.width() - 1, 0),
        (0, image.height() - 1),
        (image.width() - 1, image.height() - 1),
    ];
    let bg = corners.map(|(x, y)| image.get_pixel(x, y).0.map(f64::from));
    let spread = (0..3)
        .map(|c| {
            bg.iter().map(|p| p[c]).fold(f64::NEG_INFINITY, f64::max)
                - bg.iter().map(|p| p[c]).fold(f64::INFINITY, f64::min)
        })
        .fold(0.0, f64::max);
    if spread > 40.0 {
        return Err("silhouette_background_ambiguous");
    }
    let background = std::array::from_fn::<_, 3, _>(|c| bg.iter().map(|p| p[c]).sum::<f64>() / 4.0);
    let mut ratios = Vec::new();
    for threshold in [24.0, 38.0, 54.0] {
        let mut min_x = image.width();
        let mut min_y = image.height();
        let mut max_x = 0;
        let mut max_y = 0;
        let mut count = 0u32;
        for (x, y, p) in image.enumerate_pixels() {
            let delta = (0..3)
                .map(|c| (f64::from(p[c]) - background[c]).powi(2))
                .sum::<f64>()
                .sqrt();
            if delta >= threshold {
                min_x = min_x.min(x);
                min_y = min_y.min(y);
                max_x = max_x.max(x);
                max_y = max_y.max(y);
                count += 1;
            }
        }
        if count < image.width().saturating_mul(image.height()) / 200
            || max_x <= min_x
            || max_y <= min_y
        {
            return Err("silhouette_segmentation_ambiguous");
        }
        let w = (max_x - min_x + 1) as f64;
        let h = (max_y - min_y + 1) as f64;
        if w >= image.width() as f64 * 0.98 || h >= image.height() as f64 * 0.98 {
            return Err("silhouette_crop_ambiguous");
        }
        ratios.push(w / h);
    }
    let low = ratios.iter().copied().fold(f64::INFINITY, f64::min) * 0.92;
    let high = ratios.iter().copied().fold(f64::NEG_INFINITY, f64::max) * 1.08;
    Ok(Interval { low, high })
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
}
