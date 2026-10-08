#!/usr/bin/env python3
"""Generate the Rust Quick SW50/SW80 mesh catalogue from measured sources.

The source of truth is `kb/egt_library.json` for section/model dimensions and
`kb/real_features.json` for the measured coordinate frame and hole cylinders.
Only SW50/SW80 profiles are emitted; the intersecting D30 family is excluded.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "kb" / "egt_library.json"
REAL_FEATURES = ROOT / "kb" / "real_features.json"
OUTPUT = ROOT.parent / "apps" / "desktop" / "src-tauri" / "src" / "native_quick_egt_sw_catalog.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    library = json.loads(LIBRARY.read_text(encoding="utf-8"))
    real_features = json.loads(REAL_FEATURES.read_text(encoding="utf-8"))
    profiles = []
    for profile in library["profiles"].values():
        if profile.get("section") not in {"SW50", "SW80"}:
            continue
        for code_text, record in profile["records"].items():
            model = f"{profile['family']} {profile['section']}x{int(float(code_text)):03d}"
            filename = f"{profile['family']} {profile['section']}x{int(float(code_text)):03d}.stp"
            measured = real_features.get(filename)
            if not measured or not measured.get("cylinders"):
                raise SystemExit(f"missing measured holes: {filename}")
            envelope = [float(value) for value in measured["envelope"]]
            axis_index = max(range(3), key=lambda index: envelope[index])
            holes = [
                {
                    "d": float(hole["d"]),
                    "axis": str(hole["axis"]).upper(),
                    "u": float(hole["u"]),
                    "v": float(hole["v"]),
                    "through": True,
                }
                for hole in measured["cylinders"]
                if float(hole["d"]) >= 2.0
            ]
            if not holes:
                raise SystemExit(f"empty hole list: {filename}")
            profiles.append(
                {
                    "id": f"{profile['family']}:{profile['section']}:{int(float(code_text))}",
                    "model": model,
                    "family": profile["family"],
                    "section_code": profile["section"],
                    "code": int(float(code_text)),
                    "length_mm": float(record["length"]),
                    "sec_a_mm": envelope[[i for i in range(3) if i != axis_index][0]],
                    "sec_b_mm": envelope[[i for i in range(3) if i != axis_index][1]],
                    "axis": "XYZ"[axis_index],
                    "base_mm": -envelope[axis_index] / 2.0,
                    "ref_min_mm": [float(value) for value in measured["bbox_min"]],
                    "ref_size_mm": envelope,
                    "holes": holes,
                    "source_file": filename,
                }
            )
    profiles.sort(key=lambda item: (item["family"], item["code"]))
    if len(profiles) != 267:
        raise SystemExit(f"expected 267 SW profiles, found {len(profiles)}")
    output = {
        "schema_version": 1,
        "source": {
            "library": "src/legacy/kb/egt_library.json",
            "library_sha256": sha256(LIBRARY),
            "real_features": "src/legacy/kb/real_features.json",
            "real_features_sha256": sha256(REAL_FEATURES),
            "builder": "src/legacy/ai_modeler/parts/measured/realparts.py::build_egt_profile_real",
            "scope": "EGT001 SW80 and EGT002 SW50 only; measured non-intersecting hole models",
        },
        "profiles": profiles,
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {len(profiles)} measured SW profiles to {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
