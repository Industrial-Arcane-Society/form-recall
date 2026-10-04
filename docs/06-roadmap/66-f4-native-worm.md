# F4.2.18 CADPlan worm 原生执行

## 范围

将既有 CADPlan `worm` 接入 Rust native geometry dispatch，并保留与 legacy `Document` 配方相同的蜗杆构造语义。该实现只负责本地 OCCT 候选几何；不改 CATIA COM，也不声称创建 CATIA 原生螺纹。

Legacy `_worm` 配方将蜗杆表示为沿全局 Z 的圆轴身与采样螺旋齿：标准夹具轴身为 `radius=10 mm`、`height=60 mm`、底面中心 `[0,0,-30]`；螺旋线中心半径 `thread_r=12.5 mm`、线半径 `wire_r=2.5 mm`、高度 `50 mm`、圈数 `3.75`、起点 `[0,0,-25]`。轴身采用 legacy `segments=48` 圆截面棱柱，齿形复用 F4.2.17 helix 的多边形截面采样，再通过 OCCT Boolean fuse 合为一个体。

`axis` 是新增的可选 CADPlan 参数，用于明确拒绝旧式未知/非 Z 方向输入；省略时表示全局 Z。`center` 表示轴身底面中心；`center2` 表示螺旋起点；两点必须沿同一条全局 Z 轴。

## 严格几何契约

- 必填：`radius`、`height`、`thread_r`、`wire_r`、`turns`；`height2` 默认 `height`，`center` 默认原点，`center2` 默认 `center`。
- 只接受全局 Z 轴及有限 XYZ 中心；`center` 与 `center2` 的 X/Y 偏差不得大于 `1e-6 mm`。坐标统一先按 CADPlan 长度单位换算为 mm，再进行约束检查。
- 要求 `radius`、`height`、`thread_r`、`wire_r`、`height2` 为正，`turns >= 0.25`；helix 自身另行执行螺距、曲率、线段数和三角面预算检查。
- 仅当螺旋线材内缘与轴身半径有接触或重叠、外冠超出轴身、且两者 Z 轴向范围有交集时才允许尝试构建。存在径向间隙、螺旋冠完全埋入轴身、或轴向不重叠时在 OCCT 前拒绝。
- 轴身圆截面离散数为整数 `8..=512`，默认 `48`。
- OCCT fuse 后必须同时满足 `valid=1`、`solid_count=1`、`closed_solid_count=1` 和有限正体积。即使 OCCT 返回有效的多闭合体 compound，也拒绝整个候选，不将其报告为成功蜗杆。

## 近似与格式边界

轴身的圆截面和螺旋齿均为离散多边形，螺旋线复用 helix 的三角片扫掠缝合实现；结果是近似 facet-BRep，不是解析螺旋曲面。CADPlan capability 和 build report 均将本地几何标记为 `approximate`。CATIA 映射仍按旧 Document 转换器仅输出光滑轴身的近似能力登记；本地生成的螺旋齿不会被描述为已写入 CATPart。

## 测试与真实几何对拍

工作目录为隔离 worktree `f4-helix`；OCCT runtime manifest 从 `f7-main-merge` 验收 target 只读加载；本次编译和报告输出位于本 worktree 的 `src/apps/desktop/src-tauri/target`。

命令：

```powershell
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_worm
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter worm_is_registered_and_normalizes_both_centers_and_all_radial_lengths
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter worm_is_native_approximate_and_keeps_catia_smooth_shaft_boundary
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter advertised_native_parameter_limits_are_exercised_by_preflight_and_builder
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_capability_contract_matches_registry_preflight_and_variant_shape
```

以上 5 项 Rust 定向测试分别为 `5/5`、`1/1`、`1/1`、`1/1`、`1/1` 通过。另编译并运行真正的 CADPlan→Rust→OCCT probe：

```powershell
$env:TAURI_CONFIG='{"bundle":{"resources":[]}}'
cargo build --manifest-path src/apps/desktop/src-tauri/Cargo.toml --offline --example native_plan_geometry_probe
$env:AIMODELER_OCCT_MANIFEST='C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\f4-occt\runtime-manifest.json'
$env:PYTHONDONTWRITEBYTECODE='1'
python -B src/apps/desktop/src-tauri/examples/native_worm_parity.py
```

`native_worm_parity.py` 运行三个 Rust native plan，并与保留的 Python `plan_to_document` + OCP `build_document` 比较最终 B-Rep：

| 正例 | BBox 最大绝对误差 | 体积绝对误差 | 两边实体数 |
|---|---:|---:|---:|
| 旧配方标准切触（mm） | `0 mm` | `7.28e-12 mm³` | `1 / 1` |
| 同轴平移 `[12,-7,5]`（mm） | `0 mm` | `6.19e-11 mm³` | `1 / 1` |
| 标准切触（cm，归一化后等价） | `0 mm` | `7.28e-12 mm³` | `1 / 1` |

验收容差为 BBox `1e-6 mm`、体积 `1e-5 mm³`。五类 schema-valid 无效几何（非 Z 轴、偏轴 center2、径向间隙、埋冠、轴向不重叠）均在 build 前拒绝。资源上限由 Rust 单测检查。

多体防误报另有一个实际 fuse 负例：保持分析意义上的径向切触但将轴身截面降至最低 `8` 边，轴身内切多边形与采样螺旋齿仅在离散位置接触。真实 OCCT 返回 `valid=1, solid_count=2, closed_solid_count=2`；`native_plan_geometry_probe` 返回失败，错误包含“必须是单一有效闭合实体”，并且不生成 build report。由此覆盖了“有效 compound 被误当成单体成功”的风险。

完整报告与输入计划保存于 `src/apps/desktop/src-tauri/target/f4-occt/worm-parity/`，由 parity runner 重新生成，不纳入提交。

## 未覆盖项

- 未执行视觉图片对比；本项验收是确定性 CADPlan 几何和旧 OCP B-Rep 对拍。
- 未执行 CATIA / CATPart 导出和安装机验收；CATIA 螺纹仍缺原生映射。
- 不支持 X/Y/Z 任意朝向、偏心螺旋、任意曲轴关系、离散轴身之外的解析曲面或多实体蜗杆。
- 对旧 facet/B-Rep 的 exact parity 只覆盖表格中的三组输入，不外推到所有 turns、segments 或复杂啮合比例。
