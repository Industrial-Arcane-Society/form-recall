# F7.G4 施工后 partial evidence 修订绑定回归

## 范围与结论

本次从 `origin/main`（`20c2ebece1983d54b4ba43fed686837d7ee0359c`）只读检查 partial producer、preview rows、native review aggregation 与 finish gate。当前生产路径已有显式连续 `built` 前缀、host attestation、revision/plan fingerprint 核对；没有找到可稳定复现的“未建特征被当作已施工”生产缺陷。

为锁定 producer 输出在施工后不能跨 revision 继续支持复核或 finish，本次扩展已有本地 OCCT 集成 fixture：先由 `cad_preview_model` 生成真实本机 partial build 与七视图，再把同一状态的几何 revision 推进一版，同时保留旧 partial report / preview rows。期望 aggregate 将 geometry 证据标为 `binding_valid=false`，finish 拒绝该旧 revision 的 partial preview。

## 修改与测试边界

- 在 `native_cad_tools::tests::cad_preview_model_publishes_only_host_attested_partial_multiview_diagnostics` 增加 stale-revision 分支。它复用该 fixture 原有的生产工具入口、OCCT preview 和 finish 路径，而非另造静态对象。
- 当前环境没有配置 `AIMODELER_OCCT_MANIFEST`。该 fixture 在检查 manifest 后会提前 `return`；故本次未执行 OCCT 构建、七视图或新增集成断言，也不把这种返回记作集成验收。
- `native_cad_session::tests::partial_preview_is_revision_bound_and_never_accepted` 已实际运行，1/1 通过。新增集成代码随该测试 crate 编译成功。
- 未调用 GUI、真实 DSH/provider 或 CATIA；没有视觉验证结论。

## 复现命令

在具备 OCCT runtime manifest 的 Windows 环境设置 `AIMODELER_OCCT_MANIFEST` 后执行：

```powershell
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter cad_preview_model_publishes_only_host_attested_partial_multiview_diagnostics
```

离线 revision-bound 单测：

```powershell
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_cad_session::tests::partial_preview_is_revision_bound_and_never_accepted
```

## 2026-10-06 main 实际 OCCT fixture 复跑

- 本报告代码随 commit `2612963` 集成到 main。主工作区提供了已校验的 OCCT runtime manifest：`src/apps/desktop/src-tauri/target/bundle-audit-20261004/dsh-install-harness/target/debug/occt-runtime/runtime-manifest.json`。
- 设置 `AIMODELER_OCCT_MANIFEST` 指向该 manifest 后，使用仓库 Windows runner 执行 `cad_preview_model_publishes_only_host_attested_partial_multiview_diagnostics`，实际结果 1 passed / 0 failed / 0 ignored，耗时 1.73 s；该次运行执行了 partial producer、真实本地 OCCT 预览和新增 stale-revision finish 拒绝断言。
- 未调用真实 DSH/provider、CATIA 或可见 GUI；此项仅关闭施工范围与 revision binding 的本地几何集成证据，不代表视觉质量通过。
