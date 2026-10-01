# -*- coding: utf-8 -*-
"""Private one-shot JSONL bridge used by the native Cordis plugin."""
from __future__ import annotations

import json
import sys
import traceback
import threading
from typing import Any

from ai_modeler.agent import dsh_tool_backend


def _emit(value: Any) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--schemas":
        _emit({"ok": True, "schemas": dsh_tool_backend.schemas()})
        return 0
    try:
        request = json.loads(sys.stdin.readline())
        if not isinstance(request, dict) or not isinstance(request.get("name"), str):
            raise ValueError("工具请求格式无效。")

        cancelled = threading.Event()
        def read_control_lines():
            for line in sys.stdin:
                try:
                    control = json.loads(line)
                except ValueError:
                    continue
                if isinstance(control, dict) and control.get("cancel") is True:
                    cancelled.set()
        threading.Thread(target=read_control_lines, name="ai-modeler-tool-cancel",
                         daemon=True).start()
        result = dsh_tool_backend.execute(request["name"], request.get("arguments") or {},
                                          cancel_event=cancelled)
        _emit({"ok": True, "result": result})
        return 0
    except Exception as exc:
        # Keep stack traces out of model-visible output and persistent state.
        code = "AI_MODELER_TOOL_CANCELLED" if 'cancelled' in locals() and cancelled.is_set() else "AI_MODELER_TOOL_FAILED"
        _emit({"ok": False, "error": {"code": code,
                                       "message": str(exc)[:1200] or type(exc).__name__}})
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
