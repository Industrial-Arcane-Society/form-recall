# -*- coding: utf-8 -*-
r"""用 AI 提示词把每个零件建一遍，再"拍照"（渲染成图片）。

用户要求（原话）：「用 ai 提示词里面的数据，将每一个零件用 ai 提示词自动建模一次，
然后拍照。将拍的照片，与零件真实照片里面的零件真实图进行对比。」

做法：
  1. 提示词来自 `AI训练提示词\全部提示词.jsonl`（1828 条，含标准件/厂内件/型材）；
     只取"能对应到真实 STEP 实拍图"的那些（型号能在 `零件真实图片` 里找到同名 PNG）。
  2. 建模走**真管线**：`partlib.build_features_ex(提示词)` → `docmodel.Document.build()`。
  3. 渲染走 `softrender`，视角与真实照片**完全一致**：
     CATIA 等轴测视向 (1,−1,1)/√3、up=+Z → 方位角 −45°、俯仰角 35.264°。
  4. 背景故意用**品红 (255,0,255)**：这样"零件轮廓掩码"是精确的
     （掩码 = 非品红像素），比对时不用猜背景。

⚠️ 真实照片是**CATIA 整窗截图**（左边有规格树、上面有工具栏），所以比对脚本会先
   把三维视口裁出来 —— 渲染图这边同样保留边界留白，尺度才可比。

用法:
    python tools\render_ai_parts.py --limit 8          # 先试 8 个
    python tools\render_ai_parts.py --group 销座        # 只跑某族
    python tools\render_ai_parts.py --all              # 全部
输出:
    零件AI建模图片\<子目录>\<型号>.png        渲染图
    零件AI建模图片\<子目录>\<型号>_mask.png   轮廓掩码（比对用）
    kb/ai_render_index.json                   索引（提示词、渲染尺寸、耗时、失败原因）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: 渲染用粗网格（实测 B-Rep 精细网格 2 万面，纯 Python 渲染 25 s/件；
#  粗网格把全量渲染从 3 小时压到十几分钟，轮廓在 352×448 下看不出差别）
os.environ.setdefault("AI_MODELER_BREP_COARSE", "1")
sys.path.insert(0, ROOT)

from ai_modeler import partlib as PL           # noqa: E402
from ai_modeler import docmodel as DM          # noqa: E402
from ai_modeler import kernel as K             # noqa: E402
from ai_modeler import softrender as SR        # noqa: E402

REAL_DIR = os.path.join(ROOT, "零件真实图片")
OUT_DIR = os.path.join(ROOT, "零件AI建模图片")
KB = os.path.join(ROOT, "kb")
PROMPTS = os.path.join(ROOT, "AI训练提示词", "全部提示词.jsonl")

#: CATIA 等轴测：(1,-1,1)/√3，up = +Z → 方位 −45°、俯仰 35.264°
CATIA_AZ, CATIA_EL = -45.0, 35.264
SR.VIEWS["catia"] = (CATIA_AZ, CATIA_EL)
MAGENTA = (255, 0, 255)
#: 渲染分辨率：与真实照片同一长宽比（450×600 → 3:4）。
#  ⚠ 实测 B-Rep 复刻件的网格很大（型材 3 万三角面），640×800 要 19 s/件、
#  447 件接近 2 小时；320×400 只要 ~5 s，而比对是**尺度归一化**的，分辨率不影响结论。
W, H = 352, 448


def real_images():
    """型号(小写) → 真实图片相对路径。"""
    out = {}
    for dirpath, _dirs, files in os.walk(REAL_DIR):
        for fn in files:
            if fn.lower().endswith(".png"):
                rel = os.path.relpath(os.path.join(dirpath, fn), REAL_DIR)
                out[os.path.splitext(fn)[0].strip().lower()] = rel
    return out


def prompts():
    """读提示词库 → [(提示词, 型号, 族)]，只保留有实拍图的。"""
    real = real_images()
    seen = set()
    out = []
    with io.open(PROMPTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            text = str(d.get("prompt") or d.get("text") or "").strip()
            model = str(d.get("model") or d.get("key") or "").strip()
            if not text or not model:
                continue
            key = os.path.splitext(model)[0].strip().lower()
            rel = real.get(key)
            if rel is None:
                # 型材型号是 "EGT001 SW80x088"，提示词里写 "EGT型材 EGT001 SW80x088"
                for tail in text.replace("　", " ").split():
                    k2 = tail.strip().lower()
                    if k2 in real:
                        rel, key = real[k2], k2
                        break
            if rel is None or key in seen:
                continue
            seen.add(key)
            out.append((text, key, os.path.dirname(rel) or "根目录"))
    return out


def render_one(text, mask_path, img_path):
    """返回 (宽, 高, 前景占比)；失败抛异常。"""
    r = PL.build_features_ex(text)
    if not r or not r[1]:
        raise RuntimeError("提示词建不出模型")
    doc = DM.Document()
    for f in r[1]:
        doc.add(f)
    body = doc.build(max_faces=400000)
    if not body:
        raise RuntimeError("网格为空")
    rd = SR.Renderer(W, H)
    # 背景涂成品红，再按"非品红"取精确掩码
    SR.BG_TOP = MAGENTA
    SR.BG_BOT = MAGENTA
    ok = rd.render(body, view="catia", edges=True, margin=0.06)
    if not ok:
        raise RuntimeError("渲染失败")
    rgb = bytes(rd.fb)
    # 渲染器把背景按渐变填充；品红在上下两端一致，直接按颜色判
    mask = bytearray(W * H)
    n = 0
    for i in range(W * H):
        r0, g0, b0 = rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2]
        if abs(r0 - 255) + g0 + abs(b0 - 255) > 24:
            mask[i] = 1
            n += 1
    mfb = bytearray()
    for v in mask:
        mfb += b"\xff\xff\xff" if v else b"\x00\x00\x00"
    SR.write_png(img_path, W, H, rgb)
    SR.write_png(mask_path, W, H, bytes(mfb))
    return W, H, n / float(W * H)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--group", default="")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    cs = prompts()
    if a.group:
        cs = [c for c in cs if a.group in c[2]]
    if a.limit:
        cs = cs[:a.limit]
    print("待渲染 %d 个零件（有实拍图且提示词库里有型号）" % len(cs))
    os.makedirs(OUT_DIR, exist_ok=True)
    index = []
    t0 = time.time()
    fail = 0
    for i, (text, key, group) in enumerate(cs):
        d = os.path.join(OUT_DIR, group)
        os.makedirs(d, exist_ok=True)
        img = os.path.join(d, key + ".png")
        msk = os.path.join(d, key + "_mask.png")
        t1 = time.time()
        rec = {"prompt": text, "model": key, "group": group,
               "img": os.path.relpath(img, ROOT), "mask": os.path.relpath(msk, ROOT)}
        try:
            w, h, fg = render_one(text, msk, img)
            rec.update({"ok": True, "w": w, "h": h, "fg": round(fg, 4),
                        "sec": round(time.time() - t1, 2)})
        except Exception as e:
            fail += 1
            rec.update({"ok": False, "err": "%s: %s" % (type(e).__name__, str(e)[:80]),
                        "sec": round(time.time() - t1, 2)})
            if os.path.exists(img):
                os.remove(img)
        index.append(rec)
        if (i + 1) % 20 == 0 or (i + 1) == len(cs):
            print("  %d/%d  已用 %.0fs  失败 %d" % (i + 1, len(cs), time.time() - t0, fail),
                  flush=True)
    with io.open(os.path.join(KB, "ai_render_index.json"), "w", encoding="utf-8") as f:
        json.dump({"n": len(index), "fail": fail, "w": W, "h": H,
                   "view": {"az": CATIA_AZ, "el": CATIA_EL},
                   "items": index}, f, ensure_ascii=False, indent=1)
    print("完成：%d 个，失败 %d，用时 %.0fs" % (len(index), fail, time.time() - t0))
    print("→ 零件AI建模图片\\、kb/ai_render_index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
