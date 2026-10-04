# F7 visual/finish 安全子条件投影

日期：2026-10-04

## 目的

Run26 的 allowlist summary 只显示 visual 与 finish gate 未通过及其 reason code，无法从受限产物里定位具体阻断条件。为了让 Run27 可做有针对性的排查，cold-start `summary.json` 增加 `visual_finish_diagnostics`，将现有 reason code 映射为固定字段的布尔值，并附上失败条件计数和 `passed`/`failed` 安全状态。

## 投影与边界

- `visual.checks` 固定覆盖原图绑定、inventory/manifest、reference coverage、独立复核、mismatch 和 summary 当前性条件。
- `finish.checks` 固定覆盖 host/domain/summary accepted 状态、overall review、construction、open/unresolved findings 及 external fact 条件。
- 未知 gate reason 不会透传；只增加 `unclassified_failure=true`，并计入 `failed_check_count`。gate 缺失时状态 fail closed，所有 checks 为 false。
- 除布尔值、非负整数计数和固定的 `passed`/`failed` enum 外，不发布 reason 文本、ID、hash、提示词、finding 正文、工具参数、几何/图片数据或 provider 文本。
- 该投影只解释 host gate 评估字段，不构成独立视觉质量、几何质量或 CATIA 验收。

## 实现与验证

实现位于 `native_agent_coldstart_publication.mjs` 的 `buildSafeVisualFinishDiagnostics`，由现有 safe acceptance summary 调用；failure-only summary 和其他输出文件结构未改变。合成测试覆盖 visual mismatch/coverage、finish review/open finding 失败、未知字段剔除和缺失 gate fail-closed。另有现有 table-driven gate 翻转用例覆盖各 predicate 的判定阈值。

相关验证命令：

```powershell
node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs
```

测试只验证本地确定性 gate 与 publication 投影，不证明真实 DSH/provider 运行中会生成完整或正确的视觉证据。Run27 应只审阅安全 allowlist 输出；若 visual/finish 字段显示失败，仍需根据 host 子条件定位缺失 evidence，再决定是否有证据支持代码修复，不得放宽 acceptance thresholds。
