# -*- coding: utf-8 -*-
"""Cold-start K2.7 AgentLoop trial; evidence only under artifacts/agentloop."""
from __future__ import annotations
import copy, json, os, re, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(r"C:\Users\lycohana\.codex\worktrees\agentloop-refactor\AI-_-")
CONFIG_PATH = Path(r"C:\Users\lycohana\Desktop\code\AI-_-\config.json")
IMAGE_PATH = Path(r"C:\Users\lycohana\.codex\attachments\0e0f3409-8877-4d84-a9d4-32a5bce2b544\image-1.png")
OUT = Path(__file__).resolve().parent
PROMPT = "复刻"
LOCK = threading.RLock()
PHASE = "startup"
T0 = time.monotonic()
REASON_CHARS = REASON_EVENTS = 0
SECRETS = []

def stamp():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")

def find_secrets(v):
    if isinstance(v, dict):
        found = []
        for k, x in v.items():
            if any(s in str(k).lower() for s in ("api_key", "token", "secret", "password", "authorization")) and isinstance(x, str) and x:
                found.append(x)
            found.extend(find_secrets(x))
        return found
    if isinstance(v, list):
        return sum((find_secrets(x) for x in v), [])
    return []

def redact(s):
    s = str(s)
    for secret in SECRETS:
        s = s.replace(secret, "[REDACTED]") if secret else s
    s = re.sub(r"(?i)\b(?:sk|sf)-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", s)
    s = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{16,}", r"\1[REDACTED]", s)
    return s

def safe(v, depth=0):
    if depth > 5: return "[depth-limited]"
    if isinstance(v, dict): return {str(k): safe(x, depth+1) for k,x in v.items()}
    if isinstance(v, list): return [safe(x, depth+1) for x in v[:80]]
    if isinstance(v, str): return redact(v[:3000])
    if v is None or isinstance(v, (int,float,bool)): return v
    return redact(v)

def jsonl(name, obj):
    p = OUT / name
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(safe(obj), ensure_ascii=False, separators=(",",":")) + "\n")
        f.flush()
        os.fsync(f.fileno())

def progress(message):
    global PHASE, REASON_CHARS, REASON_EVENTS
    msg = redact(message)
    with LOCK:
        ts = stamp()
        if msg.startswith("模型等待中："):
            PHASE = msg.split("：",1)[1].split(" · ",1)[0]
        elif msg.startswith(("准备 AgentLoop", "AgentLoop 第")):
            PHASE = msg[:180]
        phase = PHASE
        elapsed = round(time.monotonic()-T0, 3)
        jsonl("progress.jsonl", {"timestamp":ts,"elapsed_seconds":elapsed,"phase":phase,"message":msg})
        if msg.startswith("模型思考链增量："):
            delta = msg.split("：",1)[1]
            REASON_CHARS += len(delta); REASON_EVENTS += 1
            jsonl("reasoning.jsonl", {"timestamp":ts,"elapsed_seconds":elapsed,"phase":phase,
                                      "event_index":REASON_EVENTS,"delta_chars":len(delta),"delta":delta})
            if REASON_EVENTS == 1 or REASON_EVENTS % 40 == 0:
                print(f"[{ts}] reasoning event={REASON_EVENTS}, chars={REASON_CHARS}, phase={phase}", flush=True)
        elif any(k in msg for k in ("模型等待中：","Agent 决策摘要：","工具完成：","达到总时限","已停止","超时","失败","已完成")):
            print(f"[{ts}] {msg[:500]}", flush=True)

def preview(plan, data, stage):
    fs = plan.get("features",[]) if isinstance(plan,dict) else []
    items = [{"id":f.get("id"),"kind":f.get("kind"),"operation":f.get("operation"),
              "parameters":f.get("parameters"),"sources":f.get("sources")}
             for f in fs if isinstance(f,dict)] if isinstance(fs,list) else []
    row = {"timestamp":stamp(),"elapsed_seconds":round(time.monotonic()-T0,3),"stage":stage,
           "state":data.get("state") if isinstance(data,dict) else None,
           "geometry_fingerprint":data.get("geometry_fingerprint") if isinstance(data,dict) else None,
           "dimensions":data.get("dimensions") if isinstance(data,dict) else None,
           "preview_image":data.get("preview_image") if isinstance(data,dict) else None,
           "preview_image_views":data.get("preview_image_views") if isinstance(data,dict) else None,
           "feature_count":len(items),"features":items}
    jsonl("preview_timeline.jsonl",row)
    print(f"[{row['timestamp']}] preview stage={stage} features={len(items)} dimensions={row['dimensions']}",flush=True)

def args_summary(args):
    if not isinstance(args,dict): return safe(args)
    out={}
    for k,v in args.items():
        if k=="reference_reading" and isinstance(v,dict):
            out[k]={"feature_groups":v.get("feature_groups"),
                    "visible_regions":[{"image":r.get("image"),"id":r.get("id"),
                      "surface_state":r.get("surface_state"),
                      "features":r.get("features")} for r in v.get("visible_regions",[]) if isinstance(r,dict)],
                    "dimensionless_constraints":v.get("dimensionless_constraints"),
                    "uncertainties":v.get("uncertainties")}
        else: out[k]=safe(v)
    return out

