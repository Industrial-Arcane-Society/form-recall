# -*- coding: utf-8 -*-
"""从 assets/aimodeler_source.png 生成程序图标 assets/aimodeler.ico。

为什么需要这个脚本而不是直接给个 ico：
    原始 logo 是一张 108×111 的截图，四周带白色虚线边框。直接缩放会把
    虚线一起做成图标边缘的白点，所以必须先按实测的行/列位置裁掉边框。
    这个「裁到第几行」是量出来的，不是猜的——下面 _detect_border 保留了
    当时用的判据，换一张源图时能直接复现。

用法：
    python tools/build_icon.py
"""
from __future__ import annotations

import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "aimodeler_source.png")
DST = os.path.join(ROOT, "assets", "aimodeler.ico")
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _is_light(pixel) -> bool:
    r, g, b = pixel[:3]
    return r > 200 and g > 200 and b > 200


def _detect_border(im: Image.Image) -> tuple[int, int, int, int]:
    """量出虚线边框的位置，返回裁切框 (left, top, right, bottom)。

    判据：虚线是点状，所以「整行/整列近白像素超过 30%」才算边框线；
    零散的亮点（原图右下角就有一个）不算，否则会把内容一起裁掉。
    """
    w, h = im.size
    px = im.load()
    threshold = 0.30

    def row_ratio(y):
        return sum(1 for x in range(w) if _is_light(px[x, y])) / w

    def col_ratio(x):
        return sum(1 for y in range(h) if _is_light(px[x, y])) / h

    top = next((y for y in range(h // 4) if row_ratio(y) > threshold), 0)
    bottom = next((y for y in range(h - 1, h - h // 4, -1) if row_ratio(y) > threshold), h - 1)
    left = next((x for x in range(w // 4) if col_ratio(x) > threshold), 0)
    right = next((x for x in range(w - 1, w - w // 4, -1) if col_ratio(x) > threshold), w - 1)
    # 边框线本身要裁掉，再各留 1px 余量
    return (left + 1, top + 1, right, bottom)


def main() -> int:
    if not os.path.isfile(SRC):
        print("缺少源图：%s" % SRC)
        return 1
    im = Image.open(SRC).convert("RGB")
    print("源图：", im.size)

    box = _detect_border(im)
    print("检测到边框，裁切框：", box)
    im2 = im.crop(box)
    print("裁后：", im2.size)

    # 补成正方形：用上半部背景色，避开底部文字与四角
    bg = im2.getpixel((im2.width // 2, max(1, im2.height // 5)))
    side = max(im2.size)
    canvas = Image.new("RGB", (side, side), bg)
    canvas.paste(im2, ((side - im2.width) // 2, (side - im2.height) // 2))

    # 先放大到 256 再打包：让 Windows 每个尺寸都有原生位图，
    # 而不是拿 48px 放大显示。源图本身只有 108px，256 档会偏软，这是源图限制。
    big = canvas.resize((256, 256), Image.LANCZOS)
    big.save(DST, format="ICO", sizes=SIZES)

    with Image.open(DST) as chk:
        print("已生成：%s（%d 字节）" % (DST, os.path.getsize(DST)))
        print("内含尺寸：", sorted(chk.info.get("sizes", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
