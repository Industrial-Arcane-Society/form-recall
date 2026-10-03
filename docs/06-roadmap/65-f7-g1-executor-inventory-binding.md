# F7.G1：executor 视觉证据库存绑定负例

日期：2026-10-04
范围：Rust NativeToolExecutor headless 集成测试。不启动 DSH、外部视觉 provider、桌面 GUI、WebView 或 CATIA。

## 覆盖

native_executor_rejects_unbound_or_invalid_inventory_before_review_provider 将畸形/过期 source inventory 送入生产 cad_review_reference_similarity executor 路由，覆盖四种输入：

- inventory 的 source SHA 与当前原图不一致；
- 只有 ROI inventory，但本次请求要求完整原图；
- inventory 没有可见证据；
- inventory 中重复使用同一个 evidence ID。

每种情况均要求 executor 在复核记录写入前返回 inventory 绑定错误。fixture 将 provider 指向未监听的 loopback 端口；如果请求错误地进入 provider，会得到不同的网络错误并使断言失败。

## 验证

    pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_tool_executor::tests::native_executor_rejects_unbound_or_invalid_inventory_before_review_provider -Exact

结果：1 passed, 0 failed, 296 filtered out。

随后在设置 AIMODELER_TEST_NODE 与 AIMODELER_TEST_ACP_FIXTURE 后运行完整 Rust 单测：297 passed, 0 failed。

## 验收边界

此测试覆盖 executor 对 source inventory 的 fail-closed 绑定，不证明真实模型能正确读图，也不验证 provider/ACP/DSH 的线上或进程链路。真实图像理解和视觉返修仍待允许执行的严格冷启动；不得用本地 fixture 代替该验收。

## 2026-10-04 executor 状态闭环扩展

在初始四类 inventory 拒绝用例之外，新增 executor→cad_get_state 集成覆盖：

- 原图 reobserve 从 observation v1 升级到 v2 后，旧 review 不再贡献 current/comparable coverage。
- construction milestone 未完成时，provider 即便报告 verified/match，stage 结果保持 diagnostic-only，covered evidence 不晋升。
- mismatch 与 unknown covered evidence claim 均保持未覆盖；其中 unsupported_coverage_ids 必须传递到 cad_get_state 汇总。

unsupported case 暴露了生产缺口：NativeToolExecutor 已计算并保存 host 派生的 unsupported_coverage_ids，但 native_state_summary 未把该字段并入 reference row，可能只因一个额外的未知 claim 被过滤便错误显示全图 covered。现已在 Rust 状态汇总中保留 host 计算的 unsupported IDs，并以 executor 集成回归确认 coverage=false。

验证：设置 AIMODELER_TEST_NODE 与 AIMODELER_TEST_ACP_FIXTURE 后，通过 scripts/test-lib.ps1 -Offline 运行全量 Rust suite，302 passed、0 failed。测试使用本地 mock HTTP provider 和已由 complete_preview 认证的 host fixture；不证明真实 DSH/provider 视觉质量、原图理解或 CATIA 结果。
