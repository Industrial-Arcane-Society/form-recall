# -*- coding: utf-8 -*-
r"""按**真实实测数据**建模的零件族（本模块只做几何，不做文本解析）。

与 `partlib.py` 的分工：
    · `reallib.py`    负责"这是什么、现实长什么样"（真实型号库 + 解析）
    · 本模块          负责把真实尺寸变成特征
    · `partlib.py`    负责把本模块的零件类注册进 `PARTS` 并接上解析链

**核心原则**：主控尺寸一律取**实测值**，不取型号名义值；凡是按实测数据也复刻
不出来的细节，在 `LIMITS[类别]` 里写明，绝不假装修好了。

实测依据见 `reports/真实几何规律验证.md`、`reports/真实几何学习报告.md`。
"""
from __future__ import annotations

import math
from typing import Dict, List

from .docmodel import ADD, CUT, Feature
from . import reallib as RL

#: 每类的诚实边界（会出现在 UI 提示里）
LIMITS: Dict[str, str] = {
    "EGT型材": "内部齿纹/内腔截面是 TUNKERS 目录数据；外形、长度、孔位孔深均取"
               "STEP 实测。长型材真实圆柱面可达 2000+ 个，BSP 内核做不完，"
               "按预算建并把真实数量写进特征名。",
    "EGT连接件": "异形铸件，按实测包络建，不复刻加强筋与过渡圆角。",
    "内六角螺钉": "按实测外形 + 真实圆柱面指纹建：螺钉 + 弹性垫圈 + 平垫圈三件套。",
    "定位销": "按实测台阶直径与总长建，端部倒角尺寸未实测。",
    "销座": "按实测外形 + 实测圆柱面直径建主孔，孔位未实测。",
    "支座": "按实测外形建，内部减重腔未实测。",
    "支座(铝)": "按实测外形建，型腔与加强筋未实测。",
    "止块": "按实测外形 + 实测圆柱面直径建主孔。",
    "分气块": "按实测外形 + 实测圆柱面直径与个数建气路孔。",
    "阀岛": "按实测外形 + 实测圆柱面指纹建简化体，阀位细节未实测。",
    "标牌": "按实测外形建（薄板）。",
    "补高台": "按实测外形建。",
    "气缸连接块": "按实测外形 + 实测圆柱面直径建主孔。",
    "垫片垫块": "按实测外形 + 实测 ⌀9 孔个数建。",
    "夹紧气缸": "外购件，装配导出无法取得零件外形；按圆柱面指纹建简化体。",
    "双导杆气缸": "外购件，装配导出无法取得零件外形；按圆柱面指纹建简化体。",
    "吊环": "按实测外形 + 实测圆柱面直径建（GB/T 825）。",
    "接近开关": "按实测外形 + 实测圆柱面直径建（BALLUFF BES）。",
}


def _real_of(p) -> dict:
    """取出该次建模对应的真实记录（可能由参数的 `__real` 直接带下来）。"""
    r = p.get("__real")
    if isinstance(r, dict):
        return r
    code = p.get("code") or p.get("型号") or ""
    hit = RL.lookup(str(code)) if code else None
    return (hit or {}).get("real") or {}


def _pkg_of(p) -> dict:
    """取出整条真实记录（含 family/limits 等）。"""
    hit = RL.lookup(str(p.get("code") or "")) if p.get("code") else None
    return hit or {}


def _model_token(p) -> str:
    """取**真实型号**，用于查 `kb/real_features.json` 里的孔位。

    ⚠ 不能用 `p['code']`：EGT 型材的 `code` 是长度代号（118），
    不是型号（`EGT001 SW80x118`）；用代号去查孔位永远查不到 ——
    实测就是这么让 289 个型材的孔全部漏掉的（模型圆柱数 0）。
    真实型号由 `partlib._overlay_real` 塞在 `__real_model` 里。
    """
    for k in ("__real_model", "code"):
        v = p.get(k)
        if v:
            s = str(v).strip()
            if s:
                return s
    return ""


def _trust_of(p) -> str:
    """该零件实测数据的可信度（装配导出的件坐标不可信，不能拿它的孔位）。"""
    tok = _model_token(p)
    hit = RL.lookup(tok) if tok else None
    return ((hit or {}).get("real") or {}).get("trust") or "exact"


#: EGT 型材**走布尔路径**时（目前只有 22 个 EGT006 ⌀30 圆型材，孔互相相交）
#: 能建的孔数预算。
#: 旧内核上限：16 孔 0.25s / 48 孔 3.9s / 96 孔 36.6s / **192 孔体积误差 2% 崩**
#:   → 当时封顶 48。
#: A′ 局部布尔之后：**每刀只碰刀具体附近的面片**（按结点盒子 + 每张面片的盒子
#:   双重剪枝），⌀30×1000 的 48 孔从 72s 降到 6.8s，且体积与解析值一致
#:   （判据 `tools/check_bsp_local.py` 10/10）。所以预算放开到实测孔数，
#:   只在**极端长件**上封顶，避免 GUI 预览等待过久。
_EGT_HOLE_BUDGET = 120


def _box_features(env, name, center=(0.0, 0.0, 0.0)):
    L, W, H = [float(v) for v in env]
    return [Feature("box", {"length": L, "width": W, "height": H,
                            "corner": 0.0, "corner_mode": "fillet",
                            "center": center, "base": False, "axis": "Z"},
                    ADD, name=name)]


def _cyl_features(d, h, name, axis="Z", center=(0.0, 0.0, 0.0)):
    return [Feature("cylinder", {"radius": float(d) / 2.0, "height": float(h),
                                 "center": center, "base": False,
                                 "axis": axis, "segments": 48}, ADD, name=name)]


