# F4.2.17 CADPlan helix 原生执行

状态：**已完成（标记近似）**。Rust native builder 已接入 preflight、执行与 capability registry；实际 OCCT seam 生成单一有效闭合 facet-BRep。CATIA 映射仍不支持。

## 范围与参数语义

只处理 CADPlan `helix` 的本地几何。它是沿螺旋路径扫掠圆形线材的弹簧/螺旋体，不代表 CATIA 原生弹簧或解析曲面扫掠。

沿用 `src/legacy/ai_modeler/core/geometry/kernel.py::make_helix` 与 `Document` 输入语义：

- 必填：`radius`（中心线半径）、`wire_r`（线材截面半径）、`height`（首末截面的轴向总位移）、`turns`（总圈数）。
- 可选：`center` 为首截面中心的全局起点；`axis` 为全局正 X/Y/Z，默认 Z；`taper` 为中心线半径收缩比例；`taper_start` 为归一化路径上的收缩起点；`seg_per_turn`、`wire_seg` 控制路径和线材截面的多边形采样。
- Rust 保留 legacy 的径向/轴向圆形截面布局、截面方向、三角片全局方向修正，以及 Python ties-to-even 路径分段舍入。输出标记为 `approximate`。
- 必需尺寸缺失、非有限参数、退化末端半径、自交风险、资源越界或 OCCT 缝合结果无效时失败关闭；不钳制输入、不以开口网格充当成功 B-Rep。

## 原生执行边界

builder 位于 `src/apps/desktop/src-tauri/src/native_helix.rs`，通过 `native_plan_geometry` 的 kind 注册、单位归一化、required-parameter 检查、preflight 和 build dispatch 接入。线长单位归一化包含 `wire_r`。

接受范围：`radius, wire_r, height > 0`、`turns >= 0.25`、`axis=X|Y|Z`、`taper=0..=1`（且末端中心线半径必须仍大于 0）、`taper_start=0..=0.9`、`seg_per_turn=6..=512`（默认 14）、`wire_seg=3..=64`（默认 8）。拒绝轴向螺距 `height/turns <= 2*wire_r + 1e-6 mm`，或线材半径达到采样中心线的最小曲率半径；三角面总数必须不超过 100,000。运行时还要求 OCCT 得到一个 valid、closed 的单一 solid 和有限正体积。

Capability 标为 `implemented_approximate`，公开上述范围及拒绝变体；CATIA mapping 为 `unsupported`。成功的本地 OCCT B-Rep 不构成 CATIA 执行证据。

## 验收结果

实际 Rust→C ABI→OCCT 结果均是 valid 单一闭合实体。以下是 native 和保留 Python OCP `build_document(plan_to_document(plan))` 结果；包围盒最大误差容差为 `1e-6 mm`，体积误差容差为 `1e-5 mm³`：

| 案例 | 三角面 | B-Rep 包围盒 `[xmin,ymin,zmin,xmax,ymax,zmax]` mm | Native 体积 mm³ | 与旧 OCP 差值 |
|---|---:|---|---:|---:|
| Z 轴基础，R8 / 线半径1 / 高20 / 3圈 | 688 | `[-9,-8.774351209636414,-1,9,8.774351209636414,21]` | `412.1871098550947` | bbox 0 mm；volume 0 mm³ |
| Y 轴平移，R10 / 线半径1 / 高20 / 4圈 | 912 | `[1.2757929659999374,-8,-6,22.724207034000063,14,16]` | `687.0818220773301` | bbox 0 mm；volume 0 mm³ |
| X 轴平移并 taper=0.15 / taper_start=0.25 | 912 | `[1,-8,-6.72420703400006,23,14,14.724207034000061]` | `648.4382012601247` | bbox 0 mm；volume 0 mm³ |
| Y 轴厘米单位换算 | 912 | `[1.2757929659999374,-8,-6,22.724207034000063,14,16]` | `687.0818220773301` | bbox 0 mm；volume 0 mm³ |

Schema-valid capability 正负探针覆盖圈距自交、中心线曲率不足和超过三角面上限；native preflight 在装载 OCCT 前拒绝这三类输入。模块单测还覆盖参数边界、路径舍入以及单位归一化不改写原计划。

## 已运行检查

- `pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_helix`：3 passed。
- `pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_feature_specs`：10 passed。
- `pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_plan_geometry::tests`：19 passed（包含 `wire_r` 厘米换算）。
- `$env:TAURI_CONFIG='{"bundle":{"resources":[]}}'; cargo build --manifest-path src/apps/desktop/src-tauri/Cargo.toml --offline --example native_plan_geometry_probe`：通过。
- `$env:AIMODELER_OCCT_MANIFEST='C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\f4-occt\runtime-manifest.json'; $env:PYTHONDONTWRITEBYTECODE='1'; python -B src/apps/desktop/src-tauri/examples/native_geometry_parity.py`：四个 helix 正例在 runner 的旧 OCP 对拍中通过并生成报告；随后 runner 继续执行仓库既有的宽范围全矩阵，在处理约 27 个案例后为限制本 feature 的验收范围而主动停止。因此**完整 parity 矩阵未完成，未宣称通过**。manifest 仅从 `f7-main-merge` worktree 只读加载，输出在本 `f4-helix` worktree 的独立 target。
- `git diff --check`：本提交前另行执行，结果记录于提交检查。

以上是 CADPlan 参数几何和 B-Rep 验收，不是图片还原、制造公差或 CATIA 验收。facet-BRep 与解析螺旋扫掠的曲面误差未在本 feature 中度量。
