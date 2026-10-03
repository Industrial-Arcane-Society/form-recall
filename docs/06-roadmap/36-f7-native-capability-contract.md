# F7.G6：统一原生几何能力契约

## 范围

让 Agent 能查到每类 CADPlan 特征在当前 Rust/OCCT 建模器中的真实支持范围，区分本地候选几何与 CATIA 映射，不再从通用 schema 推断后端实现能力。

## 已完成

- `native_plan_geometry::supports_native_kind` 成为原生建模能力清单，preflight 和 feature spec 共用它。
- `cad_get_feature_spec` 对全部 27 个已注册 kind 返回本地执行状态、几何精度类别、支持与拒绝的变体、参数边界和说明；CATIA 的精确/近似映射继续独立描述。当前 `supports_native_kind` 执行其中 21 类，另外 6 类有参数契约但明确返回 unsupported。
- `cad_create_model`、`cad_add_feature`、工具描述和系统提示要求 Agent 在构建前读取并遵守 `native_execution`。
- 面工具 `source_type` schema 与 executor 允许值统一。hole-array schema 上限与 native executor 的 4096 项资源上限一致。

## 验证与边界

Subagent 已执行并报告以下定向测试通过：

- `native_capability_contract_matches_registry_preflight_and_variant_shape`
- `face_tool_schema_matches_source_type_and_hole_array_executor_limits`
- `coordinate_and_mapping_contracts_are_present_for_every_registered_kind`

`parameter_limits` 目前是人工维护的说明型契约，不是自动生成的参数 JSON Schema；它帮助模型选择正确的变体，但不能替代执行器校验。G8 仍需用真实 DSH 请求核实模型是否按契约建模。当前没有声称完整 Rust 测试套件或真实模型验证已通过。

2026-10-04 F4.2.14 timing pulley 与 F4.2.15 V-belt pulley 后，Rust feature specs 与 CADPlan 仍各注册 27 类；原生执行为 21 类，未接入执行器的 6 类为 `loft`、`helix`、`worm`、`impeller`、`scroll_disc`、`shell`。`box/pocket corner != 0` 和显式盲孔 `entry_point` 仍会在 OCCT 前被明确拒绝。集成分支 `native_vbelt_pulley` 模块测试 3/3、capability registry 测试 6/6 通过；完整集成 OCP/Rust 对拍 **86 正例 / 47 负例**，其中 V-belt 5 正例、timing pulley 3 正例，bbox 容差 `1e-6 mm`、volume 容差 `1e-5 mm³`。能力计数不代表完整变体覆盖或 Agent 遵循已验收。