def _measured_frame(p):
    """实测零件**自己的坐标系**：外形 (X,Y,Z) 与包围盒中心。

    为什么必须按它摆实体（本轮补的口径，之前是个系统性错位）：
      孔位坐标是从实测 STEP 直接搬来的（实测坐标系）。可实体原先一律
      "按排序后的外形、以原点为中心"摆（`_box_features(env_sorted)`、
      圆棒一律摆 Z 轴）。两边坐标系不是一个，孔就整体错位：
      实测垫片 `KMS-M09-H03-20-20` 真实孔在 Y=8、零件中心 Y=25（即距中心 -17），
      模型把实体居中、同一个 Y=8 就变成"距中心 +8"，**错 25 mm**。
      而旧判据比的是"原始坐标"，这种错位一点都看不出来（比的是同一组实测值）。
    返回 (env_raw[3], center[3])；取不到实测包围盒时返回 (None, None)。
    """
    from . import realholes as RH
    real = _real_of(p)
    env = [float(v) for v in (real.get("envelope") or [])]
    tok = _model_token(p)
    plan = RH.hole_plan(tok, budget=1) if tok else {}
    lo, hi = plan.get("bbox_min"), plan.get("bbox_max")
    if len(env) != 3 or not lo or not hi:
        return None, None
    return env, [(float(lo[i]) + float(hi[i])) / 2.0 for i in range(3)]


# ---------------------------------------------------------------------------
# 一、内六角螺钉（含弹垫 + 平垫，共 3 个实体，与真实构成一致）
# ---------------------------------------------------------------------------
def build_socket_bolt_real(p):
    d = float(p.get("d") or 10)
    L = float(p.get("L") or 30)
    dk = float(p.get("bolt_dk") or d * 1.6)
    hk = float(p.get("bolt_head_k") or d)
    s = float(p.get("bolt_hex_s") or d * 0.8)
    real = _real_of(p)
    sw = real.get("spring_washer") or {}
    fw = real.get("flat_washer") or {}
    fs: List[Feature] = []

    # ---------- 先做两个垫圈，并在这一步把内孔切掉 ----------
    # ⚠ 顺序很关键（实测踩过两次）：
    #   ① 垫圈内孔必须**在螺杆并进来之前**切 —— 否则 ⌀5.3 的内孔会把 ⌀5 的
    #      螺杆一起掏空。做法：先把垫圈切成环，再把螺杆并上去把环孔填实。
    #   ② **弹垫紧贴螺钉头、平垫在弹垫下面**。实测 M10x30 的真实轴向：
    #      弹垫 ⌀16 占 -1.6~0.9（紧贴头底面 z=0），平垫 ⌀20 占 -3.6~-1.6。
    #      第一版装反了（平垫贴头、弹垫在下），轴向位置与实测不符。
    z_top = 0.0
    if p.get("spring_washer", 1) and sw:
        st = float(sw["t"])
        fs.append(Feature("cylinder", {"radius": float(sw["d_out"]) / 2.0,
                                       "height": st,
                                       "center": (0, 0, z_top - st / 2.0),
                                       "base": False, "axis": "Z",
                                       "segments": 48}, ADD,
                          name="弹性垫圈 ⌀%g×%g" % (sw["d_out"], st)))
        fs.append(Feature("hole", {"diameter": float(sw["d_in"]),
                                   "through": True, "pos": "center",
                                   "axis": "Z", "segments": 32}, CUT,
                          name="弹性垫圈内孔 ⌀%g" % sw["d_in"]))
        z_top -= st
    if p.get("flat_washer", 1) and fw:
        ft = float(fw["t"])
        fs.append(Feature("cylinder", {"radius": float(fw["d_out"]) / 2.0,
                                       "height": ft,
                                       "center": (0, 0, z_top - ft / 2.0),
                                       "base": False, "axis": "Z",
                                       "segments": 48}, ADD,
                          name="平垫圈 ⌀%g×%g" % (fw["d_out"], ft)))
        fs.append(Feature("hole", {"diameter": float(fw["d_in"]),
                                   "through": True, "pos": "center",
                                   "axis": "Z", "segments": 32}, CUT,
                          name="平垫圈内孔 ⌀%g" % fw["d_in"]))

    # ---------- 螺杆 + 头（头在 +Z，杆朝 -Z）----------
    # 总高 = 公称长 + 头高（实测规律）。放在垫圈之后，让螺杆把环孔填实。
    fs.append(Feature("cylinder", {"radius": d / 2.0, "height": L,
                                   "center": (0, 0, -L / 2.0), "base": False,
                                   "axis": "Z", "segments": 32}, ADD,
                      name="螺杆 ⌀%g×%g" % (d, L)))
    fs.append(Feature("cylinder", {"radius": dk / 2.0, "height": hk,
                                   "center": (0, 0, hk / 2.0), "base": False,
                                   "axis": "Z", "segments": 48}, ADD,
                      name="螺钉头 ⌀%g×%g" % (dk, hk)))

    # ---------- 内六角孔 ----------
    # ⚠ 这里两处曾经都错了（已按 ISO 4762 实物核对）：
    #   ① 对边 ≠ 外接半径：`s` 是**对边宽**，正六边形外接半径 = s/√3。
    #      旧代码拿 s/2 当外接半径 → 做出来的对边只有 0.866s（M5 是 3.46 而非 4）。
    #   ② 孔要**从头顶面往下钻**深 t。
    #      ⚠ 内核的 `prism` 把 `center` 当**底面**用（kernel.extrude_profile 默认
    #      `symmetric=False, z0=0` → 沿轴占据 [c, c+h]），不是当中心用。
    #      所以底面 z 必须写 `hk − ht`，范围才是 [hk−ht, hk]，正好从头顶面往下钻 ht。
    #      写成 `hk − ht/2` 会让孔只切到 hk−ht/2 以上那半截（实测少切一半）。
    ht = float(p.get("bolt_hex_t") or max(1.0, hk * 0.5))
    ht = min(ht, hk * 0.95)               # 孔深不能超过头高
    if s > 0 and ht > 0:
        r_circ = s / (3.0 ** 0.5)         # 外接半径，使**对边 = s**
        prof = [(r_circ * math.cos(math.pi / 3.0 * i + math.pi / 6.0),
                 r_circ * math.sin(math.pi / 3.0 * i + math.pi / 6.0))
                for i in range(6)]
        fs.append(Feature("prism", {
            "profile": prof, "height": ht, "axis": "Z", "segments": 6,
            # 底面摆在 hk − ht：占据 [hk−ht, hk]，从头顶面往下钻 ht
            "center": (0.0, 0.0, hk - ht)}, CUT,
            name="内六角孔 对边%g×深%g" % (s, ht)))
    return fs


