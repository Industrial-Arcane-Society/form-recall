# F3.2：Rust CADPlan Schema 与静态校验

更新时间：2026-10-02。

## 实施边界

`cad_plan_validation.rs` 提供 `validate_plan`、`strict_preflight`、`schema_document`、`feature_schema` 和 `feature_spec`，运行时不依赖 Python。

校验覆盖当前 27 类特征的参数白名单、类型、范围、条件参数、尺寸来源、图片估值证据、单位和坐标。关系检查覆盖引用目标、先序依赖与阵列操作一致性。strict 预检在计划无效、存在待确认项、CATIA 近似或不支持特征时返回阻断结果。

`geometry_support` 是迁移时已有后端的静态能力表，不检测机器是否安装 OCCT，也不证明 Rust 几何内核已经实现这些特征。真正的几何执行和 B-Rep 核验属于 F4；CATIA 验收需另行执行。

## 对拍与修复

- fixture 位于 `src/apps/desktop/src-tauri/examples/fixtures/cad_plan_validation_golden.json`，记录真实 Python `validate_plan` 和 `build_plan(strict=True)` 的输出，以及 Python 源码 SHA-256。
- 27 个案例包含一份覆盖全部 27 类特征的计划和 26 个无效或边界变异。对象键顺序不影响比较；数组顺序必须相同。逐项比较两个接口的完整输出，27/27 一致。
- strict 样例均因校验错误或 shell 不支持而在执行前返回，未调用几何核或 CATIA。这些 fixture 验证的是执行前阻断，不能代表正常构建成功。
- 对拍修复了四种特征的支持说明差异、来源确认文案差异和 V 带轮报错中的 `%g` 格式。额外回归测试捕获并修复了整数尾零被错误删除的问题。
- 模块测试 7/7 通过；接入现有应用后的整库测试 50/50 通过。此前一次并行整库运行出现旧工具桥测试失败，单项复跑及整库复跑通过，尚无证据将其判定为已修复缺陷。

## 可复跑入口

在 `src/apps/desktop/src-tauri/` 执行：

```powershell
cargo test --lib cad_plan_validation::tests --offline
cargo build --example cad_plan_probe --offline
'{"method":"schema"}' | .\target\debug\examples\cad_plan_probe.exe
```

JSONL probe 新增 `validate`、`strict_preflight` 和 `schema` 方法；前两者接收 `plan` 字段。它们只检查计划，不创建模型。

## 剩余工作

F3.3 会话版本、候选恢复、证据生命周期及几何 dispatch 尚未完成；F2.5 原生 CAD 写工具仍需接入这些组件。F3.2 不代表整个 F3 或 Agent 建模质量通过。
