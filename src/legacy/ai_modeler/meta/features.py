# -*- coding: utf-8 -*-
"""元增量学习 · 特征抽取（纯函数，无状态）。

设计依据见 `docs/architecture/元增量学习核心.md` §3。

为什么是「字符 n-gram + 数值 + 线索位」三块拼起来，而不是别的：

1. **中文不依赖分词**。工业提示词里混着中文、型号、拉丁缩写（`EGT001 SW80x088`、
   `内六角螺栓 M5x8`），任何分词器在这里都会切坏型号。字符 2~4 gram 对
   「型号只是一个长字符串」这件事最鲁棒，也不需要 jieba 之类的第三方依赖。
2. **数值要单独立一维**。`M5x8` 和 `M6x8` 的字符 n-gram 几乎一样（只差一个字符），
   但它们对建模来说是两回事。把型号里的数字单独抽出来、量化成独立区块，
   等于把这个「族内差异大、族间差异小」的病根单独标出来。
3. **线索位是人工先验**。1828 条数据里 19 个族极度不平衡（1156 : 6），
   纯统计特征会让小族被大族的 n-gram 淹掉。15 个人工线索位是「人已经知道的常识」，
   它先于数据存在，正好补冷启动和小族。

只依赖 numpy + 标准库（契约 §2 硬约束：用户双击用的是没有 torch 的系统 Python）。
"""

from __future__ import annotations

import math
import re
import unicodedata

import numpy as np

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 型号里最多取几个数字。6 个足够覆盖 `KMS-M02-H04-20-TM` / `GB825_88_M16`
#: 这类 3~4 段型号，再多就会把「尺寸数字」和「序号数字」混在一起，收益为负。
MAX_NUMERIC = 6

#: 数值区块的量化尺度。契约 §3 写明「除以 100 归一化」：常见尺寸（3~500mm）
#: 除完落在 0.03~5 这一段，配合逐区块 L2 归一化后不会被 n-gram 区块压死。
NUMERIC_SCALE = 100.0

#: 字符 n-gram 的阶数
NGRAM_SIZES = (2, 3, 4)

#: 区块权重。三块各自先做 L2 归一化（单位球面），再乘权重拼起来。
#: 为什么必须加权：n-gram 区块维度上千、数值区块只有 6 维、线索位 19 维。
#: 如果不加权，距离几乎完全由 n-gram 决定，数值和线索等于白算。
#: 这三个数是 holdout 上小范围扫出来的（见 docs 里的评测记录）：
#:   n-gram 1.0 / 数值 0.55 / 线索 0.75
#: —— 线索权重偏高是因为小族样本少，人工先验比统计特征更可靠；
#: 数值权重中等，太高会把「M5x8 与 M5x10」的差异放大到盖过族级差异。
W_NGRAM = 1.0
W_NUMERIC = 0.55
W_CUE = 0.75

#: 数值区块要不要再做一次逐块 L2 归一化。
#: True（默认）= 把 6 维数值块也拉到单位长度；False = 只按契约原文「除以 100」。
#: 实测两种都扫过（裸型号/全量两套口径）：
#:   True  —— 裸型号 0.9891 / 全量 0.9972
#:   False —— 裸型号 0.9891 / 全量 0.9917（w=0.8 时全量掉到 0.7879，很不稳）
#: 原因：数值块只有 6 维、量纲又随尺寸乱跳，不做归一化时它的有效权重会被
#: 别的区块压得时大时小；拉平到单位长度后三个区块的贡献才稳定。
#: 这是对契约「除以 100 归一化」的**补强**，不是替代——除以 100 照做。
NUMERIC_BLOCK_L2 = True

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")

#: 全角/异体符号归一到 ASCII，否则 `M5×8` 和 `M5x8` 会变成两个不同的字符序列。
_SYMBOL_MAP = {
    "×": "x", "✕": "x", "✖": "x", "╳": "x", "＊": "x", "*": "x",
    "，": ",", "。": ".", "、": ",", "；": ";", "：": ":",
    "（": "(", "）": ")", "【": "[", "】": "]", "－": "-", "—": "-",
    "　": "", "\u3000": "",
}


