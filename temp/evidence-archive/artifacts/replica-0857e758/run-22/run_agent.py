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
SESSION_ID = "ai-modeler-replica-0857e758-r22-review-gated"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]

PROMPT = """仅依据本次提供的四张同一零件参考图，从空白状态新建可编辑三维模型。不要读取、恢复或沿用以前任何一轮的 CADPlan、尺寸假设或模型。四图顺序：1 等轴测，2 正视，3 沿主轴看弹头端，4 沿主轴看柄端。

这是无标注尺寸的比例复刻任务。我按正视原图估读的证据仅供你核对和采用/修正，不是绝对尺寸：总高约 374 px，最大法兰宽约 114 px，所以高/法兰直径约 3.28；可选总高约 73 mm、法兰直径约 22.5 mm。柄部轴向长度约占总高 0.38、直径约为法兰直径 0.46；法兰厚约占总高 0.07；法兰以上直圆柱段长度约占总高 0.21、直径约为法兰直径 0.54；弹头曲面段约占总高 0.33。所有换算值均为 image_estimate，重点是保持这些相对比例。

请回看四张原图验证这些判断再建模：法兰外缘在等轴图和正视投影没有可靠的六角直边/角点证据，按圆形法兰建，不要因为像素边线或渲染分面改成六角棱柱。头部是平滑、连续收敛的弹头/圆弧轮廓，尖端有小圆钝平面，不是直线圆锥尖顶。正视图可辨柄、法兰、法兰上方直圆柱、曲面头部四段的轴向比例。顶/底视图中心的十字、彩色圆点/高亮与背景网格是视口标记，不当成实体孔或槽；只有清晰闭合且由其它视角支持的轮廓才建端面孔槽。

先在首个 CAD 工具调用前简要列出你采用的视角映射、比例和不确定项；随后用 CAD 工具从零创建主体。主体完成后立即生成 iso 和 front 预览，核对外包比例，再按图像证据添加细节。按需要逐张对照当前模型视图与对应原图；发现误差时只修改有证据支持的受影响参数，不要为追分反复摇摆。最后校验模型；只有本地工程和网格确实保存成功时才调用完成工具。不要只返回文字或 CADPlan，不要编造校验、输出路径或完成状态。""".strip()


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
