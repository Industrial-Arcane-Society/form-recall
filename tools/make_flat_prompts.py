# -*- coding: utf-8 -*-
r"""生成 kb/test_prompts_flat.txt —— 一行一条、可直接复制粘贴的测试文本。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import make_test_prompts as M      # noqa: E402

lines = []
for group, text, expect in M.CASES:
    if text is None:
        lines.append("")
        lines.append("# " + group)
        continue
    lines.append(text)

out = os.path.join(ROOT, "kb", "test_prompts_flat.txt")
with io.open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
n = sum(1 for g, t, e in M.CASES if t is not None)
print("已写入 %s（%d 条提示词，一行一条，空行与 # 开头是分组说明）" % (out, n))
