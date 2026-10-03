# F4.2.3 齿轮与螺钉原生建模

CADPlan 原生几何执行已接入齿轮和内六角圆柱头螺钉。两类特征调用前拒绝缺少关键尺寸的计划，不使用生成器内的典型尺寸兜底。所有声明尺寸按 CADPlan 长度单位换算后送入 OCCT。

齿轮继承旧版 8 点梯形齿廓并用 OCCT 棱柱生成，仍不是渐开线齿形；螺钉使用解析头部/六角凹槽并缝合有可见螺旋牙型的 facet-BRep。两者在几何报告中均标为 `approximate`，不能将近似件宣称为精确制造几何。螺钉的本地可见螺纹也不等于 CATIA 原生螺纹；CATIA 导出边界见特征能力说明。

## 验收

在 `src/apps/desktop/src-tauri/` 执行：

```powershell
cargo build --example native_plan_geometry_probe
python examples/native_geometry_parity.py
```

2026-10-02：齿轮和螺钉各作为完整 CADPlan 通过 Rust→C ABI→OCCT 生成，均为单一有效封闭实体，并与旧 Python 几何的包围盒/体积对拍。螺钉报告带可见近似螺纹和显式 warning。缺少螺钉头径时在内核执行前返回 CADPlan 校验问题。