# ---------------------------------------------------------------------------
# 二、EGT 型材
# ---------------------------------------------------------------------------
def build_egt_profile_real(p):
    """EGT 型材：**直写网格**（B 计划）—— 等截面拉伸 + 全部实测孔，不做任何布尔。

    实测依据：型材是等截面拉伸件（EGT001 SW80 截面 80.000×80.000、
    EGT002 SW50 截面 50.000×50.000、EGT006 D30 截面 30.000×30.000），
    长度 = 型号码（偏差 0.000）；孔是 30mm 等距孔阵，最长那档有 **545 个孔**。

    为什么必须绕开布尔：纯 Python BSP-CSG 正确做多孔的上限约 96 个孔
    （实测 192 孔体积就错 2%、545 孔更不可能）。
    直写网格实测：545 孔 **0.5 秒**建完、体积误差 0.36%、网格完全封闭
    （判据见 `tools/check_directmesh.py`）。
    """
    from . import realholes as RH
    real = _real_of(p)
    fam = _pkg_of(p).get("family") or {}
    sect = real.get("section") or [p.get("sec_w") or 80.0,
                                   p.get("sec_h") or p.get("sec_w") or 80.0]
    L = float(real.get("length") or p.get("L") or 88.0)
    sec_code = str(fam.get("section") or p.get("section_code") or "SW80").upper()
    model = _model_token(p)
    feats = RH.feature_of(model)
    if feats and feats.get("cylinders"):
        # ⚠ 包络必须取 **STEP 特征的 XYZ 包络**（`feats["envelope"]`）：
        #   ① 型材记录里没有 `envelope` 字段，退化兜底会丢掉轴向信息
        #      （实测 x118 被当成"沿 Z 长 118"，网格变成 118×101×80）；
        #   ② 也不能用 `__env_sorted`（降序），那样 argmax 永远是 0。
        env_raw = [float(v) for v in (feats.get("envelope")
                                      or real.get("envelope")
                                      or [max(sect), min(sect), L])]
        ai = max(range(3), key=lambda k: env_raw[k])
        others = [k for k in range(3) if k != ai]
        holes = [{"d": float(h["d"]), "axis": h["axis"],
                  "u": float(h["u"]), "v": float(h["v"]), "through": True}
                 for h in feats["cylinders"] if float(h["d"]) >= 2.0]
        # ⚠ 直写网格的前提是**孔与孔不相交**。实测 289 个型材里 267 个满足；
        #   22 个 EGT006（⌀30 圆型材）的孔会互相穿 —— 那批必须走布尔路径，
        #   否则两段圆柱壁互相穿插，拼出来的不是合法实体。
        if not RH.holes_intersect(feats["cylinders"]):
            return [Feature("extrude_holes", {
                "sec_a": env_raw[others[0]], "sec_b": env_raw[others[1]],
                "length": env_raw[ai], "axis": "XYZ"[ai],
                "base": -env_raw[ai] / 2.0, "holes": holes, "segments": 24,
                "ref_min": feats.get("bbox_min"),
                "ref_size": feats.get("envelope"),
            }, ADD, name="EGT 型材 %s 截面 %g×%g × 长 %g，实测 %d 个孔（直写网格）"
                         % (model, env_raw[others[0]], env_raw[others[1]],
                            env_raw[ai], len(holes)))]
    # 取不到实测孔位时退回"方管 + 预算孔"，并如实标注（实体仍摆在实测坐标系里）
    # ⚠ 型材记录里**没有** `envelope` 字段，包络只能从 STEP 特征记录取
    #   （`feats["envelope"]` 是原始轴序，`__env_sorted` 是降序、会丢轴向）。
    _env_raw, _cen = _measured_frame(p)
    if _env_raw is None:
        _env_raw = [float(v) for v in ((feats or {}).get("envelope")
                                       or (feats or {}).get("__env_sorted") or [])]
    if len(_env_raw) != 3:
        _env_raw = [max(sect), min(sect), L]
    if _cen is None:
        lo, hi = (feats or {}).get("bbox_min"), (feats or {}).get("bbox_max")
        _cen = ([(float(lo[i]) + float(hi[i])) / 2.0 for i in range(3)]
                if lo and hi else (0.0, 0.0, 0.0))
    _ai = max(range(3), key=lambda k: _env_raw[k])       # 实测长轴
    if sec_code.startswith("D"):
        D = float(sec_code[1:])
        fs = _cyl_features(D, _env_raw[_ai], "EGT 圆型材 ⌀%g×%g" % (D, _env_raw[_ai]),
                           axis="XYZ"[_ai], center=_cen)
    else:
        fs = _box_features(tuple(_env_raw),
                           "EGT 型材 %g×%g×%g（实测截面 × 实测长度）"
                           % tuple(_env_raw), center=_cen)
    _apply_real_holes(p, fs, budget=_EGT_HOLE_BUDGET)
    return fs


