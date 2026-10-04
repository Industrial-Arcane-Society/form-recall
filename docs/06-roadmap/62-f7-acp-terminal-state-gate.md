# F7: ACP 终态状态门禁

## 背景

Run15 的安全聚合诊断显示存在重复 ACP 终态更新，但聚合计数无法证明这些更新对应哪些工具调用，也无法判断它们来自重复通知、状态快照还是底层重放。因此不能把它直接归因为 DSH 或桌面宿主的重复执行。

门禁原先把同一个 `toolCallId` 收到两次 `completed` / `failed` 通知直接判为重复结果，并且在同参数调用关联时要求终态通知数恰好为 1。这把“通知条数”当成了“调用终态数量”。ACP v1 将 `tool_call_update` 定义为针对既有工具调用的更新，除 `toolCallId` 外字段均可省略，只需发送变更字段；`status` 表示当前执行状态。参考：[ACP v1 Tool Calls: Updating and Status](https://agentclientprotocol.com/protocol/v1/tool-calls#updating)。

## 修正

- 同一调用重复收到相同终态状态时，门禁按一个终态状态解释；通知计数仍留在安全聚合投影中，供诊断使用。
- `failed` 仍然阻断；同一个调用同时出现 `completed` 和 `failed` 时作为状态冲突阻断。
- 缺少 ACP 调用 ID、没有终态、调用开始重复、跨 run/session/turn、Cordis 参数指纹不匹配等情况仍 fail closed。
- 重复同名同参数调用的匹配仍要求每个 ACP 调用和 Cordis 调用构成唯一的双向时间区间匹配；首次进入终态的时间作为调用结束边界，后续相同状态快照不会延长区间。
- 该变更只调整调用状态解释，不证明真实调用只执行一次，也不放宽模型视觉、几何、孔位或完成审查门禁。

## 验收

固定 Node 22.23.3 的 `native_agent_coldstart_gates.test.mjs` 定向套件：86/86 通过。新增覆盖包括：

- 同 ID 重复 `completed` 更新可幂等通过；
- 重复 `failed` 仍报告工具失败；
- `completed` 与 `failed` 混合仍报告状态冲突并阻断；
- 重复同参数调用使用首次终态时间做唯一配对；
- 时间重叠、缺终态、跨作用域和缺少身份仍失败或保持歧义。

该测试是合成 gate 回归，不调用 Provider，也不构成真实 DSH 复跑证据。Run15 仍为失败候选：孔位、视觉覆盖/原图绑定、完成审查，以及部分 ACP↔Cordis 关联仍未通过；必须在后续严格冷启动中分别复核。
