# F4.2.1：CADPlan 基础特征原生执行

## 完成边界

`native_plan_geometry.rs` 接受有效 CADPlan，通过固定 manifest 加载 OCCT，依序执行长方体、等径圆柱、球、孔和矩形凹槽，并检查每一步的实体有效性、封闭性、包围盒与体积变化。失败返回错误，不提交半成品为成功结果；无材料变化的切除也会报错。形状与来源计划分离，执行单位统一换算为 mm/deg，原始来源与 CADPlan 不变。

当前未实现的类型或变体在执行前阻止建模，包括圆角矩形、圆锥和显式盲孔入口。它不调用 Python 兼容执行器，不会把未实现的特征改成另一种形状。生产 CAD 写工具接线仍属于 F2.5，本模块的 CLI 成功不代表 DSH 已完成这次建模。

## 坐标契约与实际修复

实际对拍确认 CADPlan 的 box 若提供 center 且省略 base，则 center 表示几何中心；明确 base=true 才表示沿正轴拉伸的底面。Rust 校验器拒绝布尔 base 的迁移错误另由 F3.2.1 修复。

旧内核使用右手轮廓基底：X 轴拉伸的 u/v 为 Y/Z，Y 轴为 Z/X，Z 轴为 X/Y。长方体/凹槽的 length/width 跟随 u/v；孔与凹槽的定位 x/y 则跟随去掉拉伸轴后的升序全局轴。这两个约定在 Y 轴上不同。新执行层保留旧几何语义，工具规格新增显式基底和 base 默认规则，避免 LLM 把它们当成同一坐标顺序。

## 实际验收

- 24 个真实 Python OCP → Rust OCCT 参数对拍通过：凹槽九孔、三个轴的 box center/base、三个轴的 cylinder base、厘米球体、三个轴的正/负面凹槽和弧度 PCD 阵列。
- 对拍检查实际包围盒与体积，容差分别为 1e-6 mm、1e-5 mm³。Python OCP 安装版本为 7.9.3.1.1，Rust SDK 为 7.9.3；没有宣称逐位相同。
- 四个负样本被拒绝且未写成功报告：未实现圆角、缺少来源、孔完全在实体外、首特征切除。
- 基础模块五项测试通过；结合其他进行中模块的一次整库测试 100/100 通过。
- 本地报告在 `src/apps/desktop/src-tauri/target/f4-occt/plan-parity/summary.json`，未上传。图像和 CATIA 验收均未执行。

测试夹具的尺寸为明确的合成输入，不是 Agent 对用户图片的独立估计。`requested_holes` 记录请求的刀具参数；`hole_measurements_verified=false` 明确表示尚未独立量测贯穿与孔径，不能用于完成验收。

## 可复跑

在 `src/apps/desktop/src-tauri/`：

```powershell
cargo build --example native_plan_geometry_probe --offline
& ./target/debug/examples/native_plan_geometry_probe.exe ./examples/fixtures/native_geometry_plan.json ./target/f4-occt/runtime-manifest.json ./target/f4-occt/native-plan-report.json
python examples/native_geometry_parity.py
```

最后一个命令是开发用旧实现对拍，要求可导入保留的 Python SDK/OCP；它不是应用运行时后端。剩余特征、独立几何量测、视图渲染/导出和 DSH 生产 dispatch 继续实施。
