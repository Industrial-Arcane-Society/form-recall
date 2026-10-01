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
SESSION_ID = "ai-modeler-replica-0857e758-r11"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]

PROMPT = """
请直接依据这四张同一工件的原图生成并复核一个可编辑的本地 CAD 模型，不要先向用户提问。四图顺序：1 等轴测，2 正视，3 顶视，4 底视。所有毫米值都是图像估算值，不是标注尺寸。

先看完整四图拼版并统一理解工件。它是竖直同轴的销/弹头状零件：底部细圆柱杆，中部一片薄圆盘法兰，法兰上方较粗的直圆柱，再接圆钝的弹头曲线。主体绕 Z 轴对称，法兰是圆盘，不是六角螺母。正视图里圆盘和六角件的投影可能相似，不能据此推断六角；等轴测图显示圆形连续外缘。

按正视图像素比例估算：总高约 74 mm；底杆直径约 10 mm、长度约 28 mm；法兰外径约 22–23 mm、厚约 5–6 mm；法兰上方圆柱直径约 15–16 mm、高约 15 mm；弹头段约 24 mm 高，根部直径与圆柱相近，平滑收尖到顶部约 1–2 mm 的小平面，不要做成锐锥。由正视像素估算的高/杆径比约 7.4，法兰直径/杆径比约 2.2，圆柱直径/杆径比约 1.55。尺寸先后关系和模型内比例优先于毫米绝对值。

优先用一个闭合回转轮廓建立连续实体，轮廓包含底杆、圆法兰、上圆柱、圆钝弹头及可见的小台阶；每个估算参数标记 image_estimate。正视图没有侧向孔。顶视/底视中心的圆线或着色不够证明有贯穿孔；除非原图能看清孔壁和开口，否则不要加轴向孔。颜色、网格线、阴影、三角剖分缝不作为几何特征。

工作步骤：简短决定后直接用 CAD 工具创建首版；首个主体完成后立刻生成等轴测预览，并按原图做独立多模态复核。根据证据一次只改相关特征；每次修改后复核受影响视图，再回归正视、俯视、底视。原图证据优先于一次复核中无法从该投影确认的结构判断；有投影歧义就保留为未知，不按猜测加六角或孔。每次只输出简短动作说明，不写长篇自我质疑，不重复说明已知尺寸。

仅当同一最终 revision 的四个配对检查均通过（1/iso、2/front、3/top、4/bottom），逐项证据没有中等或更高严重度的可见差异，本地几何核验通过，且外形包围盒与上述图像比例估算一致时才调用完成工具。若无法解决，保留候选和明确的差异证据，不要把分数高于 80 当作唯一依据。
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
        "# 第十一轮复刻记录",
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
        "- `run_agent.py`：空白 CAD 状态启动的 DSH 运行脚本和本轮提示。",
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
    if dsh_agent._session_path(SESSION_ID).exists():
        raise RuntimeError(f"DSH 会话目录已存在，避免复用旧模型状态：{SESSION_ID}")

    cfg = config_module.load()
    cfg = json.loads(json.dumps(cfg))
    llm_cfg = cfg.setdefault("llm", {})
    llm_cfg["reasoning_effort"] = "minimal"
    llm_cfg["thinking_budget"] = 2048
    append_log(
        "启动第十一轮：从空白 CADPlan 使用四张原图；"
        f"模型={llm_cfg.get('model', 'configured')}，推理强度=minimal，思考预算=2048。"
    )
    result = None
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
