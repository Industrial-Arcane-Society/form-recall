# -*- coding: utf-8 -*-
r"""
文献采集（公开可访问来源）：为"可升级方向"文档收集**可核实**的方法学依据。

说明（如实交代）：
  · kns.cnki.net（知网主检索）无头访问返回"安全验证"；search.cnki.com.cn 的检索结果是
    JS 实时渲染，脚本抓不到结果条目 —— 因此本脚本**不伪造知网题录**。
  · 改为抓取可公开访问、且与知网论文同源的学术来源（arXiv / ACL / MDPI / 期刊 PDF），
    重点是"文本->CAD 参数化/特征序列"这条主线的方法与指标。
  · 知网上的中文论文请用你已登录的浏览器按 kb/papers_cn.md 里的检索式核对。

输出：
  kb\papers_intl.json   结构化文献（标题/来源/链接/关键方法/要点摘录）
  kb\papers_intl.md     人可读清单

运行: python tools\crawl_papers_intl.py
"""
from __future__ import annotations

import html as htmllib
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "kb")
OUT_JSON = os.path.join(KB, "papers_intl.json")
OUT_MD = os.path.join(KB, "papers_intl.md")

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# 每篇: 标题 / 来源 / 链接 / 与本地代码的对应关系 / 我们要抄的方法点
PAPERS = [
    dict(
        title="Automated CAD Modeling Sequence Generation from Text Descriptions "
              "via Transformer-Based Large Language Models",
        venue="ACL 2025 (Long Papers)",
        url="https://aclanthology.org/2025.acl-long.1054/",
        topic="A. 文本 -> CAD 建模序列",
        why="把自然语言映射成**带顺序的建模操作序列**（而不是一步生成网格），"
            "与本项目的“中文描述 -> 特征树”完全同构；它的评测口径（序列级/参数级/几何级）"
            "可以直接搬来做我们的验收指标",
    ),
    dict(
        title="CAD-Llama: Leveraging Large Language Models for Computer-Aided Design "
              "Parametric 3D Generation",
        venue="CVPR 2025",
        url="https://openaccess.thecvf.com/content/CVPR2025/papers/"
            "Li_CAD-Llama_Leveraging_Large_Language_Models_for_Computer-Aided_Design_"
            "Parametric_3D_CVPR_2025_paper.pdf",
        topic="A. 文本 -> CAD 建模序列",
        why="核心思想:**把 CAD 命令序列当成语言来建模**（含参数占位），"
            "用 LLM 生成可编辑的**参数化**结果；对我们的启示是把“提示词”编译成"
            "规范化 DSL（草图->约束->拉伸->切除->修饰），而不是靠关键词替换",
    ),
    dict(
        title="Pointer-CAD v2: Plan-Then-Construct CAD Generation with "
              "Dimension-Aware Parametric Precision",
        venue="arXiv",
        url="https://ar5iv.labs.arxiv.org/html/2606.29301",
        topic="D. 尺寸精度",
        why="**Plan-Then-Construct（先规划再构造）+ 尺寸感知**：先输出建模计划，"
            "再把尺寸数值单独注入，避免“结构对、尺寸飘”。可直接用于我们"
            "「先出特征计划 -> 再填标准尺寸」的两段式解析",
    ),
    dict(
        title="Semantic Direct Modeling",
        venue="arXiv 2504.13893",
        url="https://ar5iv.labs.arxiv.org/html/2504.13893",
        topic="C. 特征识别与设计意图",
        why="语义直接建模：在**已有几何**上按语义定位并修改面/特征（“把这个面偏移 2mm”），"
            "对应我们的「参数页改尺寸即时重建」，可用它的面-特征映射提升可编辑性",
    ),
    dict(
        title="Labeling CAD Parts to capture Design Intent for LLMs through "
              "Machining Feature Recognition",
        venue="CIRP Design 2026 / Procedia CIRP",
        url="https://www.sciencedirect.com/science/article/pii/S2212827126007948",
        topic="C. 特征识别与设计意图",
        why="把 CAD 零件**标注成带设计意图的数据**（加工特征 = 孔/槽/圆角/倒角）再喂给 LLM；"
            "正是我们“提示词 -> 特征”对照表该有的形式：每个特征带类型、参数、依据",
    ),
    dict(
        title="MBD 技术在汽车尺寸工程数字化虚拟匹配中的运用",
        venue="《汽车工艺与材料》(知网/万方收录)",
        url="https://www.auto1950.com/cn/article/pdf/preview/10.16173/j.cnki.ame.2024.11.008.pdf",
        topic="B. 汽车零件精度",
        why="汽车行业的尺寸工程做法：**MBD（基于模型的定义）+ 虚拟匹配**，"
            "把公差、基准、测量点写进模型并做装配偏差分析。"
            "对我们的启示:给特征加**基准/公差/测量点**三元信息，才能进企业实测",
    ),
    dict(
        title="基于 CAD 制图的汽车发动机核心零部件维修建模与精度优化研究",
        venue="万方（期刊论文）",
        url="https://d.wanfangdata.com.cn/periodical/"
            "CiBQZXJpb2RpY2FsQ0hJU29scjkyMDI2MDYwMjE2MjUxORIObnJqcGoyMDI2MDcwMzMaCGlzM2xkaTNt",
        topic="B. 汽车零件精度",
        why="中文文献里汽车核心零部件**建模 + 精度优化**的典型思路："
            "先建参数化基准模型，再用实测数据反求修正量（误差补偿）",
    ),
    dict(
        title="Computational Intelligence-Driven Innovation in Vehicle Body "
              "Design Automation",
        venue="IEEE",
        url="https://ieeexplore.ieee.org/abstract/document/11196624",
        topic="B. 汽车零件精度",
        why="车身设计自动化的计算智能路线（含参数化 + 优化 + 代理模型），"
            "可用于论证“标准数据 + 优化”这条升级线的行业价值",
    ),
    dict(
        title="KPI-Conditioned Generative Design of Automotive Hood Inner Panels: "
              "A Two-Stage Retrieval-Generation Pipeline",
        venue="arXiv",
        url="https://arxiv.org/pdf/2608.22457v1",
        topic="E. 检索增强 / 两段式生成",
        why="**两段式 检索-生成**（先检索相近案例，再生成）—— 与我们"
            "「先查标准件库/手册数据，再生成几何」的做法同源，可作为 RAG 升级的依据",
    ),
    dict(
        title="A Multidisciplinary Design and Optimization (MDO) Agent Driven by "
              "Large Language Models",
        venue="arXiv 2511.17511",
        url="https://ar5iv.labs.arxiv.org/html/2511.17511",
        topic="A. 文本 -> CAD 建模序列",
        why="LLM 作为**调度器**调用专业求解器（多学科设计优化 Agent）；"
            "对应我们的 MCP 工具化路线：让 AI 调“带标准约束的建模工具”",
    ),
]


