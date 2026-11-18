# -*- coding: utf-8 -*-
"""元增量学习核心的单元测试。

覆盖契约 §8 要求的六项，外加几项容易悄悄坏掉的边界：

  1. 冷启动 ``suggest`` 不抛异常（主程序第一天就是这个状态）
  2. 写入门禁：``verified=False`` 不涨样本、不动原型
  3. 新族 ≥3 门槛
  4. EMA 不把原型拖垮（一条离群样本，漂移受限）
  5. 持久化往返（含维度校验）
  6. ``evaluate_holdout`` 跑通并给出 1-NN 基线

运行：``python -m pytest tests/test_meta_memory.py -q``
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from ai_modeler.meta import (
    DEFAULT_STORE,
    EXEMPLAR_CAP,
    NEW_FAMILY_MIN_VERIFIED,
    MetaMemory,
    build_vocab,
    embed,
    evaluate_holdout,
    evaluate_incremental,
    evaluate_incremental_hard,
    evaluate_variant_ablation,
    family_cues,
    load_dataset,
    numeric_tokens,
)
from ai_modeler.meta.evaluate import DATASET_PATH


def _mem(**kw) -> MetaMemory:
    """测试里永远用内置词表 + 不落盘的实例。"""
    m = MetaMemory(**kw)
    m.vocab = build_vocab(None)
    return m


class FeatureTests(unittest.TestCase):
    """特征层：型号数字、线索词、向量形状。"""

    def test_numeric_tokens_follows_contract_examples(self):
        # 契约 §3 的四个例子必须逐字对得上
        self.assertEqual(numeric_tokens("M5x8"), [5.0, 8.0])
        self.assertEqual(numeric_tokens("SW80x088"), [80.0, 88.0])
        self.assertEqual(numeric_tokens("EGT001"), [1.0])
        self.assertEqual(numeric_tokens("直径 12 的孔"), [12.0])
        # 小数型号不能被截成整数（EGT016.1 与 EGT0161 不是一回事）
        self.assertEqual(numeric_tokens("EGT016.1"), [16.1])
        # 最多 6 个
        self.assertEqual(len(numeric_tokens("1 2 3 4 5 6 7 8")), 6)
        # 没有数字
        self.assertEqual(numeric_tokens("随便建一个"), [])

    def test_family_cues_covers_at_least_13_families_with_4_words(self):
        cues = family_cues()
        self.assertGreaterEqual(len(cues), 13)
        for name, words in cues.items():
            self.assertGreaterEqual(len(words), 4, "族 %s 线索词不足 4 个" % name)

    def test_embed_is_unit_length_and_dim_follows_vocab(self):
        v = build_vocab(None)
        x = embed("内六角螺栓 M5x8", v)
        self.assertEqual(x.shape[0], len(v["ngrams"]) + 6 + len(v["cue_families"]))
        self.assertAlmostEqual(float(np.linalg.norm(x)), 1.0, places=5)
        # 显式维度契约
        v2 = build_vocab(None, embed_dim=64)
        self.assertEqual(embed("任意文本", v2).shape[0], 64)

    def test_embed_survives_empty_and_garbage(self):
        v = build_vocab(None)
        for text in ("", "   ", "🙂", "???", None):
            x = embed(text, v)  # type: ignore[arg-type]
            self.assertEqual(x.shape[0], len(v["ngrams"]) + 6 + len(v["cue_families"]))
            self.assertTrue(np.all(np.isfinite(x)))


class ColdStartTests(unittest.TestCase):
    """冷启动：空记忆必须能回答，而不是抛异常。"""

    def test_empty_memory_suggest_returns_none(self):
        m = _mem()
        out = m.suggest("内六角螺栓 M5x8")
        self.assertIsNone(out["family"])
        self.assertEqual(out["confidence"], 0.0)
        self.assertEqual(out["family_scores"], {})
        self.assertIsNone(out["envelope_hint"])
        self.assertEqual(out["similar"], [])

    def test_empty_memory_stats(self):
        st = _mem().stats()
        self.assertEqual(st["families"], 0)
        self.assertEqual(st["samples"], 0)
        self.assertEqual(st["candidates"], 0)
        self.assertGreater(st["dim"], 0)

    def test_default_store_is_runtime_artifact_under_kb(self):
        self.assertEqual(DEFAULT_STORE.name, "meta_memory.json")
        self.assertEqual(DEFAULT_STORE.parent.name, "kb")


class WriteGateTests(unittest.TestCase):
    """写入门禁：未验证的样本绝对不能碰原型。"""

    def _seed_family(self, m, n=6, fam="内六角螺钉"):
        for i in range(n):
            m.observe("内六角螺栓 M5x%d" % (8 + i), fam, "M5x%d" % (8 + i), verified=True)

    def test_unverified_does_not_touch_prototype(self):
        m = _mem()
        self._seed_family(m)
        before = m._centroids["内六角螺钉"].copy()
        n_before = m.families()[0]["n_samples"]

        res = m.observe("完全乱写的一坨 UCBH99ZZZZ999", "内六角螺钉",
                        "UCBH99", verified=False)
        self.assertFalse(res["updated_prototype"])
        self.assertEqual(res["reason"], "candidate_only")
        self.assertEqual(res["n_samples"], n_before)

        np.testing.assert_allclose(m._centroids["内六角螺钉"], before, rtol=0, atol=0)
        rec = [f for f in m.families() if f["name"] == "内六角螺钉"][0]
        self.assertEqual(rec["n_samples"], n_before)
        self.assertEqual(rec["n_candidates"], 1)

    def test_unverified_only_memory_still_cannot_predict(self):
        m = _mem()
        for i in range(10):
            m.observe("内六角螺栓 M5x%d" % i, "内六角螺钉", verified=False)
        self.assertIsNone(m.suggest("内六角螺栓 M5x8")["family"])
        self.assertEqual(m.stats()["candidates"], 10)

    def test_empty_category_is_rejected(self):
        m = _mem()
        res = m.observe("完全不存在的型号XYZ123", None, verified=True)
        self.assertFalse(res["accepted"])
        self.assertEqual(res["reason"], "rejected_no_category")
        self.assertEqual(m.stats()["families"], 0)


class NewFamilyGateTests(unittest.TestCase):
    """新族 ≥3 门槛：少于 3 条 verified 不建原型。"""

    def test_below_gate_buffers_only(self):
        m = _mem()
        for i in range(NEW_FAMILY_MIN_VERIFIED - 1):
            res = m.observe("定位销 KMS-M01-D15%d-A20" % i, "定位销", verified=True)
            self.assertEqual(res["reason"], "buffered_until_gate")
            self.assertFalse(res["updated_prototype"])
        rec = m.families()[0]
        self.assertEqual(rec["n_verified"], NEW_FAMILY_MIN_VERIFIED - 1)
        self.assertEqual(rec["n_samples"], 0)
        self.assertFalse(rec["has_prototype"])

        # 门槛管的是「建原型」，不是「不许用证据」：这 2 条已验证样本还在 pending 里，
        # 混合判决的 kNN 分支可以用它们给出族名（置信度偏低），不能判 None。
        out = m.suggest("定位销 KMS-M01-P077-A20")
        self.assertEqual(out["family"], "定位销")
        self.assertLessEqual(out["confidence"], 1.0)

    def test_gate_opens_exactly_at_three(self):
        m = _mem()
        for i in range(NEW_FAMILY_MIN_VERIFIED - 1):
            m.observe("定位销 KMS-M01-D15%d-A20" % i, "定位销", verified=True)
        res = m.observe("定位销 KMS-M01-D159-A20", "定位销", verified=True)
        self.assertEqual(res["reason"], "prototype_created")
        self.assertTrue(res["updated_prototype"])
        rec = m.families()[0]
        self.assertEqual(rec["n_samples"], NEW_FAMILY_MIN_VERIFIED)
        self.assertTrue(rec["has_prototype"])
        self.assertEqual(m.suggest("定位销 KMS-M01-P077-A20")["family"], "定位销")


class HybridDecisionTests(unittest.TestCase):
    """混合判决：原型 + exemplar kNN，只给型号也要认得出族。"""

    def _memory(self):
        m = _mem()
        # 两族各自的型号跨度都很大（模拟裸型号口径：题面里没有族名可抄）
        for i, d in enumerate((5, 6, 8, 10, 12)):
            m.observe("M%dx%d" % (d, 8 + i), "内六角螺钉", verified=True)
        for i, d in enumerate((159, 177, 179, 197, 199)):
            m.observe("KMS-M01-D%d-A20" % d, "定位销", verified=True)
        return m

    def test_unseen_model_is_assigned_to_right_family(self):
        m = self._memory()
        self.assertEqual(m.suggest("M12x80")["family"], "内六角螺钉")
        self.assertEqual(m.suggest("KMS-M01-Z999-A29")["family"], "定位销")

    def test_exemplar_branch_can_override_prototype_ranking(self):
        """A 族原型中心离查询更近，但 B 族有几乎一样的 exemplar，判决该给 B。"""
        m = _mem()
        # A 族：一堆散开的中等型号，原型居中 → 对查询的距离中等
        for i in range(8):
            m.observe("内六角螺栓 M%dx%d" % (6 + i % 3, 20 + i * 5), "内六角螺钉", verified=True)
        # B 族：样本少但其中一条和查询几乎一样
        m.observe("EGT型材 EGT001 SW80x088", "EGT型材", verified=True)
        m.observe("EGT型材 EGT001 SW80x118", "EGT型材", verified=True)
        m.observe("EGT型材 EGT001 SW80x148", "EGT型材", verified=True)
        out = m.suggest("EGT型材 EGT001 SW80x090")
        self.assertEqual(out["family"], "EGT型材")
        # 混合分里该族应当排第一
        self.assertEqual(next(iter(out["family_scores"])), "EGT型材")



class EmaRobustnessTests(unittest.TestCase):
    """EMA 抗离群：成熟的族不会被一条怪样本带偏。"""

    def test_outlier_barely_moves_a_mature_prototype(self):
        m = _mem()
        fam = "内六角螺钉"
        for i in range(25):
            m.observe("内六角螺栓 M5x%d 圆柱头" % (6 + i), fam, verified=True)
        before = m._centroids[fam].copy()

        m.observe("内六角螺栓 M99x999 圆柱头 超长描述 无关内容 乱七八糟", fam, verified=True)
        after = m._centroids[fam]
        drift = float(np.linalg.norm(after - before))

        # α = max(0.05, 1/26) = 0.05：单条最多把原型推 5% * 2.0 = 0.1
        self.assertLess(drift, 0.15, "EMA 被一条离群样本拖走了 %.4f" % drift)
        self.assertGreater(drift, 0.0, "原型应该确实挪动了一点点")
        # 老样本仍然判得对
        self.assertEqual(m.suggest("内六角螺栓 M5x10")["family"], fam)

    def test_alpha_floor_kicks_in_for_large_families(self):
        m = _mem()
        fam = "EGT型材"
        for i in range(60):
            m.observe("EGT型材 EGT001 SW80x%03d" % i, fam, verified=True)
        # n=60 -> 1/(n+1) 远小于 0.05，实际生效的是地板值
        self.assertLess(1.0 / 61.0, 0.05)
        before = m._centroids[fam].copy()
        m.observe("EGT连接件 EGT013 按真实尺寸建模", fam, verified=True)  # 塞一条别的族的
        drift = float(np.linalg.norm(m._centroids[fam] - before))
        self.assertLess(drift, 0.12)
        # 而且它没有被那条样本带成「连接件」
        self.assertEqual(m.suggest("EGT型材 EGT001 SW80x200")["family"], fam)


class ConfidenceTests(unittest.TestCase):
    """confidence 是族间区分度，不是概率。"""

    def _two_family_memory(self):
        m = _mem()
        for p in ("内六角螺栓 M5x8", "内六角螺栓 M5x10", "内六角螺钉 M6x10"):
            m.observe(p, "内六角螺钉", verified=True)
        for p in ("定位销 KMS-M01-D159-A20", "定位销 KMS-M01-P077-A20",
                  "定位销 KMS-M01-P079-A20"):
            m.observe(p, "定位销", verified=True)
        return m

    def test_confidence_in_range_and_clear_case_is_high(self):
        m = self._two_family_memory()
        out = m.suggest("内六角螺栓 M5x12")
        self.assertEqual(out["family"], "内六角螺钉")
        self.assertGreater(out["confidence"], 0.7)
        self.assertLessEqual(out["confidence"], 1.0)

    def test_family_scores_are_sorted_descending(self):
        m = self._two_family_memory()
        out = m.suggest("内六角螺栓 M5x12")
        scores = list(out["family_scores"].values())
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len(out["family_scores"]), 2)

    def test_similar_is_top_k_and_has_contract_fields(self):
        m = self._two_family_memory()
        out = m.suggest("内六角螺栓 M5x12", k=3)
        self.assertLessEqual(len(out["similar"]), 3)
        for item in out["similar"]:
            for key in ("prompt", "category", "model", "envelope", "score"):
                self.assertIn(key, item)
        self.assertEqual(out["similar"][0]["category"], "内六角螺钉")

    def test_envelope_hint_is_per_axis_median(self):
        m = _mem()
        for i, env in enumerate(([10, 10, 12], [10, 10, 14], [10, 10, 30])):
            m.observe("垫片垫块 KMS-M09-%02d" % i, "垫片垫块", envelope=env, verified=True)
        out = m.suggest("垫片垫块 KMS-M09-01")
        self.assertEqual(out["family"], "垫片垫块")
        self.assertEqual(out["envelope_hint"], [10.0, 10.0, 14.0])  # 中位数不吃离群值


class PersistenceTests(unittest.TestCase):
    """持久化：往返一致、维度校验、体积受控。"""

    def _populated(self, m):
        for i in range(8):
            m.observe("内六角螺栓 M5x%d" % (8 + i), "内六角螺钉", "M5x%d" % (8 + i),
                      envelope=[10, 10, 13], verified=True)
        for i in range(5):
            m.observe("EGT型材 EGT001 SW80x%03d" % (80 + i), "EGT型材",
                      envelope=[80, 80, 500], verified=True)
        return m

    def test_roundtrip_preserves_state(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sub" / "meta_memory.json"
            m = self._populated(_mem())
            m.save(path)
            self.assertTrue(path.exists())

            loaded = MetaMemory.load_from(path)
            self.assertEqual(loaded.stats()["dim"], m.stats()["dim"])
            self.assertEqual(loaded.stats()["families"], m.stats()["families"])
            self.assertEqual(loaded.stats()["samples"], m.stats()["samples"])
            for fam, cen in m._centroids.items():
                np.testing.assert_allclose(loaded._centroids[fam], cen, atol=1e-5)
            # 预测结果也必须一致
            a = m.suggest("内六角螺栓 M5x12")
            b = loaded.suggest("内六角螺栓 M5x12")
            self.assertEqual(a["family"], b["family"])
            self.assertAlmostEqual(a["confidence"], b["confidence"], places=5)
            self.assertEqual(a["envelope_hint"], [10.0, 10.0, 13.0])

    def test_store_exemplar_pool_is_capped(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "meta_memory.json"
            m = _mem()
            for i in range(EXEMPLAR_CAP * 3):
                m.observe("内六角螺栓 M5x%d 变体%d" % (8 + i, i), "内六角螺钉", verified=True)
            m.save(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            pool = raw["families"]["内六角螺钉"]["exemplars"]
            self.assertLessEqual(len(pool), EXEMPLAR_CAP)
            # 原型浮点做了截断，存档体积不该随样本数线性膨胀
            self.assertLess(path.stat().st_size, 900_000)

    def test_load_works_both_ways(self):
        """契约写的是 `def load(cls, path)`，集成方常写 `mem.load(path)`——两种都得能用。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "meta_memory.json"
            m = self._populated(_mem())
            m.save(path)
            inst = MetaMemory().load(path)       # 实例方法形式
            cls_ = MetaMemory.load(path)         # 类方法形式（自动新建实例）
            self.assertEqual(inst.stats()["samples"], cls_.stats()["samples"])
            self.assertEqual(inst.suggest("内六角螺栓 M5x10")["family"],
                             cls_.suggest("内六角螺栓 M5x10")["family"])

    def test_dimension_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "meta_memory.json"
            m = self._populated(_mem())
            m.save(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["dim"] = raw["dim"] + 1  # 模拟词表漂移
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                MetaMemory.load_from(path)

    def test_reset_clears_memory_and_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "meta_memory.json"
            m = self._populated(_mem())
            m.save(path)
            m.reset()
            self.assertEqual(m.stats()["families"], 0)
            self.assertFalse(path.exists())


class DatasetTests(unittest.TestCase):
    """数据加载口径。"""

    def test_negative_examples_are_excluded(self):
        self.assertTrue(DATASET_PATH.exists(), "缺少数据集：%s" % DATASET_PATH)
        kept = load_dataset()
        full = load_dataset(exclude_null_category=False)
        self.assertEqual(len(full), 1828)
        self.assertEqual(len(kept), 1816)          # 1828 - 12 条 category=null
        self.assertEqual(len(kept) - len(full) + 0, -12)
        self.assertTrue(all(r["category"] for r in kept))
        self.assertEqual(len({r["category"] for r in kept}), 19)


class EvaluationTests(unittest.TestCase):
    """评测：能跑通，而且**必须**带 1-NN 基线。"""

    @classmethod
    def setUpClass(cls):
        cls.rows = load_dataset()

    def test_holdout_reports_prototype_and_baseline(self):
        r = evaluate_holdout(self.rows)
        for key in ("n_train", "n_test", "accuracy", "macro_f1", "per_family",
                    "baseline_nearest", "confusion"):
            self.assertIn(key, r)
        self.assertGreater(r["n_train"], r["n_test"])
        self.assertEqual(r["n_train"] + r["n_test"], len(self.rows))
        self.assertGreaterEqual(r["accuracy"], 0.5)
        self.assertGreaterEqual(r["macro_f1"], 0.5)
        self.assertGreaterEqual(r["baseline_nearest"], 0.0)
        self.assertEqual(len(r["confusion"]), len(r["labels"]))
        self.assertEqual(sum(map(sum, r["confusion"])), r["n_test"])

    def test_holdout_is_deterministic(self):
        a = evaluate_holdout(self.rows, seed=7)
        b = evaluate_holdout(self.rows, seed=7)
        self.assertEqual(a["accuracy"], b["accuracy"])
        self.assertEqual(a["n_train"], b["n_train"])

    def test_holdout_model_disjoint_split_is_wired(self):
        r = evaluate_holdout(self.rows, group_by_model=True)
        self.assertTrue(r["group_by_model"])
        self.assertGreater(r["n_test"], 0)
        # 同一型号的变体不能同时出现在训练集和测试集里
        train, test = _split_model_disjoint(self.rows)
        train_models = {(r["category"], r["model"]) for r in train}
        for r in test:
            self.assertNotIn((r["category"], r["model"]), train_models)

    def test_incremental_forgetting_is_reported(self):
        r = evaluate_incremental(self.rows)
        self.assertIn("forgetting", r)
        self.assertIn("final_accuracy", r)
        self.assertEqual(len(r["steps"]), len(r["order"]))
        self.assertEqual(r["steps"][-1]["seen_families"], len(r["order"]))
        # acc_on_all 应该随见过族数增加而单调上升（每步只多认识了几个族）
        accs = [s["acc_on_all"] for s in r["steps"]]
        self.assertEqual(accs, sorted(accs))
        self.assertGreaterEqual(r["final_accuracy"], 0.9)

    def test_incremental_sampling_knobs_keep_forgetting(self):
        """采样口径变快，但遗忘率仍然算得出来（不能因为跳过步就变成 0 或 NaN）。"""
        r = evaluate_incremental(self.rows, max_test_per_family=10, eval_every=4)
        self.assertEqual(len(r["steps"]), 1 + len(r["order"]) // 4)
        self.assertEqual(set(r["per_family_final_accuracy"]), set(r["order"]))

    def test_hard_incremental_measures_continued_learning(self):
        """困难版：建好原型后继续喂真实样本，旧族不该被打扰。"""
        r = evaluate_incremental_hard(self.rows, stream_per_family=10)
        self.assertIn("forgetting", r)
        self.assertIn("max_drop", r)
        self.assertEqual(len(r["order"]), len(r["per_family_before"]))
        # 允许 ±0.05 的抖动（采样测试集、exemplar 池换代），
        # 但绝不允许某一族被系统性打垮
        self.assertLessEqual(r["max_drop"], 0.05)
        self.assertGreaterEqual(r["final_accuracy"], r["baseline_accuracy"] - 0.05)

    def test_variant_ablation_splits_category_word_leak(self):
        """有类别词的组会「读到答案」，裸型号组才是真本事——两组必须分开报。"""
        out = evaluate_variant_ablation(self.rows)
        self.assertIn("with_category_word", out)
        self.assertIn("bare", out)
        self.assertIn("baseline_nearest", out["bare"])
        self.assertEqual(out["n_rows"], len(self.rows))
        # 裸型号口径下混合判决不该输给 1-NN
        self.assertGreaterEqual(out["bare"]["accuracy"],
                                out["bare"]["baseline_nearest"] - 0.02)



def _split_model_disjoint(rows):
    """复现 `evaluate_holdout(group_by_model=True)` 的切分，供断言使用。"""
    from ai_modeler.meta.evaluate import _split_by_family
    return _split_by_family([r for r in rows if r.get("category")], 0.8, 0, group_key="model")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
