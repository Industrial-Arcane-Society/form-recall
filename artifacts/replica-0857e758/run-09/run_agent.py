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
SESSION_ID = "ai-modeler-replica-0857e758-r09"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]
PROMPT = (
    "这是一个全新的独立复刻，从空 CADPlan 建模；不要读取或沿用 run-05 至 run-08 的候选。"
    "4 张图是同一工件的不同视角：1 等轴测、2 正视、3 俯视、4 仰视。"
    "先用正视图估计 X/Z 总体比例、俯视图估计 X/Y 比例，再结合等轴图识别前后层级；尺寸统一按图像估算并标 image_estimate。"
    "先识别并建立清晰稳定的主体、杆部、法兰/环肩、上部圆柱段和弹头轮廓；不要把所有形状变化含混地塞进反复变化的单一轮廓。"
    "若回转主体必须用一个完整 revolve，调用前一次性估好完整轮廓；之后优先用独立增量特征表达非回转结构，避免改杆部时破坏头部比例。"
    "不把屏幕中心标记、选择高亮或渲染接缝当成孔/槽；孔槽必须有清晰边界并获得合适视图支持。不要加图像未支持的装饰。"
    "每次几何更新只针对一个明确差异；更新后立即生成对应视图，并用匹配 reference_image_index 复核原图，再决定下一次修改。"
    "参考索引：iso=1、front=2、top=3、bottom=4。每次复核只比较一张原图和一个新版单视图。"
    "不用网页搜索，不执行任意代码，不推送 CATIA。4 张原图在同一最终 revision 都达到 80/100 且几何校验通过才算达标；否则保留候选和具体未解决差异。"
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
    cfg = config_module.load()
    llm_cfg = dict(cfg.get("llm") or {})
    llm_cfg["reasoning_effort"] = "none"
    cfg["llm"] = llm_cfg
    append_log(
        "启动第九轮：全新空 CADPlan，从四张参考图开始，使用逐次更新后立即复核的指令；"
        f"模型={llm_cfg.get('model', 'configured')}，本轮推理强度=off，运行配置仅在内存中覆盖。"
    )
    try:
        result = dsh_agent.run_dsh_agent(
            PROMPT,
            image_paths=[str(path) for path in REFERENCE_IMAGES],
            cfg=cfg,
            session_id=SESSION_ID,
            current_plan=None,
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
