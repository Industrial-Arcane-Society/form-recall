# -*- coding: utf-8 -*-
r"""
U6 验收自检：约束求解（先诊断，再求解）。

升级文档口径：
    引入 `ConstraintSolver`（x 为自由度，f(x)=0 为约束方程，Newton/LM + 冗余检测）。
    风险与回退：**先做"检测与报告"（是否过约束/欠约束），再做求解**；
    提供手动摆位兜底。

本脚本把它变成可复跑的断言：
  A) **欠约束**（秩 < 自由度）-> 诊断出来，且**拒绝给解**；
  B) **恰定解**（秩 = 自由度）-> 诊断 well，解出的点满足全部约束；
  C) **过约束**（约束数 > 秩）-> 诊断出来，列出冗余/冲突候选，且拒绝给解；
  D) 无约束 -> 'empty'，不当成错；
  E) `force=True` 时才强行迭代，且返回值**标明 forced**（不会被误当真解）；
  F) 解出的结果代回约束残差 ≈ 0（不是"看起来对"）；
  G) 线性代数自检：奇异矩阵返回 None，秩计算正确。

用法：python tools\check_constraints.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import constraints as CS      # noqa: E402

FAIL = []


def chk(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL.append(msg)


def make_under():
    """4 个自由度，只有 1 条距离约束 -> 欠约束。"""
    p = CS.Problem({"ax": 0.0, "ay": 0.0, "bx": 0.0, "by": 0.0})
    p.add("dist", CS.dist2(("ax", "ay"), ("bx", "by"), 5.0), label="A-B 距离=5")
    return p


def make_well():
    """4 个自由度，4 条独立约束 -> 恰定解（B 应在 (5,0)）。"""
    p = CS.Problem({"ax": 0.0, "ay": 0.0, "bx": 3.0, "by": 1.0})
    p.add("fix_ax", CS.fixed("ax", 0.0), label="ax=0")
    p.add("fix_ay", CS.fixed("ay", 0.0), label="ay=0")
    p.add("horiz", CS.horizontal(("ax", "ay"), ("bx", "by")), label="A-B 水平")
    p.add("dist", CS.dist2(("ax", "ay"), ("bx", "by"), 5.0), label="A-B 距离=5")
    return p


def make_over():
    p = make_well()
    p.add("dist_dup", CS.dist2(("ax", "ay"), ("bx", "by"), 5.0),
          label="A-B 距离=5(重复)")
    return p


def main():
    print("U6 约束求解 —— 验收自检")

    # ---------------------------------------------------------------- A
    print("\n[A] 欠约束 -> 诊断出来且拒绝给解")
    pu = make_under()
    du = CS.diagnose(pu)
    chk(du["status"] == "under",
        "诊断为欠约束（实得 %s，秩 %s / 自由度 %s）"
        % (du["status"], du.get("rank"), du.get("dof")))
    ru = CS.solve(pu)
    chk(ru["ok"] is False, "拒绝给解（ok=False）")
    chk("欠约束" in (ru.get("note") or ""), "说明了原因是欠约束")
    chk(ru.get("x") == pu.vars, "原变量未被悄悄改动")

    # ---------------------------------------------------------------- B
    print("\n[B] 恰定解 -> 解出来且满足约束")
    pw = make_well()
    dw = CS.diagnose(pw)
    chk(dw["status"] == "well", "诊断为恰定解（实得 %s）" % dw["status"])
    rw = CS.solve(pw)
    chk(rw["ok"] is True, "求解成功（残差 %.3g）" % (rw.get("residual") or 0.0))
    x = rw["x"]
    chk(abs(x["ax"]) < 1e-6 and abs(x["ay"]) < 1e-6, "A 被钉在原点")
    chk(abs(x["bx"] - 5.0) < 1e-6 and abs(x["by"]) < 1e-6,
        "B 解到 (5,0)（实得 %.6f, %.6f）" % (x["bx"], x["by"]))

    # ---------------------------------------------------------------- C
    print("\n[C] 过约束 -> 诊断出来、列出冗余、拒绝给解")
    po = make_over()
    do = CS.diagnose(po)
    chk(do["status"] == "over",
        "诊断为过约束（实得 %s，%s 条约束 / 秩 %s）"
        % (do["status"], do.get("n_con"), do.get("rank")))
    chk(bool(do.get("redundant")), "列出了冗余/冲突约束：%s" % (do.get("redundant"),))
    chk(any("重复" in s for s in (do.get("redundant") or [])),
        "冗余里包含那条重复的距离约束")
    ro = CS.solve(po)
    chk(ro["ok"] is False, "拒绝给解（ok=False）")

    # ---------------------------------------------------------------- D
    print("\n[D] 无约束 -> 'empty'，不当成错")
    pe = CS.Problem({"ax": 0.0, "ay": 0.0})
    de = CS.diagnose(pe)
    chk(de["status"] == "empty", "诊断为 empty（实得 %s）" % de["status"])
    re_ = CS.solve(pe)
    chk(re_["ok"] is True, "无约束不算失败（ok=True）")

    # ---------------------------------------------------------------- E
    print("\n[E] force=True 才强行迭代，且标明 forced")
    rf = CS.solve(pu, force=True)
    chk(rf.get("forced") is True, "返回值标明 forced=True（不会被误当真解）")
    # 强行解一个欠约束问题：允许解出来，但必须能看出"这是强行的"
    chk(rf["diagnosis"]["status"] == "under",
        "诊断仍如实报告 under（不因为强行解就改写诊断）")

    # ---------------------------------------------------------------- F
    print("\n[F] 解代回约束，残差必须 ≈ 0")
    res = [c.residual(x) for c in pw.cons]
    chk(max(abs(v) for v in res) < 1e-6,
        "4 条约束的最大残差 %.3g" % max(abs(v) for v in res))

    # ---------------------------------------------------------------- G
    print("\n[G] 线性代数自检")
    chk(CS.solve_linear([[2.0, 0.0], [0.0, 4.0]], [2.0, 8.0]) == [1.0, 2.0],
        "高斯消元解对角系统正确")
    chk(CS.solve_linear([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0]) is None,
        "奇异矩阵返回 None（不硬凑一个解）")
    chk(CS.matrix_rank([[1.0, 0.0], [0.0, 1.0]]) == 2, "单位阵秩 = 2")
    chk(CS.matrix_rank([[1.0, 2.0], [2.0, 4.0]]) == 1,
        "线性相关的两行秩 = 1（这正是'重复约束'能被抓到的原因）")

    # ---------------------------------------------------------------- 附加
    print("\n[附加] 角度约束与手动摆位兜底")
    pa = CS.Problem({"ax": 0.0, "ay": 0.0, "bx": 2.0, "by": 0.0})
    pa.add("fa", CS.fixed("ax", 0.0), label="ax=0")
    pa.add("fb", CS.fixed("ay", 0.0), label="ay=0")
    pa.add("d", CS.dist2(("ax", "ay"), ("bx", "by"), 10.0), label="长=10")
    pa.add("ang", CS.angle_deg(("ax", "ay"), ("bx", "by"), 30.0), label="30°")
    ra = CS.solve(pa)
    chk(ra["ok"] is True, "角度+长度约束可解（残差 %.3g）" % (ra.get("residual") or 0.0))
    if ra["ok"]:
        import math
        xx = ra["x"]
        ang = math.degrees(math.atan2(xx["by"], xx["bx"]))
        chk(abs(ang - 30.0) < 1e-4, "解出的角度 = %.4f°（期望 30°）" % ang)
        chk(abs(math.hypot(xx["bx"], xx["by"]) - 10.0) < 1e-6, "解出的长度 = 10")

    # ---------------------------------------------------------------- H
    print("\n[H] 接进建模管线：解约束 -> 真几何，且一致性可证明")
    pt = CS.Problem({"ax": 0.0, "ay": 0.0, "bx": 60.0, "by": 0.0,
                     "cx": 30.0, "cy": 40.0})
    pt.add("fa", CS.fixed("ax", 0.0), label="ax=0")
    pt.add("fb", CS.fixed("ay", 0.0), label="ay=0")
    pt.add("fc", CS.fixed("bx", 60.0), label="bx=60")
    pt.add("fd", CS.fixed("by", 0.0), label="by=0")
    pt.add("e1", CS.dist2(("ax", "ay"), ("cx", "cy"), 50.0), label="AC=50")
    pt.add("e2", CS.dist2(("bx", "by"), ("cx", "cy"), 50.0), label="BC=50")
    polys, res = CS.closed_polygon(pt, [("ax", "ay"), ("bx", "by"), ("cx", "cy")],
                                   height=8.0)
    chk(polys is not None and len(polys) > 0,
        "由约束解出的三角形建出了网格（%s 面）" % (len(polys) if polys else 0))
    if polys:
        ok_v, mx_v, rows = CS.verify_solution(pt, res["x"])
        chk(ok_v, "**一致性可证明**：全部约束最大残差 %.3g" % mx_v)
        chk(all(abs(v) < 1e-9 for _lb, v in rows),
            "6 条约束逐条残差均为 0")
        from ai_modeler import kernel as K
        bb = K.bbox(polys)
        sx, sy, sz = bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]
        chk(abs(sx - 60.0) < 1e-6 and abs(sy - 40.0) < 1e-6 and abs(sz - 8.0) < 1e-6,
            "包围盒 = 60×40×8（实得 %.2f×%.2f×%.2f）" % (sx, sy, sz))
    # 欠约束 -> 必须拒绝建几何（不产出一个错的形状）
    pq = CS.Problem({"ax": 0.0, "ay": 0.0, "bx": 60.0, "by": 0.0,
                     "cx": 30.0, "cy": 40.0})
    pq.add("e1", CS.dist2(("ax", "ay"), ("cx", "cy"), 50.0))
    polys2, res2 = CS.closed_polygon(pq, [("ax", "ay"), ("bx", "by"), ("cx", "cy")])
    chk(polys2 is None, "欠约束时**拒绝建几何**（不产出一个错的形状）")

    print("\n%s" % ("全部通过 ✅" if not FAIL else "失败 %d 项 ❌" % len(FAIL)))
    for m in FAIL:
        print("   -", m)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
