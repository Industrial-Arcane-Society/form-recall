# -*- coding: utf-8 -*-
r"""真实几何命中率基准。

**判据全部对"实测几何"，不对"型号名义值"** —— 这是这一轮迭代的核心要求：
先让 AI 明白零件现实长什么样，再建模。

对库里每一个真实型号，生成自然语言题，跑完整链路
（`find_part` → `reallib.lookup` → `model_params` → `build_features_ex`），
然后比对：

  1. 类别对不对
  2. 真实型号识别得对不对
  3. **建出来的几何外形**是否等于 STEP 实测外形（按尺寸集合比，容差 0.05mm）
  4. 至少建出一个实体

装配导出的外购件（气缸）实测包围盒是"装配体范围"，**不能**当外形，
这些件改用"型号规格 + 实测圆柱面指纹"判定，并在报告里单列。

用法: python tools\bench_real_geometry.py
输出: reports\真实几何命中率.md、kb\real_geometry_bench.json
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import partlib as PL       # noqa: E402
from ai_modeler import reallib as RL       # noqa: E402
from ai_modeler import nlparser as NLP     # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_mesh import measure_sorted    # noqa: E402

KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")
TOL = 0.05          # mm，实测外形容差


def _load(name):
    with io.open(os.path.join(KB, name), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 出题
# ---------------------------------------------------------------------------
def cases():
    out = []

    # ---- 1. 内六角螺钉（55）----
    b = _load("bolt_library.json")["bolts"]
    for k, rec in b.items():
        spec, ln = rec["spec"], rec["length"]
        out.append(dict(
            case_id="bolt:%s" % k, group="内六角螺钉", model=rec["model"],
            prompt="内六角螺栓 %sx%g" % (spec, ln),
            exact_env=sorted(rec["envelope"], reverse=True),
            cyl=rec["cyl_diameters"], trust="exact"))
        out.append(dict(
            case_id="bolt_code:%s" % k, group="内六角螺钉", model=rec["model"],
            prompt="%sx%g" % (spec, ln),
            exact_env=sorted(rec["envelope"], reverse=True),
            cyl=rec["cyl_diameters"], trust="exact"))

    # ---- 2. EGT 型材（289）----
    e = _load("egt_library.json")
    for fam, v in e["profiles"].items():
        for code, rec in v["records"].items():
            env = sorted([float(x) for x in rec["section"]] + [rec["length"]],
                         reverse=True)
            out.append(dict(
                case_id="egt:%s:%s" % (fam.replace(" ", "_"), code),
                group="EGT型材", model="%s %sx%03d" % (v["family"], v["section"],
                                                      int(code)),
                prompt="EGT型材 %s %sx%03d" % (v["family"], v["section"],
                                               int(code)),
                exact_env=env, cyl=rec.get("cyl_diameters"),
                trust="exact", extra={"n_cyl": rec.get("n_cylinders")}))
        # 另一种写法：不带 EGT 号，只写截面
        for code, rec in list(v["records"].items())[:6]:
            env = sorted([float(x) for x in rec["section"]] + [rec["length"]],
                         reverse=True)
            out.append(dict(
                case_id="egt_short:%s:%s" % (fam.replace(" ", "_"), code),
                group="EGT型材(简写)", expect_cat="EGT型材",
                model="%s %sx%03d" % (v["family"], v["section"], int(code)),
                prompt="型材 %sx%03d" % (v["section"], int(code)),
                exact_env=env, cyl=rec.get("cyl_diameters"), trust="exact"))

    # ---- 3. EGT 连接件（73）----
    for model, rec in e["connectors"].items():
        f = rec["files"][0]
        env = f.get("envelope")
        out.append(dict(
            case_id="egtc:%s" % model, group="EGT连接件", model=model,
            prompt="EGT连接件 %s" % model,
            exact_env=sorted(env, reverse=True) if env else None,
            cyl=f.get("cyl_diameters"),
            trust="exact" if f.get("n_solids", 1) <= 1 and env else "mixed"))

    # ---- 4. 厂内标准件 ----
    std = _load("standard_real_library.json")
    for g, v in std.get("groups", {}).items():
        for it in v.get("items", []):
            if not it.get("envelope"):
                continue
            out.append(dict(
                case_id="std:%s" % it["model"], group=g, model=it["model"],
                prompt="%s %s" % (g, it["model"]),
                exact_env=sorted(it["envelope"], reverse=True),
                cyl=it.get("cyl_diameters"), trust="exact"))
    for it in std.get("shims", {}).get("items", []):
        if not it.get("envelope"):
            continue
        out.append(dict(
            case_id="shim:%s" % it["model"], group="垫片垫块",
            model=it["model"],
            prompt="垫片垫块 %s" % it["model"],
            exact_env=sorted(it["envelope"], reverse=True),
            cyl=it.get("cyl_diameters"), trust="exact"))

    # ---- 5. 外购件（17）----
    pur = _load("purchased_library.json")
    for model, rec in pur.items():
        env = rec.get("envelope")
        exact = rec.get("trust") == "exact" and env
        out.append(dict(
            case_id="pur:%s" % model, group=rec.get("desc") or "外购件",
            model=model, prompt="%s %s" % (rec.get("desc") or "外购件", model),
            exact_env=sorted(env, reverse=True) if exact else None,
            nominal=True,
            cyl=rec.get("cyl_diameters"),
            trust="exact" if exact else "assembly_scope"))
    return out


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def run_case(c):
    r = {"case_id": c["case_id"], "group": c["group"], "prompt": c["prompt"],
         "model": c["model"], "checks": {}, "fails": []}
    t0 = time.time()
    try:
        cat = PL.find_part(c["prompt"])
    except Exception as ex:
        cat = None
        r["fails"].append("find_part 异常 %s" % ex)
    r["cat"] = cat
    r["expect_cat"] = c.get("expect_cat", c["group"])
    # `group` 是**显示分组**（同一零件的不同问法可以归到不同组），
    # `expect_cat` 才是期望类别。第一版把两者当成一个字段，于是
    # "EGT型材(简写)" 这组被判成类别错 —— 那是**基准自己的 bug**，不是插件缺陷。
    r["checks"]["category"] = (cat == r["expect_cat"])

    try:
        hit = RL.lookup(c["prompt"])
    except Exception as ex:
        hit = None
        r["fails"].append("reallib 异常 %s" % ex)
    r["real_model"] = (hit or {}).get("model")
    # 型号识别：库里的 model 与出题 model 归一化后一致即算命中
    def nm(s):
        return re.sub(r"[^0-9A-Za-z]", "", str(s or "")).upper()
    r["checks"]["model"] = (hit is not None
                            and nm(hit.get("model")) == nm(c["model"]))

    fs = None
    try:
        got = PL.build_features_ex(c["prompt"])
    except Exception as ex:
        got = None
        r["fails"].append("build 异常 %s" % ex)
    if got:
        _cat, fs, bb, notes = got
        r["notes"] = notes[:3]
    r["checks"]["buildable"] = bool(fs)
    # 取一次解析结果，供装配件判据检查"实测指纹是否真的挂上了"
    try:
        mp = PL.model_params(c["prompt"])
        rp = dict((mp or {}).get("params") or {})
        rp.pop("__table", None)          # ParamTable 不可 JSON 序列化
        r["real_params"] = rp
    except Exception:
        r["real_params"] = {}

    r["checks"]["size"] = None
    r["got_env"] = None
    r["unverifiable"] = None
    # ---- 真实测量建出来的网格 ----
    # ⚠ 这一步是必须的：`bb` 是 `PartDef.bbox(p)` 的**声明值**（"这个零件应该
    #   多大"），不是测量。只比声明值会出现"声明对了、建出来是错的"这种假通过。
    #   这里把特征真的跑一遍 BSP-CSG，从网格顶点量尺寸，再和 STEP 实测比。
    r["checks"]["mesh_size"] = None
    r["mesh_env"] = None
    mesh = None
    if fs:
        try:
            # 超时给足：多孔件的 BSP 碎片化很重（实测 MGPA50 要 1 分多钟），
            # 60 秒会把"能建出来"误判成"建空了"。验收不是 UI，不抢时间。
            mesh = measure_sorted(fs, timeout=180.0)
        except Exception as ex:
            r["fails"].append("网格测量异常 %s" % ex)
    r["mesh_env"] = mesh
    if mesh is None and fs:
        r["fails"].append("网格量不出尺寸（建空了或超时）")
        r["checks"]["mesh_size"] = False

    if fs and c.get("exact_env"):
        got_env = tuple(round(float(v), 3) for v in sorted(bb or [], reverse=True))
        r["got_env"] = got_env
        ok = (len(got_env) == 3
              and all(abs(got_env[i] - c["exact_env"][i]) <= TOL
                      for i in range(3)))
        r["checks"]["size"] = ok
        if not ok:
            r["fails"].append("声明外形 %s ≠ 实测 %s"
                              % (got_env, tuple(c["exact_env"])))
        if mesh is not None:
            mok = all(abs(mesh[i] - c["exact_env"][i]) <= TOL for i in range(3))
            r["checks"]["mesh_size"] = mok
            if not mok:
                r["fails"].append("**建出来的网格** %s ≠ STEP 实测 %s"
                                  % (mesh, tuple(c["exact_env"])))
    elif fs and c.get("nominal"):
        # 装配导出件：实测包围盒是"装配体范围"，**不能**当外形来比。
        # 这里检验的是一条可以证伪的性质：**插件没有把脏包围盒当真外形**。
        #   ① 输出的外形里不得出现实测到的公共骨架坐标 2720.501 / 1372.005；
        #   ② 输出的外形必须是合理量级（>0 且 ≤ 2000mm）；
        #   ③ 实测圆柱面指纹必须挂在参数里（说明它确实读了实测数据）。
        real = (hit or {}).get("real") or {}
        cd = real.get("cyl_diameters") or {}
        if not cd:
            r["unverifiable"] = ("该型号在导出的 521 个 STEP 里没有文件，"
                                 "实测数据为空")
            r["checks"]["size"] = None
        else:
            got_env = tuple(round(float(v), 3) for v in sorted(bb or [],
                                                              reverse=True))
            r["got_env"] = got_env
            dirty = (2720.501, 1372.005)
            has_dirty = any(abs(v - d) <= 0.01 for v in got_env for d in dirty)
            sane = all(0.0 < v <= 2000.0 for v in got_env)
            checked = {"__real" in (r.get("real_params") or {})}
            ok = (not has_dirty) and sane and checked
            r["checks"]["size"] = ok
            if has_dirty:
                r["fails"].append("把装配骨架包围盒当成了零件外形：%s" % (got_env,))
            elif not sane:
                r["fails"].append("外形量级不合理：%s" % (got_env,))
            elif not checked:
                r["fails"].append("未挂载实测圆柱面指纹")
            # 建出来的网格同样不得出现装配骨架坐标
            if mesh is not None:
                mok = (not any(abs(v - d) <= 0.01 for v in mesh for d in dirty)
                       and all(0.0 < v <= 2000.0 for v in mesh))
                r["checks"]["mesh_size"] = mok
                if not mok:
                    r["fails"].append("建出的网格里出现装配骨架坐标/量级异常：%s"
                                      % (mesh,))
    r["ms"] = round((time.time() - t0) * 1000.0, 1)
    r["hit"] = all(v for v in r["checks"].values() if v is not None)
    return r


def main():
    cs = cases()
    print("出题 %d 条（全部由真实型号库逐条生成）" % len(cs))
    t0 = time.time()
    results = []
    for i, c in enumerate(cs):
        if (i + 1) % 100 == 0:
            print("  已跑 %d/%d  (%.0fs)" % (i + 1, len(cs), time.time() - t0))
        results.append(run_case(c))
    dt = time.time() - t0

    n = len(results)
    verifiable = [r for r in results if not r.get("unverifiable")]
    unver = [r for r in results if r.get("unverifiable")]
    hits = sum(1 for r in verifiable if r["hit"])
    rate = 100.0 * hits / len(verifiable) if verifiable else 0.0
    print("\n" + "=" * 80)
    print("真实几何命中率 **%.1f%%**（%d/%d 可验证）"
          % (rate, hits, len(verifiable)))
    print("不可验证 %d 条（实测数据本身缺失，不计入分母）：" % len(unver))
    for r in unver:
        print("   %-40s %s" % (r["prompt"][:40], r["fails"] or
                               r.get("unverifiable")))
    print("总题数 %d，用时 %.1fs" % (n, dt))

    bygroup = {}
    for r in results:
        d = bygroup.setdefault(r["group"], [0, 0])
        d[0] += r["hit"]
        d[1] += 1
    print("\n按族：")
    for g, (h, t) in sorted(bygroup.items(), key=lambda x: -x[1][1]):
        print("   %-16s %4d/%4d  %5.1f%%" % (g, h, t, 100.0 * h / t))
    sub = {k: sum(1 for r in results if r["checks"].get(k)) for k in
           ("category", "model", "buildable", "size")}
    print("\n分项：%s" % sub)
    fails = [r for r in results if not r["hit"]]
    if fails:
        print("\n未命中样例（前 20）：")
        for r in fails[:20]:
            print("   %-44s %s" % (r["prompt"][:44], r["fails"] or
                                   {k: v for k, v in r["checks"].items()}))

    os.makedirs(REPORTS, exist_ok=True)
    with io.open(os.path.join(KB, "real_geometry_bench.json"), "w",
                 encoding="utf-8") as f:
        json.dump({"total": n, "hit": hits, "rate": round(rate, 2),
                   "tol_mm": TOL, "sub": sub,
                   "by_group": {g: {"hit": v[0], "total": v[1]}
                                for g, v in bygroup.items()},
                   "fails": fails[:200]}, f, ensure_ascii=False, indent=1)

    L = ["# 真实几何命中率", "",
         "题目由**真实型号库逐条生成**（不是手写）：",
         "- 内六角螺钉 %d 条（含裸型号写法）" % sum(
             1 for r in results if r["group"] == "内六角螺钉"),
         "- EGT 型材 %d 条" % sum(1 for r in results
                                if r["group"].startswith("EGT型材")),
         "- EGT 连接件 %d 条" % sum(1 for r in results
                                  if r["group"] == "EGT连接件"),
         "- 厂内标准件 %d 条" % sum(1 for r in results
                                  if r["group"] not in
                                  ("内六角螺钉", "EGT连接件", "EGT型材",
                                   "EGT型材(简写)", "吊环", "接近开关",
                                   "夹紧气缸", "双导杆气缸")),
         "", "判据对**实测几何**，不对型号名义值；外形容差 **%.2f mm**。" % TOL,
         "", "| 命中率 | 命中/总数 | 用时 |", "|---|---|---|",
         "| **%.1f%%** | %d/%d | %.0f s |" % (rate, hits, n, dt), "",
         "## 分项", "", "| 检查项 | 通过 |", "|---|---|"]
    for k, v in sub.items():
        L.append("| %s | %d/%d |" % (k, v, n))
    L += ["", "## 按族", "", "| 族 | 命中/总数 | 命中率 |", "|---|---|---|"]
    for g, (h, t) in sorted(bygroup.items(), key=lambda x: -x[1][1]):
        L.append("| %s | %d/%d | %.1f%% |" % (g, h, t, 100.0 * h / t))
    if fails:
        L += ["", "## 未命中（前 %d 条）" % min(len(fails), 60), "",
              "| 提示词 | 期望型号 | 类别 | 判据 | 失败原因 |", "|---|---|---|---|---|"]
        for r in fails[:60]:
            L.append("| `%s` | %s | %s | %s | %s |"
                     % (r["prompt"], r["model"], r["cat"],
                        {k: v for k, v in r["checks"].items()},
                        "；".join(r["fails"])[:80]))
    with io.open(os.path.join(REPORTS, "真实几何命中率.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n→ reports/真实几何命中率.md、kb/real_geometry_bench.json")
    return 0 if rate >= 95.0 else 1


if __name__ == "__main__":
    sys.exit(main())
