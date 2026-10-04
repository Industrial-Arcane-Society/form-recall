# F7 原生 Cordis CAD 工具契约修复

## 范围

本记录覆盖 DSH Cordis schema、原生插件参数检查、Rust 工具派发和 CAD session 前置条件之间的静态契约核查。代码基线为 `1bf326daa89522f0e5c4c05b012433c37c6a9322`，本 feature 未发出 provider 请求，也未使用真实 Run18 参数。

Run18 可用的事件摘要仅表明 `cad_create_model`、`cad_inspect_references`、`cad_remove_feature`、`cad_restore_partial_candidate` 各出现一次 `bridge_execution_rejected`，以及 `cad_update_feature` 出现一次 ACP→bridge missing。具体 arguments 没有提供，且本核查没有读取 Run18/Run17 临时目录、summary、tool diagnostics、日志或 raw trace。因此下文是源码契约结论，**不能据此认定这些静态问题就是 Run18 对应调用的根因**。

## 核查结果与修复

- **Cordis 动态对象 schema**：`dsh_native_tools.mjs` 的 `authorSchema` 把 JSON Schema 的 `additionalProperties` 约束转成 Cordis 接受的布尔值；嵌套对象形状不能直接沿用原 JSON Schema 子 schema。之前未显式开放动态键的参数 map 存在被收窄为空对象的契约风险。本次给 `cad_update_feature.parameter_updates` / `source_updates` 和特征 `sources` 明确声明动态对象，并在原生工具执行前检查来源对象的已知类型、字段和值类型。非空合法 map 可通过；畸形来源和未声明字段在 bridge 之前失败关闭。
- **`cad_create_model` / `cad_add_feature`**：`units` 现在是长度、角度单位的有限枚举，提供 `units` 时两项均必需且禁止额外键；首个特征必须显式包含 `sources`；`relations` 项必须包含受支持的 `type` 与非空 `feature_id`，不允许额外字段。Rust 还检查单位、关系、来源形状，并通过 `cad_plan_validation::validate_feature_parameter_values` 复用 CADPlan 的参数名、类型和数值范围校验。
- **`cad_update_feature`**：动态参数/来源 map 现在能表达并接受非空映射。Rust 根据目标特征 kind 调用同一 CADPlan 参数 validator，而不是在工具执行器复制一套参数范围；移除参数检查允许键，来源更新必须对应现有或本次更新的参数；metadata 的 `fields` 只允许字符串 `name` / `note`。公开 schema 曾包含、executor 却拒绝的 `provenance_corrections` 已从工具合同中移除；来源更正通过 `source_updates` 及可信 turn 绑定提交。
- **`cad_remove_feature`**：`finding_ids` 是可选数组，Rust 缺省解码为空数组；普通删除无需虚构 finding。session 层仍禁止删除唯一 add 主体，并对回到已见几何签名的删除应用 finding 守卫。新增回归覆盖普通删除、无 finding 的历史几何回退拒绝、有效 finding 修复三个路径。
- **`cad_inspect_references`**：schema 暴露 `reference_id`、从 1 开始的 `reference_image_index`、归一化 ROI 与 `refresh_reason`；executor 将其路由到 Rust vision domain，domain 仅接受这四个键、校验刷新原因和索引，并由 reference manifest 解析原图。静态检查没有找到 schema 与 handler 的确定性不一致；缺少引用选择或坏 ROI 的具体调用能否被拒绝，取决于实际传入值，本审查没有 Run18 参数可核对。
- **`cad_restore_partial_candidate`**：此前对 Cordis 发布但原生 executor 的 `supports`/dispatch 未接通，属于公开死工具。现从 public native schemas 移除；工具数相应由 25 改为 24，executor 明确不支持该工具，合同测试防止重新发布。session 内部恢复实现保留给域逻辑，不等于对 Agent 暴露可用工具。
- **工具身份与参数派发**：Cordis registration 使用 manifest 中的 `tool.name`；执行时同一名称和参数传给 `client.callTool`，Rust executor 再通过对应 name 的支持检查和 dispatch 分流。新增 pinned DSH fixture 从 Rust schema manifest 注册工具，验证非空 create/update map 穿过实际同版本 Cordis `defineTool` 并到达本地测试 bridge。

## 验证入口与结果

