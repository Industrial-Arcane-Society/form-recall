# -*- coding: utf-8 -*-
r"""大模型适配层 —— 同时支持线上 API 和本地 llama.cpp / Ollama。

只用标准库, 不依赖第三方包。

本地模式说明:
  程序会在需要时自动拉起 local_ai\llamacpp\llama-server.exe 加载 DeepSeek,
  它提供 OpenAI 兼容接口 (http://127.0.0.1:8090/v1), 因此网络层代码完全一样。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    from . import llm_plan as LP
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
