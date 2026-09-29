# -*- coding: utf-8 -*-
r"""Token 用量聚合与费用估算（Agent 创造模式）。

数据来源（实测确认，不是估算）
    DSH 运行时在**每个模型调用结束**时发出一条 `assistant/message` 事件，其中
    `event["data"]["usage"]` =
        {"inputTokens": 新增(未命中缓存)输入, "cacheReadTokens": 缓存命中读取,
         "outputTokens": 生成, "totalTokens": 三者之和}
    用量事件条数与 `model_call_count` 一一对应（实测 6 与 6、4 与 4 均吻合）。
    一次任务会有多次模型调用（规划 → 建特征 → 预览 → 复核 → 返修），所以要累加。

价格从哪来（诚实说明）
    DSH 运行时**不给自定义路由算钱**（应用用的是自定义 provider `ai-modeler`，
    没有声明 pricing），所以费用只能**本地估算**：
      * 单价表放在 `config.json` 的 `pricing` 里，可随时改；
      * 默认值取自 OpenRouter 公开模型接口（`/api/v1/models`）公开的参考价，
        USD / 百万 tokens，**不等于你的服务商实际账单**；
      * 界面上所有金额都带"≈"与"估算"字样，并显示所用单价，避免被当成账单。
    如果某个模型没有配单价，就只显示 token，不显示金额（不猜）。
"""
from __future__ import annotations

from typing import Any

#: 默认单价：{"input": 新增输入, "cache_read": 缓存命中读取, "output": 生成}，单位 USD / 百万 tokens。
#: 参考价抓取时间 2026-09（OpenRouter /api/v1/models）。请以服务商官网为准。
DEFAULT_PRICING: dict[str, Any] = {
    "currency": "USD",
    "usd_to_cny": 7.2,
    "_来源": "OpenRouter 公开模型接口参考价（USD/百万 tokens，2026-09 抓取）；请以你的服务商官网为准。",
    "models": {
        "deepseek-flash": {"input": 0.02, "cache_read": 0.02, "output": 0.60},
        "deepseek-v4-flash": {"input": 0.14, "cache_read": 0.028, "output": 0.28},
        "deepseek-v4-flash-vision-exp": {"input": 0.216, "cache_read": 0.00686, "output": 0.647},
        "deepseek-v4-pro": {"input": 0.955, "cache_read": 0.079605, "output": 1.911},
        "deepseek-v4-flash-latest": {"input": 0.012, "cache_read": 0.01, "output": 0.5},
        "deepseek-flash-latest": {"input": 0.02, "cache_read": 0.02, "output": 0.60},
        "moonshotai/kimi-k2.7-code": {"input": 0.656, "cache_read": 0.18, "output": 3.30},
        "kimi-k2.7-code": {"input": 0.656, "cache_read": 0.18, "output": 3.30},
    },
}


def _norm(text: str) -> str:
    return str(text or "").strip().lower().replace("_", "-")


def price_for(model: str, pricing: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
    """按模型名找单价：先精确，再去掉路径前缀/后缀做模糊匹配。"""
    table = {}
    if isinstance(pricing, dict):
        models = pricing.get("models")
        if isinstance(models, dict):
            table = models
    if not table:
        table = DEFAULT_PRICING["models"]
    want = _norm(model)
    for name, price in table.items():
        if _norm(name) == want and isinstance(price, dict):
            return price, str(name)
    # 模糊：kimi-k2.7-code 命中 moonshotai/kimi-k2.7-code
    for name, price in table.items():
        n = _norm(name)
        if isinstance(price, dict) and want and (want in n or n in want):
            return price, str(name)
    return None, ""


def aggregate(events) -> dict[str, Any]:
    """把一轮任务里所有 assistant/message 事件的用量累加起来。

    返回 {"input_tokens", "output_tokens", "cache_read_tokens", "total_tokens", "calls"}
    —— 全部是**实测值**；没有用量的事件直接跳过（例如纯工具调用的回合）。
    """
    got = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
           "total_tokens": 0, "calls": 0}
    for event in events or []:
        if not isinstance(event, dict) or event.get("type") != "assistant/message":
            continue
        data = event.get("data")
        usage = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(usage, dict) or not usage:
            continue
        try:
            got["input_tokens"] += int(usage.get("inputTokens") or 0)
            got["output_tokens"] += int(usage.get("outputTokens") or 0)
            got["cache_read_tokens"] += int(usage.get("cacheReadTokens") or 0)
            got["total_tokens"] += int(usage.get("totalTokens") or 0)
        except (TypeError, ValueError):
            continue
        got["calls"] += 1
    return got


