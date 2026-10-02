# F7.G3：请求终态与模型验收状态的可见区分

## 本 feature

Agent 对话区固定展示两组独立状态：当前轮次（处理中、回复完成、取消、停止、结束或失败）与当前 CAD 模型（候选或已验收），并列出完成状态、几何状态、视觉状态。ACP 回合完成不会提升 CAD 模型验收等级。

模型摘要只有在宿主状态为 accepted、finish summary 明确 accepted，且 finish revision 与当前 CAD revision、geometry fingerprint 与当前模型指纹一致时才显示“已验收”。Vue 仅收到文档/模型状态时也做相同绑定校验，过期 finish 显示“已过期”，避免模型修改后用旧摘要误标。

纯状态映射位于 `src/agentStatus.mjs`，UI 接线位于 `src/App.vue`。没有新增服务端 acceptance 写入行为。

## 验收

- `node --test src/agentStatus.test.mjs`：7/7通过，覆盖 ACP completed+candidate、当前 accepted、取消/停止/结束、缺少模型摘要、finish revision 过期、geometry fingerprint 过期和宿主摘要错绑。
- `npm run build`：Vue 类型检查和生产 bundle 通过；构建保留现有单个 500 KB 以上 chunk 提示。
- 没有打开 Tauri GUI。本记录证明状态映射与 bundle 接线，不证明真实 `AgentHost → Tauri Channel → Vue` 事件渲染或真实模型达到 accepted；这两项仍需 G2/G8 端到端证据。

## 未覆盖

- Tauri 实际模型变更事件、恢复已验收模型和 stale finish 的浏览器/桌面可视检查。
- 真实多模态模型行为和 CATIA 接受状态。
