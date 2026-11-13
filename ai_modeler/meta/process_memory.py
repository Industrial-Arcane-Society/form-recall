# -*- coding: utf-8 -*-
"""工序记忆：让企业流程「越用越准」。

与 `memory.py` 的 MetaMemory 是**两层记忆，别混**：

    MetaMemory    记「这个零件属于哪一族、典型包络多少」——**建模对象**层面
    ProcessMemory 记「这类任务该走哪道工序、这道工序历史上栽在哪」——**做法**层面

为什么工序记忆单独一个类：
    工序只有十几条，数量不会随使用暴涨，用余弦相似度检索就够，不需要原型网络的
    EMA 与 exemplar 池。硬塞进 MetaMemory 会把「零件族」的统计口径搅乱。

学习发生在三个地方：
    1. 导入工序库时：每道工序的「动作+判据+输出」被向量化，成为可检索的条目；
    2. 每次执行后 record()：累计试验次数、成功次数；
    3. 失败时：把失败原因归并进该工序，下次命中时一并带出来。

依赖边界：只 numpy + 标准库，不 import workflow 包。工序数据由调用方以 dict
列表传入（见 `to_rows`），这样换企业流程表不用改本模块。
"""
from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from .features import build_vocab, embed

#: 失败原因最多记这么多条，防止备注无限膨胀把记忆库撑爆
MAX_FAIL_NOTES = 16


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度。embed 已做 L2 归一化，点积即可，但仍防一手零向量。"""
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


@dataclass
class StepRecord:
    """一道工序的记忆条目。"""

    step_id: str
    stage: str
    text: str                    # 向量化用的检索文本
    status: str = ""             # 视频证实 / 报告推断 / 待企业确认
    trials: int = 0              # 执行过几次
    success: int = 0             # 其中成功几次
    fail_notes: list = field(default_factory=list)
    vector: list = field(default_factory=list)
    # 同一道工序的**别的说法**。工序表里一句抽象描述不够用：真实输入是
    # 「在 CATIA 里打开钣金件调视角」这种口语，与「确认输入数模与定位基准」的
    # 词面几乎不重合。企业视频逐段看出来的场景描述就存这里，检索时取最大相似度。
    aliases: list = field(default_factory=list)
    alias_vectors: list = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """没执行过时返回 -1，而不是 0——「还没跑过」和「每次都失败」是两回事。"""
        if self.trials <= 0:
            return -1.0
        return self.success / self.trials

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id, "stage": self.stage, "text": self.text,
            "status": self.status, "trials": self.trials, "success": self.success,
            "fail_notes": list(self.fail_notes), "vector": self.vector,
            "aliases": list(self.aliases),
            "alias_vectors": [list(v) for v in self.alias_vectors],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StepRecord":
        return cls(
            step_id=str(data.get("step_id") or ""),
            stage=str(data.get("stage") or ""),
            text=str(data.get("text") or ""),
            status=str(data.get("status") or ""),
            trials=int(data.get("trials") or 0),
            success=int(data.get("success") or 0),
            fail_notes=[str(x) for x in (data.get("fail_notes") or [])][-MAX_FAIL_NOTES:],
            vector=[float(x) for x in (data.get("vector") or [])],
            aliases=[str(x) for x in (data.get("aliases") or [])],
            alias_vectors=[[float(y) for y in v]
                           for v in (data.get("alias_vectors") or [])],
        )


class ProcessMemory:
    """工序记忆。"""

    def __init__(self, store_path: str | None = None):
        self.store_path = store_path
        self.records: dict[str, StepRecord] = {}
        # 工序语料有**自己的词表**。借用 MetaMemory 的零件族词表会出事：
        # 那套词表装的是「螺栓/型材/定位销」这类族名线索，工序文本里
        # 「托块/压臂/铰接孔」基本命中不到，embed 出来接近零向量，
        # 余弦相似度全是 0 或 1，检索等于乱指。实测踩过这个坑。
        self.vocab: dict | None = None
        self.updated_at = ""
        if store_path:
            try:
                self.load()
            except Exception:
                # 首次运行没有文件是正常的；读坏了也退回空库，不带病运行
                pass

    # ---------------- 导入 ----------------

    def learn_steps(self, rows, force: bool = False, scenes=None) -> dict:
        """把工序库灌进记忆。

        rows 每项至少要有 step_id / stage / text（检索文本）。重复导入默认只补缺，
        不覆盖已有的 trial 统计——否则每次重灌都会把「跑过 30 次」清零。

        scenes: ``{step_id: [场景描述, ...]}``，来源是企业视频的逐段观察。
        工序表里一句抽象描述撑不住真实输入——真人说的是「在 CATIA 里打开钣金件
        调视角」，跟「确认输入数模与定位基准」词面几乎不重合。这些场景描述既进
        词表（让词表认识「钣金面」「结构树」「干涉」），也各自存一条向量，
        检索时取最大值。

        每次导入都会用**当前全量文本**重建词表与向量：工序只有十几条，
        重建成本可忽略，但换了一家企业的流程表之后，旧向量立刻会失效——
        维度含义变了，继续用旧向量等于拿另一套坐标去量。
        """
        scenes = scenes or {}
        texts = []
        clean_rows = []
        for row in rows:
            step_id = str(row.get("step_id") or "").strip()
            text = str(row.get("text") or "").strip()
            if not step_id or not text:
                continue
            texts.append(text)
            clean_rows.append((row, step_id, text))
        for group in scenes.values():
            texts.extend(str(x).strip() for x in group if str(x).strip())

        if not clean_rows:
            return {"added": 0, "updated": 0, "skipped": 0, "total": len(self.records)}

        # 语料变大后 max_features 提到 600：场景描述比工序摘要长，400 会截掉
        # 一部分该留的词（比如「钣金」「结构树」这种只在场景里出现的）。
        self.vocab = build_vocab(texts, max_features=600)

        added = updated = skipped = 0
        for row, step_id, text in clean_rows:
            vec = embed(text, self.vocab)
            vec_list = [round(float(x), 6) for x in vec]
            alias_list = [str(x).strip() for x in (scenes.get(step_id) or [])
                          if str(x).strip()]
            alias_vecs = [[round(float(y), 6) for y in embed(a, self.vocab)]
                          for a in alias_list]

            old = self.records.get(step_id)
            if old is not None and not force:
                # 只刷新文本与向量，保留 trials/success —— 这是「学习」的下半截
                old.text = text
                old.stage = str(row.get("stage") or old.stage)
                old.status = str(row.get("status") or old.status)
                old.vector = vec_list
                old.aliases = alias_list
                old.alias_vectors = alias_vecs
                updated += 1
                continue
            self.records[step_id] = StepRecord(
                step_id=step_id,
                stage=str(row.get("stage") or ""),
                text=text,
                status=str(row.get("status") or ""),
                vector=vec_list,
                aliases=alias_list,
                alias_vectors=alias_vecs,
            )
            added += 1
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        return {"added": added, "updated": updated, "skipped": skipped,
                "total": len(self.records), "scenes": sum(len(v) for v in scenes.values())}

    # ---------------- 检索 ----------------

    def suggest(self, description: str, k: int = 3) -> dict:
        """任务描述 → 候选工序。

        返回的每一项都带「历史成功率」与「常见失败」，调用方据此决定要不要
        提前提醒工程师。未命中任何工序时返回空列表 + 明确说明，不编。
        """
        text = str(description or "").strip()
        if not text or not self.records:
            return {"query": text, "matches": [],
                    "note": "工序记忆还是空的，先导入企业流程表。" if not self.records
                    else "描述为空。"}
        query = embed(text, self.vocab)
        scored = []
        for rec in self.records.values():
            if not rec.vector or len(rec.vector) != len(query):
                continue
            score = _cosine(query, np.asarray(rec.vector, dtype=float))
            # 场景别名逐条比、取最大：同一道工序在视频里有好几种说法，
            # 命中任意一种都算命中这道工序。
            for alias_vec in rec.alias_vectors:
                if len(alias_vec) != len(query):
                    continue
                alias_score = _cosine(query, np.asarray(alias_vec, dtype=float))
                if alias_score > score:
                    score = alias_score
            scored.append((score, rec))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        # 阈值用**相对**的：只保留与最佳命中相差不到四成的候选。
        # 早先用绝对阈值 0.5，短查询（「开安装孔」）整体相似度都低于 0.5，
        # 结果只剩一条，本该出现的 S10 被 S08 挤掉了——绝对阈值在短文本上失效。
        best = scored[0][0] if scored else 0.0
        matches = []
        for score, rec in scored[:k]:
            if matches and score < best * 0.6:
                break
            matches.append({
                "step_id": rec.step_id,
                "stage": rec.stage,
                "score": round(score, 3),
                "text": rec.text,
                "status": rec.status,
                "trials": rec.trials,
                "success": rec.success,
                "success_rate": (None if rec.success_rate < 0
                                 else round(rec.success_rate, 3)),
                "fail_hints": list(rec.fail_notes[-3:]),
            })
        return {"query": text, "matches": matches}

    # ---------------- 学习 ----------------

    def record(self, step_id: str, outcome: str, note: str = "") -> dict:
        """记一次执行结局。

        outcome: success / fail / skipped
        只有 success 与 fail 计入 trials；skipped 表示这一步没做（不参与、
                 不适用），计入会让成功率失真，所以单列。
        """
        key = str(step_id or "").strip()
        if not key:
            raise ValueError("step_id 不能为空。")
        rec = self.records.get(key)
        if rec is None:
            raise ValueError("未知工序：%s（先把流程表导入工序库）" % key)
        kind = str(outcome or "").strip().lower()
        if kind not in ("success", "fail", "skipped"):
            raise ValueError("outcome 只能是 success / fail / skipped，收到 %r" % outcome)

        if kind in ("success", "fail"):
            rec.trials += 1
            if kind == "success":
                rec.success += 1
        if kind == "fail" and note:
            # 这里**不能**用 features.normalize：那是给向量化做的大小写/标点归一，
            # 会把「检查未通过：压臂最小间距（L=65 mm…）」压成小写去标点的一串，
            # 人看不懂。失败原因是要展示给人看的，只做轻清洗（折叠空白 + 限长）。
            cleaned = " ".join(str(note).split())[:120]
            if cleaned and cleaned not in rec.fail_notes:
                rec.fail_notes.append(cleaned)
                rec.fail_notes = rec.fail_notes[-MAX_FAIL_NOTES:]
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        return {
            "step_id": rec.step_id, "outcome": kind,
            "trials": rec.trials, "success": rec.success,
            "success_rate": (None if rec.success_rate < 0 else round(rec.success_rate, 3)),
        }

    # ---------------- 查询 ----------------

    def advice(self, step_id: str) -> dict:
        """某道工序攒下来的经验。给界面或工具用。"""
        rec = self.records.get(str(step_id or "").strip())
        if rec is None:
            return {"step_id": step_id, "found": False}
        return {
            "step_id": rec.step_id, "found": True, "stage": rec.stage,
            "status": rec.status, "trials": rec.trials, "success": rec.success,
            "success_rate": (None if rec.success_rate < 0
                             else round(rec.success_rate, 3)),
            "fail_hints": list(rec.fail_notes),
        }

    def hot_failures(self, top: int = 5) -> list:
        """全局最常见的失败原因。用来回答「流程最常栽在哪」。"""
        counter = Counter()
        for rec in self.records.values():
            for note in rec.fail_notes:
                counter[note] += 1
        return [{"reason": r, "count": n} for r, n in counter.most_common(top)]

    def stats(self) -> dict:
        total_trials = sum(r.trials for r in self.records.values())
        total_success = sum(r.success for r in self.records.values())
        attempted = [r for r in self.records.values() if r.trials > 0]
        return {
            "steps": len(self.records),
            "attempted_steps": len(attempted),
            "trials": total_trials,
            "success": total_success,
            "success_rate": (None if total_trials == 0
                             else round(total_success / total_trials, 3)),
            "updated_at": self.updated_at,
            "store": self.store_path,
        }

    # ---------------- 持久化 ----------------

    def save(self, path: str | None = None) -> str:
        target = path or self.store_path
        if not target:
            raise ValueError("没有指定存储路径。")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": self.updated_at,
            # 词表必须跟着记忆一起存：换了词表，每个维度的含义就变了，
            # 旧向量立刻报废（这一点与 MetaMemory 的维度契约同理）。
            "vocab": self.vocab,
            "records": {k: v.to_dict() for k, v in self.records.items()},
        }
        with open(target, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        return target

    def load(self, path: str | None = None) -> int:
        target = path or self.store_path
        if not target or not os.path.isfile(target):
            return 0
        with open(target, encoding="utf-8") as f:
            payload = json.load(f)
        records = payload.get("records") or {}
        self.records = {k: StepRecord.from_dict(v) for k, v in records.items()}
        self.vocab = payload.get("vocab") or None
        self.updated_at = str(payload.get("updated_at") or "")
        return len(self.records)

    def reset(self) -> None:
        self.records = {}
        self.updated_at = ""


#: 默认存储位置：项目根 kb 下，与零件族记忆并列，但**不同文件**。
DEFAULT_PROCESS_STORE = "kb/process_memory.json"
