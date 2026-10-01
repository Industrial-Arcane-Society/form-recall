import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from ai_modeler.agent.agentloop import run_agent_loop


IMAGE = Path(
    r"C:\Users\lycohana\.codex\attachments\0e0f3409-8877-4d84-a9d4-32a5bce2b544\image-1.png"
)
OUT = Path(__file__).resolve().parent
if not IMAGE.is_file():
    raise SystemExit("Reference image not found: %s" % IMAGE)
if any(OUT.glob("20*-*")):
    raise SystemExit("Output directory already contains a prior run: %s" % OUT)

cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
llm = cfg.setdefault("llm", {})
llm["reasoning_effort"] = "high"
llm["thinking_budget"] = 4096
cfg.setdefault("agentloop", {})["total_timeout_sec"] = 900

started = time.monotonic()


def progress(message):
    elapsed = int(time.monotonic() - started)
    print("[%04ds] %s" % (elapsed, message), flush=True)


result = run_agent_loop(
    "复刻",
    image_paths=[str(IMAGE)],
    cfg=cfg,
    output_dir=str(OUT),
    progress=progress,
)
run_record = result.get("run_record_path")
summary = {
    "state": result.get("state"),
    "reply": result.get("reply"),
    "dimensions": result.get("dimensions"),
    "output_paths": result.get("output_paths"),
    "model_call_count": len(result.get("model_calls") or []),
    "tool_trace_count": len(result.get("tool_trace") or []),
    "usage": result.get("usage"),
    "run_record": run_record,
    "elapsed_seconds": round(time.monotonic() - started, 1),
}
summary_path = OUT / "summary.json"
summary_path.write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
