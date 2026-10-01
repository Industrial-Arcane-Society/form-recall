"""Headless entry point for the same DSH Agent used by the desktop app."""
from __future__ import annotations

import argparse
import contextlib
import datetime as _datetime
import json
import os
import re
import shutil
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_OUTPUT_KEYS = {
    "aimodel": "candidate.aimodel",
    "stl": "candidate.stl",
    "preview": "preview.png",
    "report": "geometry-report.json",
    "preview_front": "views/front.png",
    "preview_iso": "views/iso.png",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ai_modeler agent",
        description="通过与桌面 Agent 创造模式相同的 DSH/MCP 工作流建模；不会启动桌面窗口。",
    )
    parser.add_argument("prompt", nargs="?", help="用户的自然语言建模要求")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text", "--prompt", dest="text",
                        help="用户的自然语言建模要求")
    source.add_argument("--text-file", help="从 UTF-8/UTF-16/GB18030 文件读取要求")
    parser.add_argument("--image", action="append", default=[], metavar="PATH",
                        help="参考图路径；可重复传入多张同一工件的图片")
    parser.add_argument("--session-id", help="复用 DSH 对话与 CAD 状态的稳定会话 ID")
    parser.add_argument("--output-dir", help="交付文件和本轮运行记录的输出目录")
    parser.add_argument("--offline", action="store_true",
                        help="仅关闭 Agent 网页搜索；不修改全局 config.json")
    parser.add_argument("--debug", action="store_true", help="错误时在 stderr 输出 Python traceback")
    return parser


def _decode_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    raise ValueError("文本文件编码无法识别；请保存为 UTF-8、UTF-16 或 GB18030。")


