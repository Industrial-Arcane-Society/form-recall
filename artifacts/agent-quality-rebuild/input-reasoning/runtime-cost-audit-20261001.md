# 正式 CLI 耗时与推理证据审计（2026-10-01）

## 范围与口径

只读核查以下正式运行的四份 `agent-observability.jsonl`，并对照同 turn 的 `result.json`、`active-turn.json`、`runtime-capacity-policy.json`、`geometry-report.json` 与 `user-turns.json`：图像建模 `921c175f…`、文本编辑 `d1fae560…`、连续两步文本编辑 `b52046c7…`、长文字几何建模 `35bf6e27…`。没有改源码、启动 provider 或运行桌面软件。

请求数、图像 parts、usage 和 provider 时长以 `provider_wire_request` / `provider_wire_response_complete` 为准；turn elapsed 取 `result.json.cli_progress_events` 的最大 `elapsed_ms`。时间戳存在 epoch 秒、epoch 毫秒混用，已按字段归一。工具耗时由日志中的 `tool_start` / `tool_complete` 配对估算；若工具 span 包含内部视觉 provider 调用，它与 provider 时长重叠，不重复相加。日志缺失 tool 参数与返回正文，不能单靠它们判定工具调用是否失败或是否无进展。

实际 CLI provider/model 是 SiliconFlow `moonshotai/Kimi-K2.7-Code`，推理强度 `low`。

## 每 turn 统计

| 正式运行 | Turn elapsed | DSH 逻辑请求 / provider requests | 工具调用 | Reasoning / input / output tokens | Wire 图像 parts | repair / review / preview | 结果 |
|---|---:|---:|---:|---:|---:|---:|---|
| 图像建模 `921c175f…` | 686.968 s | 21 / 23 | 20 | 12,533 / 486,428 / 18,603 | 28 | 3 / 1 / 1 | `candidate_not_verified` |
| 文本改高 `d1fae560…` | 63.985 s | 6 / 6 | 5 | 907 / 104,769 / 1,586 | 2 | 2 / 0 / 1 | `candidate_not_verified` |
| 两步增高 `b52046c7…` | 72.860 s | 7 / 7 | 6 | 1,435 / 112,012 / 2,098 | 4 | 2 / 0 / 2 | `candidate_not_verified` |
| 长文字几何 `35bf6e27…` | 979.922 s | 32 / 32 | 35 | 19,443 / 897,726 / 26,661 | 5 | 3 / 0 / 3 | `accepted` |
| **合计（四 turn 相加）** | **1,803.735 s** | **66 / 68** | **66** | **34,318 / 1,600,935 / 48,948** | **39** | — | — |

`input_tokens` 是 provider 报告的输入总量，包含缓存 token：四 turn 分别缓存 444,160、87,680、95,232、834,304；它不是单次请求上下文长度。数值不代表价格或未缓存 token 计费量。

## 预算、provider 等待、流与工具开销

- `reasoning_effort=low` 与 `thinking_budget=2048` 是**每个 provider request 的配置**，不是 turn 总额。68 个 provider request 中，67 个请求传 2048；图像首轮的一个 `vision_review/source_inventory` 请求传 1024，另一个 review 传 2048。日志标记 `thinking_budget_hard_limit_known=false`，所以 2048 也不是已证实的硬上限。每 turn 实际累计 reasoning usage 是上表数值；长文字 turn 是 32 次请求累计 19,443（均值约 607/request），不能拿 2048 当整轮上限。
- 容量记录称 context window 256,000 来自已核实 provider capability；`max_completion_tokens=32768` / `output_cap=32768` 是 DSH operational fallback，provider 最大输出长度未知。
- 图像首轮 23 个 wire request 的 provider response span 合计 675.919 s：21 个流式 construction request 的首 body 等待合计 28.873 s，首 body 后流式区间合计 427.600 s；另有 2 个非流式 `vision_review` 请求，分别耗时 78.990 s 与 140.456 s，合计 **219.446 s**。该首轮日志没有 `stream_activity_summary`，不把 post-body 时间伪称为纯 reasoning 时长。
- 两次文本编辑的 provider response span 分别为 60.453 s、68.671 s；首 body 等待分别为 6.569 s、7.641 s。可观察的 assistant stream 活动分别是：改高 turn reasoning 44.251 s、回复 9.644 s；两步增高 turn reasoning 56.263 s、回复 4.781 s。
- 长文字 turn 的 32 个 provider response span 合计 **972.611 s**，占 979.922 s turn elapsed 的约 99.3%。首 body 等待合计 37.596 s；assistant stream 活动累计 reasoning **922.819 s**、回复 12.260 s，与首 body 后区间 935.015 s 基本对应。调用 `cad_validate_model` 的本地 span 约 35 ms；全部 35 个工具 span 合计约 2.937 s（含 preview 等），不能把它误当 15 分钟的主要去向。