# ---------------------------------------------------------------------------
# 三、厂内标准件（按实测外形 + **实测孔位**建）
# ---------------------------------------------------------------------------
def _apply_real_holes(p, fs, budget=64, name_prefix="", skip_d=()):
    """按**实测孔位**在已有实体上加孔。

    这是"细节复现"的关键一步。第一轮迭代只用了包围盒 + 直径指纹，
    **孔的位置全丢了**，孔只能按预算平均摆 —— 外形对、细节完全不对
    （用户反馈："其他零件都只是一个大体形状"）。

    现在每个孔的直径/轴线/位置/深度都来自 STEP 实测（`kb/real_features.json`）：
      · 轴线支持 X/Y/Z（型材的孔三个方向都有）；
      · 直径/位置/深度按实测值，沉孔与通孔按真实轴向范围区分；
      · 超过 `budget` 的孔不静默丢弃 —— 数量写进提示，由上层如实上报。
    """
    from . import realholes as RH
    real = _real_of(p)
    if _trust_of(p) != "exact":
        # 装配导出的件：孔位坐标与外形一起被骨架污染了，**不能用**。
        # 这种情况下宁可退回"外形不可得"的诚实说法，也不拿脏坐标去建孔。
        return 0, {"holes": [], "dropped": 0, "total": 0, "envelope": None,
                   "source": None, "skipped": "装配导出，孔位不可信"}
    # ⚠ 这里**不要**按"多实体件一律不用孔位"来一刀切。
    #   我试过那条规则：它确实挡住了 MGPA50（4 实体）与吊环 M24（2 实体）的坏孔，
    #   但同时把**本来完全正确**的四个族（支座 3 实体、阀岛 9 实体、补高台、
    #   接近开关）一起废掉了 —— 实测它们的复现度从 100% 掉到 32%~55%。
    #   真正的原因是按件不同的，逐件处理：
    #     · 装配件骨架污染（UNIVER 气缸）→ 由上面的 trust 判断挡住；
    #     · "圆柱其实是实体不是孔"（吊环）→ 由该族自己的建模器处理，
    #       根本不调用本函数；
    #     · 内核容量不够（MGPA50 撞面数上限）→ 由各族的孔预算控制。
    plan = RH.hole_plan(_model_token(p), budget=budget)
    made = 0
    for h in plan["holes"]:
        d = float(h["d"])
        if any(abs(d - s) < 0.05 for s in skip_d):
            continue
        axis = h["axis"]
        ai = "XYZ".index(axis)
        others = [k for k in range(3) if k != ai]
        kw = {"diameter": d, "axis": axis, "segments": 24}
        # 面内位置：孔特征用 x/y 表达（Z 轴时就是 x/y；X/Y 轴时是另外两轴）
        kw["x"] = float(h["u"])
        kw["y"] = float(h["v"])
        if h.get("through") or not h.get("depth"):
            kw["through"] = True
        else:
            kw["through"] = False
            kw["depth"] = float(h["depth"])
        fs.append(Feature("hole", kw, CUT,
                          name="%s实测孔 ⌀%g@(%g,%g) 轴%s%s"
                               % (name_prefix, d, h["u"], h["v"], axis,
                                  "通" if kw.get("through") else
                                  "深%g" % kw["depth"])))
        made += 1
    if plan["dropped"]:
        fs.append(Feature("box", {"length": 0.01, "width": 0.01, "height": 0.01,
                                  "corner": 0.0, "corner_mode": "fillet",
                                  "center": (0, 0, 0), "base": False,
                                  "axis": "Z", "enabled": False}, ADD,
                          name="（提示：真实圆柱面 %d 处，本次按预算建 %d 处）"
                               % (plan["total"], made)))
    return made, plan


#: 通用族的孔预算。
#: 旧内核实测：32 孔 → 5.4 万面 / 24 s，64 孔直接撞面数上限被中止 → 当时封顶 32。
#: A′ 局部布尔之后每刀只碰局部面（⌀30×1000 上 48 孔 72s → 6.8s），
#: 所以按**实测孔数**给足预算；只在极端多孔件上封顶，避免预览等待过久。
_GENERIC_HOLE_BUDGET = 96


def _generic_real_holes(p, hole_budget=_GENERIC_HOLE_BUDGET, name_prefix=""):
    """外形按实测建：**圆盘/圆柱类**用圆柱，**方块类**用长方体，再叠实测孔位。

    为什么要判形状：实测 `KMS-M14-030`（标牌）是 ⌀50×1 的**圆片**，
    旧版一律按包围盒建长方体，孔位再对也永远差一截 —— 实测这一件就是这样
    卡在 94% 上不去的。判据用的是实测数据本身：如果有一个圆柱的直径 ≈ 两个
    较小的包络尺寸、且它的轴向跨度 ≈ 最小的包络尺寸，那这个零件就是圆的。
    """
    from . import realholes as RH
    real = _real_of(p)
    env = p.get("__env_sorted") or real.get("envelope")
    if not env:
        env = [p.get("L") or 50.0, p.get("W") or 30.0, p.get("H") or 10.0]
    L, W, H = (float(env[0]), float(env[1]), float(env[2]))
    # ⚠ 实体必须摆在**实测坐标系**里（`_measured_frame`），孔位才是对的。
    #   判定"圆盘/方块"仍然用排序后的尺寸（与轴名无关），但**摆放**用原始轴序。
    env_raw, cen = _measured_frame(p)
    if env_raw is None:
        env_raw, cen = (L, W, H), (0.0, 0.0, 0.0)
    model = _model_token(p) or (p.get("code") or "")
    plan = RH.hole_plan(model, budget=hole_budget)
    disc_d = None
    for h in plan["holes"]:
        d = float(h["d"])
        # 圆片/圆盘：直径接近两个较小的外形尺寸
        if (abs(d - W) <= max(0.3, 0.02 * W)
                and abs(d - L) <= max(0.3, 0.02 * L)):
            disc_d = d
            break
    if disc_d:
        # 轴向 = 实测**最短的那一维**（标牌 ⌀50 跨度 1.002 = 厚度）
        mn = min(env_raw)
        ax = "XYZ"[env_raw.index(mn)]
        fs = _cyl_features(disc_d, mn,
                           "%s%s 实测圆盘 ⌀%g×%g" % (name_prefix, model,
                                                  disc_d, mn),
                           axis=ax, center=cen)
        made, _pl = _apply_real_holes(p, fs, budget=hole_budget,
                                      name_prefix=name_prefix,
                                      skip_d=(disc_d,))
        return fs
    fs = _box_features(env_raw, "%s%s 实测 %g×%g×%g"
                       % (name_prefix, model, env_raw[0], env_raw[1], env_raw[2]),
                       center=cen)
    made, plan = _apply_real_holes(p, fs, budget=hole_budget,
                                   name_prefix=name_prefix)
    if not made:
        # 没有实测孔位数据时退回旧做法（按直径指纹平均摆几个），并保持诚实
        cd = real.get("cyl_diameters") or {}
        for dia, cnt in sorted(cd.items(), key=lambda kv: -float(kv[0]))[:3]:
            n = min(int(cnt), 4)
            for j in range(n):
                x = L * (j + 1.0) / (n + 1.0) - L / 2.0
                fs.append(Feature("hole", {"diameter": float(dia),
                                           "through": True, "x": x, "y": 0.0,
                                           "axis": "Z", "segments": 24}, CUT,
                                  name="实测圆柱面 ⌀%s（真实 %s 处，位置未取到）"
                                       % (dia, cnt)))
    return fs


def _generic_real_box(p, hole_budget=_GENERIC_HOLE_BUDGET, name_prefix=""):
    return _generic_real_holes(p, hole_budget=hole_budget,
                               name_prefix=name_prefix)


