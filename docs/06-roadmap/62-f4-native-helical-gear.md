# F4.2.12 CADPlan helical_gear 原生几何

日期：2026-10-04

## 范围与语义

本项将 `helical_gear` 接入 Rust native CADPlan 几何执行与能力注册。实现遵循保留的 `CADPlan -> Document -> OCP` 语义：在全局 Z 轴构造居中的梯形齿直棱柱；`helix_deg` 作为有限输入接受但不参与构形；`bore_d=0` 不生成内孔，显式正 `bore_d` 使用 40 边多边形构造贯穿孔。齿形仍是梯形近似，不是渐开线。CATIA 映射标记为 approximate，也不声称保留螺旋角。

未实现真实螺旋扭转、非 Z 轴和隐式推断内孔。这里没有补造缺失尺寸。

## 实现位置

- `src/apps/desktop/src-tauri/src/native_standard_features.rs`：原生直齿棱柱和显式 40 边内孔；返回近似支持细节与 `helix_deg_applied=0`。
- `src/apps/desktop/src-tauri/src/native_plan_geometry.rs`：注册、必需参数及齿数范围 preflight、builder dispatch。
- `src/apps/desktop/src-tauri/src/native_feature_specs.rs`：能力规格明确列出近似性质、参数语义与未支持变体。
- `src/apps/desktop/src-tauri/examples/native_geometry_parity.py`：三项 legacy 几何对拍及无效输入覆盖。

## 验收证据

- `cargo check --manifest-path src/apps/desktop/src-tauri/Cargo.toml --offline --tests`：通过。
- `pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter helical_gear`：3 passed、0 failed。使用 repo 的 Common Controls SxS manifest-aware runner。
- `cargo build --manifest-path src/apps/desktop/src-tauri/Cargo.toml --offline --example native_plan_geometry_probe`：通过；使用复制到本 worktree ignored target 的 17 个 OCCT runtime DLL 与哈希 manifest。
- `python src/apps/desktop/src-tauri/examples/native_geometry_parity.py`：72 个正例通过，25 个负例被拒绝；bbox 容差 `1e-6 mm`，体积容差 `1e-5 mm³`。旧 Python OCP 为 `7.9.3.1.1`，Rust OCCT 为 `7.9.3`。
- 将 helical_gear 与 crankshaft 集成后，在集成工作树再次执行同一矩阵：75 个正例通过、28 个负例被拒绝；bbox 容差 `1e-6 mm`，体积容差 `1e-5 mm³`。这是当前已迁移特征的累计几何矩阵，不代表 27 类全部完成。

helical_gear 三项 B-Rep 都有效，为一个闭合 solid；结果为：

| 用例 | Legacy 体积 mm³ | Rust 体积 mm³ | BBox 最大误差 mm | 体积绝对误差 mm³ | 相对差 |
| --- | ---: | ---: | ---: | ---: | ---: |
| helix 18°、无孔 | 12088.0289424854 | 12088.0289424854 | 0 | 1.82e-12 | 1.50e-16 |
| helix 42°、无孔 | 12088.0289424854 | 12088.0289424854 | 0 | 1.82e-12 | 1.50e-16 |
| helix 18°、显式 8 mm 孔 | 11587.4386543563 | 11587.4386543566 | 0 | 3.04e-10 | 2.62e-14 |

两组不同 `helix_deg` 的 BBox 和体积完全相同，符合 legacy Document builder 忽略该角度的行为。体积误差在测量范围内，以上值属于实测，不外推为所有参数精确等价。

## 限制

上述是 CADPlan 几何参数对拍，不是 Agent 图片验收或 CATIA 真实几何验收。没有运行真实 CATIA；不能据此宣称 CATIA exact 或真实斜齿几何能力。