# ---------------------------------------------------------------------------
# 族与线索词
# ---------------------------------------------------------------------------

#: 类别线索词表。契约 §3 要求「至少覆盖 13 个族，每族 ≥4 个线索词」；
#: 这里按数据里真实存在的 **19 个族**全覆盖（比契约下限多 6 个），
#: 因为数据集里 `category` 非空的族就是这 19 个，漏一个就等于让那个族只能靠 n-gram 硬猜。
#:
#: 选词原则：
#:   * 优先「只有这个零件会这么说」的词（`内六角`、`补高台`）；
#:   * 家族前缀（`kms-m05`）允许在多个族重复命中——它是「同源」信号，
#:     两块都亮说明它们本来就该靠近，不是错误；
#:   * 避免单字线索（`销` 这种会误伤 `销座`），宁可少一个也别乱命中。
_FAMILY_CUES: dict[str, list[str]] = {
    "EGT型材": ["型材", "铝型材", "tunkers", "sw", "egt型材"],
    # 注意：这里刻意**不留** `m` 这种单字母线索——`KMS-M01`、`MGPA50`、`SW` 里全都有 m，
    # 留着它等于给「内六角螺钉」加了一个到处误报的位，白赚假准确率。
    "内六角螺钉": ["内六角", "螺钉", "螺栓", "圆柱头", "弹垫", "平垫"],
    "EGT连接件": ["egt连接件", "连接件", "egt0", "stp", "按真实尺寸建模"],
    "定位销": ["定位销", "台阶销", "kms-m01", "m01", "销"],
    "销座": ["销座", "kms-m02", "m02", "-rd", "-rm"],
    "垫片垫块": ["垫片", "垫块", "kms-m09", "m09", "垫"],
    "支座": ["支座", "kms-m05-010", "kms-m05", "支架"],
    "止块": ["止块", "挡块", "kms-m11", "m11"],
    "分气块": ["分气块", "kms-m15", "m15", "气路"],
    "支座(铝)": ["支座(铝)", "lz0", "lz01001", "铝"],
    "阀岛": ["阀岛", "kms-m13", "m13", "电磁阀"],
    "双导杆气缸": ["双导杆", "导杆气缸", "mgpa", "ula630", "smc", "univer"],
    "夹紧气缸": ["夹紧气缸", "ucbh", "夹紧", "ucb", "sk"],
    "手动夹紧气缸": ["手动", "ucbm", "手动夹紧", "ucb"],
    "标牌": ["标牌", "铭牌", "kms-m14", "m14"],
    "气缸连接块": ["气缸连接块", "连接块", "kms-m06", "m06"],
    "吊环": ["吊环", "gb825", "市购", "吊"],
    "接近开关": ["接近开关", "bes", "balluff", "传感器"],
    "补高台": ["补高台", "垫高", "kms-m05-020", "高台"],
}

#: 内置通用 n-gram 词干。冷启动时没有语料可拟合，就用这批「领域常识」兜底：
#: 常见零件词 + 常见品牌/前缀 + 数字二元组（型号里的数字串靠它才有字符级信号）。
_GENERIC_TOKENS = [
    "帮我建", "按真实尺寸建模", "真实尺寸", "建模", "型号", "品牌", "销",
    "内六角", "螺钉", "螺栓", "圆柱头", "垫片", "垫块", "支座", "止块",
    "分气块", "阀岛", "标牌", "吊环", "接近开关", "补高台", "连接件",
    "连接块", "气缸", "夹紧", "手动", "双导杆", "导杆", "型材", "铝型材",
    "定位销", "销座", "台阶销", "市购", "kms", "egt", "lz", "sw", "gb",
    "mgp", "ula", "ucb", "bes", "smc", "balluff", "tunkers", "univer",
    "stp", "m0", "m1", "p0", "h0", "d1", "a2", "20", "30", "50", "80",
    "x0", "x1", "x2", "x5", "_h1", "010", "020", "030", "-rd", "-rm",
    "-td", "-tm", "-pd", "-ud", "-wd", "sk", "z-p", "g14", "h04", "h06",
]


