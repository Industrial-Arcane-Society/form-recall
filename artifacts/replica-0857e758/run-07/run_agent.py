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
SESSION_ID = "ai-modeler-replica-0857e758-r07"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]
SOURCE_STATE = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "run-06" / "cad-state.json"
PROMPT = (
    "延续传入的 CADPlan，不要从头建模或重复估尺寸。先读 CAD 状态中的 reference_images 和当前特征树。"
    "本次拼版编号已核对：REFERENCE 1=等轴测，REFERENCE 2=正视，REFERENCE 3=俯视，REFERENCE 4=仰视。"
    "复核时每次只比较一组匹配视图：iso 用 reference_image_index=1，front 用 2，top 用 3，bottom 用 4。"
    "每张原始参考图都要在当前几何 revision 单独复核。不要用其它视角中的结构评价当前投影，也不要重复发送所有原图。"
    "优先检查并修复 run-06 暴露的差异：不要让圆柱 flange_band 遮掉六角法兰；保持主体连接；"
    "top_recess 只有在俯视图确实支持时才保留。不要添加测试特征到交付 CADPlan。"
    "修改只根据相应单视图复核返回的证据，修改后重新预览并复核受影响原图。按简短步骤调用工具，"
    "本任务不需要网页搜索。只有当前版本几何有效且所有参考图复核通过才完成；若证据不能支持返修，"
    "保留候选并列出未解决差异，不要猜测。"
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
    contact_sheet = source_session / "reference-contact-sheet.png"
    if contact_sheet.is_file():
        shutil.copy2(contact_sheet, RUN_DIR / "input-reference-sheet.png")
    target_previews = RUN_DIR / "previews"
    target_previews.mkdir(parents=True, exist_ok=True)
    for source in source_session.glob("*.png"):
        if source.name != "reference-contact-sheet.png":
            shutil.copy2(source, target_previews / source.name)
    source_previews = source_session / "previews"
    if source_previews.is_dir():
        for source in source_previews.glob("*.png"):
            shutil.copy2(source, target_previews / source.name)


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if not SOURCE_STATE.is_file():
        raise FileNotFoundError(f"缺少前一轮 CAD 状态：{SOURCE_STATE}")
    state = json.loads(SOURCE_STATE.read_text(encoding="utf-8"))
    current_plan = state.get("plan")
    if not isinstance(current_plan, dict) or not current_plan.get("features"):
        raise ValueError("前一轮 CADPlan 不包含可继续的特征")

    cfg = config_module.load()
    llm_cfg = dict(cfg.get("llm") or {})
    llm_cfg["reasoning_effort"] = "low"
    cfg["llm"] = llm_cfg
    append_log(
        "启动第七轮：从 run-06 的 revision 24 CADPlan 继续，单图编号映射复核；"
        f"模型={llm_cfg.get('model', 'configured')}，本轮推理强度=low，原配置不修改。"
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