def build_locator_pin_real(p):
    """定位销：按**实测台阶的直径与轴向范围**逐段建（不是平均分段）。

    实测 KMS-M01-P097-A20 的 4 个圆柱：⌀22（法兰）、⌀9.7（定位段）、
    ⌀10、⌀5.4（尾段），每个都有真实的 z0~z1。旧版按"总长均分"排台阶，
    直径对、**段长不对** —— 那正是"大体形状对、细节没有"的一种。
    """
    from . import realholes as RH
    real = _real_of(p)
    env = real.get("envelope") or [p.get("D") or 22.0, p.get("D") or 22.0,
                                   p.get("L") or 60.0]
    total = float(max(env))
    plan = RH.hole_plan(_model_token(p), budget=8)
    segs = []
    for h in plan["holes"]:
        z0, z1 = h.get("z0"), h.get("z1")
        if z0 is None or z1 is None or abs(z1 - z0) < 0.1:
            continue
        a, b = (float(z0), float(z1)) if z0 < z1 else (float(z1), float(z0))
        segs.append((float(h["d"]), a, b))
    if segs:
        # 按实测 z0 从下往上排。
        # ⚠ 实测段位要**归一化到真实总长**：提取器给出的 z0~z1 是由端面圆配对推出来的，
        #   存在系统性偏移 —— 实测 KMS-M01-P097-A20 的四段只覆盖 -28~20（48 mm），
        #   而 STEP 实测总长是 62.308；直接照搬会让模型短 14 mm，
        #   把本来 100% 的外形判据打回 94.6%（实测踩到过）。
        #   做法：保留实测的**相对比例**，整体仿射映射到 [0, total]。
        zmin = min(a for _d, a, _b in segs)
        zmax = max(b for _d, _a, b in segs)
        span = zmax - zmin
        if span > 1.0 and total > span + 0.5:
            k = total / span
            segs = [(d, (a - zmin) * k, (b - zmin) * k) for d, a, b in segs]
        segs.sort(key=lambda s: s[1])
        fs = []
        for d, a, b in segs:
            fs.append(Feature("cylinder", {"radius": d / 2.0, "height": b - a,
                                           "center": (0, 0, (a + b) / 2.0),
                                           "base": False, "axis": "Z",
                                           "segments": 48}, ADD,
                              name="实测台阶 ⌀%g×%g（z %g~%g）"
                                   % (d, b - a, a, b)))
        return fs
    # 取不到实测段位时退回"按直径排序均分"，并如实标注
    cd = real.get("cyl_diameters") or {}
    dias = sorted((float(k) for k in cd), reverse=True) or [22.0, 10.0]
    fs, z = [], total
    for dia in dias[:4]:
        h = min(total / max(1, min(len(dias), 4)), z)
        if h <= 0:
            break
        z -= h
        fs.append(Feature("cylinder", {"radius": dia / 2.0, "height": h,
                                       "center": (0, 0, z + h / 2.0),
                                       "base": False, "axis": "Z",
                                       "segments": 48}, ADD,
                          name="台阶 ⌀%g×%g（段位未取到，按均分）" % (dia, h)))
    return fs


def build_shim_real(p):
    """垫片/垫块：实测 长×宽×厚 + **实测孔位**（实体摆在实测坐标系里）。"""
    real = _real_of(p)
    env = real.get("envelope") or [p.get("L") or 50.0, p.get("W") or 20.0,
                                   p.get("t") or 5.0]
    L, W, t = [float(v) for v in env]
    env_raw, cen = _measured_frame(p)
    if env_raw is None:
        env_raw, cen = (L, W, t), (0.0, 0.0, 0.0)
    fs = _box_features(env_raw, "垫片垫块 实测 %g×%g×%g"
                       % (env_raw[0], env_raw[1], env_raw[2]), center=cen)
    made, _plan = _apply_real_holes(p, fs, budget=16)
    if not made:
        cd = real.get("cyl_diameters") or {}
        for dia, cnt in sorted(cd.items(), key=lambda kv: -float(kv[0]))[:2]:
            n = min(int(cnt), 6)
            for j in range(n):
                x = L * (j + 1.0) / (n + 1.0) - L / 2.0
                fs.append(Feature("hole", {"diameter": float(dia),
                                           "through": True, "x": x, "y": 0.0,
                                           "axis": "Z", "segments": 24}, CUT,
                                  name="实测 ⌀%s（孔位未取到）" % dia))
    return fs


def build_manifold_real(p):
    """分气块：实测 长×65×35 + **实测孔位**（⌀11 沉孔 + ⌀6.6 通孔 + ⌀15 侧向气路）。

    实测 KMS-M15-G14-H06：12 个物理圆柱 = ⌀15×2（轴向 X 的侧气路）
    + ⌀11×8（沉孔，深 9）+ ⌀6.6×2（通孔）。旧版把孔平均摆在长边上，
    直径对、**位置全错** —— 那正是用户说的"细节都没有"。
    """
    real = _real_of(p)
    env = real.get("envelope") or [p.get("L") or 120.0, 65.0, 35.0]
    L, W, H = [float(v) for v in env]
    env_raw, cen = _measured_frame(p)
    if env_raw is None:
        env_raw, cen = (L, W, H), (0.0, 0.0, 0.0)
    fs = _box_features(env_raw, "分气块 实测 %g×%g×%g"
                       % (env_raw[0], env_raw[1], env_raw[2]), center=cen)
    made, _plan = _apply_real_holes(p, fs, budget=32)
    if not made:
        cd = real.get("cyl_diameters") or {}
        for dia, cnt in sorted(cd.items(), key=lambda kv: -float(kv[0]))[:3]:
            n = min(int(cnt), 8)
            for j in range(n):
                x = L * (j + 1.0) / (n + 1.0) - L / 2.0
                fs.append(Feature("hole", {"diameter": float(dia),
                                           "through": True, "x": x, "y": 0.0,
                                           "axis": "Z", "segments": 20}, CUT,
                                  name="气路孔 ⌀%s（孔位未取到）" % dia))
    return fs


