# -*- coding: utf-8 -*-
"""Transactional, incremental CADPlan edits for the model-facing tools."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from ai_modeler.sdk import cadplan


COORDINATE_SYSTEM = {
    "type": "global_cartesian", "handedness": "right",
    "axes": ["X", "Y", "Z"], "origin": [0, 0, 0],
}


def geometry_fingerprint(plan):
    """Hash ordered CAD geometry semantics, excluding provenance and presentation metadata."""
    if not isinstance(plan, dict):
        features = []
    else:
        features = [
            {**{key: feature.get(key) for key in
                ("id", "kind", "operation", "parameters", "relations")},
             **({"selection_binding": feature["selection_binding"]}
                if "selection_binding" in feature else {})}
            for feature in (plan.get("features") or [])
            if isinstance(feature, dict)
        ]
    encoded = json.dumps(features, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _has_number(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, (list, tuple)):
        return any(_has_number(item) for item in value)
    if isinstance(value, dict):
        return any(_has_number(item) for item in value.values())
    return False


def create(name, first_feature, source_prompt="", units=None):
    return {
        "schema_version": cadplan.CADPLAN_SCHEMA_VERSION,
        "name": name,
        "source_prompt": source_prompt,
        "units": deepcopy(units or {"length": "mm", "angle": "deg"}),
        "coordinate_system": deepcopy(COORDINATE_SYSTEM),
        "features": [deepcopy(first_feature)],
        "pending_questions": [],
    }


def add(plan, feature):
    candidate = deepcopy(plan)
    candidate["features"].append(deepcopy(feature))
    return candidate


def update(plan, feature_id, parameter_updates=None, source_updates=None,
           remove_parameters=None, fields=None):
    candidate = deepcopy(plan)
    target = next((feature for feature in candidate["features"]
                   if feature.get("id") == feature_id), None)
    if target is None:
        raise ValueError("未找到特征 id：%s" % feature_id)
    for key, value in (fields or {}).items():
        if key not in {"name", "note", "relations", "kind", "operation"}:
            raise ValueError("不能更新特征字段：%s" % key)
        target[key] = deepcopy(value)
    params = target.setdefault("parameters", {})
    sources = target.setdefault("sources", {})
    for key in remove_parameters or []:
        params.pop(key, None)
        sources.pop(key, None)
    for key, value in (parameter_updates or {}).items():
        if (_has_number(value) and params.get(key) != value
                and key not in (source_updates or {})):
            raise ValueError("修改数值参数 %s 时必须同时更新其 sources 证据。" % key)
        params[key] = deepcopy(value)
    for key, value in (source_updates or {}).items():
        sources[key] = deepcopy(value)
    return candidate


def remove(plan, feature_id):
    candidate = deepcopy(plan)
    features = candidate["features"]
    index = next((i for i, feature in enumerate(features)
                  if feature.get("id") == feature_id), None)
    if index is None:
        raise ValueError("未找到特征 id：%s" % feature_id)
    features.pop(index)
    return candidate


def snapshot(plan, validation=None, dimensions=None, revision=0,
             findings=None, include_features=True):
    if not isinstance(plan, dict):
        return {"state": "empty", "revision": revision}
    result = {
        "state": "model", "revision": revision,
        "name": plan.get("name"), "units": plan.get("units"),
        "feature_count": len(plan.get("features") or []),
        "validation": validation,
        "dimensions": dimensions,
        "unresolved_findings": findings or [],
    }
    if include_features:
        result["features"] = deepcopy(plan.get("features") or [])
    return result


def from_document(document, previous_plan=None):
    """Import a live Document without silently dropping geometry parameters."""
    if not isinstance(document, dict) or not isinstance(document.get("features"), list):
        raise ValueError("当前 Document 特征树快照无效。")
    old_features = (previous_plan or {}).get("features") or []
    features = []
    used_ids = set()
    for index, row in enumerate(document["features"]):
        if not isinstance(row, dict) or row.get("enabled") is False:
            raise ValueError("当前特征树包含已禁用或无效特征，无法无损导入 CADPlan。")
        kind = row.get("kind")
        spec = cadplan.FEATURE_SPECS.get(kind)
        if not spec:
            raise ValueError("当前特征树包含 CADPlan 不支持的类型：%s" % kind)
        params = deepcopy(row.get("params") or {})
        old = old_features[index] if index < len(old_features) else {}
        if not isinstance(old, dict) or old.get("kind") != kind:
            old = {}
        if kind == "hole" and params.get("pos") == "point":
            axis = str(params.get("axis") or "Z").upper()
            prior_point = (old.get("parameters") or {}).get("point") or [0, 0, 0]
            point = list(params.get("point") or prior_point)
            if len(point) != 3:
                raise ValueError("当前孔特征的 point 必须包含三个坐标。")
            if "x" in params or "y" in params:
                a = params.pop("x", 0)
                b = params.pop("y", 0)
                if axis == "X":
                    point[1], point[2] = a, b
                elif axis == "Y":
                    point[0], point[2] = a, b
                else:
                    point[0], point[1] = a, b
            params["point"] = point
        unknown = set(params) - set(spec["params"])
        if unknown:
            raise ValueError("当前 %s 特征包含 CADPlan 不支持的参数：%s" %
                             (kind, ", ".join(sorted(unknown))))
        fid = old.get("id") or "imported-%d" % (index + 1)
        if fid in used_ids:
            fid = "imported-%d" % (index + 1)
        used_ids.add(fid)
        old_params = old.get("parameters") or {}
        old_sources = old.get("sources") or {}
        sources = {}
        for key, value in params.items():
            if not _has_number(value):
                continue
            if key in old_params and old_params[key] == value and key in old_sources:
                sources[key] = deepcopy(old_sources[key])
            else:
                sources[key] = {
                    "type": "existing_model",
                    "note": "来自当前 Document 特征树；之前的数值来源未保留。",
                }
        feature = {
            "id": fid, "kind": kind, "operation": row.get("op"),
            "name": str(row.get("name") or kind),
            "note": str(row.get("note") or ""),
            "parameters": params, "sources": sources,
        }
        if old.get("relations"):
            feature["relations"] = deepcopy(old["relations"])
        elif kind == "pattern" and features:
            feature["relations"] = [{"type": "pattern_source",
                                     "feature_id": features[-1]["id"]}]
        features.append(feature)
    plan = {
        "schema_version": cadplan.CADPLAN_SCHEMA_VERSION,
        "name": str(document.get("name") or (previous_plan or {}).get("name") or "Current model"),
        "source_prompt": str(document.get("source_prompt") or ""),
        "units": {"length": "mm", "angle": "deg"},
        "coordinate_system": deepcopy(COORDINATE_SYSTEM),
        "features": features, "pending_questions": [],
    }
    validation = cadplan.validate_plan(plan)
    if validation.get("state") != "valid":
        rows = validation.get("errors") or validation.get("confirmations") or []
        detail = "; ".join(str(row.get("message") or row.get("question") or row)
                           for row in rows[:4])
        raise ValueError("当前特征树无法无损导入 CADPlan：%s" % detail)
    return plan