def _resolve_prompt(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    values = [value for value in (args.prompt, args.text) if value is not None]
    if args.text_file:
        if values:
            parser.error("位置参数/--text 与 --text-file 不能同时使用")
        try:
            prompt = _decode_text(Path(args.text_file).expanduser())
        except OSError as exc:
            parser.error("读取 --text-file 失败：%s" % exc)
    elif values:
        if len(values) > 1:
            parser.error("位置参数与 --text/--prompt 不能同时使用")
        prompt = values[0]
    else:
        parser.error("请提供位置参数、--text 或 --text-file")
    if not prompt.strip():
        parser.error("建模要求不能为空")
    return prompt


def _session_id(value: str | None) -> str:
    if value is None:
        return "ai-modeler-" + uuid.uuid4().hex
    if not _SESSION_ID_RE.fullmatch(value):
        raise ValueError("session-id 仅允许 1–64 个 ASCII 字母、数字、下划线和连字符，且必须以字母或数字开头。")
    return value


_NEGATED_MODEL_EDIT = re.compile(
    r"(?:不要|不需|不需要|无需|勿|禁止).{0,10}(?:修改|改动|调整|更新|增删)", re.I)
_DIRECT_MODEL_EDIT = re.compile(
    r"(?:把|将).{0,40}(?:改成|改为|调整为|增加|减少|加大|缩小|加深|减浅|移到|删除|新增)|"
    r"(?:改成|改为|调整为|增加|减少|加大|缩小|加深|减浅|删除|新增).{0,40}"
    r"(?:mm|毫米|cm|厘米|孔|槽|尺寸|直径|半径|高度|宽度|长度)", re.I)
_MODEL_CREATE = re.compile(
    r"(?:创建|新建|建立|建模|做一个|制作一个).{0,32}"
    r"(?:长方体|圆柱|模型|工件|零件|孔|槽|螺钉|螺栓)", re.I)
_TURN_QUERY = re.compile(
    r"[?？]|(?:请)?(?:复述|总结|说明|告诉我|解释|查看|核对|确认|当前是什么|现在是什么|"
    r"多少|几个|多大|是否|是不是|能否|可以吗)", re.I)


def _infer_user_intent(prompt: str, has_references: bool) -> tuple[str | None, bool | None]:
    """Classify CLI turns for evidence invalidation, without rewriting text."""
    text = str(prompt or "").strip()
    if has_references:
        return "model_target", True
    if _NEGATED_MODEL_EDIT.search(text):
        return "query", False
    if _DIRECT_MODEL_EDIT.search(text) or _MODEL_CREATE.search(text):
        return "geometry_edit", True
    if _TURN_QUERY.search(text):
        return "query", False
    return None, None


@contextlib.contextmanager
def _session_lock(session_dir: Path):
    """Prevent two CLI processes from mutating one persistent DSH session."""
    session_dir.mkdir(parents=True, exist_ok=True)
    lock_path = session_dir / ".agent-cli.lock"
    stream = lock_path.open("a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("该 DSH 会话正在另一进程中运行：%s" % session_dir.name) from exc
            locked = True
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("该 DSH 会话正在另一进程中运行：%s" % session_dir.name) from exc
            locked = True
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        stream.close()


def _redact(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if secret:
                result = result.replace(secret, "[REDACTED]")
        result = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
                        r"\1[REDACTED]", result)
        result = re.sub(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+",
                        r"\1[REDACTED]", result)
        result = re.sub(r"(?i)([?&](?:api[_-]?key|access[_-]?token|token)=)[^&#\s]+",
                        r"\1[REDACTED]", result)
        return result
    if isinstance(value, dict):
        return {str(key): _redact(child, secrets) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact(child, secrets) for child in value]
    if isinstance(value, tuple):
        return [_redact(child, secrets) for child in value]
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _conversation_history_path(session_dir: Path) -> Path:
    return session_dir / "conversation-history.json"


def _load_conversation_history(session_dir: Path, output_base: Path) -> list[dict[str, Any]]:
    """Load saved visible turns, or reconstruct them from archived CLI results."""
    saved = _read_json(_conversation_history_path(session_dir))
    saved_turns = saved.get("turns")
    if saved.get("schema_version") == 1 and isinstance(saved_turns, list):
        return [row for row in saved_turns if isinstance(row, dict)]

    manifest = _read_json(session_dir / "user-turns.json")
    user_turns = {str(row.get("turn_id")): row for row in manifest.get("turns", [])
                  if isinstance(row, dict) and row.get("turn_id")}
    history = []
    turns_root = output_base / "turns"
    if turns_root.is_dir():
        for turn_dir in turns_root.iterdir():
            if not turn_dir.is_dir():
                continue
            turn_id = turn_dir.name
            user_turn = user_turns.get(turn_id)
            if not user_turn:
                continue
            result = _read_json(turn_dir / "result.json")
            reply = result.get("reply")
            if not isinstance(reply, str) or not reply.strip():
                continue
            history.append({
                "turn_id": turn_id,
                "user_prompt": str(user_turn.get("raw_user_prompt") or ""),
                "assistant_reply": reply,
                "assistant_source": "dsh_final_reply",
                "result_state": str(result.get("state") or "unknown"),
                "created_at": str(user_turn.get("created_at") or ""),
            })
    history.sort(key=lambda row: (row.get("created_at", ""), row.get("turn_id", "")))
    return history


def _save_visible_turn(session_dir: Path, history: list[dict[str, Any]],
                       prompt: str, assistant_reply: str, result_state: str,
                       assistant_source: str, secrets: list[str]) -> list[dict[str, Any]]:
    active = _read_json(session_dir / "active-turn.json")
    turn_id = str(active.get("turn_id") or uuid.uuid4().hex)
    created_at = _datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    row = {
        "turn_id": turn_id,
        "user_prompt": str(prompt or ""),
        "assistant_reply": _redact(str(assistant_reply or ""), secrets),
        "assistant_source": assistant_source,
        "result_state": result_state,
        "created_at": created_at,
    }
    turns = [*history, row]
    path = _conversation_history_path(session_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "turns": turns},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return turns


def _turn_output_dir(base: Path, session_dir: Path) -> Path:
    active = _read_json(session_dir / "active-turn.json")
    turn_id = str(active.get("turn_id") or uuid.uuid4().hex)
    safe_turn_id = re.sub(r"[^A-Za-z0-9_-]", "-", turn_id)[:96] or uuid.uuid4().hex
    return base / "turns" / safe_turn_id


def _copy_outputs(result: dict[str, Any], turn_dir: Path, session_dir: Path) -> dict[str, str]:
    turn_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str] = {}
    warnings: list[str] = []
    output_paths = result.get("output_paths")
    if isinstance(output_paths, dict):
        for key, filename in _OUTPUT_KEYS.items():
            raw = output_paths.get(key)
            if not isinstance(raw, str) or not raw:
                continue
            source = Path(raw).expanduser()
            try:
                if not source.is_file():
                    if key in {"aimodel", "stl", "preview", "report"}:
                        warnings.append("交付文件缺失：%s" % key)
                    continue
                target = turn_dir / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied[key] = str(target.resolve())
            except OSError as exc:
                warnings.append("复制交付文件失败（%s）：%s" % (key, exc))
                continue
    else:
        output_paths = {}

    has_model = bool((result.get("plan") or {}).get("features"))
    if has_model:
        for key in ("aimodel", "stl", "report"):
            if key not in copied and not any(key in warning for warning in warnings):
                warnings.append("交付文件未提供：%s" % key)
        if result.get("state") == "accepted" and "preview" not in copied:
            warnings.append("已接受模型没有可复制的预览文件。")

    observability = result.get("observability")
    raw_stream = observability.get("path") if isinstance(observability, dict) else None
    if isinstance(raw_stream, str):
        source = Path(raw_stream)
        if source.is_file():
            target = turn_dir / "agent-observability.jsonl"
            active = _read_json(session_dir / "active-turn.json")
            turn_id = str(active.get("turn_id") or "")
            started_ms = active.get("started_at_epoch_ms")
            ended_ms = active.get("completed_at_epoch_ms") or int(time.time() * 1000)
            rows = []
            try:
                for line in source.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(row, dict):
                        continue
                    row_turn_id = row.get("turn_id")
                    if row_turn_id == turn_id:
                        rows.append(row)
                        continue
                    # Earlier bridge rows without turn_id are attributable only
                    # by their timestamp. Keep them within this turn's window.
                    at = row.get("at")
                    if row_turn_id is None and isinstance(at, (int, float)):
                        at_ms = at if at > 10**12 else at * 1000
                        if (isinstance(started_ms, (int, float)) and
                                started_ms <= at_ms <= ended_ms):
                            rows.append(row)
                target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                                  for row in rows), encoding="utf-8")
            except (OSError, UnicodeError, TypeError, ValueError) as exc:
                # Never fall back to copying the entire multi-turn session log
                # into one turn archive; that would make its call totals wrong.
                result["observability"]["copied_path"] = None
                result["observability"]["copied_turn_id"] = turn_id or None
                result["observability"]["copied_event_count"] = None
                result["observability"]["copy_error"] = type(exc).__name__
            else:
                result["observability"]["copied_path"] = str(target.resolve())
                result["observability"]["copied_turn_id"] = turn_id or None
                result["observability"]["copied_event_count"] = len(rows)

    for source_name, target_name in (
        ("active-turn.json", "active-turn.json"),
        ("user-turns.json", "user-turns.json"),
        ("conversation-history.json", "conversation-history.json"),
        ("runtime-capacity-policy.json", "runtime-capacity-policy.json"),
    ):
        source = session_dir / source_name
        if source.is_file():
            shutil.copy2(source, turn_dir / target_name)

    result["output_paths"] = copied
    result["cli_output_dir"] = str(turn_dir.resolve())
    result["delivery_warnings"] = warnings
    return copied