# ---------------------------------------------------------------------------
# 四、外购件（装配导出：按圆柱面指纹建简化体，并写明边界）
# ---------------------------------------------------------------------------
def build_purchased_real(p):
    """外购件。

    分两种情况，**必须分开**：
      · 实测外形可信（单实体导出，`trust == exact`）
        → 外形按**实测包围盒**建，再叠加实测圆柱面。
      · 装配导出（`trust == assembly_scope`）
        → 实测包围盒是**装配体范围**（含 2720.50/1372.01 这类公共骨架坐标），
          不能用；按型号编码里的规格（缸径/行程）建简化体。

    第一版两种情况都用"最大圆柱当主体"，结果接近开关被建成
    71.5×12×12（比实测 65×15.011×13 大）—— **网格实测判据抓到了**。
    """
    real = _real_of(p)
    exact = real.get("trust") == "exact"
    cd = real.get("cyl_diameters") or {}
    model = p.get("code") or real.get("model") or ""
    if exact and real.get("envelope"):
        env = p.get("__env_sorted") or sorted(
            (float(v) for v in real["envelope"]), reverse=True)
        L, W, H = [float(v) for v in env]
        mn = min(L, W, H)
        env_raw, cen = _measured_frame(p)
        if env_raw is None:
            env_raw, cen = (L, W, H), (0.0, 0.0, 0.0)
        fs = _box_features(env_raw,
                           "%s 实测外形 %g×%g×%g"
                           % (model, env_raw[0], env_raw[1], env_raw[2]),
                           center=cen)
        # 实测圆柱面用**等长于最小边**的圆柱表示，并居中：
        # 这样它一定落在实测外形之内，绝不会把外形撑大。
        # （第一版按 `L*1.2` 建，接近开关被撑成 78×65×15 —— 网格实测判据抓到了。）
        # 实测圆柱面：**单实体**件按实测孔位建；**多实体**（外购气缸/传感器这类
        # 装配件）不建孔。
        # ⚠ 为什么外购件要区别对待：它们的实测圆柱面是 ⌀50 缸体、⌀20/⌀14 导杆、
        #   ⌀8 活塞杆这些**实体**，不是孔。拿它们当孔切在包络盒上，形状本身就错
        #   （实测 MGPA50 建出 17.8 万面、耗时 128 秒，外形虽然对但结构是假的）。
        #   而厂内标准件（支座/阀岛/补高台/接近开关）的圆柱面确实是孔，必须建 ——
        #   所以这条规则只用在**本族**，不做全局一刀切（试过全局版，把四个
        #   本来 100% 达标的族打回了 32%~55%）。
        ns = (real.get("n_solids")
              or (_pkg_of(p).get("real") or {}).get("n_solids"))
        if ns is not None and int(ns) > 1:
            made = 0
        else:
            made, _plan = _apply_real_holes(p, fs, budget=64)
        if not made:
            for dia, cnt in sorted(cd.items(), key=lambda kv: -float(kv[0]))[:3]:
                d = float(dia)
                if d > mn:
                    continue
                fs.append(Feature("cylinder", {
                    "radius": d / 2.0, "height": mn,
                    "center": (0.0, 0.0, 0.0), "base": False, "axis": "Z",
                    "segments": 24}, ADD,
                    name="实测圆柱面 ⌀%s（真实 %s 处，孔位未取到）" % (dia, cnt)))
        return fs
    # 装配导出：按型号规格建简化体
    dias = sorted((float(k) for k in cd), reverse=True)
    if not dias:
        dias = [float(p.get("bore") or p.get("D") or 50.0)]
    D = float(p.get("bore") or dias[0])
    L = float(p.get("L") or max(D * 2.0, 60.0))
    fs = _cyl_features(D, L, "%s 主体 ⌀%g×%g（型号规格 + 实测圆柱面）"
                       % (model, D, L), axis="Z")
    stroke = p.get("stroke")
    if stroke:
        fs.append(Feature("cylinder", {
            "radius": D * 0.55, "height": float(stroke),
            "center": (0.0, 0.0, L / 2.0 + float(stroke) / 2.0),
            "base": False, "axis": "Z", "segments": 32}, ADD,
            name="导杆/活塞杆 行程 %g" % float(stroke)))
    return fs


def build_lifting_eye_real(p):
    """吊环 GB/T 825：环体 + 螺杆，按**实测外形**建。

    实测（`GB825_88_M16`）外形 63×35×89.5，圆柱面 ⌀35 / ⌀16 / ⌀9.5。
    由此可**唯一**定出构造：
      · 环体 = ⌀63 圆柱，轴线沿 35 那一轴（厚 35），内孔 ⌀35（实测直径之一）
      · 螺杆 = ⌀16（GB/T 825 M16 的螺纹直径，也是实测直径之一）
      · 总高 89.5 = 63/2 + 螺杆长 58
    第一版按"最大圆柱当环体外径"建，量出来是 89.5×35×35 —— **网格实测判据
    当场抓到了这个错**（声明值那关是过的）。这里改成按实测外形反推。
    """
    real = _real_of(p)
    env = real.get("envelope") or [63.0, 35.0, 89.5]
    xs = sorted((float(v) for v in env), reverse=True)   # 89.5, 63, 35
    total_h, ring_d, ring_t = xs[0], xs[1], xs[2]
    cd = real.get("cyl_diameters") or {}
    dias = sorted((float(k) for k in cd), reverse=True)   # 35, 16, 9.5
    ring_hole = dias[0] if dias and dias[0] < ring_d else ring_d * 0.55
    rod_d = None
    for d in dias:
        if 0.15 * ring_d <= d <= 0.45 * ring_d:
            rod_d = d
            break
    if rod_d is None:
        rod_d = max(1.0, ring_d * 0.26)
    r_ring = ring_d / 2.0
    rod_len = max(1.0, total_h - r_ring)
    fs = [
        # 环体：轴线沿 Y，厚 = ring_t
        Feature("cylinder", {"radius": r_ring, "height": ring_t,
                             "center": (0.0, 0.0, 0.0), "base": False,
                             "axis": "Y", "segments": 48}, ADD,
                name="环体 ⌀%g×%g" % (ring_d, ring_t)),
        Feature("hole", {"diameter": ring_hole, "through": True,
                         "pos": "center", "axis": "Y", "segments": 48}, CUT,
                name="环体内孔 ⌀%g" % ring_hole),
        # 螺杆：从环心往上长，使总高 = 实测总高
        Feature("cylinder", {"radius": rod_d / 2.0, "height": rod_len,
                             "center": (0.0, 0.0, rod_len / 2.0),
                             "base": False, "axis": "Z", "segments": 32}, ADD,
                name="螺杆 ⌀%g×%g" % (rod_d, rod_len)),
    ]
    # ⚠ 这里**刻意不调用 `_apply_real_holes`**：吊环的实测圆柱面里，⌀52 是**环体
    #   本身的外圆**（实体，不是孔）。把它当孔去切，环体会被切开、外形从 124.5
    #   掉到 94.5（实测踩到过）。这一类零件的圆柱面"哪些是实体、哪些是孔"，
    #   得由该族的构造自己决定，不能交给通用规则。
    return fs


