"""JSON Lines bridge between the Rust/Tauri UI and the Python application.

The protocol owns no modeling rules.  It adapts the existing Python document,
agent, export, settings, CATIA, MCP, and knowledge-base services to a small
request/response interface over stdin/stdout.
"""
from __future__ import annotations

import concurrent.futures
import copy
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from ai_modeler import __version__
from ai_modeler.paths import PROJECT_ROOT, documentation_root
from ai_modeler.core import exporters, kernel as K
from ai_modeler.core import docmodel as DM
from ai_modeler.core import nlparser as NLP
from ai_modeler.runtime import config as cfgmod


_PALETTE = (
    (0x4A, 0x90, 0xD9), (0x5C, 0xB8, 0x5C), (0xF0, 0xAD, 0x4E),
    (0xD9, 0x53, 0x4F), (0x9B, 0x59, 0xB6), (0x1A, 0xBC, 0x9C),
    (0xE6, 0x7E, 0x22), (0x34, 0x98, 0xDB), (0x2E, 0xCC, 0x71),
    (0xE7, 0x4C, 0x3C), (0x16, 0xA0, 0x85), (0x8E, 0x44, 0xAD),
)
_PARAM_SCHEMA = {
    "box": ["length", "width", "height", "corner"],
    "cylinder": ["radius", "radius2", "height", "segments"],
    "sphere": ["radius", "segments", "rings"],
    "prism": ["height"], "revolve": ["angle", "segments"],
    "loft": ["span", "root_chord", "tip_chord", "thickness", "twist", "sweep", "sections"],
    "gear": ["teeth", "module", "height"],
    "hole": ["diameter", "depth", "count", "pcd", "x", "y", "through"],
    "pocket": ["length", "width", "depth", "corner"],
    "pattern": ["count", "angle"],
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", str(value or ""))[:96] or uuid.uuid4().hex


def _ensure_parent_dir(path: str) -> None:
    Path(path).expanduser().absolute().parent.mkdir(parents=True, exist_ok=True)


def preview_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home() / "AppData" / "Local"
    return root / "AI Modeler" / "ui-preview"


def write_glb(polys, path: Path) -> str:
    """Write a small glTF 2.0 binary mesh, with the kernel feature tags as colors."""
    triangles = K.polygons_to_triangles(polys or [])
    by_tag: dict[int, list[tuple]] = {}
    for a, b, c, tag in triangles:
        normal = K.v_cross(K.v_sub(b, a), K.v_sub(c, a))
        if K.v_len(normal) <= 1e-12:
            continue
        by_tag.setdefault(int(tag or 0), []).append((a, b, c, K.v_norm(normal)))

    binary = bytearray()
    buffer_views = []
    accessors = []
    materials = []
    primitives = []

    def append_view(blob: bytes, target: int) -> int:
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        binary.extend(blob)
        index = len(buffer_views)
        buffer_views.append({"buffer": 0, "byteOffset": offset,
                             "byteLength": len(blob), "target": target})
        return index

    for tag in sorted(by_tag):
        rows = by_tag[tag]
        positions = bytearray()
        normals = bytearray()
        indices = bytearray()
        all_points = []
        for triangle_index, (a, b, c, normal) in enumerate(rows):
            for point in (a, b, c):
                positions.extend(struct.pack("<3f", *point))
                normals.extend(struct.pack("<3f", *normal))
                all_points.append(point)
            indices.extend(struct.pack("<3I", triangle_index * 3,
                                       triangle_index * 3 + 1,
                                       triangle_index * 3 + 2))
        position_view = append_view(bytes(positions), 34962)
        normal_view = append_view(bytes(normals), 34962)
        index_view = append_view(bytes(indices), 34963)
        accessors.append({"bufferView": position_view, "componentType": 5126,
                          "count": len(rows) * 3, "type": "VEC3",
                          "min": [min(p[i] for p in all_points) for i in range(3)],
                          "max": [max(p[i] for p in all_points) for i in range(3)]})
        position_accessor = len(accessors) - 1
        accessors.append({"bufferView": normal_view, "componentType": 5126,
                          "count": len(rows) * 3, "type": "VEC3"})
        normal_accessor = len(accessors) - 1
        accessors.append({"bufferView": index_view, "componentType": 5125,
                          "count": len(rows) * 3, "type": "SCALAR"})
        index_accessor = len(accessors) - 1
        rgb = _PALETTE[tag % len(_PALETTE)]
        material_index = len(materials)
        materials.append({"name": "feature-%d" % tag,
                          "pbrMetallicRoughness": {
                              "baseColorFactor": [c / 255.0 for c in rgb] + [1.0],
                              "metallicFactor": 0.06, "roughnessFactor": 0.54}})
        primitives.append({"attributes": {"POSITION": position_accessor,
                                            "NORMAL": normal_accessor},
                           "indices": index_accessor, "material": material_index,
                           "mode": 4})

    if not primitives:
        raise ValueError("当前模型没有可预览的三角面。")
    while len(binary) % 4:
        binary.append(0)
    document = {
        "asset": {"version": "2.0", "generator": "AI Modeler Python bridge"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"name": "AI Modeler", "primitives": primitives}],
        "materials": materials, "buffers": [{"byteLength": len(binary)}],
        "bufferViews": buffer_views, "accessors": accessors,
    }
    json_chunk = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    bin_chunk = bytes(binary)
    total_length = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(struct.pack("<4sII", b"glTF", 2, total_length))
        stream.write(struct.pack("<I4s", len(json_chunk), b"JSON"))
        stream.write(json_chunk)
        stream.write(struct.pack("<I4s", len(bin_chunk), b"BIN\x00"))
        stream.write(bin_chunk)
    return str(path)


