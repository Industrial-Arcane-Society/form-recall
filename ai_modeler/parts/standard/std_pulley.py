# -*- coding: utf-8 -*-
r"""
V 带轮 / 窄 V 带轮 的标准尺寸解析与剖面生成。

数据来源：`kb/pulley_std.json`（由 `tools/extract_pulley_std.py` 从机械设计手册中
标注了国家标准号的表格抽取）：
  · 轮槽截面尺寸：GB/T 10412—2002（基准宽度制）/ GB/T 10413—2002（有效宽度制）
  · 基准直径系列：GB/T 10412—2002
  · 槽角 φ 分档（≤60°:32°、≤118°:34°、≤190°:36°、>190°:38°）：同表表头
  · 带轮结构尺寸（轮毂直径/长度、辐板厚度、轮缘内径）：手册"带轮结构型式和辐板厚度"

几何约定（与标准一致）：
  · 槽的**基准宽度 bd** 在**基准线**处量取，基准线在基准直径 dd 的圆柱面上；
  · 槽在基准线以上再深 ha（槽顶高）、以下再深 hf，总槽深 = ha + hf；
  · 带轮外径 da = dd + 2·ha，即基准线比轮缘外圆低 ha；
  · 槽的槽角 φ 随基准直径分档（直径越大、带弯曲越厉害，槽口越张）。
  · 轮槽在基准线处的宽度就是 bd，因此槽壁斜率由 φ 决定：
        半角 = φ/2，槽顶（外圆处）宽度 = bd - 2·ha·tan(φ/2)，
        槽底宽度 = bd + 2·hf·tan(φ/2)。
    这一构造与手册"轮槽截面尺寸"表中 bd / ha / hf / e 四个尺寸自洽，
    不需要额外的槽顶/槽底宽度表。

注意：本模块只做"按标准尺寸生成几何"，不改变任何标准数值。
"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import json
import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

_KB_PATH = os.path.join(str(PROJECT_ROOT),
                        "kb", "pulley_std.json")

_DB: Optional[dict] = None


def load_db() -> dict:
    """按需载入标准数据（只读一次）。文件缺失时返回空字典，调用方走兜底。"""
    global _DB
    if _DB is None:
        try:
            with open(_KB_PATH, encoding="utf-8") as f:
                _DB = json.load(f)
        except Exception:
            _DB = {}
    return _DB


# 兜底：知识库缺失时使用公开标准数值（GB/T 10412—2002 基准宽度制）
FALLBACK_GROOVES = {
    "Y": dict(symbol="Y", narrow_symbol="—", base_width_bd=5.3, addendum_ha=1.6,
              dedendum_hf=4.7, groove_depth=6.3, pitch_e=8.0),
    "Z": dict(symbol="Z", narrow_symbol="SPZ", base_width_bd=8.5, addendum_ha=2.0,
              dedendum_hf=7.0, groove_depth=9.0, pitch_e=12.0),
    "A": dict(symbol="A", narrow_symbol="SPA", base_width_bd=11.0, addendum_ha=2.75,
              dedendum_hf=8.7, groove_depth=11.45, pitch_e=15.0),
    "B": dict(symbol="B", narrow_symbol="SPB", base_width_bd=14.0, addendum_ha=3.5,
              dedendum_hf=10.8, groove_depth=14.3, pitch_e=19.0),
    "C": dict(symbol="C", narrow_symbol="SPC", base_width_bd=19.0, addendum_ha=4.8,
              dedendum_hf=14.3, groove_depth=19.1, pitch_e=25.5),
    "D": dict(symbol="D", narrow_symbol="—", base_width_bd=27.0, addendum_ha=8.1,
              dedendum_hf=19.9, groove_depth=28.0, pitch_e=37.0),
    "E": dict(symbol="E", narrow_symbol="—", base_width_bd=32.0, addendum_ha=9.6,
              dedendum_hf=23.4, groove_depth=33.0, pitch_e=44.5),
}

# 槽型别名（用户可能写全称）
TYPE_ALIASES = {
    "y": "Y", "z": "Z", "spz": "Z", "a": "A", "spa": "A", "b": "B", "spb": "B",
    "c": "C", "spc": "C", "d": "D", "e": "E",
    "y型": "Y", "z型": "Z", "a型": "A", "b型": "B", "c型": "C", "d型": "D", "e型": "E",
    "窄v": "SPZ", "窄v带": "SPZ",
}

# 槽角 φ 分档（手册表头：≤60 → 32°、>60 → 34°、>118 → 36°、>190 → 38°）
ANGLE_TABLE = ((60.0, 32.0), (118.0, 34.0), (190.0, 36.0), (float("inf"), 38.0))


def normalize_type(text) -> Optional[str]:
    """把用户写的槽型（'A'、'a'、'SPA'、'A型'）归一成 'A'。"""
    if not text:
        return None
    key = str(text).strip().lower()
    got = TYPE_ALIASES.get(key)
    if got and got.startswith("SP"):
        return got[2:]
    return got


def groove_table() -> Dict[str, dict]:
    db = load_db()
    tbl = db.get("v_belt_grooves") or {}
    if not tbl:
        return {k: dict(v) for k, v in FALLBACK_GROOVES.items()}
    out = {}
    for sym, v in tbl.items():
        out[sym] = dict(v)
    for sym, v in FALLBACK_GROOVES.items():      # 缺项补齐
        out.setdefault(sym, dict(v))
    return out


def groove_angle(pitch_d: float) -> float:
    d = float(pitch_d)
    for bound, ang in ANGLE_TABLE:
        if d <= bound:
            return ang
    return 38.0


def diameter_series(gtype: str):
    """取该槽型的标准基准直径表 {基准直径: 外径}。"""
    db = load_db()
    tbl = (db.get("v_belt_diameter_series") or {}).get(gtype) or {}
    return {float(k): float(v) for k, v in tbl.items()}


def round_to_series(pitch_d: float, gtype: str):
    """把用户给的基准直径吸附到标准系列值（没有系列表或差太远时原样返回）。"""
    tbl = diameter_series(gtype)
    if not tbl:
        return float(pitch_d), None
    best = min(tbl.keys(), key=lambda k: abs(k - float(pitch_d)))
    if abs(best - float(pitch_d)) <= max(2.0, 0.08 * float(pitch_d)):
        return best, tbl[best]
    return float(pitch_d), None


def edge_distance(bd: float, ha: float, phi: float) -> float:
    """轮槽基准线到轮缘端面的最小距离 f：槽顶半宽（外圆处最宽）加一点圆角余量。"""
    half_tip = max(0.5, (bd - 2.0 * ha * math.tan(math.radians(phi / 2.0))) / 2.0)
    return half_tip


def spec(pitch_d: float, grooves: int = 3, gtype: str = None,
         height: float = None, bore_d: float = 0.0,
         snap_to_series: bool = True, keep_width: bool = False,
         raw_width: float = None):
    """算出带轮的完整标准尺寸。返回 dict（含 warnings）。

    raw_width: 用户**原文里写的**轮宽。只用于提示语措辞——height 可能是调用方
    已经按标准取过的值，直接拿它去写"原输入的 X mm 不足"会显示一个用户从没写过的数
    （实测参数表路径提示成"原输入的 39.3185 mm"，旧解析路径提示"原输入的 30 mm"，
    用户看不懂 39.3185 是从哪来的）。
    """
    warnings: List[str] = []
    n = max(1, int(grooves))
    tbl = groove_table()

    # 1) 定槽型：没给就按基准直径推荐（手册常用：小轮 Y/Z、中轮 A/B、大轮 C 以上）
    gtype = normalize_type(gtype)
    if gtype not in tbl:
        d = float(pitch_d)
        gtype = "Y" if d < 60 else "Z" if d < 100 else "A" if d < 200 else "B" if d < 320 else "C"
        warnings.append("未指定槽型，按基准直径 ⌀%g mm 推荐 %s 型（GB/T 10412 常用档）" % (d, gtype))
    g = tbl[gtype]

    bd = float(g["base_width_bd"])
    ha = float(g["addendum_ha"])
    hf = float(g["dedendum_hf"])
    e = float(g["pitch_e"])
    phi = groove_angle(float(pitch_d))
    half = math.radians(phi / 2.0)
    tip_w = max(1.0, bd - 2.0 * ha * math.tan(half))     # 槽顶宽（外圆处）
    root_w = bd + 2.0 * hf * math.tan(half)              # 槽底宽
    depth = ha + hf

    dd, da_std = (round_to_series(float(pitch_d), gtype) if snap_to_series
                  else (float(pitch_d), None))
    if snap_to_series and da_std and abs(dd - float(pitch_d)) > 1e-9:
        warnings.append("基准直径 ⌀%g 不在 %s 型标准系列中，已吸附到标准值 ⌀%g（外径 ⌀%g）"
                        % (float(pitch_d), gtype, dd, da_std))
    da = da_std if da_std else dd + 2.0 * ha            # 标准外径 = 基准直径 + 2·ha

    # 2) 轮缘宽度：n 槽时基准线间距 = (n-1)·e，两侧各留 f。
    # ⚠️ 留边要按**槽底最宽处**（root_w/2）算，不能只按槽口半宽：
    #    槽底比槽口更宽（V 形张开），只按槽口算会让槽底戳出端面 ——
    #    实测轮缘 39.32 时剖面被撑到 45.92（槽底超出 6.6）。
    f_edge = max(tip_w / 2.0, root_w / 2.0)
    rim_w_min = (n - 1) * e + 2.0 * f_edge
    width = float(height) if height else rim_w_min
    # keep_width: 调用方（如 kernel.make_vbelt_pulley）已经按标准取过宽度，
    # 这里不要再放大一次 —— 否则会出现"双层放大"（实测 39.32 被放到 46.32），
    # 使"受控尺寸自检"与几何对不上。
    if keep_width:
        pass
    elif width < rim_w_min - 1e-6:
        shown = float(raw_width) if raw_width else width
        warnings.append("%d 个 %s 型槽的标准槽间距 e=%g mm，轮缘宽度至少需 %g mm；"
                        "已按标准取 %g mm（原输入的 %g mm 不足，强行压缩会使相邻槽干涉）"
                        % (n, gtype, e, rim_w_min, rim_w_min, shown))
        width = rim_w_min

    # 3) 轮毂 / 辐板 / 轮缘内径（手册结构公式）
    bore = float(bore_d) if bore_d else max(8.0, dd * 0.22)
    if bore >= dd * 0.92:
        # 内孔不可能接近或超过基准直径（那样轮缘就没地方开槽了）。
        # 与其把孔悄悄丢掉（旧行为: 孔没了用户更困惑），不如按上限收一下并明确告知。
        new_bore = max(4.0, dd * 0.55)
        warnings.append('内孔 ⌀%g 相对基准直径 ⌀%g 过大（轮缘无法开槽），'
                        '已按基准直径的 55%% 取 ⌀%g' % (bore, dd, new_bore))
        bore = new_bore
    hub_d = min(1.95 * bore, max(bore + 6.0, da * 0.55))
    hub_len = min(1.8 * bore, width)                     # L=(1.5~2)d，但不宽于轮缘
    web_s = max(4.0, 0.6 * e)                            # 辐板厚度 S=(0.6~1)e
    d2 = da - 2.0 * depth - 2.0 * max(3.0, 0.25 * depth)  # 轮缘内径 d2=da-2(ha+hf+δ)
    d2 = max(hub_d + 6.0, d2)
    if (da - d2) / 2.0 > 0.35 * da:                      # 轮缘太薄 → 加厚
        d2 = da - 2.0 * max(depth + 3.0, 0.12 * da)

    spokes = 0
    web = True
    if dd <= 100 and bore >= 0.33 * dd:                  # 小直径+大孔 → 实心盘
        web = False
    elif (d2 - hub_d) / 2.0 > 45.0 and dd >= 250:        # 大直径 → 辐条
        web = False
        spokes = max(4, min(8, int(dd / 60.0)))
    # 辐板/辐条的轴向中心：贴着轮毂端面往里放，保证不超出轮毂宽度
    web_z = min(width / 2.0, max(width / 2.0 - web_s, 0.0)) - web_s / 2.0

    return dict(gtype=gtype, narrow=g.get("narrow_symbol", "—"),
                grooves=n, pitch_d=dd, outer_d=da, base_width=bd, ha=ha, hf=hf,
                depth=depth, pitch_e=e, angle=phi, tip_w=tip_w, root_w=root_w,
                width=width, rim_w_min=rim_w_min, bore_d=bore, hub_d=hub_d,
                hub_len=hub_len, web_s=web_s, rim_id=d2, web=web, spokes=spokes,
                web_z=web_z,
                standard=g.get("standard", "GB/T 10412-2002"), warnings=warnings)


def profile(s: dict) -> List[Tuple[float, float]]:
    """生成带轮轴向剖面（x=半径, y=轴向坐标），闭合成可回转的轮廓。

    轮廓顺序: 内孔左端 → 轮毂 → 辐板 → 轮缘左端 → 依次跨过 n 个轮槽 → 轮缘右端
              → 辐板 → 轮毂 → 内孔右端（首尾在轴上闭合，回转后即为实体）。
    """
    r_bore = max(1.0, s["bore_d"] / 2.0)
    r_hub = max(r_bore + 1.0, s["hub_d"] / 2.0)
    r_web_lo = r_hub
    r_rim_id = max(r_web_lo + 1.0, s["rim_id"] / 2.0)
    r_rim = s["outer_d"] / 2.0
    r_pitch = s["pitch_d"] / 2.0
    r_root = r_pitch - s["hf"]
    z_rim = s["width"] / 2.0
    z_hub = s["hub_len"] / 2.0
    z_web = s["web_s"] / 2.0
    tip_half = s["tip_w"] / 2.0
    root_half = s["root_w"] / 2.0
    e = s["pitch_e"]

    if r_root <= r_rim_id + 1.0:            # 轮缘太薄，收敛槽深以免切穿
        r_root = max(r_rim_id + 1.0, r_pitch - s["hf"] * 0.75)
    if r_rim <= r_root + 0.5:
        r_rim = r_root + 0.5

    base = min(z_hub, max(z_hub - z_web, 0.0))   # 辐板轴向位置：贴着轮毂端面往里

    def rim_edge_points():
        """自左向右跨过全部轮槽：先到槽口左缘，再下到槽底，再回到槽口右缘。

        ⚠️ 槽必须夹在轮缘之内：如果 n 个槽按标准间距 e 排布会超出轮缘宽度
        （用户给的宽度偏小、或 keep_width 让宽度没被放大），
        早先会照原样摆槽，结果**槽轴比轮缘还宽**，轮廓被撑大 ——
        实测 3 槽 A 型轮缘 39.32 变成 46.32。这里按实际轮缘宽度等比压缩槽间距。
        """
        max_half = z_rim - root_half - 0.2         # 槽底最宽处不许超出端面
        need_half = (s["grooves"] - 1) * e * 0.5
        if need_half > max_half and need_half > 1e-9:
            e_eff = max(0.0, 2.0 * max_half / (s["grooves"] - 1)) if s["grooves"] > 1 else e
        else:
            e_eff = e
        right_start = (s["grooves"] - 1) * e_eff * 0.5
        pts = []
        for i in range(s["grooves"]):
            c = -right_start + i * e_eff
            pts.append((r_rim, c - tip_half))
            pts.append((r_root, c - root_half))
            pts.append((r_root, c + root_half))
            pts.append((r_rim, c + tip_half))
        return pts

    pts: List[Tuple[float, float]] = []
    pts += [(r_bore, -z_hub), (r_hub, -z_hub)]
    if base > -z_hub + 1e-6:
        pts += [(r_hub, -base), (r_rim_id, -base)]
    pts += [(r_rim_id, -z_rim), (r_rim, -z_rim)]
    pts += rim_edge_points()
    pts += [(r_rim, z_rim), (r_rim_id, z_rim)]
    if base > -z_hub + 1e-6:
        pts += [(r_rim_id, base), (r_hub, base)]
    pts += [(r_hub, z_hub), (r_bore, z_hub)]

    # 去掉重合/零长边，避免回转三角化退化
    out: List[Tuple[float, float]] = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    if len(out) >= 2 and abs(out[0][0] - out[-1][0]) < 1e-9 and abs(out[0][1] - out[-1][1]) < 1e-9:
        out.pop()
    return out


def summary(s: dict) -> str:
    """生成一段可读的标准依据说明（会显示在界面"步骤"与建模报告里）。"""
    return ("%s 型 %d 槽（%s）；基准宽度 bd=%g、槽顶高 ha=%g、槽底高 hf=%g、"
            "槽间距 e=%g、槽角 φ=%g°；基准直径 ⌀%g → 外径 ⌀%g"
            % (s["gtype"], s["grooves"], s["standard"], s["base_width"], s["ha"],
               s["hf"], s["pitch_e"], s["angle"], s["pitch_d"], s["outer_d"]))


def explain(s: dict) -> list:
    """提示词 → 生成特征 的对照说明（回答"这句话到底建出了什么"）。

    返回 [(提示词/参数, 对应特征或尺寸, 标准依据), ...]，界面与日志会逐条显示。
    """
    n2 = lambda v: round(float(v), 2)
    out = [
        ("“%s 型”" % s["gtype"], "%s 型轮槽 %d 条" % (s["gtype"], s["grooves"]),
         s["standard"]),
        ("“槽数 %d”" % s["grooves"],
         "轮缘上开 %d 条 V 形槽，基准线间距 e=%g mm" % (s["grooves"], s["pitch_e"]),
         "槽间距 e 取自 GB/T 10412—2002 轮槽截面尺寸表"),
        ("基准直径 ⌀%g" % s["pitch_d"],
         "基准线所在圆柱面；外径 da = dd + 2·ha = ⌀%g" % s["outer_d"],
         "da = dd + 2·ha（ha 为槽顶高，标准表列值）"),
        ("槽剖面",
         "槽顶宽 %g、槽底宽 %g、全槽深 %g（ha=%g + hf=%g）"
         % (n2(s["tip_w"]), n2(s["root_w"]), n2(s["depth"]), s["ha"], s["hf"]),
         "槽角 φ=%g° 由基准直径分档决定（32/34/36/38°）" % s["angle"]),
        ("轮缘宽度 %g" % n2(s["width"]),
         "轮缘最小宽度 = (n-1)·e + 2f = %g" % n2(s["rim_w_min"]),
         "f 为槽顶半宽，保证槽不开口在端面上"),
        ("内孔 ⌀%g" % s["bore_d"], "中心通孔",
         "由输入给定；未给则按 dd 的 0.22 倍取"),
    ]
    if s.get("web"):
        out.append(("轮毂/辐板",
                    "轮毂 ⌀%g × %g 长、辐板厚 %g，轮缘内径 ⌀%g"
                    % (n2(s["hub_d"]), n2(s["hub_len"]), n2(s["web_s"]), n2(s["rim_id"])),
                    "手册带轮结构公式：d1=(1.8~2)d、L=(1.5~2)d、S≈(0.6~1)e、"
                    "轮缘内径 d2=da−2(ha+hf+δ)"))
    else:
        out.append(("实心盘", "整轮做成实心盘体（直径小、孔径大，不需要辐板）",
                    "手册带轮结构型式：小直径带轮用实心结构"))
    if s.get("spokes"):
        out.append(("辐条", "%d 根辐条（大直径带轮减轻重量）" % s["spokes"],
                    "手册带轮结构型式：大直径用辐条式"))
    return out


# --------------------------------------------------------------------------
# 同步带轮：周节制（GB/T 11361—1989）/ 特殊节距制 / 圆弧齿（JB/T 7512.2—1994）节距
#   节距 p 是同步带的齿距，带轮齿数 z 与节圆直径的关系为 dd = p·z/π。
#   齿形按标准梯形齿近似：齿高 ht ≈ 0.5p，齿顶宽 ≈ 0.4p，齿侧为直边。
# --------------------------------------------------------------------------
TIMING_TYPES = {
    # 型号: (节距 mm, 齿高 mm, 齿顶宽 mm, 常用带宽 mm)
    "MXL": (2.032, 0.76, 1.14, 6.4),
    "XL": (5.080, 1.27, 2.57, 9.5),
    "L": (9.525, 1.91, 4.65, 19.1),
    "H": (12.700, 2.29, 6.12, 19.1),
    "XH": (22.225, 3.05, 9.53, 50.8),
    "XXH": (31.750, 3.30, 12.70, 50.8),
    "T2.5": (2.500, 0.70, 1.50, 6.0),
    "T5": (5.000, 1.20, 2.65, 10.0),
    "T10": (10.000, 2.50, 5.30, 16.0),
    "T20": (20.000, 4.00, 10.70, 32.0),
    "3M": (3.000, 1.17, 1.85, 6.0),
    "5M": (5.000, 1.94, 3.05, 15.0),
    "8M": (8.000, 3.05, 5.20, 20.0),
    "14M": (14.000, 5.60, 9.40, 40.0),
}


def timing_pitch(ttype) -> dict:
    t = str(ttype or "XL").strip().upper().replace("型", "")
    pitch, ht, tip, width = TIMING_TYPES.get(t, TIMING_TYPES["XL"])
    return {"type": t if t in TIMING_TYPES else "XL", "pitch": pitch,
            "tooth_h": ht, "tooth_tip": tip, "width": width}


def timing_width(ttype) -> float:
    return float(timing_pitch(ttype)["width"])


def timing_profile(dd: float, teeth: int, ttype: str = "XL"):
    """同步带轮齿廓（x=半径, y=该齿的**角向**偏移量）。用法见 kernel.make_timing_pulley。"""
    info = timing_pitch(ttype)
    p = info["pitch"]
    ht = info["tooth_h"]
    tip = info["tooth_tip"]
    r_pitch = max(2.0, dd / 2.0)
    r_tip = r_pitch - ht * 0.55          # 齿根（轮齿之间）
    r_root = r_pitch + ht * 0.45         # 轮齿顶
    step = 2.0 * math.pi / max(6, teeth)
    # 以弧长换算角向半宽
    half_tip = (tip / 2.0) / r_pitch
    half_root = (p / 2.0 - tip / 2.0) / r_pitch
    pts = []
    for i in range(teeth):
        c = i * step
        pts.append((r_tip, c - half_root * 0.9))
        pts.append((r_root, c - half_tip))
        pts.append((r_root, c + half_tip))
        pts.append((r_tip, c + half_root * 0.9))
    return pts, info


def explain_timing(dd: float, teeth: int, ttype: str) -> list:
    """同步带轮的"提示词 → 生成特征"对照说明。"""
    info = timing_pitch(ttype)
    return [
        ("“%s 型”" % info["type"],
         "%s 型轮齿，节距 p=%g mm" % (info["type"], info["pitch"]),
         "节距取自周节制 GB/T 11361—1989 / 圆弧齿 JB/T 7512.2—1994 规格表"),
        ("“齿数 %d”" % teeth,
         "圆周均布 %d 个轮齿；节圆直径 ⌀%g = p·z/π" % (teeth, dd),
         "dd = p·z/π（同步带轮节圆直径的定义）"),
        ("齿形",
         "齿高 %g、齿顶宽 %g、齿根宽 %g"
         % (info["tooth_h"], info["tooth_tip"], round(info["pitch"] - info["tooth_tip"], 2)),
         "标准梯形齿近似（圆弧齿按同一节距与齿高生成）"),
        ("轮宽",
         "未给宽度时按该型号常用带宽取 %g mm" % info["width"],
         "GB/T 11361 带轮宽度系列"),
    ]
