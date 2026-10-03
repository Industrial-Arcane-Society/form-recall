# F7.G3：回复结束与模型状态独立

ACP 的 `stopReason` 只决定本轮 `turn_status`，不再推导模型完成。Host 另行读取当前 CADPlan、revision/fingerprint、预览几何检查、视觉证据及 finish_summary；旧 revision 或旧 fingerprint 的 finish 不能显示 accepted，过期预览显示 stale。

界面显示“回复完成”“已取消”或实际停止原因。模型完成、几何和视觉状态单独进入日志；纯问答不会显示“Agent 建模完成”。未完成模型仍可作为候选存在。

## 验证

- Rust Agent Host 定向测试 9/9，通过纯问答无模型、当前几何状态、旧 finish revision/fingerprint 和旧 preview revision/fingerprint 正负例。
- Vue TypeScript 检查和 Vite production build 通过；现有大 chunk 提示保留。
- 未启动桌面做视觉验收，未证明真实 DSH 工具失败/验收通过的四场景 UI 行为。该端到端证据仍由 G8 完成。

2026-10-03 新增提交 `73938f4`：从生产终态事件载荷构建函数验证 `completed` 和 `stopped` 两种轮次结果都独立携带当前模型的 `accepted` 状态；`agent_host::tests` 16/16 通过。该测试不覆盖真实 ACP/DSH 事件送达 Tauri channel 后由 Vue 渲染。
