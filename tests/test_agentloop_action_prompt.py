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
        cls.blind_inventory = (prompt_dir / "blind_reference_inventory.md").read_text(encoding="utf-8")

    def test_runtime_prompt_prefers_one_short_action_and_tool_arguments(self):
        self.assertIn("每轮用一句简短行动进展后立即调用一个最小必要工具", self.runtime)
        self.assertIn("具体几何、CADPlan、尺寸来源与校验依据一律放在工具参数里", self.runtime)
        self.assertIn("普通问答以及用户明确要求说明、比较或补充信息时，正常用正文回答", self.runtime)
        self.assertIn("颜色边界不能单独证明零件分离", self.runtime)

    def test_runtime_prompt_diagnoses_tool_errors_before_one_targeted_fix(self):
        self.assertIn("先依据该次返回的错误信息诊断", self.runtime)
        self.assertIn("只做一次有错误证据支撑的定向修正", self.runtime)
        self.assertIn("不要原样重试、盲目换参、长篇猜测内核行为或输出教程式排障", self.runtime)

    def test_reference_read_is_a_separate_step_before_model_creation(self):
        self.assertIn("调用 `cad_inspect_references`", self.reference_read)
        self.assertIn("下一步再调用 `cad_create_model` 创建主体", self.reference_read)
        self.assertIn("优先把它理解为主体上的凹槽/退台", self.reference_read)
        self.assertIn("单一等轴图则说明透视估值的不确定性", self.reference_read)

    def test_visual_prompts_distinguish_recessed_surfaces_from_open_voids(self):
        self.assertIn("有边界的内侧平面", self.reference_read)
        self.assertIn("背景连续穿过", self.reference_read)
        self.assertIn("阴影、颜色和遮挡本身都不能证明材料缺失", self.reference_read)
        similarity = (ROOT / "prompts" / "agentloop" / "similarity_review.md").read_text(encoding="utf-8")
        self.assertIn("实体内侧平面还是背景穿过的空隙", similarity)
        self.assertIn("后退平面上的特征", similarity)

    def test_visual_review_uses_source_only_blind_inventory(self):
        self.assertIn("不含 CADPlan、模型预览和首轮 `reference_reading`", self.runtime)
        self.assertIn("\u4e0d\u4f7f\u7528 CADPlan", self.blind_inventory)
        self.assertIn("\u6a21\u578b\u9884\u89c8", self.blind_inventory)
        self.assertIn("submit_reference_inventory", self.blind_inventory)
        self.assertIn("hole_layout_coverage=complete", self.blind_inventory)
        self.assertIn("relative_size_order", self.blind_inventory)
        self.assertIn('["large","small","large"]', self.blind_inventory)
        multi = (ROOT / "prompts" / "agentloop" / "multiview_check.md").read_text(encoding="utf-8")
        self.assertIn("independent_visible_feature_groups", multi)
        self.assertIn("submit_multiview_review", multi)


if __name__ == "__main__":
    unittest.main()