- 同版本 Cordis schema/`defineTool` fixture：`src/legacy/tests/test_dsh_native_tools_rpc.mjs`，锁定 `@deepseek-ai/dsh` 和 `@deepseek-ai/dsh-tools` `0.2.0-rc.2`。临时按 lockfile 安装依赖后 7/7 通过；测试使用本地 fake bridge，无 provider 请求。验证结束后临时 `node_modules` 已删除，package 与 lockfile 未改。
- 清理临时依赖后重跑 Node 文件：6 项通过、pinned DSH fixture 1 项按预期跳过；上面的 7/7 是安装锁定版本时实际运行 `defineTool` 的结果。
- Rust executor 合同：`src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Filter 'native_cad_tools::tests::'`，7/7 通过；覆盖 `create_and_update_dynamic_maps_use_the_authoritative_cadplan_parameter_validator`、`create_rejects_invalid_units_relations_sources_and_parameter_types`、`partial_restore_is_not_a_public_native_executor_tool`。
- session 删除守卫：同一 wrapper 使用完整唯一测试名作为过滤器，`native_cad_session::tests::remove_without_findings_is_allowed_until_it_reverts_to_a_seen_geometry_signature`，1/1 通过。
- Schema 合同：同一 wrapper 使用完整唯一测试名作为过滤器，`native_tool_schemas::tests::native_mutation_schemas_match_executor_contracts`，1/1 通过。
- 扩展 `native_cad_` 过滤为 29 项通过、4 项失败。已在干净 detached HEAD `1bf326d` 的 `native_cad_session::tests::` 过滤上重跑，18 项通过、相同 4 项失败；失败均存在于基线：
  - `native_cad_session::tests::complete_visual_coverage_cannot_override_an_open_finding_at_finish`
  - `native_cad_session::tests::image_task_with_high_per_view_coverage_still_needs_current_aggregate_acceptance`
  - `native_cad_session::tests::current_overall_geometry_acceptance_unlocks_image_finish`
  - `native_cad_session::tests::finish_reference_coverage_failure_remains_unaccepted`

这些基线失败没有被本次改动引入；它们也不证明或解释 Run18 的具体拒绝。完整的 provider 冷启动仍需由主任务在隔离环境安排。

## 代码定位

- Cordis schema 编译、来源校验、工具名派发：[dsh_native_tools.mjs](../../src/legacy/ai_modeler/agent/dsh_native_tools.mjs)
- 原生 schema manifest 与 schema 合同：[native_tool_schemas.rs](../../src/apps/desktop/src-tauri/src/native_tool_schemas.rs)
- 原生 CAD dispatcher、单位/来源/关系/增量更新验证：[native_cad_tools.rs](../../src/apps/desktop/src-tauri/src/native_cad_tools.rs)
- CADPlan 共享参数 validator：[cad_plan_validation.rs](../../src/apps/desktop/src-tauri/src/cad_plan_validation.rs)
- vision dispatch 与引用观察检查：[native_tool_executor.rs](../../src/apps/desktop/src-tauri/src/native_tool_executor.rs)、[native_vision_domain.rs](../../src/apps/desktop/src-tauri/src/native_vision_domain.rs)
- 删除守卫回归：[native_cad_session.rs](../../src/apps/desktop/src-tauri/src/native_cad_session.rs)
- pinned Cordis fixture：[test_dsh_native_tools_rpc.mjs](../../src/legacy/tests/test_dsh_native_tools_rpc.mjs)

## 集成复核补充（2026-10-04）

集成分支全量 Rust 单测第一次运行发现，G9 将 `sources`、`parameter_updates`、`source_updates` 与 `fields` 的模型说明改成较短的动态 map 描述时，丢失了既有提示契约中的语义：来源只对应数值参数、不能附到特征元数据、数值更新必须提供来源、`fields` 仅编辑名称/备注。既有 `run07_provenance_hole_group_update_recess_and_finish_contracts_are_explicit` 因此失败。代码中的 executor / CADPlan validator 仍有结构校验，但不足以替代明确的模型提示。

本次 follow-up 在保留动态 map 和 native validator 说明的同时恢复以上提示，并明确 `image_estimate` 必须有 evidence、`existing_model` 必须有 note，以及 Host 对 `user.turn_id` 的可信绑定。定向测试 `native_tool_schemas::tests::run07_provenance_hole_group_update_recess_and_finish_contracts_are_explicit` 在修复后 **1/1 通过**。这只证明 schema/prompt 文本契约，真实 DSH 对提示的遵循仍须 Run20+ 冷启动验收。
