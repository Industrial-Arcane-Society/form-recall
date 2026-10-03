# F4.2.6：原生辐条轮

验收日期：2026-10-03。

## 输入与完成条件

CADPlan v1 的 `spoked_wheel`，保持既有 Python OCP 参数语义，不扩展公开 Schema。构建解析环形轮缘、带轴向中心孔的轮毂、绕全局 Z 轴等角布置的棱柱辐条；沿用旧内核的接合重叠量与中心孔半径计算。必须形成一个有效闭合实体，中心偏移及 cm 输入与旧实现一致。

`axis` 并非此特征的公开参数，不能传入非 Z 轴后悄悄忽略。尺寸非正、轮缘与轮毂不相容、辐条少于两根、辐条宽度不合法或超过 512 根的资源边界会拒绝。

## 实现与验证

- `native_parametric_features.rs`：解析轮廓回转、辐条旋转并集合并及 B-Rep 合法性检查。
- `native_plan_geometry.rs`：preflight/执行路由，以及 outer_r/rim_w/hub_r/spoke_w 的长度单位换算。
- `native_feature_specs.rs`：原生能力说明与 CATIA 近似状态分别公开。
- `examples/native_geometry_parity.py`：新增原点、平移中心、cm 输入三个正例及非公开轴参数、过少辐条、不合法轮缘三个负例。

在 `src/apps/desktop/src-tauri` 执行：

```powershell
cargo build --example native_plan_geometry_probe --offline
cargo test --lib native_parametric_features::tests::spoked_wheel --offline
python examples/native_geometry_parity.py
```

定向 Rust 2/2 通过。真实 Rust OCCT 7.9.3 与 Python OCP 7.9.3.1.1 的全量对拍 60 个正例通过、15 个负例拒绝。三个新增样例包围盒误差均为 0 mm，体积绝对误差为 3.64e-12、0、0 mm³；总体容差沿用 1e-6 mm 与 1e-5 mm³。机器报告保存在本地忽略目录 `target/f4-occt/plan-parity/summary.json`。

此项验证本地 CADPlan → B-Rep 的迁移语义；没有运行图像盲测或 CATIA。F4 的其余公开特征、变体和安装交付继续执行。
