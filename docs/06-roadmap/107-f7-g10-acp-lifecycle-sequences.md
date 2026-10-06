# F7.G10：ACP 工具调用生命周期序列诊断

## 状态

诊断代码已合入并推送到 `main`：`862f5f8`（源提交 `261a875`）。Node 回归通过 **135/135**。本项改善冷启动验收的可诊断性，不代表真实 Agent 建模、视觉审查或验收门禁已通过。

## 变更

- 对允许发布的 Cordis 工具名，聚合发布 ACP call 生命周期的固定序列类别与计数，覆盖无终态、progress 后 completed/failed、无 progress 的终态、终态后更新、重复同终态以及互相冲突的终态。
- 安全投影 schema 升至 v4，发布器严格检查版本、工具名、序列枚举与计数；未知字段或非法条目不进入安全摘要。
- 保留 v3 的既有状态统计与 HMAC 行为。新摘要不输出 ACP call ID、参数、工具正文、错误文本或事件时间戳。

## 验证与边界

在合入后的 `main` 执行：

```powershell
node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs
```

结果：135 passed、0 failed；`git diff --check` 通过。测试覆盖状态序列矩阵、隐私过滤与安全发布白名单。

这是聚合计数，不提供跨调用的逐 call 因果重建。本次没有调用 provider、打开 GUI 或运行 CATIA；真实 DSH 行为仍须由独立冷启动运行验证。
