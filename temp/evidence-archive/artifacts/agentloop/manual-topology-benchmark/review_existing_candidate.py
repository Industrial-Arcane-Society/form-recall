"""Run the production multimodal reviewer on an existing saved CADPlan."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from ai_modeler.agent import agentloop, llm


RUN_PATH = (ROOT / "artifacts" / "agentloop" /
            "20260926-stepped-block-retest-sse-fixed" /
            "20260926-140220-ac4e4248" / "run.json")
REVIEW_DIR = ROOT / "artifacts" / "agentloop" / "20260926-compact-visual-review"
IMAGE_PATHS = [
    Path(r"C:\Users\lycohana\AppData\Local\Temp\codex-clipboard-ed8d31d7-08e3-4099-9716-508c99c6dbf6.png"),
    Path(r"C:\Users\lycohana\AppData\Local\Temp\codex-clipboard-4c747551-0ffd-4789-9cd7-4187fd67c729.png"),
]


def main():
    if not RUN_PATH.is_file():
        raise SystemExit(f"Missing candidate run: {RUN_PATH}")
    if not all(path.is_file() for path in IMAGE_PATHS):
        raise SystemExit("A source image is missing")
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_PATH.open("r", encoding="utf-8-sig") as stream:
        run = json.load(stream)
    with (ROOT / "config.json").open("r", encoding="utf-8") as stream:
        full_config = json.load(stream)
    llm_config = full_config.get("llm", full_config)
    prompt = (
        "根据两张同一工件参考图检查已建模型。无尺寸标注；只按可见外形、孔位和前后关系核对。"
        "请逐张比较同方向原图与当前模型视图，差异要指向可修改特征。"
    )
    image_names = [path.name for path in IMAGE_PATHS]
    source_content = [{"type": "text", "text": prompt}]
    source_content.extend(llm.image_content_part(str(path)) for path in IMAGE_PATHS)
    source_messages = [{"role": "user", "content": source_content}]
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    started = time.monotonic()

    def progress(message):
        print(f"[{int(time.monotonic() - started):03d}s] {message}", flush=True)

    state = {
        "cfg": full_config,
        "model_cfg": llm_config,
        "deadline": time.monotonic() + 1800,
        "session_dir": REVIEW_DIR,
        "run_prompt": prompt,
        "run_image_paths": [str(path) for path in IMAGE_PATHS],
        "started_at": started_at,
        "run_started_at": started_at,
        "current_plan": run["plan"],
        "latest_preview": run["preview"],
        "active_reference_images": image_names,
        "selected_reference_images": image_names,
        "reference_image_paths": {path.name: str(path) for path in IMAGE_PATHS},
        "model_calls": [],
        "tool_calls": 0,
        "tool_trace": [],
        "preview_calls": 0,
        "tool_preview_calls": 0,
        "review_preview_calls": 0,
        "previews": {},
        "limits": {"tool_calls": 256, "context_chars": 1_000_000,
                   "context_turns": 128, "context_summary_chars": 32_000},
        "temporary_review_images": [],
        "progress": progress,
        "review_history": [],
        "open_multiview_findings": [],
        "repair_cycles": 0,
        "web_search_calls": 0,
        "blocked_web_search_calls": 0,
        "independent_reference_inventory": None,
        "routing": {"action": "review_existing_candidate",
                    "review_mode": "same_view_multimodal"},
    }
    try:
        inventory_path = REVIEW_DIR / "reference_inventory.json"
        if inventory_path.is_file():
            with inventory_path.open("r", encoding="utf-8-sig") as stream:
                saved_inventory = json.load(stream)
            inventory = saved_inventory.get("inventory")
            if isinstance(inventory, dict) and inventory.get("images"):
                state["independent_reference_inventory"] = inventory
                progress("复用仅由原始图片生成的盲读清单；复核仍逐像素重新核对")
        if not state.get("independent_reference_inventory"):
            inventory, inventory_gaps = agentloop._independent_reference_inventory(
                source_messages, image_names, prompt, llm_config, state, None)
            inventory_path.write_text(
                json.dumps({"inventory": inventory, "gaps": inventory_gaps},
                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = agentloop._split_view_review(
            run["plan"], run["preview"], source_messages,
            llm_config, state, None, "multiview_check", 1, prompt,
            views=["front", "iso"],
        )
    except Exception as exc:
        (REVIEW_DIR / "review-failure.json").write_text(
            json.dumps({"error": str(exc), "model_calls": state["model_calls"],
                        "active_request": state.get("active_request"),
                        "independent_reference_inventory":
                            state.get("independent_reference_inventory"),
                        "temporary_review_images": state["temporary_review_images"]},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise
    summary = {
        "state": result["decision"],
        "reviewed_views": result["reviewed_views"],
        "skipped_views": result["skipped_views"],
        "checks": result["checks"],
        "issues": result["issues"],
        "questions": result["questions"],
        "review_gaps": result["review_gaps"],
        "view_incompatible_findings": result["view_incompatible_findings"],
        "feature_group_checks": result["feature_group_checks"],
        "blind_reference_hole_targets": result["blind_reference_hole_targets"],
        "local_image_comparisons": result["local_image_comparisons"],
        "single_view_reports": result["single_view_reports"],
        "model_calls": state["model_calls"],
        "temporary_review_images": state["temporary_review_images"],
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    (REVIEW_DIR / "review.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in (
        "state", "reviewed_views", "skipped_views", "issues", "review_gaps",
        "questions", "view_incompatible_findings",
        "temporary_review_images", "elapsed_seconds")},
        ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
