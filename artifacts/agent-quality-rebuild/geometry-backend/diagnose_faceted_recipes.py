"""Probe sewing/fusion tolerances for legacy faceted recipe meshes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from ai_modeler.core.geometry import occ_kernel as O
from ai_modeler.core.model.docmodel import Feature
from tests.test_cadplan_sdk import CASE_PARAMETERS


KINDS = ["socket_head_bolt", "spoked_wheel", "vbelt_pulley", "timing_pulley",
         "worm", "crankshaft", "camshaft", "impeller", "piston", "conrod",
         "cross_joint", "scroll_disc", "rotor"]


def main():
    out = []
    for kind in KINDS:
        feature = Feature(kind, CASE_PARAMETERS[kind], feature_id=kind)
        polygons = [poly for solid in feature.build({"tag": 0, "bbox": None})
                    for poly in solid]
        item = {"kind": kind, "polygon_count": len(polygons), "attempts": []}
        for tolerance in (1e-5, 1e-4, 1e-3, 1e-2):
            try:
                shape, info = O._polygon_mesh_to_brep(polygons, tolerance=tolerance)
                item["attempts"].append({
                    "tolerance_mm": tolerance, "state": "valid",
                    "solid_count": info["solid_count"],
                    "face_count": info["face_count"],
                    "volume_mm3": info["volume_mm3"],
                })
                # Test boolean fusion of disconnected but potentially touching
                # shells without replacing source geometry or hidden fallback.
                solids = []
                from OCP.TopAbs import TopAbs_SOLID
                from OCP.TopExp import TopExp_Explorer
                from OCP.TopoDS import TopoDS
                explorer = TopExp_Explorer(shape, TopAbs_SOLID)
                while explorer.More():
                    solids.append(TopoDS.Solid_s(explorer.Current()))
                    explorer.Next()
                if len(solids) > 1:
                    fused = O.fuse_many(solids)
                    item["attempts"][-1]["boolean_fuse"] = O.inspect_shape(fused)
                break
            except Exception as exc:
                item["attempts"].append({"tolerance_mm": tolerance,
                                         "state": "failed", "error": str(exc)})
        out.append(item)
    target = Path(__file__).resolve().parent / "faceted-recipe-sewing-probe.json"
    target.write_text(json.dumps({"mode": "diagnostic_not_agent", "results": out},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", target)
    print(json.dumps({row["kind"]: row["attempts"][-1] for row in out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
