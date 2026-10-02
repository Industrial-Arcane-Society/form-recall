# F4.2.5：CADPlan piston 原生几何执行

## 范围

本项将已注册的 CADPlan `piston` 特征接入 Rust → C ABI → OCCT 本地几何构建。它复用旧 Python OCP B-Rep builder 的剖面语义；不改 CADPlan 公共参数、不新增 OCCT ABI，也不代表 CATIA 创建原生活塞特征或 Agent 图片验收。

## 几何语义

- 以 `diameter / 2` 为外半径，内半径沿用旧实现 `max(1 mm, outer_radius * 0.55)`。因此无槽无销孔的基准活塞是带贯通内腔的环状回转体，不是实心圆柱。
- 剖面绕全局 Z 轴旋转 360°；中心点沿用 CADPlan 全局 XYZ。
- 每道环槽沿用旧公式：深度 `max(0.4 mm, outer_radius * 0.06)`、宽度 `max(0.6 mm, height * 0.035)`，布置于顶部下方 `0.34 * height` 的槽带内。几何过密而会让旧剖面自交时明确报错，不输出近似或损坏 B-Rep。
- `pin_d > 0` 时沿全局 X 轴切贯穿销孔，中心为活塞中心；有效孔径沿用旧上限 `min(pin_d, 1.5 * outer_radius, 0.9 * height)`。
- CADPlan 单位中的 `diameter`、`height`、`pin_d` 和 `center` 均在建模前转换为毫米；原计划及来源记录保持不变。
- 当前公共 CADPlan 校验器要求 `pin_d >= 1` 且 `grooves >= 1`，因此无销孔/无槽虽是旧几何函数支持的内部状态，不能通过现有 CADPlan 接口提交；本 feature 不扩大该公共契约。

## 限制与状态

- 单特征最多允许 256 道请求环槽，并受槽距、壁厚和封闭剖面有效性约束；超限或自交在 OCCT 事务前拒绝。
- Rust 本地几何能力标记为 exact。CATIA 映射仍是 approximate，不能把本地 B-Rep 报告当作 CATIA 验收。
- 此项只验证 CADPlan 到本地 OCCT 的几何构造，不验证视觉相似度、多模态审查或安装包运行时。

## 验收

验收使用 `examples/native_geometry_parity.py`，对照保留的 Python OCP `occ_kernel` 路径。样例包括一道槽加 X 轴销孔、带两道槽与 X 轴销孔的偏置中心、厘米单位缩放；Rust 单测另核对无槽剖面与旧实现的环形截面公式。负例覆盖无效内半径、槽距自交和资源超限。比较包围盒容差为 `1e-6 mm`，体积容差为 `1e-5 mm³`。

当前验收结果：Rust 库单测 **148/148 通过**；几何对拍 **57 个正例、12 个负例全部通过**（本 feature 新增三正、三负）；Python OCP 为 `7.9.3.1.1`，Rust 绑定的 OCCT 为 `7.9.3`。厘米样例确认尺寸、销孔直径和偏置中心先归一到毫米；失败参数和自交槽在调用 OCCT 前拒绝。结果仅代表本地参数几何一致性，不代表图片质量或 CATIA 结果。

本地验证命令：

```powershell
cargo test --lib --offline
cargo build --example native_plan_geometry_probe --offline
python examples/native_geometry_parity.py
```

执行结果及版本信息在脚本生成的 `target/f4-occt/plan-parity/summary.json`；该文件属于本地生成物，不纳入提交。
