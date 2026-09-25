# -*- coding: utf-8 -*-
r"""大模型适配层 —— 同时支持线上 API 和本地 llama.cpp / Ollama。

只用标准库, 不依赖第三方包。

本地模式说明:
  程序会在需要时自动拉起 local_ai\llamacpp\llama-server.exe 加载 DeepSeek,
  它提供 OpenAI 兼容接口 (http://127.0.0.1:8090/v1), 因此网络层代码完全一样。
"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import json
import base64
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

ROOT = str(PROJECT_ROOT)
AI_DIR = os.path.join(ROOT, 'local_ai')
LLAMA_DIR = os.path.join(AI_DIR, 'llamacpp')
LLAMA_EXE = os.path.join(LLAMA_DIR, 'llama-server.exe')
GGUF_DIR = os.path.join(AI_DIR, 'gguf')
LOCAL_PORT = 8090

SYSTEM_PROMPT = '''你是一个 CAD 特征解析器。把用户的中文建模需求转换成 JSON 特征数组。

严格输出 JSON, 格式: {"features": [ ... ]}
每个特征形如:
{"kind": "box",      "params": {"length":100,"width":60,"height":20,"corner":0,"corner_mode":"fillet","center":[0,0,0],"base":true,"axis":"Z"}, "op": "add"}
{"kind": "cylinder", "params": {"radius":30,"height":120,"radius2":null,"center":[0,0,0],"base":true,"axis":"Z","segments":48}, "op": "add"}
{"kind": "sphere",   "params": {"radius":40,"center":[0,0,0]}, "op": "add"}
{"kind": "gear",     "params": {"teeth":24,"module":3,"height":20}, "op": "add"}
{"kind": "hole",     "params": {"diameter":10,"through":true,"pos":"center","axis":"Z"}, "op": "cut"}
    hole 的 pos 可取: "center"(中心一个) | "corners"(四角各一, 可给 margin) | "pcd"(均布, 需 count 与 pcd) | "coord"(需 x, y)
{"kind": "pocket",   "params": {"length":40,"width":20,"depth":5,"axis":"Z"}, "op": "cut"}
{"kind": "prism",    "params": {"profile":[[-50,-10],[50,-10],[50,10],[-50,10]],"height":20,"axis":"Z"}, "op": "add"}
{"kind": "pattern",  "params": {"mode":"circular","count":24,"axis":"Z","angle":360,"full":true,"center":[0,0,0],"compound":true}, "op": "add"}
{"kind": "pattern",  "params": {"mode":"linear","count":4,"step":[40,0,0]}, "op": "add"}
{"kind": "loft",     "params": {"span":80,"root_chord":40,"tip_chord":22,"thickness":0.12,"twist":18,"sections":8}, "op": "add"}

规则:
- 单位一律换算为毫米。
- 长方体以底面中心为基准 (base=true, center 为底面中心)。
- 圆柱/球默认以原点为中心对称 (base=false 时 center 为几何中心)。
- 除上述字段外不要输出任何多余文字或解释, 不要使用 markdown 代码块。
- 如果无法识别用户的描述, 输出 {"features": []}。
'''


# --------------------------------------------------------------------------
# 本地模型服务
# --------------------------------------------------------------------------

def _find_gguf():
    if not os.path.isdir(GGUF_DIR):
        return None
    best, size = None, 0
    for fn in os.listdir(GGUF_DIR):
        if fn.lower().endswith('.gguf'):
            p = os.path.join(GGUF_DIR, fn)
            s = os.path.getsize(p)
            if s > size:
                best, size = p, s
    return best


def local_available(cfg=None):
    """本地引擎是否已装好。"""
    return os.path.isfile(LLAMA_EXE) and _find_gguf() is not None


def local_url():
    return 'http://127.0.0.1:%d/v1' % LOCAL_PORT


def _ping(url, timeout=2):
    try:
        with urllib.request.urlopen(url + '/models', timeout=timeout) as r:
            json.load(r)
        return True
    except Exception:
        return False


def ensure_local_server(cfg=None, wait=90):
    """需要时把本地 llama-server 拉起来。返回 (是否就绪, 说明)。"""
    base = ((cfg or {}).get('base_url') or '')
    if '127.0.0.1' not in base and 'localhost' not in base:
        return True, '使用线上接口'
    if _ping(local_url()):
        return True, '本地服务已在运行'
    if not local_available():
        return False, '本地模型未安装'
    gguf = _find_gguf()
    flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            [LLAMA_EXE, '-m', gguf, '--host', '127.0.0.1', '--port', str(LOCAL_PORT),
             '-ngl', '99', '-c', '4096', '-t', str(max(2, (os.cpu_count() or 4) // 2)),
             '--no-webui'],
            cwd=LLAMA_DIR, creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return False, '启动本地服务失败: %s' % e
    for _ in range(int(wait * 2)):
        time.sleep(0.5)
        if _ping(local_url()):
            return True, '本地服务已启动'
    return False, '本地服务启动超时'


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

def _extract_json(text):
    """从模型输出里抠出 JSON —— 推理模型会在前面写一大堆思考过程。"""
    if not text:
        return None
    t = text.strip()
    fence = chr(96) * 3
    if fence in t:
        parts = t.split(fence)
        for p in parts:
            p = p.strip()
            if p.startswith('json'):
                p = p[4:].strip()
            if p.startswith('{'):
                t = p
                break
    i, j = t.find('{'), t.rfind('}')
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except Exception:
        try:
            return json.loads(t[i:j + 1].replace(',}', '}').replace(',]', ']'))
        except Exception:
            return None


def image_content_part(path: str, detail: str = 'high'):
    """Return an OpenAI-compatible image_url content block for a local image.

    The file is sent byte-for-byte: this function never resizes or re-encodes it,
    so the model sees the user's original pixels. `detail` is the only lever
    against a provider silently swapping in a low-resolution copy, so it
    defaults to 'high'.
    """
    path = os.path.abspath(os.fspath(path))
    if not os.path.isfile(path):
        raise RuntimeError('找不到参考图片: %s' % path)
    mime, _encoding = mimetypes.guess_type(path)
    if mime not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}:
        raise RuntimeError('不支持的图片格式: %s（请使用 PNG、JPG、WebP 或 GIF）'
                           % os.path.basename(path))
    size = os.path.getsize(path)
    if size > 20 * 1024 * 1024:
        raise RuntimeError('图片超过 20 MB: %s' % os.path.basename(path))
    with open(path, 'rb') as stream:
        encoded = base64.b64encode(stream.read()).decode('ascii')
    return {'type': 'image_url',
            'image_url': {'url': 'data:%s;base64,%s' % (mime, encoded),
                          'detail': detail}}


def user_content(text: str, image_paths=None):
    """Create multimodal user content while preserving an empty text prompt."""
    parts = [{'type': 'text', 'text': str(text or '')}]
    for path in image_paths or ():
        parts.append(image_content_part(path))
    return parts


def _is_deepseek_responses(base_url, model):
    try:
        parsed = urlsplit(str(base_url or ''))
    except ValueError:
        return False
    return ((parsed.hostname or '').lower() == 'api.deepseek.com'
            and str(model or '').lower() in {'deepseek-flash', 'deepseek-v4-pro'}
            and parsed.path.rstrip('/') in {'', '/v1'})


def _is_siliconflow_host(base_url):
    """SiliconFlow chat/completions speaks OpenAI but has native thinking fields."""
    try:
        host = (urlsplit(str(base_url or '')).hostname or '').lower()
    except ValueError:
        return False
    return 'siliconflow' in host


_SF_BUDGET_MAX_WORDS = {'max', 'xhigh', 'ultra'}


def _siliconflow_thinking_params(cfg):
    r"""Translate the app's thinking config into SiliconFlow's native fields.

    SiliconFlow /v1/chat/completions 只认 enable_thinking(bool) 与
    thinking_budget(128..32768)；OpenAI Responses 风格的 ``thinking`` 对象
    与 ``reasoning_effort`` 字符串会被它静默忽略（后者只对 DeepSeek-V4/GLM
    系列生效）。文档: https://docs.siliconflow.cn/docs/api/chat-completions-post

    档位映射（沿设置界面"none 不发送推理参数"的既有语义）:
      · thinking 键（布尔/字典，程序化配置存在时优先）:
          False / {'type':'disabled'} → enable_thinking=False（真正关闭）
          True  / {'type':'enabled'}  → enable_thinking=True（max 档加预算）
      · 否则按 reasoning_effort：
          max / xhigh / ultra → enable_thinking=True + thinking_budget=32768
          low / medium / high → enable_thinking=True
          none / auto / 空   → 不发送开关，保持服务端默认
            （Kimi-K2.7 等推理模型在 SiliconFlow 默认就会思考，
             思考链经流式增量实时显示；实测见修复说明）
    """
    thinking = (cfg or {}).get('thinking')
    effort = str((cfg or {}).get('reasoning_effort') or '').strip().lower()
    if isinstance(thinking, bool):
        if not thinking:
            return {'enable_thinking': False}
        params = {'enable_thinking': True}
        if effort in _SF_BUDGET_MAX_WORDS:
            params['thinking_budget'] = 32768
        return params
    if isinstance(thinking, dict):
        kind = str(thinking.get('type') or '').strip().lower()
        if kind in {'disabled', 'none', 'off', 'false'}:
            return {'enable_thinking': False}
        if kind in {'enabled', 'on', 'true'}:
            params = {'enable_thinking': True}
            if effort in _SF_BUDGET_MAX_WORDS:
                params['thinking_budget'] = 32768
            return params
    if effort in _SF_BUDGET_MAX_WORDS:
        return {'enable_thinking': True, 'thinking_budget': 32768}
    if effort in {'low', 'medium', 'high'}:
        return {'enable_thinking': True}
    return {}


def _responses_endpoint(base_url):
    parsed = urlsplit(str(base_url or '').rstrip('/'))
    return urlunsplit((parsed.scheme, parsed.netloc, '/responses', '', ''))


def _responses_content(role, content):
    if not isinstance(content, list):
        return content if isinstance(content, str) else str(content or '')
    parts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        kind = part.get('type')
        if kind in {'text', 'input_text', 'output_text'}:
            text = part.get('text')
            if text is not None:
                output_kind = 'output_text' if role == 'assistant' else 'input_text'
                parts.append({'type': output_kind, 'text': str(text)})
        elif kind == 'image_url':
            if role not in {'user', 'developer'}:
                raise RuntimeError(
                    'DeepSeek Responses API 只允许在 user/developer 消息中附图。')
            image = part.get('image_url') or {}
            url = image.get('url') if isinstance(image, dict) else image
            if not url:
                continue
            converted = {'type': 'input_image', 'image_url': url}
            if isinstance(image, dict) and image.get('detail'):
                converted['detail'] = image['detail']
            parts.append(converted)
        elif kind == 'input_image' and role in {'user', 'developer'}:
            parts.append(dict(part))
    return parts


def _chat_messages_to_responses(messages):
    """Translate the local Chat Completions transcript into Responses input items."""
    items = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get('role') or 'user')
        content = message.get('content')
        if role == 'tool':
            call_id = str(message.get('tool_call_id') or '').strip()
            if not call_id:
                raise RuntimeError('工具历史缺少 tool_call_id，无法转换为 Responses API。')
            items.append({
                'type': 'function_call_output',
                'call_id': call_id,
                'output': _responses_content('user', content),
            })
            continue

        if role == 'assistant':
            reasoning = message.get('reasoning_content')
            if reasoning:
                items.append({
                    'type': 'reasoning',
                    'content': [{'type': 'reasoning_text', 'text': str(reasoning)}],
                })
            tool_calls = message.get('tool_calls') or []
            if content not in (None, ''):
                items.append({
                    'role': 'assistant',
                    'content': _responses_content('assistant', content),
                })
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get('function') or {}
                call_id = str(call.get('id') or '').strip()
                name = str(function.get('name') or '').strip()
                arguments = function.get('arguments')
                if not call_id or not name:
                    raise RuntimeError('工具历史缺少 call_id/name，无法转换为 Responses API。')
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments or {}, ensure_ascii=False,
                                           separators=(',', ':'))
                items.append({
                    'type': 'function_call', 'call_id': call_id,
                    'name': name, 'arguments': arguments,
                })
            continue

        if role not in {'user', 'system', 'developer'}:
            role = 'user'
        items.append({'role': role, 'content': _responses_content(role, content)})
    return items


def _responses_tools(tools):
    result = []
    for tool in tools or []:
        if not isinstance(tool, dict) or tool.get('type') != 'function':
            continue
        function = tool.get('function') or {}
        name = function.get('name')
        if not name:
            continue
        item = {
            'type': 'function',
            'name': name,
            'parameters': function.get('parameters') or {'type': 'object', 'properties': {}},
        }
        for key in ('description', 'strict'):
            if key in function:
                item[key] = function[key]
        result.append(item)
    return result


def describe_stream_progress(event):
    """Describe stream activity without embedding reasoning text in a status label."""
    if not isinstance(event, dict):
        return '正在接收模型流式响应'
    kind = str(event.get('event') or '')
    reasoning_chars = int(event.get('reasoning_chars') or 0)
    output_chars = int(event.get('output_chars') or 0)
    argument_chars = int(event.get('tool_argument_chars') or 0)
    visible_output = str(event.get('visible_output') or '').strip()
    if kind == 'response.created':
        return '服务端已接收请求'
    if visible_output:
        return '模型正在思考；进展摘要：%s' % visible_output[-480:]
    if reasoning_chars:
        return '模型正在思考；推理流已接收 %s 字符' % reasoning_chars
    if output_chars:
        return '模型正在生成答复；已收到 %s 字符' % output_chars
    if argument_chars:
        return '模型正在形成工具调用；已收到 %s 字符的参数流' % argument_chars
    if kind.startswith('response.'):
        return '模型正在处理（%s）' % kind[len('response.'):]
    return '正在接收模型流式响应'


def emit_stream_progress(event, callback, detail=''):
    """Forward stream status and reasoner thinking-chain deltas separately."""
    if not callable(callback):
        return
    status = describe_stream_progress(event)
    if detail:
        status = '%s · %s' % (detail, status)
    callback('模型流式状态：' + status)
    if isinstance(event, dict):
        reasoning_delta = event.get('reasoning_delta')
        if isinstance(reasoning_delta, str) and reasoning_delta:
            callback('模型思考链增量：' + reasoning_delta)


def _read_responses_stream(response, progress=None):
    event_name = ''
    data_lines = []
    final_response = None
    final_event = ''
    reasoning_chars = 0
    pending_reasoning_delta = ''
    output_chars = 0
    visible_output = ''
    tool_argument_chars = 0
    last_progress = 0.0
    started = time.monotonic()

    def emit(kind, force=False):
        nonlocal last_progress, pending_reasoning_delta
        now = time.monotonic()
        if not callable(progress) or (not force and now - last_progress < 0.8):
            return
        last_progress = now
        reasoning_delta = pending_reasoning_delta
        try:
            progress({
                'event': kind,
                'reasoning_chars': reasoning_chars,
                'reasoning_delta': reasoning_delta,
                'output_chars': output_chars,
                'visible_output': visible_output[-1200:],
                'tool_argument_chars': tool_argument_chars,
                'elapsed_seconds': round(now - started, 1),
            })
            pending_reasoning_delta = ''
        except Exception:
            pass

    def consume_event():
        nonlocal event_name, data_lines, final_response, final_event
        nonlocal reasoning_chars, pending_reasoning_delta
        nonlocal output_chars, visible_output, tool_argument_chars
        if not data_lines:
            event_name = ''
            return
        raw = '\n'.join(data_lines).strip()
        data_lines = []
        kind = event_name
        event_name = ''
        if not raw or raw == '[DONE]':
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError('DeepSeek Responses SSE 事件不是有效 JSON: %s' % exc) from exc
        if not isinstance(event, dict):
            return
        kind = str(event.get('type') or kind or '')
        delta = event.get('delta')
        if kind == 'response.reasoning_text.delta' and isinstance(delta, str):
            reasoning_chars += len(delta)
            pending_reasoning_delta += delta
        elif kind == 'response.output_text.delta' and isinstance(delta, str):
            output_chars += len(delta)
            visible_output += delta
        elif kind == 'response.function_call_arguments.delta' and isinstance(delta, str):
            tool_argument_chars += len(delta)
        if kind in {'response.completed', 'response.incomplete', 'response.failed'}:
            final_event = kind
            final_response = event.get('response') or event
        emit(kind, force=kind in {
            'response.created', 'response.completed', 'response.incomplete', 'response.failed'})

    for raw_line in response:
        line = raw_line.decode('utf-8', errors='replace').rstrip('\r\n')
        if not line:
            consume_event()
        elif line.startswith(':'):
            continue
        elif line.startswith('event:'):
            event_name = line[6:].strip()
        elif line.startswith('data:'):
            data_lines.append(line[5:].lstrip())
    consume_event()
    if final_response is None:
        raise RuntimeError('DeepSeek Responses SSE 提前结束，未收到 response.completed/incomplete/failed。')
    if final_event == 'response.failed':
        error = final_response.get('error') or {}
        if isinstance(error, dict):
            message = error.get('message') or error.get('code') or str(error)
        else:
            message = str(error)
        raise RuntimeError('DeepSeek Responses API 生成失败: %s' % message)
    return final_response, final_event


def _responses_to_chat_message(response, final_event, payload):
    status = str(response.get('status') or '')
    output = response.get('output') or []
    texts = []
    reasoning = []
    tool_calls = []
    for item in output:
        if not isinstance(item, dict):
            continue
        kind = item.get('type')
        content = item.get('content') or []
        if kind == 'reasoning':
            for part in content:
                if isinstance(part, dict) and part.get('type') == 'reasoning_text':
                    reasoning.append(str(part.get('text') or ''))
        elif kind == 'message':
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get('type') == 'output_text':
                    texts.append(str(part.get('text') or ''))
                elif part.get('type') == 'refusal':
                    texts.append(str(part.get('refusal') or ''))
        elif kind == 'function_call':
            tool_calls.append({
                'id': str(item.get('call_id') or item.get('id') or ''),
                'type': 'function',
                'function': {
                    'name': str(item.get('name') or ''),
                    'arguments': (item.get('arguments') if isinstance(item.get('arguments'), str)
                                  else json.dumps(item.get('arguments') or {}, ensure_ascii=False)),
                },
            })
    message = {'role': 'assistant', 'content': ''.join(texts)}
    if reasoning:
        message['reasoning_content'] = ''.join(reasoning)
    if tool_calls:
        message['tool_calls'] = tool_calls
    if final_event == 'response.incomplete' or status == 'incomplete':
        finish_reason = 'length'
    elif tool_calls:
        finish_reason = 'tool_calls'
    else:
        finish_reason = 'stop'
    message['_finish_reason'] = finish_reason
    effort = ((payload.get('reasoning') or {}).get('effort')
              if isinstance(payload.get('reasoning'), dict) else None)
    message['_request_thinking'] = {'type': 'disabled' if effort == 'none' else 'enabled'}
    message['_request_reasoning_effort'] = effort
    message['_api_protocol'] = 'responses'
    message['_response_id'] = response.get('id')
    usage = response.get('usage') or {}
    if isinstance(usage, dict):
        mapped = {}
        for target, source in (('prompt_tokens', 'input_tokens'),
                               ('completion_tokens', 'output_tokens'),
                               ('total_tokens', 'total_tokens')):
            value = usage.get(source)
            if isinstance(value, (int, float)):
                mapped[target] = value
        details = usage.get('output_tokens_details') or {}
        if isinstance(details, dict) and isinstance(details.get('reasoning_tokens'), (int, float)):
            mapped['reasoning_tokens'] = details['reasoning_tokens']
        message['_usage'] = mapped
    return message


def _deepseek_responses_completion(messages, cfg, tools, timeout, max_tokens,
                                   temperature, progress=None):
    model = cfg.get('model') or 'deepseek-flash'
    base = (cfg.get('base_url') or '').rstrip('/')
    effort = str(cfg.get('reasoning_effort') or '').strip().lower()
    thinking = cfg.get('thinking')
    if isinstance(thinking, bool):
        thinking = {'type': 'enabled' if thinking else 'disabled'}
    if isinstance(thinking, dict):
        if thinking.get('type') == 'disabled':
            effort = 'none'
        elif thinking.get('type') == 'enabled' and not effort:
            effort = 'high'
    effort = {'auto': 'high', 'medium': 'high', 'xhigh': 'high', 'ultra': 'max'}.get(
        effort, effort or 'high')
    if effort not in {'none', 'low', 'high', 'max'}:
        raise RuntimeError('DeepSeek Responses 思考强度不支持: %s' % effort)
    payload = {
        'model': model,
        'input': _chat_messages_to_responses(messages),
        'reasoning': {'effort': effort},
        'max_output_tokens': int(max_tokens),
        'temperature': temperature,
        'stream': True,
    }
    converted_tools = _responses_tools(tools)
    if converted_tools:
        payload['tools'] = converted_tools
        payload['tool_choice'] = 'auto'
    request = urllib.request.Request(
        _responses_endpoint(base),
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
            'Authorization': 'Bearer ' + (cfg.get('api_key') or 'sk-local'),
        },
        method='POST')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result, final_event = _read_responses_stream(response, progress=progress)
    except urllib.error.HTTPError as exc:
        try:
            reason = exc.read().decode('utf-8', errors='replace')[:2000]
        except Exception:
            reason = str(exc)
        raise RuntimeError('DeepSeek Responses API HTTP %s: %s' % (exc.code, reason)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError('DeepSeek Responses API 流式连接失败: %s' % exc) from exc
    return _responses_to_chat_message(result, final_event, payload)


def _read_chat_completions_stream(response, progress=None):
    r"""Consume an OpenAI-compatible chat/completions SSE stream.

    Streaming is what makes thinking chains visible for reasoning models on
    OpenAI-compatible providers (SiliconFlow Kimi, DeepSeek, vLLM, llama.cpp,
    ...): the chain arrives as ``delta.reasoning_content`` fragments. This
    reader aggregates the full reply (content, reasoning, tool_calls, usage)
    while forwarding live reasoning deltas through the shared progress
    channel, mirroring what the DeepSeek Responses path already does.
    """
    content_parts = []
    reasoning_parts = []
    tools_by_index = {}
    tool_order = []
    finish_reason = None
    usage = None
    response_id = None
    chunk_count = 0
    reasoning_chars = 0
    output_chars = 0
    tool_argument_chars = 0
    pending_reasoning_delta = ''
    visible_output = ''
    last_progress = 0.0
    started = time.monotonic()
    data_lines = []
    body_lines = []

    def emit(force=False):
        nonlocal last_progress, pending_reasoning_delta
        now = time.monotonic()
        if not callable(progress) or (not force and now - last_progress < 0.8):
            return
        last_progress = now
        reasoning_delta = pending_reasoning_delta
        try:
            progress({
                'event': 'chat.completion.delta',
                'reasoning_chars': reasoning_chars,
                'reasoning_delta': reasoning_delta,
                'output_chars': output_chars,
                'visible_output': visible_output[-1200:],
                'tool_argument_chars': tool_argument_chars,
                'elapsed_seconds': round(now - started, 1),
            })
            pending_reasoning_delta = ''
        except Exception:
            pass

    def consume_chunk():
        nonlocal finish_reason, usage, response_id, chunk_count
        nonlocal reasoning_chars, output_chars, tool_argument_chars
        nonlocal pending_reasoning_delta, visible_output
        if not data_lines:
            return
        raw = '\n'.join(data_lines).strip()
        data_lines.clear()
        if not raw or raw == '[DONE]':
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                '兼容接口流式分片不是有效 JSON: %s' % exc) from exc
        if not isinstance(event, dict):
            return
        chunk_count += 1
        if isinstance(event.get('id'), str) and not response_id:
            response_id = event['id']
        if isinstance(event.get('usage'), dict) and event.get('usage'):
            usage = event['usage']       # partial per-chunk; keep the latest
        choices = event.get('choices') or []
        choice0 = choices[0] if choices else None
        if not isinstance(choice0, dict):
            return
        finish = choice0.get('finish_reason')
        if isinstance(finish, str) and finish:
            finish_reason = finish
        delta = choice0.get('delta')
        if not isinstance(delta, dict):
            return
        reasoning_delta = delta.get('reasoning_content')
        if isinstance(reasoning_delta, str) and reasoning_delta:
            reasoning_parts.append(reasoning_delta)
            reasoning_chars += len(reasoning_delta)
            pending_reasoning_delta += reasoning_delta
        content_delta = delta.get('content')
        if isinstance(content_delta, str) and content_delta:
            content_parts.append(content_delta)
            output_chars += len(content_delta)
            visible_output += content_delta
        for call_delta in delta.get('tool_calls') or []:
            if not isinstance(call_delta, dict):
                continue
            try:
                index = int(call_delta.get('index') or 0)
            except (TypeError, ValueError):
                index = 0
            slot = tools_by_index.get(index)
            if slot is None:
                slot = {'id': '', 'type': 'function', 'name': '', 'arguments': ''}
                tools_by_index[index] = slot
                tool_order.append(index)
            call_id = call_delta.get('id')
            if isinstance(call_id, str) and call_id:
                slot['id'] = call_id
            call_type = call_delta.get('type')
            if isinstance(call_type, str) and call_type:
                slot['type'] = call_type
            function = call_delta.get('function')
            if isinstance(function, dict):
                name = function.get('name')
                if isinstance(name, str) and name:
                    slot['name'] = name
                arguments = function.get('arguments')
                if isinstance(arguments, str) and arguments:
                    slot['arguments'] += arguments
                    tool_argument_chars += len(arguments)
        emit()

    for raw_line in response:
        line = raw_line.decode('utf-8', errors='replace').rstrip('\r\n')
        body_lines.append(line)
        if not line:
            consume_chunk()
        elif line.startswith(':'):
            continue
        elif line.startswith('data:'):
            data_lines.append(line[5:].lstrip())
    consume_chunk()
    emit(force=True)
    if chunk_count == 0 and body_lines:
        # 个别兼容实现会忽略 stream=true 直接回完整 JSON；保持该路径可用。
        try:
            plain = json.loads('\n'.join(body_lines).strip())
        except json.JSONDecodeError:
            plain = None
        if (isinstance(plain, dict) and isinstance(plain.get('choices'), list)
                and plain['choices'] and isinstance(plain['choices'][0], dict)):
            choice0 = plain['choices'][0]
            message = choice0.get('message')
            if isinstance(message, dict):
                slots = []
                for call in message.get('tool_calls') or []:
                    if not isinstance(call, dict):
                        continue
                    function = call.get('function') or {}
                    slots.append({
                        'id': str(call.get('id') or ''),
                        'type': str(call.get('type') or 'function'),
                        'name': str(function.get('name') or ''),
                        'arguments': str(function.get('arguments') or ''),
                    })
                return {
                    'content': str(message.get('content') or ''),
                    'reasoning': str(message.get('reasoning_content') or ''),
                    'finish_reason': choice0.get('finish_reason') or 'stop',
                    'usage': plain.get('usage') if isinstance(
                        plain.get('usage'), dict) else None,
                    'response_id': plain.get('id') if isinstance(
                        plain.get('id'), str) else None,
                    'tool_calls': slots,
                }
    if chunk_count == 0:
        raise RuntimeError('兼容接口流式响应为空：未收到任何 chat.completion.chunk。')
    if finish_reason is None and not content_parts and not tool_order:
        raise RuntimeError('兼容接口流式响应提前结束，未收到任何增量内容。')
    return {
        'content': ''.join(content_parts),
        'reasoning': ''.join(reasoning_parts),
        'finish_reason': finish_reason or 'stop',
        'usage': usage,
        'response_id': response_id,
        'tool_calls': [
            tools_by_index[index] for index in tool_order
        ],
    }


def _chat_stream_to_message(stream, payload):
    """Assemble a normalized assistant message from a chat/completions stream."""
    tool_calls = []
    for index, slot in enumerate(stream.get('tool_calls') or []):
        call_id = slot.get('id') or ('chatcall-%d' % index)
        tool_calls.append({
            'id': call_id,
            'type': slot.get('type') or 'function',
            'function': {
                'name': slot.get('name') or '',
                'arguments': slot.get('arguments') or '',
            },
        })
    message = {'role': 'assistant', 'content': stream.get('content') or ''}
    if stream.get('reasoning'):
        message['reasoning_content'] = stream['reasoning']
    if tool_calls:
        message['tool_calls'] = tool_calls
    message['_finish_reason'] = stream.get('finish_reason') or 'stop'
    if tool_calls and message['_finish_reason'] not in {'tool_calls', 'length'}:
        message['_finish_reason'] = 'tool_calls'
    thinking_sent = payload.get('thinking')
    if not thinking_sent and 'enable_thinking' in payload:
        thinking_sent = {'type': 'enabled' if payload.get('enable_thinking')
                         else 'disabled'}
    message['_request_thinking'] = thinking_sent
    message['_request_reasoning_effort'] = payload.get('reasoning_effort')
    message['_api_protocol'] = 'chat_completions_stream'
    message['_response_id'] = stream.get('response_id')
    usage = stream.get('usage') or {}
    if isinstance(usage, dict):
        mapped = {}
        for source in ('prompt_tokens', 'completion_tokens', 'total_tokens',
                        'input_tokens', 'output_tokens'):
            value = usage.get(source)
            if isinstance(value, (int, float)):
                mapped[source] = value
        completion_details = usage.get('completion_tokens_details')
        if (isinstance(completion_details, dict)
                and isinstance(completion_details.get('reasoning_tokens'), (int, float))):
            mapped['reasoning_tokens'] = completion_details['reasoning_tokens']
        if mapped:
            message['_usage'] = mapped
    return message


def chat_completion(messages, cfg: dict, tools=None, timeout=180,
                    max_tokens=4096, temperature=0.1, progress=None):
    """Call a configured model and normalize its reply for the local AgentLoop.

    Official DeepSeek endpoints use the Responses API with SSE streaming;
    other OpenAI-compatible providers keep the Chat Completions adapter.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    base = (cfg.get('base_url') or '').rstrip('/')
    model = cfg.get('model') or 'deepseek'
    key = cfg.get('api_key') or 'sk-local'
    if not base:
        raise RuntimeError('未配置大模型接口地址')

    ok, detail = ensure_local_server(cfg)
    if not ok:
        raise RuntimeError(detail)

    if _is_deepseek_responses(base, model):
        return _deepseek_responses_completion(
            messages, cfg, tools, timeout, max_tokens, temperature,
            progress=progress)

    payload = {
        'model': model,
        'temperature': temperature,
        'max_tokens': max_tokens,
        'messages': messages,
        # 流式接收：思考链(reasoning) 由服务端以 delta.reasoning_content 分片
        # 下发，非流式请求拿不到实时增量（SiliconFlow 等推理模型）。
        'stream': True,
    }
    # Compatibility path for non-DeepSeek Responses providers. Official
    # DeepSeek requests have already been routed through the Responses API.
    if _is_siliconflow_host(base):
        # SiliconFlow 只认 enable_thinking / thinking_budget；
        # thinking 对象与 reasoning_effort 对它无效。
        payload.update(_siliconflow_thinking_params(cfg))
    elif 'thinking' in cfg:
        thinking = cfg.get('thinking')
        if isinstance(thinking, bool):
            thinking = {'type': 'enabled' if thinking else 'disabled'}
        if isinstance(thinking, dict):
            payload['thinking'] = dict(thinking)
    elif cfg.get('reasoning_effort'):
        effort = str(cfg['reasoning_effort']).lower()
        if effort in {'none', 'auto'}:
            if model == 'deepseek-flash' and 'api.deepseek.com' in base.lower():
                payload['thinking'] = {'type': 'disabled'}
        else:
            payload['reasoning_effort'] = effort
    elif model == 'deepseek-flash' and 'api.deepseek.com' in base.lower():
        payload['thinking'] = {'type': 'disabled'}
    if tools:
        payload['tools'] = tools
        payload['tool_choice'] = 'auto'
    request = urllib.request.Request(
        base + '/chat/completions', data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'Accept': 'text/event-stream',
                 'Authorization': 'Bearer ' + key})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            stream = _read_chat_completions_stream(response, progress=progress)
    except urllib.error.HTTPError as exc:
        try:
            reason = exc.read().decode('utf-8', errors='replace')[:1000]
        except Exception:
            reason = str(exc)
        reason_lower = reason.lower()
        if tools and any(marker in reason_lower for marker in (
                'tool_calls', 'tool_choice', 'function calling', 'function_call',
                'tool calls are not supported', 'tools are not supported',
                'does not support tools')):
            raise RuntimeError(
                '当前模型/接口不支持所需的 function calling，或请求格式不兼容。'
                '请配置支持 OpenAI 兼容 tools 的模型。服务返回: %s' % reason) from exc
        if any(marker in reason_lower for marker in (
                'image_url', 'image input', 'image content', 'vision', 'multimodal')):
            raise RuntimeError(
                '当前模型/接口不接受图像输入；请确认该模型支持 OpenAI 兼容的 image_url。'
                '服务返回: %s' % reason) from exc
        if 'stream' in reason_lower:
            # 某些非标准实现可能不支持流式；这里明确报出而不是静默降级。
            raise RuntimeError(
                '当前接口不支持流式响应(stream=true)，无法接收思考链增量。'
                '请更换支持 OpenAI 兼容流式的模型服务。服务返回: %s' % reason) from exc
        raise RuntimeError('大模型接口 HTTP %s: %s' % (exc.code, reason)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError('大模型接口连接失败: %s' % exc) from exc

    return _chat_stream_to_message(stream, payload)


def parse(text: str, cfg: dict):
    """调用大模型解析, 返回 features 列表; 失败抛异常。"""
    base = (cfg.get('base_url') or '').rstrip('/')
    model = cfg.get('model') or 'deepseek'
    key = cfg.get('api_key') or 'sk-local'
    if not base:
        raise RuntimeError('未配置接口地址')

    ok, msg = ensure_local_server(cfg)
    if not ok:
        raise RuntimeError(msg)

    url = base + '/chat/completions'
    payload = {
        'model': model,
        'temperature': 0.1,
        'max_tokens': 2048,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': text},
        ],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode('utf-8'))

    msgobj = data['choices'][0]['message']
    content = msgobj.get('content') or ''
    obj = _extract_json(content)
    if obj is None:
        # 有些实现把答案放在 reasoning_content 里
        obj = _extract_json(msgobj.get('reasoning_content') or '')
    if obj is None:
        raise RuntimeError('模型没有返回可解析的 JSON: %s' % content.strip()[:120])

    feats = obj.get('features') if isinstance(obj, dict) else obj
    if not isinstance(feats, list):
        raise RuntimeError('模型返回内容不是特征数组')
    clean = []
    for f in feats:
        if isinstance(f, dict) and f.get('kind'):
            clean.append({'kind': f['kind'], 'params': f.get('params') or {},
                          'op': f.get('op', 'add')})
    if not clean:
        raise RuntimeError('模型没能从描述里提取出特征')
    return clean


