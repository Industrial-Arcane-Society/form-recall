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
SESSION_ID = "ai-modeler-replica-0857e758-r29-missing-views"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]
SOURCE_STATE = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "run-28" / "cad-state.json"

PROMPT = """继续由产品里的 DSH Agent 完成参考件复刻。当前 CADPlan 是 Agent 通过 CAD MCP 工具创建的 revision 6，必须从此计划继续；不要在会话外编辑计划文件、脚本或网格，也不要整体重建。

明确的状态事实：revision 6 已有三个特征 main_revolve_body、hex_flange、hex_socket_shank；误建的 flange_bottom_chamfer 已删除。当前 revision 6 的预览 generation 为 iso=21、front=22、top=23、bottom=24。相同 geometry 的 iso/image-1 与 bottom/image-4 已经多模态复核；当前 finish_model 明确拒绝并告知缺少原图编号 2、3。请不要重复审查 iso/image-1 或 bottom/image-4；下一步只调用 cad_review_reference_similarity(view="front", reference_image_index=2)，随后只调用 cad_review_reference_similarity(view="top", reference_image_index=3)，使用 revision 6 对应的单视图预览。每次只配对一个参考图和匹配视图。

几何证据与边界：
- REFERENCE 2 正视图像素估值：总高约372 px；头部约0.33H，上圆柱约0.21H，法兰轴向厚度约0.07H，下柄约0.39H。现有 CADPlan 的72.27 mm总高、27.74 mm柄长、15.33 mm上圆柱、24.09 mm头部与这些比例接近；不得仅因一个复核分数改变已匹配的比例。
- REFERENCE 3 与 REFERENCE 4 是工件两端视图。先分别估量外轮廓宽/高比，检查是否存在稳定平边/角点，再与 REFERENCE 1 等轴图交叉核对。端视模型 R6 中四边比例约为22.5×19.49 mm；不要因一张复核说“圆”或“六边形”就立即换拓扑。若两端证据冲突，回看原图本身，记录可见边界和不确定性。
- 中心黄色区域、背景网格、十字线和光影不能单独证明孔或凸台。仅在闭合边界、对侧视图或等轴边缘支持时改动中心结构；证据不足就标 unknown。
- R6 中心浅六角槽、法兰边数仍属待核验估计。先完成 front/image-2 和 top/image-3 的当前版对照，再汇总四视图 evidence，决定是否定向修改。修改后重新预览受影响视图并复核新 revision。
- 不要对相同的 revision、preview_generation、view 和 reference_image_index 重复调用 review；不要把分数当作造型目标。若完成四个视图仍有明确差异，继续通过增量 CAD 工具修正；仅对图片本身无法确认的次要结构标记 unknown。达标后调用 cad_finish_model 输出工程与网格。""".strip()

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
    llm_cfg["reasoning_effort"] = "low"
    llm_cfg["thinking_budget"] = 2048
    progress(
        "正在启动 DSH Agent 继续上一位 Agent 由原图创建的 CADPlan；"
        f"模型={llm_cfg.get('model', 'configured')}，推理强度=low。"
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



