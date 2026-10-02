# F7.G5：冲突驱动的图像观察刷新

`cad_inspect_references` 默认按 source SHA、reference ID 和 ROI 复用 observation 缓存，不把一次失败或无效观察变成程序自动重试循环。Agent 判定旧观察与其他证据冲突时，必须明确传 `refresh_reason="conflict"`；确实需要重新读同一原图/ROI 时传 `"reobserve"`。未提供原因继续用缓存；未知值、错误类型和其他字符串会被拒绝。

每次新观察递增 `observation_version` 并生成新的 `observation_id`，evidence ID 包含观察版本。状态保留旧观察与新观察历史，绑定来源 SHA、ROI、刷新原因、有效状态和 provider diagnostics。视觉 review fingerprint 同时绑定当前 observation ID、版本和观察内容；状态摘要不会把旧 observation 的 review 计入新 observation 的 coverage。旧 finding 保持未解决，不能拿旧 evidence ID 关闭新版差异。

## 验证

- `cargo test --manifest-path src/apps/desktop/src-tauri/Cargo.toml --lib --offline native_vision_domain::tests`：11/11。
- `cargo test --manifest-path src/apps/desktop/src-tauri/Cargo.toml --lib --offline native_state_summary::tests`：5/5。
- `cargo test --manifest-path src/apps/desktop/src-tauri/Cargo.toml --lib --offline native_tool_schemas::tests`：3/3。
- 回归覆盖：首次观察、普通重复调用复用、冲突刷新、新版 ID、ROI 重新读取、旧 review 不贡献新版覆盖，以及旧 evidence 不能关闭新 finding。
- `git diff --check` 通过。

这是 mock provider 下的 domain/schema/summary 验证，尚未证明真实视觉模型在几何冲突时会主动调用刷新；该模型决策与差异关闭由 F7.G8 后续实测。
