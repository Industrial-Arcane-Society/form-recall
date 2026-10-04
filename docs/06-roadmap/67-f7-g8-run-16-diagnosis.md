# F7 G8 Run16 冷启动诊断

## 运行边界

- 运行 checkout：HEAD `db2aaa3`，包含 ACP terminal 状态门禁修正 `2eabd1e`、视觉原图绑定 fail-closed `2b2cf96` 和 `scroll_disc` `e492aab`。
- 运行前确认工作树干净、没有并行 Cargo/rustc 构建进程。对当前 checkout 离线重编译 `native_read_probe` 和 `native_agent_profile`，Cargo exit code `0`，两个 example 可执行文件均存在。runner 随后也按当前 checkout 再次离线构建。
- 使用 pinned Node `v22.23.3`、DSH `0.2.0-rc.2`、单张默认原始参考图及 runner 默认无尺寸提示文本。provider descriptor 只含 DeepSeek endpoint、`deepseek-flash` 与 `low`，API key 只从当前进程环境读取；仅核验凭据存在状态，未读取或输出其值。
- runner 采用其 `AIMODELER_ACCEPTANCE_DEADLINE_SECONDS=900` 有界 session/prompt timeout。只启动一次，没有重跑；没有启动 GUI/CATIA。
- 仅读取安全发布白名单文件 `summary.json`、`tool-result-diagnostics.jsonl` 和 `build-status.json`。未读取 raw trace、模型 answer/thought、payload、ID 或自由文本错误/构建正文。

## 运行结果

runner exit code `1`；总体状态与 strict-gates 状态均为 `failed`，transport 为 `failed`，model status 为 `candidate`。strict failure reason count 为 `15`，evidence error count 为 `0`。安全摘要的 scope 为单图、单轮 CLI host gates，不代表独立视觉或 CATIA 验收。

构建状态为 `passed`，exit code `0`，耗时 `1347 ms`，stdout `0 bytes`、stderr `72 bytes`；未读取构建输出正文。runtime 为 Node `v22.23.3` / DSH `0.2.0-rc.2`。输入图像为 `72,309 bytes`，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，摘要记录 unchanged。scratch cleanup 为 `removed`；stale-scratch recovery 为 `complete`，scanned、removed、preserved、rejected、unknown-owner、failed 均为 `0`。

| Gate | 状态 | 白名单固定原因码 |
|---|---|---|
| `run_scope` | passed | — |
| `dsh` | failed | `dsh_not_completed_or_nonzero_exit` |
| `trace_integrity` | passed | — |
| `original_image` | passed | — |
| `main_image_wire` | passed | — |
| `cordis_cad_tools` | failed | `acp_cad_tool_failed`; `cad_tool_rejected_unbound_or_unaudited`; `cordis_tool_arguments_or_identity_missing_or_unmatched`; `native_calls_not_individually_observed_or_duplicate`; `tool_call_rejected_or_failed` |
| `acp_cad_tool_results` | failed | `acp_cad_tool_result_failed` |
| `current_geometry` | failed | `geometry_attestation_or_gate_binding_invalid`; `preview_geometry_finish_revision_or_fingerprint_stale` |
| `artifacts` | passed | — |
| `holes` | failed | `holes_incomplete_or_unverified` |
| `visual` | failed | `visual_coverage_incomplete_or_conflicting`; `visual_evidence_not_current_rust_summary`; `visual_gate_original_reference_binding_missing` |
| `finish` | failed | `finish_findings_construction_or_external_facts_incomplete`; `host_finish_not_accepted` |

## 安全诊断投影

### ACP terminal update v2

状态计数按 `in_progress start / completed update / failed update` 记录：

| 工具 | 计数 |
|---|---|
| `cad_add_feature` | 4 / 8 / 0 |
| `cad_add_hole_array` | 5 / 4 / 3 |
| `cad_create_model` | 1 / 2 / 0 |
| `cad_get_feature_spec` | 3 / 3 / 0 |
| `cad_get_reference_image` | 8 / 8 / 0 |
| `cad_get_state` | 1 / 1 / 0 |
| `cad_inspect_references` | 2 / 2 / 0 |
| `cad_preview_model` | 4 / 8 / 0 |
| `cad_remove_feature` | 4 / 2 / 3 |
| `cad_review_reference_similarity` | 1 / 1 / 0 |
| `cad_search_knowledge` | 1 / 0 / 1 |
| `cad_set_construction_milestone` | 1 / 1 / 0 |
| `cad_update_feature` | 1 / 0 / 1 |

终态调用组投影：

- `completed_once` 共 16 组：`cad_get_feature_spec` 3、`cad_get_reference_image` 8、`cad_get_state` 1、`cad_inspect_references` 2、`cad_review_reference_similarity` 1、`cad_set_construction_milestone` 1。
- `failed_once` 共 8 组：`cad_add_hole_array` 3、`cad_remove_feature` 3、`cad_search_knowledge` 1、`cad_update_feature` 1。
- `repeated_same_status_distinct_updates` 共 12 组：`cad_add_feature` 4、`cad_add_hole_array` 2、`cad_create_model` 1、`cad_preview_model` 4、`cad_remove_feature` 1。
- 相同/不同脱敏终态摘要对计数为 `0 / 12`；HMAC 不可用事件数 `0`；缺少 tool-call ID 事件数 `0`。

### Cordis binding v1

ACP→bridge 阶段的匹配计数：`cad_add_feature` 4、`cad_add_hole_array` 5、`cad_create_model` 1、`cad_get_feature_spec` 3、`cad_get_reference_image` 8、`cad_get_state` 1、`cad_inspect_references` 2、`cad_preview_model` 4、`cad_remove_feature` 2、`cad_review_reference_similarity` 1、`cad_search_knowledge` 1、`cad_set_construction_milestone` 1。另有 `cad_remove_feature` 2 次和 `cad_update_feature` 1 次 `bridge_call_missing`。

bridge→audit 阶段的匹配计数：`cad_add_feature` 4、`cad_add_hole_array` 2、`cad_create_model` 1、`cad_get_feature_spec` 3、`cad_get_reference_image` 8、`cad_get_state` 1、`cad_inspect_references` 2、`cad_preview_model` 4、`cad_remove_feature` 1、`cad_review_reference_similarity` 1、`cad_search_knowledge` 1、`cad_set_construction_milestone` 1。另有 `cad_add_hole_array` 3 次及 `cad_remove_feature` 1 次 `bridge_execution_rejected`。

## 结论与限制

本轮严格门禁失败。`dsh_not_completed_or_nonzero_exit` 只表明摘要记录的 DSH 完成状态/退出状态不满足门禁；脱敏摘要没有给出可用于判断具体原因的自由文本。各 gate 和 projection 是主机侧汇总证据，不能反推出具体模型答复、provider 原因、参数、工具 payload 或几何错误。`current_geometry`、`holes`、`visual` 与 `finish` 的失败原因码也不构成具体形状差异或视觉判断的证据。

因此不宣称候选通过视觉验收、模型几何正确或 CATIA 验收通过。当前记录不放宽或改变任何严格门禁；后续归因应继续限定在经审阅的安全白名单证据范围内。