def result_summary(v):
    if not isinstance(v,dict): return {"value":safe(v)}
    keys=("state","error","revision","geometry_fingerprint","feature_count","validation","dimensions",
          "preview_image","preview_image_views","findings","finding_ids","blockers","next_step_hint",
          "reason","questions","updated_feature_ids","removed_feature_ids")
    return {k:safe(v[k]) for k in keys if k in v}

def finalize(session_dir,result,error):
    record_path=Path(session_dir)/"run.json" if session_dir else None
    try: record=json.loads(record_path.read_text(encoding="utf-8")) if record_path and record_path.exists() else {}
    except Exception as e: record={"record_read_error":redact(e)}
    calls=record.get("model_calls",[])
    for i,c in enumerate(calls,1):
        jsonl("model_calls.jsonl",{"index":i,"phase":c.get("phase"),"model":c.get("model"),
          "api_protocol":c.get("api_protocol"),"reasoning_effort":c.get("reasoning_effort"),
          "thinking_budget":c.get("thinking_budget"),"thinking":c.get("thinking"),
          "duration_seconds":c.get("duration_seconds"),"request_timeout_seconds":c.get("request_timeout_seconds"),
          "image_parts_in_request":c.get("image_parts_in_request"),"function_tool_names":c.get("function_tool_names"),
          "tool_call_names":c.get("tool_call_names"),"tool_argument_lengths":c.get("tool_argument_lengths"),
          "reasoning_chars":c.get("reasoning_chars"),"answer_chars":c.get("answer_chars"),
          "usage":c.get("usage"),"finish_reason":c.get("finish_reason"),"error":c.get("error")})
    pending=[]
    for line in (OUT/"progress.jsonl").read_text(encoding="utf-8").splitlines() if (OUT/"progress.jsonl").exists() else []:
        try: e=json.loads(line)
        except Exception: continue
        msg=e.get("message","")
        if msg.startswith("Agent 决策摘要："):
            pending.append({"timestamp":e.get("timestamp"),"elapsed_seconds":e.get("elapsed_seconds"),
                            "phase":e.get("phase"),"summary":msg.split("：",1)[1]})
        elif msg.startswith("工具完成：") and pending:
            pending[-1]["completion"]=msg.split("：",1)[1]
    traces=record.get("tool_trace",[])
    for i,t in enumerate(traces,1):
        call=pending[i-1] if i<=len(pending) else {}
        jsonl("tool_trace_summary.jsonl",{"index":i,"timestamp":call.get("timestamp"),
          "elapsed_seconds":call.get("elapsed_seconds"),"phase":t.get("phase"),"tool":t.get("tool"),
          "decision_summary":call.get("summary"),"arguments":args_summary(t.get("arguments",{})),
          "result_summary":t.get("result_summary"),"result":result_summary(t.get("result"))})
    report={"trial_started_at":STARTED_AT,"trial_finished_at":stamp(),
      "duration_seconds":round(time.monotonic()-T0,3),"prompt":PROMPT,"input_image_count":1,
      "input_image_name":IMAGE_PATH.name,"model":"moonshotai/Kimi-K2.7-Code",
      "provider_host":"api.siliconflow.cn","reasoning_effort_override":"auto",
      "config_writeback":False,"run_record":str(record_path) if record_path else None,
      "state":record.get("state"),"model_call_count":len(calls),"tool_call_count":len(traces),
      "preview_count":sum(1 for _ in (OUT/"preview_timeline.jsonl").open(encoding="utf-8")) if (OUT/"preview_timeline.jsonl").exists() else 0,
      "usage":record.get("counts",{}).get("llm_usage"),"reply":result.get("reply") if isinstance(result,dict) else None,
      "error":error,"reasoning_delta_events":REASON_EVENTS,"reasoning_delta_chars":REASON_CHARS}
    jsonl("trial_report.jsonl",report)
    if record_path and record_path.parent.exists():
        for name in ("progress.jsonl","reasoning.jsonl","preview_timeline.jsonl","model_calls.jsonl",
                     "tool_trace_summary.jsonl","trial_report.jsonl"):
            src=OUT/name
            if src.exists(): (record_path.parent/name).write_bytes(src.read_bytes())
        if isinstance(result,dict):
            (record_path.parent/"result.json").write_text(json.dumps(safe(result),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    # Scrub exact secret values from all new textual artifacts; never print values.
    changed=[]
    for p in OUT.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in {".json",".jsonl",".txt",".md",".log"}: continue
        try: old=p.read_text(encoding="utf-8")
        except Exception: continue
        new=old
        for secret in SECRETS:
            if secret: new=new.replace(secret,"[REDACTED]")
        if new!=old: p.write_text(new,encoding="utf-8"); changed.append(str(p.relative_to(OUT)))
    return record,changed

if not CONFIG_PATH.is_file() or not IMAGE_PATH.is_file():
    raise SystemExit("Existing config or reference image is missing; no request issued.")
with CONFIG_PATH.open("r",encoding="utf-8") as f: cfg=copy.deepcopy(json.load(f))
SECRETS=find_secrets(cfg)
llm=cfg.get("llm") if isinstance(cfg.get("llm"),dict) else None
if not llm or llm.get("model")!="moonshotai/Kimi-K2.7-Code" or not llm.get("api_key"):
    raise SystemExit("Existing active model/key is not the expected K2.7 setup; no request issued.")
if "thinking" in llm:
    raise SystemExit("Explicit thinking override would take precedence; no request issued.")
llm["reasoning_effort"]="auto"
if not llm.get("base_url"): raise SystemExit("Existing endpoint is missing; no request issued.")
STARTED_AT=stamp()
(OUT/"trial_setup.json").write_text(json.dumps({"created_at":STARTED_AT,"prompt":PROMPT,
 "input_image":IMAGE_PATH.name,"input_image_count":1,"model":llm.get("model"),
 "provider_host":urlsplit(str(llm.get("base_url"))).hostname,"api_key_present":True,
 "reasoning_effort_override":"auto","config_writeback":False},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
sys.path.insert(0,str(ROOT))
from ai_modeler.agent import llm as llm_module
import urllib.request
_original_urlopen = urllib.request.urlopen
def _capture_urlopen(request, *args, **kwargs):
    url = getattr(request, "full_url", "")
    raw = getattr(request, "data", None)
    if isinstance(raw, bytes) and urlsplit(str(url)).path.rstrip("/").endswith("/chat/completions"):
        try:
            payload = json.loads(raw.decode("utf-8"))
            image_count = 0
            for message in payload.get("messages", []):
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, list):
                    image_count += sum(1 for part in content
                        if isinstance(part, dict) and part.get("type") == "image_url")
            names = []
            for tool in payload.get("tools", []):
                fn = tool.get("function") if isinstance(tool, dict) else None
                if isinstance(fn, dict) and fn.get("name"):
                    names.append(fn["name"])
            jsonl("wire_requests.jsonl", {
                "timestamp": stamp(),
                "provider_host": urlsplit(str(url)).hostname,
                "model": payload.get("model"),
                "stream": payload.get("stream"),
                "max_tokens": payload.get("max_tokens"),
                "enable_thinking": payload.get("enable_thinking"),
                "thinking_budget": payload.get("thinking_budget"),
                "reasoning_effort_field": payload.get("reasoning_effort"),
                "tool_choice": payload.get("tool_choice"),
                "tool_names": names,
                "image_parts": image_count,
                "authorization_header_present": bool(request.get_header("Authorization")),
            })
        except Exception as capture_error:
            jsonl("wire_requests.jsonl", {"timestamp":stamp(),
                "capture_error_type":type(capture_error).__name__})
    return _original_urlopen(request, *args, **kwargs)
urllib.request.urlopen = _capture_urlopen
from ai_modeler.agent.agentloop import run_agent_loop
before={p.resolve() for p in OUT.iterdir() if p.is_dir()}
print(f"Starting actual AgentLoop: model={llm.get('model')}, provider=SiliconFlow, one image, prompt={PROMPT!r}, effort=auto",flush=True)
result=None; caught=None; error=None
try:
    result=run_agent_loop(PROMPT,image_paths=[str(IMAGE_PATH)],cfg=cfg,progress=progress,
                          output_dir=str(OUT),on_preview=preview)
    if isinstance(result,dict):
        (OUT/"result.json").write_text(json.dumps(safe(result),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
except BaseException as exc:
    caught=exc; error=redact(f"{type(exc).__name__}: {exc}")
    (OUT/"exception.txt").write_text(error+"\n",encoding="utf-8")
    print("AgentLoop raised "+error[:500],flush=True)
finally:
    new=[p for p in OUT.iterdir() if p.is_dir() and p.resolve() not in before]
    session=Path(result["run_record"]).parent if isinstance(result,dict) and result.get("run_record") else None
    if session is None and new:
        candidates=[p for p in new if (p/"run.json").exists()]
        if candidates: session=max(candidates,key=lambda p:p.stat().st_mtime)
    record,changed=finalize(session,result,error)
    print(json.dumps({"finished_at":stamp(),"duration_seconds":round(time.monotonic()-T0,3),
      "session_dir":str(session) if session else None,"state":record.get("state"),
      "reasoning_events":REASON_EVENTS,"reasoning_chars":REASON_CHARS,
      "tool_calls":len(record.get("tool_trace",[])),"model_calls":len(record.get("model_calls",[])),
      "sensitive_artifact_files_scrubbed":changed},ensure_ascii=False),flush=True)