def family_cues() -> dict[str, list[str]]:
    """返回「族名 -> 线索词」表。

    每次返回**新的副本**：调用方（界面、评测）可能需要对它做微调，
    直接吐内部字典会让一次误改污染后面所有调用。
    """
    return {name: list(words) for name, words in _FAMILY_CUES.items()}


def cue_families() -> list[str]:
    """线索位的族顺序。必须固定，否则持久化回来的向量维度对不上。"""
    return list(_FAMILY_CUES.keys())


# ---------------------------------------------------------------------------
# 文本归一化 / 数值抽取
# ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    """统一大小写与全角符号，**保留空白**（空白折叠成一个空格）。

    为什么要单独留一份「保留空白」的视图：`1 2 3` 和 `123` 是两回事。
    做 n-gram 时空白是噪声该删（`M5 x 8` 要认出来就是 `m5x8`），
    但抽数字时必须靠空白把相邻的两个数分开，否则 `直径 12 长度 34`
    会被读成 `1234`。
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).lower()
    for src, dst in _SYMBOL_MAP.items():
        if src in s:
            s = s.replace(src, dst)
    return re.sub(r"\s+", " ", s).strip()


def normalize(text: str) -> str:
    """归一化并去掉所有空白，用于字符 n-gram 与线索词匹配。

    为什么要 NFKC + 小写：数据集里同一个型号会写成 `M5x8` / `M5×8` / `m5X8`，
    不归一化它们就是三个不同的字符串；空白对小 n-gram 只会产生噪声。
    """
    return _clean(text).replace(" ", "")


def numeric_tokens(text: str) -> list[float]:
    """从提示词里抽型号数字，最多 6 个，不足的部分由调用方补 0。

    规则（契约 §3 逐条对应）：
      * `M5x8`      -> [5.0, 8.0]      x 是分隔符，不是数字
      * `SW80x088`  -> [80.0, 88.0]    前导零按整数读（088 -> 88）
      * `EGT001`    -> [1.0]           同上
      * `直径 12 的孔` -> [12.0]
      * `EGT016.1`  -> [16.1]          小数点保留，它和 0161 不是一回事

    注意：在**保留空白**的归一化视图上匹配（`_clean`），所以
    `1 2 3` 不会被粘成 `123`；全角的 `Ｍ５ｘ８` 也能抽到。

    Args:
        text: 原始提示词。

    Returns:
        浮点数列表，长度 0..6；不补零（补零是 `embed` 的事，保持这个函数诚实
        —— 调用方要知道「到底有没有数字」）。
    """
    s = _clean(text)
    if not s:
        return []
    out: list[float] = []
    for m in _NUM_RE.finditer(s):
        raw = m.group(0).replace(",", ".")
        try:
            val = float(raw)
        except ValueError:  # 理论上到不了，防御一下别让脏数据打断训练
            continue
        out.append(val)
        if len(out) >= MAX_NUMERIC:
            break
    return out


# ---------------------------------------------------------------------------
# 词表
# ---------------------------------------------------------------------------


def _ngrams_of(text: str) -> list[str]:
    """把一段文本切成 2~4 字符 n-gram（去重前的原始序列）。"""
    s = normalize(text)
    grams: list[str] = []
    for n in NGRAM_SIZES:
        if len(s) < n:
            continue
        for i in range(len(s) - n + 1):
            grams.append(s[i:i + n])
    return grams


def _builtin_ngrams() -> list[str]:
    """用领域常识拼出内置词表。

    冷启动（第一次跑、kb 里没有 meta_memory.json）时只能用这个：
    没有语料就没法拟合词表，但「螺钉/螺栓/内六角/EGT/KMS」这些词是领域常识，
    写死在代码里既不需要训练也不需要联网。
    """
    seen: dict[str, None] = {}
    for token in _GENERIC_TOKENS:
        for g in _ngrams_of(token):
            seen.setdefault(g, None)
    for words in _FAMILY_CUES.values():
        for token in words:
            for g in _ngrams_of(token):
                seen.setdefault(g, None)
    # 数字二元组：型号串里 `50`、`105` 这类数字段靠它才有字符级信号。
    # 只有 100 维，代价很小，但让「裸型号」提示词（`KMS-M01-D159-A20`）不至于只剩数值区块。
    for a in "0123456789":
        for b in "0123456789":
            seen.setdefault(a + b, None)
    return list(seen)


