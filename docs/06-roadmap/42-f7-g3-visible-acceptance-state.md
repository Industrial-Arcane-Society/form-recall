# F7.G3：请求终态与模型验收状态的可见区分

## 本 feature

Agent 对话区固定展示两组独立状态：当前轮次（处理中、回复完成、取消、停止、结束或失败）与当前 CAD 模型（候选或已验收），并列出完成状态、几何状态、视觉状态。ACP 回合完成不会提升 CAD 模型验收等级。

模型摘要只有在宿主状态为 accepted、finish summary 明确 accepted，且 finish revision 与当前 CAD revision、geometry fingerprint 与当前模型指纹一致时才显示“已验收”。Vue 仅收到文档/模型状态时也做相同绑定校验，过期 finish 显示“已过期”，避免模型修改后用旧摘要误标。

纯状态映射位于 `src/agentStatus.mjs`，UI 接线位于 `src/App.vue`。没有新增服务端 acceptance 写入行为。

## 验收

- `node --test src/agentStatus.test.mjs`：7/7通过，覆盖 ACP completed+candidate、当前 accepted、取消/停止/结束、缺少模型摘要、finish revision 过期、geometry fingerprint 过期和宿主摘要错绑。
- `npm run build`：Vue 类型检查和生产 bundle 通过；构建保留现有单个 500 KB 以上 chunk 提示。
- 没有打开 Tauri GUI。本记录证明状态映射与 bundle 接线，不证明真实 `AgentHost → Tauri Channel → Vue` 事件渲染或真实模型达到 accepted；这两项仍需 G2/G8 端到端证据。
- `73938f4` 新增 Rust 终态载荷单测，确认 `completed`/`stopped` 轮次状态不改写当前模型 `accepted` 状态；`agent_host::tests` 16/16。它补的是 Host 载荷层证据，channel/UI运行时仍未验证。

## 未覆盖

- Tauri 实际模型变更事件、恢复已验收模型和 stale finish 的浏览器/桌面可视检查。
- 真实多模态模型行为和 CATIA 接受状态。

## 2026-10-03：Rust Channel stale finish 联验

提交 `52b38e1` 将 `accepted=true`、但 finish revision 已过期的状态送入 `App.vue` 生产 Channel handler：Rust 终态保持 `candidate/not_finished`，UI 显示候选模型且不显示“已验收”。`npm run test:agent-channel-vue` 在隔离 worktree 通过，包含 Rust MockRuntime 1/1 和生产 Vue headless handler 检查。测试脚本临时设置 `TAURI_CONFIG.bundle.resources=[]` 以免测试依赖 release-only package-resources，并恢复调用前环境值。

这不是实际 WebView JS 投递。纯问答、工具错误、当前有效 accepted、真实 DSH 与窗口渲染仍未覆盖；G3 不据此整体完成。

## 2026-10-03：Channel→Vue 四场景矩阵

提交 `1dd79b5` 扩展 `npm run test:agent-channel-vue`，同一个生产 `App.vue` Channel handler 现在检查四个由 Rust MockRuntime 捕获的真实 Tauri `Channel<Value>` payload：

| 场景 | Rust/Channel 断言 | 生产 Vue headless 断言 |
|---|---|---|
| 纯问答 | 不发 `model.changed`，`current_model.exists=false` | 显示“尚无 CADPlan 模型”，不出现候选或已验收 |
| CAD 工具失败 | `cad_preview_model` failed、`tool_error`、turn stopped | 显示失败工具和本轮停止，不出现已验收 |
| 当前验收 | finish revision 与 CAD revision 相同，geometry fingerprint 相同 | 显示“已验收” |
| 过期验收 | finish `accepted=true` 但 revision 过期，terminal 为 candidate/not_finished | 显示“候选模型”，不显示已验收 |

`npm run test:agent-channel-vue` 通过；两个 Node 文件 `node --check` 与 `git diff --check` 通过。测试用 fake ACP 场景和 MockRuntime 注入来驱动生产前端 handler，验证消息状态契约；它没有启动真实 WebView，未连接真实 DSH 或 CAD bridge，也不是视觉建模验收。

## 2026-10-05：活动模型启动恢复投影

活动记录启动时不再直接把持久化 DocumentState 当作接受真值。Rust `active_model_snapshot` 按当前 CAD state 和证据 manifests 重算 Host summary，并附上非持久化 projection；Vue 仅在 generation、CAD revision、geometry fingerprint、finish revision、evidence fingerprint 及 Host acceptance 全部吻合时恢复“已验收”。缺失/损坏/陈旧投影保持 candidate/unavailable，且旧 generation 的恢复响应不能覆盖已更新的 Vue 文档。

验证结果：Rust 活动模型投影负例/正例 1/1，Vue helper 3/3，`npm run test:agent-channel-vue` 覆盖 MockRuntime Channel 四场景及生产 App headless startup hydration 的 accepted、stale-projection、candidate 三场景。首轮 wrapper 的 accepted-current 失败是旧 IPC fixture 在临时空 manifests 上预先计算 fingerprint，而 Host 按真实 session manifests 重算；修正仅限测试 fixture，改由 session 文件计算绑定后四场景均通过。headless Teleport 警告是 renderer 缺少 DOM selector 的既有测试限制。

测试边界：启动验证经 Tauri 命令投影与生产 Vue mount 的 headless harness；没有执行 WebView 冷启动/真实 JS 投递、真实 DSH/视觉验收或 CATIA 验收。因此 G3 的恢复状态同步有 headless 证据，完整桌面运行时仍待验收。
