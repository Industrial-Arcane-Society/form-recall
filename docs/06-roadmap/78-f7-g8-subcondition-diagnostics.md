# F7 G8 safe subcondition diagnostics

日期：2026-10-04

## 范围

本 feature 细化 cold-start strict gate 的失败原因，并扩展 ACP safe projection。只修改 Node runner/gate/publication 与对应 Node 测试；不改 Rust finding/session 逻辑、主 Todo，也不启动 GUI、DSH 或 CATIA。

## 固定安全 reason codes

- `dsh_session` 与 `dsh_process` 分开：session RPC/能力/完成状态与 DSH child exit code/exit observation 可分别定位。
- `current_geometry` 将 postcheck revision/signature、preview/checks/finish/review revision 与 fingerprint、native engine/check state/solid count/validation、partial preview、attestation binding/validity/check equality 及 review freshness 分开。
- `holes` 分别报告 finish measurement、review evidence、native measurement completeness 与 verified 状态。
- `visual` 分别报告各层 coverage、independent review、manifest/mismatch、original reference inventory count/ID/hash/coverage/validity，以及 Rust visual summary freshness。
- `finish` 分别报告 Host/域状态、summary 状态、acceptance flag、geometry review acceptance/issuer/decision、construction、open/unresolved findings 与 external facts required/verified/failed/unknown。

Publisher 对每个 gate 的 reason codes 使用固定 allowlist。未知 reason 不会透传；只留下数值 `reason_count`。缺省或非法 gate status 仍按 failed 处理，保持 fail-closed。

## DSH safe failure 分类

DSH session 失败只发布白名单阶段、RPC method 和 error class。阶段为 `session_rpc`、`capability_check`、`reasoning_selection` 或 `unknown`；method 可为 `initialize`、`session/new`、`session/set_config_option`、`session/prompt`、`session/close`、`session/cancel` 或 `unknown`。error class 可为 `none`、`rpc_error`、`timeout`、`child_exit`、`write_error`、`capability_missing`、`invalid_response`、`config_selection_failed` 或 `unknown`。RPC error payload、message、provider 内容、异常栈、参数、路径、调用 ID 和密钥都不会进入安全诊断。

## ACP 重复参数摘要

ACP projection schema 从 `acp_terminal_update_projection_v2` 升为 `v3`，新增 `repeated_argument_fingerprint_counts`，按 allowlisted CAD tool 汇总重复参数指纹对应的额外调用数。指纹仅用于 runner 内存分组，projection 不发布指纹、调用 ID、ROI 或参数；不同指纹不会被算作同参数重复。该指标提示重复行为，不单独证明无进展或判断 ROI 是否相同。

## Run22 静态链路审查

- `native_tool_schemas.rs` 中存在 `cad_finish_model` schema/契约断言；`native_cad_tools.rs` 将它列为支持工具并路由至 finish 处理器。`agent_host.rs` 读取 `src/legacy/prompts/dsh_agent/system.md` 并把 native tool schema 提供给 DSH Host。
- 系统提示词要求在当前 revision 有效预览后调用 finish，并明确允许保存未验收 candidate。`native_cad_session.rs` 的回归覆盖了存在 open finding 时仍可保存 `accepted=false` candidate。因此，所有验收 gate 失败本身不足以解释 Run22 未见 finish 记录。
- 几何变更会使已有预览及 finish 状态失效；finish 需要当前 revision/geometry fingerprint 对应的预览。若最后工具顺序导致 stale preview，finish handler 可能拒绝该状态；这是源码允许的路径之一，不能据此断言 Run22 实际发生了这一情况。
- Run22 的 safe projection 不含工具顺序、finish 请求是否发出或 finish 工具结果分类。现有证据只能证明 allowlist 中没有 finish terminal group，以及 DSH 未正常完成/退出码门禁失败；是模型未请求、调用失败、session 提前结束或绑定状态不匹配，仍未知。

本次静态审查没有放宽 finish/geometry/visual gates，也没有读取 raw provider/DSH logs、工具 payload 或模型回答。Run23 已使用包含该诊断代码的固定 commit 完成单次真实 provider 冷启动；分开的 session/process、geometry、holes、visual 和 finish 结果及边界见 [Run23 诊断](./79-f7-g8-run-23-diagnosis.md)。

## 验证与边界

`node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs`：**123/123 通过**。测试覆盖新增 reason predicate 翻转、相同/不同参数指纹计数、非法枚举降级、secret sentinel 不外泄及既有 fail-closed 和 publication 行为。

123/123 是合成 fixture 验证；Run23 是单独的真实 provider 冷启动证据，确认新 reason codes 可应用于 DSH session/process 与各子门禁。Run23 的视觉和 finish 仍失败，见 [Run23 诊断](./79-f7-g8-run-23-diagnosis.md)。重复参数计数只保留总数，不能区分不同 ROI，也不能据此宣称 Agent 陷入无进展循环。
