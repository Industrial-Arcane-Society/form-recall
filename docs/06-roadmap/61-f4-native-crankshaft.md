# F4.2.11：CADPlan crankshaft 原生几何执行

验收日期：2026-10-04。

## 实现范围与几何口径

`crankshaft` 通过 Rust CADPlan preflight/build dispatch 调用 native builder，复用现有 Rust → C ABI → OCCT 原语，无需新增 C ABI 符号。

- 沿全局 X 轴构建解析主轴，轴长 `length`、半径 `main_d / 2`。
- 每个 throw 在 `length / throws` 的中点放置一段偏心连杆颈，长度为 throw 间距的 `0.42`；偏置沿全局 Y 轴按 throw 索引交替为 `+stroke/2`、`-stroke/2`。
- 每个 throw 两侧各构建一块与旧 CADPlan B-Rep 路径相同的矩形曲柄臂。宽度为 `min(max(2 mm, main_d * 0.28), spacing * 0.25)`；矩形跨越主轴颈与偏心连杆颈的外包络，Z 向延展至两者较大半径。
- 接受并保留 `segments` CADPlan 参数，但旧 CADPlan OCP B-Rep builder 不使用它，原生解析构造也忽略它。
- 长度参数 `length/main_d/pin_d/stroke` 与中心点按 CADPlan 长度单位转换为 mm。要求四个尺寸为正、throws 为整数 `1..=128`，center 为有限 XYZ。超限 throw 数和非正尺寸在调用 OCCT 前拒绝。
- 该几何仍是曲轴的近似表示：曲柄臂采用矩形 web。native feature 状态与 CATIA 映射均公开为 `approximate`。

## 实际 B-Rep 与 Python OCP 对拍

运行 `examples/native_geometry_parity.py`，全套当前结果为 **72 个正例、26 个负例通过**。新增三个正例：B30 四拐原点样例、偏置中心三拐样例、同一 B30 尺寸的 cm 单位样例；新增三个负例：主轴直径为零、负 stroke、throws 超过 128。三例均实际生成单个有效闭合 B-Rep。

Rust OCCT 7.9.3 与 Python OCP 7.9.3.1.1 比较旧 CADPlan B-Rep builder 的包围盒和体积，容差分别为 `1e-6 mm` 与 `1e-5 mm³`：

| 样例 | 包围盒最大误差 | Python OCP 体积 | Rust 原生体积 | 绝对体积误差 |
|---|---:|---:|---:|---:|
| B30 四拐原点 | 0 mm | 1,088,606.95856308 mm³ | 1,088,606.95856308 mm³ | 2.33e-10 mm³ |
| 偏置中心三拐 | 0 mm | 292,308.466222116 mm³ | 292,308.466222116 mm³ | 5.82e-11 mm³ |
| B30 cm 单位 | 0 mm | 1,088,606.95856308 mm³ | 1,088,606.95856308 mm³ | 2.33e-10 mm³ |

三例对拍均在容差内通过，cm 样例归一化后与原点 B30 体积相同。这里的“精确通过”仅指与旧 CADPlan Python OCP 解析 B-Rep 构造器数值对拍；曲柄臂模型本身仍按 capability spec 标 approximate。

## Document polygon mesh 路径的独立结果

旧 `Document.build()` 的兼容 polygon recipe 生成的是更细致的圆弧外公切线曲柄臂 facet 网格，与 CADPlan `Document.build_brep()`/`occ_kernel` 路径的矩形 web 语义不同。对 B30 四拐样例，`Document.build()` 生成 1,074 个 polygon（1,692 个三角形）；调用 `occ_kernel._polygon_mesh_to_brep` 缝合时，在 17 个离散部件的首个 shell 遇到 32 条自由边并失败。因此这条 mesh-only 输出不能形成可靠可比的闭合 B-Rep，本项没有报告其体积误差，也不把它与上面的 Python OCP 解析构造器混作同一基线。

## Rust 测试

- `scripts/test-lib.ps1 -Filter crankshaft -Offline`：3/3 通过，覆盖 preflight 上限、长度单位换算、capability/CATIA 映射状态。
- `scripts/test-lib.ps1 -Filter native_feature_specs::tests -Offline`：4/4 通过，能力 registry 与 27 类特征数对齐。
- 完整 `scripts/test-lib.ps1 -Offline`，设置 `AIMODELER_TEST_NODE` 与 `AIMODELER_TEST_ACP_FIXTURE`：264/265 通过。唯一失败是既有 piston spec 测试断言 `status == "implemented"`，而当前契约返回 `"implemented_exact"`；本 feature 未修改 piston 实现或该测试。Agent IPC 测试在提供其要求的 Node/fixture 环境后通过。

示例构建与几何对拍命令：

```powershell
$env:TAURI_CONFIG='{"bundle":{"resources":[]}}'
cargo build --manifest-path src/apps/desktop/src-tauri/Cargo.toml --example native_plan_geometry_probe --offline
python src/apps/desktop/src-tauri/examples/native_geometry_parity.py
```

机器生成的完整 report 位于忽略目录 `src/apps/desktop/src-tauri/target/f4-occt/plan-parity/summary.json`，不提交。