def fetch_text(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        raw = r.read()
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            html = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        html = raw.decode("utf-8", "ignore")
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = htmllib.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


# 想从论文里确认的方法关键词（抓到就记下来，作为"文献确实这么写"的证据）
EVIDENCE_KEYS = [
    "parameter", "sequence", "constraint", "feature recognition", "tolerance",
    "dimension", "chain-of-thought", "retrieval", "sketch", "extrude",
    "ablation", "accuracy", "IoU", "chamfer", "validity",
    "参数“, ”约束“, ”特征“, ”公差“, ”尺寸“, ”检索",
]


def main():
    out = []
    for p in PAPERS:
        rec = dict(p)
        rec["fetched"] = time.strftime("%Y-%m-%d %H:%M")
        try:
            txt = fetch_text(p["url"])
            rec["page_chars"] = len(txt)
            hits = [k for k in EVIDENCE_KEYS if k.lower() in txt.lower()]
            rec["evidence_keys"] = hits[:14]
            rec["excerpt"] = txt[:900]
            print('OK   %-58s %d chars, keyword hits %d'
                  % (p['title'][:56], len(txt), len(hits)))
        except Exception as e:
            rec["page_chars"] = 0
            rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:80])
            print("FAIL %-58s %s" % (p["title"][:56], rec["error"]))
        out.append(rec)
        time.sleep(1.0)

    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"_about": "公开可访问的文献（含方法要点与页面摘录），用于可升级方向论证",
                   "_note": "知网需登录且检索结果为 JS 渲染，本文件不含伪造的知网题录",
                   "count": len(out), "papers": out}, f, ensure_ascii=False, indent=1)

    md = ['# 文献清单（公开可访问来源）', '',
          '> 由 `tools/crawl_papers_intl.py` 抓取。知网主检索需登录、检索结果为 JS 渲染，',
          '> 脚本无法直接取得题录，因此本清单只收录能公开打开并核实的来源；',
          '> 知网上的中文论文请用 `kb/papers_cn.md` 里的检索式在已登录的浏览器里核对。', '']
    for p in out:
        md.append('## %s' % p['title'])
        md.append('- 来源：%s' % p['venue'])
        md.append('- 链接：%s' % p['url'])
        md.append('- 主题：%s' % p['topic'])
        md.append('- 为什么要看它：%s' % p['why'])
        if p.get('evidence_keys'):
            md.append('- 页面命中关键词：%s' % '、'.join(p['evidence_keys']))
        if p.get('error'):
            md.append('- 抓取结果：%s' % p['error'])
        if p.get('excerpt'):
            md.append('- 页面摘录：%s' % p['excerpt'][:300])
        md.append('')
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n已写出 %s / %s（%d 篇）" % (OUT_JSON, OUT_MD, len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
