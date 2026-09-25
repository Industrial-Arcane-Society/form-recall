# -*- coding: utf-8 -*-
r"""
U6：约束求解（草图约束 + 装配约束）。

升级文档口径：
    引入 `ConstraintSolver`（`x` 为草图自由度，`f(x)=0` 为约束方程，
    Newton / LM 迭代 + 冗余检测）：复杂件从"拼特征"升级为"解约束"。
    风险与回退：**先做"检测与报告"（是否过约束/欠约束），再做求解**；
    提供手动摆位兜底。

本模块的实现纪律（两条，都是为了让"解不出来"可见而不是静默给个错解）：

1. **先诊断，再求解。**
   `diagnose()` 会用数值雅可比的秩判断：
     · `under`    自由度 > 独立约束数  -> **欠约束**，解不唯一，**拒绝给解**；
     · `over`     独立约束数 > 自由度  -> **过约束**，列出相互冲突的约束；
     · `well`     恰好定解            -> 可以解。
   升级文档明确要求"不能随便给一个解"，所以欠约束/过约束一律**返回诊断而不返回
   伪解**，由调用方决定（问用户 / 提示哪几条冗余）。

2. **只用标准库做数值线性代数。**
   高斯消元（带部分主元）+ 数值雅可比 + Levenberg–Marquardt 阻尼，
   不引入 numpy。规模预期是"草图级"（几个到几十个自由度），够用且无依赖。

用法：
    from ai_modeler.core import constraints as CS
    prob = CS.Problem({"x": 0.0, "y": 0.0})
    prob.add("dist", {"x": 3.0, "y": 4.0}, 5.0)          # sqrt(x²+y²) = 5
    d = CS.diagnose(prob)
    if d["status"] == "well":
        sol = CS.solve(prob)
"""
from __future__ import annotations

import math
import sys
from typing import Callable, Dict, List, Optional, Sequence, Tuple