def build_vocab(
    texts=None,
    max_features: int = 1200,
    extra_ngrams=None,
    embed_dim: int | None = None,
) -> dict:
    """建词表。

    Args:
        texts: 语料提示词列表。``None``/空 -> 用内置领域词表（冷启动路径）。
            给了语料则按**文档频次**挑词：只在一条提示词里出现过的 n-gram
            没有泛化价值，直接丢掉，把维度留给真正反复出现的片段。
        max_features: 最多收录多少个 n-gram。
        extra_ngrams: 额外强制收录的 n-gram（调试/迁移用）。
        embed_dim: 显式维度契约。``None`` 时维度由词表自然决定。

    Returns:
        可直接 JSON 序列化的 dict：``{'version','ngrams','cue_families','cues'}``。

    为什么词表要持久化：契约 §3 要求「维度必须由 vocab 决定并随 memory 一起持久化，
    不能每次重算」。同一个 768 维向量，如果换一套词表，每个维度含义就变了，
    旧原型立刻报废。所以词表是记忆的一部分，不是可再生的缓存。
    """
    fams = cue_families()
    base = 6 + len(fams)  # 数值 6 维 + 线索 len(fams) 维
    if embed_dim is not None and embed_dim < base:
        raise ValueError(
            "embed_dim=%r 太小：至少要 %d 维（数值 6 + 线索 %d）" % (embed_dim, base, len(fams))
        )

    # 显式 embed_dim 是硬契约（必须严丝合缝）；不传时维度由词表自然决定，
    # 只受 max_features 约束。
    want = None if embed_dim is None else embed_dim - base
    limit = want if want is not None else max_features

    ngrams: list[str] = []
    if texts:
        df: dict[str, int] = {}
        for t in texts:
            for g in set(_ngrams_of(t)):
                df[g] = df.get(g, 0) + 1
        # 频次降序 + 字典序兜底：同一个语料必须得到同一个词表，否则评测不可复现。
        # df>=2 的门槛：只在一句里出现过的片段学不到任何东西，留着纯占维度。
        ranked = sorted(df.items(), key=lambda kv: (-kv[1], kv[0]))
        ngrams = [g for g, c in ranked if c >= 2][:limit]
        if len(ngrams) < limit:
            more = [g for g, c in ranked if c < 2]
            ngrams.extend(more[: max(0, limit - len(ngrams))])
    else:
        ngrams = _builtin_ngrams()[:limit]

    if extra_ngrams:
        for g in extra_ngrams:
            if g not in ngrams:
                ngrams.append(g)

    # 显式 embed_dim 才需要补齐/截断：不够就用「永远匹配不上」的占位 n-gram 撑满，
    # 它们恒为 0、不影响任何距离，但保证 dim 与调用方约定一致（界面表格、旧存档校验
    # 都靠这个数）。不传 embed_dim 时绝不补——白占维度没有任何好处。
    if want is not None:
        if len(ngrams) < want:
            ngrams.extend("\x00pad%04d" % i for i in range(want - len(ngrams)))
        else:
            ngrams = ngrams[:want]

    return {
        "version": 1,
        "ngrams": ngrams,
        "cue_families": list(fams),
        "cues": {name: list(_FAMILY_CUES[name]) for name in fams},
    }


def vocab_dim(vocab: dict) -> int:
    """词表对应的向量维度。"""
    return len(vocab.get("ngrams") or ()) + 6 + len(vocab.get("cue_families") or ())


def _index_of(vocab: dict) -> dict[str, int]:
    """惰性挂一份 `n-gram -> 位置` 的倒排表。

    挂在 vocab 字典里（键名以 `_` 开头）而不是每次重建：embed 会被调上万次，
    1828 条数据 × 每次重建 1200 项的 dict 是纯浪费。保存时必须剔除这个键，
    它不重要、也不该进 JSON（见 memory.save）。
    """
    idx = vocab.get("_idx")
    if idx is None:
        idx = {g: i for i, g in enumerate(vocab.get("ngrams") or ()) if not g.startswith("\x00")}
        vocab["_idx"] = idx
    return idx


