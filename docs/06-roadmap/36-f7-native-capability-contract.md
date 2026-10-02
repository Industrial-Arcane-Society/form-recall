# F7.G6：统一原生几何能力契约

## 范围

让 Agent 能查到每类 CADPlan 特征在当前 Rust/OCCT 建模器中的真实支持范围，区分本地候选几何与 CATIA 映射，不再从通用 schema 推断后端实现能力。

## 已完成

- `native_plan_geometry::supports_native_kind` 成为原生建模能力清单，preflight 和 feature spec 共用它。
- `cad_get_feature_spec` 对全部 26 个已注册 kind 返回本地执行状态、几何精度类别、支持与拒绝的变体、参数边界和说明；CATIA 的精确/近似映射继续独立描述。
- `cad_create_model`、`cad_add_feature`、工具描述和系统提示要求 Agent 在构建前读取并遵守 `native_execution`。
- 面工具 `source_type` schema 与 executor 允许值统一。hole-array schema 上限与 native executor 的 4096 项资源上限一致。

## 验证与边界

Subagent 已执行并报告以下定向测试通过：

- `native_capability_contract_matches_registry_preflight_and_variant_shape`
- `face_tool_schema_matches_source_type_and_hole_array_executor_limits`
- `coordinate_and_mapping_contracts_are_present_for_every_registered_kind`

`parameter_limits` 目前是人工维护的说明型契约，不是自动生成的参数 JSON Schema；它帮助模型选择正确的变体，但不能替代执行器校验。G8 仍需用真实 DSH 请求核实模型是否按契约建模。当前没有声称完整 Rust 测试套件或真实模型验证已通过。
