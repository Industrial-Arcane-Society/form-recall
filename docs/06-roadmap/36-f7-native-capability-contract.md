# F7.G6：统一原生几何能力契约

## 范围

让 Agent 能查到每类 CADPlan 特征在当前 Rust/OCCT 建模器中的真实支持范围，区分本地候选几何与 CATIA 映射，不再从通用 schema 推断后端实现能力。

## 已完成

- `native_plan_geometry::supports_native_kind` 成为原生建模能力清单，preflight 和 feature spec 共用它。
- `cad_get_feature_spec` 对全部 27 个已注册 kind 返回本地执行状态、几何精度类别、支持与拒绝的变体、参数边界和说明；CATIA 的精确/近似映射继续独立描述。2026-10-04 F4.2.19 loft 接入后，`supports_native_kind` 执行其中 25 类，`impeller` 与 `shell` 明确返回 unsupported。
- `cad_create_model`、`cad_add_feature`、工具描述和系统提示要求 Agent 在构建前读取并遵守 `native_execution`。
- 面工具 `source_type` schema 与 executor 允许值统一。hole-array schema 上限与 native executor 的 4096 项资源上限一致。

## 验证与边界

Subagent 已执行并报告以下定向测试通过：

- `native_capability_contract_matches_registry_preflight_and_variant_shape`
- `face_tool_schema_matches_source_type_and_hole_array_executor_limits`
- `coordinate_and_mapping_contracts_are_present_for_every_registered_kind`

`parameter_limits` 目前是人工维护的说明型契约，不是自动生成的参数 JSON Schema；它帮助模型选择正确的变体，但不能替代执行器校验。G8 仍需用真实 DSH 请求核实模型是否按契约建模。当前没有声称完整 Rust 测试套件或真实模型验证已通过。

2026-10-04 F4.2.19 loft 接入后，Rust feature specs 与 CADPlan 仍各注册 27 类；原生执行为 25 类，未接入执行器的 2 类为 `impeller`、`shell`。`shell` 的 CADPlan 只带 `thickness`，没有要移除面的稳定选择，也没有抽壳方向约定；legacy polygon `Feature.build` 不产生 shell 几何，legacy OCP executor 以“CADPlan 未指定要移除的面”拒绝。F4.2.20 因而只提交阻塞说明，不登记实现或构造器。所需 schema 与拓扑边界见 [F4.2.20 shell 阻塞记录](./68-f4-native-shell-blocker.md)。`box/pocket corner != 0` 和显式盲孔 `entry_point` 仍会在 OCCT 前被明确拒绝。Run15 包含 scroll_disc 的 B-Rep 对拍结果记于 [F4.2.16 验收记录](./64-f4-native-scroll-disc.md)。能力计数不代表完整变体覆盖或 Agent 遵循已验收。
