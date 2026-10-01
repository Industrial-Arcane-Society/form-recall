# 冷启动建模无进展循环审计（2026-10-01）

## 范围与结论

只读检查冷启动报告、CAD state、actions、observability 记录与本地实现。未读取/判读原图，未运行 provider、GUI 或测试。报告将记录中直接可证事实与实现推断分开。

此次循环的确定性阻断是 `back_blind_1` 在 preview 中反复产生 `cut_no_material_change`。工具拒绝继续更新/删除该特征后，Agent 仍尝试另一种建模表达（额外 cylinder）、移除其他特征和最终验证；虽然 CAD revision 增加，阻断几何没有解决，最终调用仍不能正常结束。实现里有几类进展/熔断保护，但它们的进展键允许几何 fingerprint 的变化重置无进展计数；这可解释为什么“计划有变更”掩盖了“问题单未解决”，但就此次 run 来说是基于静态实现的解释，不是 provider 决策原因的日志证据。

## 真实事件序列

`actions.json` 共 53 条工具动作。与循环直接相关的顺序（括号为 action 序号 / revision）：

1. 创建 base、凹槽和三组孔阵列后，首次 preview 失败为 `duplicate_through_axis`（9 / rev5）。Agent 删除重复阵列若干孔；一次删除又因回到历史 geometry hash 被拒绝（19 / rev13）。之后增加 `horz_hole_1..3`，并连续修订 `back_hole_3`。
2. 添加 `back_blind_1..2` 后首次 preview 报 `back_blind_1 / cut_no_material_change`（28 / rev20）。随后改变该盲孔和第二盲孔，preview 再次同一失败（34 / rev24）。
3. 由于相同失败，删除 `back_blind_1` 被 guard 拒绝；删除 `back_blind_2` 成功（35–36 / rev25）。接着加入 `back_cyl_20/40/60`（37–39 / rev28），preview 仍报告同一失败（40 / rev28）。
4. 创建新模型被拒（41）；对失败特征的更新与删除也被 guard 拒绝（42–43）；调用 `cad_get_reference_image` 后仍有一次更新被拒绝（44–45）。随后删除 `back_hole_3`，并再次 preview（46–47 / rev29），仍是同一 `cut_no_material_change`。
5. 移除三个 back cylinder（48–50 / rev30–32），两次盲孔更新都被 guard 拒绝（51–52），最终调用 `cad_validate_model`（53 / rev32）。报告记录该 validate 只有 stderr 进度提示“校验已完成；未启动 CATIA”，stdout 为空且进程被外部结束；不能据此声称结束条件成功、最终 BREP 有效或模型导出。

### 四次相同失败的位置

`actions.json` 中四次 preview 结果的 `failure_code` 均为 `cut_no_material_change`、`failed_feature_id=back_blind_1`：

- 第 28 条：rev20，首次添加双盲孔之后。
- 第 34 条：rev24，多轮更新孔参数之后。
- 第 40 条：rev28，额外 cylinder 特征加入之后。
- 第 47 条：rev29，删除 `back_hole_3` 之后。

此计数按四次 preview failure 统计；中间工具 guard 的重复失败提示属于同一失败 episode，不额外计作 preview。

## rev29 局部 BREP 为何没有进入候选/导出

报告给出的 rev29 / preview generation 5 证据是 partial preview：`candidate_partial_not_accepted`，失败项仍为 `back_blind_1`。其几何检查报告前缀（base box、recess、三条横向孔）`brep_valid=true`、单闭合 solid、0 free edges、无自交命中，bbox 和 volume 均有记录；状态仍是 `needs_review` 且 `accepted=false`。这里是有效前缀 BREP，不是整个 requested plan 通过。