class Bridge:
    def __init__(self, output_stream=None):
        self.doc = DM.Document("Part1")
        self.cfg = cfgmod.load()
        self.current_path: str | None = None
        self.plan: dict | None = None
        self.revision = 0
        self.polys = []
        self._write_lock = threading.Lock()
        self._output_stream = output_stream or sys.stdout
        self._state_lock = threading.RLock()
        self._jobs_lock = threading.Lock()
        self._jobs: dict[str, threading.Event] = {}
        self._busy_request: str | None = None
        self.session_id = "ai-modeler-" + uuid.uuid4().hex
        self.preview_dir = preview_root() / _safe_component(self.session_id)

    def send(self, value: dict) -> None:
        payload = json.dumps(_jsonable(value), ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            self._output_stream.write(payload + "\n")
            self._output_stream.flush()

    def event(self, request_id: str, name: str, data: Any) -> None:
        self.send({"event": name, "request_id": request_id, "data": data})

    def _document_payload(self, include_mesh: bool = False) -> dict:
        stats = self.doc.stats() if self.doc.features else {
            "faces": 0, "triangles": 0, "bbox": None, "size": None,
            "volume": 0.0, "area": 0.0,
        }
        mesh_path = None
        if include_mesh and self.polys:
            mesh_path = write_glb(self.polys, self.preview_dir / ("mesh-r%d.glb" % self.revision))
        features = []
        for index, feature in enumerate(self.doc.features):
            item = feature.to_dict()
            item.update({"index": index, "description": feature.describe()})
            item["editable_parameters"] = _PARAM_SCHEMA.get(feature.kind, list(feature.params))
            features.append(item)
        return {
            "revision": self.revision, "name": self.doc.name,
            "source_prompt": self.doc.source_prompt, "current_path": self.current_path,
            "features": features, "stats": _jsonable(stats),
            "build_log": list(self.doc.last_log or []), "mesh_path": mesh_path,
        }

    def _build(self, request_id: str, cancel: threading.Event | None = None) -> dict:
        with self._state_lock:
            if self._busy_request and self._busy_request != request_id:
                raise RuntimeError("另一个建模任务仍在执行。")
            self._busy_request = request_id
        try:
            self.event(request_id, "job.progress", {"message": "正在计算几何…", "progress": 0.05})
            deadline = time.time() + float((self.cfg.get("build") or {}).get("time_budget", 30.0))
            max_faces = int((self.cfg.get("build") or {}).get("max_faces", 60000))
            body = self.doc.build(
                progress=lambda message, fraction: self.event(
                    request_id, "job.progress", {"message": str(message), "progress": float(fraction or 0)}),
                deadline=deadline, cancel=cancel, max_faces=max_faces)
            self.polys = body
            self.revision += 1
            payload = self._document_payload(include_mesh=bool(body))
            self.event(request_id, "model.changed", payload)
            return payload
        finally:
            with self._state_lock:
                if self._busy_request == request_id:
                    self._busy_request = None

    def _start_job(self, request_id: str) -> threading.Event:
        with self._jobs_lock:
            if request_id in self._jobs:
                raise ValueError("重复的请求 ID。")
            cancel = threading.Event()
            self._jobs[request_id] = cancel
            return cancel

    def cancel(self, target_id: str) -> dict:
        with self._jobs_lock:
            cancel = self._jobs.get(str(target_id))
            if cancel is None:
                return {"cancelled": False, "message": "活动任务不存在。"}
            cancel.set()
        return {"cancelled": True, "request_id": str(target_id)}

    def reset_chat(self) -> dict:
        from ai_modeler.agent import dsh_agent

        dsh_agent.close_session(self.session_id)
        self.session_id = "ai-modeler-" + uuid.uuid4().hex
        self.preview_dir = preview_root() / _safe_component(self.session_id)
        self.plan = None
        return {"session_id": self.session_id}

    def dispatch(self, method: str, params: dict, request_id: str) -> Any:
        params = params if isinstance(params, dict) else {}
        if method == "system.handshake":
            return {"protocol": 1, "app_version": __version__,
                    "python_version": sys.version.split()[0],
                    "python_executable": sys.executable,
                    "project_root": str(documentation_root()),
                    "capabilities": ["agent", "quick_model", "document", "export",
                                     "settings", "parts", "online", "mcp", "catia"]}
        if method == "system.initialize":
            self.cfg = cfgmod.load()
            return {"settings": self.cfg, "session_id": self.session_id,
                    "document": self._document_payload(include_mesh=bool(self.polys))}
        if method == "agent.cancel":
            return self.cancel(str(params.get("request_id") or ""))
        if method == "chat.reset":
            return self.reset_chat()
        if method == "settings.get":
            self.cfg = cfgmod.load()
            return self.cfg
        if method == "settings.save":
            cfg = params.get("settings")
            if not isinstance(cfg, dict):
                raise ValueError("缺少 settings 对象。")
            from ai_modeler.agent import providers
            updated = copy.deepcopy(cfg)
            if isinstance(updated.get("llm"), dict):
                llm = providers.ensure_providers(updated["llm"])
                llm["providers"] = [dict(row) for row in llm.get("providers", [])]
                updated["llm"] = providers.mirror_active(llm)
            cfgmod.save(updated)
            self.cfg = cfgmod.load()
            return {"settings": self.cfg, "path": cfgmod.CONFIG_PATH}
        if method == "settings.provider_templates":
            from ai_modeler.agent import providers
            return [{"id": key, "label": label} for key, label in providers.template_labels()]
        if method == "settings.provider_template":
            from ai_modeler.agent import providers
            return providers.template_profile(str(params.get("id") or "custom"))
        if method == "settings.fetch_models":
            from ai_modeler.agent import providers
            ok, payload = providers.fetch_models(str(params.get("base_url") or ""),
                                                 str(params.get("api_key") or ""))
            if not ok:
                raise RuntimeError(str(payload))
            return {"models": payload}
        if method == "settings.active_profile":
            from ai_modeler.agent import providers
            return providers.active_profile((self.cfg.get("llm") or {}))

        if method == "document.get":
            return self._document_payload(include_mesh=bool(self.polys))
        if method == "document.new":
            self.doc = DM.Document(str(params.get("name") or "Part1"))
            self.polys = []
            self.plan = None
            self.current_path = None
            self.revision += 1
            return self._document_payload()
        if method == "document.clear":
            self.doc = DM.Document("Part1")
            self.polys = []
            self.plan = None
            self.revision += 1
            return self._document_payload()
        if method == "document.build":
            cancel = self._start_job(request_id)
            try:
                return self._build(request_id, cancel)
            finally:
                with self._jobs_lock:
                    self._jobs.pop(request_id, None)
        if method == "model.quick_build":
            text = str(params.get("text") or "").strip()
            if not text:
                raise ValueError("请输入建模描述。")
            if params.get("new_model", True):
                result = NLP.parse(text)
            else:
                result = NLP.parse(text, self.doc)
            if not result.ok:
                suggestion = None
                if self.cfg.get("online", {}).get("enabled", True):
                    try:
                        from ai_modeler.agent import online
                        suggestion = online.ask(text)
                    except Exception as exc:
                        suggestion = {"error": str(exc)}
                return {"state": "parse_failed", "message": result.message,
                        "online_suggestion": _jsonable(suggestion)}
            self.doc = result.doc
            self.doc.source_prompt = text
            self.plan = None
            self.polys = []
            cancel = self._start_job(request_id)
            try:
                payload = self._build(request_id, cancel)
                payload.update({"state": "complete", "steps": result.steps,
                                "warnings": result.warnings, "recognized": result.recognized})
                return payload
            finally:
                with self._jobs_lock:
                    self._jobs.pop(request_id, None)
        if method.startswith("feature."):
            index = int(params.get("index", -1))
            if index < 0 or index >= len(self.doc.features):
                raise IndexError("特征索引无效。")
            if method == "feature.update":
                new_params = params.get("params")
                if not isinstance(new_params, dict):
                    raise ValueError("params 必须是对象。")
                feature = self.doc.features[index]
                merged = dict(feature.params)
                merged.update(new_params)
                feature.params = merged
                feature.enabled = bool(params.get("enabled", feature.enabled))
                if "name" in params:
                    feature.name = str(params["name"])
            elif method == "feature.rename":
                name = str(params.get("name") or "").strip()
                if not name:
                    raise ValueError("特征名称不能为空。")
                self.doc.features[index].name = name
            elif method == "feature.toggle":
                self.doc.features[index].enabled = bool(params.get("enabled"))
            elif method == "feature.delete":
                self.doc.remove(index)
            elif method == "feature.move":
                self.doc.move(index, int(params.get("delta", 0)))
            else:
                raise ValueError("未知特征操作: " + method)
            self.doc.invalidate()
            self.plan = None
            cancel = self._start_job(request_id)
            try:
                return self._build(request_id, cancel)
            finally:
                with self._jobs_lock:
                    self._jobs.pop(request_id, None)

        if method == "project.open":
            path = str(params.get("path") or "")
            if not path:
                raise ValueError("缺少工程路径。")
            data = exporters.load_project(path)
            self.doc = DM.Document.from_dict(data)
            self.current_path = path
            self.plan = None
            self.polys = []
            cancel = self._start_job(request_id)
            try:
                result = self._build(request_id, cancel)
                result["state"] = "opened"
                return result
            finally:
                with self._jobs_lock:
                    self._jobs.pop(request_id, None)
        if method == "project.save":
            path = str(params.get("path") or self.current_path or "")
            if not path:
                raise ValueError("请选择工程保存路径。")
            if not self.doc.features:
                raise ValueError("当前没有可保存的特征。")
            _ensure_parent_dir(path)
            exporters.export_project(self.doc, path)
            self.current_path = path
            return {"path": path, "document": self._document_payload()}

        if method == "export":
            kind, path = str(params.get("kind") or ""), str(params.get("path") or "")
            if not path:
                raise ValueError("请选择导出路径。")
            body = self.doc.build()
            if not body:
                raise ValueError("当前没有可导出的几何体。")
            _ensure_parent_dir(path)
            if kind == "stl":
                exporters.export_stl(body, path, self.doc.name)
            elif kind == "obj":
                exporters.export_obj(body, path, self.doc.name)
            elif kind == "report":
                exporters.export_report(self.doc, path, self.doc.source_prompt)
            elif kind == "catia":
                exporters.export_catia_script(self.doc, path)
            elif kind == "project":
                exporters.export_project(self.doc, path)
            else:
                raise ValueError("不支持的导出类型: " + kind)
            return {"path": path, "kind": kind}

        if method == "render.snapshot":
            from ai_modeler.ui import softrender as SR
            path = str(params.get("path") or "")
            if not path:
                raise ValueError("请选择截图保存路径。")
            body = self.doc.build()
            if not body:
                raise ValueError("当前没有可截图的模型。")
            _ensure_parent_dir(path)
            view_set = str(params.get("view_set") or "single")
            if view_set == "four":
                SR.save_four_view(body, path)
            elif view_set == "three":
                SR.save_three_view(body, path)
            elif view_set == "extended":
                SR.save_multiview(body, path,
                                  views=["iso", "front", "right", "top", "back", "left", "bottom"],
                                  tile=(330, 260), columns=3)
            else:
                SR.render_to_png(body, path, int(params.get("width", 1100)),
                                 int(params.get("height", 820)))
            return {"path": path}

        if method == "agent.send":
            return self._run_agent(params, request_id)

        if method == "parts.list":
            from ai_modeler.parts import partlib
            kw = str(params.get("query") or "").strip().casefold()
            counts = partlib.load_kb().get("by_category", {})
            rows = []
            for category, item in partlib.PARTS.items():
                aliases = list(item.get("aliases") or [])
                if kw and kw not in category.casefold() and not any(kw in str(alias).casefold() for alias in aliases):
                    continue
                rows.append({"category": category, "aliases": aliases,
                             "params": item.get("params", {}),
                             "manual_count": counts.get(category, 0),
                             "sample_prompt": self._sample_prompt(category, item)})
            return rows
        if method == "samples.list":
            from ai_modeler.ui.examples import SAMPLES
            return [{"label": label, "prompt": prompt} for label, prompt in SAMPLES]
        if method == "online.search":
            from ai_modeler.agent import online
            return online.ask(str(params.get("query") or ""))
        if method == "mcp.info":
            from ai_modeler.runtime import mcp_server
            from ai_modeler.catia_mcp import server as catia_mcp
            catia_tools, _report = catia_mcp.all_tools()
            return {"offline": mcp_server.client_config(),
                    "catia": catia_mcp.client_config(),
                    "offline_tool_count": len(mcp_server.list_tools()),
                    "catia_tool_count": len([row for row in catia_tools if row[0].startswith("catia_")])}
        if method == "mcp.test":
            from ai_modeler.runtime import mcp_server
            from ai_modeler.catia_mcp import server as catia_mcp
            catia_tools, _report = catia_mcp.all_tools()
            return {"offline_tool_count": len(mcp_server.list_tools()),
                    "catia_tool_count": len([row for row in catia_tools if row[0].startswith("catia_")]),
                    "catia_selftest": catia_mcp.selftest(verbose=False)}
        if method == "catia.probe":
            from ai_modeler.catia_backend import catia
            ok, message = catia.probe()
            return {"ok": ok, "message": message}
        if method == "catia.push":
            from ai_modeler.catia_backend import catia
            if not self.doc.features:
                raise ValueError("当前没有模型可推送。")
            ok, message, script = catia.push_to_catia(self.doc, save_catpart=params.get("save_path"))
            return {"ok": ok, "message": message, "script": script}
        if method == "catia.library":
            from ai_modeler.catia_backend import catialib
            return catialib.load_index()
        if method in {"catia.install_plugin", "catia.connect", "local_ai.install"}:
            scripts = {"catia.install_plugin": "tools/install_catia_plugin.py",
                       "catia.connect": "tools/connect_catia.py",
                       "local_ai.install": "tools/install_deepseek.py"}
            path = PROJECT_ROOT / scripts[method]
            if not path.is_file():
                raise FileNotFoundError("未找到脚本: %s" % path)
            child = subprocess.Popen([sys.executable, str(path)], cwd=str(PROJECT_ROOT),
                                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            return {"started": True, "pid": child.pid, "path": str(path)}
        if method == "system.selftest":
            return self._selftest()
        if method == "system.open_path":
            path = str(params.get("path") or "")
            if not path:
                raise ValueError("缺少路径。")
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
            return {"opened": path}
        if method == "system.about":
            return {"app_version": __version__, "python_version": sys.version.split()[0],
                    "description": "自然语言驱动的参数化三维建模工具"}
        raise ValueError("未知方法: " + method)

    def _sample_prompt(self, category: str, item: dict) -> str:
        try:
            from ai_modeler.ui.examples import SAMPLE_VALUES
            sample = SAMPLE_VALUES.get(category, {})
        except Exception:
            sample = {}
        bits = []
        for key, aliases in (item.get("params") or {}).items():
            if key in sample:
                bits.append("%s%g" % ((aliases or [key])[0], sample[key]))
        return (category + " " + " ".join(bits)).strip()

    def _run_agent(self, params: dict, request_id: str) -> dict:
        from ai_modeler.agent import dsh_agent
        from ai_modeler.sdk import cadplan

        prompt = str(params.get("text") or "请根据参考图建立模型。")
        image_paths = [str(path) for path in (params.get("images") or [])]
        cancel = self._start_job(request_id)
        with self._state_lock:
            if self._busy_request and self._busy_request != request_id:
                with self._jobs_lock:
                    self._jobs.pop(request_id, None)
                raise RuntimeError("另一个建模任务仍在执行。")
            self._busy_request = request_id
        try:
            self.event(request_id, "agent.progress", {"message": "正在准备 DSH Agent 会话…"})
            current_document = {"name": self.doc.name,
                               "features": [feature.to_dict() for feature in self.doc.features]} if self.doc.features else None
            def on_progress(message):
                self.event(request_id, "agent.progress", {"message": str(message)})
            def on_preview(plan, preview, stage):
                preview_path = str((preview or {}).get("preview_image_path") or "")
                safe_preview = None
                if preview_path and Path(preview_path).is_file():
                    self.preview_dir.mkdir(parents=True, exist_ok=True)
                    safe_preview = str(self.preview_dir / ("agent-preview-%d.png" % int(time.time() * 1000)))
                    try:
                        import shutil
                        shutil.copyfile(preview_path, safe_preview)
                    except OSError:
                        safe_preview = None
                self.event(request_id, "model.preview", {
                    "stage": str(stage), "revision": (preview or {}).get("revision"),
                    "preview_path": safe_preview,
                    "preview": _jsonable(preview or {}),
                })
            result = dsh_agent.run_dsh_agent(
                prompt, image_paths=image_paths, cfg=copy.deepcopy(self.cfg),
                session_id=self.session_id, progress=on_progress,
                cancel_event=cancel, current_plan=self.plan,
                current_document=current_document, on_preview=on_preview)
            plan = result.get("plan") if isinstance(result, dict) else None
            if isinstance(plan, dict) and isinstance(result.get("validation"), dict) and result["validation"].get("state") == "valid":
                document = cadplan.plan_to_document(plan)
                document.source_prompt = prompt
                with self._state_lock:
                    self.doc = document
                    self.plan = plan
                    self.polys = []
                if document.features:
                    built = self._build(request_id, cancel)
                    result["document"] = built
            self.event(request_id, "agent.completed", {
                "state": result.get("state"), "reply": result.get("reply"),
                "plan": result.get("plan"), "document": result.get("document"),
            })
            return _jsonable(result)
        finally:
            with self._state_lock:
                if self._busy_request == request_id:
                    self._busy_request = None
            with self._jobs_lock:
                self._jobs.pop(request_id, None)

    def _selftest(self) -> dict:
        lines = ["Python      : %s" % sys.version.split()[0]]
        try:
            from ai_modeler.catia_backend import catia
            ok, message = catia.probe()
            lines.append("CATIA V5    : %s" % ((message.splitlines() or [""])[0] if ok else "未接入"))
        except Exception as exc:
            lines.append("CATIA V5    : 检测失败 (%s)" % exc)
        try:
            from ai_modeler.runtime import mcp_server
            lines.append("MCP 工具数  : %d" % len(mcp_server.list_tools()))
        except Exception as exc:
            lines.append("MCP         : 失败 (%s)" % exc)
        sample = DM.Document("Selftest")
        sample.add(DM.Feature("box", {"length": 50, "width": 40, "height": 20}))
        sample.add(DM.Feature("hole", {"diameter": 10, "through": True, "pos": "center"}, DM.CUT))
        body = sample.build()
        lines.append("几何内核    : OK (%d 面片)" % len(body))
        parsed = NLP.parse("画一个 100×60×20 的长方体, 中心打直径 10 的通孔")
        lines.append("自然语言引擎: %s (%d 条指令)" % ("OK" if parsed.ok else "异常", parsed.recognized))
        return {"lines": lines, "ok": parsed.ok and bool(body)}


def serve_stdio() -> int:
    """Read JSONL requests, run each operation off the input loop, emit JSONL."""
    protocol_stdout = sys.stdout
    bridge = Bridge(output_stream=protocol_stdout)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=6, thread_name_prefix="ui-bridge")

    def work(request_id: str, method: str, params: dict) -> None:
        try:
            result = bridge.dispatch(method, params, request_id)
            bridge.send({"id": request_id, "type": "result", "result": _jsonable(result)})
        except Exception as exc:
            bridge.send({"id": request_id, "type": "error",
                         "error": {"code": type(exc).__name__, "message": str(exc),
                                   "traceback": traceback.format_exc()}})

    # Keep stdout exclusively for protocol frames. Third-party diagnostics and
    # accidental prints from reused UI/backend modules go to stderr.
    sys.stdout = sys.stderr
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("请求必须是 JSON 对象。")
                request_id = str(request.get("id") or "")
                method = str(request.get("method") or "")
                params = request.get("params") if isinstance(request.get("params"), dict) else {}
                if not request_id or not method:
                    raise ValueError("请求缺少 id 或 method。")
                if method == "app.shutdown":
                    bridge.reset_chat()
                    bridge.send({"id": request_id, "type": "result", "result": {"stopped": True}})
                    break
                if method == "agent.cancel":
                    work(request_id, method, params)
                else:
                    pool.submit(work, request_id, method, params)
            except Exception as exc:
                bridge.send({"id": "", "type": "error",
                             "error": {"code": type(exc).__name__, "message": str(exc)}})
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        with bridge._jobs_lock:
            for cancel in bridge._jobs.values():
                cancel.set()
    return 0