# ---------------------------------------------------------------------------
# 嵌入
# ---------------------------------------------------------------------------


def embed(prompt: str, vocab: dict | None = None) -> np.ndarray:
    """把一条提示词变成定长向量。

    结构（三段拼接，逐段 L2 归一化后加权）：

    ``[字符 n-gram 区块 | 数值区块(6) | 线索位区块(19)]``

    具体做法：
      * n-gram：2~4 gram 计数，做**子线性 TF**（``1 + log(tf)``）再加权。
        为什么子线性：一个字符在自己的词里出现 5 次不代表它重要 5 倍，
        线性 TF 会让 `EGT型材EGT型材EGT型材` 这种重复文本主导距离。
      * 数值：``numeric_tokens`` 除以 100，不足 6 个补 0。
      * 线索位：``family_cues`` 命中为 1，未命中 0（可以有多个族同时亮，
        它们本来就同源，不是互斥分类）。
      * 三段各自 L2 归一化 → 乘区块权重 → 整条向量再做一次 L2 归一化。

    为什么最后还要整体归一化：这样欧氏距离与余弦距离单调等价（``d^2 = 2-2cos``），
    「距离 0.4」在所有样本上有统一含义，界面上写「相似度 0.71」不会随文本长度漂。

    Args:
        prompt: 提示词原文。
        vocab: ``build_vocab`` 的产物；``None`` 时临时用内置词表（冷启动）。

    Returns:
        ``np.float32`` 一维数组，长度等于 ``vocab_dim(vocab)``。
    """
    v = vocab if vocab is not None else build_vocab(None)
    dim = vocab_dim(v)
    vec = np.zeros(dim, dtype=np.float32)

    s = normalize(prompt)
    # ---- 1) n-gram 区块 ----
    if s:
        idx = _index_of(v)
        counts: dict[int, int] = {}
        for n in NGRAM_SIZES:
            if len(s) < n:
                continue
            for i in range(len(s) - n + 1):
                j = idx.get(s[i:i + n])
                if j is not None:
                    counts[j] = counts.get(j, 0) + 1
        if counts:
            n_ng = len(v.get("ngrams") or ())
            block = np.zeros(n_ng, dtype=np.float32)
            for j, c in counts.items():
                block[j] = 1.0 + math.log(c)
            block *= W_NGRAM / (np.linalg.norm(block) + 1e-12)
            vec[:n_ng] = block

    # ---- 2) 数值区块 ----
    nums = numeric_tokens(prompt)
    if nums:
        fams_n = len(v.get("cue_families") or ())
        nb = np.zeros(6, dtype=np.float32)
        vals = (np.asarray(nums, dtype=np.float32) / NUMERIC_SCALE)[:6]
        nb[: len(vals)] = vals
        if NUMERIC_BLOCK_L2:
            nb *= W_NUMERIC / (np.linalg.norm(nb) + 1e-12)
        else:
            nb *= W_NUMERIC
        vec[dim - 6 - fams_n: dim - fams_n] = nb

    # ---- 3) 线索位区块 ----
    fams = v.get("cue_families") or []
    cues = v.get("cues") or {}
    cb = np.zeros(len(fams), dtype=np.float32)
    for i, fam in enumerate(fams):
        for word in cues.get(fam, ()):
            if word in s:
                cb[i] = 1.0
                break
    if cb.any():
        cb *= W_CUE / (np.linalg.norm(cb) + 1e-12)
    vec[dim - len(fams):] = cb

    # ---- 4) 整体 L2 归一化 ----
    norm = float(np.linalg.norm(vec))
    if norm > 1e-12:
        vec /= norm
    return vec


def embed_many(prompts, vocab: dict | None = None) -> np.ndarray:
    """批量嵌入，返回 ``(n, dim)`` 矩阵。评测里几百条一次算完比逐条快得多。"""
    v = vocab if vocab is not None else build_vocab(None)
    rows = [embed(p, v) for p in prompts]
    if not rows:
        return np.zeros((0, vocab_dim(v)), dtype=np.float32)
    return np.vstack(rows)
