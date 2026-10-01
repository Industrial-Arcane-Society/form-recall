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
SESSION_ID = "ai-modeler-replica-0857e758-r04"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]
PROMPT = (
    "请只根据这四张原始视图复刻同一工件。先辨识各视角与工件朝向；没有标注尺寸时，"
    "从最合适的正交视图估计外轮廓宽高比及各段比例，选择合理名义毫米尺寸并把估值标记为 "
    "image_estimate。不要添加参考图和用户文字没有支持的结构或细节。若主体确实绕同一轴回转，"
    "优先用一个完整闭合的回转轮廓建立主体，再按图像证据增量添加非回转细节。"
    "生成预览后，按视角将原图与当前模型单视图多模态比较；只按复核中有图像依据的差异返修，"
    "每次几何修改后重新预览并复核受影响的视图。不要用推测代替看图，不要为达到分数捏造特征。"
    "只有当前几何合理且当前 revision 的独立视图复核达到 80/100 才完成；无法判断的细节保持未知。"
)


def append_log(message: str) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"{stamp} {message}"
    with (RUN_DIR / "agent-events.txt").open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
    if message.startswith(("Agent 工具调用：", "Agent 决策摘要：", "DSH 状态：")):
        print(line, flush=True)


def copy_snapshot() -> None:
    source_session = dsh_agent._session_path(SESSION_ID)
    source_state = source_session / "cad-state.json"
    if source_state.is_file():
        shutil.copy2(source_state, RUN_DIR / "cad-state.json")
    source_previews = source_session / "previews"
    target_previews = RUN_DIR / "previews"
    if source_previews.is_dir():
        target_previews.mkdir(parents=True, exist_ok=True)
        for source in source_previews.glob("*.png"):
            shutil.copy2(source, target_previews / source.name)


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    for image_path in REFERENCE_IMAGES:
        if not image_path.is_file():
            raise FileNotFoundError(f"缺少参考图：{image_path}")

    cfg = config_module.load()
    llm_cfg = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
    append_log(
        "启动第四轮：四张原始视图，使用通用证据驱动的多模态复核提示词；"
        f"模型={llm_cfg.get('model', 'configured')}，推理强度={dsh_agent._effort(llm_cfg) or 'configured'}。"
    )
    append_log("Agent 输入包含 4 张参考图；用户没有提供毫米标注，尺寸由模型按图估算。")

    try:
        result = dsh_agent.run_dsh_agent(
            PROMPT,
            image_paths=[str(path) for path in REFERENCE_IMAGES],
            cfg=cfg,
            session_id=SESSION_ID,
            progress=append_log,
        )
        copy_snapshot()
        summary_path = RUN_DIR / "summary.json"
        summary_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output_dir = RUN_DIR / "outputs"
        output_dir.mkdir(exist_ok=True)
        for key, raw_path in (result.get("output_paths") or {}).items():
            source = Path(str(raw_path))
            if source.is_file():
                target = output_dir / source.name
                if source.resolve() != target.resolve():
                    shutil.copy2(source, target)
        append_log(
            "DSH 会话返回：状态=%s，工具调用=%s，模型轮次=%s，输出=%s。"
            % (
                result.get("state"),
                result.get("tool_calls"),
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
        append_log("收到中断；保留当前 CADPlan、复核历史和预览。")
        dsh_agent.close_session(SESSION_ID, delete_data=False)
        copy_snapshot()
        raise
    except Exception as exc:
        append_log(f"DSH 会话异常：{type(exc).__name__}: {exc}")
        copy_snapshot()
        raise


if __name__ == "__main__":
    sys.exit(main())
