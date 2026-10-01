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
SESSION_ID = "ai-modeler-replica-0857e758-r18"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]
SOURCE_STATE = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "run-17" / "cad-state.json"

PROMPT = """继续 run17 revision 1 的 CADPlan，不要清空重建。四图顺序：1 等轴测，2 正视，3 顶视，4 底视。run17 的近圆法兰已使 iso/top/bottom 外圈匹配，保持法兰、杆部、鼻部主轮廓和底端沉孔/六角槽完全不变。

唯一待修差异来自原图3顶视：鼻尖中心可见一个小的六边形/螺母状边界；当前模型中心为圆形平端，因此 top 评分79.9并被识别为核心差异。不要添加贯穿孔，也不要改变鼻尖外轮廓。只在端面中心增加浅六角盲凹部：
- feature id nose_tip_hex_recess，kind prism，operation cut；
- 正六边形外接半径约1.1 mm，中心 [0,0,72.3]，轴 Z，深度约0.7 mm，开口与 Z=73 mm 端面齐平；
- profile 六点等角排列，可用 [1.1,0]、[0.55,0.9526]、[-0.55,0.9526]、[-1.1,0]、[-0.55,-0.9526]、[0.55,-0.9526]；
- 参数来源标记 image_estimate，深度仅为浅盲凹估算，不能声称贯穿。
如果该切除因切到鼻尖端面范围外而失败，请先检查当前 profile 的端面半径并用最小可见尺寸修正，不能扩宽外轮廓。

只新增这个一个局部特征。随后重生成同一 revision 的 iso/front/top/bottom，逐张多模态复核四图，检查顶视中心轮廓和其它视图的端部外形；运行本地几何校验并尝试 finish。不得为了分数修改其它几何；若仍有具证据的差异，记录具体视图、证据与未解决项。""".strip()


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
        "# 第十八轮复刻记录",
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
        "- `run_agent.py`：从 run17 revision 1 继续的 DSH 运行脚本和本轮提示。",
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
        raise FileNotFoundError(f"缺少 run17 CAD 状态：{SOURCE_STATE}")
    source_state = json.loads(SOURCE_STATE.read_text(encoding="utf-8"))
    starting_plan = source_state.get("plan")
    if not isinstance(starting_plan, dict) or not starting_plan.get("features"):
        raise ValueError("run14 revision 1 中没有可继续的有效 CADPlan")
    if dsh_agent._session_path(SESSION_ID).exists():
        raise RuntimeError(f"DSH 会话目录已存在，避免复用旧模型状态：{SESSION_ID}")

    cfg = config_module.load()
    cfg = json.loads(json.dumps(cfg))
    llm_cfg = cfg.setdefault("llm", {})
    llm_cfg["reasoning_effort"] = "minimal"
    llm_cfg["thinking_budget"] = 2048
    append_log(
        "启动第十八轮：从 run17 revision 1 继续，修正鼻尖六边形中心凹部并复核四视图；"
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






