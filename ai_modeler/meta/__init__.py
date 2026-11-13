# -*- coding: utf-8 -*-
"""元增量学习核心包。

契约：`docs/architecture/元增量学习核心.md`（唯一口径，本包逐条照做）。

对外只暴露这几样：

    # 记忆（推理 / 学习 / 持久化）
    from ai_modeler.meta import MetaMemory, DEFAULT_STORE
    # 评测（留出 + 增量遗忘）
    from ai_modeler.meta import load_dataset, evaluate_holdout, evaluate_incremental
    # 特征（纯函数，界面上想画「这条命中了哪些线索」时用得上）
    from ai_modeler.meta import embed, numeric_tokens, family_cues

依赖边界（契约 §2 硬约束）：**只允许 numpy + 标准库**。
这里不许 import torch / sklearn / tkinter / 其它 ai_modeler 子包——
用户双击启动的是没有 torch 的系统 Python，本包一旦引入重依赖，程序就装不动了。
"""

from __future__ import annotations

from .evaluate import (
    DATASET_PATH,
    VARIANT_GROUPS,
    MetaEvaluator,
    evaluate_holdout,
    evaluate_incremental,
    evaluate_incremental_hard,
    evaluate_variant_ablation,
    load_dataset,
)
from .features import (
    MAX_NUMERIC,
    build_vocab,
    cue_families,
    embed,
    embed_many,
    family_cues,
    normalize,
    numeric_tokens,
    vocab_dim,
)
from .memory import (
    ALPHA_FLOOR,
    DEFAULT_STORE,
    EXEMPLAR_CAP,
    HYBRID_KNN_K,
    HYBRID_KNN_WEIGHT,
    NEW_FAMILY_MIN_VERIFIED,
    MetaMemory,
)

__all__ = [
    # 记忆
    "MetaMemory",
    "DEFAULT_STORE",
    "NEW_FAMILY_MIN_VERIFIED",
    "EXEMPLAR_CAP",
    "ALPHA_FLOOR",
    "HYBRID_KNN_K",
    "HYBRID_KNN_WEIGHT",
    # 评测
    "MetaEvaluator",
    "load_dataset",
    "evaluate_holdout",
    "evaluate_incremental",
    "evaluate_incremental_hard",
    "evaluate_variant_ablation",
    "VARIANT_GROUPS",
    "DATASET_PATH",
    # 特征
    "embed",
    "embed_many",
    "numeric_tokens",
    "family_cues",
    "cue_families",
    "build_vocab",
    "vocab_dim",
    "normalize",
    "MAX_NUMERIC",
    "__version__",
]

__version__ = "1.0.0"
