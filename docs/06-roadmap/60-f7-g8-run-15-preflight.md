# F7 G8 Run15 冷启动前置结果

## 运行结果

本节保留的是首次启动尝试记录；下文补充安全发布修复审阅后的唯一一次真实复跑。

Run15 未启动。runner 的单次 CLI 启动请求在进程创建前被执行器拒绝，工具返回 `CreateProcess ... Rejected(...): blocked by policy`。该错误没有提供更具体的策略原因。没有启动 runner 或 DSH，没有发送 provider 请求，也没有可记录的 Run15 gate 结果；本记录不是模型验收报告。

## 已核验的前置项

- 使用当前隔离 worktree 中跟踪的 DSH runtime manifest 和锁文件，准备 ignored `target/f1-runtime-probe`。Node 官方归档 SHA-256 与 manifest 相符；Node `v22.23.3`、DSH `0.2.0-rc.2` 均通过本地版本核对，锁文件安装报告 538 个包完成。
- 原始参考图在已记录路径存在，大小 72,309 bytes，SHA-256 与基线 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553` 一致。未读取、展示或复制图片内容。
- 当前进程的 `DEEPSEEK_API_KEY` 仅核验存在状态；密钥值没有读取或写入 descriptor。公开 provider descriptor 指向 `https://api.deepseek.com/v1`、模型 `deepseek-flash`、推理强度 `low`。
- OCCT runtime manifest、当前 worktree 编译出的 Rust probe/profile 可执行文件均存在。DSH home 与输出目录规划为本轮新建隔离路径。

## 边界与后续

CLI 被拒后没有改用其它执行通道或重试。创建的公开 descriptor 和空输出目录留在 ignored `temp/acceptance/`；执行器也拒绝了清理命令，因此它们被保留。descriptor 只含 provider URL、模型名和推理强度，不含凭据；没有 runner 产生的 trace、payload、thought 或 gate 数据。本次不得推断 v2 projection 的真实运行表现，也不得推断任何门禁通过/失败。

恢复 Run15 需要一个允许启动该本地严格验收 runner 的执行上下文。重新运行前仍须使用新输出目录、同一原图 SHA 和隔离 DSH home；完成后只读取自动 gate 结果与两个固定脱敏 projection，并维持现有 strict gates。

## 安全发布修复（尚未运行真实 DSH）

后续静态审查发现旧 runner 会写出 `tool-trace.json`、带 run/session ID 的完整 strict gate、ACP tool-event 列表，以及 answer/hash/thought 长度等字段。旧的 Run15 CLI 尝试在 CreateProcess 前被拒绝，因此这些旧 runner 输出未在 Run15 产生；此前历史 run 记录不在本次读取范围内。

基于 `1e445f1` 的独立隔离 worktree 中已将发布路径收敛为固定三个文件：`summary.json`（总体状态、固定 gate 名称/状态/reason 计数、白名单 runtime 版本与输入图像字节数/SHA）、`tool-result-diagnostics.jsonl`（ACP terminal update v2 HMAC 聚合和 Cordis binding v1 白名单计数）及 `build-status.json`（结果、退出码、耗时、stdout/stderr 字节数）。raw trace、自由文本原因/evidence error、run/session/request/call ID、工具参数、answer/hash、thought 内容/长度、profile、Rust postcheck 和模型几何文件不进入这些发布文件或控制台。诊断投影现在默认生成，没有 debug/raw dump 开关；strict gate 逻辑保持原样。

