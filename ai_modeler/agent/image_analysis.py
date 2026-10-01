# -*- coding: utf-8 -*-
"""Lightweight pixel evidence for simple, flat-color CAD reference images.

This is a measurement aid for the multimodal agent, not a replacement for
visual recognition. Images that do not contain clear horizontal region runs
simply produce no candidate and remain for the model to interpret.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

# A three-region scanline is only evidence when the very same segmentation
# repeats over a real vertical span of the part. Below this share of the image
# height the rows come from an axonometric top face or a background grid.
MIN_SCANLINE_SPAN_RATIO = 0.25


def _color_distance(left: Tuple[int, int, int], right: Tuple[int, int, int]) -> int:
    return sum(abs(a - b) for a, b in zip(left, right))


def _horizontal_runs(pixels, width: int, y: int) -> List[Tuple[int, int, Tuple[int, int, int]]]:
    runs = []
    start = 0
    color = pixels[0, y]
    for x in range(1, width):
        next_color = pixels[x, y]
        if next_color != color:
            runs.append((start, x - 1, color))
            start, color = x, next_color
    runs.append((start, width - 1, color))
    return runs


def _candidate_rows(image) -> List[Dict[str, Any]]:
    width, height = image.size
    pixels = image.load()
    min_background_run = max(4, int(width * 0.02))
    min_region_run = max(8, int(width * 0.06))
    rows = []
    for y in range(max(1, int(height * 0.05)), min(height - 1, int(height * 0.95))):
        runs = _horizontal_runs(pixels, width, y)
        left_bg = next((run for run in runs if run[0] == 0), None)
        right_bg = next((run for run in reversed(runs) if run[1] == width - 1), None)
        if not left_bg or not right_bg:
            continue
        if (left_bg[1] - left_bg[0] + 1 < min_background_run
                or right_bg[1] - right_bg[0] + 1 < min_background_run
                or _color_distance(left_bg[2], right_bg[2]) > 24):
            continue
        background_colors = {left_bg[2], right_bg[2]}
        regions = [run for run in runs
                   if run[2] not in background_colors
                   and run[1] - run[0] + 1 >= min_region_run]
        if len(regions) != 3:
            continue
        widths = [run[1] - run[0] + 1 for run in regions]
        coverage = sum(widths) / float(width)
        if coverage < 0.35:
            continue
        total = float(sum(widths))
        rows.append({
            "y_px": y,
            "segments": [{"x_start_px": run[0], "x_end_px": run[1],
                          "width_px": region_width}
                         for run, region_width in zip(regions, widths)],
            "width_ratios": [region_width / total for region_width in widths],
            "coverage": coverage,
        })
    return rows


def analyze_reference_images(image_paths: Iterable[os.PathLike | str]) -> Dict[str, Any]:
    """Return scanline candidates for each image; gracefully skip if Pillow is absent."""
    try:
        from PIL import Image
    except ImportError:
        return {"available": False, "reason": "Pillow is unavailable", "images": []}

    results = []
    for index, raw_path in enumerate(image_paths, start=1):
        path = os.fspath(raw_path)
        result = {"image_index": index, "filename": os.path.basename(path)}
        try:
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.thumbnail((640, 640))
                result["size_px"] = list(image.size)
                rows = _candidate_rows(image)
        except Exception as exc:
            result["error"] = str(exc)
            results.append(result)
            continue
        if rows:
            best = max(rows, key=lambda row: (row["coverage"], -abs(row["y_px"] - image.height / 2)))
            consistent = [row for row in rows
                          if max(abs(a - b) for a, b in zip(row["width_ratios"],
                                                            best["width_ratios"])) <= 0.03]
            confidence = "high" if len(consistent) >= 5 and best["coverage"] >= 0.65 else (
                "medium" if len(consistent) >= 2 and best["coverage"] >= 0.45 else "low")
            x_min = min(run["x_start_px"] for row in consistent for run in row["segments"])
            x_max = max(run["x_end_px"] for row in consistent for run in row["segments"])
            y_min = min(row["y_px"] for row in consistent)
            y_max = max(row["y_px"] for row in consistent)
            bbox_width = x_max - x_min + 1
            bbox_height = y_max - y_min + 1
            span_ratio = bbox_height / float(image.height)
            if span_ratio < MIN_SCANLINE_SPAN_RATIO:
                # A flat orthographic view keeps the same three regions for most
                # of the part's height. A handful of rows at one y is an
                # axonometric top face, a grid line, or a highlight -- not a
                # measurement. Publish the refusal instead of the ratios: a
                # caveat in `note` was ignored and produced a 14.5/71/14.5 mm
                # part from an isometric screenshot.
                result["three_region_scanline_candidate"] = None
                result["foreground_bbox_candidate"] = None
                result["scanline_rejected"] = {
                    "consistent_scanline_count": len(consistent),
                    "span_px": bbox_height,
                    "image_height_px": image.height,
                    "span_ratio": round(span_ratio, 4),
                    "min_span_ratio": MIN_SCANLINE_SPAN_RATIO,
                    "reason": ("扫描线只在 %d/%d px 高度上一致（%.1f%% < %.0f%%），"
                               "不足以判定为平色正视图；本图不得作为比例尺寸证据。"
                               % (bbox_height, image.height, span_ratio * 100,
                                  MIN_SCANLINE_SPAN_RATIO * 100)),
                    "instruction": ("不要把该图的像素比例写进 sources.evidence。"
                                    "如需尺寸，按整体轮廓做目视比例估算，或向用户确认。"),
                }
                results.append(result)
                continue
            result["three_region_scanline_candidate"] = {
                **best,
                "consistent_scanline_count": len(consistent),
                "confidence": confidence,
                "usable_for_dimensions": True,
                "note": "Pixel evidence only; confirm this image is a front orthographic view before using the ratios.",
            }
            result["foreground_bbox_candidate"] = {
                "x_min_px": x_min, "x_max_px": x_max,
                "y_min_px": y_min, "y_max_px": y_max,
                "width_px": bbox_width, "height_px": bbox_height,
                "width_height_ratio": bbox_width / float(bbox_height),
                "confidence": confidence,
                "note": "Pixel evidence only; use as front width/height only if the model identifies this image as orthographic front.",
            }
        else:
            result["three_region_scanline_candidate"] = None
            result["foreground_bbox_candidate"] = None
        results.append(result)
    return {"available": True, "images": results}


def _pixel_values(image):
    """Pixel access that survives Pillow's getdata deprecation."""
    return list(image.get_flattened_data()
                if hasattr(image, "get_flattened_data") else image.getdata())


