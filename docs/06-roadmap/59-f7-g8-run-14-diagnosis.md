# F7 G8 Run14 冷启动诊断

## 运行边界

- 在独立 worktree `f7-run14-coldstart` 以严格 runner 单次启动；未重试。没有打开桌面 GUI 或 CATIA。
- 当前 checkout `HEAD` 为 `d7083a8`，包含视觉提示词提交 `e7e5b2e` 和“无进展/歧义时允许保留未验证候选”修订；本次没有改实现代码。
- 输入原始参考图 72,309 bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，运行前校验相符。
- 使用 pinned Node `22.23.3`、DSH `0.2.0-rc.2`。桌面 `config.json` 与工作区 `src/legacy/config.json` 均未找到；按先前冷启动记录使用项目内置 DeepSeek provider 模板 `https://api.deepseek.com/v1` / `deepseek-flash` / configured effort `low`。凭据仅来自当前进程 `DEEPSEEK_API_KEY` 环境变量；报告、descriptor 和提交没有包含凭据。DSH profile 与 state 在新的本地输出目录中生成，不复用旧会话。
- 本 worktree 缺少 `target`，使用既有已固定 package resources 在本 worktree 的 ignored `target/package-resources` 下准备本地构建资源。运行时从当前 checkout 编译。`build-status.json` 的安全字段显示 Cargo build 通过、exit code 0、耗时 416,946 ms；stdout 0 bytes、stderr 9,302 bytes（未读取构建正文）。
- 启用 `--tool-result-diagnostics`。只审阅 runner summary 的状态字段、严格 gate 名称/原因、build 状态和 allowlisted `acp_terminal_update_projection_v1`；没有读取原始 trace、工具 payload/arguments、provider response、模型答复/thought、图像内容或 ACP/native ID。

## 门禁结果

| Gate | 结果 | 原因 |
|---|---|---|
| `run_scope` | 通过 | — |
| `dsh` | 通过 | — |
| `trace_integrity` | 通过 | — |
| `original_image` | 通过 | — |
| `main_image_wire` | 通过 | — |
| `cordis_cad_tools` | 失败 | `successful_cordis_cad_call_and_native_audit_missing`、`cad_tool_rejected_unbound_or_unaudited`、`cordis_tool_arguments_or_identity_missing_or_unmatched`、`native_calls_not_individually_observed_or_duplicate`、`tool_call_rejected_or_failed`、`acp_cad_tool_failed` |
| `acp_cad_tool_results` | 失败 | `acp_cad_tool_result_failed`、`acp_cad_tool_result_terminal_update_duplicated` |
| `current_geometry` | 通过 | — |
| `artifacts` | 通过 | — |
| `holes` | 通过 | — |
| `visual` | 失败 | `visual_coverage_incomplete_or_conflicting`、`visual_gate_original_reference_binding_missing` |
| `finish` | 失败 | `host_finish_not_accepted` |

总体状态为 `failed`；transport `completed`、DSH `completed` 且退出码 0，模型结果为 `candidate`。严格 gate 失败不能被 transport 完成或 candidate 文件存在覆盖。

## Terminal update allowlisted projection

仅统计宽泛工具类别、事件种类和状态枚举；ID 只用于运行时分组且没有输出。

| 投影类别 | 计数 |
|---|---:|
| CAD start / `in_progress` | 24 |
| CAD update / `completed` | 34 |
| CAD update / `failed` | 1 |
| 调用组 `progress_then_completed` | 12 |
| 调用组 `progress_then_failed` | 1 |
| 调用组 `repeated_same_terminal_status` | 11 |
| 终态数量分布 0 / 1 / 2 / 3+ | 0 / 13 / 11 / 0 |
| 相同脱敏终态摘要对 / 不同摘要对 | 11 / 0 |
| 缺少 tool-call ID 的事件 | 0 |

本轮再次观察到非空 ID 调用组中有多次相同类别的 terminal update；其中一个调用组记录了 failed 状态。相同脱敏摘要只代表投影字段中的状态类别相同，**不证明完整 payload 相同，也不能区分合法状态重发、collector 重复观察或真实重放**。projection 不提供相关事件、调用 ID、具体工具、顺序正文或失败原因。没有依据放宽 gate；`acp_cad_tool_result_terminal_update_duplicated` 和 failed gate 均保持严格失败。

## 证据边界与待解决事项

- `current_geometry`、`artifacts`、`holes` 通过只证明本地宿主几何、当前输出与孔计量 gate 的状态，不代表视觉外观正确；本记录未读取尺寸、CADPlan、工件预览或图像内容。
- `cordis_cad_tools` 的多个失败原因与 ACP result gate 同时出现，但由于明确禁止检查 trace、arguments 和 provider response，本轮不能定位具体 Cordis 工具、绑定失败环节或 terminal update 的来源。
- `visual` 与 `finish` 仍失败。本次安全摘要不足以拆解 host inventory、review coverage 或 finish conjunction 的具体子条件；按 [视觉/finish evidence audit](./58-f7-visual-finish-evidence-audit.md) 继续排查，不更改 gate。
- 没有结论称本轮独立视觉质量或 CATIA 验收通过。本次未对模型做人工视觉比较，也没有 GUI/CATIA 证据。
- 本地完整运行目录保留在 ignored 的 `temp/acceptance/f7-g8-run14-<unique>/`，包含不应提交的运行资料；该目录与原图、descriptor、trace、provider数据均未进入 Git。此文档仅记录经过脱敏的验收字段。
