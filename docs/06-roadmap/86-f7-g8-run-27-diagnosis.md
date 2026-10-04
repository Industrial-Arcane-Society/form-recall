# F7/G8 Run27：单次 CLI 冷启动输入校验失败

日期：2026-10-04

## 运行设置

- 固定 Run27 worktree HEAD：`1fdd64636f54b42d160d4c7015719d0e3c06b545`。
- 无 GUI；使用项目 runner `native_agent_coldstart_acceptance.mjs`，默认原图与默认 prompt；未提供 CADPlan、尺寸、孔数或额外用户文本 seed。
- 使用主 Agent 提供的 clean-target runtime manifest，Node/DSH runtime probe，以及 Run26 报告中的 provider-public descriptor 路径。API key 未读取或输出。
- 临时输出父目录：`C:\Users\lycohana\AppData\Local\Temp\f7-g8-run27-20261004-e79a20b2bd0b4b4c927f068cdc329127`。调用前 `safe-output` leaf 不存在，由 runner 创建。
- stdout/stderr 重定向丢弃。只读取 runner allowlist 的 `summary.json`、`build-status.json`、`tool-result-diagnostics.jsonl`；后两者不存在。未读取其他日志、scratch、模型思考/回答、工具参数、几何或图像数据。
- runner exit code `1`，调用约 2.8 秒后结束。

## Allowlist 结果

只存在 `summary.json`。其中：

| 字段 | 值 |
|---|---|
| `status` | `failed` |
| `transport_status` | `failed` |
| `model_status` | `none` |
| `status_scope` | `scope_unverified` |
| `runtime.node` / `runtime.dsh` | `unverified` / `unverified` |
| `failure_diagnostic.stage` | `input_validation` |
| `failure_diagnostic.code` | `uncaught_exception` |
| `strict_gates.status` | `failed` |
| `strict_gates.failure_reason_count` / `evidence_error_count` | `1` / `0` |
| `strict_gates.gates` | 空对象 |

`build-status.json` 与 `tool-result-diagnostics.jsonl` 缺失。因此本轮没有可用的 gate 子条件投影，不能据此判断 Run26 的 visual/finish 根因，也不代表视觉门禁通过或失败。

## 启动限制与归因边界

本次 PowerShell 命令在传递参考图路径时使用了未加括号的字符串拼接表达式，可能导致原图参数被拆分或错误传递。该调用确实在 runner 记录的 `input_validation` 阶段退出；safe summary 没有显示 DSH/provider runtime 已识别，也没有产生 ACP/Cordis 投影。更细的异常原因不在 allowlist 产物中，因此这里将参考图参数拼接视作最可能的本地调用错误，而非已证实根因。

按本轮约束没有重试。没有证据可确认 provider 是否实际收到任何请求；本报告不把该尝试记作真实多模态链路或 strict gate 运行，也不据此推断模型行为。后续如需 Run28，应先修正 PowerShell 参数构造并由用户/主流程明确安排新的单次运行。
