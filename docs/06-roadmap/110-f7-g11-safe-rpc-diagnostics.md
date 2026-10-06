# F7.G11：DSH JSON-RPC 脱敏诊断实现

## 实现范围

`native_agent_coldstart_prompt.mjs` 在读到 JSON-RPC error 后，仅从 `error.code` 立即映射固定桶：`parse_error`、`invalid_request`、`method_not_found`、`invalid_params`、`internal_error`、`server_error`、`unknown`。JSON-RPC message 与 data 不复制进异常、日志或投影。每个 request 的本地递增数值序号只在该 runner 进程内有意义；错误关联字段为 `request_sequence`。

`native_agent_coldstart_acceptance.mjs` 将这两个安全字段带入 DSH failure diagnostic。`native_agent_coldstart_publication.mjs` 再次验证枚举和序号范围后才发布。summary 仍不含 RPC 原始 code、message/data、prompt、provider 内容或工具参数；request sequence 不是 session/tool/provider ID。

## 验证

定向 fake ACP 回归固定返回数字错误码 `-32602`，request id 来自 ACP client 的本地递增序号；在 JSON-RPC error 的 `message` 与 `data` 中放入 prompt、provider 和 tool payload sentinel。断言 initialize 与 session/new 后的失败 `session/prompt` 关联本地 `request_sequence=3`、错误桶为 `invalid_params`，异常只有安全字段且使用固定通用 message，完整 safe acceptance summary 不含任何 sentinel。

同一 Node 回归逐项覆盖标准 code `-32700` 至 `-32603`、server 范围边界 `-32099`/`-32000`，以及范围外、非安全整数和非数字输入的 `unknown` 归桶。Windows 的仓库测试入口为 `src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline`；G11 Node 定向回归另用 `node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_prompt.test.mjs`，均为本地合成测试，不启动 DSH/provider。

本次回归没有执行 DSH 或长时间 coldstart。故障分支的本地分类、request sequence 关联与 summary 脱敏已通过确定性注入验证；真实 runner 冷启动是否产生并留存该 failure row 仍待后续受控运行确认。此实现不解释 Run39 的根因，也不改变 Run40 的结果。