实现明确把这两种对象分开：`_partial_plan_from_report` 从失败报告提取已 applied feature，建立可编辑 partial CADPlan（`dsh_mcp_server.py:1653`）；preview 失败分支将其保存在 `partial_preview` 与 `partial_preview_history`，并标为 `candidate_partial_not_accepted`（`dsh_mcp_server.py:3627-3710`）。`_capture_candidate` / `_capture_best_candidate` 只接受当前 revision 的成功 `last_preview` 且 `local_geometry_checks.state == "passed"`（`dsh_mcp_server.py:2152-2220`）；preview 失败分支不会设置 `last_preview`、`preview_revision`，也不会调用捕获函数。已有测试正是断言失败 partial 可见但 `last_preview` 和 `best_candidate` 为空（`tests/test_dsh_review_workflow.py:128-155`）。因此 rev29 的 BREP 虽局部闭合有效，却不会自动变成 `last_valid_candidate` / `best_candidate`，也不能走要求完成状态的正常导出路径。这是当前保守候选语义的结果；但从恢复角度看，系统缺少“保留可继续加工/显式降级导出”的独立 partial candidate 档位。

## 程序可证明的 progress 与本次保护缺口

- CAD 几何失败分类可报告材料体积没有变化：OCCT 返回 `cut_no_material_change` / `no_op`（`core/geometry/occ_kernel.py:1956,1994,2061`）；现有测试验证该 failure code 与体积不变（`tests/test_agent_runtime_incremental.py:2027-2058`）。这比“工具调用已发生”更接近几何进展证据。
- `_state_progress_key` 将 `plan_geometry_fingerprint`、preview fingerprint、validation errors、未解决 finding、review 结果、finish blockers 等纳入状态键（`agent_runtime.py:887-925`）。它没有纳入 DSH MCP server 的 `last_preview_failure` / failure signature / `partial_preview`，两套状态也不能直接等同。
- AgentLoop 每次工具前后比较进展键；只要键不同就清零 `stagnant_tool_calls`，否则累计，到固定连续调用阈值后只发 warning/nudge（`agent_runtime.py:3020-3044`；阈值定义 `:27`）。因此不同 fingerprint 的小改动可以一直被视为 progress，即使同一个 blocker/failure 仍 open。此处是代码行为推论。
- `_apply_incremental` 用 `COSMETIC_EPSILON=0.01` 拒绝参数微扰；纯来源元数据更新可以改变 plan revision 而保持几何 fingerprint 不变（`agent_runtime.py:1385,1432-1530`）。现有 `test_cosmetic_parameter_nudge_is_not_progress` 覆盖前者（`tests/test_agent_runtime_incremental.py:1188-1207`）。这表明“数值变化”不应直接等同实质进展。
- repeated-failure guard 对同一失败特征要求新来源证据复核，阻止重复编辑（`dsh_mcp_server.py:1695-1810`）。它能拒绝无证据重复 mutation，却不能自行促成部分候选封存、寻找不同拓扑路径、标记“几何任务已停滞并等待决策”或终止长时间 reasoning 请求。最后几项是现有事件与分层机制的缺口判断。
- `cad_validate_model` 只返回 CADPlan validation 并记 action，不代表 preview、finish gate、CATIA 或导出成功（`dsh_mcp_server.py:3621-3625`）。

## 可落地的无进展检测与候选保留

不要按工具调用次数或某个小的固定上限判断进展。按每次工具完成后的持久状态，比较三组信号，并保留变更轨迹：

1. **状态层**：记录 CADPlan 有效性、当前 blocker/failure episode、preview state、finish gate blocker、导出/交付状态。纯 metadata/revision/tool activity 不算进展。只有 blocker 从 open 到 resolved、validation error 消失、finish gate 发生有证据的前进，才构成 workflow progress。
2. **几何指纹层**：记录规范化 CADPlan geometry signature 与实际 BREP 指纹/测量（实体数、体积、bbox、自由边/自交、局部检查状态）。与上次相比，若 geometry signature 改变但同一失败特征和 failure code 仍在，视为“尝试发生、阻断无变化”；若 BREP 体积差为 0、特征 effect 为 no-op，归为几何无效变更。小于 `COSMETIC_EPSILON` 的 nudge 已单独防住。没有测量值时不能用参数变化冒充 BREP 进步。
3. **未解决问题单层**：为每个 finding/failure 建立稳定 id，如 `geometry:cut_no_material_change:back_blind_1`，绑定 related feature、first/last seen revision、repeat count、来源图/ROI复核时间、已尝试的几何签名与参数差、resolution evidence。仅在同一问题被明确验证关闭，或修复后新的 preview/geometry evidence 证明影响消失时清零；被删掉 feature、改名、revision 前进都不得清零。

