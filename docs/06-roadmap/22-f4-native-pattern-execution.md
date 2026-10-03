# F4.2.4 CADPlan pattern 原生执行

## 范围

本 feature 将 CADPlan `pattern` 接入 Rust → C ABI → OCCT 本地几何构建。它只处理阵列复制，不改变 Agent 的工具选择流程，也不代表 CATIA 执行或图片复刻验收。

## 几何语义

- `pattern_source` 必须唯一，并指向紧邻的前一个已构建特征；阵列操作必须与来源特征的 `add`/`cut` 相同。这与 CADPlan 校验和旧 OCCT builder 的约定一致。
- 线性阵列按全局 XYZ `step` 平移第 2 至第 N 个副本；`step` 按计划长度单位转换为毫米。
- 圆周阵列绕全局 X/Y/Z 轴、指定中心执行右手旋转。完整圆周使用 `angle/count`，开放角度使用 `angle/(count-1)`；孔记录的中心线和方向随 B-Rep 一起变换。
- 复用来源特征的 B-Rep 刀具进行布尔加料或切除。孔报告新增 `axis_vector_mm`，并以任意轴向投影包围盒生成采样与最终 B-Rep 计量请求。
- `count=1` 或切除副本完全落在已有空腔时，保留当前实体并报告 `no_op`。复制结果若产生多个互不连接的实体，按当前单一封闭实体合同拒绝。

## 资源与支持状态

- 单个阵列最多 10,000 个总实例；最多变换 100,000 个 B-Rep 刀具/孔记录。超限在 OCCT 事务前报错；这属于几何工作量保护，与 Agent 工具调用次数无关。
- CADPlan 验证器按阵列模式要求几何参数：线性模式需要 `step`；圆周模式需要 `axis`、`angle` 和 `center`。`full` 可省略并按总角度推断。
- 本地 Rust 几何状态为 exact；这不等于当前 CATIA 原生特征自动创建。CATIA 对 X/Y 轴圆周阵列仍会按已有支持矩阵标记。

## 验收记录

2026-10-02，在固定 OCCT runtime 上完成：

- `cargo test --lib --offline`：143 项通过。
- `native_geometry_parity.py`：Rust 与 Python OCP 共 54 个正例、9 个负例通过。正例包括已有基础特征回归，以及 pattern 的 X/Y/Z 线性平移、X/Y/Z 圆周变换、相交和不相交的工具复制、四个贯穿孔圆周阵列及 no-op 切除。
- 几何对拍容差：包围盒 1e-6 mm，体积 1e-5 mm³。运行时版本：Rust bridge OCCT 7.9.3，Python OCP 7.9.3.1.1。
- 负例覆盖超出阵列资源上限、产生分离多实体、错误/缺失来源，以及现有特征的既有拒绝样例。
- 最终几何只用于验证 CADPlan 到 B-Rep 的参数语义和 Rust/Python 几何一致性；没有运行 Agent 图像盲测，没有执行 CATIA，也没有将局部孔采样描述为图片验收。

复跑命令：

```powershell
# 在 src/apps/desktop/src-tauri
cargo test --lib --offline
cargo build --example native_plan_geometry_probe --offline

# 在仓库根目录
python src/apps/desktop/src-tauri/examples/native_geometry_parity.py
```
