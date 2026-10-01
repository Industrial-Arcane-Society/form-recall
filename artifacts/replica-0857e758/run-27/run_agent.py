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
SESSION_ID = "ai-modeler-replica-0857e758-r27-evidence-reset"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]
SOURCE_STATE = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "run-26" / "cad-state.json"

PROMPT = """继续 AI Modeler Agent 在上一轮通过 CAD MCP 工具建立的同一工件模型；当前特征树由 Agent 创建。所有几何更改必须继续由你通过 CAD MCP 工具完成，不能在会话外手工改 JSON、生成脚本或整体清空重建。

重要纠偏：前一轮任务提示把“圆法兰、无孔”当成了既定事实，这是助手的猜测，不是用户给出的约束。四张图是本轮唯一的用户证据；尺寸和结构推断都标记为 image_estimate/derived。请不要继承“圆法兰”“无孔”结论，也不要把任何先前助手推测称作用户要求。多模态复核的建议同样是待核实线索：既不能照抄，也不能因为先前假设就一概登记为误判。

四图对应：REFERENCE 1 等轴测，REFERENCE 2 正视，REFERENCE 3 从头端看，REFERENCE 4 从柄端看。正视图总高约372 px、法兰最大宽约114 px，H/D≈3.26；采用法兰最大宽约22.5 mm时总高约73 mm。柄长约0.39H、柄径约0.46D、法兰厚约0.07H、上圆柱段约0.20H、头部约0.33H。这些是图像估值；已接近此比例的主体尺寸不要因模糊的“偏长/偏窄”评论改动，先用同视图像素比和模型参数计算验证。

R26 最新 CADPlan revision 8 含 Agent 新增的 `flange_chamfers` prism-cut 特征，且尚未预览。先校验并预览整棵当前特征树。该特征目前的 profile 是 `[[0,0],[12.5,0],[11.25,0.5],[0,0.5]]`、axis Z；核实它在真实预览中是否产生了目标的环形倒角/退台，以及是否改变/破坏法兰。若它只是一个矩形棱柱切块或造成错误，按 CAD 工具返回的 schema 和当前视图证据修复或删除；不要只根据 feature note 认定成功。轴对称轮廓的圆周倒角/台阶优先进入 revolve 的 `[radius,Z]` profile；多边形法兰需用闭合真实多边形 prism，不能用单块 `prism cut` 冒充环形切槽。

重新读取原图判断外廓：REFERENCE 1 等轴图和 REFERENCE 2 正视图里，法兰侧面/内部线可能是圆柱接缝，也可能是多边形平面边界；请用真实直边连续性和不同视图共同确认，不预设圆形或六边形。REFERENCE 3/4 中心附近有闭合深色边界、同心轮廓及径向边线；背景网格、横纵十字线和黄色选中颜色是叠加层候选，但其内侧闭合的黑色/灰色边界可能是中心孔、沉孔、六角窝或台阶。核对两个端视图的轮廓是否同轴、是否在多个视图重复，并用局部边缘连续性判断；证据仍不足则记为 unknown，而不是直接声明“无孔”或直接挖通孔。

R26 的 top profile 最后半径降到 0.3 mm。REFERENCE 2 顶端平端宽约9–12 px，对应直径约1.8–2.4 mm、半径约0.9–1.2 mm（image_estimate）；较小端面半径会更尖，较大半径会更钝。结合真实正视预览判断后用 `cad_update_feature` 修正，并检查 profile 末点的几何方向；不要反向改变参数却声称轮廓变钝。

先根据原图可确认的差异定向修正；当前 review score 重复不代表当前几何没有变化，也不能单独当作修改理由。对每条建议先比较 reference 与 model 的归一化包围盒比例、特征尺寸比和边界位置，再决定接受、拒绝或设为不可观测。不得把本轮提示里的估算冒充用户尺寸。几何每次变化后都生成当前 revision 预览；最终在同一 revision 将 iso/front/top/bottom 分别与匹配的四张原图多模态复核，按证据修到相符后完成并导出可编辑工程、网格及核验报告。""".strip()

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


