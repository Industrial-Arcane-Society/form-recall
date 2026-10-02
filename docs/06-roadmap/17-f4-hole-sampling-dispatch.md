# F4.3.2 孔材料采样接入 CADPlan

## 已实现

原生 CADPlan 孔切除在执行前检查孔轴所经过的材料。盲孔入口与允许深度使用该位置的材料区间，而非整个零件包围盒。切除后记录中心、孔内和孔外共 41 条射线的材料变化。

近边孔缺少可用于对照的孔外材料时保留 `partial` 证据，允许保存合法几何候选。采样不能作为孔径计量，报告始终不凭此设置 `hole_measurements_verified`。

## CLI 验收

在 `src/apps/desktop/src-tauri/` 执行：

```powershell
cargo build --example native_plan_geometry_probe
python examples/native_geometry_parity.py
```

2026-10-02 结果：44/44 个原生与旧 OCCT 参数对拍通过；6 个非法几何样例被拒绝；局部材料入口盲孔正例通过；近边孔部分证据没有阻止合法几何生成。包围盒容差 1e-6 mm，体积容差 1e-5 mm³。报告位于忽略的 `target/f4-occt/plan-parity/summary.json`。

## 边界

这是程序参数和几何执行验收，不是 Agent 看图建模验收。当前采样在各孔切除之后运行；最终形体的实际圆柱面孔径、轴线和后续特征覆盖检查另行接入，不能复用此证据宣布最终模型验收通过。
