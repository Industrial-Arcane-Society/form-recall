//! Immutable session reference selection and in-memory, display-oriented ROIs.
use crate::native_vision_provider::VerifiedImage;
use image::{DynamicImage, ImageDecoder, ImageFormat, ImageReader, Limits};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Cursor, Read},
    path::Path,
};

const MAX_SOURCE_BYTES: u64 = 20 * 1024 * 1024;
const MAX_MANIFEST_BYTES: u64 = 4 * 1024 * 1024;
const MAX_PIXELS: u64 = 64_000_000;

pub struct ReferenceInput {
    pub image: VerifiedImage,
    pub metadata: Value,
    pub reference_index: usize,
    pub roi_cache_key: String,
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn read_bounded(path: &Path, maximum: u64) -> Result<Vec<u8>, String> {
    let mut file = fs::File::open(path).map_err(|e| format!("无法读取会话图片/清单：{e}"))?;
    if file.metadata().map_err(|e| e.to_string())?.len() > maximum {
        return Err("会话图片/清单超过大小上限。".into());
    }
    let mut bytes = Vec::new();
    file.by_ref()
        .take(maximum + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > maximum {
        return Err("会话图片/清单在读取期间超过大小上限。".into());
    }
    Ok(bytes)
}

fn decode(bytes: &[u8]) -> Result<(DynamicImage, &'static str, u8), String> {
    let format = image::guess_format(bytes).map_err(|_| "无法识别图片编码。")?;
    let mime = match format {
        ImageFormat::Png => "image/png",
        ImageFormat::Jpeg => "image/jpeg",
        ImageFormat::WebP => "image/webp",
        ImageFormat::Gif => "image/gif",
        _ => return Err("仅支持 PNG/JPEG/WebP/GIF 参考图。".into()),
    };
    let mut reader = ImageReader::with_format(Cursor::new(bytes), format);
    let mut limits = Limits::default();
    limits.max_image_width = Some(16_384);
    limits.max_image_height = Some(16_384);
    limits.max_alloc = Some(256 * 1024 * 1024);
    reader.limits(limits);
    let mut decoder = reader
        .into_decoder()
        .map_err(|e| format!("图片解码失败：{e}"))?;
    let (width, height) = decoder.dimensions();
    if width == 0 || height == 0 || u64::from(width) * u64::from(height) > MAX_PIXELS {
        return Err("参考图像素数超过限制或尺寸为空。".into());
    }
    let orientation = decoder
        .orientation()
        .map_err(|e| format!("图片方向解析失败：{e}"))?;
    let exif = orientation.to_exif();
    let mut decoded =
        DynamicImage::from_decoder(decoder).map_err(|e| format!("图片解码失败：{e}"))?;
    decoded.apply_orientation(orientation);
    Ok((decoded, mime, exif))
}

/// Metadata uses the same display-oriented pixel space as crop coordinates.
pub fn image_metadata(bytes: &[u8]) -> Result<Value, String> {
    let (image, mime, orientation) = decode(bytes)?;
    Ok(
        json!({"width":image.width(),"height":image.height(),"media_type":mime,
        "exif_orientation":orientation,"roi_coordinate_space":"display_pixels_after_exif"}),
    )
}

/// Select only a Host-authored manifest entry. Tool arguments cannot supply a path.
/// Originals are rehashed on every call; only a crop's transport bytes are encoded.
pub fn reference_input(
    manifest: &Path,
    reference_id: Option<&str>,
    index: Option<usize>,
    roi: Option<&Value>,
) -> Result<ReferenceInput, String> {
    let value: Value = serde_json::from_slice(&read_bounded(manifest, MAX_MANIFEST_BYTES)?)
        .map_err(|e| format!("参考图清单损坏：{e}"))?;
    let rows = value
        .get("images")
        .and_then(Value::as_array)
        .ok_or("参考图清单缺少 images 数组。")?;
    if index == Some(0) {
        return Err("reference_image_index 从 1 开始。".into());
    }
    let selected = if let Some(id) = reference_id {
        rows.iter()
            .position(|row| row["id"].as_str() == Some(id))
            .ok_or("reference_id 不在当前会话原图清单中。")?
    } else {
        index.unwrap_or(1).checked_sub(1).ok_or("图片索引无效。")?
    };
    if reference_id.is_some() && index.is_some_and(|i| i != selected + 1) {
        return Err("reference_id 与 reference_image_index 指向不同原图。".into());
    }
    let row = rows
        .get(selected)
        .ok_or("reference_image_index 超出当前原图清单。")?;
    let bytes = read_bounded(
        Path::new(row["path"].as_str().ok_or("原图清单缺少 path。")?),
        MAX_SOURCE_BYTES,
    )?;
    let source_sha = digest(&bytes);
    let id = format!("sha256:{source_sha}");
    if row["id"].as_str() != Some(id.as_str())
        || row["sha256"].as_str() != Some(source_sha.as_str())
    {
        return Err("原图实际 SHA 与会话稳定 ID/清单不一致；拒绝出网。".into());
    }
    let (decoded, source_mime, orientation) = decode(&bytes)?;
    if row
        .get("media_type")
        .and_then(Value::as_str)
        .is_some_and(|m| m != source_mime)
    {
        return Err("原图 media_type 与实际编码不一致。".into());
    }
    let normalized = normalize_roi(roi)?;
    let (transport, mime) = if let Some([x, y, w, h]) = normalized {
        let width = decoded.width() as f64;
        let height = decoded.height() as f64;
        let left = (x * width).round_ties_even() as u32;
        let top = (y * height).round_ties_even() as u32;
        let right = ((x + w) * width).round_ties_even().min(width) as u32;
        let bottom = ((y + h) * height).round_ties_even().min(height) as u32;
        if right <= left || bottom <= top {
            return Err("ROI 在像素空间为空。".into());
        }
        let cropped = decoded.crop_imm(left, top, right - left, bottom - top);
        let mut buffer = Cursor::new(Vec::new());
        cropped
            .write_to(&mut buffer, ImageFormat::Png)
            .map_err(|e| e.to_string())?;
        (buffer.into_inner(), "image/png")
    } else {
        (bytes, source_mime)
    };
    if transport.len() as u64 > MAX_SOURCE_BYTES {
        return Err("ROI PNG 编码超过出网大小上限。".into());
    }
    let roi_value = normalized.map(|r| json!(r));
    let cache_projection = json!({"source_sha256":source_sha,"roi_norm":roi_value});
    let roi_cache_key = digest(&serde_json::to_vec(&cache_projection).map_err(|e| e.to_string())?);
    let transport_sha256 = digest(&transport);
    Ok(ReferenceInput {
        metadata: json!({"id":id,"sha256":source_sha,"filename":row.get("filename"),
        "width":decoded.width(),"height":decoded.height(),"media_type":source_mime,"exif_orientation":orientation,
        "roi_coordinate_space":"display_pixels_after_exif","roi_norm":roi_value}),
        image: VerifiedImage {
            bytes: transport,
            mime: mime.into(),
            reference_id: Some(id),
            source_sha256: Some(source_sha),
            transport_sha256,
            roi_norm: roi_value,
            preview: None,
        },
        reference_index: selected + 1,
        roi_cache_key,
    })
}

fn normalize_roi(value: Option<&Value>) -> Result<Option<[f64; 4]>, String> {
    let Some(value) = value.filter(|v| !v.is_null()) else {
        return Ok(None);
    };
    let array = value
        .as_array()
        .filter(|a| a.len() == 4)
        .ok_or("roi_norm 必须为 [x,y,width,height]。")?;
    let mut roi = [0.; 4];
    for (i, v) in array.iter().enumerate() {
        roi[i] = v
            .as_f64()
            .filter(|n| n.is_finite())
            .ok_or("ROI 值必须为有限数值。")?;
    }
    let [x, y, w, h] = roi;
    if x < 0. || y < 0. || w <= 0. || h <= 0. || x + w > 1. || y + h > 1. {
        return Err("ROI 必须位于原图内，宽高必须大于零。".into());
    }
    Ok(Some(roi.map(|n| {
        (n * 100_000_000.).round_ties_even() / 100_000_000.
    })))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn jpeg_exif_rotation_sets_display_dimensions_before_roi() {
        let mut encoded = Cursor::new(Vec::new());
        DynamicImage::ImageRgb8(image::RgbImage::new(2, 3))
            .write_to(&mut encoded, ImageFormat::Jpeg)
            .unwrap();
        let original = encoded.into_inner();
        // APP1 TIFF orientation=6 (90 degrees clockwise), preserving JPEG data.
        let exif = b"Exif\0\0II\x2a\0\x08\0\0\0\x01\0\x12\x01\x03\0\x01\0\0\0\x06\0\0\0\0\0\0\0";
        let mut jpeg = original[..2].to_vec();
        jpeg.extend_from_slice(b"\xff\xe1");
        jpeg.extend_from_slice(&((exif.len() + 2) as u16).to_be_bytes());
        jpeg.extend_from_slice(exif);
        jpeg.extend_from_slice(&original[2..]);
        let meta = image_metadata(&jpeg).unwrap();
        assert_eq!(meta["exif_orientation"], 6);
        assert_eq!(
            (meta["width"].as_u64(), meta["height"].as_u64()),
            (Some(3), Some(2))
        );
        assert_eq!(meta["roi_coordinate_space"], "display_pixels_after_exif");
    }
    fn fixture() -> (std::path::PathBuf, std::path::PathBuf, Vec<u8>, String) {
        let root = std::env::temp_dir().join(format!(
            "vision-assets-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        let mut bytes = Cursor::new(Vec::new());
        DynamicImage::ImageRgb8(image::RgbImage::from_fn(8, 6, |x, y| {
            image::Rgb([x as u8, y as u8, 5])
        }))
        .write_to(&mut bytes, ImageFormat::Png)
        .unwrap();
        let bytes = bytes.into_inner();
        let sha = digest(&bytes);
        let id = format!("sha256:{sha}");
        let path = root.join("source.png");
        fs::write(&path, &bytes).unwrap();
        let manifest = root.join("refs.json");
        fs::write(
            &manifest,
            serde_json::to_vec(&json!({"images":[{"id":id,"sha256":sha,"path":path,
            "filename":"reference.png","media_type":"image/png"}]}))
            .unwrap(),
        )
        .unwrap();
        (root, manifest, bytes, id)
    }
    #[test]
    fn originals_preserve_bytes_and_roi_is_memory_only_with_distinct_hash() {
        let (dir, manifest, bytes, id) = fixture();
        let original = reference_input(&manifest, Some(&id), Some(1), None).unwrap();
        assert_eq!(original.image.bytes, bytes);
        assert_eq!(original.metadata["width"], 8);
        let crop = reference_input(
            &manifest,
            Some(&id),
            Some(1),
            Some(&json!([0.25, 0., 0.5, 1.])),
        )
        .unwrap();
        assert_ne!(crop.image.transport_sha256, original.image.transport_sha256);
        assert_eq!(crop.image.source_sha256, original.image.source_sha256);
        assert_ne!(crop.roi_cache_key, original.roi_cache_key);
        let decoded = image::load_from_memory(&crop.image.bytes).unwrap();
        assert_eq!((decoded.width(), decoded.height()), (4, 6));
        assert_eq!(fs::read_dir(&dir).unwrap().count(), 2);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn source_mutation_index_conflict_and_invalid_roi_are_rejected() {
        let (dir, manifest, _, id) = fixture();
        assert!(reference_input(&manifest, Some(&id), Some(2), None).is_err());
        assert!(reference_input(&manifest, None, Some(0), None).is_err());
        assert!(
            reference_input(&manifest, Some(&id), None, Some(&json!([0., 0., 1.1, 1.]))).is_err()
        );
        fs::write(dir.join("source.png"), b"modified source").unwrap();
        assert!(
            reference_input(&manifest, Some(&id), None, None)
                .err()
                .unwrap()
                .contains("SHA")
        );
        fs::remove_dir_all(dir).unwrap();
    }
}
