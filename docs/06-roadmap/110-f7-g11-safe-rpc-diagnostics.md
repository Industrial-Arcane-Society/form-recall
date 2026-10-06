# F7.G11：DSH JSON-RPC 脱敏诊断实现

## 实现范围

`native_agent_coldstart_prompt.mjs` 在读到 JSON-RPC error 后，仅从 `error.code` 立即映射固定桶：`parse_error`、`invalid_request`、`method_not_found`、`invalid_params`、`internal_error`、`server_error`、`unknown`。JSON-RPC message 与 data 不复制进异常、日志或投影。每个 request 的本地递增数值序号只在该 runner 进程内有意义；错误关联字段为 `request_sequence`。

`native_agent_coldstart_acceptance.mjs` 将这两个安全字段带入 DSH failure diagnostic。`native_agent_coldstart_publication.mjs` 再次验证枚举和序号范围后才发布。summary 仍不含 RPC 原始 code、message/data、prompt、provider 内容或工具参数；request sequence 不是 session/tool/provider ID。

## 验证

定向 fake ACP 回归返回 `-32602`，并在响应携带秘密 message/data sentinel；断言客户端只暴露 `invalid_params` 和请求序号，safe summary 只保留枚举/数值且不含 sentinel。覆盖标准 code、server 范围和非标准值归桶。

本次没有执行 DSH 或长时间 coldstart。因而代码路径与合成隐私边界已验证，真实 runner 的冷启动产物留存尚待后续受控运行确认；此实现不解释 Run39 的根因。