def _exit_code(result: dict[str, Any]) -> int:
    if result.get("error") or result.get("state") == "error":
        return 3
    if result.get("delivery_warnings"):
        return 5
    if result.get("state") == "conversation" and result.get("reply"):
        return 0
    if result.get("state") == "accepted":
        checks = result.get("local_geometry_checks")
        geometry_passed = (isinstance(checks, dict) and
                           checks.get("geometry_state") == "passed" and
                           checks.get("brep_valid") is True)
        validation = result.get("validation")
        plan_valid = isinstance(validation, dict) and validation.get("valid") is True
        if geometry_passed and plan_valid:
            return 0
    return 4


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    prompt = _resolve_prompt(args, parser)
    try:
        session_id = _session_id(args.session_id)
    except ValueError as exc:
        parser.error(str(exc))

    image_paths: list[str] = []
    for raw in args.image:
        image = Path(raw).expanduser().resolve()
        if not image.is_file():
            parser.error("参考图不存在或不是文件：%s" % raw)
        image_paths.append(str(image))

    from ai_modeler.agent import dsh_agent
    from ai_modeler.paths import temp_path
    from ai_modeler.runtime import config

    cfg = json.loads(json.dumps(config.load(), ensure_ascii=False))
    if args.offline:
        online = cfg.get("online")
        if not isinstance(online, dict):
            online = {}
            cfg["online"] = online
        online["enabled"] = False
    llm_cfg = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
    secrets = [str(llm_cfg.get("api_key") or "")]
    session_dir = dsh_agent._session_path(session_id)
    # The app session owns persistent CAD/user-turn state. The bundled
    # harness cannot resume its persisted DSH conversation id from a new CLI
    # process, so each CLI turn uses a fresh DSH id while keeping app state.
    dsh_session_id = "%s-dsh-%s" % (session_id, uuid.uuid4().hex[:12])
    user_intent, affects_model_target = _infer_user_intent(prompt, bool(image_paths))
    output_base = (Path(args.output_dir).expanduser().resolve() if args.output_dir else
                   temp_path("agent-logs", "agent-cli", session_id))
    conversation_history = _load_conversation_history(session_dir, output_base)
    progress_events: list[dict[str, Any]] = []
    started = time.monotonic()
    result: dict[str, Any]
    run_error: str | None = None

    def on_progress(message: str) -> None:
        elapsed = round((time.monotonic() - started) * 1000, 1)
        safe_message = _redact(str(message), secrets)
        event = {"elapsed_ms": elapsed, "message": safe_message}
        progress_events.append(event)
        stamp = _datetime.datetime.now().astimezone().strftime("%H:%M:%S")
        sys.stderr.write("[%s +%.1fs] %s\n" % (stamp, elapsed / 1000.0, safe_message))
        sys.stderr.flush()

    try:
        with _session_lock(session_dir):
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    result = dsh_agent.run_dsh_agent(
                        prompt,
                        image_paths=image_paths,
                        cfg=cfg,
                        session_id=session_id,
                        dsh_session_id=dsh_session_id,
                        user_intent=user_intent,
                        affects_model_target=affects_model_target,
                        intent_source="cli_rules" if user_intent else None,
                        conversation_history=conversation_history,
                        progress=on_progress,
                    )
            finally:
                # Flush and close the in-process DSH runtime but retain the
                # DSH home, CAD state, references, output, and timelines.
                dsh_agent.close_session(session_id, delete_data=False)
            result["cli_progress_events"] = progress_events
            result["cli_session_id"] = session_id
            result["cli_dsh_session_id"] = dsh_session_id
            result["cli_reference_paths"] = image_paths
            turn_dir = _turn_output_dir(output_base, session_dir)
            conversation_history = _save_visible_turn(
                session_dir, conversation_history, prompt,
                str(result.get("reply") or ""), str(result.get("state") or "unknown"),
                "dsh_final_reply", secrets)
            result["cli_conversation_history_path"] = str(
                _conversation_history_path(session_dir).resolve())
            result["cli_conversation_turn_count"] = len(conversation_history)
            _copy_outputs(result, turn_dir, session_dir)
            result = _redact(result, secrets)
            result["cli_exit_code"] = _exit_code(result)
            result["cli_outcome"] = (
                "accepted_and_verified" if result["cli_exit_code"] == 0 and
                result.get("state") == "accepted" else
                "conversation_completed" if result["cli_exit_code"] == 0 else
                "candidate_not_accepted" if result["cli_exit_code"] == 4 else
                "delivery_failed" if result["cli_exit_code"] == 5 else "error")
            result_path = turn_dir / "result.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result["cli_result_path"] = str(result_path.resolve())
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                              allow_nan=False) + "\n", encoding="utf-8")
            sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2,
                                        allow_nan=False) + "\n")
            sys.stdout.flush()
            return int(result["cli_exit_code"])
    except Exception as exc:
        run_error = _redact(str(exc), secrets)
        if args.debug:
            sys.stderr.write(_redact(traceback.format_exc(), secrets) + "\n")
        sys.stderr.write("Agent CLI 错误：%s\n" % run_error)
        try:
            turn_dir = _turn_output_dir(output_base, session_dir)
            turn_dir.mkdir(parents=True, exist_ok=True)
            saved_state = _read_json(session_dir / "cad-state.json")
            partial = {
                "plan": saved_state.get("plan"),
                "output_paths": saved_state.get("output_paths") or {},
                "observability": {"path": str(session_dir / "agent-observability.jsonl")},
            }
            _copy_outputs(partial, turn_dir, session_dir)
        except Exception:
            turn_dir = output_base
        payload = {
            "state": "error", "error": run_error,
            "session_id": session_id,
            "dsh_session_id": dsh_session_id,
            "reference_image_count": len(image_paths),
            "cli_progress_events": progress_events,
            "output_paths": partial.get("output_paths", {}) if "partial" in locals() else {},
            "run_record": str(session_dir / "cad-state.json"),
            "observability": {"path": str(session_dir / "agent-observability.jsonl")},
            "cli_output_dir": str(turn_dir), "cli_exit_code": 3,
            "cli_outcome": "error",
        }
        try:
            payload = _redact(payload, secrets)
            turn_dir.mkdir(parents=True, exist_ok=True)
            result_path = turn_dir / "result.json"
            payload["cli_result_path"] = str(result_path.resolve())
            result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        except OSError:
            pass
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        sys.stdout.flush()
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
