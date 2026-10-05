# F4.2.21 CADPlan 显式平面抽壳子集

日期：2026-10-04

## 支持范围

桌面端 Rust/OCCT 执行器现在支持 CADPlan `1.1` 的显式平面抽壳子集。`shell` 特征必须使用 `operation=cut`，并明确提供正的 `thickness`、`offset_mode`（`inward` 或 `outward`）、`removed_faces`、`selection_binding`，以及唯一指向当前 plan-head 特征的 `shell_target` relation。

面选择只能来自当前几何 revision 上的 `cad_list_shell_faces`。绑定包含 revision、几何 SHA-256、kernel version、inventory version 和目标特征 ID。选择器是不透明且单次几何身份绑定的值；陈旧、重复、手工编造或来自其他目标的选择会被拒绝。原始验收只覆盖平面；后续新增的受限完整圆柱面子集、selector 边界和曲面拒绝项见[圆柱面验收](./101-f4-curved-shell.md)。部分/分片圆柱及其它曲面仍明确不可选。

旧 CADPlan `1.0` 的 thickness-only `shell` 不会自动迁移成开口面或方向；它会进入待确认状态。Python `Document`/OCP 几何执行器和 CATIA 导出映射仍不支持此 shell 子集；当前建模能力属于桌面 Rust/OCCT 执行通道。

历史上的问题分析保留在[原阻塞记录](./69-f4-native-shell-blocker.md)。

## 实测

- `PYTHONPATH=src/legacy python -m unittest src.legacy.tests.test_cadplan_sdk -v`：28/28 通过。
- `src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline`：Rust 测试 wrapper 351/351 通过。
- 从固定官方 OCCT 7.9.3 源码重新构建包含 `TKOffset` 的 runtime；C++ bridge CTest 1/1 通过。缺少 `TKOffset` 会令 bridge 的 CMake 链接配置失败，构建脚本已补齐该 toolkit。
- 用新构建 runtime manifest 通过 Rust → C ABI → OCCT CLI smoke：100×80×20 mm 方盒顶面移除、2 mm 壁厚的 inward 结果为有效单闭合实体，体积 28,672 mm³，包围盒 `[-50,-40,0]`–`[50,40,20]`；outward 结果同为有效单闭合实体，体积 31,799.055928 mm³，包围盒 `[-52,-42,-2]`–`[52,42,20]`。
- 使用该 runtime manifest 重建 MSI 和 NSIS：
  - `AI Modeler_0.1.0_x64_zh-CN.msi`：38,071,749 bytes，SHA-256 `27f43b74ab50a4439ae97825e3e8748f3947015d56d56e16862f0e679cf95ccb`
  - `AI Modeler_0.1.0_x64-setup.exe`：30,442,683 bytes，SHA-256 `7a3d7dd05acfd85945e8e329c52b934fe1de4f9c8b38ba47c4d5d7438502a245`

## 未覆盖范围

本记录证明显式平面抽壳路径及打包 runtime 的 CLI 几何结果，不代表 CATIA shell 导出、任意曲面抽壳、安装后的桌面可见窗口或干净机器安装验收通过。受限圆柱面子集有独立几何验收记录；安装包仍需独立安装和运行验收。
