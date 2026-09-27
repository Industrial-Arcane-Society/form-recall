from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from ai_modeler.agent import dsh_agent
from ai_modeler.runtime import config as config_module


RUN_DIR = Path(__file__).resolve().parent
SESSION_ID = "ai-modeler-replica-0857e758-r10"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]
SOURCE_STATE = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "run-09" / "cad-state.json"
PROMPT = (
    "继续 run-09 revision 3 的 CADPlan，不要重建主体。4 张原图对应 iso=1、front=2、top=3、bottom=4。"
    "我对原始 image-2 正视轮廓量得总宽/总高约 115/372=0.31；当前预览为 28x28x75 mm，宽高比 0.373，明显过宽。按 image_estimate 将最大直径收至约 23 mm，整体高度保持约 75 mm。"
    "按正视像素比例，底杆直径约 10 mm、长度约 28 mm；法兰厚约 5 mm、直径约 23 mm；上段圆柱直径约 15-16 mm，向顶端平滑收成带小圆角的弹头轮廓。这些是比例估算，不是标注尺寸。"
    "原图优先于单次复核文字：image-1/2/3 显示法兰外缘为圆盘/圆环，不要只因为复核文字说‘六角’就添加六角螺母；只有原图明确显示六个平面才建六角特征。中心小记号/接缝也不能直接当孔槽。"
    "先读取当前状态，然后只更新影响上述外形的已知 revolve 轮廓；不要查询 box、pattern、prism 等无关特征规格。更新后立即预览 front 并用 reference_image_index=2 复核；只根据新版证据决定下一步。"
    "若正视通过，再逐张复核 iso=1、top=3、bottom=4；一次只比较一张原图和对应单视图。每个数值来源标 image_estimate。"
    "每次只调用一个相关 CAD 工具，不输出长篇说明，不网页搜索、不生成代码、不推送 CATIA。四张原图在同一最终 revision 均达 80/100 且本地几何校验通过才算达标。"
)


def append_log(message: str) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().strftime('%H:%M:%S')} {message}"
    with (RUN_DIR / "agent-events.txt").open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
    if message.startswith(("Agent 工具调用：", "Agent 决策摘要：", "DSH 状态：")):
        print(line, flush=True)


def copy_snapshot() -> None:
    source_session = dsh_agent._session_path(SESSION_ID)
    source_state = source_session / "cad-state.json"
    if source_state.is_file():
        shutil.copy2(source_state, RUN_DIR / "cad-state.json")
        state = json.loads(source_state.read_text(encoding="utf-8"))
        revision = state.get("revision")
    else:
        revision = None
    contact_sheet = source_session / "reference-contact-sheet.png"
    if contact_sheet.is_file():
        shutil.copy2(contact_sheet, RUN_DIR / "input-reference-sheet.png")
    target_previews = RUN_DIR / "previews"
    target_previews.mkdir(parents=True, exist_ok=True)
    source_previews = source_session / "previews"
    if source_previews.is_dir() and isinstance(revision, int):
        # Keep only the latest revision's views; intermediate previews stay in
        # the external DSH session cache instead of accumulating in artifacts.
        for source in source_previews.glob(f"r{revision}-g*.png"):
            shutil.copy2(source, target_previews / source.name)


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if not SOURCE_STATE.is_file():
        raise FileNotFoundError(f"缺少 run-09 CAD 状态：{SOURCE_STATE}")
    state = json.loads(SOURCE_STATE.read_text(encoding="utf-8"))
    current_plan = state.get("plan")
    if not isinstance(current_plan, dict) or not current_plan.get("features"):
        raise ValueError("run-09 CADPlan 不包含可继续的特征")

    cfg = config_module.load()
    llm_cfg = dict(cfg.get("llm") or {})
    llm_cfg["reasoning_effort"] = "minimal"
    cfg["llm"] = llm_cfg
    append_log(
        "启动第十轮：从 run-09 revision 3 继续，按正视轮廓比例修正并立即复核；"
        f"模型={llm_cfg.get('model', 'configured')}，本轮推理强度=minimal，运行配置仅在内存中覆盖。"
    )
    try:
        result = dsh_agent.run_dsh_agent(
            PROMPT,
            image_paths=[str(path) for path in REFERENCE_IMAGES],
            cfg=cfg,
            session_id=SESSION_ID,
            current_plan=current_plan,
            progress=append_log,
        )
        copy_snapshot()
        (RUN_DIR / "summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output_dir = RUN_DIR / "outputs"
        output_dir.mkdir(exist_ok=True)
        for raw_path in (result.get("output_paths") or {}).values():
            source = Path(str(raw_path))
            if source.is_file() and source.resolve() != (output_dir / source.name).resolve():
                shutil.copy2(source, output_dir / source.name)
        append_log(
            "DSH 会话返回：状态=%s，工具调用=%s，模型轮次=%s，输出=%s。"
            % (
                result.get("state"), result.get("tool_calls"),
                result.get("model_call_count"),
                ",".join((result.get("output_paths") or {}).keys()) or "无",
            )
        )
        print(json.dumps({
            "state": result.get("state"),
            "tool_calls": result.get("tool_calls"),
            "model_call_count": result.get("model_call_count"),
            "output_paths": result.get("output_paths"),
            "run_record": result.get("run_record"),
        }, ensure_ascii=False), flush=True)
        return 0
    except KeyboardInterrupt:
        append_log("收到中断；保留当前 CADPlan 与预览。")
        dsh_agent.close_session(SESSION_ID, delete_data=False)
        copy_snapshot()
        raise
    except Exception as exc:
        append_log(f"DSH 会话异常：{type(exc).__name__}: {exc}")
        copy_snapshot()
        raise


if __name__ == "__main__":
    sys.exit(main())
