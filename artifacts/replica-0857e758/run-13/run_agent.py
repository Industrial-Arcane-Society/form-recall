from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from ai_modeler.agent import dsh_agent
from ai_modeler.runtime import config as config_module


RUN_DIR = Path(__file__).resolve().parent
SESSION_ID = "ai-modeler-replica-0857e758-r13"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]
SOURCE_STATE = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "run-12" / "cad-state.json"

PROMPT = """
继续 run12 revision 3 的 CADPlan，保留主体、弹头曲线和底部浅盲槽，不要清空重建。四图顺序：1 等轴测、2 正视、3 顶视、4 底视。重新查看原始图和 run12 四个单视图预览：顶视、底视的法兰外缘均近似圆形但可见若干平边；正视可见中央面和两侧斜面。现有尖锐规则六边形在顶视/底视明显过于尖，应改成接近圆形的八边形/截角多边形，不要在完美圆盘和尖锐六边形之间来回切换。

只修改当前法兰特征 `hex_flange`，保留 `main_body`、`bottom_hex_socket` 以及其余尺寸比例。将法兰改成八边形 prism，外接 X/Y 尺寸约 22.5 mm、高约 5.8 mm、底面中心 [0,0,28]。若 CADPlan prism 使用顶点轮廓，可按顺时针使用近似顶点 (11.25,4.67)、(4.67,11.25)、(-4.67,11.25)、(-11.25,4.67)、(-11.25,-4.67)、(-4.67,-11.25)、(4.67,-11.25)、(11.25,-4.67)；这些数值均标记 image_estimate。修改前读取当前 feature spec，确认字段与坐标语义；不要改成六边形，也不要让圆形连接芯超出法兰平面边界。

保留图像比例估值：总高约 73–74 mm；底杆 Ø10×28 mm；法兰约 22.5×22.5×5.8 mm；法兰上方圆柱 Ø15.5 mm、高约 15 mm；弹头段约 24 mm，顶部约 Ø1–2 mm 小平面。底视中心保留浅盲槽，除非新视图明确显示贯穿，不得改为通孔。颜色、阴影、背景网格和渲染三角线不作为几何证据。

改完后生成当前 revision 的 iso、front、top、bottom 单视图，并逐张对照对应原图。分数低于80或 evidence 存在中等以上可见差异时，修改可定位的特征并对新 revision 重新预览/复核；当评审把同一差异同时说成明确 mismatch 和 unobservable 时，结合其余视图判断，不重复调用来刷分。每个工具动作的决策简短，不输出长篇自我质疑。只有四个配对视图在同一 revision 均达到80分、没有未解决的中等以上可见差异、本地几何校验通过且比例吻合时才完成，否则保留候选并报告未解决证据。
""".strip()


_last_thought_bucket = 0


def _atomic_copy(source: Path, destination: Path) -> None:
    if not source.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        temp_path = Path(stream.name)
    try:
        shutil.copy2(source, temp_path)
        temp_path.replace(destination)
    finally:
        temp_path.unlink(missing_ok=True)


