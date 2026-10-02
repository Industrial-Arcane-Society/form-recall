# F4.2.2 / F4.3.1：轮廓执行与材料区间基础

## 实现

原生 C ABI 和 Rust 包装新增解析圆锥、简单多边形棱柱、半径/轴向轮廓回转。CADPlan 原生执行已接入 cylinder.radius2、prism、revolve；坐标遵守旧内核 X:(Y,Z)、Y:(Z,X)、Z:(X,Y) 轮廓基。profile 和角度参与单位换算，原始来源保持不变。

参数越界、非有限坐标、零面积、自交、重复点轮廓明确拒绝。CADPlan 当前仍沿用既有校验器，不因 C ABI 能创建尖锥而静默允许校验器拒绝的 radius2=0。

## 真实对拍

```powershell
# 在 src/apps/desktop/src-tauri/ 执行
cargo build --example native_plan_geometry_probe
python examples/native_geometry_parity.py
cargo run --example native_geometry_evidence_probe -- target/f4-occt/runtime-manifest.json target/f4-occt/native-geometry-evidence-report.json
```

44 组 Rust OCCT 7.9.3 与保留的 Python OCP 7.9.3.1.1 几何对拍通过，包含此前 24 组基础样例和 20 组圆锥/棱柱/回转样例。包围盒容差 1e-6 mm，体积容差 1e-5 mm³；4 个既有负样本仍拒绝。首次对拍发现新 C ABI 拒绝旧内核支持的负角度回转，修正后 X/Y/Z 的 -90° 均通过。

Python 仅为开发验收基线，不是 Rust 几何运行时的 fallback。

## 材料证据

C ABI 使用 B-Rep 面交事件切分射线，再用实体分类器确定材料区间；返回内存只能通过 bridge 的专属释放函数释放。Rust 验证区间排序、有限性及范围，并用 RAII 覆盖错误路径。ABI 1 新符号可选，旧基础 DLL 不因此变为不可加载；缺能力会明确报错。

独立探针将 pre-cut / post-cut 实体对比：中心射线、24 条孔内射线、16 条孔外射线。真实九孔夹具 9/9 通过；盲孔检测到剩余材料；错位孔与错误直径负样本被拒绝。C++ smoke 亦包含范围裁剪、凹槽、相切、多个实体及非法输入。

**这些是离散材料采样证据，不是连续孔边界或拟合孔径计量。** `hole_measurements_verified=false` 保留；中心射线为空不足以证明孔。当前材料采样尚未接入生产 CADPlan 报告或 finish 门禁。本阶段没有执行图片 Agent 或 CATIA。