# --------------------------------------------------------------------------
# U9：结构优先路径（LLM 只出结构，数值回本地求值）
# --------------------------------------------------------------------------

def parse_plan(text: str, cfg: dict, user_text: str = None):
    """**U9 推荐路径**：让模型只输出结构 plan，数值由本地求值。

    与 `parse()` 的区别（也是升级文档 U9 的要求）：
        · `parse()`（旧）在提示词里**要求模型把所有数字都算出来**，
          包括单位换算 —— 通用 LLM 在参数精度上不可靠，数值幻觉会直接变成错误几何，
          而且换模型/换温度结果就变，**不可复现**。
        · `parse_plan()` 要求模型**一个数字都不许输出**（`llm_plan.validate_plan`
          会把任何裸数字判为违规），只说"这个参数取自原文的『直径』"或
          "这个尺寸去 GB/T 276 表里查 6204"；取数、换算、推导全部留在本地。

    返回 (特征列表, 问题列表, 诊断信息)；模型输出不合规时抛 RuntimeError（带违规清单）。
    """
    from ai_modeler.agent import llm_plan as LP
    base = (cfg.get('base_url') or '').rstrip('/')
    model = cfg.get('model') or 'deepseek'
    key = cfg.get('api_key') or 'sk-local'
    if not base:
        raise RuntimeError('未配置接口地址')
    ok, msg = ensure_local_server(cfg)
    if not ok:
        raise RuntimeError(msg)

    url = base + '/chat/completions'
    payload = {
        'model': model,
        'temperature': 0.0,          # 结构任务不需要随机性
        'max_tokens': 2048,
        'messages': [
            {'role': 'system', 'content': LP.PLAN_SYSTEM_PROMPT},
            {'role': 'user', 'content': text},
        ],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    msgobj = data['choices'][0]['message']
    obj = _extract_json(msgobj.get('content') or '')
    if obj is None:
        obj = _extract_json(msgobj.get('reasoning_content') or '')
    if obj is None:
        raise RuntimeError('模型没有返回可解析的 JSON')

    ok2, errs = LP.validate_plan(obj)
    if not ok2:
        # 不"尽力而为"地修：plan 不合法就是模型越界去算数了，必须让它重来
        raise RuntimeError('模型输出违反"只出结构不出数"：\n  - ' + '\n  - '.join(errs[:8]))
    feats, asks, bad = LP.resolve_plan(obj, user_text if user_text is not None else text)
    if bad:
        raise RuntimeError('plan 求值失败：\n  - ' + '\n  - '.join(bad[:8]))
    return feats, asks, {'plan': obj}


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    cfg = {'base_url': local_url(), 'model': 'deepseek', 'api_key': 'sk-local'}
    print('本地引擎:', local_available())
    print('服务状态:', ensure_local_server(cfg))
    if len(sys.argv) > 1:
        t0 = time.time()
        print(json.dumps(parse(sys.argv[1], cfg), ensure_ascii=False, indent=2)[:1200])
        print('耗时 %.1fs' % (time.time() - t0))