def estimate(usage: dict[str, Any], model: str, pricing: dict[str, Any] | None) -> dict[str, Any]:
    """按单价估算费用。返回 dict；未配置单价时 priced=False，只给 token。"""
    price, matched = price_for(model, pricing)
    src = pricing if isinstance(pricing, dict) else {}
    currency = str(src.get("currency") or DEFAULT_PRICING["currency"])
    rate = src.get("usd_to_cny")
    if rate is None:
        rate = DEFAULT_PRICING["usd_to_cny"]
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        rate = DEFAULT_PRICING["usd_to_cny"]

    inp = int(usage.get("input_tokens") or 0)
    cache = int(usage.get("cache_read_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    basis = {
        "model": model,
        "matched": matched,
        "currency": currency,
        "unit": "USD/百万 tokens" if currency.upper() == "USD" else "每百万 tokens",
        "input": (price or {}).get("input"),
        "cache_read": (price or {}).get("cache_read"),
        "output": (price or {}).get("output"),
        "usd_to_cny": rate,
        "source": str(src.get("_来源") or DEFAULT_PRICING["_来源"]),
    }
    if not price:
        return {"priced": False, "amount": None, "cny": None, "basis": basis}

    def _cost(tokens: int, unit_price) -> float:
        try:
            return tokens / 1_000_000.0 * float(unit_price or 0)
        except (TypeError, ValueError):
            return 0.0

    amount = _cost(inp, price.get("input")) + _cost(cache, price.get("cache_read")) \
        + _cost(out, price.get("output"))
    cny = amount * rate if currency.upper() == "USD" else amount
    return {"priced": True, "amount": round(amount, 6), "cny": round(cny, 4),
            "usd": round(amount, 6) if currency.upper() == "USD" else None,
            "basis": basis}


def fmt_tokens(n) -> str:
    try:
        return "{:,}".format(int(n or 0))
    except (TypeError, ValueError):
        return "0"


def fmt_money(amount, cny=None, currency: str = "USD") -> str:
    """把金额格式化成便于阅读的字符串。金额都很小，所以小数位给足。"""
    if amount is None:
        return "未配置单价"
    try:
        a = float(amount)
    except (TypeError, ValueError):
        return "—"
    if currency.upper() == "USD":
        s = ("$%.6f" % a).rstrip("0").rstrip(".")
        if cny is not None:
            try:
                s += "（≈ ¥%.4f）" % float(cny)
            except (TypeError, ValueError):
                pass
        return s
    return "¥%.4f" % a


def format_chat_lines(usage: dict[str, Any], cost: dict[str, Any],
                      total_cost: dict[str, Any] | None = None) -> str:
    """聊天区里显示的两行说明（本轮用量 + 费用口径）。"""
    basis = cost.get("basis") if isinstance(cost, dict) else {} or {}
    line1 = ("本轮用量：新增输入 %s · 缓存读取 %s · 输出 %s = %s tokens（%d 次模型调用）"
             % (fmt_tokens(usage.get("input_tokens")), fmt_tokens(usage.get("cache_read_tokens")),
                fmt_tokens(usage.get("output_tokens")), fmt_tokens(usage.get("total_tokens")),
                int(usage.get("calls") or 0)))
    if cost.get("priced"):
        line2 = "估算费用 ≈ %s" % fmt_money(cost.get("amount"), cost.get("cny"),
                                           str(basis.get("currency") or "USD"))
        unit = ("单价（%s，%s）：输入 %s / 缓存 %s / 输出 %s 每百万 tokens"
                % (basis.get("matched") or basis.get("model") or "当前模型",
                   basis.get("currency") or "USD", basis.get("input"),
                   basis.get("cache_read"), basis.get("output")))
        line2 += "｜" + unit + "｜参考价，可在 config.json 的 pricing 里改"
    else:
        line2 = ("费用未估算：config.json 的 pricing 里没有 %s 的单价"
                 "（只显示 token；填上单价即可显示金额）" % (basis.get("model") or "当前模型"))
    if isinstance(total_cost, dict) and total_cost:
        tot = total_cost.get("amount")
        line2 += "｜本聊天累计 ≈ %s" % fmt_money(tot, total_cost.get("cny"),
                                             str(basis.get("currency") or "USD"))
    return line1 + "\n" + line2
