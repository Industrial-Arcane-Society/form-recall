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
SESSION_ID = "ai-modeler-replica-0857e758-r14"
REFERENCE_DIR = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "references"
REFERENCE_IMAGES = [REFERENCE_DIR / f"image-{index}.png" for index in range(1, 5)]
SOURCE_STATE = PROJECT_ROOT / "artifacts" / "replica-0857e758" / "run-13" / "cad-state.json"

PROMPT = """
继续 run13 revision 7 的当前 CADPlan，不要清空重建。四张原图顺序：1 等轴测，2 正视，3 顶视，4 底视。只修正法兰轮廓，保留主体、底部盲槽和全部尺寸。

跨视图几何证据：正视图2里法兰总宽约114 px，中央平面约49 px，两侧斜面各约33 px。图像估算中央平面/总宽约0.43，中央面/单侧投影宽约1.5；这支持带一个正向平面和两侧斜面的近规则八边形，不支持完美圆盘，也不是尖锐六边形。顶视图3、底视图4的外缘能看到多段平边，只是低分辨率下接近圆形。将其记为图像估值，不冒充标注尺寸。

仅更新 `hex_flange` 的 prism profile，固定外接 X/Y 包围宽约22.5 mm、厚5.8 mm、底面中心 [0,0,28]、沿+Z。为匹配正视面宽比例，使用 b=4.84 mm：[[11.25,4.84],[4.84,11.25],[-4.84,11.25],[-11.25,4.84],[-11.25,-4.84],[-4.84,-11.25],[4.84,-11.25],[11.25,-4.84]]。所有这些参数标记 image_estimate。保留 `main_body`、`bottom_hex_socket` 原样；底视黄色区域不能单凭颜色推断为贯穿孔，顶视径向线不能单凭渲染线推断为切槽。

上一轮独立复核互相矛盾：iso/top/bottom 多次称原图圆形，front 又看见平面或六边形；不能根据单一视角的“像圆”评语把有平面证据的法兰改成圆盘。按上述可量化正视证据修正一次后，生成同一 revision 的 iso、front、top、bottom 预览，并且每张图都调用一次 `cad_review_reference_similarity` 与对应原图配对。先完成独立复核，再作总判断；不要用 `cad_record_visual_review` 替代多模态复核，也不要只复核两张就再次改版。若某视角仍把近规则八边形误判为圆盘，结合正视平面比例和另一张带平边的正交图处理；没有新增几何证据时不要重复或回滚这个参数。随后调用本地几何校验，并保存可编辑模型及网格。每一步给出简短决策，不写长篇自我质疑。
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
        "# 第十四轮复刻记录",
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
        "- `run_agent.py`：从 run13 revision 7 继续的 DSH 运行脚本和本轮提示。",
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
        raise FileNotFoundError(f"缺少 run13 CAD 状态：{SOURCE_STATE}")
    source_state = json.loads(SOURCE_STATE.read_text(encoding="utf-8"))
    starting_plan = source_state.get("plan")
    if not isinstance(starting_plan, dict) or not starting_plan.get("features"):
        raise ValueError("run13 revision 7 中没有可继续的有效 CADPlan")
    if dsh_agent._session_path(SESSION_ID).exists():
        raise RuntimeError(f"DSH 会话目录已存在，避免复用旧模型状态：{SESSION_ID}")

    cfg = config_module.load()
    cfg = json.loads(json.dumps(cfg))
    llm_cfg = cfg.setdefault("llm", {})
    llm_cfg["reasoning_effort"] = "minimal"
    llm_cfg["thinking_budget"] = 2048
    append_log(
        "启动第十四轮：从 run13 revision 7 继续，按正视面宽比例修正法兰并复核四视图；"
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
