# F4.2.8：十字轴 cross_joint 原生执行

Rust CADPlan backend 新增 `cross_joint` preflight 与 OCCT 构造：以 `span` 和 `journal_d` 描述几何，在指定 `center` 构造中心方块和沿全局 ±X、±Y 的四个圆柱轴颈，并直接融合接触面。缺少必需尺寸、非正尺寸或无效中心会拒绝构造，不注入默认尺寸或 epsilon。参数中的 `segments` 只涉及旧网格离散，在解析圆柱实现中不参与尺寸。

native 能力标记为 `approximate`：形体结构可编辑且 B-Rep 为解析几何，但它与旧版 24 段多边形圆柱并非相同的离散表面。该差异不应被记作精确体积一致。

## 验证

- `python examples/native_geometry_parity.py`：66 个正例、20 个负例全部通过。
- `cargo test --manifest-path Cargo.toml --lib --offline cross_joint`：1/1 通过；另一个 feature-spec 契约测试 1/1 通过。
- 3 组 Rust→OCCT 实体均为有效单一闭合体；X/Y 轴颈以及 Z 向中心块材料区间采样符合预期。
- native 与 legacy 包围盒误差不超过 `8.88e-16 mm`。legacy 24 段 facet 体积为 `7785.885932 mm³`，native 解析体积为 `7860.519345 mm³`，差 `74.633413 mm³`（约 `0.95857%`）；仅作差异记录，不作为通过或失败门槛。
- `git diff --check` 通过。

这是 geometry fixture 和原生 OCCT 的能力验证，不代表 Agent 识图、CATIA 导出或制造级几何已验收。
