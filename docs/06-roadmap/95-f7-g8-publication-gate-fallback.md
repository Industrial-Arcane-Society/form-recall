# F7/G8 fatal publication gate fallback

日期：2026-10-05

## 问题

Run30/Run31 的 allowlist summary 显示 `stage=publication`、`code=uncaught_exception`，但 `strict_gates.gates` 为空。Run31 的 gate evaluation 已在内存中完成，却在写 safe artifacts 时失败；fatal fallback 不接收或保留逐项 gate 状态。因此无法区分 strict gate 的实际结果。Run31 的原始诊断仍只代表当时观测到的 safe artifacts，本文记录的是随后代码修复，不回溯改写运行事实。

## 修复

- gate evaluation 刚返回时，coldstart runner 立即生成并缓存 `buildSafeStrictGateProjection`。缓存只含固定 13 个 gate key、`passed`/`failed`/`unknown` 状态、非负 reason count/null 和按 gate 白名单过滤的 reason code。
- fatal publication fallback 将缓存的 projection 再按固定 schema 清洗后写入 `summary.json`。异常对象、错误正文、provider 内容、scratch 路径、工具参数/ID、图像与几何载荷均不传给 projection。
- 如果 gate 尚未评估，或某项 gate 缺少有效状态，该项发布为 `unknown`，reason count 为 `null`，reason code 为空。未知的 gate key、状态和 reason code 都会被丢弃；不猜测失败原因。
- fatal summary 的整体状态仍为 `failed`，即使已评估 gate 中有 `passed` 项；逐项状态表示评估当时的 gate 结果，不能推翻 publication failure。

## 回归与边界

新增 Node 回归人为令常规 artifact publisher 在第一份安全诊断写出后、summary 发布前失败，再验证 fatal fallback 写出完整 13 项 gate projection。测试覆盖已知 pass/fail、缺项 unknown、allowlisted reason code 保留，以及 provider/工具/几何/异常 sentinel 不泄漏。没有运行 DSH/provider；这不是 Run32，也不证明真实 publication failure 已复现。

Node 全量 coldstart gate suite：130/130 通过；Run32 仍需在具备有效 pinned DSH runtime 与 OCCT manifest 的隔离环境中单次验证，之后只读取 safe allowlist artifacts。
