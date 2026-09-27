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
SESSION_ID = "ai-modeler-replica-0857e758-r25-schema-repair"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]
SOURCE_STATE = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "run-23" / "cad-state.json"

PROMPT = """继续上一位 AI Modeler Agent 在上一会话里通过 cad_create_model、仅依据这四张原图从空白创建的 main_revolve_body。当前 CADPlan 是 Agent 输出，不是人工建模；保留它作为唯一基线，不要清空重建或调用 cad_create_model(replace=true)。

现有主体是按图片比例估算的一体回转体：bbox 目标约 22.5 × 22.5 × 73 mm；柄部长度约 0.38H、直径约 0.46D_flange；法兰厚约 0.07H；法兰以上直圆柱段约 0.21H、直径约 0.54D_flange；头部曲面约 0.33H。四图顺序：1 等轴测，2 正视，3 沿主轴看弹头端，4 沿主轴看柄端。尺寸均为 image_estimate，相对比例比绝对毫米值重要。

当前 plan 来自 Agent，但校验时发现 revolve 缺少必需的 angle 参数；请先用 cad_update_feature 为 `main_revolve_body` 补 `angle=360`，并用 `source_updates.angle` 标为 derived，证据写明四张视图支持绕主轴完整回转。此参数修复发生在计划尚不能预览时，允许先修复。通过校验后，必须先预览 iso/front，再调用 cad_review_reference_similarity 将模型预览与对应原图独立对照。关键几何证据：法兰外缘为圆形，没有可靠六角直边/角点；头部为平滑的弹头/圆弧曲面并以小钝平面收尾，不是直线圆锥尖顶；顶/底视图中心的十字、彩色圆点/高亮与背景网格是视口标记，不当成实体孔槽。遇到失败工具时读出并处理具体 error；不要把来源元数据更新当成几何修正。仅在多模态复核指出具体可见差异后，依据门禁流程修改相关特征并复核新 revision；最后完成校验并保存工程、网格和报告，不能只返回对话状态。""".strip()

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
    if not SOURCE_STATE.is_file():
        raise FileNotFoundError(f"缺少 Agent 创建的基础 CADPlan：{SOURCE_STATE}")
    source_state = json.loads(SOURCE_STATE.read_text(encoding="utf-8"))
    starting_plan = source_state.get("plan")
    if not isinstance(starting_plan, dict) or not starting_plan.get("features"):
        raise ValueError("上一轮 Agent 没有留下有效 CADPlan")
    if dsh_agent._session_path(SESSION_ID).exists():
        raise RuntimeError(f"DSH 会话目录已存在，避免复用旧状态：{SESSION_ID}")

    cfg = json.loads(json.dumps(config_module.load()))
    llm_cfg = cfg.setdefault("llm", {})
    llm_cfg["reasoning_effort"] = "minimal"
    llm_cfg["thinking_budget"] = 2048
    progress(
        "正在启动 DSH Agent 继续上一位 Agent 由原图创建的 CADPlan；"
        f"模型={llm_cfg.get('model', 'configured')}，推理强度=minimal。"
    )
    result = None
    try:
        result = dsh_agent.run_dsh_agent(
            PROMPT,
            image_paths=[str(path) for path in REFERENCE_IMAGES],
            cfg=cfg,
            session_id=SESSION_ID,
            current_plan=starting_plan,
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
            "# Agent 继续从参考图创建的模型\n\n"
            f"- 会话：`{SESSION_ID}`\n"
            "- 初始 CADPlan：由上一 DSH Agent 仅凭四张原图创建；本轮只负责通过工具预览、复核和定向返修。\n"
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