def _mask_values(image):
    return _pixel_values(image)


def _foreground_values(image):
    """Boolean foreground samples for one flat-background render.

    Returns ``(values, bbox, count)`` or ``(None, None, 0)`` when the foreground
    cannot be separated from the background.
    """
    width, height = image.size
    pixels = _pixel_values(image)
    values = []
    inconsistent_border_rows = 0
    for y in range(height):
        left, right = pixels[y * width], pixels[(y + 1) * width - 1]
        if _color_distance(left, right) > 24:
            inconsistent_border_rows += 1
        background = tuple((a + b) // 2 for a, b in zip(left, right))
        values.extend(_color_distance(pixel, background) > 60
                      for pixel in pixels[y * width:(y + 1) * width])
    if inconsistent_border_rows > height * 0.1:
        return None, None, 0
    count = sum(values)
    if count < width * height * 0.01:
        return None, None, 0
    xs = [index % width for index, present in enumerate(values) if present]
    ys = [index // width for index, present in enumerate(values) if present]
    return values, [min(xs), min(ys), max(xs), max(ys)], count


def _tolerant_foreground(image, radius: int = 2):
    """Foreground for screenshots whose background is not a flat band.

    The strict separator needs the same flat background at both edges of a row;
    a perspective grid over a gradient background never provides that. Each row
    here takes its own background from its darker border pixel, and a
    (2*radius+1)^2 erosion removes the thin grid lines that survive the
    threshold. One modal border colour is not enough: on a gradient it marks most
    of the background as foreground, which made a whole canvas look like a part.
    """
    width, height = image.size
    pixels = _pixel_values(image)
    raw = [False] * len(pixels)
    for y in range(height):
        row = y * width
        left, right = pixels[row], pixels[row + width - 1]
        background = left if sum(left) <= sum(right) else right
        raw[row:row + width] = [
            _color_distance(pixels[row + x], background) > 60 for x in range(width)]
    eroded = [False] * len(raw)
    offsets = [(dy, dx) for dy in range(-radius, radius + 1)
               for dx in range(-radius, radius + 1)]
    for y in range(radius, height - radius):
        row = y * width
        for x in range(radius, width - radius):
            index = row + x
            if raw[index] and all(raw[index + dy * width + dx] for dy, dx in offsets):
                eroded[index] = True
    count = sum(eroded)
    if count < width * height * 0.005:
        return None, None, 0
    xs = [index % width for index, present in enumerate(eroded) if present]
    ys = [index // width for index, present in enumerate(eroded) if present]
    return eroded, [min(xs), min(ys), max(xs), max(ys)], count


def _cropped_advisory(reference, model, reason: str) -> Dict[str, Any]:
    """Weak, clearly-advisory comparison for canvases that cannot be overlaid."""
    try:
        from PIL import Image
    except ImportError:
        return {"state": "not_applicable", "reason": reason}

    def cropped(image):
        values, bbox, _count = _foreground_values(image)
        if values is None:
            values, bbox, _count = _tolerant_foreground(image)
        if values is None:
            return None, None
        mask = Image.new("L", image.size)
        mask.putdata([255 if present else 0 for present in values])
        return mask.crop((bbox[0], bbox[1], bbox[2] + 1, bbox[3] + 1)), bbox

    reference_mask, reference_bbox = cropped(reference)
    model_mask, model_bbox = cropped(model)
    if reference_mask is None or model_mask is None:
        return {"state": "not_applicable", "reason": reason,
                "fallback": "foreground not separable",
                "instruction": ("本轮没有任何可用的独立像素对照；"
                                "结束前必须在复核里逐项写出参考图与模型各自的可见证据，"
                                "不得只写结论。")}
    grid = (128, 128)
    left = _mask_values(reference_mask.resize(grid, Image.NEAREST))
    right = _mask_values(model_mask.resize(grid, Image.NEAREST))
    intersection = sum(1 for a, b in zip(left, right) if a and b)
    union = sum(1 for a, b in zip(left, right) if a or b)
    reference_aspect = reference_mask.size[0] / float(reference_mask.size[1])
    model_aspect = model_mask.size[0] / float(model_mask.size[1])
    return {
        "state": "advisory",
        "method": "cropped foreground aspect + normalised overlap",
        "reason": reason,
        "reference_bbox_px": reference_bbox,
        "model_bbox_px": model_bbox,
        "reference_aspect": round(reference_aspect, 4),
        "model_aspect": round(model_aspect, 4),
        "aspect_delta": round(abs(reference_aspect - model_aspect) / reference_aspect, 4)
        if reference_aspect else None,
        "iou": round(intersection / union, 4) if union else 0.0,
        "scope": ("advisory only: the crop and scale differ, so this can neither "
                  "accept nor reject a model. Report it as an observation, never as "
                  "evidence that the geometry matches."),
    }


def compare_same_renderer_silhouette(reference_path, model_path, threshold=0.95):
    """Compare aligned flat-render silhouettes only when canvas style matches.

    A camera change, screenshot crop, or different background makes a pixel
    overlay score misleading, so those inputs never produce a pass/fail. They do
    still produce an advisory crop comparison: returning a bare
    ``not_applicable`` left the whole acceptance decision to the model's own
    reading of the image.
    """
    try:
        from PIL import Image
    except ImportError:
        return {"state": "not_applicable", "reason": "Pillow unavailable"}
    try:
        with Image.open(reference_path) as source, Image.open(model_path) as output:
            reference = source.convert("RGB")
            model = output.convert("RGB")
    except Exception as exc:
        return {"state": "not_applicable", "reason": str(exc)}

    strict_reason = None
    if reference.size != model.size or min(reference.size) < 64:
        strict_reason = "canvas sizes differ"
    else:
        width, height = reference.size
        corners = ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
        if any(_color_distance(reference.getpixel(xy), model.getpixel(xy)) > 12
               for xy in corners):
            strict_reason = "render backgrounds differ"
    if strict_reason:
        return _cropped_advisory(reference, model, strict_reason)

    width, height = reference.size
    reference_mask, reference_bbox, reference_count = _foreground_values(reference)
    model_mask, model_bbox, model_count = _foreground_values(model)
    if reference_mask is None or model_mask is None:
        return _cropped_advisory(reference, model, "foreground not separable")
    intersection = sum(a and b for a, b in zip(reference_mask, model_mask))
    union = reference_count + model_count - intersection
    iou = intersection / union if union else 0.0
    return {
        "state": "measured", "method": "same-canvas foreground mask overlap",
        "iou": round(iou, 4), "threshold": threshold,
        "passed": iou >= threshold,
        "reference_bbox_px": reference_bbox, "model_bbox_px": model_bbox,
        "reference_foreground_px": reference_count,
        "model_foreground_px": model_count,
        "scope": "applies only to aligned renders with matching canvas and background",
    }
