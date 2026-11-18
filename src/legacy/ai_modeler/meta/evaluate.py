# -*- coding: utf-8 -*-
"""元增量学习 · 评测。

契约见 `docs/architecture/元增量学习核心.md` §5。评测只有两个问题：

1. **它准不准**（``evaluate_holdout``）：80/20 分层切分，报准确率、宏 F1，
   并且**必须**同时报 1-NN 基线。只报一个数没有说服力——如果原型法打不过
   最简单的「找最像的那一条」，那这套东西就没必要存在。
2. **学了新的会不会忘旧的**（``evaluate_incremental``）：按族逐个学，
   每学完一族立刻回测「已见过的族」，最后再整体回测一遍。
   两者的差就是遗忘率（Chaudhry et al. 2018 的标准口径）。

两条口径都写死在代码里，不因为数字难看就改。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np

from .features import build_vocab, embed_many
from .memory import NEW_FAMILY_MIN_VERIFIED, MetaMemory

#: 训练数据默认位置（仓库根/datasets/训练提示词/全部提示词.jsonl）。
DATASET_PATH = (
    Path(__file__).resolve().parents[2] / "datasets" / "训练提示词" / "全部提示词.jsonl"
)


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------


def load_dataset(path=None, exclude_null_category: bool = True) -> list[dict]:
    """读取提示词数据集。

    Args:
        path: jsonl 路径，默认 ``datasets/训练提示词/全部提示词.jsonl``。
        exclude_null_category: 是否剔除 ``category`` 为空的样本。默认 True。

    Returns:
        dict 列表，每项保证有 ``prompt / category / model / envelope`` 四个键。
        ``envelope`` 统一成 3 个 float 或 ``None``。

    为什么必须默认排除空 category：数据集里有 12 条负例
    （``完全不存在的型号XYZ123``、``随便给我建一个零件``…），它们是用来测
    「查不到怎么办」的探针，**不是第 20 个族**。放进训练会让原型网络多出一个
    「什么都不是」的质心，把所有边缘样本吸过去。
    """
    p = Path(path) if path else DATASET_PATH
    rows: list[dict] = []
    skipped_null = skipped_bad = 0
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                skipped_bad += 1
                continue
            cat = raw.get("category")
            if cat is None or not str(cat).strip():
                if exclude_null_category:
                    skipped_null += 1
                    continue
                cat = None
            env = raw.get("envelope")
            if isinstance(env, list) and len(env) >= 3:
                try:
                    env = [float(x) for x in env[:3]]
                except (TypeError, ValueError):
                    env = None
            else:
                env = None
            rows.append({
                "id": raw.get("id", ""),
                "prompt": raw.get("prompt", "") or "",
                "category": cat,
                "model": raw.get("model", "") or "",
                "envelope": env,
                "trust": raw.get("trust"),
                "variant": raw.get("variant"),
                "source": raw.get("source"),
            })
    return rows


def _split_by_family(rows, train_ratio: float, seed: int, group_key: str | None = None):
    """按族**分层**切分训练/测试。

    为什么分层而不是整体洗牌：最小的族只有 6 条。整体随机切分时，
    6 条全被抽进测试集（或全进训练集）的概率并不小，一旦发生，
    那一族的指标准确率就是纯抽签——评测数字会随 seed 剧烈跳动，
    谁也说不清算法到底行不行。分层保证每个族在测试集里至少有 1 条。

    Args:
        rows: `load_dataset` 的输出（category 非空）。
        train_ratio: 训练比例。
        seed: 随机种子（同一个 seed 必须得到同一份切分）。
        group_key: 按此字段**整组**切分（同组样本只会整体进训练或整体进测试）。
            传 ``'model'`` 可以模拟「这个型号一条都没见过」的严格场景：
            数据里每个型号有 3~4 条近重复的提示词变体
            （`内六角螺栓 M5x8` / `M5x8` / `帮我建M5x8内六角螺钉`），
            随机切分时这些变体会分散到两边，测试集等于在训练集里见过自己——
            数字会虚高到接近 1.0。整组切分才是「泛化到新型号」的真实检验。
            ``None``（默认）时按契约 §5 的普通分层随机切分。

    Returns:
        ``(train_rows, test_rows)``。
    """
    by_fam: dict[str, list[dict]] = {}
    for r in rows:
        by_fam.setdefault(r["category"], []).append(r)
    train, test = [], []
    for fam in sorted(by_fam):
        items = list(by_fam[fam])
        random.Random("%s|%s" % (seed, fam)).shuffle(items)  # 按族独立种子，族间互不影响
        n = len(items)
        if n < 2:
            train.extend(items)
            continue
        target = max(1, min(n - 1, int(round(n * (1.0 - train_ratio)))))
        if group_key:
            groups: dict[str, list[dict]] = {}
            for r in items:
                g = str(r.get(group_key) or r.get("prompt") or "")
                groups.setdefault(g, []).append(r)
            keys = list(groups)
            random.Random("%s|%s|g" % (seed, fam)).shuffle(keys)
            picked: list[dict] = []
            for k in keys:
                if len(picked) >= target:
                    break
                picked.extend(groups[k])
            # 必须给训练集留够建原型的样本（新族门槛 3 条），否则这族等于没学
            if len(items) - len(picked) < NEW_FAMILY_MIN_VERIFIED:
                picked = picked[: max(0, n - NEW_FAMILY_MIN_VERIFIED)]
            picked_ids = {id(r) for r in picked}
            test.extend([r for r in items if id(r) in picked_ids])
            train.extend([r for r in items if id(r) not in picked_ids])
        else:
            test.extend(items[:target])
            train.extend(items[target:])
    return train, test


def _f1(precision: float, recall: float) -> float:
    """P、R 都为 0 时按 0 处理（sklearn 的 zero_division=0 口径）。"""
    if precision + recall <= 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _metrics(gold, pred, labels):
    """算准确率 / 宏 F1 / 逐族 P·R·F1 / 混淆矩阵。

    宏 F1 用「测试集里真实出现过的族」求平均：1156:6 的长尾下，
    微平均会被 EGT型材 一个族带跑，宏平均才看得出小族有没有被吃掉。
    """
    n = len(gold)
    acc = (sum(1 for g, p in zip(gold, pred) if g == p) / n) if n else 0.0
    idx = {f: i for i, f in enumerate(labels)}
    conf = [[0] * len(labels) for _ in labels]
    for g, p in zip(gold, pred):
        gi = idx.get(g)
        pi = idx.get(p) if p is not None else None
        if gi is None:
            continue
        if pi is None:
            continue
        conf[gi][pi] += 1

    per_family = {}
    f1s = []
    for fam in labels:
        gi = idx[fam]
        tp = conf[gi][gi]
        support = sum(conf[gi])
        predicted = sum(conf[i][gi] for i in range(len(labels)))
        if support == 0 and predicted == 0:
            continue
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = _f1(precision, recall)
        per_family[fam] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "support": support,
        }
        if support:
            f1s.append(f1)
    macro = float(np.mean(f1s)) if f1s else 0.0
    return {
        "accuracy": round(acc, 6),
        "macro_f1": round(macro, 6),
        "per_family": per_family,
        "confusion": conf,
    }


def _one_nn(train_x, train_y, test_x):
    """1-NN 基线：测试样本取余弦最近的那条训练样本的类别。

    为什么给这个基线：原型法把「同族所有样本」压成一个点，天然丢掉了族内多峰
    的信息。如果这都比不过「直接找最像那一条」，那说明特征空间里
    「同族样本彼此靠近」这个前提根本没成立，该改的是特征而不是原型。
    所有向量都已 L2 归一化，所以内积就是余弦相似度。
    """
    sims = test_x @ train_x.T
    return [train_y[int(i)] for i in np.argmax(sims, axis=1)]


# ---------------------------------------------------------------------------
# 留出评测
# ---------------------------------------------------------------------------

#: 按 `variant` 字段分的消融组。
#: 为什么必须分开报：520 条 `带类别词` 的提示词里直接写着「内六角螺栓」，
#: 而 `family_cues()` 就把这几个词编进了特征位——等于把答案抄进了输入。
#: 混在一起报一个 100% 没有说服力，必须把「有类别词」和「只有型号」拆开。
#:
#:   * ``with_category_word``：带类别词 / 自然语言 / 带规格描述——题面里有族名；
#:   * ``bare``：裸型号 / 简写——题面里基本只有型号（简写的 `型材 SW…` 仍带一点词，
#:     所以再单列一个最严格的 ``strict_bare``，只留纯裸型号）。
VARIANT_GROUPS: dict[str, tuple[str, ...]] = {
    "with_category_word": ("带类别词", "自然语言", "带规格描述"),
    "bare": ("裸型号", "简写"),
    "strict_bare": ("裸型号",),
}


def evaluate_variant_ablation(rows=None, groups=None, **kwargs) -> dict:
    """按 `variant` 分组跑留出评测，分清「读到了词」和「真的认出了族」。

    Args:
        rows: `load_dataset` 的输出；``None`` 时自动读取数据集。
        groups: ``{组名: (variant 值, ...)}``，默认 `VARIANT_GROUPS`。
        **kwargs: 透传给 `evaluate_holdout`。

    Returns:
        ``{组名: evaluate_holdout 的结果, ..., 'n_rows': 总条数}``。
        每组都带 ``baseline_nearest``，便于直接看出原型/混合判决和 1-NN 的差距。
    """
    rows = rows if rows is not None else load_dataset()
    rows = [r for r in rows if r.get("category")]
    out: dict[str, dict] = {"n_rows": len(rows)}
    for name, variants in (groups or VARIANT_GROUPS).items():
        sub = [r for r in rows if r.get("variant") in variants]
        if len(sub) < 20:
            continue
        res = evaluate_holdout(sub, **kwargs)
        res["variants"] = list(variants)
        out[name] = res
    return out


def evaluate_holdout(rows, train_ratio: float = 0.8, seed: int = 0,
                     use_corpus_vocab: bool = True, max_features: int = 1200,
                     group_by_model: bool = False) -> dict:
    """80/20 留出评测：原型网络 vs 1-NN 基线。

    Args:
        rows: `load_dataset` 的输出。
        train_ratio: 训练比例。
        seed: 切分与词表构建的随机种子。
        use_corpus_vocab: 是否用**训练集文本**拟合 n-gram 词表。
            True：模拟「程序已经见过一批提示词」的中期状态（默认，数字更好看，
            也更接近实际部署：词的统计量本来就会随使用积累）。
            False：只用内置领域词表，模拟第一天冷启动。
            两种都必须能跑，评测报告里要分开说。
        max_features: n-gram 词表上限。
        group_by_model: 是否按型号整组切分（同型号的提示词变体不会跨训练/测试）。
            默认 False = 契约 §5 的普通分层随机切分；True 是「泛化到新型号」的
            严格口径，见 `_split_by_family` 的说明。

    Returns:
        契约 §5 要求的 dict：``n_train / n_test / accuracy / macro_f1 /
        per_family / baseline_nearest / confusion``，另附 ``labels`` 等诊断字段。
    """
    rows = [r for r in rows if r.get("category")]
    train, test = _split_by_family(
        rows, train_ratio, seed, group_key="model" if group_by_model else None
    )
    if not train or not test:
        raise ValueError("数据不足以切分：train=%d test=%d" % (len(train), len(test)))

    vocab = (build_vocab([r["prompt"] for r in train], max_features=max_features)
             if use_corpus_vocab else build_vocab(None, max_features=max_features))

    mem = MetaMemory()
    mem.vocab = vocab  # 词表是记忆的一部分，评测里显式换成「只见过训练集」的那套
    mem.observe_batch(train, verified=True)

    labels = sorted({r["category"] for r in rows})
    gold = [r["category"] for r in test]
    pred = [mem.suggest(r["prompt"], k=0).get("family") for r in test]

    met = _metrics(gold, pred, labels)

    # 1-NN 基线：用同一套特征、同一份训练集，只换分类器
    train_x = embed_many([r["prompt"] for r in train], vocab)
    test_x = embed_many([r["prompt"] for r in test], vocab)
    nn_pred = _one_nn(train_x, [r["category"] for r in train], test_x)
    nn_acc = sum(1 for g, p in zip(gold, nn_pred) if g == p) / len(gold)
    nn_met = _metrics(gold, nn_pred, labels)

    return {
        "n_train": len(train),
        "n_test": len(test),
        "accuracy": met["accuracy"],
        "macro_f1": met["macro_f1"],
        "per_family": met["per_family"],
        "baseline_nearest": round(nn_acc, 6),
        "baseline_nearest_macro_f1": nn_met["macro_f1"],
        "confusion": met["confusion"],
        "labels": labels,
        "dim": mem.dim,
        "n_prototypes": len(mem.families()),
        "train_ratio": train_ratio,
        "seed": seed,
        "use_corpus_vocab": bool(use_corpus_vocab),
        "group_by_model": bool(group_by_model),
    }


# ---------------------------------------------------------------------------
# 增量评测
# ---------------------------------------------------------------------------


def _acc_on(mem, family_rows: dict, fams, limit_per_family: int | None = None) -> float:
    """在给定族的测试样本上算准确率。

    Args:
        limit_per_family: 每族最多测几条（``None`` = 全测）。
            评测慢的时候用它采样——注意它会改变样本口径，报告里必须注明。
    """
    gold = pred = 0
    hit = 0
    for fam in fams:
        rows = family_rows.get(fam, ())
        if limit_per_family is not None:
            rows = rows[:limit_per_family]
        for r in rows:
            gold += 1
            if mem.suggest(r["prompt"], k=0).get("family") == fam:
                hit += 1
    return hit / gold if gold else 0.0


def evaluate_incremental(rows, family_order=None, shots_per_family: int = 5,
                         seed: int = 0, max_test_per_family: int | None = None,
                         eval_every: int = 1) -> dict:
    """增量序列评测：一族一族地学，看学新的会不会忘旧的。

    协议（class-incremental，few-shot）：
      * 每族随机抽 ``shots_per_family`` 条当**演示样本**（这一步只学这些），
        其余全部留作该族的测试集；
      * 按 ``family_order`` 逐族学，每学完一步立刻测「截至当前见过的族」
        （``acc_on_seen``）和「全部族」（``acc_on_all``）；
      * 全部学完后再测一遍每个族。

    为什么用 few-shot 而不是把这族的训练集全喂进去：真实使用里新族第一次
    出现时，手里就只有几个样本。全量喂是另一种（更宽松的）实验，
    和「新族来了能快速适配」这个卖点不对应。

    遗忘率（契约 §5，Chaudhry et al. 2018 口径）：
        对每个族 f，``a_f`` = 刚学完 f 那一刻在 f 上的准确率，
        ``b_f`` = 全部学完后在 f 上的准确率；
        ``forgetting = mean_f(a_f - b_f)``。正值表示退步。

    ⚠️ **这个口径下 forgetting 天生接近 0**，别把它当成抗遗忘的证据：
        每个族学完就再也不动它的原型了，后面的族碰不到它。这是「局部原型」
        方案的结构性优点，但也意味着这个指标测不出干扰。
        想看「旧族会不会被新样本打扰」，用 `evaluate_incremental_hard`。

    Args:
        rows: `load_dataset` 的输出。
        family_order: 族的学习顺序；``None`` 时按样本数降序（常见的族先来）。
        shots_per_family: 每族学几条。
        seed: 抽样种子。
        max_test_per_family: 每族最多测几条（``None`` = 全测）。
            默认全测；跑得慢时用它采样，报告里要注明口径变了。
        eval_every: 每几步测一次（默认每步都测）。``>1`` 时只测
            ``step % eval_every == 0`` 的步和最后一步，用于快速跑。

    Returns:
        ``{'steps','forgetting','final_accuracy','order'}``，
        另附 ``per_family_final_accuracy`` 便于判断遗忘是不是被大族带偏。
    """
    rows = [r for r in rows if r.get("category")]
    by_fam: dict[str, list[dict]] = {}
    for r in rows:
        by_fam.setdefault(r["category"], []).append(r)

    demo: dict[str, list[dict]] = {}
    test: dict[str, list[dict]] = {}
    for fam in sorted(by_fam):
        items = list(by_fam[fam])
        random.Random("inc|%s|%s" % (seed, fam)).shuffle(items)
        shots = max(1, min(int(shots_per_family), len(items) - 1)) if len(items) > 1 else len(items)
        demo[fam] = items[:shots]
        test[fam] = items[shots:]

    if family_order is None:
        order = sorted(by_fam, key=lambda f: (-len(by_fam[f]), f))
    else:
        order = [f for f in family_order if f in by_fam]
        order += [f for f in sorted(by_fam) if f not in order]

    # 词表不在这里拟合：增量场景下「将来会见到什么文本」本来就不知道，
    # 用内置领域词表才是诚实的冷启动协议（也顺便测了冷启动路径）。
    mem = MetaMemory()

    steps = []
    acc_after_learning: dict[str, float] = {}
    for i, fam in enumerate(order):
        mem.observe_batch(demo[fam], verified=True)
        seen = order[: i + 1]
        if eval_every > 1 and (i + 1) % eval_every and i != len(order) - 1:
            # 跳过的步也要记「刚学完 f」的基准线，否则遗忘率算不出来
            acc_after_learning[fam] = _acc_on(mem, test, [fam], max_test_per_family)
            continue
        acc_seen = _acc_on(mem, test, seen, max_test_per_family)
        acc_all = _acc_on(mem, test, sorted(by_fam), max_test_per_family)
        steps.append({
            "step": i + 1,
            "family": fam,
            "seen_families": len(seen),
            "acc_on_seen": round(acc_seen, 6),
            "acc_on_all": round(acc_all, 6),
        })
        # 刚学完 f 这一刻在 f 上的准确率——遗忘率的基准线
        acc_after_learning[fam] = _acc_on(mem, test, [fam], max_test_per_family)

    final_per_family = {
        fam: _acc_on(mem, test, [fam], max_test_per_family) for fam in sorted(by_fam)
    }
    drops = [acc_after_learning[f] - final_per_family[f] for f in order if f in acc_after_learning]
    forgetting = float(np.mean(drops)) if drops else 0.0

    return {
        "steps": steps,
        "forgetting": round(forgetting, 6),
        "final_accuracy": steps[-1]["acc_on_all"] if steps else 0.0,
        "order": order,
        "per_family_final_accuracy": {k: round(v, 6) for k, v in final_per_family.items()},
        "shots_per_family": shots_per_family,
        "seed": seed,
        "n_test": sum(len(v) for v in test.values()),
        "n_demo": sum(len(v) for v in demo.values()),
        "dim": mem.dim,
        "max_test_per_family": max_test_per_family,
        "eval_every": eval_every,
    }


def evaluate_incremental_hard(rows, family_order=None, shots_per_family: int = 5,
                              stream_per_family: int = 10, seed: int = 0,
                              max_test_per_family: int | None = 20) -> dict:
    """**困难版**增量评测：先一次建好全部原型，再持续喂新样本，看旧族会不会被碰坏。

    和 `evaluate_incremental` 的区别：那一版每个族学完就再也不更新原型了，
    遗忘率结构性地等于 0（不是算法稳，是压根没人动它）。
    这一版模拟真实的持续使用——程序上线后还会不断收到同一族的新提示词：

      1. **建原型阶段**：按顺序用每族的 ``shots_per_family`` 条演示样本建好全部原型；
      2. **标尺**：测一遍每族准确率 ``b_f``；
      3. **持续学习阶段**：按顺序把每族剩下的 ``stream_per_family`` 条真实样本
         再喂进去（走真实 EMA 更新 + exemplar 淘汰）；
      4. **复测**：再测一遍每族准确率 ``f_f``；
      5. **遗忘** = ``mean_f(b_f - f_f)``。

    这一版能测出两种真实的干扰：
      * 原型被新样本拉走（EMA 系数够不够稳）；
      * exemplar 池满 32 条后淘汰旧样本，kNN 证据被换掉（混合判决的代价）。
    这两种都不会在「每族只学一次」的协议里出现。

    Args:
        rows: `load_dataset` 的输出。
        family_order: 学习顺序；``None`` 时按样本数降序。
        shots_per_family: 建原型用的条数。
        stream_per_family: 之后每族再喂多少条。
        seed: 抽样种子。
        max_test_per_family: 每族最多测几条（默认 20，控制耗时；``None`` = 全测）。

    Returns:
        ``{'forgetting','baseline_accuracy','final_accuracy','steps','order',
        'per_family_before','per_family_after'}``
    """
    rows = [r for r in rows if r.get("category")]
    by_fam: dict[str, list[dict]] = {}
    for r in rows:
        by_fam.setdefault(r["category"], []).append(r)

    demo: dict[str, list[dict]] = {}
    stream: dict[str, list[dict]] = {}
    test: dict[str, list[dict]] = {}
    for fam in sorted(by_fam):
        items = list(by_fam[fam])
        random.Random("hard|%s|%s" % (seed, fam)).shuffle(items)
        # 演示 / 后续流 / 测试 三段互不重叠，避免拿测试样本训练
        shots = max(1, min(int(shots_per_family), max(1, len(items) // 3)))
        n_stream = max(0, min(int(stream_per_family), len(items) - shots - 1))
        demo[fam] = items[:shots]
        stream[fam] = items[shots: shots + n_stream]
        test[fam] = items[shots + n_stream:]

    order = (sorted(by_fam, key=lambda f: (-len(by_fam[f]), f))
             if family_order is None else list(family_order))
    order += [f for f in sorted(by_fam) if f not in order]

    mem = MetaMemory()
    for fam in order:
        mem.observe_batch(demo[fam], verified=True)

    before = {fam: _acc_on(mem, test, [fam], max_test_per_family) for fam in order}
    steps = []
    for i, fam in enumerate(order):
        if stream[fam]:
            mem.observe_batch(stream[fam], verified=True)   # 真实 EMA + exemplar 淘汰
        steps.append({
            "step": i + 1,
            "family": fam,
            "n_stream": len(stream[fam]),
            "acc_on_all": round(_acc_on(mem, test, order, max_test_per_family), 6),
        })
    after = {fam: _acc_on(mem, test, [fam], max_test_per_family) for fam in order}

    drops = [before[f] - after[f] for f in order]
    return {
        "forgetting": round(float(np.mean(drops)) if drops else 0.0, 6),
        "max_drop": round(float(max(drops)) if drops else 0.0, 6),
        "baseline_accuracy": round(float(np.mean([before[f] for f in order])), 6),
        "final_accuracy": round(float(np.mean([after[f] for f in order])), 6),
        "order": order,
        "steps": steps,
        "per_family_before": {k: round(v, 6) for k, v in before.items()},
        "per_family_after": {k: round(v, 6) for k, v in after.items()},
        "shots_per_family": shots_per_family,
        "stream_per_family": stream_per_family,
        "n_test": sum(len(v) for v in test.values()),
        "n_stream": sum(len(v) for v in stream.values()),
        "max_test_per_family": max_test_per_family,
        "seed": seed,
        "dim": mem.dim,
    }


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------


class MetaEvaluator:
    """把「加载数据 + 两种评测」包成一个对象，给界面和脚本用。

    契约 §2 要求导出这个名字；它没有任何新口径，只是把两个函数
    绑在一份已经读好的数据上，免得调用方到处传 rows。
    数据惰性加载：构造时不读盘，第一次真的要用才读（界面里点「跑一次评测」
    不应该在窗口打开时就卡一下）。
    """

    def __init__(self, rows=None, path=None):
        self._rows = rows
        self._path = path

    @property
    def rows(self) -> list[dict]:
        if self._rows is None:
            self._rows = load_dataset(self._path)
        return self._rows

    def holdout(self, **kwargs) -> dict:
        return evaluate_holdout(self.rows, **kwargs)

    def incremental(self, **kwargs) -> dict:
        return evaluate_incremental(self.rows, **kwargs)

    def summary(self) -> dict:
        """一行能塞进状态条的摘要（界面用，别在 UI 线程里跑全量评测）。"""
        h = self.holdout()
        return {
            "accuracy": h["accuracy"],
            "baseline_nearest": h["baseline_nearest"],
            "macro_f1": h["macro_f1"],
            "n_train": h["n_train"],
            "n_test": h["n_test"],
        }

