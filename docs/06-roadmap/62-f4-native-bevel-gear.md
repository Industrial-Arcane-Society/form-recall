# F4.2.13：CADPlan bevel_gear 原生几何执行

验收日期：2026-10-04。

## 迁移依据与模型口径

本项从当前 legacy 源码逐层核对后迁移已有近似配方，没有按零件名称重建标准渐开线锥齿轮：

- `src/legacy/ai_modeler/parts/library/partlib.py` 的 `_bevel_gear` 将 `m/z/b/delta/dh` 映射为 CADPlan 参数 `module/teeth/height/cone_deg/bore_d`，中心固定为 `[0,0,0]`，旧零件目录给出 `segments=12`。
- `src/legacy/ai_modeler/core/model/docmodel.py` 的 `Document` builder 调用 `K.make_bevel_gear`。
- `src/legacy/ai_modeler/core/geometry/kernel.py` 的 `make_bevel_gear`、`profile_gear`、`make_twisted_tube` 定义真实网格：每齿 8 个梯形轮廓点，40 边中心孔，沿全局 Z 居中，端面之间按线性缩放逐段缝合，齿面和端面均为三角片。
- `src/legacy/ai_modeler/core/geometry/occ_kernel.py` 将 `bevel_gear` 纳入现有 recipe mesh → facet-BREP 路径；其局部几何状态明确为 `approximate`。
- `src/legacy/ai_modeler/sdk/cadplan.py` 声明必需参数为 `teeth/module/height/cone_deg`，可选参数为 `bore_d/center/segments`；`module` 和 `height` 按长度单位归一，`cone_deg` 按角度单位归一。

已复刻的主要语义：

- CADPlan 公共校验要求齿数为整数且 `>=6`；旧低层几何 helper 会将低于 6 的输入夹到 6，但这些值不会通过 CADPlan。分度半径 `R = module * teeth / 2`，齿顶半径 `R + module`，齿根半径 `max(1 mm, R - 1.25 * module)`。
- `cone_deg` 限制在 `5..=75°` 后使用；顶端缩放 `max(0.3, 1 - height * tan(cone_deg) / R)`。全局 `center` 是厚度中点，实体轴向范围为 `center.z ± height/2`。
- CADPlan 对显式 `segments` 要求整数且 `>=3`；旧低层几何 helper 再按 `max(4, min(requested_segments, floor(teeth/2)))` 取实际分段数，缺省请求值为 `12`。零扭转；上下环端面与内外侧壁按 legacy 三角面连接。
- `bore_d` 是直径。省略或传 `0` 都会选用旧版默认孔半径 `max(0.5 mm, 0.28 * R)`，之后按 `min(radius, 0.70 * R)` 限制；旧版没有用 `0` 表示无孔的变体。正孔径仍须严格小于离散齿廓到中心的实际最小净空。负值由 CADPlan 和 native preflight 拒绝。
- `center` 缺省为原点。锥角越界按旧版显式夹紧规则处理；齿数低于 6 或显式分段低于 3 会先被 CADPlan 公共校验拒绝。Rust 额外拒绝超过 100,000 个三角面的网格、孔切穿齿根的几何，以及 OCCT 缝合容差下的退化面。

Rust 直接生成同一拓扑三角面，并复用现有 `occt_make_solid_from_triangles` C ABI。构造结果必须通过有效性分析且恰好是一个闭合 solid 才能返回。能力查询将本地状态报告为 `approximate`。该表示是当前梯形齿/线性锥缩 facet-BREP，不含渐开线齿面或解析锥面；CATIA 映射仍为 `approximate`，其旧映射是直齿圆柱轮廓并省略锥缩，CATIA 未在本次运行。

## Rust 测试和 registry

- `scripts/test-lib.ps1 -Offline -Filter bevel_gear`：4/4 通过，覆盖公式/旧版夹紧、必需参数、孔根净空和面数上限、计划单位换算与近似能力状态。
- `scripts/test-lib.ps1 -Offline -Filter native_plan_geometry::tests::`：15/15 通过。
- `scripts/test-lib.ps1 -Offline -Filter native_feature_specs::tests::`：5/5 通过；包含 27 类 feature spec、CADPlan schema 和 preflight/build registry 一致性检查，目前原生执行 19 类。

## Rust OCCT 与 Python OCP 几何对拍

运行 `python src/apps/desktop/src-tauri/examples/native_geometry_parity.py`：bevel 独立特征分支记录为 **75 个正例通过、34 个负例拒绝**；并入当前集成分支后复跑为 **78 个正例通过、36 个负例拒绝**。新增 3 个 bevel gear 正例、8 个负例。Rust OCCT 7.9.3 与 Python OCP 7.9.3.1.1 对比旧 `Document` recipe 经 `occ_kernel.build_document` 产生的实际 B-Rep；全量矩阵容差为包围盒 `1e-6 mm`、体积 `1e-5 mm³`。三个新增实例均为单个有效闭合体，并逐项得到包围盒差 `0 mm`、体积差 `0 mm³`：

| 样例 | 旧/新 B-Rep 面数 | 包围盒 mm `[xmin,ymin,zmin,xmax,ymax,zmax]` | Python OCP 体积 mm³ | Rust OCCT 体积 mm³ |
|---|---:|---|---:|---:|
| 原点、隐式默认中心孔 | 4400 | `[-21.984368398,-21.984368398,-6,21.984368398,21.984368398,6]` | 6930.688114117896 | 6930.688114117896 |
| 偏置中心、显式孔、`cone_deg=90` 触发旧版角度夹紧 | 4176 | `[-7.490377927,-26.490377927,-4,31.490377927,12.490377927,14]` | 6535.479698985891 | 6535.479698985891 |
| cm/rad 输入归一化到原点样例 | 4400 | `[-21.984368398,-21.984368398,-6,21.984368398,21.984368398,6]` | 6930.688114117896 | 6930.688114117896 |

负例拒绝包括：零模数、负齿宽、负孔径、齿数为 5、分段为 2、中心孔切穿实际齿根轮廓、预估网格超过 100,000 三角面、缺少必需 `cone_deg`。零模数、负齿宽、负孔径、齿数和分段范围由 CADPlan 公共校验在几何执行前拒绝；孔净空和网格资源限额由 native preflight 拒绝；缺失参数由 CADPlan 校验拒绝。

这里的“精确对拍”只表示 Rust 复现当前 legacy facet-BREP 配方的包围盒和体积，不表示模型是标准齿轮精确工程设计，也不构成 Agent 原图验收或真实 CATIA 检查。完整机器报告位于忽略目录 `src/apps/desktop/src-tauri/target/f4-occt/plan-parity/summary.json`，未纳入提交。