## 可证实的重复与无进展风险

1. **最明确的昂贵低收益分支是图像首轮 review。** `result.json` 记 `review_rounds=1`、`independent_review_pair_count=1`，但该 pair 为 non-comparable，`comparable_review_pairs=0`、参考图覆盖状态仍为 unverified，最终 `candidate_not_verified`。两个非流式视觉请求花 219.446 s，累计 3,072 reasoning tokens。建议在发起昂贵 review 前先检查预览和参考图能否形成有效比较；无效时返回具体缺项并停止，不要继续付出长时间推理。
2. **长文字建模在最后几分钟重复带同一份预览图。** 预览 #30、#31、#32 都发送同一 SHA-256 的 top view，均为 geometry revision 7；#32 是无工具的最终 `stop` 请求，仍携带该图像。前两次文本编辑也在最终 stop request 重传刚生成的同一预览。生成最终文字回复时可去掉无需视觉判断的 tool-preview part。旧图清理本身有证据：b520 的最新预览更新时记录移除 1 张旧预览、只保留最新图；长文字预览迭代中累计移除 2 张旧图。不要把“同一最新图被重用”误报成旧图未清理。
3. **长文字用例的显式几何验证较晚。** 唯一 `cad_validate_model` 在 turn 开始后 784.944 s（约 13:05）才发生，对应 provider request #27/32 之后；之后还有三个 preview。若先构造明确的用户尺寸 plan，再在 feature/milestone 处运行本地确定性检查，可更早发现形状/尺寸问题，减少模型在无法观测几何状态时继续推演的机会。日志没有证明之前发生了具体几何失败。
4. **可疑的重建段，尚不能定性为重复失败。** 长文字 `cad_create_model` 9 次、`cad_add_feature` 11 次，而最终 plan 只有 4 个特征；第 20 个请求后调用 `cad_restore_best_candidate`，接着第 21 个请求耗时 90.113 s / 1,898 reasoning tokens 并调用 `cad_create_model`，第 22、23 个请求又调用 `cad_add_feature`。`repair_cycles=3` 与该序列说明存在多轮修复/恢复，但 JSONL 没有工具参数、返回状态、revision 前后值，不能断言这些写入是相同参数的无进展重试。建议给每个 tool span 记录安全参数摘要、返回状态、geometry revision before/after 和 repair reason；只有相同输入、revision 不变、检查失败重复时才折叠/中止。
5. **来源标签有直接证据的错误。** 长文字 prompt 直接给定底板 `100 × 60 × 20 mm`，final plan 对 `base_plate.length/width/height` 三项均标 `type=derived`，但 evidence 又写“用户给定”；agent 自报也说明 CADPlan 来源字段只支持 `type/evidence/note`，因此把数值来源统一标为 derived。应增加明确的 `user_supplied` 来源类别，把单位/半径换算、几何中心等真正计算项留作 `derived`。这能修复审计/追溯错误；现有记录不能证明该 schema 问题单独造成了全部 32 次请求。

## 失败证据边界与建议

四份 JSONL 的 provider response 都有 HTTP 200 且完整结束；未见 provider retry/error 事件。三个图像 turn 的本地 OCCT 几何检查通过，但均未完成独立原图视觉覆盖，因此状态仍是 `candidate_not_verified`。长文字结果 `accepted`、OCCT BREP 有效、单一封闭实体、贯穿孔和盲孔均有几何报告验证；CATIA 未启动，不能据此声称 CATPart 已验证。

结果摘要虽有 `repair_cycles` 计数，但没有逐次原因/输入/输出；`tool_complete` 只记通用“工具”，不携带工具结果。当前证据支持优化昂贵的不可比 review、最终回复中的重复预览图、过晚的本地检查和来源类别；不支持把 19,443 reasoning tokens 归因于某一个重复失败。**不建议用更低的固定 token 上限或固定轮数解决**：应按 geometry revision、工具结果、证据可比性和真实进展决定是否继续调用模型。