def _try_real_solid(p, name_prefix=""):
    """优先用**实测 B-Rep**建（外轮廓 = 真实的型腔/加强筋/台阶）。

    判据（可复核）：`kb/real_brep/<型号>.npz` 存在且三角面 > 0。
    取不到就返回 None，由调用方退回原来的做法 —— 不静默降级、不伪造。
    """
    from . import realsolid as RS
    model = _model_token(p)
    if not model or not RS.available(model):
        return None
    polys = RS.polys(model)
    if not polys:
        return None
    return [Feature("real_solid", {"model": model}, ADD,
                    name="%s实测 B-Rep 复刻（型腔/筋/台阶来自 STEP 实测）" % name_prefix)]


def _realsolid_on() -> bool:
    """是否启用"实测 B-Rep 复刻"（默认开；`AI_MODELER_REALSOLID=0` 关掉做 A/B）。"""
    import os as _os
    return _os.environ.get("AI_MODELER_REALSOLID", "1").lower() not in ("0", "off", "false")


def _prefer_real_solid(fn):
    """包一层：**有实测 B-Rep 就复刻真实外轮廓**，否则走原来的参数化构建器。

    为什么这么定（用户的目标是"所有零件的建模准确率"）：
      · 用实测数据的族（销座/支座/阀岛/连接件/型材/垫片…）：实测网格就是真实零件，
        外轮廓（型腔/筋/台阶/内腔）**严格精确**，比"包络盒+孔"高一个量级；
      · **标准件（内六角螺钉）不套这层** —— 它们是按 ISO/GB 参数算出来的，
        参数化构建才是真本事，而且复现度已经是 99.98%；
      · 取不到实测网格时静默走原路径（不是静默降级：特征名里会写明用的哪条路）。
    """
    def run(p):
        if _realsolid_on():
            fs = _try_real_solid(p)
            if fs:
                return fs
        return fn(p)
    run.__name__ = fn.__name__ + "_prefer_brep"
    return run


def build_real_generic(p):
    """兜底族：**优先按实测 B-Rep 复刻**（外轮廓真实）；没有再退回"包络长方体 + 实测孔位"。"""
    if _realsolid_on():
        fs = _try_real_solid(p)
        if fs:
            return fs
    return _generic_real_box(p)


BUILDERS = {
    #: ⚠ `内六角螺钉` **不套**实测 B-Rep：标准件走参数化（ISO 4762 / GB/T 97.1），
    #:   复现度已 99.98%，用实测网格反而失去"按标准算"的意义。
    "内六角螺钉": build_socket_bolt_real,
    "EGT型材": _prefer_real_solid(build_egt_profile_real),
    "EGT连接件": build_real_generic,
    "定位销": _prefer_real_solid(build_locator_pin_real),
    "销座": build_real_generic,
    "支座": build_real_generic,
    "支座(铝)": build_real_generic,
    "止块": build_real_generic,
    "分气块": _prefer_real_solid(build_manifold_real),
    "阀岛": build_real_generic,
    "标牌": build_real_generic,
    "补高台": build_real_generic,
    "气缸连接块": build_real_generic,
    "垫片垫块": _prefer_real_solid(build_shim_real),
    "夹紧气缸": _prefer_real_solid(build_purchased_real),
    "双导杆气缸": _prefer_real_solid(build_purchased_real),
    "吊环": _prefer_real_solid(build_lifting_eye_real),
    "接近开关": _prefer_real_solid(build_purchased_real),
}


def _bbox_from_real(p, default=(50.0, 50.0, 50.0)):
    """期望外形，**统一按从大到小排**。

    为什么排序：STEP 里的坐标轴与"长/宽/高"的中文语义没有必然对应关系，
    实测（如 `KMS-M05-010-100-D` 是 110×140×155）无法从几何判断哪个轴该叫"长"。
    验收因此按**尺寸集合**比对，不按轴名比对 —— 这样判据是可复核的，
    也不会因为我把轴名猜错而误判为"不命中"。

    ⚠ 多实体（装配导出）件的包围盒是**装配体范围**，不是零件外形，
    这里**不使用**它（实测该值是 2720×1372 这种荒谬数）。
    """
    env = p.get("__env_sorted")
    if env:
        return tuple(float(v) for v in env)
    real = _real_of(p)
    if real.get("envelope") and real.get("trust") != "assembly_scope":
        return tuple(sorted((float(v) for v in real["envelope"]), reverse=True))
    return tuple(sorted((float(p.get("L") or default[0]),
                         float(p.get("W") or default[1]),
                         float(p.get("H") or default[2])), reverse=True))


def bbox_real(p):
    return _bbox_from_real(p)


# ---------------------------------------------------------------------------
# 五、注册表：这些类别怎么被文本命中、需要哪些参数
# ---------------------------------------------------------------------------
_V = ["型号", "代号", "图号"]
_DIM = {"L": ["长度", "长"], "W": ["宽度", "宽"], "H": ["高度", "高"],
        "D": ["直径"], "t": ["厚度", "厚"]}

