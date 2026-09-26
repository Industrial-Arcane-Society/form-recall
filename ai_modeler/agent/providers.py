# -*- coding: utf-8 -*-
"""多供应商模型配置：档案存储、迁移、激活镜像与模型列表拉取。

只用标准库。存储结构（config.json 的 llm 键）::

    {
      "enabled": true,
      "active_provider_id": "p1",
      "providers": [
        {"id": "p1", "name": "SiliconFlow",
         "base_url": "https://api.siliconflow.cn/v1",
         "model": "moonshotai/Kimi-K2.7-Code",
         "api_key": "sk-…",
         "reasoning_effort": "none",
         "thinking_budget": 4096},  # optional; Kimi only
        {"id": "p2", "name": "DeepSeek 官方 API", …}
      ],
      "base_url": "…", "model": "…", "api_key": "…", "reasoning_effort": "…"
    }

设计要点：平铺字段（base_url/model/api_key/reasoning_effort）始终等于
“当前供应商”的镜像。这样 agentloop / agent_runtime / ui 等所有既有消费方
继续读平铺字段即可，完全不用感知供应商列表的存在。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

EFFORTS = ('none', 'low', 'high', 'max')

# 新建供应商的模板。label 用于 UI 展示；note 提示一句话用途。
TEMPLATES = [
    {'key': 'siliconflow', 'label': 'SiliconFlow',
     'note': 'Kimi-K2.7-Code 等，OpenAI 兼容，思维链流式显示',
     'base_url': 'https://api.siliconflow.cn/v1',
     'model': 'moonshotai/Kimi-K2.7-Code', 'api_key': '',
     'reasoning_effort': 'none'},
    {'key': 'deepseek', 'label': 'DeepSeek 官方 API',
     'note': 'deepseek-flash；走官方 Responses API，思考链流式显示',
     'base_url': 'https://api.deepseek.com/v1',
     'model': 'deepseek-flash', 'api_key': '',
     'reasoning_effort': 'high'},
    {'key': 'openai', 'label': 'OpenAI',
     'note': 'gpt-4o-mini；需自备 api.openai.com 密钥',
     'base_url': 'https://api.openai.com/v1',
     'model': 'gpt-4o-mini', 'api_key': '',
     'reasoning_effort': 'none'},
    {'key': 'local', 'label': '本地 llama.cpp',
     'note': '离线免密钥；先运行「安装/管理本地 DeepSeek」',
     'base_url': 'http://127.0.0.1:8090/v1',
     'model': 'deepseek', 'api_key': 'sk-local',
     'reasoning_effort': 'none'},
    {'key': 'custom', 'label': '自定义（OpenAI 兼容）',
     'note': '任何 /v1/chat/completions 兼容服务（vLLM、Ollama 等）',
     'base_url': '', 'model': '', 'api_key': '',
     'reasoning_effort': 'none'},
]

_HOST_LABELS = (
    ('siliconflow', 'SiliconFlow'),
    ('deepseek', 'DeepSeek 官方 API'),
    ('openai', 'OpenAI'),
    ('moonshot', 'Moonshot'),
    ('dashscope', '阿里云百炼'),
    ('bigmodel', '智谱 GLM'),
)


def new_id() -> str:
    """生成不依赖 uuid 的档案 id（时间戳 + 随机后缀）。"""
    return 'p%x%03x' % (int(time.time() * 1000),
                        int(time.monotonic() * 1000) % 0x1000
                        ^ (id(object()) % 0x1000))


def host_label(base_url) -> str:
    """由接口地址推断一个可读的供应商名。"""
    try:
        host = (urlsplit(str(base_url or '')).hostname or '').strip().lower()
    except ValueError:
        host = ''
    if not host:
        return '自定义'
    if host in {'127.0.0.1', 'localhost', '::1'}:
        return '本地 llama.cpp'
    for marker, label in _HOST_LABELS:
        if marker in host:
            return label
    return host


def template_labels():
    """[(key, '标签 — 说明'), …]，供新建对话框使用。"""
    return [(t['key'], '%s — %s' % (t['label'], t['note'])) for t in TEMPLATES]


def template_profile(template_key: str) -> dict:
    """按模板生成一份新的供应商档案（未加入列表）。未知模板 → 空白自定义。"""
    tpl = None
    for item in TEMPLATES:
        if item['key'] == template_key:
            tpl = item
            break
    if tpl is None:
        tpl = next(t for t in TEMPLATES if t['key'] == 'custom')
    return {
        'id': new_id(),
        'name': tpl['label'],
        'base_url': tpl['base_url'],
        'model': tpl['model'],
        'api_key': tpl.get('api_key') or '',
        'reasoning_effort': tpl.get('reasoning_effort') or 'none',
    }


def _valid_profiles(value):
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, dict) and str(item.get('id') or '').strip():
            profile = {
                'id': str(item['id']).strip(),
                'name': str(item.get('name') or '').strip(),
                'base_url': str(item.get('base_url') or '').strip(),
                'model': str(item.get('model') or '').strip(),
                'api_key': str(item.get('api_key') or '').strip(),
            }
            effort = str(item.get('reasoning_effort') or '').strip().lower()
            profile['reasoning_effort'] = effort if effort in EFFORTS else 'none'
            for key, value_extra in item.items():
                if key not in profile and isinstance(value_extra, (str, int, float, bool)):
                    profile[key] = value_extra
            out.append(profile)
    return out


def ensure_providers(llm_cfg: dict) -> dict:
    """补全 providers / active_provider_id（幂等；不修改传入对象）。

    没有档案列表时，用现有平铺字段生成第一份档案并激活 ——
    旧版单供应商配置因此无感升级，平铺字段保持原值。
    """
    cfg = dict(llm_cfg or {})
    if not isinstance(cfg, dict):
        return {'enabled': False, 'providers': [], 'active_provider_id': ''}
    profiles = _valid_profiles(cfg.get('providers'))
    if not profiles:
        profile = {
            'id': new_id(),
            'name': host_label(cfg.get('base_url')),
            'base_url': str(cfg.get('base_url') or '').strip(),
            'model': str(cfg.get('model') or '').strip(),
            'api_key': str(cfg.get('api_key') or '').strip(),
        }
        effort = str(cfg.get('reasoning_effort') or '').strip().lower()
        profile['reasoning_effort'] = effort if effort in EFFORTS else 'none'
        # Preserve this optional SiliconFlow Kimi extension when migrating
        # legacy flat llm configs into a provider profile.
        if 'thinking_budget' in cfg:
            profile['thinking_budget'] = cfg['thinking_budget']
        profiles = [profile]
    active_id = str(cfg.get('active_provider_id') or '').strip()
    if active_id not in {p['id'] for p in profiles}:
        active_id = profiles[0]['id']
    cfg['providers'] = profiles
    cfg['active_provider_id'] = active_id
    return cfg


def active_profile(llm_cfg: dict) -> dict:
    """返回当前供应商档案（副本）；结构缺失时返回 {}。"""
    profiles = _valid_profiles((llm_cfg or {}).get('providers'))
    if not profiles:
        return {}
    active_id = str((llm_cfg or {}).get('active_provider_id') or '').strip()
    for profile in profiles:
        if profile['id'] == active_id:
            return dict(profile)
    return dict(profiles[0])


def mirror_active(llm_cfg: dict) -> dict:
    """把当前供应商写入平铺字段；返回更新后的配置（覆盖式镜像）。

    llm.py 的适配层（SiliconFlow enable_thinking / DeepSeek Responses）
    与所有既有消费方都只读平铺字段，这一步让切换供应商即时生效。
    """
    cfg = dict(llm_cfg or {})
    profiles = _valid_profiles(cfg.get('providers'))
    active = active_profile(cfg)
    if active:
        cfg['base_url'] = active.get('base_url') or ''
        cfg['model'] = active.get('model') or ''
        cfg['api_key'] = active.get('api_key') or ''
        cfg['reasoning_effort'] = active.get('reasoning_effort') or 'none'
        if 'thinking_budget' in active:
            cfg['thinking_budget'] = active['thinking_budget']
        else:
            # Avoid leaking one provider's budget into another active profile.
            cfg.pop('thinking_budget', None)
    cfg.pop('thinking', None)        # 避免旧布尔开关覆盖每供应商的档位
    cfg['providers'] = profiles or cfg.get('providers') or []
    if active:
        cfg['active_provider_id'] = active['id']
    return cfg


def unique_name(profiles, name: str) -> str:
    """显示名去重：重名自动追加“ 2”“ 3”。"""
    existing = {str(p.get('name') or '').strip() for p in profiles or []}
    base = str(name or '').strip() or '未命名'
    if base not in existing:
        return base
    n = 2
    while ('%s %d' % (base, n)) in existing:
        n += 1
    return '%s %d' % (base, n)


def profile_label(profile: dict) -> str:
    """下拉框展示：名称 · 模型短名。"""
    name = str((profile or {}).get('name') or '').strip() or '未命名'
    model = str((profile or {}).get('model') or '').strip()
    short = model.rsplit('/', 1)[-1] if model else '未选模型'
    return '%s · %s' % (name, short)


def fetch_models(base_url, api_key, timeout=15):
    """GET {base}/models 拉取模型 id 列表（OpenAI 兼容格式）。

    返回 (True, [id, …]) 或 (False, 中文错误说明)。
    SiliconFlow / DeepSeek / OpenAI / llama.cpp / vLLM 均支持该端点。
    """
    base = str(base_url or '').strip().rstrip('/')
    if not base:
        return False, '接口地址为空，请先填写。'
    headers = {'Accept': 'application/json'}
    if str(api_key or '').strip():
        headers['Authorization'] = 'Bearer ' + str(api_key).strip()
    request = urllib.request.Request(base + '/models', headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode('utf-8', errors='replace')[:200]
        except Exception:
            body = ''
        hint = ('（检查 API Key 是否正确）' if exc.code in (401, 403)
                else '（本地服务请确认已启动）' if '127.0.0.1' in base else '')
        return False, ('HTTP %s %s %s' % (exc.code, body, hint)).strip()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, '无法连接 %s：%s' % (base, exc)
    except json.JSONDecodeError:
        return False, '返回的不是有效 JSON（该服务可能不提供 /models）。'
    items = data.get('data') if isinstance(data, dict) else None
    ids = []
    if isinstance(items, list):
        for item in items:
            model_id = item.get('id') if isinstance(item, dict) else None
            if isinstance(model_id, str) and model_id.strip():
                ids.append(model_id.strip())
    if not ids and isinstance(data, dict):
        # 少数实现把列表直接放在顶层
        for key in ('models', 'data'):
            value = data.get(key)
            if isinstance(value, list):
                for item in value:
                    model_id = item.get('id') if isinstance(item, dict) else (
                        item if isinstance(item, str) else None)
                    if isinstance(model_id, str) and model_id.strip():
                        ids.append(model_id.strip())
    if not ids:
        return False, '响应里没有模型列表（格式不兼容 /models）。'
    return True, sorted(set(ids))
