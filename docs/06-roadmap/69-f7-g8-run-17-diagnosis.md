# F7 G8 Run17 严格冷启动诊断

日期：2026-10-04

## 运行边界

- 在 `feat/f7-p0-completion` 的 `91318d9` 上离线构建 `native_read_probe` 与 `native_agent_profile`；Cargo 构建 exit code 为 0。Run17 使用构建后的新二进制，包含此前的 ACP/Cordis callId 绑定提交 `9ea84df`。
- 使用 Node `v22.23.3`、DSH `0.2.0-rc.2`、默认 prompt 和一张原始参考图；未传入用户文字、预制 CADPlan 或人工设定尺寸。原图为 `72,309 bytes`，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，安全摘要标记未变。
- 当前进程的 `DEEPSEEK_API_KEY` 仅确认存在，值未读取；runner 映射到子进程使用的 `AIMODELER_LLM_API_KEY`。Run16 的安全文件无法确认当时使用同一环境变量来源或密钥字节。
- 只运行一次，900 秒 deadline，无重试、无 GUI/CATIA。只读取 `summary.json`、`tool-result-diagnostics.jsonl` 和 `build-status.json` 白名单字段；没有读取 raw trace、ACP 事件、ID、工具参数/payload、thought、模型回答或自由文本错误。
- 构建 exit code 0，runner 内部构建耗时 1,455 ms，stdout 0 bytes、stderr 72 bytes；未读取 stderr 内容。清理状态为 `removed`，stale scratch recovery 为 `complete`，各计数均为 0。

运行期间 HEAD 从启动时的 `91318d9` 前进。运行结束时 HEAD 为 `f265759`；检查 `91318d9..f265759` 的提交差异，只有 Todo 文档变更，运行所用代码未变。但执行期间没有持续监控 HEAD，因此不能声称整个运行期间 HEAD 始终固定。后续冷启动应在开始前锁定并记录 commit，运行中禁止提交。

## 运行结果

runner exit code `1`；transport `completed`，模型状态 `candidate`，strict 状态 `failed`，strict failure reason count `10`，evidence error count `0`。

| Gate | 状态 | 固定原因码 |
|---|---|---|
| `run_scope` | passed | — |
| `dsh` | passed | — |
| `trace_integrity` | passed | — |
| `original_image` | passed | — |
| `main_image_wire` | passed | — |
| `cordis_cad_tools` | failed | `acp_cad_tool_failed`; `cad_tool_rejected_unbound_or_unaudited`; `cordis_tool_arguments_or_identity_missing_or_unmatched`; `native_calls_not_individually_observed_or_duplicate`; `tool_call_rejected_or_failed` |
| `acp_cad_tool_results` | failed | `acp_cad_tool_result_failed`; `acp_cad_tool_result_terminal_status_invalid` |
| `current_geometry` | passed | — |
| `artifacts` | passed | — |
| `holes` | passed | — |
| `visual` | failed | `visual_coverage_incomplete_or_conflicting`; `visual_gate_original_reference_binding_missing` |
| `finish` | failed | `host_finish_not_accepted` |

### 安全聚合诊断

ACP 工具的 start / completed / failed 计数：

| 工具 | 计数 |
|---|---:|
| `cad_add_feature` | 1 / 2 / 0 |
| `cad_add_hole_array` | 3 / 6 / 0 |
| `cad_create_model` | 1 / 2 / 0 |
| `cad_finish_model` | 1 / 1 / 0 |
| `cad_get_feature_spec` | 3 / 3 / 0 |
| `cad_get_reference_image` | 5 / 5 / 0 |
| `cad_get_state` | 2 / 2 / 0 |
| `cad_inspect_references` | 3 / 4 / 0 |
| `cad_preview_model` | 1 / 2 / 0 |
| `cad_review_reference_similarity` | 3 / 2 / 1 |
| `cad_set_construction_milestone` | 1 / 1 / 0 |

终态聚合有 `completed_once=16`、`failed_once=1`、`repeated_same_status_distinct_updates=7`；HMAC 相同/不同摘要对为 `0 / 7`，缺失 ACP ID 事件数为 0。ACP→bridge 有 17 次 matched、7 次 `identity_pairing_ambiguous`；bridge→audit 有 23 次 matched、1 次 `bridge_execution_rejected`，工具名为 `cad_review_reference_similarity`。

汇总投影没有逐调用关联关系，因此不能证明 7 个重复终态组与 7 个 identity ambiguity 一一对应，也不能断言它们是唯一原因。源码审查确认当时的 gate 在 ACP/Cordis 配对与结果 gate 都要求 `terminal_updates === 1`；这与 Run17 重复同态更新计数相符，说明它足以造成误拒，但 Run17 摘要无法证明每组实际因该谓词失败。被拒绝的 review 工具及其具体原因也不能从安全枚举摘要还原。

## 运行后的门禁修正

`e8f02ce` 移除“终态通知必须恰好一条”的错误要求，继续要求每个调用只有一个 start、至少有一个终态、终态状态集合只有一个合法值、精确 ACP callId/参数指纹/run-session-turn 范围匹配，并保持 bridge/audit 唯一性和首次终态时间边界。`failed` 仍然失败，`completed` 与 `failed` 混合、缺终态、重复 start、缺失或错配 ID 仍 fail closed。诊断投影继续记录通知数量。

回归验证：`node --check` 通过；`node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs` 89/89 通过，包括重复 `completed` 幂等配对、重复 `failed` 仍失败、混合状态失败和 ID/作用域/调用唯一性负例。该门禁修正尚未经过新一轮真实 DSH 冷启动，Run17 本身仍是失败结果。

## 结论与剩余工作

Run17 已证明本次冷启动将原图送入主图请求并产出 candidate，且本地几何与孔 gate 通过；它没有通过严格验收。必须以修正后的固定 commit 进行一次新的冷启动，再核对 Cordis/ACP 关联、视觉覆盖与原图绑定、finish。不能把重复终态门禁修复描述成视觉质量改善，也不能据此标记 F7 完成或宣称 CATIA 验收通过。
