# F7.G8 安全 Gate 失败原因摘要

## 变更

严格冷启动验收产物 `summary.json` 之前只公开每个 gate 的失败原因数量。Run15 若获准重新执行，结果仍无法只靠公开摘要判断失败集中在哪个静态检查项。

现在每个固定 gate 除 `status` 和 `reason_count` 外，还输出 `reason_codes`。发布器按 gate 使用源码中固定的原因码白名单，只保留匹配的静态代码，并排序、去重。未知字符串继续过滤；原始 gate 名称、trace、临时 ID、图片或工具 payload、模型 answer/thought 均不进入摘要。`reason_count` 仍按内部 gate 结果计数，因此若发现白名单外原因，数量和可见代码数可能不同，但未知内容不会被写出。

该变化仅增加诊断可见性，不修改 `evaluateColdstartGates`、任何 pass/fail 条件，也不放宽 Run15 的严格门槛。它不能替代真实 DSH/provider 冷启动、视觉复核或 CATIA 验收。

## 验证

- `node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs`：77/77 通过。
- 发布测试注入白名单内的 `run_identity_missing` 与 sentinel 未知原因；产物仅包含前者，并确认 sentinel、ACP/native ID、answer/thought 与工具 payload 不泄露。
- `git diff --check` 通过。

## 重新执行后的用途

如果后续环境允许单次严格 CLI runner 启动，可只读公开的 `summary.json`，直接定位失败 gate 的白名单原因码。当前机器上的 Run15 在 runner CreateProcess 前已被策略拒绝；本变更没有启动 provider，也没有生成 Run15 gate 结果。
