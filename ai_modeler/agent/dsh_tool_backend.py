# -*- coding: utf-8 -*-
"""Private, protocol-neutral DSH tool backend seam."""
from __future__ import annotations

import threading
from typing import Any

from ai_modeler.agent import dsh_tool_domain as _domain


def schemas() -> list[dict[str, Any]]:
    """Return the model-facing JSON schemas for the existing CAD tools."""
    return [dict(tool) for tool in _domain.TOOLS]


def execute(name: str, arguments: dict[str, Any],
            cancel_event: threading.Event | None = None) -> dict[str, Any]:
    """Execute one domain operation and return a JSON-compatible result."""
    if not isinstance(arguments, dict):
        raise ValueError("工具参数必须为 JSON 对象。")
    event = cancel_event or threading.Event()
    _domain._TOOL_EXECUTION_CONTEXT.cancel_event = event
    try:
        legacy_result = _domain._handle_tool(name, arguments, cancel_event=event)
    finally:
        _domain._TOOL_EXECUTION_CONTEXT.cancel_event = None
    if not isinstance(legacy_result, dict):
        raise RuntimeError("工具后端返回了无效结果。")
    content = legacy_result.get("content")
    text = "\n".join(str(block.get("text") or "") for block in content
                      if isinstance(block, dict) and block.get("type") == "text") \
        if isinstance(content, list) else ""
    private_images = legacy_result.get("_native_images")
    images = private_images if isinstance(private_images, list) else []
    return {"text": text or "工具已完成。", "images": images,
            "payload": {key: value for key, value in legacy_result.items()
                        if key not in {"content", "_native_images"}}}
