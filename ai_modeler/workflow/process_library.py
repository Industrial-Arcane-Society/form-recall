# -*- coding: utf-8 -*-
"""企业夹具设计工序库。

数据来源：`datasets/企业流程结构化_视频实证版.csv`——那 16 道工序是从企业
3 小时 14 分的 CATIA 录屏里逐段读出来、再按五阶段整理的。本模块只负责
「把表读成程序能用的结构」，不判断工序对不对——对错由企业核对。

为什么单独成模块而不是塞进 stages.py：
    stages.py 描述的是**流程的骨架**（五个阶段、门禁规则），对任何工位都成立；
    本模块装的是**某一家企业的具体做法**（16 道工序、各自的判据与证据）。
    骨架换不了，做法经常换，两者混在一起，以后换企业就得改引擎。
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path

from ai_modeler.paths import PROJECT_ROOT

#: 企业流程表的默认位置。换一家企业时只改这里或传 path。
DEFAULT_PROCESS_CSV = os.path.join(str(PROJECT_ROOT), "datasets",
                                   "企业流程结构化_视频实证版.csv")

#: 确认状态三档。语义见表头说明，程序按这三档决定「能不能当成结论用」。
STATUS_VERIFIED = "视频证实"
STATUS_INFERRED = "报告推断"
STATUS_PENDING = "待企业确认"

VALID_STATUS = (STATUS_VERIFIED, STATUS_INFERRED, STATUS_PENDING)


@dataclass
class ProcessStep:
    """一道工序。字段与 CSV 一一对应，不做二次命名，免得两边对不上。"""

    step_id: str                 # S01
    stage: str                   # "1 工程输入"
    input: str
    action: str
    criterion: str
    output: str
    parts: str
    evidence: str
    status: str
    note: str = ""

    @property
    def stage_no(self) -> int:
        """从「3 参数建模」里取 3。取不到返回 0，不抛——表是企业填的，脏数据要容错。"""
        head = self.stage.strip().split(" ", 1)[0]
        try:
            return int(head)
        except ValueError:
            return 0

    @property
    def is_pending(self) -> bool:
        """待企业确认的工序：可以展示，但不能当作已定稿的判据执行。"""
        return self.status == STATUS_PENDING

    def search_text(self) -> str:
        """检索用的文本：动作 + 判据 + 输出。

        刻意不把 step_id 和 stage 拼进去——「S07」「3 参数建模」这类字串
        在别的工序里也会出现，拼进去会稀释真正有区分度的动作描述。
        """
        return " ".join([self.action, self.criterion, self.output]).strip()

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id, "stage": self.stage, "input": self.input,
            "action": self.action, "criterion": self.criterion,
            "output": self.output, "parts": self.parts,
            "evidence": self.evidence, "status": self.status,
            "note": self.note,
        }


@dataclass
class ProcessLibrary:
    """一家企业的工序全集。"""

    steps: list[ProcessStep] = field(default_factory=list)
    source: str = ""

    def by_stage(self) -> dict[int, list[ProcessStep]]:
        out: dict[int, list[ProcessStep]] = {}
        for step in self.steps:
            out.setdefault(step.stage_no, []).append(step)
        return out

    def get(self, step_id: str) -> ProcessStep | None:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def pending(self) -> list[ProcessStep]:
        """待企业确认的工序。做资料索取清单时直接用它。"""
        return [s for s in self.steps if s.is_pending]

    def stats(self) -> dict:
        from collections import Counter
        return {
            "steps": len(self.steps),
            "stages": len(self.by_stage()),
            "by_status": dict(Counter(s.status for s in self.steps)),
            "source": self.source,
        }


def load_process(path: str | None = None) -> ProcessLibrary:
    """读工序表。

    容错原则：表是人填的，可能少列、多列、留空。缺列用空串补，不抛异常——
    一张填了一半的表也比打不开强。
    """
    target = Path(path or DEFAULT_PROCESS_CSV)
    if not target.is_file():
        raise FileNotFoundError("找不到企业流程表：%s" % target)

    with open(target, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    steps = []
    for row in rows:
        step_id = (row.get("工序号") or "").strip()
        if not step_id:
            continue
        status = (row.get("确认状态") or "").strip()
        if status not in VALID_STATUS:
            # 状态写错按「待企业确认」处理：宁可拦一下，也不让未经核对的条目
            # 混进已验证集合。
            status = STATUS_PENDING
        steps.append(ProcessStep(
            step_id=step_id,
            stage=(row.get("对应阶段") or "").strip(),
            input=(row.get("输入") or "").strip(),
            action=(row.get("操作") or "").strip(),
            criterion=(row.get("判据") or "").strip(),
            output=(row.get("输出") or "").strip(),
            parts=(row.get("涉及零件") or "").strip(),
            evidence=(row.get("证据来源") or "").strip(),
            status=status,
            note=(row.get("备注") or "").strip(),
        ))

    return ProcessLibrary(steps=steps, source=str(target))
