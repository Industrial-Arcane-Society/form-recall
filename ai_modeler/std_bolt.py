# -*- coding: utf-8 -*-
r"""
U5 扩展：内六角圆柱头螺钉标准数据（ISO 4762 / GB/T 70.1—2008）。

数据来源：`kb/bolt_std.json`（由 `tools/extract_bolt_std.py` 生成，
含 10 个规格 M3–M20 的标准尺寸与 5 项锚点自检）。

为什么要单独做这个：`零件参数` 文件夹里的螺钉全是 CATIA 私有二进制，
实测只能抠出 V5 版本号，逆向不出外形。**但标准件的外形本来就由标准定义** ——
按标准尺寸表建模才是正解，而且可核验（有锚点自检）。

查不到的规格**返回 None**，绝不猜一个"差不多"的头径。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

_KB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "kb", "bolt_std.json")
_DB: Optional[dict] = None


def load_db() -> dict:
    global _DB
    if _DB is None:
        try:
            with open(_KB, encoding="utf-8") as f:
                _DB = json.load(f)
        except Exception:
            _DB = {}
    return _DB


def bolts() -> Dict[str, dict]:
    return (load_db().get("bolts") or {})


def standard() -> str:
    return load_db().get("standard") or "ISO 4762 / GB/T 70.1—2008"


def length_series() -> List[float]:
    return list(load_db().get("length_series") or [])


def normalize(spec) -> Optional[str]:
    """'m10' / 'M10' / '10' / 12 / 12.0 / 'M10×30' -> 'M10'。

    ⚠️ 必须接受**数字**输入：参数表抽出来的 `螺纹规格12` 是 float 12.0，
    直接 str() 会得到 "12.0"，既不是 "M12" 也不是纯数字，于是整条查表失败
    （实测：`内六角螺钉 螺纹规格12 长度50` 因此返回 None）。
    """
    if spec is None:
        return None
    if isinstance(spec, bool):
        return None
    if isinstance(spec, (int, float)):
        v = float(spec)
        if abs(v - round(v)) < 1e-9:
            t = "M%d" % int(round(v))
            return t if t in bolts() else None
        return None
    t = str(spec).strip().upper().replace(" ", "")
    # 完整写法 M10X30 / M10-30：先归一成规格 + 长度，再取规格
    import re as _re
    m = _re.match(r"^M(\d+(?:\.\d+)?)\s*[X*\u00d7-]\s*(\d+(?:\.\d+)?)$", t)
    if m:
        t = "M" + (str(int(float(m.group(1)))) if float(m.group(1)) == int(float(m.group(1)))
                   else m.group(1))
    for sep in ("×", "X", "*", "-"):
        if sep in t:
            t = t.split(sep)[0]
    if t.startswith("M") and t in bolts():
        return t
    # "12" / "12.0" 这类纯数字
    try:
        v = float(t)
        t2 = "M%d" % int(round(v)) if abs(v - round(v)) < 1e-9 else None
    except ValueError:
        t2 = None
    return t2 if t2 in bolts() else None


def nearest_length(L) -> Optional[float]:
    """非标准长度 -> 最接近的标准长度（供提示，不静默替换）。"""
    ser = length_series()
    if not ser:
        return None
    try:
        v = float(L)
    except (TypeError, ValueError):
        return None
    return min(ser, key=lambda x: abs(x - v))


def spec_of(spec, length=None) -> Optional[dict]:
    """取某个规格的完整标准尺寸。查不到返回 None。"""
    key = normalize(spec)
    if not key:
        return None
    v = dict(bolts()[key])
    out = {"spec": key, "standard": standard(), **v}
    out["thread_d"] = float(key[1:])
    out["head_d"] = v["dk"]
    out["head_h"] = v["k"]
    out["hex_s"] = v["s"]
    out["hex_t"] = v["t"]
    if length is not None:
        try:
            L = float(length)
        except (TypeError, ValueError):
            return None
        out["length"] = L
        out["thread_len"] = L
        near = nearest_length(L)
        if near is not None and abs(near - L) > 1e-9:
            out["length_not_standard"] = near
    return out


def spec_from_text(text) -> Optional[dict]:
    """从原文里认 `M10x30` / `M10×30` / `M10*30` 这类**螺钉标准写法**。

    为什么需要：螺钉的行业写法就是 `M10x30`，用户往往**不写"螺纹规格"四个字**，
    参数表的别名规则因此抓不到 d 与 L（实测 `内六角螺钉 M10x30` 整条无特征）。
    这里把这种写法解出来（规格 + 长度），长度仍会在标准长度系列里核对。
    """
    if not text:
        return None
    import re as _re
    m = _re.search(r"[Mm]\s*(\d+(?:\.\d+)?)\s*[xX\u00d7*]\s*(\d+(?:\.\d+)?)", str(text))
    if not m:
        # 只有规格没有长度（如 "M10 的内六角螺钉"）
        m2 = _re.search(r"[Mm]\s*(\d+(?:\.\d+)?)", str(text))
        return spec_of(m2.group(1)) if m2 else None
    return spec_of(m.group(1), m.group(2))


def summary(s: dict) -> str:
    if not s:
        return "标准表里没有这个规格（可用 %s）" % "、".join(sorted(bolts()))
    t = ("内六角圆柱头螺钉 %s（%s）：头部 ⌀%g×%g、内六角对边 %g 深 %g、螺距 %g"
         % (s["spec"], s["standard"], s["head_d"], s["head_h"],
            s["hex_s"], s["hex_t"], s["p"]))
    if s.get("length"):
        t += "、长度 %g" % s["length"]
    if s.get("length_not_standard"):
        t += "（⚠️ %g 不是标准长度系列，最近的是 %g）" % (
            s["length"], s["length_not_standard"])
    return t


def explain(s: dict) -> list:
    """提示词 -> 生成特征 的对照说明（与带轮/轴承一致的可解释口径）。"""
    if not s:
        return []
    return [
        ("“%s”螺纹规格" % s["spec"],
         "螺纹大径 ⌀%g、螺距 p=%g" % (s["thread_d"], s["p"]),
         "ISO 4762 / GB/T 70.1—2008"),
        ("头部",
         "圆柱头 ⌀%g × 高 %g" % (s["head_d"], s["head_h"]),
         "ISO 4762 头部尺寸（表中为 max 值）"),
        ("内六角孔",
         "对边 s=%g、深 t=%g" % (s["hex_s"], s["hex_t"]),
         "ISO 4762 内六角尺寸"),
    ]