触发停滞后应按状态输出可行动响应：保存当前完整 plan 快照和失败前缀 BREP；保留可回退的 `last_valid_candidate`；把该 issue 标为 `blocked_needs_new_evidence`；要求另一个有依据的几何假设/ROI证据或承认部分完成。若 partial BREP 通过实体有效性检查，可进入单独的 `partial_candidate` 槽位，记录 applied/not-applied IDs、geometry checks、失败项与 `accepted=false`，允许恢复继续编辑；默认不得将其混同为完整 best candidate 或无提示导出。每个明确的几何尝试须有新的候选签名或新的证据/拓扑策略；相同 blocker 下重复旧几何签名应返回下一步说明并保留候选，而不是让 Agent 仅靠递增 revision 避开停滞检测。

检测器应以 blocker episode 的无变化和重复 signature 为核心，而非全局微小固定工具数。与之并行，可将请求/总任务 deadline 用作故障安全边界，但 deadline 不能替代语义进展检测。

## 最小补充测试

已有覆盖：失败 partial 不晋升为 valid (`tests/test_dsh_review_workflow.py:128-155`)，恢复 best candidate (`:848-865`)，微扰不算 progress (`tests/test_agent_runtime_incremental.py:1188-1207`)，几何 no-op volume test (`:2027-2058`)。建议最小加以下回归：

1. **重复 failure 即使 geometry signature 每轮变化仍触发 stall**：在 `tests/test_agent_runtime_incremental.py` 构造连续工具结果：参数/plan fingerprint 改变、preview fingerprint 失效，但相同 failure id 与 open finding 保持；断言状态键/问题单计数不会被清零，达到语义终止条件后返回 blocked/needs-evidence，而不是允许无限 mutation。实现关注 `ai_modeler/agent/agent_runtime.py:887-925,3020-3044` 与 `ai_modeler/agent/dsh_mcp_server.py:1695-1810`。
2. **部分有效 BREP 单独持久保存与恢复**：在 `tests/test_dsh_review_workflow.py` 扩展 `test_partial_failed_preview_is_visible_but_never_promoted_to_valid`，断言 valid prefix 写入独立 `partial_candidate`（带 applied IDs、signature、checks、failed issue），仍不写 `best_candidate`；随后以恢复/继续编辑路径读回同一 CADPlan。实现关注 `ai_modeler/agent/dsh_mcp_server.py:1653-1693,3627-3710,3158-3195`。
3. **问题单仅在修复证据出现时关闭**：preview 同一 failure 后删除/改名 feature 或新增无关特征，问题仍 open；新 preview 显示失败项消失且几何效应实测变化，才关闭。实现关注 `ai_modeler/agent/dsh_mcp_server.py:1695-1810` 与 failure/finding 更新点。

## 可用真实 CLI 验证的输入与观察点

复用报告中的真实命令输入：`请按参考图复刻这个工件；图中没有标注的尺寸请按图像比例估算，标明估算来源，生成本地模型`，原图 `artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`。此次已经有实际冷启动基线；源码修复后另启新 session 再跑同一输入（不得复用旧 session 状态）。

观察 `evidence/actions.json`/run record 中每次 preview 的 `(geometry_signature, failed_feature_id, failure_code, partial_candidate_id)`，以及同 issue 下参数变化、geometry effect/volume delta、issue 状态、候选槽位和最终 stop reason。验证重点：当 blind hole 重复产生同一 no-material-change 且没有新几何/来源证据时，Agent 能留下可恢复 partial candidate，报告明确 blocker 并结束当前建模动作；成功条件不是声称孔已修好，而是不会静默删掉前缀、循环改写 revision 或把未通过的 partial 当完整导出。
