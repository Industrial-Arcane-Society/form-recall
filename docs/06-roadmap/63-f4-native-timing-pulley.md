# F4.2.14：CADPlan timing_pulley 原生候选几何

## 迁移范围

本项把旧 Document/CADPlan 的同步带轮算法迁到 Rust→C ABI→OCCT。本地特征沿全局 Z 轴、以 `center` 为几何中面；轮齿由每齿一个六点梯形棱柱构成，齿数绕轴均布。齿根半径、齿顶半径及内孔默认值沿用 `kernel.make_timing_pulley`：`pitch_r=max(2,pitch_d/2)`、`root_r=pitch_r-0.55*tooth_height`、`tip_r=pitch_r+0.45*tooth_height`；显式孔半径 `max(1,bore_d/2)`，隐式孔半径 `max(2,root_r*0.35)`。单位归一化包含 `pitch_d`、高度、孔径与中心坐标。

## 可构建变体与拒绝条件

单体约束要求齿柱的内缘 `pitch_r-1` 与盘体齿根半径有径向交叠。按旧梯形轮廓，`0.55*tooth_height <= 1 mm` 才能保证交叠。因此 native preflight 仅接受 MXL、XL、T2.5、T5、3M 五种 profile；拒绝 L、H、XH、XXH、T10、T20、5M、8M、14M。这是对构造可保证性的约束，不代表这些标准 profile 本身在行业定义上无效。首轮 OCP 对拍中，8M 确实以 `profile is self-intersecting` 被 legacy recipe 拒绝。

其它明确边界：`pitch_d` 与 `height` 必须为正；`teeth` 取 6..=512；`bore_d` 非负且有效孔半径须小于齿根半径；齿顶切向齿距小于标准齿顶宽时拒绝；型号输入按 14 种旧标准字符串解析并做大小写与“型”后缀规范化，但仅前述五种进入构建；`segments` 需为 3..=512。Native blank 是解析环形回转面，忽略 `segments`，齿仍是旧梯形近似。OCCT fuse 后要求有效、单一、闭合实体。

## 几何近似边界

旧盘体是按 `segments` 生成的 facet-BREP；native 使用精确圆柱面/端面。包围盒预期一致，但体积不承诺逐值相等。验收 runner 对每个正例记录 legacy/native 体积，并把盘体圆周离散相对解析圆的面积差乘以 `(root_r²-inner_r²)*height` 作为体积误差上界；还单独要求 volume error < legacy 体积的 1%。这不是齿面标准精度或 CATIA 几何验收。CATIA 映射仍是近似描述。

## 定向验证与状态

`test-lib.ps1 -Offline -Filter timing_pulley`：4/4 通过，覆盖 registry、`pitch_d` 长度单位换算、有效尺寸、型号解析、缺参/越界孔/相邻齿重叠/资源上限及九个无径向重叠 profile 拒绝。

`native_capability_contract_matches_registry_preflight_and_variant_shape`：1/1 通过，核实 CADPlan 27 类中原生 registry 共 20 类，另外 7 类为 `loft`、`helix`、`vbelt_pulley`、`worm`、`impeller`、`scroll_disc`、`shell`。

在隔离 worktree 使用集成 worktree 已存在的 ignored runtime staging artifacts，设置 `TAURI_CONFIG={"bundle":{"resources":[]}}` 后，`cargo build --offline --example native_plan_geometry_probe` 成功。修正模型矩阵后，完整 `python examples/native_geometry_parity.py` 通过 **81 个正例、43 个负例拒绝**，其中 timing pulley 新增 3 个正例与 7 个定向负例。三个 timing 正例均为有效单闭合体，bbox 最大差 `8.9e-16 mm`；体积仅报告、不做逐值相等断言：XL 默认孔 `44332.30063 → 44353.78087 mm³`（差 `21.48024 mm³`，相对 `0.04845%`），T5 平移显式孔 `39193.78363 → 39184.82420 mm³`（差 `8.95943 mm³`，相对 `0.02286%`），MXL cm 单位 `87083.22156 → 87278.36272 mm³`（差 `195.14116 mm³`，相对 `0.22409%`）。三例都小于 1% legacy 相对体积差，且通过按旧分段盘体估算的解析圆周误差上界。完整 runner 的成功结果不包含 CATIA、Agent、图片验收或最终安装包验收。

无 Agent、图片理解、CATIA 执行或最终安装包验收声明。
