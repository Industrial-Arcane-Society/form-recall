# F3.1：Rust CADPlan 增量事务

更新时间：2026-10-02。该子项是 Rust CAD domain 的纯计划编辑组件；完整 F3 还需要静态校验、会话版本管理与几何执行接口。

## 实施边界

- `cad_plan.rs` 提供 create、add、update、remove、snapshot、geometry_fingerprint，按现有 `plan_state.py` 的事务语义移植。
- 编辑返回计划副本。失败时原计划不变；保留来源、关系以及扩展字段。改变数值参数必须同时更新其来源，删除参数同步删除对应来源。
- 数值等价按 JSON 中的 Python 比较语义处理；整数 10 与浮点 10.0 不构成新的尺寸变化。
- 几何指纹按 feature 的 id、kind、operation、parameters、relations 五字段构造，保留特征/关系数组顺序，忽略名称和来源。缺失字段按 null 投影。
- 启用 `serde_json/float_roundtrip`：扩展对拍发现默认解析会使部分输入小数偏移，从而改变几何指纹。该选项保留 JSON 浮点数往返精度。
- `examples/cad_plan_probe.rs` 是使用同一 Rust 组件的 JSONL CLI 对拍入口，不执行几何或 CATIA。

## 验收证据

- 组件测试覆盖来源保留、不可变编辑、数值来源要求、失败回滚、删参/来源同步、缺失 ID、未知字段和指纹的 Unicode/浮点/关系顺序行为。
- 主 Agent 用固定随机种子 937 生成 128 组小数，加上 12 组指数、极值、负零等边界，对 Python `geometry_fingerprint` 与 Rust JSONL probe 作 140 组对拍。首次 120/140 相同；启用精确往返解析后 140/140 相同。
- 将实际发现的小数解析失败样本固化为模块回归测试；仅测试原来的静态 golden 会漏掉这个问题。

## 完整 F3 的剩余项

计划编辑组件本身不执行完整参数校验、几何构建或预览。F3.2 完整校验、F3.3 会话事务与几何 dispatch 以及 F2.5 原生写工具接线尚待完成。此提交不证明 Agent 建模、多模态复核或 CATIA 的质量通过。
