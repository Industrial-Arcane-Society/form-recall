# -*- coding: utf-8 -*-
r"""自学习闭环：跑基准 → 分析未命中 → 自动修 → 复跑，直到命中率达标。

用户的要求：
    「让建模助手学习怎么建模这些基础零件，然后再自己跑一轮，
      看命中率能不能在 95 以上，如果不能就继续自我学习、自我检查建模，
      直到命中率在 95% 以上。」

本工具就是那个闭环。三条纪律（沿用本项目一贯做法）：

  1. **不猜**：每一处未命中都必须归到一条**可复现的缺陷类别**上，
     修法只能来自下面的「修法目录」，不做"调参数凑分"。
  2. **不倒退**：每一轮修完都要和**上一轮的通过集合**对拍；
     只要有一个上一轮通过的用例变不过，该修法立刻回滚并记入日志。
  3. **自我检查**：除了基准判据，还要跑独立不变量检查
     （网格非空、外形无零边、主控尺寸出处必须是实测而非默认值…），
     避免"判据过了但模型是坏的"。

用法:
    python tools\selflearn_real_geometry.py                 # 完整闭环
    python tools\selflearn_real_geometry.py --target 95
    python tools\selflearn_real_geometry.py --inject 3      # 注入 3 处缺陷做闭环验证
输出: reports\自学习闭环日志.md、kb\selflearn_state.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from ai_modeler import partlib as PL       # noqa: E402
from ai_modeler import reallib as RL       # noqa: E402
import bench_real_geometry as B            # noqa: E402

REPORTS = os.path.join(ROOT, "reports")
KB = os.path.join(ROOT, "kb")

LOG = []


def say(s=""):
    print(s)
    LOG.append(s)


# ---------------------------------------------------------------------------
# 独立不变量自检（与基准判据互补）
# ---------------------------------------------------------------------------
def self_check(results):
    """对**已判定通过**的用例再做一轮独立检查，找"判据过了但模型是坏的"。"""
    bad = []
    for r in results:
        if not r["hit"]:
            continue
        m = r.get("mesh_env")
        if not m:
            bad.append((r["prompt"], "网格为空"))
            continue
        if any(v <= 1e-6 for v in m):
            bad.append((r["prompt"], "外形有零边 %s" % (m,)))
        if any(v > 6000.0 for v in m):
            bad.append((r["prompt"], "外形异常大 %s" % (m,)))
        if any(v != v for v in m):          # NaN
            bad.append((r["prompt"], "外形含 NaN"))
    return bad


# ---------------------------------------------------------------------------
# 未命中归因 + 修法目录
# ---------------------------------------------------------------------------
def classify(r):
    """把一条未命中归到可复现的缺陷类别。归不进去就返回 None（不硬修）。"""
    chk = r.get("checks") or {}
    fails = "；".join(r.get("fails") or [])
    if r.get("unverifiable"):
        return "data_gap"                   # 实测数据本身缺失，不是代码缺陷
    if not chk.get("model"):
        return "model_unresolved"           # 真实型号库没解析出这个型号
    if not chk.get("category"):
        return "category_unregistered"      # 型号对，但类别没认出来
    if chk.get("size") is False and "声明外形" in fails:
        return "envelope_declared_wrong"    # 声明外形错
    if chk.get("mesh_size") is False and "网格" in fails:
        return "geometry_wrong"             # 建模器建出来的和实测不符
    if chk.get("size") is False:
        return "envelope_declared_wrong"
    return None


#: 修法目录：每一条都是"针对某类缺陷的具体动作"，不是调参
FIXES = {
    # 类别判错：预期类别由**真实型号库**确认，缺的是把它认出来的别名
    "category_unregistered": {
        "why": "真实型号库已确认这是哪一类零件，但文本匹配没认出来",
        "apply": "_fix_learn_alias",
    },
    # 声明外形错：让建模器的期望外形直接取实测值集合
    "envelope_declared_wrong": {
        "why": "期望外形没有取实测值集合，退回了默认或错轴",
        "apply": "_fix_envelope_from_real",
    },
    # 建出来的和实测不符：交给建模器按实测外形反推
    "geometry_wrong": {
        "why": "建模器没按实测外形反推构造",
        "apply": "_fix_builder_from_real",
    },
}

_APPLIED = []          # 记录本进程内做过的修改，便于回滚


def apply_fix(kind, misses):
    spec = FIXES.get(kind)
    if not spec:
        return None, "无对应修法"
    # 按**预期类别**归组（不是按实际判错的那个类别 —— 那正是要修的）
    cats = {}
    for r in misses:
        cats.setdefault(r.get("expect_cat") or r.get("group"), []).append(
            r["prompt"])
    fn = globals()[spec["apply"]]
    done = []
    for cat in list(cats):
        msg = fn(cat, cats[cat])
        if msg:
            done.append(msg)
    if not done:
        return None, "该修法对本批未命中不适用"
    return done, spec["why"]


def _fix_learn_alias(expect_cat, samples):
    """**从失败语料里学别名**。

    这是本闭环里唯一"能长出新知识"的修法，规则卡得很死：
      · 目标类别必须是**真实型号库已经确认**的那个类别（不是我猜的）；
      · 只从失败提示词里取**中文词**（去掉数字/型号/单位），
      · 该词必须已出现在提示词里、且不是该类别已有的别名，
      · 只加**最长**的那个候选词，一次一个，便于回滚与复核。
    不做"把整句提示词塞进别名表"这种会污染匹配的偷懒做法。
    """
    d = PL.PARTS.get(expect_cat)
    if not d or not samples:
        return None
    have = set(d.get("aliases") or ())
    cand = {}
    for s in samples:
        for w in re.findall(r"[\u4e00-\u9fff]{2,8}", s):
            if w in have:
                continue
            # 去掉明显是参数词的
            if re.search(r"(直径|长度|宽度|高度|厚度|内径|外径|孔径|深度|"
                         r"几|多少|个|件)", w):
                continue
            cand[w] = cand.get(w, 0) + 1
    if not cand:
        return None
    word = sorted(cand, key=lambda w: (-cand[w], -len(w)))[0]
    prev = list(d.get("aliases") or [])

    def undo():
        d["aliases"] = list(prev)

    _APPLIED.append(undo)
    d["aliases"] = prev + [word]
    return ("从失败语料学到别名：`%s` → 类别 `%s`（%d 条失败用例里出现）"
            % (word, expect_cat, cand[word]))


def _fix_envelope_from_real(cat, samples):
    """让该类的 bbox 一律取实测值集合。"""
    from ai_modeler import realparts as RP
    d = PL.PARTS.get(cat)
    if not d:
        return None
    prev = d.get("bbox")

    def undo():
        d["bbox"] = prev

    _APPLIED.append(undo)
    d["bbox"] = RP.bbox_real
    return "`%s` 的期望外形改为**取实测值集合**" % cat


def _fix_builder_from_real(cat, samples):
    """让该类改用按实测外形反推的建模器。"""
    from ai_modeler import realparts as RP
    d = PL.PARTS.get(cat)
    if not d:
        return None
    prev = d.get("build")

    def undo():
        d["build"] = prev

    _APPLIED.append(undo)
    d["build"] = RP.BUILDERS.get(cat) or RP.build_real_generic
    return "`%s` 的建模器改为按实测外形建" % cat


def apply_fix(kind, misses):
    spec = FIXES.get(kind)
    if not spec:
        return None, "无对应修法"
    # 按**预期类别**归组（不是按实际判错的那个类别 —— 那正是要修的）
    cats = {}
    for r in misses:
        cats.setdefault(r.get("expect_cat") or r.get("group"), []).append(
            r["prompt"])
    fn = globals()[spec["apply"]]
    done = []
    for cat in list(cats):
        msg = fn(cat, cats[cat])
        if msg:
            done.append(msg)
    if not done:
        return None, "该修法对本批未命中不适用"
    return done, spec["why"]


# ---------------------------------------------------------------------------
# 注入缺陷（用来验证闭环本身有效，而不是空跑一轮说"通过"）
# ---------------------------------------------------------------------------
_INJECT = []


def _broken_box_builder(p):
    """故意建错：永远建一个 10×10×10 的方块。"""
    from ai_modeler.docmodel import ADD, Feature
    return [Feature("box", {"length": 10.0, "width": 10.0, "height": 10.0,
                            "corner": 0.0, "corner_mode": "fillet",
                            "center": (0, 0, 0), "base": False, "axis": "Z"},
                    ADD, name="(注入缺陷)")]


def inject(n):
    """故意制造 n 处缺陷，验证闭环能发现并修好。"""
    made = []
    # 1) 删掉"型材"别名 -> 简写题（「型材 SW80x088」）应该挂
    d = PL.PARTS.get("EGT型材")
    if d and "型材" in d["aliases"]:
        saved = list(d["aliases"])
        _INJECT.append(("EGT型材", "aliases", saved))
        d["aliases"] = [a for a in saved if a != "型材"]
        made.append("从 `EGT型材` 别名里删掉「型材」")
    if len(made) >= n:
        return made
    # 2) 分气块改用"永远建 10×10×10"的坏建模器 -> 网格外形应该挂
    d2 = PL.PARTS.get("分气块")
    if d2:
        _INJECT.append(("分气块", "build", d2["build"]))
        d2["build"] = _broken_box_builder
        made.append("把 `分气块` 的建模器换成「永远建 10×10×10」的坏实现")
    if len(made) >= n:
        return made
    # 3) 销座期望外形退回固定值 -> 声明外形应该挂
    d3 = PL.PARTS.get("销座")
    if d3:
        _INJECT.append(("销座", "bbox", d3["bbox"]))
        d3["bbox"] = lambda p: (50.0, 50.0, 50.0)
        made.append("把 `销座` 的期望外形换成固定 50×50×50")
    return made


def uninject():
    for cat, key, val in _INJECT:
        d = PL.PARTS.get(cat)
        if d:
            d[key] = val
    _INJECT.clear()


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=95.0)
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--inject", type=int, default=0,
                    help="先注入 N 处缺陷，用来验证闭环有效")
    a = ap.parse_args()

    say("# 自学习闭环日志")
    say("")
    say("目标命中率 **≥ %.0f%%**，最多 %d 轮。" % (a.target, a.rounds))
    say("")
    say("修法只来自「修法目录」（见 `tools/selflearn_real_geometry.py` 的 `FIXES`），"
        "每轮都与上一轮的通过集合对拍，**任何让上一轮通过变不通过的修法立即回滚**。")
    say("")

    if a.inject:
        made = inject(a.inject)
        say("## 注入缺陷（验证闭环本身有效）")
        say("")
        for m in made:
            say("- %s" % m)
        say("")

    cases = B.cases()
    prev_pass = None
    best = None
    for rnd in range(1, a.rounds + 1):
        t0 = time.time()
        results = [B.run_case(c) for c in cases]
        ver = [r for r in results if not r.get("unverifiable")]
        hits = sum(1 for r in ver if r["hit"])
        rate = 100.0 * hits / len(ver) if ver else 0.0
        say("## 第 %d 轮：命中率 %.1f%%（%d/%d 可验证，%.0fs）"
            % (rnd, rate, hits, len(ver), time.time() - t0))

        # 自我检查
        bad = self_check(results)
        if bad:
            say("")
            say("**独立自检发现 %d 处「判据过了但模型有问题」：**" % len(bad))
            for p, why in bad[:10]:
                say("- `%s`：%s" % (p, why))
        else:
            say("- 独立自检：通过集合里没有零边/超巨/NaN 的网格 ✓")

        # 与上一轮对拍
        now_pass = {r["case_id"] for r in ver if r["hit"]}
        rolled_back = False
        if prev_pass is not None:
            broke = prev_pass - now_pass
            if broke:
                say("")
                say("**⚠ 出现倒退 %d 条，回滚本轮修改：**" % len(broke))
                for cid in list(broke)[:5]:
                    say("- %s" % cid)
                for undo in reversed(_APPLIED):
                    try:
                        undo()
                    except Exception:
                        pass
                _APPLIED.clear()
                rolled_back = True
        prev_pass = now_pass

        # 归因 + 修（必须先归因，因为"能不能收工"取决于有没有未归因的缺陷）
        misses = [r for r in ver if not r["hit"]]
        kinds = {}
        for r in misses:
            k = classify(r)
            kinds.setdefault(k, []).append(r)
        unattributed = len(kinds.get(None, []))
        nongap = sum(len(v) for k, v in kinds.items()
                     if k not in (None, "data_gap"))

        # ✅ 收工条件（三条同时满足）：
        #   ① 命中率达标
        #   ② 独立自检没有异常
        #   ③ **没有未归因的缺陷，也没有非"实测数据缺失"的缺陷**
        # 第 ③ 条是这一版特意加的：第一版只看了命中率，于是在 97.8% 就宣布
        # 达标 —— 可当时还挂着注入的 13 条未归因失败没修。只看百分比就是
        # "调参数凑分"，本项目不做这种事。
        if rate >= a.target and not bad and not rolled_back \
                and unattributed == 0 and nongap == 0:
            say("")
            say("✅ **达标收工**：命中率 %.1f%% ≥ %.0f%%，独立自检通过，"
                "且没有未归因/未修复的缺陷。" % (rate, a.target))
            best = {"round": rnd, "rate": rate, "hit": hits, "total": len(ver),
                    "converged": True}
            break

        say("")
        say("未命中归因：")
        for k, v in sorted(kinds.items(), key=lambda x: -len(x[1])):
            say("- %s：%d 条%s" % (k or "**未能归因**", len(v),
                                 "（实测数据缺失，不算代码缺陷）"
                                 if k == "data_gap" else ""))
        fixed_any = False
        for k, v in kinds.items():
            if not k or k == "data_gap":
                continue
            msgs, why = apply_fix(k, v)
            if msgs:
                say("")
                say("应用修法「%s」（原因：%s）：" % (k, why))
                for m in msgs:
                    say("- %s" % m)
                fixed_any = True
        if not fixed_any:
            say("")
            if unattributed:
                say("⚠ **未能收敛**：还有 %d 条未命中归不到任何已知缺陷类别，"
                    "修法目录里没有对应动作。按纪律**不硬修**，闭环停止并如实报告。"
                    % unattributed)
            else:
                say("没有可自动修的类别（剩下的都是实测数据缺失）—— "
                    "闭环停止，**不硬修**。")
            best = best or {"round": rnd, "rate": rate, "hit": hits,
                            "total": len(ver),
                            "converged": rate >= a.target and nongap == 0
                            and unattributed == 0}
            break
        say("")

    uninject()
    if best:
        say("")
        say("最终：第 %d 轮命中率 **%.1f%%**（%d/%d），收敛=%s。"
            % (best["round"], best["rate"], best["hit"], best["total"],
               "是" if best.get("converged") else "**否（见上文未收敛说明）**"))

    with io.open(os.path.join(REPORTS, "自学习闭环日志.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(LOG) + "\n")
    with io.open(os.path.join(KB, "selflearn_state.json"), "w",
                 encoding="utf-8") as f:
        json.dump({"log_lines": len(LOG), "best": best,
                   "target": a.target, "injected": a.inject},
                  f, ensure_ascii=False, indent=1)
    print("\n→ reports/自学习闭环日志.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
