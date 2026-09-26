import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from ai_modeler.agent.agentloop import run_agent_loop


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts" / "agentloop" / "20260926-stepped-block-retest-sse-fixed"
if OUT.exists() and any(OUT.iterdir()):
    raise SystemExit(f"Output directory already contains files: {OUT}")

with (ROOT / "config.json").open("r", encoding="utf-8") as stream:
    cfg = json.load(stream)
cfg.setdefault("agentloop", {})["total_timeout_sec"] = 1800
cfg["agentloop"]["context_chars"] = 4_000_000
cfg["agentloop"]["context_turns"] = 8192

images = [
    Path(r"C:\Users\lycohana\AppData\Local\Temp\codex-clipboard-ed8d31d7-08e3-4099-9716-508c99c6dbf6.png"),
    Path(r"C:\Users\lycohana\AppData\Local\Temp\codex-clipboard-4c747551-0ffd-4789-9cd7-4187fd67c729.png"),
]
missing = [str(path) for path in images if not path.is_file()]
if missing:
    raise SystemExit("Missing reference images: " + ", ".join(missing))

prompt = (
    "用这两张同一工件参考图生成可编辑模型。第一张是等轴测图，第二张是正视图。"
    "没有标注尺寸，请从两张图自己估算并标记 image_estimate。正视外轮廓宽高比约 0.95；"
    "等轴图可见厚度约为高度的 0.36；正面中央区域后退，后退量可先估约 20 mm 并由原图复核。"
    "结构应有连续厚实体外壳，右侧外壁贯穿全高，中央是后退实体面而不是背景空槽；"
    "正面左、中、右三组各有 3 个贯穿孔，纵向大-小-大。"
    "先建立匹配外廓比例的完整主体，再逐个切除凹槽和孔；首个候选完成后，"
    "用与原图相同的视角逐张多模态复核并定向修正。"
)

started = time.monotonic()


def progress(message):
    elapsed = int(time.monotonic() - started)
    print(f"[{elapsed:04d}s] {message}", flush=True)


result = run_agent_loop(
    prompt,
    image_paths=[str(path) for path in images],
    cfg=cfg,
    output_dir=str(OUT),
    progress=progress,
)
summary = {
    "state": result.get("state"),
    "reply": result.get("reply"),
    "dimensions": result.get("dimensions"),
    "output_paths": result.get("output_paths"),
    "model_call_count": len(result.get("model_calls") or []),
    "tool_trace": [
        {"tool": row.get("tool"), "result_summary": row.get("result_summary")}
        for row in result.get("tool_trace") or []
    ],
    "usage": result.get("usage"),
    "run_record": result.get("run_record_path"),
    "elapsed_seconds": int(time.monotonic() - started),
}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