# --------------------------------------------------------------------------
# 线性代数（标准库，规模小）
# --------------------------------------------------------------------------
def solve_linear(A: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """高斯消元（部分主元）。奇异返回 None。"""
    n = len(A)
    if n == 0:
        return []
    M = [list(map(float, A[i])) + [float(b[i])] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(col + 1, n):
            f = M[r][col] / pv
            if f == 0.0:
                continue
            for c in range(col, n + 1):
                M[r][c] -= f * M[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = M[r][n] - sum(M[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / M[r][r]
    return x


def matrix_rank(A: List[List[float]], tol: float = 1e-9) -> int:
    """行阶梯化求秩（用于"独立约束数"的判断）。"""
    if not A:
        return 0
    M = [list(map(float, row)) for row in A]
    rows, cols = len(M), len(M[0]) if M else 0
    rank = 0
    for col in range(cols):
        piv = None
        best = tol
        for r in range(rank, rows):
            if abs(M[r][col]) > best:
                best, piv = abs(M[r][col]), r
        if piv is None:
            continue
        M[rank], M[piv] = M[piv], M[rank]
        pv = M[rank][col]
        for r in range(rows):
            if r != rank and abs(M[r][col]) > 0:
                f = M[r][col] / pv
                for c in range(col, cols):
                    M[r][c] -= f * M[rank][c]
        rank += 1
        if rank == rows:
            break
    return rank


# --------------------------------------------------------------------------
# 约束
# --------------------------------------------------------------------------
class Constraint(object):
    """一个标量约束方程 f(x)=0。

    `fn(x: dict) -> float` 返回残差。`label` 用于报"哪几条冲突了"。
    """

    def __init__(self, name: str, fn: Callable[[Dict[str, float]], float],
                 label: str = "", required: bool = True):
        self.name = name
        self.fn = fn
        self.label = label or name
        self.required = required

    def residual(self, x: Dict[str, float]) -> float:
        return float(self.fn(x))


class Problem(object):
    """一个约束问题：变量（带初值）+ 约束列表。"""

    def __init__(self, initial: Dict[str, float] = None):
        self.vars: Dict[str, float] = dict(initial or {})
        self.cons: List[Constraint] = []

    # ---- 变量 ----
    def add_var(self, name: str, value: float = 0.0):
        self.vars[name] = float(value)
        return self

    @property
    def names(self) -> List[str]:
        return list(self.vars.keys())

    def dof(self) -> int:
        """自由度 = 变量个数。"""
        return len(self.vars)

    # ---- 约束 ----
    def add(self, name: str, fn, target: float = 0.0, label: str = "",
            required: bool = True):
        """加一条 f(x) - target = 0 的约束。"""
        t = float(target)
        self.cons.append(Constraint(name, lambda x, f=fn: f(x) - t,
                                    label=label or name, required=required))
        return self

    def residuals(self, x: Dict[str, float]) -> List[float]:
        return [c.residual(x) for c in self.cons]


# --------------------------------------------------------------------------
# 雅可比（数值）
# --------------------------------------------------------------------------
def jacobian(prob: Problem, x: Dict[str, float], h: float = 1e-6):
    names = prob.names
    J = []
    for c in prob.cons:
        row = []
        for nm in names:
            xp = dict(x)
            step = h * max(1.0, abs(x[nm]))
            xp[nm] = x[nm] + step
            xm = dict(x)
            xm[nm] = x[nm] - step
            row.append((c.residual(xp) - c.residual(xm)) / (2.0 * step))
        J.append(row)
    return J


# --------------------------------------------------------------------------
# 诊断：先判断"能不能解"
# --------------------------------------------------------------------------
def diagnose(prob: Problem, tol: float = 1e-7) -> Dict[str, object]:
    """先诊断再求解。返回 {status, dof, n_con, rank, redundant, conflicts, note}。

    status:
        'well'       恰好定解（可解）
        'under'      欠约束（解不唯一）—— **不给解**
        'over'       过约束（约束互相冲突）—— **不给解**，并列出冲突约束
        'empty'      没有约束
    """
    dof = prob.dof()
    n = len(prob.cons)
    if n == 0:
        return {"status": "empty", "dof": dof, "n_con": 0, "rank": 0,
                "redundant": [], "conflicts": [],
                "note": "没有任何约束，%d 个自由度全部自由" % dof}
    x0 = dict(prob.vars)
    J = jacobian(prob, x0)
    rank = matrix_rank(J)
    res = prob.residuals(x0)

    info = {"dof": dof, "n_con": n, "rank": rank,
            "max_residual": round(max((abs(r) for r in res), default=0.0), 6)}

    # 冗余约束：去掉某一条后秩不变 -> 它对定解没有新信息
    redundant = []
    for i in range(n):
        Jr = J[:i] + J[i + 1:]
        if matrix_rank(Jr) == rank:
            redundant.append(prob.cons[i].label)

    if rank < dof:
        info.update({
            "status": "under", "redundant": redundant, "conflicts": [],
            "note": ("欠约束：自由度 %d > 独立约束数 %d，解不唯一。"
                     "按要求**不给解**，请补充 %d 条独立约束或指定某些变量为已知。"
                     % (dof, rank, dof - rank))})
        return info
    if n > rank:
        # 约束条数多于秩 -> 其中有互相冲突的（也可能是纯冗余）
        info.update({
            "status": "over", "redundant": redundant, "conflicts": redundant or
            [c.label for c in prob.cons],
            "note": ("过约束：%d 条约束只有 %d 条独立，存在冗余或冲突。"
                     "按要求**不给解**，请先决定保留哪几条（冗余候选见 redundant）。"
                     % (n, rank))})
        return info
    info.update({"status": "well", "redundant": redundant, "conflicts": [],
                 "note": "恰好定解（独立约束数 = 自由度 %d）" % dof})
    return info


# --------------------------------------------------------------------------
# 求解：Levenberg–Marquardt
# --------------------------------------------------------------------------
def solve(prob: Problem, max_iter: int = 200, tol: float = 1e-10,
          force: bool = False) -> Dict[str, object]:
    """解约束。**默认先诊断**：欠约束/过约束直接返回诊断，不给伪解。

    force=True 时才无视诊断强行迭代（给"手动摆位兜底"用），
    并且返回值里会标明 `forced=True`，避免被误当成可信结果。
    """
    d = diagnose(prob)
    if d["status"] in ("under", "over") and not force:
        return {"ok": False, "solved": False, "diagnosis": d,
                "x": dict(prob.vars), "residual": None,
                "note": d["note"]}
    if d["status"] == "empty":
        return {"ok": True, "solved": False, "diagnosis": d, "x": dict(prob.vars),
                "residual": 0.0, "note": d["note"]}

    names = prob.names
    x = dict(prob.vars)
    lam = 1e-3
    best = None
    for it in range(int(max_iter)):
        r = prob.residuals(x)
        cost = sum(v * v for v in r)
        if best is None or cost < best[0]:
            best = (cost, dict(x))
        if max((abs(v) for v in r), default=0.0) < tol:
            break
        J = jacobian(prob, x)
        # (JᵀJ + λI) dx = −Jᵀr
        m = len(names)
        JTJ = [[sum(J[k][i] * J[k][j] for k in range(len(J))) for j in range(m)]
               for i in range(m)]
        JTr = [sum(J[k][i] * r[k] for k in range(len(J))) for i in range(m)]
        stepped = False
        for _ in range(12):
            A = [row[:] for row in JTJ]
            for i in range(m):
                A[i][i] += lam * (1.0 + abs(JTJ[i][i]))
            dx = solve_linear(A, [-v for v in JTr])
            if dx is None:
                lam *= 10.0
                continue
            xn = {nm: x[nm] + dx[i] for i, nm in enumerate(names)}
            rn = prob.residuals(xn)
            cn = sum(v * v for v in rn)
            if cn < cost:
                x = xn
                lam = max(1e-12, lam * 0.3)
                stepped = True
                break
            lam *= 10.0
        if not stepped:
            break
    r = prob.residuals(x)
    cost = sum(v * v for v in r)
    max_abs = max((abs(v) for v in r), default=0.0)
    ok = max_abs < 1e-6
    return {"ok": ok, "solved": ok, "x": x, "residual": max_abs,
            "cost": cost, "iters": it + 1, "diagnosis": d,
            "forced": bool(force and d["status"] in ("under", "over")),
            "note": ("已收敛（最大残差 %.3g）" % max_abs) if ok
                    else ("未收敛（最大残差 %.3g）—— 请检查约束是否自相矛盾"
                          % max_abs)}


# --------------------------------------------------------------------------
# 常用约束的便捷构造（草图级）
# --------------------------------------------------------------------------
#  ⚠️ 两类辅助函数的参数形状**故意不同**，别混：
#     · `dist2(pa, pb, d)` / `angle_deg(pa, pb, deg)` —— 目标值**写在函数里**，
#       直接 `prob.add("...", dist2(A, B, 5.0))`（`add` 的 target 用默认 0）。
#     · `horizontal(pa, pb)` / `vertical(pa, pb)` —— 无目标值，同样直接 add。
#   早先在 __main__ 里按"先建函数再传 target"写，于是 `dist2` 少了 d 参数报错。
def dist2(pa, pb, d):
    """两点距离 = d（目标值写在函数里）。"""
    dd = float(d)

    def fn(x):
        ax, ay = _pt(x, pa)
        bx, by = _pt(x, pb)
        return math.hypot(bx - ax, by - ay) - dd
    return fn


def horizontal(pa, pb):
    """两点等高。"""
    def fn(x):
        return _pt(x, pb)[1] - _pt(x, pa)[1]
    return fn


def vertical(pa, pb):
    """两点同 x。"""
    def fn(x):
        return _pt(x, pb)[0] - _pt(x, pa)[0]
    return fn


def fixed(nm, value):
    """把一个变量钉住。"""
    return lambda x: x[nm] - float(value)


def angle_deg(pa, pb, deg):
    """线段 pa->pb 与水平方向成 deg 度（目标值写在函数里）。"""
    dg = float(deg)

    def fn(x):
        ax, ay = _pt(x, pa)
        bx, by = _pt(x, pb)
        return math.degrees(math.atan2(by - ay, bx - ax)) - dg
    return fn


def _pt(x, spec):
    """把 ('A',) 或 ('A','B') 或 dict 解析成坐标 (x, y)。"""
    if isinstance(spec, dict):
        return float(spec.get("x", 0.0)), float(spec.get("y", 0.0))
    if isinstance(spec, str):
        return float(x[spec + "x"]), float(x[spec + "y"])
    if isinstance(spec, (tuple, list)) and len(spec) == 2:
        a, b = spec
        if isinstance(a, str) and isinstance(b, str):
            return float(x[a]), float(x[b])
        return float(a), float(b)
    raise ValueError("无法解析点: %r" % (spec,))


# --------------------------------------------------------------------------
# 接进建模管线：把"解出来的点"变成真几何
# --------------------------------------------------------------------------
def closed_polygon(prob: Problem, pts, height: float = 10.0, tag: int = 0,
                   seg=None):
    """解约束 -> 得到闭合多边形截面 -> 直接建出棱柱网格。

    `pts` 是点的顺序（如 `[("ax","ay"), ("bx","by"), ("cx","cy")]`）。
    这是 U6 "复杂件从拼特征升级为解约束"的**最小可用落地**：
    截面由约束定出来（而不是人手敲坐标），几何一致性因此是可证明的
    （解出来的点代回约束，残差为 0 —— 见 `check_constraints.py` 的 F 组）。

    返回 (polys, result)。result 是 `solve()` 的返回值；
    欠约束/过约束时 polys 为 None，**不建一个错误的几何出来**。
    """
    res = solve(prob)
    if not res.get("ok"):
        return None, res
    x = res["x"]
    poly = []
    for spec in pts:
        px, py = _pt(x, spec)
        poly.append((px, py))
    if len(poly) < 3:
        res = dict(res, ok=False, note="点数少于 3，构不成截面")
        return None, res
    try:
        from ai_modeler.core import kernel as K
        # kernel 里没有 make_prism：拉伸多边形的正确入口是 make_twisted_prism
        # （扭转角 0 就是普通棱柱）。第一版我按印象写成 make_prism，直接 AttributeError。
        polys = K.make_twisted_prism(poly, float(height), 0.0, (0.0, 0.0, 0.0),
                                     tag, 1, 'Z')
        if not isinstance(polys, list):
            polys = [polys]
    except Exception as e:
        res = dict(res, ok=False, note="建几何失败: %s" % e)
        return None, res
    return polys, res


def verify_solution(prob: Problem, x: Dict[str, float], tol: float = 1e-6):
    """把解代回**全部**约束，返回 (是否满足, 最大残差, 逐条残差)。

    U6 的"几何一致性可证明"就落在这一步：不是"看起来对"，
    而是每条约束的残差都能报出来。
    """
    r = [c.residual(x) for c in prob.cons]
    mx = max((abs(v) for v in r), default=0.0)
    return (mx <= tol), mx, list(zip([c.label for c in prob.cons], r))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=== U6 约束求解演示 ===")

    # ① 欠约束：一个点两个自由度，只给一条距离约束
    p = Problem({"ax": 0.0, "ay": 0.0, "bx": 0.0, "by": 0.0})
    p.add("dist", dist2(("ax", "ay"), ("bx", "by"), 5.0), label="A-B 距离=5")
    print("\n[欠约束] 4 自由度 + 1 条约束:")
    print("  诊断:", diagnose(p)["status"], "-", diagnose(p)["note"])
    r = solve(p)
    print("  求解:", r["ok"], "|", r["note"])

    # ② 恰好定解：钉住 A，加水平 + 距离
    p2 = Problem({"ax": 0.0, "ay": 0.0, "bx": 3.0, "by": 1.0})
    p2.add("fix_ax", fixed("ax", 0.0), label="ax=0")
    p2.add("fix_ay", fixed("ay", 0.0), label="ay=0")
    p2.add("horiz", horizontal(("ax", "ay"), ("bx", "by")), label="A-B 水平")
    p2.add("dist", dist2(("ax", "ay"), ("bx", "by"), 5.0), label="A-B 距离=5")
    d2 = diagnose(p2)
    print("\n[恰定解] 4 自由度 + 4 条独立约束:")
    print("  诊断:", d2["status"], "-", d2["note"])
    r2 = solve(p2)
    print("  解:", {k: round(v, 6) for k, v in r2["x"].items()},
          "| 残差 %.2g" % (r2["residual"] or 0.0))

    # ③ 过约束：再加一条与距离重复的约束
    p3 = Problem({"ax": 0.0, "ay": 0.0, "bx": 3.0, "by": 1.0})
    p3.add("fix_ax", fixed("ax", 0.0), label="ax=0")
    p3.add("fix_ay", fixed("ay", 0.0), label="ay=0")
    p3.add("horiz", horizontal(("ax", "ay"), ("bx", "by")), label="A-B 水平")
    p3.add("dist", dist2(("ax", "ay"), ("bx", "by"), 5.0), label="A-B 距离=5")
    p3.add("dist_dup", dist2(("ax", "ay"), ("bx", "by"), 5.0),
           label="A-B 距离=5(重复)")
    d3 = diagnose(p3)
    print("\n[过约束] 同上但距离约束写了两遍:")
    print("  诊断:", d3["status"], "-", d3["note"])
    print("  冗余候选:", d3["redundant"])
    print("  求解:", solve(p3)["ok"], "（应按要求拒绝给解）")