为满足 probe/DSH 的工作目录契约，runner 将会话 manifest、stream/native audit、profile 和候选 artifacts 写到系统临时目录的私有 scratch；正常完成、错误退出及常规进程退出都会尝试删除。该 scratch 在运行期间仍是本机临时文件，并非内存文件系统；断电、强制结束进程或删除失败可能留下 scratch。发布层重新构造固定 gate-name allowlist、只输出状态/reason_count；自由文本原因、evidence error、scope 与 runtime 字符串都会丢弃或映射为固定标签。合成 sentinel 回归测试把 ACP/native ID、answer、thought、payload 与参数 marker 注入 scope、gate names/reasons、failure/evidence errors、runtime 及 projections，扫描每个输出文件并确认没有泄漏，同时确认 passed gate 和 v2/v1 projection 保留有效字段：55/55 通过。未启动真实 DSH/provider，也未生成 Run15 gate 结论；待代码审阅后再决定是否重跑。
## Run15 真实复跑（2026-10-04）

### 运行边界

- 在 worktree HEAD `2461295` 上确认工作树干净后，使用 pinned Node `v22.23.3`、DSH `0.2.0-rc.2` 及当前 checkout 离线编译出的 `native_read_probe` / `native_agent_profile`。运行前重新核对凭据仅存在状态、原图字节数及 SHA；没有读取或输出 API key 值。
- 公开 provider descriptor 仅包含 `https://api.deepseek.com/v1`、`deepseek-flash` 和 `low`，不含密钥。使用默认原始参考图和 runner 默认用户文字；未提供 CADPlan、尺寸、孔数或其它答案提示。runner 只启动一次；未启动 GUI/CATIA，未重试。
- runner 进程成功创建，没有发生 CreateProcess 策略拒绝。约 8 分钟边界复核准备受控停止时，runner/DSH 进程均已退出，因此没有发送停止信号。
- 仅读取发布白名单中的 `summary.json`、`tool-result-diagnostics.jsonl`、`build-status.json`。不读取 raw trace、模型 answer/thought、payload、ACP/native ID 或自由文本错误/构建正文。
- 安全摘要和两项 projection 位于本机 `%TEMP%\aimodeler-f7-g8-run15\strict-run15-<unique>\`。该本机临时证据目录不纳入仓库提交。

### 结果与严格门禁

runner exit code 为 `1`；总体与 strict-gates 状态均为 `failed`，transport 为 `completed`，model status 为 `candidate`。严格失败原因计数为 `13`，evidence error count 为 `0`。构建状态为 `passed`，exit code `0`，耗时 `1840 ms`，stdout `0 bytes`、stderr `72 bytes`；没有读取构建输出正文。

原图为 `72,309 bytes`，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，运行前后未变。scratch cleanup 状态为 `removed`；启动时 stale-scratch recovery 为 `complete`，扫描、清理、保留、拒绝、owner 状态未知及清理失败计数均为 `0`。

| Gate | 状态 | 固定原因码 |
|---|---|---|
| `run_scope` | passed | — |
| `dsh` | passed | — |
| `trace_integrity` | passed | — |
| `original_image` | passed | — |
| `main_image_wire` | passed | — |
| `cordis_cad_tools` | failed | `acp_cad_tool_failed`; `cad_tool_rejected_unbound_or_unaudited`; `cordis_tool_arguments_or_identity_missing_or_unmatched`; `native_calls_not_individually_observed_or_duplicate`; `successful_cordis_cad_call_and_native_audit_missing`; `tool_call_rejected_or_failed` |
| `acp_cad_tool_results` | failed | `acp_cad_tool_result_failed`; `acp_cad_tool_result_terminal_update_duplicated` |
| `current_geometry` | passed | — |
| `artifacts` | passed | — |
| `holes` | failed | `holes_incomplete_or_unverified` |
| `visual` | failed | `visual_coverage_incomplete_or_conflicting`; `visual_gate_original_reference_binding_missing` |
| `finish` | failed | `finish_findings_construction_or_external_facts_incomplete`; `host_finish_not_accepted` |

### 安全工具结果投影

`acp_terminal_update_projection_v2` 的 start/update 状态计数如下；每对数值依次表示 `in_progress start / completed update`，带 failed update 的工具单列说明：

| 工具 | 状态计数 |
|---|---|
| `cad_add_face_recess` | 2 / 4 |
| `cad_add_feature` | 1 / 2 |
| `cad_add_hole_array` | 2 / 4 |
| `cad_create_model` | 1 / 2 |
| `cad_finish_model` | 1 / 2 |
| `cad_get_feature_spec` | start 3，completed 2，failed 1 |
| `cad_get_reference_image` | 7 / 7 |
| `cad_get_state` | 2 / 3 |
| `cad_inspect_references` | 4 / 6 |
| `cad_preview_model` | 2 / 4 |
| `cad_review_reference_similarity` | 2 / 4 |
| `cad_set_construction_milestone` | 2 / 2 |
| `cad_update_feature` | start 1，failed update 1 |
| `cad_validate_model` | 1 / 1 |

终态调用组分布为：`completed_once` 13 组（`cad_get_feature_spec` 2、`cad_get_reference_image` 7、`cad_get_state` 1、`cad_set_construction_milestone` 2、`cad_validate_model` 1）；`failed_once` 2 组（`cad_get_feature_spec` 1、`cad_update_feature` 1）；`repeated_same_status_distinct_updates` 14 组（`cad_add_face_recess` 2、`cad_add_feature` 1、`cad_add_hole_array` 2、`cad_create_model` 1、`cad_finish_model` 1、`cad_get_state` 1、`cad_inspect_references` 2、`cad_preview_model` 2、`cad_review_reference_similarity` 2）。相同/不同脱敏终态摘要对计数为 `0 / 14`；HMAC 不可用事件数 `0`；缺少 tool-call ID 事件数 `0`。

`cordis_binding_projection_v1` 的 ACP→bridge 阶段中，匹配计数为：`cad_add_face_recess` 2、`cad_add_feature` 1、`cad_add_hole_array` 2、`cad_create_model` 1、`cad_finish_model` 1、`cad_get_feature_spec` 3、`cad_get_reference_image` 7、`cad_inspect_references` 4、`cad_review_reference_similarity` 2、`cad_set_construction_milestone` 2、`cad_validate_model` 1。另有 `cad_get_state` 和 `cad_preview_model` 各 2 次 `identity_pairing_ambiguous`，以及 `cad_update_feature` 1 次 `bridge_call_missing`。

bridge→audit 阶段的匹配计数为：`cad_add_face_recess` 2、`cad_add_feature` 1、`cad_add_hole_array` 2、`cad_create_model` 1、`cad_finish_model` 1、`cad_get_feature_spec` 2、`cad_get_reference_image` 7、`cad_get_state` 2、`cad_inspect_references` 4、`cad_preview_model` 2、`cad_review_reference_similarity` 2、`cad_set_construction_milestone` 2、`cad_validate_model` 1；另有 `cad_get_feature_spec` 1 次 `bridge_execution_rejected`。这些计数不包含可识别调用 ID、工具参数或错误正文。

本轮严格门禁失败，不代表模型视觉验收通过；`current_geometry`、`artifacts` 通过也不能替代独立视觉复核或 CATIA 验收。安全投影只缩小了 Cordis binding、孔完整性、视觉覆盖/原图绑定及 finish 未通过的范围，不足以从投影推断具体工具参数或模型答复。不得据此放宽 gate。

### 后续门禁修正

Run15 在旧 gate 逻辑下执行；当时同一调用存在多条 terminal 状态更新会直接触发 `acp_cad_tool_result_terminal_update_duplicated`。随后按 ACP 更新/upsert 语义，将 gate 调整为按终态状态集合判断：重复相同状态快照幂等，同状态通知数仍保留在诊断投影中；`failed`、混合终态、缺身份与无法唯一关联仍阻断。细节和 86/86 合成回归见[ACP 终态状态门禁](./62-f7-acp-terminal-state-gate.md)。Run15 原始结果不回写或重解释；新规则尚未在真实 DSH 冷启动中复验，且不会解决本次独立失败的孔位、视觉覆盖/原图绑定、finish 和剩余 ACP↔Cordis 关联。
