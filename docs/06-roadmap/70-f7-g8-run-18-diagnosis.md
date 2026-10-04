# F7 G8 Run18 严格冷启动诊断

日期：2026-10-04

## 运行边界

- 在 `feat/f7-p0-completion` 的固定 HEAD `9a2f5b04a5c90d29a36283dda89f4c852c3d2bdf` 上运行；启动前确认工作树 clean，运行期间持续检查 HEAD，结束时 HEAD 未变且工作树仍 clean。该提交包含 ACP 同态 upsert 和单图 review 自动选图修复。
- 父任务确认本 HEAD 的离线预构建成功，`native_read_probe` 与 `native_agent_profile` 可执行文件已生成。runner 启动前仍执行其固定的 offline Cargo build：结果 `passed`、exit code `0`、耗时 `1,479 ms`、stdout `0 bytes`、stderr `72 bytes`；未读取构建输出正文。
- 使用固定 Node `v22.23.3`、DSH `0.2.0-rc.2`，公开 provider 配置为 DeepSeek / `deepseek-flash` / `low`。本轮配置值与 Run17 相同；只确认继承环境中的 `DEEPSEEK_API_KEY` 存在，没有读取其值，无法确认 Run17 与 Run18 的凭据来源或密钥字节相同。
- 使用默认原始 prompt 与单张原始参考图；没有传入额外用户文字、预制 CADPlan、尺寸或孔数。图像为 `72,309 bytes`，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，安全摘要标记 `unchanged=true`。
- 只发起一次真实 DSH CLI 冷启动，deadline 为 900 秒；没有重试、GUI 或 CATIA。运行耗时 `787.8 s`，runner exit code `1`。只读取 `summary.json`、`tool-result-diagnostics.jsonl`、`build-status.json` 的安全白名单字段；未读取 raw trace、ACP/native ID、工具参数或 payload、thought、answer、自由文本错误或构建输出。
- scratch cleanup 为 `removed`；stale-scratch recovery 为 `complete`，scanned、removed、preserved、rejected、unknown-owner、failed 计数均为 `0`。安全产物保留于本机 `%TEMP%` 的独立 Run18 输出目录，不纳入仓库。

## 运行结果

总体状态与 strict-gates 状态均为 `failed`；transport `completed`，model status `candidate`，strict failure reason count `10`，evidence error count `0`。安全摘要 scope 为 `single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance`。

| Gate | 状态 | 固定原因码 |
|---|---|---|
| `run_scope` | passed | — |
| `dsh` | passed | — |
| `trace_integrity` | passed | — |
| `original_image` | passed | — |
| `main_image_wire` | passed | — |
| `cordis_cad_tools` | failed | `acp_cad_tool_failed`; `cad_tool_rejected_unbound_or_unaudited`; `cordis_tool_arguments_or_identity_missing_or_unmatched`; `native_calls_not_individually_observed_or_duplicate`; `tool_call_rejected_or_failed` |
| `acp_cad_tool_results` | failed | `acp_cad_tool_result_failed` |
| `current_geometry` | passed | — |
| `artifacts` | passed | — |
| `holes` | passed | — |
| `visual` | failed | `visual_coverage_incomplete_or_conflicting`; `visual_gate_original_reference_binding_missing` |
| `finish` | failed | `finish_findings_construction_or_external_facts_incomplete`; `host_finish_not_accepted` |

## 安全 projection

ACP terminal-update v2 的 start / completed update / failed update 计数：

| 工具 | start | completed | failed |
|---|---:|---:|---:|
| `cad_add_feature` | 10 | 20 | 0 |
| `cad_add_hole_array` | 2 | 4 | 0 |
| `cad_create_model` | 3 | 4 | 1 |
| `cad_finish_model` | 1 | 1 | 0 |
| `cad_get_feature_spec` | 4 | 4 | 0 |
| `cad_get_reference_image` | 6 | 6 | 0 |
| `cad_get_state` | 2 | 2 | 0 |
| `cad_inspect_references` | 5 | 4 | 1 |
| `cad_preview_model` | 6 | 12 | 0 |
| `cad_remove_feature` | 1 | 0 | 1 |
| `cad_restore_partial_candidate` | 1 | 0 | 1 |
| `cad_review_reference_similarity` | 2 | 2 | 0 |
| `cad_set_construction_milestone` | 2 | 2 | 0 |
| `cad_update_feature` | 1 | 0 | 1 |

同态 terminal event pair count 为 `0`，异态 pair count 为 `20`，HMAC unavailable event count 为 `0`，missing tool-call ID event count 为 `0`。其中 20 组 `repeated_same_status_distinct_updates` 是通知更新的安全聚合；这些统计没有逐调用因果关联，不能据此还原具体工具参数或失败原因。Run18 的统计与 e8f02ce 对重复同态 upsert 的修正方向一致，但不证明所有调用均成功。

Cordis binding v1 的 ACP→bridge 阶段共 `45` 个 `matched`、`1` 个 `bridge_call_missing`；缺失项只在投影中列为 `cad_update_feature`。bridge→audit 阶段共 `41` 个 `matched`、`4` 个 `bridge_execution_rejected`：

| 工具 | outcome | count |
|---|---|---:|
| `cad_create_model` | `bridge_execution_rejected` | 1 |
| `cad_inspect_references` | `bridge_execution_rejected` | 1 |
| `cad_remove_feature` | `bridge_execution_rejected` | 1 |
| `cad_restore_partial_candidate` | `bridge_execution_rejected` | 1 |

本轮拒绝枚举中未列出 `cad_review_reference_similarity`；其安全聚合在 ACP 中为 2 次 start、2 次 completed update、0 次 failed update，两个 binding 阶段各有 2 个 `matched`。这不表示视觉复核或 visual gate 通过：visual gate 仍失败。白名单摘要没有参数明细，因此不能把任一 rejected/missing/failure 归因到具体 request args。

## 结论与下一步

Run18 验证了固定提交下真实 DSH session 完成、原图主模型请求通过主图 wire gate、原图哈希保持不变，且摘要中的 current geometry、holes 与 artifacts gates 通过；整体 strict CLI gate 仍失败。安全 projection 显示同态 pair 为 `0`、缺失 call ID 为 `0`，但 Cordis 工具执行/身份关联、ACP tool result、视觉 coverage/original binding 和 finish 仍有失败。不能由这些枚举推断具体工具输入或模型行为，也不能声称形状、独立视觉质量或 CATIA 通过。

下一步先静态审查相关 producer→consumer 字段路径；逐项分析安全摘要中 4 个 `bridge_execution_rejected` 以及 `cad_update_feature` 的 `bridge_call_missing`，不查看 raw trace 或工具 payload。`cad_review_reference_similarity` 本轮拒绝枚举中未列出，但 visual gate 仍失败；保持该项视觉验收未通过。只有代码修正需要新的真实验收时才另开隔离运行；F7 保持失败，不能标记通过。F0 的 `npm run dev` 修复提交仍在 sibling branch；如目标分支需要该修复，再单独集成和验收。
