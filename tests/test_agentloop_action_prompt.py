"""Regression checks for concise, tool-driven AgentLoop prompt guidance."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AgentLoopActionPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prompt_dir = ROOT / "prompts" / "agentloop"
        cls.runtime = (prompt_dir / "runtime.md").read_text(encoding="utf-8")
        cls.reference_read = (prompt_dir / "reference_read.md").read_text(encoding="utf-8")

    def test_runtime_prompt_prefers_one_short_action_and_tool_arguments(self):
        self.assertIn("每轮用一句简短行动进展后立即调用一个最小必要工具", self.runtime)
        self.assertIn("具体几何、CADPlan、尺寸来源与校验依据一律放在工具参数里", self.runtime)
        self.assertIn("普通问答以及用户明确要求说明、比较或补充信息时，正常用正文回答", self.runtime)

    def test_runtime_prompt_diagnoses_tool_errors_before_one_targeted_fix(self):
        self.assertIn("先依据该次返回的错误信息诊断", self.runtime)
        self.assertIn("只做一次有错误证据支撑的定向修正", self.runtime)
        self.assertIn("不要原样重试、盲目换参、长篇猜测内核行为或输出教程式排障", self.runtime)

    def test_reference_read_goes_into_create_tool_instead_of_long_prose(self):
        self.assertIn("`reference_reading` 是给建模工具使用的结构化证据", self.reference_read)
        self.assertIn("由 `cad_create_model` 的工具参数承载", self.reference_read)
        self.assertIn("首轮正文只保留一句简短行动进展", self.reference_read)
        self.assertIn("并在同一轮调用 `cad_create_model` 创建主体", self.reference_read)


if __name__ == "__main__":
    unittest.main()