PARTS_EXT: Dict[str, dict] = {
    "内六角螺钉": dict(
        aliases=["内六角螺钉", "内六角螺栓", "内六角圆柱头螺钉", "杯头螺丝",
                 "内六角"],
        params=dict(_DIM, **{"d": ["螺纹规格", "规格", "公称直径"],
                             "bolt_dk": ["头径", "头直径"],
                             "bolt_head_k": ["头高", "头厚"],
                             "bolt_hex_s": ["对边", "六角对边"],
                             "code": _V}),
        build=build_socket_bolt_real, bbox=_bbox_from_real,
        note="内六角圆柱头螺钉（含弹垫+平垫三件套），尺寸取 CATIA 实测库"),
    "EGT型材": dict(
        aliases=["EGT型材", "EGT", "型材", "铝型材", "TUNKERS型材", "EGT铝型材"],
        params=dict(_DIM, **{"sec_w": ["截面", "截面尺寸", "边长"],
                             "sec_h": ["截面高"], "shape": ["系列", "结构"],
                             "code": _V}),
        build=_prefer_real_solid(build_egt_profile_real), bbox=_bbox_from_real,
        note="TUNKERS EGT 型材：截面与长度全部取 CATIA 实测（289 档已入库）"),
    "EGT连接件": dict(
        aliases=["EGT连接件", "EGT接头", "EGT连接", "VGT连接件", "TUNKERS连接件"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_real_generic), bbox=_bbox_from_real,
        note="TUNKERS EGT/VGT 连接件（实测 73 件已入库）"),
    "定位销": dict(
        aliases=["定位销", "销钉", "圆柱销"],
        params={"d": ["直径", "销径", "定位直径"], "L": ["长度", "长"],
                "D": ["台阶直径", "台径"], "h": ["台阶高度", "台高"],
                "code": _V},
        build=_prefer_real_solid(build_locator_pin_real), bbox=_bbox_from_real,
        note="定位销 KMS-M01：台阶直径/总长取 CATIA 实测（22 个型号已入库）"),
    "销座": dict(
        aliases=["销座", "定位销座", "销支座"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_real_generic), bbox=_bbox_from_real,
        note="销座 KMS-M02：外形取实测（13 个型号已入库）"),
    "支座": dict(
        aliases=["支座", "支撑座", "支架座"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_real_generic), bbox=_bbox_from_real,
        note="支座 KMS-M05：外形取实测（7 个型号已入库）"),
    "支座(铝)": dict(
        aliases=["铝支座", "支座(铝)", "铝合金支座", "铝支座座"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_real_generic), bbox=_bbox_from_real,
        note="铝支座 LZ01 系列：外形取实测（5 个型号已入库）"),
    "止块": dict(
        aliases=["止块", "限位块", "挡块"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_real_generic), bbox=_bbox_from_real,
        note="止块 KMS-M11：外形取实测（6 个型号已入库）"),
    "分气块": dict(
        aliases=["分气块", "分气排", "气路块", "分气板"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_manifold_real), bbox=_bbox_from_real,
        note="分气块 KMS-M15：长=20×H、宽 65、厚 35，⌀11 孔 = H+2（实测）"),
    "阀岛": dict(
        aliases=["阀岛", "电磁阀岛", "阀组"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_real_generic), bbox=_bbox_from_real,
        note="阀岛 KMS-M13：外形取实测（5 个型号已入库）"),
    "标牌": dict(
        aliases=["标牌", "铭牌", "标识牌"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_real_generic), bbox=_bbox_from_real,
        note="标牌 KMS-M14：外形取实测（3 个型号已入库）"),
    "补高台": dict(
        aliases=["补高台", "垫高台", "加高台"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_real_generic), bbox=_bbox_from_real,
        note="补高台 KMS-M05-020：外形取实测（2 个型号已入库）"),
    "气缸连接块": dict(
        aliases=["气缸连接块", "气缸接头块", "缸连接块"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_real_generic), bbox=_bbox_from_real,
        note="气缸连接块 KMS-M06：外形取实测（3 个型号已入库）"),
    "垫片垫块": dict(
        aliases=["垫片垫块", "垫块", "垫片", "调整垫片"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_shim_real),
        bbox=lambda p: _bbox_from_real(p, (50.0, 20.0, 5.0)),
        note="垫片垫块 KMS-M09：外形与 ⌀9 孔数取实测（16 个型号已入库）"),
    "夹紧气缸": dict(
        aliases=["夹紧气缸", "UNIVER气缸", "转角气缸"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_purchased_real), bbox=_bbox_from_real,
        note="UNIVER UCBH 夹紧气缸：按型号缸径 + 实测圆柱面指纹建简化体"),
    "手动夹紧气缸": dict(
        # ⚠ 必须排在"夹紧气缸"之前单独成类：`find_part` 是"谁出现得早取谁"，
        #   "手动夹紧气缸"里**包含**"夹紧气缸"子串，不单列的话 UCBM63 会被判成
        #   气动夹紧气缸 —— 实测就踩过这个坑（型号识别对了、类别错了，
        #   于是真实几何的覆盖层整段被跳过，外形退回默认值 50×50×50）。
        aliases=["手动夹紧气缸", "手动夹紧缸", "手动气缸"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_purchased_real), bbox=_bbox_from_real,
        note="UNIVER UCBM 手动夹紧气缸：按型号缸径 + 实测圆柱面指纹建简化体"),
    "双导杆气缸": dict(
        aliases=["双导杆气缸", "导杆气缸", "SMC气缸", "气缸"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_purchased_real), bbox=_bbox_from_real,
        note="SMC MGPA / UNIVER ULA 双导杆气缸：按实测圆柱面指纹建简化体"),
    "吊环": dict(
        aliases=["吊环", "吊环螺钉", "起重吊环"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_lifting_eye_real), bbox=_bbox_from_real,
        note="吊环 GB/T 825：外形取实测（3 个型号已入库）"),
    "接近开关": dict(
        aliases=["接近开关", "感应开关", "接近传感器", "BALLUFF"],
        params=dict(_DIM, **{"code": _V}),
        build=_prefer_real_solid(build_purchased_real), bbox=_bbox_from_real,
        note="BALLUFF BES 接近开关：外形取实测（3 个型号已入库）"),
}

#: 型号写了就能补齐的参数（缺了不算"参数缺失"）
CODE_ONLY_EXT = {
    "内六角螺钉": ("d", "L", "bolt_dk", "bolt_head_k", "bolt_hex_s", "bolt_hex_t"),
    "EGT型材": ("L", "sec_w", "sec_h", "shape"),
    "EGT连接件": ("L", "W", "H"),
    "定位销": ("d", "L", "D", "h"),
    "销座": ("L", "W", "H"),
    "支座": ("L", "W", "H"),
    "支座(铝)": ("L", "W", "H"),
    "止块": ("L", "W", "H"),
    "分气块": ("L", "W", "H"),
    "阀岛": ("L", "W", "H"),
    "标牌": ("L", "W", "H"),
    "补高台": ("L", "W", "H"),
    "气缸连接块": ("L", "W", "H"),
    "垫片垫块": ("L", "W", "t"),
    "夹紧气缸": ("L", "W", "H", "D"),
    "手动夹紧气缸": ("L", "W", "H", "D"),
    "双导杆气缸": ("L", "W", "H", "D"),
    "吊环": ("L", "W", "H", "D"),
    "接近开关": ("L", "W", "H", "D"),
}
