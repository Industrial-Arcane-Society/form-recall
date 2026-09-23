# -*- coding: utf-8 -*-
r"""渲染"实测几何参考图"：直接用 kb 里的实测数据（包围盒 + 每个圆柱面）建几何，
用**同一个渲染器、同一个视角**渲染成图，作为"真实零件长什么样"的图像基准。

⚠️ 为什么不用 CATIA 截图当基准：那些是**整窗截图**（深红渐变背景 + 左侧规格树），
   边缘提取里混着上千个噪声连通域 —— 实测"真配对 85.3% / 交叉配对 84.1%"，
   区分度只有 1.2 个百分点，**没有区分能力**。要拿截图当基准，必须白底重拍
   （`tools\capture_real_images.py` 已支持改背景）。这里改用**实测几何渲染图**，
   干净、可复核、且与"人眼看到的像不像"一致。

用法: python tools\render_real_reference.py [--limit N] [--group 族] [--all]
输出: 零件实测几何图\<族>\<型号>.png   与 kb\real_render_index.json
"""
from __future__ import annotations
import argparse, io, json, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ai_modeler import docmodel as DM, softrender as SR, kernel as K   # noqa: E402

KB = os.path.join(ROOT, "kb")
OUT = os.path.join(ROOT, "零件实测几何图")
SR.VIEWS["catia"] = (-45.0, 35.264)
MAGENTA = (255, 0, 255)
W, H = 640, 800


def load(name):
    with io.open(os.path.join(KB, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--group", default="")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    geo = {os.path.splitext(p["file"])[0].strip().lower(): p
           for p in load("real_geometry.json")["parts"]}
    feat = {k.strip().lower(): v for k, v in load("real_features.json").items()}
    with io.open(os.path.join(KB, "ai_render_index.json"), encoding="utf-8") as f:
        idx = json.load(f)
    items = [it for it in idx["items"] if it.get("ok")]
    if a.group:
        items = [it for it in items if a.group in it["group"]]
    if a.limit:
        items = items[:a.limit]
    print("生成实测几何参考图 %d 个…" % len(items))
    os.makedirs(OUT, exist_ok=True)
    out = []
    t0 = time.time()
    fail = 0
    for i, it in enumerate(items):
        model = it["model"]
        mk = model.strip().lower()          # 索引里的型号是小写，kb 里是原始大小写
        g = geo.get(mk)
        f = feat.get(mk + ".stp") or feat.get(mk)
        rec = {"model": model, "group": it["group"]}
        if not g or not f:
            fail += 1
            rec["ok"] = False
            rec["err"] = "kb 里没有实测几何"
            out.append(rec)
            continue
        try:
            bb = g["bbox"]
            lo, hi = bb["min"], bb["max"]
            cen = [(float(lo[k]) + float(hi[k])) / 2.0 for k in range(3)]
            size = [float(hi[k]) - float(lo[k]) for k in range(3)]
            fs = [  # 包围盒（实测坐标系）
                {"kind": "box", "params": {"length": size[0], "width": size[1],
                                           "height": size[2], "corner": 0.0,
                                           "corner_mode": "fillet",
                                           "center": tuple(cen), "base": False,
                                           "axis": "Z"}, "op": "add", "name": "实测外形"}
            ]
            doc = DM.Document()
            doc.add(DM.Feature("box", {"length": size[0], "width": size[1],
                                       "height": size[2], "corner": 0.0,
                                       "corner_mode": "fillet", "center": tuple(cen),
                                       "base": False, "axis": "Z"}, DM.ADD,
                               name="实测外形"))
            nh = 0
            for c in (f.get("cylinders") or []):
                d = float(c["d"])
                if d < 2.0:
                    continue
                ax = c["axis"]
                z0, z1 = c.get("z0"), c.get("z1")
                span = size["XYZ".index(ax)]
                through = (z0 is None or z1 is None
                           or abs(float(z1) - float(z0)) < 0.2
                           or abs(float(z1) - float(z0)) >= span - 1.0)
                kw = {"diameter": d, "axis": ax, "segments": 24,
                      "x": float(c["u"]), "y": float(c["v"]), "through": bool(through)}
                if not through:
                    kw["depth"] = abs(float(z1) - float(z0))
                doc.add(DM.Feature("hole", kw, DM.CUT,
                                   name="实测圆柱 ⌀%g" % d))
                nh += 1
            body = doc.build(max_faces=400000)
            if not body:
                raise RuntimeError("网格为空")
            rd = SR.Renderer(W, H)
            SR.BG_TOP = MAGENTA
            SR.BG_BOT = MAGENTA
            if not rd.render(body, view="catia", edges=True, margin=0.06):
                raise RuntimeError("渲染失败")
            rgb = bytes(rd.fb)
            mask = bytearray(W * H)
            for k in range(W * H):
                if abs(rgb[3*k] - 255) + rgb[3*k+1] + abs(rgb[3*k+2] - 255) > 24:
                    mask[k] = 1
            d2 = os.path.join(OUT, it["group"])
            os.makedirs(d2, exist_ok=True)
            png = os.path.join(d2, model + ".png")
            mpng = os.path.join(d2, model + "_mask.png")
            SR.write_png(png, W, H, rgb)
            mfb = bytearray()
            for v in mask:
                mfb += b"\xff\xff\xff" if v else b"\x00\x00\x00"
            SR.write_png(mpng, W, H, bytes(mfb))
            rec.update({"ok": True, "n_cyl": nh,
                        "img": os.path.relpath(png, ROOT),
                        "mask": os.path.relpath(mpng, ROOT)})
        except Exception as e:
            fail += 1
            rec.update({"ok": False, "err": "%s: %s" % (type(e).__name__, str(e)[:70])})
        out.append(rec)
        if (i + 1) % 25 == 0:
            print("  %d/%d  %.0fs 失败 %d" % (i + 1, len(items), time.time() - t0, fail), flush=True)
    with io.open(os.path.join(KB, "real_render_index.json"), "w", encoding="utf-8") as f:
        json.dump({"n": len(out), "fail": fail, "items": out}, f, ensure_ascii=False, indent=1)
    print("完成：%d 个，失败 %d，用时 %.0fs" % (len(out), fail, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
