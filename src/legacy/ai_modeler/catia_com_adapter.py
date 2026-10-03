"""Minimal on-demand COM adapter for Rust's F5 `.aimodel` active model.

This module deliberately does not import `ai_modeler.Document`, CADPlan Python
code, the legacy geometry kernel, or the MCP server. Geometry operations are
derived directly from the versioned JSON project written by the Rust host.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

PROTOCOL = "ai-modeler-catia-com-v1"
AXIS_MAP = {"Z": (0, 1, 2), "X": (1, 2, 0), "Y": (2, 0, 1)}
TOLERANCE = 1e-6


class AdapterError(ValueError):
    pass


def _vec3(value: Any, label: str, default: tuple[float, float, float] = (0, 0, 0)) -> tuple[float, float, float]:
    if value is None:
        return tuple(float(v) for v in default)
    if not isinstance(value, list) or len(value) != 3:
        raise AdapterError(f"{label} must be an XYZ array")
    result = tuple(float(v) for v in value)
    if not all(math.isfinite(v) for v in result):
        raise AdapterError(f"{label} contains a non-finite value")
    return result


def _finite(params: dict[str, Any], key: str, default: float | None = None) -> float:
    value = params.get(key, default)
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise AdapterError(f"parameter `{key}` must be a number") from None
    if not math.isfinite(result):
        raise AdapterError(f"parameter `{key}` must be finite")
    return result


def _merge_bounds(bounds: list[list[float]] | None, lo: tuple[float, float, float], hi: tuple[float, float, float]) -> list[list[float]]:
    if bounds is None:
        return [[lo[i], hi[i]] for i in range(3)]
    return [[min(bounds[i][0], lo[i]), max(bounds[i][1], hi[i])] for i in range(3)]


def _name(feature: dict[str, Any], index: int) -> str:
    name = str(feature.get("name") or feature.get("kind") or f"Feature {index}")
    return " ".join(name.split())[:70] or f"Feature {index}"


def _sources(feature: dict[str, Any], cadplan_sources: dict[str, Any]) -> dict[str, Any]:
    if isinstance(cadplan_sources, dict) and cadplan_sources:
        sources = cadplan_sources
        return sources
    provenance = feature.get("provenance")
    if not isinstance(provenance, dict):
        return {}
    return {key: {"type": value} for key, value in provenance.items()}


def _source_label(sources: dict[str, Any]) -> str:
    types = sorted({str(source.get("type")) for source in sources.values() if isinstance(source, dict) and source.get("type")})
    return ",".join(types) if types else "unspecified"


def _circle_operation(kind: str, axis: str, u: float, v: float, offset: float, radius: float,
                      height: float, feature: dict[str, Any], index: int, source: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "plane": axis, "cx": u, "cy": v, "z": offset, "r": radius,
            "h" if kind == "circle_pad" else "depth": height, "name": _name(feature, index),
            "feature_id": feature.get("feature_id"), "source": source}


def _hole_positions(params: dict[str, Any], axis: str, radius: float,
                    bounds: list[list[float]] | None) -> list[tuple[float, float]]:
    if params.get("pos") == "corners":
        if bounds is None:
            raise AdapterError("corner holes need a preceding CATIA-supported body")
        ui, vi, _ = AXIS_MAP[axis]
        margin = _finite(params, "margin", 0.0) + radius
        return [
            (bounds[ui][0] + margin, bounds[vi][0] + margin),
            (bounds[ui][1] - margin, bounds[vi][0] + margin),
            (bounds[ui][1] - margin, bounds[vi][1] - margin),
            (bounds[ui][0] + margin, bounds[vi][1] - margin),
        ]
    if params.get("pos") == "pcd":
        count = max(2, int(_finite(params, "count", 4)))
        pitch_radius = _finite(params, "pcd", 60.0) / 2.0
        return [(pitch_radius * math.cos(2 * math.pi * i / count),
                 pitch_radius * math.sin(2 * math.pi * i / count)) for i in range(count)]
    if params.get("pos") == "point":
        point = _vec3(params.get("point"), "hole point")
        # F5 Document coordinates use the remaining global axes in ascending XYZ
        # order. CATIA PlaneZX reverses those axes for a Y-normal sketch.
        if axis == "X":
            return [(point[1], point[2])]
        if axis == "Y":
            return [(point[2], point[0])]
        return [(point[0], point[1])]
    if axis == "Y":
        return [(_finite(params, "y", 0), _finite(params, "x", 0))]
    return [(_finite(params, "x", 0), _finite(params, "y", 0))]


def build_feature_table(request: dict[str, Any]) -> dict[str, Any]:
    """Convert native F5 feature rows into a small, explicit COM operation table."""
    if request.get("protocol") != PROTOCOL:
        raise AdapterError("unsupported adapter protocol")
    project = request.get("project")
    identity = request.get("identity")
    if not isinstance(project, dict) or project.get("format") != "ai-modeler" or project.get("version") != 1:
        raise AdapterError("input must be an F5 ai-modeler v1 project")
    if not isinstance(identity, dict) or not isinstance(identity.get("generation"), int):
        raise AdapterError("active model identity is missing")
    features = project.get("features")
    if not isinstance(features, list) or not features:
        raise AdapterError("active project has no features")
    if len(features) > 100_000:
        raise AdapterError("feature count exceeds adapter limit")
    if not isinstance(request.get("output_path"), str) or not request["output_path"].strip():
        raise AdapterError("output_path is required")

    plan_features = {}
    cadplan = request.get("cadplan")
    if isinstance(cadplan, dict) and isinstance(cadplan.get("features"), list):
        plan_features = {row.get("id"): row for row in cadplan["features"] if isinstance(row, dict)}

    operations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    approximations: dict[int, dict[str, Any]] = {}
    bounds: list[list[float]] | None = None
    for index, feature in enumerate(features, 1):
        if not isinstance(feature, dict):
            skipped.append({"index": index, "mapping_status":"unsupported", "reason": "feature row is not an object"})
            continue
        if feature.get("enabled", True) is False:
            skipped.append({"index": index, "feature_id": feature.get("feature_id"),
                "mapping_status":"disabled", "reason": "feature is disabled"})
            continue
        kind = feature.get("kind")
        params = feature.get("params")
        if not isinstance(kind, str) or not isinstance(params, dict):
            skipped.append({"index": index, "feature_id": feature.get("feature_id"),
                "mapping_status":"unsupported", "reason": "invalid feature row"})
            continue
        feature_id = feature.get("feature_id")
        plan_row = plan_features.get(feature_id, {})
        sources = _sources(feature, plan_row.get("sources", {}) if isinstance(plan_row, dict) else {})
        is_cut = feature.get("op") == "cut" or kind in ("hole", "pocket")
        action = "_cut" if is_cut else "_pad"
        axis = str(params.get("axis") or "Z").upper()
        if axis not in AXIS_MAP:
            skipped.append({"index": index, "feature_id": feature_id, "kind": kind,
                "name": name, "mapping_status": "unsupported", "reason": f"unsupported axis {axis}",
                "sources": sources})
            continue
        source = {"feature_id": feature_id, "source_prompt": project.get("source_prompt", ""),
                  "sources": sources, "cadplan_feature": plan_row}
        name = _name(feature, index)

        # F5 semantics are intentionally rebuilt as generic CATIA sketches and
        # Pad/Pocket operations. Even when the operation parameters transfer
        # directly, this is not a one-to-one parametric feature conversion.
        approximations[index] = {
            "index": index,
            "feature_id": feature_id,
            "kind": kind,
            "name": name,
            "mapping_status": "approximate_feature_mapping",
            "reason": "F5 feature is reconstructed from generic sketch and Pad/Pocket operations; native feature type and full parametric history are not preserved.",
            "sources": sources,
        }

        try:
            if kind == "box":
                if axis != "Z":
                    raise AdapterError("box supports only its F5 Z-axis extrusion")
                if _finite(params, "corner", 0) > TOLERANCE:
                    raise AdapterError("rounded boxes are not supported by the minimal adapter")
                center = _vec3(params.get("center"), "box center")
                length = _finite(params, "length")
                width = _finite(params, "width")
                height = _finite(params, "height")
                if min(length, width, height) <= 0:
                    raise AdapterError("box dimensions must be positive")
                if is_cut and bounds is None:
                    raise AdapterError("box cut needs a preceding CATIA-supported body")
                z0 = center[2] if params.get("base", True) else center[2] - height / 2
                operations.append({"kind": "rect" + action, "plane": "Z", "cx": center[0], "cy": center[1],
                    "z": z0 if not is_cut else (bounds[2][1] + 1 if bounds else z0 + height + 1),
                    "l": length, "w": width, "h" if not is_cut else "depth": height if not is_cut else height + 1,
                    "name": name, "feature_id": feature_id, "source": source})
                if not is_cut:
                    bounds = _merge_bounds(bounds, (center[0]-length/2, center[1]-width/2, z0),
                        (center[0]+length/2, center[1]+width/2, z0+height))
            elif kind == "cylinder":
                center = _vec3(params.get("center"), "cylinder center")
                height = _finite(params, "height")
                radius = _finite(params, "radius")
                radius2 = _finite(params, "radius2", radius)
                if height <= 0 or radius <= 0:
                    raise AdapterError("cylinder dimensions must be positive")
                if abs(radius2-radius) > TOLERANCE:
                    raise AdapterError("tapered cylinders are not supported by the minimal adapter")
                ui, vi, wi = AXIS_MAP[axis]
                start = center[wi] if params.get("base", True) else center[wi] - height/2
                offset = bounds[wi][1]+1 if is_cut and bounds else start
                op_height = bounds[wi][1]-bounds[wi][0]+2 if is_cut and bounds else height
                operations.append(_circle_operation("circle"+action, axis, center[ui], center[vi], offset,
                    radius, op_height, feature, index, source))
                if not is_cut:
                    lo = list(center); hi = list(center)
                    lo[ui] -= radius; hi[ui] += radius
                    lo[vi] -= radius; hi[vi] += radius
                    lo[wi] = start; hi[wi] = start+height
                    bounds = _merge_bounds(bounds, tuple(lo), tuple(hi))
            elif kind == "prism":
                if axis != "Z":
                    raise AdapterError("prism supports only its F5 Z-axis extrusion")
                center = _vec3(params.get("center"), "prism center")
                height = _finite(params, "height")
                profile = params.get("profile")
                if not isinstance(profile, list) or len(profile) < 3 or height <= 0:
                    raise AdapterError("prism requires a closed profile and positive height")
                profile = [[float(point[0]), float(point[1])] for point in profile]
                if any(not math.isfinite(value) for point in profile for value in point):
                    raise AdapterError("prism profile contains non-finite values")
                z0 = center[2]
                if is_cut:
                    if bounds is None:
                        raise AdapterError("prism cut needs a preceding CATIA-supported body")
                    z0 = bounds[2][1]+1
                operations.append({"kind": "poly"+action, "plane":"Z", "cx":center[0], "cy":center[1],
                    "z":z0, "profile":profile, "h" if not is_cut else "depth":height if not is_cut else height+1,
                    "name":name,"feature_id":feature_id,"source":source})
                if not is_cut:
                    xs=[center[0]+point[0] for point in profile]; ys=[center[1]+point[1] for point in profile]
                    bounds=_merge_bounds(bounds,(min(xs),min(ys),z0),(max(xs),max(ys),z0+height))
            elif kind == "hole":
                diameter = _finite(params, "diameter")
                if diameter <= 0:
                    raise AdapterError("hole diameter must be positive")
                if bounds is None:
                    raise AdapterError("hole needs a preceding CATIA-supported body")
                radius = diameter/2
                positions = _hole_positions(params, axis, radius, bounds)
                ui, vi, wi = AXIS_MAP[axis]
                through = bool(params.get("through", True)) or not params.get("depth")
                depth = bounds[wi][1]-bounds[wi][0]+2 if through else _finite(params,"depth")
                if depth <= 0:
                    raise AdapterError("blind hole depth must be positive")
                for hole_index, (u,v) in enumerate(positions,1):
                    operations.append(_circle_operation("circle_cut",axis,u,v,bounds[wi][1]+1,radius,depth+1,
                        {**feature,"name":f"{name} {hole_index}"},index,source))
            elif kind == "pocket":
                if axis != "Z" or bounds is None:
                    raise AdapterError("pocket requires a preceding Z-axis CATIA-supported body")
                if _finite(params,"corner",0)>TOLERANCE:
                    raise AdapterError("rounded pockets are not supported by the minimal adapter")
                depth=_finite(params,"depth")
                length=_finite(params,"length")
                width=_finite(params,"width")
                if min(depth,length,width)<=0:
                    raise AdapterError("pocket dimensions must be positive")
                operations.append({"kind":"rect_cut","plane":"Z","cx":_finite(params,"x",(bounds[0][0]+bounds[0][1])/2),
                    "cy":_finite(params,"y",(bounds[1][0]+bounds[1][1])/2),"z":bounds[2][1]+1,
                    "l":length,"w":width,"depth":depth+1,"name":name,"feature_id":feature_id,"source":source})
            else:
                raise AdapterError(f"feature `{kind}` is outside the minimal COM adapter scope")
        except (ValueError, TypeError, IndexError, OverflowError) as error:
            skipped.append({"index":index,"feature_id":feature_id,"kind":kind,"name":name,
                "mapping_status":"unsupported_or_invalid","reason":str(error),"sources":sources})

    skipped_indices = {item["index"] for item in skipped}
    approximation_rows = [row for index, row in approximations.items() if index not in skipped_indices]
    return {"operations":operations,"skipped":skipped,"approximations":approximation_rows,"identity":request["identity"],
            "project_name":project.get("name") or "Part1","output_path":request["output_path"],
            "open_in_catia":bool(request.get("open_in_catia")),"source_prompt":project.get("source_prompt",""),
            "cadplan":cadplan}


def _plane_for(part: Any, offset: float, axis: str) -> Any:
    base = {"X":part.OriginElements.PlaneYZ,"Y":part.OriginElements.PlaneZX}.get(axis,part.OriginElements.PlaneXY)
    if abs(offset)<1e-9:
        return base
    ref=part.CreateReferenceFromObject(base)
    plane=part.HybridShapeFactory.AddNewPlaneOffset(ref,float(offset),False)
    container=part.HybridBodies.Item(1) if part.HybridBodies.Count else part.HybridBodies.Add()
    container.AppendHybridShape(plane)
    part.Update()
    return plane


def _draw_profile(factory: Any, operation: dict[str, Any]) -> None:
    kind=operation["kind"]
    if kind.startswith("circle"):
        factory.CreateClosedCircle(float(operation["cx"]),float(operation["cy"]),float(operation["r"]))
        return
    if kind.startswith("poly"):
        points=[(float(operation["cx"])+p[0],float(operation["cy"])+p[1]) for p in operation["profile"]]
    else:
        x=float(operation["cx"]); y=float(operation["cy"]); l=float(operation["l"]); w=float(operation["w"])
        points=[(x-l/2,y-w/2),(x+l/2,y-w/2),(x+l/2,y+w/2),(x-l/2,y+w/2)]
    for index,(x1,y1) in enumerate(points):
        x2,y2=points[(index+1)%len(points)]
        if abs(x1-x2)>TOLERANCE or abs(y1-y2)>TOLERANCE:
            factory.CreateLine(x1,y1,x2,y2)


def _volume(part: Any) -> float | None:
    try:
        spa=part.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(part.CreateReferenceFromObject(part.MainBody)).Volume)
    except Exception:
        return None


def _make_operation(part: Any, operation: dict[str, Any]) -> None:
    axis=str(operation.get("plane") or "Z")
    reference=_plane_for(part,float(operation["z"]),axis)
    body=part.Bodies.Item("PartBody")
    sketch=body.Sketches.Add(reference)
    factory=sketch.Factory2D
    _draw_profile(factory,operation)
    sketch.CloseEdition(); part.Update()
    shape=part.ShapeFactory
    kind=operation["kind"]
    if kind.endswith("_pad"):
        feature=shape.AddNewPad(sketch,float(operation["h"]))
    elif kind.endswith("_cut"):
        before=_volume(part)
        feature=shape.AddNewPocket(sketch,float(operation["depth"]))
        part.Update()
        after=_volume(part)
        if before is not None and after is not None and after>before+1e-6:
            for method in ("ReverseDirection","Reverse","InvertDirection"):
                try:
                    getattr(feature,method)(); part.Update(); break
                except Exception:
                    continue
    else:
        raise AdapterError(f"invalid operation kind {kind}")
    label=f"{operation.get('name','Feature')} [{_source_label(operation['source'].get('sources',{}))}]"
    feature.Name=label[:80]
    part.Update()


def _emit(result: dict[str, Any], exit_code: int) -> int:
    sys.stdout.write(json.dumps(result,ensure_ascii=False,separators=(",",":"))+"\n")
    sys.stdout.flush()
    return exit_code


def execute(input_path: Path, *, preflight: bool = False, catia_factory: Any = None) -> int:
    catia = None
    document = None
    launched_catia = False
    keep_document_open = False
    previous_alerts = None
    try:
        request=json.loads(input_path.read_text(encoding="utf-8"))
        plan=build_feature_table(request)
        if not plan["operations"]:
            raise AdapterError("no CATIA-supported operations remain after preflight")
        result={"protocol":PROTOCOL,"state":"preflight_passed" if preflight else "running",
                "identity":plan["identity"],"output_path":plan["output_path"],
                "planned_operations":[{"kind":op["kind"],"feature_id":op["feature_id"],
                    "mapping_status":"operation_parameters_transferred",
                    "source_types":_source_label(op["source"].get("sources",{}))} for op in plan["operations"]],
                "approximations":plan["approximations"],"skipped":plan["skipped"]}
        if preflight:
            return _emit(result,0)

        if catia_factory is None:
            try:
                import pythoncom
                import win32com.client as win32
            except ImportError as error:
                raise AdapterError(f"pywin32 COM dependencies are not installed: {error}") from None
            pythoncom.CoInitialize()
            try:
                catia=win32.GetActiveObject("CATIA.Application")
            except Exception:
                catia=win32.Dispatch("CATIA.Application")
                launched_catia=True
        else:
            catia=catia_factory()
        try:
            previous_alerts=catia.DisplayFileAlerts
        except Exception:
            previous_alerts=None
        catia.DisplayFileAlerts=False
        document=catia.Documents.Add("Part")
        part=document.Part
        part.Name=str(plan["project_name"])[:80]
        completed=[]; failed=[]
        for operation in plan["operations"]:
            try:
                _make_operation(part,operation)
                completed.append({"feature_id":operation["feature_id"],"kind":operation["kind"],"name":operation["name"],
                "mapping_status":"operation_parameters_transferred","source":operation["source"]})
            except Exception as error:
                failed.append({"feature_id":operation["feature_id"],"kind":operation["kind"],"name":operation["name"],
                    "mapping_status":"com_execution_failed","error":str(error)[:500]})
                break
        if failed:
            result.update({"state":"failed","completed":completed,"failed":failed})
            return _emit(result,2)
        part.Update()
        output=Path(plan["output_path"])
        output.parent.mkdir(parents=True,exist_ok=True)
        document.SaveAs(str(output))
        if not output.is_file():
            raise AdapterError("CATIA SaveAs returned without creating a CATPart")
        sidecar=output.with_suffix(output.suffix+".report.json")
        report={"protocol":PROTOCOL,"state":"partial" if plan["skipped"] or plan["approximations"] else "success",
                "identity":plan["identity"],"project_name":plan["project_name"],
                "source_prompt":plan["source_prompt"],"output_path":str(output),
                "completed":completed,"approximations":plan["approximations"],"skipped":plan["skipped"],"failed":[],
                "cadplan":plan["cadplan"],
                "geometry_source":"rust_active_model_f5_aimodel",
                "fidelity_claim":"operation_mapping_only_geometry_not_verified",
                "catpart_verified_in_catia":False}
        sidecar.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        result.update(report)
        result["report_path"]=str(sidecar)
        if plan["open_in_catia"]:
            catia.Visible=True
            keep_document_open=True
        else:
            document.Close()
            document=None
        return _emit(result,0)
    except Exception as error:
        return _emit({"protocol":PROTOCOL,"state":"failed","error":str(error)[:1000]},2)
    finally:
        if document is not None and not keep_document_open:
            try: document.Close()
            except Exception: pass
        if catia is not None:
            if previous_alerts is not None:
                try: catia.DisplayFileAlerts=previous_alerts
                except Exception: pass
            if launched_catia and not keep_document_open:
                try: catia.Quit()
                except Exception: pass


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",required=True,type=Path,help="JSON written by the Rust active-model host")
    parser.add_argument("--preflight",action="store_true",help="Validate/marshal without importing pywin32 or starting CATIA")
    args=parser.parse_args(argv)
    return execute(args.input,preflight=args.preflight)


if __name__=="__main__":
    raise SystemExit(main())
