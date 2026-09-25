# -*- coding: utf-8 -*-
"""Lightweight pixel evidence for simple, flat-color CAD reference images.

This is a measurement aid for the multimodal agent, not a replacement for
visual recognition. Images that do not contain clear horizontal region runs
simply produce no candidate and remain for the model to interpret.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Tuple


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
            result["three_region_scanline_candidate"] = {
                **best,
                "consistent_scanline_count": len(consistent),
                "confidence": confidence,
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
