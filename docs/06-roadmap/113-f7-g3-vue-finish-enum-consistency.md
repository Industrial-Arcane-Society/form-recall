# F7.G3：Vue finish 状态枚举一致性

日期：2026-10-06

## 范围与发现

F7.G3 要求把 Agent 请求结束、模型候选和模型验收作为不同状态展示。本次只处理 Vue 最终 acceptance projection 的边界：`modelAcceptanceView` 原先同时要求 Host `status=accepted`、`acceptance_confirmed=true`、当前 revision/fingerprint 绑定、`finish_status=accepted` 和 `finish_summary.accepted=true`，但没有要求同一 summary 的 `finish_state` 也是 `accepted`。因此一个内部矛盾的 Host snapshot（例如摘要布尔值为 true、枚举值为 `candidate_needs_repair`，其余状态字段均显示 accepted）仍会被渲染成“已验收”。这是 malformed projection 的 fail-open 边界；本次没有证据表明 Run23 或 Run40 曾产生这种矛盾状态。

修复后，Vue 仅在 `finish_summary.finish_state === 'accepted'` 与 accepted 布尔值及其它 Host/current-binding 条件同时成立时显示“已验收”。反例测试锁定矛盾 summary 必须显示“候选模型”。

## Run23 / Run40 事实边界

- Run23：真实 DSH transport completed、模型状态 candidate；安全工具投影显示 `cad_finish_model` 已被调用并完成，但 visual 6 个原因码、finish 7 个原因码失败，其中包括 open/unresolved findings。原图 wire/current geometry/holes 通过不等于视觉复核或 finish 接受。此次修复没有把 Run23 重新解释成通过。
- Run40：真实 DSH transport completed、model candidate、runner exit 1；13 个 strict gate 中 10 通过、3 失败，分别包含 holes、visual、finish。独立预览比较记录中间凹槽壁三孔缺失。该运行也没有接受模型，且不能证明本次发现的矛盾 Host 枚举曾在历史运行中出现。

## 验证

- `node --test src/apps/desktop/src/agentStatus.test.mjs`：9 passed，0 failed。覆盖回合 completed 与 candidate 分离、有效 accepted、revision/fingerprint 过期、缺失 Host confirmation，以及新增的 summary accepted/finish_state 矛盾反例。
- `pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter agent_host::tests::same_revision_visual_reference_findings_and_facts_changes_expire_finish_binding -Exact -NoCapture`：1 passed。使用独立临时 `CARGO_TARGET_DIR` 完成首次编译，不占用 worktree Cargo target lock。
- `git diff --check`：通过。
- `App.agentEvents.integration.test.mjs` 未能启动：当前 worktree 未安装 `@vue/compiler-sfc`。未安装依赖，也未启动可见 GUI、DSH/provider 或 CATIA。

## 未证明事项

该改动验证了 Vue 对不一致投影的保守呈现，不证明生产 Host 会生成该矛盾状态（Host 生产逻辑本来已把 `accepted` 与 `finish_state` 同时绑定）。未运行真实 DSH/provider、隐藏或可见 WebView 集成、真实视觉验收及 CATIA 导出。Run23/Run40 仍是候选且 finish 未通过的历史运行。