def copy_snapshot() -> dict:
    """Keep one latest state and preview, so interruption preserves useful evidence."""
    source_dir = dsh_agent._session_path(SESSION_ID)
    state_path = source_dir / "cad-state.json"
    state = {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        _atomic_copy(state_path, RUN_DIR / "cad-state.json")
    except (OSError, json.JSONDecodeError):
        pass
    _atomic_copy(source_dir / "current-preview.png", RUN_DIR / "current-preview.png")
    _atomic_copy(source_dir / "reference-contact-sheet.png",
                 RUN_DIR / "reference-contact-sheet.png")
    return state


def append_log(message: str) -> None:
    global _last_thought_bucket
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if "模型思考流：已接收增量" in message:
        match = re.search(r"累计约\s*([\d,]+)\s*字符", message)
        count = int(match.group(1).replace(",", "")) if match else 0
        bucket = count // 10000
        if bucket <= _last_thought_bucket:
            return
        _last_thought_bucket = bucket
        message = f"推理流累计约 {bucket * 10000:,} 字符"
    if not message.startswith((
        "Agent 工具调用：", "Agent 决策摘要：", "DSH 状态：",
        "启动第", "正在启动", "模型等待中：", "模型响应已返回：", "推理流累计",
        "收到中断", "DSH 会话异常：",
    )):
        return
    line = f"{datetime.now().strftime('%H:%M:%S')} {message}"
    with (RUN_DIR / "agent-events.txt").open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
    print(line, flush=True)
    if message.startswith(("Agent 决策摘要：", "DSH 状态：", "模型响应已返回：")):
        copy_snapshot()


def write_report(result: dict | None, interrupted: bool = False) -> None:
    state = copy_snapshot()
    revision = state.get("revision")
    latest: dict[tuple[str, int], dict] = {}
    for row in state.get("reference_similarity_history") or []:
        if not isinstance(row, dict) or row.get("revision") != revision:
            continue
        key = (str(row.get("view") or ""), row.get("reference_image_index"))
        latest[key] = row
    expected = [("iso", 1), ("front", 2), ("top", 3), ("bottom", 4)]
    review_rows = []
    current_pass = True
    for view, index in expected:
        row = latest.get((view, index))
        if not row:
            current_pass = False
            review_rows.append(f"| {index} | {view} | 缺失 | 未复核 |")
            continue
        evidence_bad = any(
            str(item.get("status") or "").lower() in {
                "mismatch", "partial", "partial_match", "partial_mismatch"
            }
            and str(item.get("mismatch_severity") or "").lower() in {
                "medium", "moderate", "high", "major", "critical"
            }
            for item in row.get("evidence") or [] if isinstance(item, dict)
        )
        passed = float(row.get("score", 0)) >= 80 and not evidence_bad
        current_pass = current_pass and passed
        mismatches = "; ".join(
            f"{item.get('item')} ({item.get('mismatch_severity')})"
            for item in row.get("evidence") or [] if isinstance(item, dict)
            and str(item.get("status") or "").lower() in {
                "mismatch", "partial", "partial_match", "partial_mismatch"
            }
        ) or "无"
        review_rows.append(
            f"| {index} | {view} | {row.get('score')} | "
            f"{'通过' if passed else '未通过'}：{mismatches} |"
        )
    validation = (result or {}).get("validation") or {}
    preview = state.get("last_preview") or {}
    dimensions = preview.get("dimensions") or {}
    geometry = preview.get("local_geometry_checks") or {}
    geometry_pass = geometry.get("state") == "passed"
    finished = bool(state.get("finished"))
    accepted = finished and current_pass and geometry_pass
    summary = {
        "state": (result or {}).get("state"),
        "tool_calls": (result or {}).get("tool_calls"),
        "model_call_count": (result or {}).get("model_call_count"),
        "output_paths": (result or {}).get("output_paths"),
        "validation": validation,
    }
    (RUN_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = [
        "# 第十三轮复刻记录",
        "",
        f"## 结论\n\n{'已满足本轮四视图和本地几何门槛。' if accepted else '本轮未达标，结果保留为诊断候选。'}",
        "",
        f"- 会话：`{SESSION_ID}`；运行状态：`{'中断保存' if interrupted else (result or {}).get('state', '未知')}`。",
        f"- 最终 revision：{revision}；Agent finished：{finished}；四视图同版通过：{current_pass}。",
        f"- 模型：`{((config_module.load().get('llm') or {}).get('model') or 'configured')}`；推理强度：minimal；本轮 thinking budget：2048。",
        f"- 特征数：{len((state.get('plan') or {}).get('features') or [])}；最终预览尺寸：{dimensions.get('size')} mm；几何核验：{geometry.get('state', '无') }。",
        "",
        "## 同版多模态复核",
        "",
        "| 原图 | 预览视角 | 分数 | 证据摘要 |",
        "| ---: | --- | ---: | --- |",
        *review_rows,
        "",
        "## 文件",
        "",
        "- `run_agent.py`：从 run12 revision 3 继续的 DSH 运行脚本和本轮提示。",
        "- `agent-events.txt`：工具调用与阶段结果；思考流只记录累计长度。",
        "- `cad-state.json`：最近 CADPlan、预览和视觉复核历史。",
        "- `current-preview.png`：最新单张预览；需以其对应的 preview revision 为准。",
        "- `summary.json`：Agent 返回摘要。",
    ]
    (RUN_DIR / "REPORT.md").write_text("\n".join(text) + "\n", encoding="utf-8")


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    existing = [path.name for path in RUN_DIR.iterdir()
                if path.name not in {"run_agent.py", "__pycache__"}]
    if existing:
        raise RuntimeError(f"运行目录非空，避免覆盖旧证据：{RUN_DIR}")
    if not all(path.is_file() for path in REFERENCE_IMAGES):
        raise FileNotFoundError("四张原始参考图不完整")
    if not SOURCE_STATE.is_file():
        raise FileNotFoundError(f"缺少 run12 CAD 状态：{SOURCE_STATE}")
    source_state = json.loads(SOURCE_STATE.read_text(encoding="utf-8"))
    starting_plan = source_state.get("plan")
    if not isinstance(starting_plan, dict) or not starting_plan.get("features"):
        raise ValueError("run12 revision 3 中没有可继续的有效 CADPlan")
    if dsh_agent._session_path(SESSION_ID).exists():
        raise RuntimeError(f"DSH 会话目录已存在，避免复用旧模型状态：{SESSION_ID}")

    cfg = config_module.load()
    cfg = json.loads(json.dumps(cfg))
    llm_cfg = cfg.setdefault("llm", {})
    llm_cfg["reasoning_effort"] = "minimal"
    llm_cfg["thinking_budget"] = 2048
    append_log(
        "启动第十三轮：从 run12 revision 3 继续，修正法兰外缘并复核四视图；"
        f"模型={llm_cfg.get('model', 'configured')}，推理强度=minimal，思考预算=2048。"
    )
    result = None
    try:
        result = dsh_agent.run_dsh_agent(
            PROMPT,
            image_paths=[str(path) for path in REFERENCE_IMAGES],
            cfg=cfg,
            session_id=SESSION_ID,
            current_plan=starting_plan,
            progress=append_log,
        )
        copy_snapshot()
        output_dir = RUN_DIR / "outputs"
        output_dir.mkdir(exist_ok=True)
        for raw_path in (result.get("output_paths") or {}).values():
            source = Path(str(raw_path))
            target = output_dir / source.name
            if source.is_file() and source.resolve() != target.resolve():
                shutil.copy2(source, target)
        append_log(
            "DSH 会话返回：状态=%s，工具调用=%s，模型轮次=%s。"
            % (result.get("state"), result.get("tool_calls"),
               result.get("model_call_count"))
        )
        write_report(result)
        print(json.dumps({
            "state": result.get("state"),
            "tool_calls": result.get("tool_calls"),
            "model_call_count": result.get("model_call_count"),
            "output_paths": result.get("output_paths"),
            "run_record": result.get("run_record"),
        }, ensure_ascii=False), flush=True)
        return 0
    except KeyboardInterrupt:
        append_log("收到中断；保存当前 revision、CADPlan 和最近预览。")
        copy_snapshot()
        dsh_agent.close_session(SESSION_ID, delete_data=False)
        write_report(result, interrupted=True)
        return 130
    except Exception as exc:
        append_log(f"DSH 会话异常：{type(exc).__name__}: {exc}")
        copy_snapshot()
        write_report(result)
        raise


if __name__ == "__main__":
    sys.exit(main())
