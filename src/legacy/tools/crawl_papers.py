# -*- coding: utf-8 -*-
r"""
中文文献采集：为中国知网（CNKI）检索页抓取论文题录与摘要，作为"可升级方向"的文献依据。

为什么用 search.cnki.com.cn：
  kns.cnki.net（知网主检索）是无头访问会被"安全验证"拦住的 JS 页面；
  而 search.cnki.com.cn 是知网空间的可抓取检索入口（本机实测 200）。
  本脚本**只抓公开的题录信息（标题/作者/来源/摘要）用于内部技术调研**，不下载全文。

输出：
  kb\papers_cn.json     结构化题录（含每篇的 URL 与命中关键词）
  kb\papers_cn.md       人可读清单（按主题分组，带链接与摘要）

运行: python tools\crawl_papers.py            # 用内置关键词表
      python tools\crawl_papers.py --fresh    # 忽略缓存重新抓
"""
from __future__ import annotations

import argparse
import html as htmllib
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "kb")
OUT_JSON = os.path.join(KB, "papers_cn.json")
OUT_MD = os.path.join(KB, "papers_cn.md")
CACHE = os.path.join(KB, "papers_cn_cache.json")

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# 主题 → 检索词（对应"提高提示词准确性 / 提高建模精度 / 可升级方向"三条线）
TOPICS = {
    "A. 自然语言/大模型驱动建模": [
        "大语言模型 CAD 建模",
        "自然语言 参数化建模 机械",
        "语义理解 三维模型 生成",
        "提示词 工程 建模 大模型",
    ],
    "B. 汽车零件建模与制造": [
        "汽车零部件 参数化建模",
        "汽车零件 三维建模 精度",
        "汽车覆盖件 尺寸工程 虚拟匹配",
        "车身 零件 建模 制造 精度控制",
    ],
    "C. 特征识别与设计意图": [
        "加工特征识别 设计意图",
        "CAD 特征 提取 参数化 重建",
        "STEP 模型 特征 识别 算法",
    ],
    "D. 精度/公差与误差控制": [
        "尺寸公差 建模 精度 分析",
        "几何误差 公差建模 机械",
        "MBD 尺寸工程 数字化",
    ],
    "E. 知识库/检索增强/标准件": [
        "标准件 参数化 图库 建模",
        "机械设计手册 数字化 知识库",
        "检索增强 生成 机械设计",
    ],
}


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        raw = r.read()
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def text_of(html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = htmllib.unescape(t)
    t = re.sub(r"[ \t\u3000]+", " ", t)
    return re.sub(r"\n{2,}", "\n", t).strip()


def parse_results(html: str, keyword: str):
    """从知网空间检索结果页里抽题录：标题 + 链接 + 摘要/来源段落。"""
    items = []
    # 结果块通常是 <div class="list-item"> 或 <div class="wz_content">，
    # 这里不依赖具体 class（页面会变），改用"标题带详情链接"的方式定位。
    for m in re.finditer(r'<a[^>]+href="(?P<href>[^"]*?(?:dbcode|kns|detail|KCMS)[^"]*?)"'
                         r'[^>]*>(?P<title>[^<]{6,120})</a>', html, re.I):
        title = htmllib.unescape(re.sub(r"\s+", " ", m.group("title"))).strip()
        href = htmllib.unescape(m.group("href"))
        if href.startswith("/"):
            href = "https://search.cnki.com.cn" + href
        if len(title) < 6 or title in ("详细", "查看", "下载", "收藏"):
            continue
        # 摘要: 标题之后 800 字里找"摘要"或较长的中文句子
        seg = html[m.end():m.end() + 1600]
        seg_txt = text_of(seg)
        abs_m = re.search(r"(?:摘\s*要[:：]?)(.{20,420})", seg_txt)
        src_m = re.search(r"(?:来源|出处|期刊|学位授予单位)[:：]?\s*([^\n]{2,60})", seg_txt)
        items.append({
            "title": title,
            "url": href,
            "abstract": (abs_m.group(1).strip() if abs_m else seg_txt[:200].strip()),
            "source": (src_m.group(1).strip() if src_m else ""),
            "keyword": keyword,
        })
    # 去重（同标题只留一条，保留更长的摘要）
    best = {}
    for it in items:
        k = it["title"]
        if k not in best or len(it["abstract"]) > len(best[k]["abstract"]):
            best[k] = it
    return list(best.values())


def crawl(fresh=False):
    cache = {}
    if os.path.exists(CACHE) and not fresh:
        try:
            with io.open(CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    out = []
    for topic, kws in TOPICS.items():
        for kw in kws:
            if kw in cache and cache[kw].get("items"):
                items = cache[kw]["items"]
                src = "缓存"
            else:
                url = ("https://search.cnki.com.cn/Search/Result?content="
                       + urllib.parse.quote(kw))
                try:
                    html = fetch(url)
                    items = parse_results(html, kw)
                    cache[kw] = {"url": url, "items": items,
                                 "fetched": time.strftime("%Y-%m-%d %H:%M")}
                    src = "在线"
                except Exception as e:
                    print("  抓取失败 %s -> %s: %s" % (kw, type(e).__name__, str(e)[:60]))
                    continue
                time.sleep(1.2)                 # 温和抓取
            print("  [%s] %-22s 命中 %d 篇" % (src, kw, len(items)))
            for it in items:
                it = dict(it)
                it["topic"] = topic
                out.append(it)
    # 全局去重
    uniq = {}
    for it in out:
        k = it["title"]
        if k not in uniq:
            uniq[k] = it
        else:
            for t in (it.get("topic", ""),):
                if t and t not in uniq[k].get("topic", ""):
                    uniq[k]["topic"] = (uniq[k].get("topic", "") + " / " + t).strip(" /")
    with io.open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    return list(uniq.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()
    print("开始采集（知网空间检索页，仅题录与摘要）：")
    papers = crawl(fresh=args.fresh)
    print("合计去重后 %d 篇" % len(papers))

    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"_about": "中文文献题录（知网空间检索页抓取，用于技术调研）",
                   "_source": "https://search.cnki.com.cn",
                   "_note": "只含公开题录与摘要，不含全文；引用请以知网原文为准",
                   "count": len(papers), "papers": papers}, f,
                  ensure_ascii=False, indent=1)

    md = ["# 中文文献题录（知网检索）", "",
          "> 由 `tools/crawl_papers.py` 从知网空间检索页抓取，**只含公开题录与摘要**，"
          "用于内部技术调研；正式引用请以知网原文为准。",
          "> 合计 %d 篇。" % len(papers), ""]
    by_topic = {}
    for p in papers:
        by_topic.setdefault(p.get("topic", "其他"), []).append(p)
    for topic in sorted(by_topic):
        md.append("## %s" % topic)
        md.append("")
        for p in by_topic[topic]:
            md.append("### %s" % p["title"])
            if p.get("source"):
                md.append("- 来源：%s" % p["source"])
            md.append("- 检索词：%s" % p.get("keyword", ""))
            md.append("- 链接：%s" % p["url"])
            if p.get("abstract"):
                md.append("- 摘要/摘录：%s" % p["abstract"][:400])
            md.append("")
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("已写出 %s / %s" % (OUT_JSON, OUT_MD))
    return 0


if __name__ == "__main__":
    sys.exit(main())
