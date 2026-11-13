# -*- coding: utf-8 -*-
"""元增量学习 · 原型记忆（`MetaMemory`）。

契约见 `docs/architecture/元增量学习核心.md` §4。

整体思路：**每族一个原型（球面上的质心），来一条就朝它挪一点**。
选它而不是训练分类器的理由写在契约 §0：1828 条数据训不出深度网络，
但「离哪个族近」这件事可以解释、可以审计、可以随时插一个新族进来。

本模块只管「记」和「查」，不做任何评测（评测在 evaluate.py，
界面在 ui/meta_panel.py）——这样契约里「推理/学习/查询」三段是干净的。

三条必须守住的规则（评测和单元测试都会验）：
  1. **写入门禁**：``verified=False`` 的样本只进候选池，绝不改原型、不涨样本数。
     现实里这个布尔值代表「用户确认了没有 / CAD 真的建出来了没有」——
     把没验证的猜测当知识，一次误判就会污染原型。
  2. **新族 ≥3 门槛**：同一族攒够 3 条 verified 才建原型。
     只来 1 条就建原型，等于把这一条的噪声当成整个族的样子；
     后面两条正确的样本反而被它拉偏。
  3. **exemplar 上限 32/族**：持久化体积要可控（别把 1828 条全写进 JSON），
     保留策略偏向**离原型远的**样本——近的样本彼此太像，删掉不心疼。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import numpy as np

from .features import build_vocab, embed, vocab_dim

# ---------------------------------------------------------------------------
# 常量（每个都写清为什么是这个数）
# ---------------------------------------------------------------------------

#: 原型记忆的默认落盘位置。运行时产物，**不进版本库**（见 .gitignore）。
DEFAULT_STORE = Path(__file__).resolve().parents[2] / "kb" / "meta_memory.json"

#: 新族建立原型所需的最小 verified 条数。
#: 为什么是 3 而不是 2 或 5：
#:   * 1 条 = 原型就是那一条，噪声原样成为「族」；
#:   * 2 条 = 平均值连方差都估不出来，第三条第若落在另一侧就直接把原型拽歪；
#:   * 3 条是「三个点能撑开一个方向」的最小值，且对 6 条样本的小族（补高台、
#:     手动夹紧气缸）来说，门槛再高就等于永远建不起来。
NEW_FAMILY_MIN_VERIFIED = 3

#: EMA 系数下限。α = max(0.05, 1/(n+1))。
#: 1/(n+1) 的含意在数学上就是**累计均值**：样本越多，单条的影响越小。
#: 为什么要地板 0.05：等 n 涨到 19 以后 1/(n+1) 已经小到几乎不动了，
#: 那程序就永远学不动了（模型升级、用户习惯变化都跟不上）。0.05 的半衰期是
#: ln(0.5)/ln(0.95) ≈ 13.5 条——真漂移半个月量级的样本能被吸收，
#: 而单条噪声最多只能把原型推 5%，不至于一次误判毁掉整族。
ALPHA_FLOOR = 0.05

#: 每族 exemplar 池上限（持久化体积 + 检索成本的主要来源）。
EXEMPLAR_CAP = 32

#: 池满时保留多少条「离原型最远的」。它们撑住族的边缘，是新样本的判别力来源。
EXEMPLAR_FAR_KEEP = 24

#: 另外保留多少条「离原型最近的」作为典型样本。为什么要留：
#: 如果只留最远的，池子会变成「一堆离群点」，`envelope_hint` 的中位数会偏，
#: `similar` 检索也全是怪样本。8 条核心样本让池子既有边界又有中心。
EXEMPLAR_CORE_KEEP = 8

#: 每族候选池上限。候选只用于「让人看见系统猜了什么」，
#: 保留最近 64 条足够，不参与任何原型计算。
CANDIDATE_CAP = 64

#: 混合判决里 kNN 分支的邻居数。
HYBRID_KNN_K = 3

#: 混合判决里 exemplar 最近邻的权重（w）。为什么偏 kNN（0.6 而不是 0.5）：
#: 原型把一族压成一个点，族内跨度大时（`M5x8` 到 `M5x50` 都是内六角螺钉）
#: 均值会把细节抹平；而 exemplar 是真实样本，天然保留「族内多个峰」。
#: 实测在只给型号、不给类别词的最难场景（`variant=裸型号`）下，
#: 纯原型 0.9348，纯 1-NN 1.0 —— 所以把权重往 kNN 偏一档。
#: 但**不是全押 kNN**：exemplar 池被 32 条上限裁过，
#: 若新族/新样本把某一族挤空，原型还是唯一能兜底的证据。
HYBRID_KNN_WEIGHT = 0.6

#: 原型距离换算成相似度的口径。三个候选都在裸型号/全量两套口径上扫过：
#:   'inv'   ：``s_proto = 1/(1+d)`` —— **和 kNN 分支同一个映射**（默认，最优）
#:   'affine'：``s_proto = 1 - d/2``，把 d∈[0,2] 映到 [0,1]
#:   'raw'   ：``s_proto = 1 - d``（最朴素的写法，落在 [-1,1]）
#: 为什么必须和 kNN 用同一个映射：两路的**地基**不一样时，加权就不是加权了。
#: 'raw' 的同族典型值约 +0.06、异族 -0.2，等于给有原型的族扣分；
#: 'affine' 则相反，1-d/2 在 d=1.2 时还有 0.4，等于给有原型的族白送一个
#: 0.4×0.4≈0.16 的底分——实测就有 pending 小族被这个底分压掉
#: （`UCBM63VESK0135°` 手动夹紧气缸 → 被夹紧气缸以 0.626:0.600 抢走）。
#: 统一成 1/(1+d) 后两路都落在 (0,1]，地板都趋近 0，谁也没白拿分。
PROTO_SCORE_MODE = "inv"


def _proto_similarity(d: float) -> float:
    """原型距离 -> 相似度（与 s_knn 分支同尺度）。"""
    if PROTO_SCORE_MODE == "inv":
        return 1.0 / (1.0 + d)
    if PROTO_SCORE_MODE == "affine":
        return 1.0 - 0.5 * d
    return 1.0 - d

#: 落盘时浮点保留几位。6 位在 0..2 的距离尺度上远小于任何有意义的差异，
#: 但能把 JSON 体积压掉一大半（原型 19 族 × 上千维）。
FLOAT_DP = 6

_STORE_VERSION = 1


def _now() -> str:
    """本地时间的秒级 ISO 串，给人看的。"""
    return datetime.now().isoformat(timespec="seconds")


class _DualMethod:
    """让一个方法既能 `obj.load(path)` 也能 `MetaMemory.load(path)`。

    为什么需要：契约 §4 写的是 ``def load(cls, path)``（类方法），
    但实际集成时更常见的写法是 ``mem.load(path)``（拿现成实例换存档）。
    两种都支持就不会有人踩 ``TypeError: load() missing 1 required positional
    argument: 'self'`` 这种坑；调用类形式时自动新建一个空实例再加载。
    """

    def __init__(self, func):
        self.func = func
        self.__doc__ = func.__doc__
        self.__name__ = getattr(func, "__name__", "wrapped")

    def __get__(self, obj, objtype=None):
        func = self.func

        def wrapper(*args, **kwargs):
            inst = obj if obj is not None else objtype()
            return func(inst, *args, **kwargs)

        wrapper.__doc__ = func.__doc__
        return wrapper



def _as_envelope(value):
    """把包络规整成 ``[x, y, z]`` 或 ``None``。

    数据里 envelope 可能是 ``[10, 10, 13]``、``None``，也可能是空列表/字符串。
    统一成 3 个 float，下游算中位数时不用到处写判断。
    """
    if value is None:
        return None
    try:
        vals = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if len(vals) < 3:
        return None
    return vals[:3]


class MetaMemory:
    """原型记忆。一个实例 = 一份「程序到目前为止学到的所有知识」。

    Args:
        store_path: 落盘路径。传了且文件存在，构造时自动 ``load``
            （主程序第一天的真实状态：文件不存在，于是走空库 + 内置词表）。
        embed_dim: 显式维度契约。``None`` 时由词表决定。
            注意：如果 `store_path` 指向的旧存档维度不同，以**存档为准**
            ——旧记忆的向量是按旧维度算的，强行改维度等于让它全部作废。

    线程安全：所有会改状态的方法都持一把 `RLock`。界面上「跑一次评测」
    和主程序「生成后 observe」可能不在同一个线程，这把锁是廉价的保险。
    """

    def __init__(self, store_path=None, embed_dim: int | None = None):
        self.store_path = Path(store_path) if store_path else None
        self.vocab = build_vocab(None, embed_dim=embed_dim)
        self._centroids: dict[str, np.ndarray] = {}
        self._n_samples: dict[str, int] = {}
        self._n_verified: dict[str, int] = {}
        self._pending: dict[str, list[dict]] = {}
        self._exemplars: dict[str, list[dict]] = {}
        self._candidates: dict[str, list[dict]] = {}
        self._updated_at: str | None = None
        self._lock = threading.RLock()
        # 嵌入缓存：prompt -> 向量。`suggest` 要在池子里算距离，池子会涨到
        # 19 族 × 96 条；每次都重新嵌入等于 O(池子) 的重复劳动，
        # 评测里这直接决定了是几秒还是几分钟（踩过）。词表不变则向量不变，
        # 所以按 prompt 文本缓存是安全的；换词表/重置时清空。
        self._xcache: dict[str, np.ndarray] = {}
        # 原型矩阵缓存，避免每次 suggest 都重新 vstack。
        self._proto_cache: tuple[list[str], np.ndarray] | None = None
        # exemplar 池矩阵缓存（混合判决的 kNN 分支用），同上。
        self._pool_cache = None

        if self.store_path is not None and self.store_path.exists():
            self.load(self.store_path)

    # ------------------------------------------------------------------
    # 基本属性
    # ------------------------------------------------------------------
    @property
    def dim(self) -> int:
        """向量维度（由词表决定，随存档一起走）。"""
        return vocab_dim(self.vocab)

    def __repr__(self) -> str:  # pragma: no cover - 只为调试好看
        return "<MetaMemory families=%d samples=%d dim=%d>" % (
            len(self._centroids), sum(self._n_samples.values()), self.dim,
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _embed(self, prompt: str) -> np.ndarray:
        return embed(prompt, self.vocab)

    def _x_of(self, entry: dict) -> np.ndarray:
        """池子里某条的向量（带缓存）。"""
        p = entry.get("prompt", "") or ""
        x = self._xcache.get(p)
        if x is None:
            x = self._embed(p)
            self._xcache[p] = x
        return x

    def _invalidate(self) -> None:
        """状态变了：原型矩阵 / 池矩阵缓存作废。"""
        self._proto_cache = None
        self._pool_cache = None

    def _ensure(self, family: str) -> None:
        self._n_samples.setdefault(family, 0)
        self._n_verified.setdefault(family, 0)
        self._pending.setdefault(family, [])
        self._exemplars.setdefault(family, [])
        self._candidates.setdefault(family, [])

    def _all_families(self) -> list[str]:
        """所有「见过」的族名（含还没建原型的），排序保证输出稳定。"""
        names = set(self._n_verified) | set(self._centroids) | set(self._candidates)
        return sorted(names)

    def _prototype_matrix(self):
        """返回 ``(族名列表, (m, dim) 原型矩阵)``；没有原型时返回 ``([], None)``。"""
        if self._proto_cache is not None:
            return self._proto_cache
        names = [f for f in self._centroids if self._centroids[f] is not None]
        if not names:
            self._proto_cache = ([], None)  # type: ignore[assignment]
            return [], None
        mat = np.vstack([self._centroids[f] for f in names])
        self._proto_cache = (names, mat)
        return names, mat

    def _pool_matrix(self):
        """返回 ``(池矩阵, 族名列表, {族: (起, 止)})``，供 kNN 分支一次算完。

        为什么建缓存：`suggest` 每次都要和整池算距离。池子上限
        19 族 × (32 exemplar + 64 候选) 条，逐条算距离等于每次几百次
        Python 循环；拼成一个矩阵后一次 matmul 就够，评测从分钟级降到秒级
        （踩过：不加缓存 evaluate_incremental 跑了两分半）。

        池子里收两样**已验证**的东西：
          * ``exemplars``：已建原型族的代表样本；
          * ``pending``：还没凑够 3 条、**暂时建不了原型**的族的缓冲样本。
        为什么要收 pending：一个族只有 1~2 条样本时按契约不能建原型，
        但它已经是一条真实证据了。如果 `suggest` 因为「没有原型」就把它当不存在，
        系统在冷启动初期会对一整批真实见过的型号失明（实测裸型号子集里
        7 个小族因此全错，accuracy 卡在 0.9348）。门槛管的是**建原型**，
        不是**不许用证据**。
        候选池（verified=False）**不进**这里：没验证的猜测连判决都不该参与。
        """
        if self._pool_cache is not None:
            return self._pool_cache
        vecs: list[np.ndarray] = []
        owners: list[str] = []
        spans: dict[str, tuple[int, int]] = {}
        fams = sorted(set(self._exemplars) | {f for f, p in self._pending.items() if p})
        for fam in fams:
            items = list(self._exemplars.get(fam) or ()) + list(self._pending.get(fam) or ())
            if not items:
                continue
            start = len(vecs)
            for it in items:
                vecs.append(self._x_of(it))
                owners.append(fam)
            spans[fam] = (start, len(vecs))
        if not vecs:
            self._pool_cache = (None, [], {})
            return self._pool_cache
        self._pool_cache = (np.vstack(vecs), owners, spans)
        return self._pool_cache

    def _retain_exemplars(self, family: str) -> None:
        """池满时的多样性保留（“远的优先” + 少量核心样本）。

        具体：按到当前原型的距离降序排，
          * 保留最远的 ``EXEMPLAR_FAR_KEEP`` 条 —— 它们定义族的边界，
            新样本能不能被判进来，靠的就是这些边角样本；
          * 再保留最近的 ``EXEMPLAR_CORE_KEEP`` 条 —— 它们代表族的中心，
            `envelope_hint` 的中位数和 `similar` 检索都靠它们不至于全变怪样本。
        中间那些「不够远也不够典型」的冗余样本被丢掉。

        为什么不用 iCaRL 那套 herding：那需要反复重算全族样本的均值，
        这里是流式写入、样本一条条来，herding 的成本和收益不成比例。
        """
        pool = self._exemplars.get(family) or []
        if len(pool) <= EXEMPLAR_CAP:
            return
        centroid = self._centroids.get(family)
        if centroid is None:  # 理论上不会发生，防御
            self._exemplars[family] = pool[-EXEMPLAR_CAP:]
            return
        dists = np.array([float(np.linalg.norm(self._x_of(e) - centroid))
                          for e in pool])
        order = np.argsort(-dists)  # 远 -> 近
        far = list(order[:EXEMPLAR_FAR_KEEP])
        core = list(order[-EXEMPLAR_CORE_KEEP:])
        keep = sorted(set(far) | set(core))
        if len(keep) > EXEMPLAR_CAP:  # 极端情况下两段重叠，粗暴截断保证上限
            keep = keep[:EXEMPLAR_CAP]
        self._exemplars[family] = [pool[i] for i in keep]

    # ------------------------------------------------------------------
    # 推理
    # ------------------------------------------------------------------
    def suggest(self, prompt: str, k: int = 5) -> dict:
        """预测一条提示词属于哪个族（**原型 + exemplar 最近邻的混合判决**）。

        判决分两路，加权合成（见 ``HYBRID_KNN_WEIGHT``）：
          * ``s_proto = 1 - d(原型)``：整族的中心，抗噪但会抹平族内跨度；
          * ``s_knn   = 该族 exemplar 里最近 k 条的相似度均值``：保留真实样本，
            天然有「族内多个峰」的分辨力，但池子被容量上限裁过。
          * ``s = w·s_knn + (1-w)·s_proto``，w=0.6 偏 kNN。
        为什么要混合而不是二选一：只给裸型号（`M5x8`）时，
        原型把 M5~M12 全平均成一个点，实测 0.9348 打不过 1-NN 的 1.0；
        而 exemplar 池只有 32 条上限，单靠它在长尾族上又不够稳。
        两路加权能同时保住「整族的稳健」和「族内细节」。
        族若只有原型没有 exemplar（理论上不会：建原型时必然同时塞 3 条 exemplar），
        就只用原型分，**不因为缺一路证据就判 Unknown**。

        Args:
            prompt: 提示词原文。
            k: 返回几条相似案例（从 exemplar 池 + 候选池里取）。

        Returns:
            dict:
              ``family``        预测族名；**空记忆时是 None，不抛异常**
                                （主程序第一天就是这个状态，必须能跑）。
              ``confidence``    0..1。公式是 ``1 - d/(d+d2) = d2/(d+d2)``，
                                d 是到**预测族**原型的距离，d2 是到其他族最近原型的距离。
                                **这是「族间区分度」，不是概率**：离谁都很远但
                                最近的那个明显更近时，置信度依然高；反过来，
                                两条原型几乎一样近时（d≈d2），哪怕绝对距离很小
                                也只有 0.5 —— 因为它确实分不出来。绝对距离
                                在两个不同的族上不可比（族内方差本来就不一样），
                                但「比第二名近多少」在所有族之间是可比的。
                                注意：判决可以由 exemplar 分支推翻「最近原型」；
                                这时 d 取的是被选中那个族的原型距离，
                                置信度会因此偏低——正好表示「原型不同意这次判决」。
              ``family_scores`` 所有已建原型族的**混合分**，降序。
              ``envelope_hint`` 该族经验包络的各轴中位数（没样本时 None）。
              ``similar``       top-k 相似案例，含 ``prompt/category/model/
                                envelope/score``，score = ``1/(1+d)``。
        """
        with self._lock:
            names, mat = self._prototype_matrix()
            out = {
                "family": None,
                "confidence": 0.0,
                "family_scores": {},
                "envelope_hint": None,
                "similar": [],
            }
            if not (prompt or "").strip():
                return out

            x = self._xcache.get(prompt)
            if x is None:
                x = self._embed(prompt)
                # 评测里同一条提示词会被反复 suggest（每一步都回测一遍），
                # 缓存能把 19 步 × 1800 条压成 1800 次真实嵌入，评测从分钟级到秒级。
                if len(self._xcache) > 20000:   # 长跑时别让缓存无限涨
                    self._xcache.clear()
                self._xcache[prompt] = x
            # ---- kNN 分支：每族取最近的 HYBRID_KNN_K 条样本 ----
            # 这一步**不看有没有原型**：pending 里的已验证样本也是证据。
            pool_mat, _owners, spans = self._pool_matrix()
            knn: dict[str, float] = {}
            if pool_mat is not None:
                # 向量都已 L2 归一化：||p-x||^2 = 2 - 2·(p·x)，一次 matmul 拿全池距离
                dots = pool_mat @ x
                d_pool = np.sqrt(np.maximum(0.0, 2.0 - 2.0 * dots))
                for fam, (a, b) in spans.items():
                    seg = d_pool[a:b]
                    m = min(HYBRID_KNN_K, len(seg))
                    if m <= 0:
                        continue
                    if m < len(seg):
                        seg = np.partition(seg, m - 1)[:m]
                    knn[fam] = float(np.mean(1.0 / (1.0 + seg)))

            # ---- 合成两路分数 ----
            scores: dict[str, float] = {}
            d_proto = None
            if mat is not None:
                d_proto = np.linalg.norm(mat - x, axis=1)
                for fam, d in zip(names, d_proto):
                    sp = _proto_similarity(float(d))
                    sk = knn.get(fam)
                    if sk is None:
                        scores[fam] = float(sp)      # 没有 exemplar：只信原型
                    else:
                        scores[fam] = float(
                            HYBRID_KNN_WEIGHT * sk + (1.0 - HYBRID_KNN_WEIGHT) * sp
                        )
            # 还没建原型的族：只可能来自 pending 里的真实样本。
            # 有证据就用 knn 分，**不因为缺原型就把它排除**（见 _pool_matrix）。
            for fam, sk in knn.items():
                if fam not in scores:
                    scores[fam] = float(sk)

            if scores:
                best_fam = max(scores, key=lambda f: (scores[f], -names.index(f)
                                                     if f in names else -1e9))
                out["family"] = best_fam
                out["family_scores"] = dict(
                    sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
                )

                # ---- 置信度：契约 §4 的族间区分度口径（仍基于原型距离） ----
                if best_fam in names:
                    i_best = names.index(best_fam)
                    d1 = float(d_proto[i_best])
                    others = [float(d_proto[i]) for i in range(len(names)) if i != i_best]
                    if not others:
                        # 只有一个族：没有「第二名」可比，退化成绝对相似度。
                        conf = 1.0 / (1.0 + d1)
                    else:
                        d2 = min(others)
                        conf = 1.0 if (d1 + d2) <= 1e-12 else d2 / (d1 + d2)
                else:
                    # 判决来自「还没建原型」的族：没有原型距离可算，
                    # 直接用它自己最近邻的相似度当置信度（同样是 1/(1+d) 口径）。
                    conf = float(knn.get(best_fam, 0.0))
                out["confidence"] = float(min(1.0, max(0.0, conf)))
                out["envelope_hint"] = self._envelope_hint(best_fam)

            # top-k 相似案例：池子里的真实样本（exemplar 是验证过的，候选是没验证的）。
            # k<=0 直接跳过：评测里每次预测都要调 suggest，k=0 时省掉这整段，
            # 不然池子一涨就是 O(池子) 的嵌入，评测从几秒变几分钟（真踩过）。
            if k <= 0:
                return out
            pool = []
            for fam, items in self._exemplars.items():
                for it in items:
                    pool.append((fam, it))
            for fam, items in self._candidates.items():
                for it in items:
                    pool.append((fam, it))
            scored = []
            for fam, it in pool:
                dist = float(np.linalg.norm(self._x_of(it) - x))
                scored.append({
                    "prompt": it.get("prompt", ""),
                    "category": fam,
                    "model": it.get("model", ""),
                    "envelope": it.get("envelope"),
                    "score": float(1.0 / (1.0 + dist)),
                })
            scored.sort(key=lambda r: (-r["score"], r["prompt"]))
            out["similar"] = scored[: max(0, int(k))]
            return out

    def _envelope_hint(self, family: str):
        """该族经验包络：exemplar 池里包络的逐轴中位数。

        为什么用中位数而不是均值：包络里偶发的大件（比如一根 3 米长的型材）
        会把均值拽飞，中位数对离群值免疫。为什么只用 exemplar 池：
        为了控制内存和存档体积，全量包络没有单独保存（池子本来就偏向边界 + 中心，
        中位数足够稳）。
        """
        vals = []
        for it in self._exemplars.get(family, ()):  # type: ignore[arg-type]
            env = _as_envelope(it.get("envelope"))
            if env:
                vals.append(env)
        if not vals:
            return None
        arr = np.asarray(vals, dtype=float)
        med = np.median(arr, axis=0)
        return [float(x) for x in med[:3]]

    # ------------------------------------------------------------------
    # 学习
    # ------------------------------------------------------------------
    def observe(
        self,
        prompt: str,
        category: str,
        model: str = "",
        envelope=None,
        verified: bool = True,
    ) -> dict:
        """写入一条经验。

        这是「写入门禁」的唯一入口：

          * ``verified=False``：只进候选池，**不改原型、不涨样本计数**。
            返回 ``reason='candidate_only'``、``updated_prototype=False``。
          * ``verified=True``：走正常学习路径（可能只是缓冲，见下）。

        新族门槛：同一族 verified 累计 < 3 时，样本进 ``pending`` 缓冲，
        **不建原型**；第 3 条到达时用这 3 条的均值一次性建原型
        （不是「第 1 条当原型再 EMA 两次」——那样前两条的顺序会永久影响原型）。

        EMA：``p_new = (1-α)·p_old + α·x``，``α = max(0.05, 1/(n+1))``，
        n 是本条**之前**的已验证样本数。更新后重新归一化到单位球面：
        这样「距离」在所有样本上有统一的量纲（``d² = 2-2cos``），
        置信度公式和 exemplar 的距离排序才有可比性。

        Args:
            prompt: 提示词原文。
            category: 族名（零件类别）。**空值直接拒绝**：负例不该变成族。
            model: 型号，仅用于展示/相似案例。
            envelope: 包络 ``[x,y,z]``，用于 `envelope_hint`。
            verified: 是否已验证。默认 True（人工确认的写入）。

        Returns:
            ``{'family','accepted','reason','n_samples'}``
            另附 ``n_verified`` 和 ``updated_prototype`` 便于审计。
        """
        fam = (category or "").strip()
        if not fam:
            # 数据集里那 12 条 category=null 的负例就是走这条路：
            # 它们是「查不到型号」的探测，不能变成第 20 个族。
            return {
                "family": None, "accepted": False, "reason": "rejected_no_category",
                "n_samples": 0, "n_verified": 0, "updated_prototype": False,
            }

        with self._lock:
            self._ensure(fam)
            entry = {
                "prompt": prompt or "",
                "model": model or "",
                "envelope": _as_envelope(envelope),
            }

            # ---- 写入门禁：未验证的样本到此为止 ----
            if not verified:
                cands = self._candidates[fam]
                cands.append(dict(entry, at=_now()))
                if len(cands) > CANDIDATE_CAP:
                    del cands[: len(cands) - CANDIDATE_CAP]
                self._updated_at = _now()
                return {
                    "family": fam, "accepted": True, "reason": "candidate_only",
                    "n_samples": self._n_samples[fam],
                    "n_verified": self._n_verified[fam],
                    "updated_prototype": False,
                }

            x = self._embed(entry["prompt"])
            self._xcache[entry["prompt"]] = x   # 顺手缓存，exemplar 保留策略与检索要用
            self._n_verified[fam] += 1
            self._pending[fam].append(entry)
            self._updated_at = _now()

            if self._centroids.get(fam) is None:
                # ---- 新族：还没到门槛就只缓冲 ----
                if self._n_verified[fam] < NEW_FAMILY_MIN_VERIFIED:
                    self._invalidate()   # 缓冲样本也是证据，kNN 分支要用
                    return {
                        "family": fam, "accepted": True,
                        "reason": "buffered_until_gate",
                        "n_samples": self._n_samples[fam],
                        "n_verified": self._n_verified[fam],
                        "updated_prototype": False,
                    }
                buf = self._pending[fam]
                mat = np.vstack([self._embed(it["prompt"]) for it in buf])
                proto = mat.mean(axis=0)
                norm = float(np.linalg.norm(proto))
                if norm > 1e-12:
                    proto = proto / norm
                self._centroids[fam] = proto.astype(np.float32)
                self._n_samples[fam] = len(buf)
                self._exemplars[fam] = list(buf)
                self._pending[fam] = []
                self._retain_exemplars(fam)
                self._invalidate()
                return {
                    "family": fam, "accepted": True, "reason": "prototype_created",
                    "n_samples": self._n_samples[fam],
                    "n_verified": self._n_verified[fam],
                    "updated_prototype": True,
                }

            # ---- 已有原型：EMA ----
            n = self._n_samples[fam]
            alpha = max(ALPHA_FLOOR, 1.0 / (n + 1))
            proto = (1.0 - alpha) * self._centroids[fam] + alpha * x
            norm = float(np.linalg.norm(proto))
            if norm > 1e-12:
                proto = proto / norm
            self._centroids[fam] = proto.astype(np.float32)
            self._n_samples[fam] = n + 1
            # 缓冲里还留着前两条吗？只有「建原型时清空」这一条路径，所以这里必定是空的；
            # 防御性地清一下，避免未来改动引入重复计数。
            self._pending[fam] = []
            self._exemplars[fam].append(entry)
            self._retain_exemplars(fam)
            self._invalidate()
            return {
                "family": fam, "accepted": True, "reason": "prototype_updated",
                "n_samples": self._n_samples[fam],
                "n_verified": self._n_verified[fam],
                "updated_prototype": True,
            }

    def observe_batch(self, rows, verified: bool = True) -> dict:
        """批量写入（数据集行 dict 或 (prompt, category, model, envelope) 元组）。

        返回汇总：``{'accepted','rejected','created','updated','buffered',
        'candidates','by_family'}``。评测和「导入历史数据」都走这里，
        避免上层重复写循环。
        """
        accepted = rejected = created = updated = buffered = candidates = 0
        by_family: dict[str, int] = {}
        for row in rows or ():
            if isinstance(row, dict):
                prompt = row.get("prompt", "")
                category = row.get("category")
                model = row.get("model", "")
                envelope = row.get("envelope") or row.get("envelope_str")
            else:
                prompt, category, model, envelope = (list(row) + [""] * 4)[:4]
            res = self.observe(prompt, category, model, envelope, verified=verified)
            if not res["accepted"]:
                rejected += 1
                continue
            accepted += 1
            by_family[res["family"]] = by_family.get(res["family"], 0) + 1
            reason = res["reason"]
            if reason == "prototype_created":
                created += 1
            elif reason == "prototype_updated":
                updated += 1
            elif reason == "buffered_until_gate":
                buffered += 1
            elif reason == "candidate_only":
                candidates += 1
        return {
            "accepted": accepted, "rejected": rejected, "created": created,
            "updated": updated, "buffered": buffered, "candidates": candidates,
            "by_family": by_family, "verified": bool(verified),
        }

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def families(self) -> list[dict]:
        """族列表，按已验证样本数降序。

        每项：``{'name','n_samples','n_verified','n_candidates',
        'has_prototype','updated_at'}``。
        ``n_samples`` 是**已经折进原型**的样本数（未过门槛时是 0），
        ``n_verified`` 是累计验证过的条数——两者在过门槛后相等，
        过门槛前差的就是缓冲里那 1~2 条。界面「原型是否已建立」看 has_prototype。
        """
        with self._lock:
            out = []
            for fam in self._all_families():
                out.append({
                    "name": fam,
                    "n_samples": int(self._n_samples.get(fam, 0)),
                    "n_verified": int(self._n_verified.get(fam, 0)),
                    "n_candidates": len(self._candidates.get(fam, ())),
                    "has_prototype": self._centroids.get(fam) is not None,
                    "updated_at": self._updated_at,
                })
            out.sort(key=lambda r: (-r["n_samples"], -r["n_verified"], r["name"]))
            return out

    def stats(self) -> dict:
        """总览，给界面顶部那排 keyvalue 用。

        ``families`` 是**已建立原型**的族数（能参与预测的族）；
        ``families_known`` 是见过名字的族（含还在攒样本的）。
        """
        with self._lock:
            return {
                "families": len(self._centroids),
                "families_known": len(self._all_families()),
                "samples": int(sum(self._n_samples.values())),
                "verified_total": int(sum(self._n_verified.values())),
                "candidates": int(sum(len(v) for v in self._candidates.values())),
                "updated_at": self._updated_at,
                "dim": self.dim,
                "store": str(self.store_path) if self.store_path else None,
            }

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _to_json(self) -> dict:
        fams = {}
        for fam in self._all_families():
            cen = self._centroids.get(fam)
            fams[fam] = {
                "centroid": [round(float(v), FLOAT_DP) for v in cen] if cen is not None else None,
                "n_samples": int(self._n_samples.get(fam, 0)),
                "n_verified": int(self._n_verified.get(fam, 0)),
                "exemplars": list(self._exemplars.get(fam) or ()),
                "pending": list(self._pending.get(fam) or ()),
                "candidates": list(self._candidates.get(fam) or ()),
            }
        return {
            "version": _STORE_VERSION,
            "dim": self.dim,
            "updated_at": self._updated_at,
            "vocab": {
                "version": self.vocab.get("version", 1),
                "ngrams": list(self.vocab.get("ngrams") or ()),
                "cue_families": list(self.vocab.get("cue_families") or ()),
                "cues": self.vocab.get("cues") or {},
            },
            "families": fams,
        }

    def save(self, path=None) -> Path:
        """原子写盘（先写同目录临时文件再 os.replace）。

        为什么原子：程序被强杀/断电时，半截 JSON 会直接毁掉全部记忆；
        临时文件 + 原子替换保证磁盘上永远是「旧的完整版」或「新的完整版」。

        体积控制：exemplar 每族 ≤32、候选每族 ≤64，原型浮点 6 位。
        1828 条数据也不会把存档撑爆。
        """
        target = Path(path) if path else self.store_path
        if target is None:
            raise ValueError("save() 需要路径：构造时传 store_path，或调用时传 path")
        with self._lock:
            payload = self._to_json()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".meta_memory_", suffix=".json", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.store_path = target
        return target

    @_DualMethod
    def load(self, path=None) -> "MetaMemory":
        """从 JSON 存档读回（返回自身，方便链式调用）。

        既可 ``mem.load(path)``，也可 ``MetaMemory.load(path)``（后者自动新建实例）。

        存档里的词表是权威：维度对不上就报错而不是悄悄降级——
        悄悄降级会让所有旧原型含义错位，比直接失败危险得多。
        """
        target = Path(path) if path else self.store_path
        if target is None:
            raise ValueError("load() 需要路径")
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        with self._lock:
            self.vocab = data.get("vocab") or build_vocab(None)
            self.vocab.pop("_idx", None)  # 反序列化后重建倒排表
            dim_now = vocab_dim(self.vocab)
            dim_saved = int(data.get("dim") or dim_now)
            if dim_saved != dim_now:
                raise ValueError(
                    "存档维度 %d 与词表维度 %d 不一致，拒绝加载（旧原型会全部错位）"
                    % (dim_saved, dim_now)
                )
            self._centroids = {}
            self._n_samples = {}
            self._n_verified = {}
            self._pending = {}
            self._exemplars = {}
            self._candidates = {}
            for fam, rec in (data.get("families") or {}).items():
                cen = rec.get("centroid")
                self._centroids[fam] = (
                    np.asarray(cen, dtype=np.float32) if cen is not None else None
                )
                self._n_samples[fam] = int(rec.get("n_samples") or 0)
                self._n_verified[fam] = int(rec.get("n_verified") or 0)
                self._pending[fam] = list(rec.get("pending") or ())
                self._exemplars[fam] = list(rec.get("exemplars") or ())
                self._candidates[fam] = list(rec.get("candidates") or ())
            self._updated_at = data.get("updated_at")
            self.store_path = target
            self._xcache.clear()   # 词表可能变了，旧向量全部作废
            self._invalidate()
        return self

    @classmethod
    def load_from(cls, path) -> "MetaMemory":
        """从存档新建一个实例（`load` 是实例方法，这个是类方法入口）。"""
        obj = cls(store_path=None)
        return obj.load(path)

    def reset(self) -> None:
        """清空记忆（并把存档删掉）。

        为什么不保留存档只清内存：重开程序又会把旧记忆读回来，
        「重置」就变成了假动作。这里是真删。
        """
        with self._lock:
            self._centroids.clear()
            self._n_samples.clear()
            self._n_verified.clear()
            self._pending.clear()
            self._exemplars.clear()
            self._candidates.clear()
            self._xcache.clear()
            self._invalidate()
            self._updated_at = None
            target = self.store_path
        if target is not None:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
