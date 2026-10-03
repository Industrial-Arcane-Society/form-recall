# F4.2.15：CADPlan vbelt_pulley 原生 Rust→C ABI→OCCT 支持

验收日期：2026-10-04。实现基于提交 `1e445f1` 的独立 managed worktree。Rust 定向测试、native capability contract、feature-only Rust/OCP 几何对拍及更新后的完整 `native_geometry_parity.py` 集成矩阵均通过。

## Legacy 语义与迁移范围

- `src/legacy/ai_modeler/sdk/cadplan.py` 声明 `pitch_d`、`grooves`、`height`、`groove_type` 为必需参数，`outer_d`、`groove_depth`、`bore_d`、`center`、`segments` 为可选参数；CADPlan 几何和 CATIA 映射均标记 approximate。
- `src/legacy/ai_modeler/core/geometry/occ_kernel.py` 的标准槽分支以 `std_pulley.spec(... keep_width=True)` 生成剖面并一次回转；轮宽低于 `((n-1) * max(e, root_w+0.5) + root_w+0.5)` 时明确拒绝。大直径辐条变体明确未实现。
- `src/legacy/ai_modeler/parts/standard/std_pulley.py` 定义基准直径槽角 32°/34°/36°/38° 分档；轴向剖面以轮缘/轮毂/辐板和每槽四个折点闭合。槽距为保持根部留边可能被压缩。其 `bore_d >= 0.92*pitch_d` 旧路径会改用较小孔径并给 warning；native 不静默修正，改为拒绝。
- 该基底 checkout 中没有 `src/legacy/ai_modeler/kb/pulley_std.json`，旧模块因此使用其内置 fallback 表。Rust 当前只承诺复刻 fallback Y/Z/A/B/C/D/E 数据；不实现或声称知识库直径系列吸附。

原生构造复用已存在的 `occt_make_revolve` C ABI 与 Rust `Kernel::revolve`，不增加新的 C ABI 符号。只有标准轴向回转体可构建；槽表使用旧 fallback 标准宽度/深度/间距，外径为 `pitch_d + 2*ha`。`outer_d` 只接受与该推导值一致的输入；`groove_depth` 只接受等于 `ha+hf` 的输入。槽型不支持未知自定义类型、外轮宽不足、`bore_d >= 0.92*pitch_d`、legacy 需生成辐条、非 Z 轴与 `base=true`。native 与能力查询始终标记 approximate，因为工程标准 profile、可能的系列吸附/其他数据库内容和 CATIA 映射并非本实现完整覆盖。

长度单位先由 CADPlan 原有规则处理；`pitch_d`、`outer_d`、`groove_depth` 已加入 native 参数规范化。旧 CADPlan 在单位转换前会以原始数值检查 v-belt 最小宽度，因此某些小尺寸 cm 输入会被旧验证拒绝；本实现保持此 legacy 限制，不放宽接受范围。

## 定向测试与能力契约

以下均在本 managed worktree 通过：

- `pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter vbelt_pulley`：**4 passed、0 failed、275 filtered out**。覆盖标准 A 三槽 profile 尺寸、未知槽型/override 拒绝、轮宽/大孔/辐条负变体、CADPlan 正例/缺参/窄轮宽及等价 B 槽 cm 预检。
- `pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_capability_contract_matches_registry_preflight_and_variant_shape`：**1 passed、0 failed**。确认此 worktree（以 `1e445f1` 为基底，不含 timing pulley 实现）Rust specs/CADPlan/native registry 一致。合入 F4.2.14 timing pulley 后原生执行总数为 **21/27**，其余 6 类为 `loft`、`helix`、`worm`、`impeller`、`scroll_disc`、`shell`；该合并态 capability 测试尚未在本 worktree 验证。

- 本 worktree 基于 `1e445f1`、不含 timing pulley，注册一致性测试为1/1通过；合入 F4.2.14 timing pulley 后总计预期为 **21/27**，其余 6 类为 `loft`、`helix`、`worm`、`impeller`、`scroll_disc`、`shell`。F4.2.14 与本变更集成后的 registry 测试仍应在集成分支复跑。

## Feature-only Rust/OCP 几何对拍

使用真实构建的 OCCT runtime、manifest 和 `native_plan_geometry_probe.exe`；这些 runtime 文件复制自 sibling timing feature worktree 的 ignored target 构建产物，没有手造 stub。临时验证脚本位于本地 ignored `target/f4-occt/vbelt-only.py`，不是提交源文件。4 个正例均与 legacy Python `occ_kernel.build_document(plan_to_document(...))` 对拍成功，包围盒误差 0 mm、体积误差 0 mm³，且 native 与 legacy 都为有效闭合单一 B-Rep：

| 用例 | 结果 | bbox 绝对误差 | 体积绝对误差 |
|---|---|---:|---:|
| A 槽，3 槽，平移 | 通过 | 0 mm | 0 mm³ |
| Y 槽，2 槽 | 通过 | 0 mm | 0 mm³ |
| B 槽，240 mm 基准径，700 mm 宽 | 通过 | 0 mm | 0 mm³ |
| 等价 B 槽 cm 输入 | 通过 | 0 mm | 0 mm³ |

4 项几何负例按预期拒绝：40 mm 窄宽、80 mm 中心孔、300 mm C 槽辐条结构、非 Z 轴参数。旧 `std_pulley.spec` 的 B 槽在 pitch 249 mm 时不生成辐条，250 mm 起生成 4 根；大于等于该切换范围的辐条形态由 native 明确拒绝。

小尺寸 `pitch_d=15 cm, height=7 cm` 虽物理换算后轮宽充足，旧 CADPlan 会在单位转换前按 15 mm/7 mm 检查并拒绝；它不是受支持输入。单位几何对拍选用 legacy CADPlan 本身能接受的 cm 值，并与等价 mm 几何比较。

## 完整 runner 状态与剩余验证

完整 `examples/native_geometry_parity.py` 使用当前 Rust probe 和真实 runtime 成功完成：**83 正例通过 / 40 负例拒绝**，包围盒容差 `1e-6 mm`、体积容差 `1e-5 mm³`；OCP `7.9.3.1.1`、Rust OCCT `7.9.3`。完整矩阵覆盖了 feature-only 样本，并保留既有 CADPlan 孔证据 flags（blind local span、near-edge partial、final B-Rep metrology、later-feature stale measurement invalidation）均为 true。先前 runner 失败分别来自旧 probe、legacy cm 预校验限制及 legacy 自动辐条尺寸边界；更新夹具后当前 run 全部通过。

CATIA 映射仍生成光轮盘体、未自动构造 V 槽；真实 CATIA 几何不在此项验收范围内。

## 复现命令

```powershell
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter vbelt_pulley
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_capability_contract_matches_registry_preflight_and_variant_shape
python src/apps/desktop/src-tauri/examples/native_geometry_parity.py
```

第三条命令会运行完整旧有集成矩阵，需先构建标准 packaged OCCT runtime、真实 manifest 与 `native_plan_geometry_probe.exe`。
