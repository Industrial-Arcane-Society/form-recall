# F7.G9 Native Cordis → Rust 安全 dispatch 阶段诊断

## 目标

Run36 的安全 Cordis binding projection 可指出 `bridge_execution_rejected`，却不能判断 Rust handler 在 executor 前拒绝、executor 返回错误，还是调用响应未写回/格式无效。本 feature 为 Native Cordis → Rust `tools/call` 增加固定枚举阶段和失败码，并投影到 coldstart allowlist summary。

## 实现边界

- Rust JSON-RPC 错误响应只增加 `dispatch_stage` 固定枚举；不向诊断投影传递自由文本 `message`。
- coldstart 代理只从 bridge JSON-RPC error 的 `data.code` / `data.dispatch_stage` 中映射允许值；原始 RPC error、调用 ID、工具参数、argument fingerprint、路径/hash、thought 均不进入投影。
- `cordis_binding_projection_v1` 以兼容方式增加 `stage=bridge_dispatch`、`outcome=bridge_dispatch_failed` 的汇总行，包含允许列表中的 `dispatch_stage` 和 `failure_code`。已有 `bridge_to_audit` outcome 仍独立表达审计关联和 native audit 结果。
- 阶段目前能明确表达 `pre_executor_validation`、`pre_executor_deadline`、`executor_return` 与 `response_writeback`。未能可靠识别的响应统一为 `unknown`；网络断开/无响应投影为 `response_writeback / response_missing`。这不保留精确时刻或 invocation identity。

## 验证

- `node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs`：135/135 通过；覆盖 mock bridge error stage/code 解析、固定 projection、publication 边界与 sentinel 不泄漏。
- `pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter 'native_tool_bridge::tests' -NoCapture`：3/3 通过；验证参数重用/未知工具为 executor 前校验、deadline 在 executor 前超时、executor error 标为 executor return。
- 直接 `cargo test` 的 test executable 在 Windows loader 阶段遇到 `0xc0000139 STATUS_ENTRYPOINT_NOT_FOUND`；使用仓库 test-lib wrapper 后，带 Common Controls manifest 的实际测试运行通过。
- `rustfmt --edition 2021 --check src/apps/desktop/src-tauri/src/native_tool_bridge.rs` 与 `git diff --check` 通过。

本项只证明安全诊断契约；不重跑真实 DSH/provider，不读取 raw run logs、scratch 或几何载荷，不解释 Run36 某一具体失败的 root cause，也不代表 F7/G8 或视觉验收通过。

## 后续核对：鉴权拒绝阶段

2026-10-06 对照 Run37 与当前 producer/consumer 后发现，`tools/call` 在 executor 前因 token/session 鉴权失败会返回 `UNAUTHORIZED`，但通用 Rust 错误写入器原先将该响应的 `dispatch_stage` 标为 `unknown`。这样 proxy 与 allowlist 虽能保留失败结果，却无法确认这是 executor 前拒绝。现将 `UNAUTHORIZED` 固定标为 `pre_executor_validation`；错误码仍经 safe mapper 投影为 `unknown`，不增加敏感或可枚举的鉴权细节。Rust 回归直接发出带无效 token 的 `tools/call` 并检查固定阶段。更新后 `native_tool_bridge::tests` 3/3、Node coldstart suite 135/135、rustfmt 与 `git diff --check` 通过。此修复只补齐诊断分类，不改变鉴权行为，也不回溯 Run37。
