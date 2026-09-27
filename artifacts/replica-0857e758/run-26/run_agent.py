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
SESSION_ID = "ai-modeler-replica-0857e758-r26-tip-and-kernel-fix"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]
SOURCE_STATE = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "run-25" / "cad-state.json"

PROMPT = """继续由前一轮 AI Modeler Agent 通过 CAD MCP 工具创建和修改的 `main_revolve_body`。当前 CADPlan 是 Agent 的工作成果；你必须继续通过 CAD MCP 工具修改，不能在会话外手工改 JSON、调用脚本或整体重建。

四张输入图对应关系：REFERENCE 1 等轴测；REFERENCE 2 正视；REFERENCE 3 从弹头端沿轴看；REFERENCE 4 从柄端沿轴看。确认它们与当前工作件一致后继续。当前主体拓扑和主要比例已有证据支持：同轴回转体，柄—圆法兰—上圆柱—弹头头部；法兰为圆形的证据强于多边形，虚线彩色标记、接缝、中心光标和阴影不作为凹槽/孔证据。

正视图量得总高约372 px、法兰最大直径约114 px，H/D≈3.26。用名义法兰直径22.5 mm，则总高约73 mm；柄长约145 px（0.39H）、柄径约52 px（0.46D）、法兰厚约27 px（0.07H）、法兰顶至上圆柱末端约76 px（0.20H）、弹头段约123 px（0.33H）。现计划名义尺寸接近这些比例，请保留已匹配的总高/外径及轴段，不要为了分数重新猜整套尺寸。

当前 Agent 预览暴露出具体差异：run-25 revision 4 的 CADPlan 声明轮廓到 Z=72.27 mm，但 `last_preview.dimensions.bbox` 只到 Z=70.5 mm；原因是轮廓终点在轴线上，旧本地回转网格漏掉了该退化端面片。本轮运行已修正回转网格，请先重新调用 `cad_preview_model` 确认包围盒确实达到约72.27 mm，再复核。

当前 `profile` 末尾是 `[0.5,70.5]`、`[0,72.27]`，其正视轮廓收成尖点，与 REFERENCE 2 顶端可见的短平端不符。按比例估算，顶端平面直径约9–12 px，即约1.8–2.4 mm（半径约0.9–1.2 mm）；标记为 image_estimate。修正头部时，保持轮廓单调收敛且圆弧饱满：在 `Z_tip≈72.27 mm` 处先到非零 `r_tip`，再用 `[0,Z_tip]` 水平闭合。最后两点必须表达该平端，不能只以 `[0,Z_tip]` 作为外轮廓端点；检查修改后的实际预览轮廓和包围盒，不要只改特征说明。

先用修正后的内核预览当前计划，再针对端部差异调用匹配原图的多模态复核。只修改由正视图明确支持的头部轮廓，不动已匹配的法兰和柄比例。每次更新后生成新 revision 的正视与等轴预览，重新复核受影响视图。若 review 建议和原图证据冲突，优先看原图对应轮廓；分数相同或波动本身不是几何修改依据。所有几何修改由你通过 CAD MCP 工具完成。

最终要在同一 revision 检查四个视图：iso 对 REFERENCE 1、front 对 REFERENCE 2、从头端的 top 对 REFERENCE 3、从柄端的 bottom 对 REFERENCE 4。逐图核对轴对称拓扑、总高/法兰直径比例、各段长度、顶端曲率和平端，以及两端的可见中心结构；中心十字或颜色覆盖层不要建成实体特征。所有视图达到可见结构与比例吻合后，调用完成工具输出可编辑工程、网格和核验报告，并检查结果路径。若工具或几何核仍有错误，读取具体返回值再修正；不能只返回对话状态。""".strip()

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
    revision = state.get("revision")
    plan = state.get("plan")
    if isinstance(revision, int) and isinstance(plan, dict):
        plan_dir = RUN_DIR / "plan-revisions"
        plan_dir.mkdir(parents=True, exist_ok=True)
        (plan_dir / ("revision-%d.json" % revision)).write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    last_preview = state.get("last_preview") or {}
    preview_source = Path(str(last_preview.get("preview_image") or ""))
    if preview_source.is_file():
        preview_dir = RUN_DIR / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        view = str(last_preview.get("preview_image_view_set") or "view")
        generation = last_preview.get("preview_generation") or state.get("preview_generation") or 0
        preview_target = preview_dir / ("r%d-g%d-%s.png" % (revision or 0, generation, view))
        if preview_source.resolve() != preview_target.resolve():
            shutil.copy2(preview_source, preview_target)
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

