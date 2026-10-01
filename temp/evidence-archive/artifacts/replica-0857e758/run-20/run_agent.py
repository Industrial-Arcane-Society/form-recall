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
SESSION_ID = "ai-modeler-replica-0857e758-r20-from-images"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]

PROMPT = """仅依据本次提供的四张同一零件参考图，从空白状态新建可编辑三维模型。不要读取、恢复或沿用任何以前的CADPlan或模型。四张图依次是等轴测、正视、顶视、底视。

请由你自己观察并判断可见结构和比例：图片没有尺寸标注时，按视图内相对比例估算一套自洽的毫米尺寸，并把估算来源标为 image_estimate；不要因缺少绝对尺寸而停下来询问。优先还原各视图都能支持的真实几何结构；对遮挡或不能确认的端部细节保持简单并记录不确定性。不要把网格、阴影、颜色或渲染分面误认作零件几何。

请直接使用 CAD 建模工具完成工作：先创建主体，再按需要逐个添加特征；生成模型预览，对照四张原图检查主要外轮廓、各段比例、同轴关系、台阶/凸缘和端面可见细节，有明确可定位的差异时修改相关特征并用修改后的模型复核。最后进行模型校验；只有模型确实保存成功时才调用完成工具。不要只返回文字或 CADPlan，不要编造校验、输出路径或完成状态。""".strip()


def save_snapshot() -> dict:
    session_dir = dsh_agent._session_path(SESSION_ID)
    state_path = session_dir / "cad-state.json"
    state = {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(state_path, RUN_DIR / "cad-state.json")
    except (OSError, json.JSONDecodeError):
        pass
    for name in ("current-preview.png", "reference-contact-sheet.png"):
        source = session_dir / name
        if source.is_file():
            shutil.copy2(source, RUN_DIR / name)
    return state


def progress(message: str) -> None:
    if message.startswith("模型思考流：已接收增量"):
        return
    allowed = (
        "Agent 工具调用：", "Agent 决策摘要：", "DSH 状态：", "模型响应已返回：",
        "模型等待中：", "正在启动", "DSH 会话异常：", "收到中断",
    )
    if not message.startswith(allowed):
        return
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().strftime('%H:%M:%S')} {message}"
    with (RUN_DIR / "agent-events.txt").open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
    print(line, flush=True)
    if message.startswith(("Agent 决策摘要：", "DSH 状态：", "模型响应已返回：")):
        save_snapshot()


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if any(path.name not in {"run_agent.py", "__pycache__"} for path in RUN_DIR.iterdir()):
        raise RuntimeError(f"运行目录非空，避免覆盖旧证据：{RUN_DIR}")
    if not all(path.is_file() for path in REFERENCE_IMAGES):
        raise FileNotFoundError("四张原始参考图不完整")
    if dsh_agent._session_path(SESSION_ID).exists():
        raise RuntimeError(f"DSH 会话目录已存在，避免复用旧状态：{SESSION_ID}")

    cfg = json.loads(json.dumps(config_module.load()))
    llm_cfg = cfg.setdefault("llm", {})
    llm_cfg["reasoning_effort"] = "minimal"
    llm_cfg["thinking_budget"] = 2048
    progress(
        "正在启动空白 CADPlan 的 DSH Agent；只附四张原图；"
        f"模型={llm_cfg.get('model', 'configured')}，推理强度=minimal。"
    )
    result = None
    try:
        result = dsh_agent.run_dsh_agent(
            PROMPT,
            image_paths=[str(path) for path in REFERENCE_IMAGES],
            cfg=cfg,
            session_id=SESSION_ID,
            current_plan=None,
            progress=progress,
        )
        state = save_snapshot()
        output_dir = RUN_DIR / "outputs"
        output_dir.mkdir(exist_ok=True)
        outputs = result.get("output_paths") or {}
        for raw_path in outputs.values():
            source = Path(str(raw_path))
            target = output_dir / source.name
            if source.is_file() and source.resolve() != target.resolve():
                shutil.copy2(source, target)
        summary = {
            "state": result.get("state"),
            "finished": bool(state.get("finished")),
            "revision": state.get("revision"),
            "feature_count": len((state.get("plan") or {}).get("features") or []),
            "tool_calls": result.get("tool_calls"),
            "model_call_count": result.get("model_call_count"),
            "output_paths": outputs,
            "run_record": result.get("run_record"),
        }
        (RUN_DIR / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (RUN_DIR / "REPORT.md").write_text(
            "# 从参考图空白建模的 Agent 运行\n\n"
            f"- 会话：`{SESSION_ID}`\n"
            "- 初始 CADPlan：空；仅输入四张原始参考图与本轮简短任务说明。\n"
            f"- Agent 状态：`{result.get('state')}`；Agent finished：`{bool(state.get('finished'))}`。\n"
            f"- 几何版本：{state.get('revision')}；特征数：{summary['feature_count']}。\n"
            f"- 工具调用：{result.get('tool_calls')}；模型轮次：{result.get('model_call_count')}。\n"
            f"- 输出路径：`{json.dumps(outputs, ensure_ascii=False)}`\n",
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0
    except KeyboardInterrupt:
        progress("收到中断；保存 Agent 当前状态。")
        save_snapshot()
        dsh_agent.close_session(SESSION_ID, delete_data=False)
        return 130
    except Exception as exc:
        progress(f"DSH 会话异常：{type(exc).__name__}: {exc}")
        save_snapshot()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
