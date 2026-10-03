# F4.2.9：rotor 显式参数原生执行

## 选型与参数前置条件

选择 rotor 是因为旧轮廓可由固定 radial recipe 生成闭合多边形，已有 OCCT prism 和 cylinder/cut 足以构造，不扩展 C ABI、不依赖 Python runtime、不需要带轮槽型标准推算或其他隐含尺寸。

必须显式提供 radius、n、height、bore_d。radius/height为有限正数，bore_d为有限非负数；0明确表示没有中心孔。n须为12..4096整数，不截断、不夹紧、不补默认值。center省略只选择原点，显式值必须为三个有限全局坐标；center表示几何中点，厚度沿全局Z从center.z-height/2到center.z+height/2。非Z轴和base约定拒绝。

孔半径必须严格小于实际离散多边形到原点的最小线段距离；不能仅用0.82R光滑半径上限保证粗轮廓孔壁。radius、height、bore_d与center全部按CADPlan长度单位归一到mm，n不缩放。缺尺寸或非法几何在OCCT执行前拒绝。

## 准确性状态与实现

原生能力标为 implemented_approximate，几何报告也标 approximate。轮廓等同保留的Python `kernel.profile_rotor`公式：固定flank=0.82与cosine exponent=6，三个90°+120°i顶点的最大径向值。它是近似圆角三角转子，不是精确Reuleaux曲线，也不是制造级发动机转子。CATIA映射另有其契约，本次未运行CATIA。

新增独立native_rotor模块；dispatcher只追加一种kind、显式参数preflight及构建分支。已有单位归一新增bore_d。用原尺寸OCCT棱柱、同高解析圆柱切孔，未给钻孔增加猜测深度或epsilon。构造后验证单一有效闭合体，并比较B-Rep体积与多边形面积×高度减解析孔体积；能力契约同步15类原生支持。

中心孔没有冒充独立孔径计量：测试中的轴心材料区间只证明孔处材料被移除；support details记录bore_d和几何报告，不新增独立hole measurement记录。当前形体的局部精确B-Rep执行不改变整体profile的approximate类别。

## 定向真实验收

在src/apps/desktop/src-tauri执行，最终均退出0：

```powershell
rustfmt --edition 2024 src/native_rotor.rs
cargo test native_rotor::tests --lib --offline
cargo test native_feature_specs::tests --lib --offline
cargo check --lib --offline
```

rotor 3/3，能力规格2/2，offline library check通过。真实几何测试要求固定runtime manifest存在，缺失会失败，不以skip计通过。初次fixture使用了不受支持的坐标系字段，尚未执行几何；改为实际global_cartesian/axes/origin契约后定向测试全部通过。

机器可读报告：`src/apps/desktop/src-tauri/target/f4-occt/rotor-acceptance.json`。5个正例全部valid=1、solid_count=closed_solid_count=1：

| 正例 | 体积 mm³ | 检查 |
|---|---:|---|
| R20/n48/h8，无孔，原点 | 8230.23721654999 | 包围盒、体积、中心材料区间 |
| 同轮廓，bore_d=4 | 8129.70625163511 | 解析切孔、轴心材料为空 |
| 带孔平移至[12,-7,5] | 8129.70625163511 | 平移不改变体积，包围盒同步平移 |
| cm参数R2/h0.8/bore0.4/center[1.2,-0.7,0.5] | 8129.70625163511 | 全长度单位含bore_d正确换算 |
| n12粗轮廓带孔 | 7738.47419133513 | 粗离散有效性与几何范围 |

原点n48包围盒为[-18.665471334646035,-16.456249999999997,-4,18.66547133464603,20,4]mm。逐坐标误差门槛1e-6mm；体积与多边形解析公式差值门槛1e-5mm³，并检查固定radius/flank给出的独立面积下界与圆盘面积上界。最大实测体积误差约1e-11mm³。这些容差是fixture核验门槛，不是制造误差或图片尺寸准确率。

负例覆盖4个参数缺失、非正radius/height、负bore、超外轮廓孔径、n过小/过大/非整数、非法center/null、非Z轴/base、派生体积溢出及孔径恰好到达实际多边形边界，均拒绝。能力测试检查approximate标签及公式的明确轮廓点。

## 验收边界

本次只验证deterministic CADPlan→Rust→C ABI→OCCT几何闭环；没有真实Agent、用户原图盲测、Tauri窗口、CATIA、制造级曲线/配合或安装包验收。没有运行完整历史几何对拍套件，不将局部数值公式检查称为旧Python完整行为兼容。n<12历史粗三角轮廓明确不迁入，保证当前原生资源与孔壁边界可控。其余12类未执行特征与F4整体安装交付继续待完成。
