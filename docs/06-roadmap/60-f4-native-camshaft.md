# F4.2.10：CADPlan camshaft 原生几何执行

验收日期：2026-10-04。

## 实现范围

`camshaft` 现经 Rust CADPlan preflight 与 build dispatch 调用 native parametric builder，再由现有 Rust OCCT wrapper 经 C ABI 构造、融合并检查 B-Rep。没有增加 C ABI 符号或改变 Python Document/CATIA 路径。

- 全局 X 轴轴颈使用解析圆柱，轴长为 `length`，直径为 `journal_d`。
- 凸轮沿轴向等距放置，中心位置和相位公式沿用 `Document`；每枚凸轮使用 48 点、120° 简谐升程/保持/回程轮廓，按 X 轴正方向拉伸。
- `length/journal_d/base_d/lift/center` 按 CADPlan 长度单位转换为毫米；`lobe_deg` 按角度单位转换为度。输入 CADPlan 和来源信息不被原地修改。
- 要求 `length >= 2 mm`、`journal_d >= 2 mm`、`base_d >= 2 mm`、`lift >= 0`、整数 `lobes` 在 `1..=512`、必填有限 `lobe_deg` 与有限 XYZ 中心。凸轮宽度超过总长或融合后不是单一有效闭合实体时拒绝。
- Rust 本地几何能力标为 exact；`cad_get_feature_spec` 继续单独标示 CATIA 映射 approximate。此项不声称真实 CATIA 几何或视觉验收。

## 几何验收

执行 `examples/native_geometry_parity.py`：总计 69 个正例、23 个负例均通过。camshaft 新增 3 个实际 OCCT 正例（原点、平移并改变相位、cm/rad 缩放）和 3 个 CADPlan 负例（长度过小、轴颈直径为零、凸轮数量超过上限）。所有 3 个正例均为单个有效闭合 B-Rep；Rust 与旧 Python OCP 的包围盒最大误差为 **0 mm**。Rust 端单位换算单测确认 cm 与 rad 的参数转换，源计划保持原值。

旧 Python `Document` 构造器生成三角 facet 几何再转 B-Rep，适合验证旧包围盒与闭合状态，但其体积不是解析几何精度基准。对三组 camshaft 实测旧体积 `25227.0058005823 mm³`、原生体积 `25272.7838644026 mm³`，差值 **45.7780638202 mm³**，相对旧体积 **0.1814645%**；三例差值一致。因此体积在报告中保留为 faceted-vs-analytic 观测值，不以 `1e-5 mm³` 容差判定通过。解析原生 B-Rep 自身仍需满足 `analyze()` 单闭合实体检查。该误差不应用于声称旧/新体积精确相等。

几何对拍 OCCT 版本：Rust **7.9.3**，Python OCP **7.9.3.1.1**。

## Rust 测试结果

- `scripts/test-lib.ps1 -Filter camshaft -Offline`：3/3 通过，覆盖 builder preflight、能力规格和 cm/rad 接线。
- `scripts/test-lib.ps1 -Filter native_feature_specs::tests -Offline`：3/3 通过，包括 27 类规格与实际原生 registry 数量一致。
- `scripts/test-lib.ps1 -Offline`，并设置 `AIMODELER_TEST_NODE` 与 `AIMODELER_TEST_ACP_FIXTURE`：260/261 通过。唯一失败是既有 piston 规格测试断言 `status == "implemented"`，而现行契约返回 `"implemented_exact"`；本 feature 未修改 piston 实现或测试。设置这两个变量后，原本缺少 Node 的 Agent IPC 测试通过。

示例构建：

```powershell
$env:TAURI_CONFIG='{"bundle":{"resources":[]}}'
cargo build --manifest-path src/apps/desktop/src-tauri/Cargo.toml --example native_plan_geometry_probe --offline
python src/apps/desktop/src-tauri/examples/native_geometry_parity.py
```

完整机器报告位于忽略目录 `src/apps/desktop/src-tauri/target/f4-occt/plan-parity/summary.json`，不提交。
