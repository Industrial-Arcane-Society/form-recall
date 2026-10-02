# 已确认的 Agent 链路治理与验收

日期：2026-10-03。用户确认八项全部实施、P0优先；感知/建模/审查允许分别选择模型/provider，默认共用当前档案。该卡记录已确认范围，尚不是验收结果。

## 当前审查依据

1. `src/legacy/ai_modeler/agent/dsh_stream_bridge.mjs::stripConversationImages` 在实际 fetch 请求上移除 image/image_url/input_image；`agent_host::run_turn` 虽向 ACP 提交原图，不能据此声称主建模模型收到了图。当前系统提示词也要求主对话只能读取结构化观察。
2. `agent_host::publish_preview` 只发布 `model.preview` PNG；主轮次返回没有 document。Vue的特征树和交互GLB文档因此未由Agent当前CADPlan同步，常规参数编辑/导出仍调用 Python bridge。
3. `agent_host::run_turn` 将非cancelled ACP结束标为complete；Vue据此显示“Agent建模完成”，未读取宿主finish_summary的accepted或candidate状态。
4. 系统提示词要求按已施工范围复核；`native_vision_domain::review` 明确忽略review_scope，固定评价all_visible_evidence。应由宿主验证施工范围，并避免把主Agent的任意陈述作为审查真值。
5. `native_vision_domain::inspect` 对同原图SHA/ROI的有效inventory直接返回缓存；当前没有显式重新观察接口。结果结构合法不证明视觉事实正确，错误观察需要可修订入口。
6. 当前feature spec已经有native_execution说明，但公开Schema、静态CATIA状态、Rust执行白名单与具体变体限制来自不同维护位置；新增特征或局部限制会出现同步风险。
7. `agent_host::configure_reasoning` 在route未公开选项时可直接跳过；metadata仍保存用户要求的effort。`session/prompt` request使用None时限，宿主轮询没有总时间/无进展终止条件。
8. 冷启动验收草稿仍硬编码DeepSeek档案和只读观察提示词，与生产主Agent提示词不同；必须修正，不能把runner自检当作建模验收。

这些是本次源码审查事实；生产provider实际行为、模型识别准确率和修正效果必须由后续真实运行确认。

## 实施边界和完成条件

| 子任务 | 改动与验收 |
|---|---|
| G1 主Agent视觉输入 | 原图不可变引用、当前单视图可见，旧预览清理；实测主模型wire包含对应图片SHA，文本续话能重新访问原图。保留独立审查请求。 |
| G2 活动模型同步 | CADPlan为唯一活动几何状态；工具提交后发布同revision的交互网格/特征/尺寸，编辑和输出读取同一状态。通过Quick→Agent改模→参数编辑→保存→重新加载的端到端验证。 |
| G3 状态真值 | 传输完成、模型已生成、未验收候选、几何通过、视觉通过、CATIA未运行分别显示；用纯问答、工具失败、候选、有证据通过四种结果验证。 |
| G4 阶段审查 | 宿主里程碑及特征ID校验；未建孔不误判成当前孔差异；已建孔错位仍检出。修改后只有新revision同问题复查可关闭finding。 |
| G5 观察修订 | 具名冲突/ROI触发重新观察，持久化新版本并关联旧证据；普通重读命中缓存。矛盾、更新和不可观察项均保留。 |
| G6 能力契约 | 同源生成Schema/spec/native支持清单和精确变体限制；每个声明可执行的组合有运行样例，Unsupported有机器错误和可操作说明。 |
| G7 模型与预算 | 三种任务可独立设置模型/provider，默认共用；施工短推理、复杂视觉按需增强。记录实际effective设置；取消、工具错误和无进展按行为/时间治理，阈值可配置并不替代模型正常决策。 |
| G8 生产一致的验收 | 复用生产profile/prompt生成入口而非复制另一流程；全新会话使用原图/文字，不提供尺寸答案。归档输入、wire、工具、候选和失败；视觉独立评审与几何指标各自核验。 |

主Agent仍由DSH负责决策与循环；Cordis直接注册建模工具。上述整改不是新增MCP转译层，也不把流程固化为每轮必须执行的四步。完成每个子feature后按证据独立提交。

## 总体通过门槛

遵守 `docs/02-methods/01-validation-protocol.md`。单图、多视图、完整/不完整文字、标准件、多轮改模、取消/失败恢复均留实跑证据；不拿几何对拍取代Agent盲测，不拿Agent自评分当视觉真值。CATIA使用目标版本重新打开与核验；当前尚无本轮CATIA实测结果。
