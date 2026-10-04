# F7.G5 人工 finding 来源重绑确认

日期：2026-10-04

## 实现范围

提交 `10668ce` 增加 Rust finding adjudication/read API、Tauri command、Rust executor/bridge 接线及 Vue 确认界面。确认请求绑定活动模型 generation 和 CAD revision、finding、唯一 pending candidate/evidence/observation、source SHA、user-target fingerprint、视角与 affected feature IDs；事务锁和 CAS 拒绝过期或已变化的状态。

人工确认只记录来源重绑，并将 finding 保持为 `awaiting_verification`，响应 `resolved=false`。finding 之后仍须由更晚 revision 上的完整独立 review 关闭。活动 Agent 正在运行时拒绝人工写入。Vue 通过 Tauri command 通信，不直接修改 `cad-state.json`。

## 验证

- Rust `native_review_findings`：11/11 通过。
- Node payload 单测与生产 Vue Agent-event 集成：4/4 通过。
- `AIMODELER_UI_SCENARIO=finding-adjudication` 的生产 Vue 挂载场景：1/1 通过，确认传出精确 CAS candidate，finding 仍未 resolved。
- `npm run build`：通过。
- 全量 Rust 套件在子任务 worktree 中为 350/353；两项失败分别依赖未设置的 CLI Node executable 与 OCCT runtime，另有一项 bridge RPC 并行测试首次失败、单测重跑通过。未将定向测试结果外推为全量通过。

## 审查边界与剩余风险

独立审查未发现人工确认路径可直接关闭 finding；但发现既有自动跨 observation rebind 仍接受视觉 provider 声明的唯一 `source_evidence_id`。SHA、target、view、features、新 preview 与几何 review 等校验会缩小风险，却不能证明 provider 在两个 observation 中重复声明的 ID 指向同一真实图像证据。因此 G5 的人工路径目前覆盖缺失/歧义身份，不能消除所有自动语义错绑风险；是否要求所有跨 observation rebind 必须人工确认，仍需作为后续设计/验收项处理。

该提交没有真实 DSH 冷启动或真实图片复核结果。mock/headless 测试只证明状态约束和 UI/host 接线。
