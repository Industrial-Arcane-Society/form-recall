# F7.G4 消费端施工 scope 绑定

## 修复范围

`native_vision_domain::review` 只让以下 evidence row 产生 coverage、exact match 或 approximate match 支持：evidence ID 唯一、mapping 为 `mapped`、状态不是 `out_of_scope`、宿主归属为 `verified_ids`，且 affected feature IDs 非空并完全落在当前 verified stage 内。provider 对空 affected IDs 或 scope 外 ID 的 match 声明保留为诊断信息，不能提升 coverage。

summary 消费端重新验证 milestone 和 review 的 revision 一致，并要求 review stage feature ID 集合精确等于当前 completed feature IDs；completed/expected ID 必须严格、唯一，completed 必须属于当前 enabled feature 集。summary 重新计算 stage complete、full current geometry in scope 与 diagnostic-only 标志并与历史 review 核对。缺 milestone、malformed/duplicate IDs、同 revision 改小或改大 scope、历史 flags 不一致都会使旧 review 不再 comparable。`visual_summary` 还会再次限制支持行必须具有 verified attribution 和非空、完全处于 review scope 的 affected IDs。

## 里程碑输入契约补强

在上述消费端绑定之外，工具入口曾把缺失的 scope 数组当成空数组，因此 JSON Schema 中仅要求 `milestone` 时，Agent 可以登记一个没有施工范围的 milestone。此次 P0 修复只收紧该入口契约：嵌入 schema 将 `completed_feature_ids`、`expected_feature_ids` 设为必填；Rust handler 也独立拒绝缺失或非数组参数，并在已有计划时拒绝空 `completed_feature_ids`。非空 partial scope 仍允许 `expected_feature_ids` 包含后续特征，full scope 也允许。

该修复只约束 milestone 的输入形状和空 completed scope，不改变施工范围归属、视觉 review、finish 门禁，也不证明此前 Run32 或 `27d4657` current-source 复测中的拒绝就是由缺少数组导致。现有 safe projection 不包含被拒绝调用的原始参数，不能归因 Run32/Run31/后续复测根因。此分支的定向回归只证明本地 Rust handler 和 schema 契约；不代表 G8 冷启动、真实 DSH/provider、多模态审查或视觉验收通过。

## 回归覆盖与命令

- `native_vision_domain::tests::provider_match_attributed_outside_verified_stage_is_diagnostic_only` 使用 mocked provider 走生产 `review → normalize → reconcile → history` 路径，覆盖 scope 外 ID、空 affected ID、重复 coverage ID 与含非字符串的 malformed coverage array。检查覆盖为 partial、covered IDs 为空；scope 外/空 affected row 被标为 unattributed，重复/畸形 coverage claim 即使配合有效 attribution 也不能提升 coverage。
- `native_state_summary::tests::same_revision_milestone_changes_invalidate_historical_visual_coverage` 覆盖同 revision milestone 缩小、expected 扩大、completed scope 真正扩至新增 enabled feature，以及历史 stage feature IDs 重复时不再 comparable/covered。
- `native_cad_session::tests::same_revision_milestone_shrink_invalidates_review_before_finish_acceptance` 和新增的扩展分支先建立 accepted baseline，再同 revision 改 milestone 并重算 finish，确认 accepted 变为 false 且为 candidate。
- `native_cad_metadata::tests::milestone_requires_both_scope_arrays_and_rejects_empty_completed_scope` 覆盖任一字段缺失以及已有 CADPlan 下空 completed scope；`milestone_accepts_nonempty_partial_and_full_scopes` 覆盖非空 partial/full scope；`native_tool_schemas::tests::construction_milestone_schema_requires_explicit_scope_arrays` 锁定 embedded schema 的 required 字段。

当前验证：`native_vision_domain::tests` 23/23、`native_state_summary::tests` 8/8、`native_review_findings::tests` 11/11、`native_cad_session::tests::finish_` 4/4、两个 `same_revision_milestone` 集成回归 2/2。每组使用仓库 `scripts/test-lib.ps1 -Offline` wrapper，设置了所需 `AIMODELER_TEST_NODE` 与 `AIMODELER_TEST_ACP_FIXTURE`。没有运行全套件；完整套件先前存在并发/环境失败迹象，不将其归因于本次修复。

本分支新增入口契约定向验证：`pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_cad_metadata::tests::milestone_` 为 4 passed / 0 failed；`pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_tool_schemas::tests::construction_milestone_schema_requires_explicit_scope_arrays -Exact` 为 1 passed / 0 failed。两项均为本地离线 Rust CLI 单元测试，不调用 provider 或 DSH。

## 未解决的 partial-preview 路径

`review` 仍可识别 partial preview 并设置 partial-candidate diagnostic，但随后 `verified_stage_scope` 要求当前 revision 有成功完整构建和 host geometry attestation。由此 partial review 在进入 provider 前被拒绝；现有生产路径契约与 partial diagnostic 分支不一致。本次 P0 保留此路径，避免把 partial review 提升为 coverage 或改变 finding 生命周期。应在单独变更中确定 partial review 的诊断输入契约，并证明其零 coverage、不会创建/关闭 finding 后再修复。

## 验收边界

mock provider 与本地 Rust 回归只证明数据归属、scope invalidation、summary projection 和 host finish 的代码行为。它们不证明真实 provider 对原图或 CAD 预览的视觉理解、真实 DSH 会话行为或 CATIA 导出结果。本次不启动 DSH/provider/CATIA，也不打开 GUI。
