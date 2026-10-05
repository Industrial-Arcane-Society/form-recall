# F4.2.21 / F7.G6：CADPlan 完整圆柱面 shell 子集

日期：2026-10-05

## 结果

在已有 CADPlan 1.1 显式平面抽壳契约上，增加一个窄的原生 Rust/OCCT 圆柱面子集。每次 shell 操作可以选择一个圆柱面；它必须是 OCCT 解析出的解析圆柱，完整覆盖 360°，有正轴向范围，且 thickness < radius。圆柱选择不能与其它移除面混合。厚度、偏置方向、目标 plan-head、removed-face selector 和 selection_binding 仍须由计划显式给出。

选择器继续由 cad_list_shell_faces 从当前 shape 发放，并绑定 revision、几何 fingerprint、kernel version、inventory version 和 target feature。inventory 升至 v3；旧 v2 selection binding 会失效并要求重新枚举。模型接口不暴露 OCCT face ordinal。

## 安全边界

- Rust inventory 只给平面或完整 360° 的圆柱面 selector。部分角度圆柱、轴向范围为零的圆柱、圆锥和其它曲面不产生可选 selector。
- Host 在采用面清单 selector 时检查同 revision/fingerprint/kernel/target、显式正厚度及圆柱厚度小于半径；曲面 shell 一次只允许一个 selector。
- native_plan_geometry::build 再对实际累计 B-Rep 解析 selector，并重复执行曲面范围/厚度校验。
- OCCT C ABI 再核验所选面为平面或完整 360° 圆柱；曲面 shell 不得与其它移除面混用。shell 输入限于有效闭合实体，或直接只包含一个 solid 的 compound。返回值仍须通过原有单有效闭合实体检查。
- 偏置方向不会在失败时自动翻转；OCCT 拒绝自交、退化或无法生成单闭合体的候选。
- Python/OCP、CATIA 转换/导出以及旧 CADPlan 1.0 thickness-only shell 均未获此能力。

## OCCT 几何证据

在固定 OCCT 7.9.3 runtime 上建立 20×20×20 mm 盒体，减去半径 4 mm、穿过 Z 轴的圆柱孔。基于该 B-Rep 面清单选择完整圆柱孔壁，执行 1 mm inward shell：

- 结果检查：valid=1、solid_count=1、closed_solid_count=1。
- 实测体积：2067.469035085 mm³；正数且与 shell 前体积不同。
- 实测包围盒：[-10,-10,-10]–[10,10,10] mm；有限且未超出原料包围盒。
- 1 mm 及正确当前 selection binding 成功；陈旧 revision、厚度等于半径和“圆柱+平面混选”会在 Host/几何入口拒绝。

另外在该实体上开槽，使圆柱面被裁剪成小于 360° 的片段；真实 OCCT 面清单观察到完整圆柱边界 2π rad 之外的部分圆柱范围，并确认 Rust allowlist 与 C ABI builder 均拒绝。真实圆锥面也由 C ABI builder 拒绝。Bridge 输入如果来自布尔运算形成的 compound，仅接受顶层直接包含一个闭合 solid 的情形，不从多个 solid 中猜选目标。

## 验证

- OCCT C ABI smoke CTest：1/1 通过，包含真实完整圆柱面 shell、包围盒/体积有效性、部分圆柱拒绝、圆锥拒绝、厚度边界拒绝与曲面混选拒绝。
- Rust 仓库测试 wrapper：
  pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter 'native_shell::tests' -NoCapture
  在本地 OCCT 7.9.3 runtime manifest 下 3 passed / 0 failed。定向组含真实 Rust→C ABI→OCCT 圆柱面 shell，以及 selector revision、厚度和允许角域回归。
- cargo check --manifest-path src/apps/desktop/src-tauri/Cargo.toml --lib --offline 通过；隔离 worktree 需要按仓库 smoke 设置 TAURI_CONFIG={"bundle":{"resources":[]}}，否则 Tauri build script 会因测试用隔离副本不含桌面发布资源而提前失败。

## 未覆盖

本验收是窄子集的本地 B-Rep 几何证据，不是任意曲面 shell 能力证明，也没有声称 Python/OCP parity、CATIA 导出、生产 DSH Agent 实际选择行为或 UI 验收。OCCT 仍可能拒绝具体的偏置/自交组合；这类失败会保留为显式构建失败。

此前平面子集及其 inward/outward 结果见[平面 shell 记录](./84-f4-native-explicit-planar-shell.md)；generic builder 与 shell 特殊路径的分界见[能力矩阵](./63-f7-g6-native-capability-matrix.md)。
