# F7.G8 桥接拒绝与 native audit 拒绝诊断修复

## 问题

在 `40d5ddf` 基线上，`summarizeCordisBindingDiagnostics` 先判断 `call.success !== true`，立即产生 `bridge_execution_rejected`。这会隐藏同一工具、同一调用 ID、同一运行时间范围内存在的失败 native audit，因此公开摘要无法区分“Rust native handler 已拒绝”与“桥接执行/响应失败”。这属于诊断投影缺陷，不改变调用的执行结果或 strict gate。

## 修复

代码提交 `4d0bd26` 保留既有 run scope、时间、schema 和 registration 校验，并在失败桥接调用上读取已经 join 的 timed native audit 状态：

| 同一调用的 timed native audit | 公开结果 |
|---|---|
| 存在明确失败记录，且没有成功记录 | `native_audit_rejected` |
| 不存在匹配的 timed audit | `bridge_execution_rejected` |
| 存在成功 audit | `bridge_execution_rejected` |

桥接调用本身成功时，原来的 missing、tool mismatch、out-of-run、audit rejected 与 matched 判定保持不变。新增结果使用已有固定 allowlist 枚举；投影仍只公开工具名、阶段、固定结果码与聚合计数，不公开调用 ID、ACP ID、参数、指纹或错误正文。该结果只定位到 native audit 被拒绝，不公开其底层错误类型或参数校验细节。

## 验证

- `node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs`：126/126 通过。
- 三条新增回归覆盖 failed bridge + failed timed audit、无 audit row、成功 audit；均断言诊断不会包含合成错误正文、调用 ID、参数值或参数指纹。
- `git diff --check` 通过。

## 证据边界与下一步

此修复只根据源代码与合成 fixture 验证安全诊断行为，没有重新运行真实 DSH。Run24 的历史 projection 仍记录 `bridge_execution_rejected`；本修复不能追溯分类那些调用，也不能推断失败尺寸或几何问题。下一步需在包含此修复的固定 commit 上使用新 worktree 与新 output 重新进行真实冷启动，并继续遵守 Run24 allowlist。
