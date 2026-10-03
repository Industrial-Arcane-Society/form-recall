# F7.G2：CADPlan、Agent 与桌面活动模型同步

## 实现范围

Rust `native_active_model` 记录当前活动工程、CADPlan、CAD revision、几何 fingerprint、原生输出路径、参考图清单和建模状态。Quick 创建、Agent 的几何写工具、特征编辑、显式旧 `.aimodel` 导入、保存、导出和单视图/多视图快照都读取或更新该记录。Agent host 观察 generation 变化后发送 `model.changed`，Vue 用同一 DocumentState 更新特征树和视口。

Agent 会话打开或恢复时比较会话 CAD 状态与活动 generation；旧会话不会覆盖 Quick、UI 或另一会话已提交的模型。几何工具先完成活动记录 CAS，再写会话状态。CAS 冲突时，会话状态、参考图清单、用户轮次清单和预览会回到最新活动记录；活动记录缺失时，会清除旧模型/证据并返回受控错误。Save As 更新活动记录、DocumentState 和 CAD 会话状态中的 `.aimodel` 路径。

## 可见限制

- CADPlan v1 不包含特征 `enabled` 状态，原生模式会禁用无损启停控件；可删除特征并重建。
- 原生活动模型支持本地 `.aimodel`、STL、几何报告和 PNG 多视图快照。OBJ 与原生 CATIA 导出当前明确返回不支持，不会悄悄回到另一份 Python 文档。
- 导入旧工程要求用户明确确认；旧特征必须完整映射为有效 CADPlan 并通过 OCCT 重建，否则导入会拒绝。
- 保存事务可回滚已存在的目标文件；它以 staging、generation 检查和恢复协调两个文件，不是跨文件系统提供的单次原子写。

## 验证与待验收

Luna subagent 报告以下定向离线验证通过：

- `native_active_model` 存储/并发测试：7 项；
- Agent CAS 冲突后会话回同步：1 项；
- 活动记录缺失时清除过期会话模型和证据：1 项；
- Save As 更新路径与冲突保护：1 项；
- `cargo check --lib --offline`；
- Vue build 与活动模型导入测试（此前 G2 验证）。

2026-10-03 新增提交 `a4dfe2b` 的 production-helper 测试：从活动 generation 1 执行 Agent CAD 编辑后，共享活动记录升至 generation 2，来源为 `agent`，会话快照与共享 CAD state 相同。新 happy-path 测试和既有 stale-CAS 回同步测试各 1/1 通过，`cargo check --lib --offline` 通过。

这些验证覆盖原生状态和保存事务，不证明运行中的 DSH 真实触发了 `model.changed`、桌面视口即时接收了更新，也不证明 UI 初始化和旧工程转换在真实 Tauri 窗口中完成。F7.G2 在真实 host/Agent/桌面闭环验收完成之前保持未验收。
