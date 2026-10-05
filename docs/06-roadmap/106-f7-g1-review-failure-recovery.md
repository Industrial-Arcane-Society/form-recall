# F7.G1 独立视觉复核失败安全诊断

日期：2026-10-05

基线：`4fd7eec`
范围：Rust 原生 `cad_review_reference_similarity` 的失败分类与安全反馈；未调用真实 provider。

## 实现

复核执行器此前使用 `Result<DomainResult, String>`。预览/清单/施工阶段前置校验、provider 请求和其它本地错误最终都通过 executor 的通用错误通道返回，Agent 得不到稳定失败类别。错误字符串还可能包含底层错误细节。

现在 review 使用 `ReviewFailure` 固定枚举，并通过固定安全消息返回：

- `preconditions_missing`：仅覆盖源码能够明确确认的当前预览、有效 source inventory、当前 revision host attestation 或施工里程碑欠缺；反馈要求补齐对应前置项后重试。
- `provider_failed`：provider 调用未完成且请求未被取消；反馈复核没有完成、候选仍未验证，可稍后重试。
- `unknown`：取消或其他未被明确分类的 review 错误。没有从自由文本推断原因。

反馈不包含 provider 原始错误、图片/参考 ID、路径、SHA、用户提示词或 CAD 参数。失败不会写入 review result、review history 或 finding ledger；现有候选状态保持未验证。成功路径仍执行原有 current revision、预览、inventory、host attestation、construction milestone 和 evidence gate。

Run36 已知的 `error_class=execution` 不能据此映射为上述某一具体前置缺失或 provider 失败；本改动不声称解释其根因。

## 回归与边界

新增离线 Rust 回归覆盖缺少当前预览的固定前置错误、模拟 provider 连接失败时的固定 provider 类别与候选未验证不变；既有 mock provider 成功复核回归覆盖成功结果仍可被记录。其它未分类错误安全降级为 `unknown`。

初次直接运行 Cargo 生成的测试执行器时，Windows loader 返回 `0xc0000139 (STATUS_ENTRYPOINT_NOT_FOUND)`。2026-10-06 使用仓库 wrapper 在当前 main 源码上重跑聚焦测试，成功通过：

```powershell
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter 'native_vision_domain::tests::review_failure_codes_are_safe_and_do_not_create_review_history' -Exact -NoCapture
```

结果为 `1 passed; 0 failed`。该 wrapper 为测试执行器添加 Common Controls v6 manifest；Cargo 构建和测试进程均成功。此结果覆盖固定前置失败、模拟 provider 连接失败、候选仍未验证及失败不写入 review history，不构成真实 DSH/provider、GUI 或 Run36 数据验收。真实 Agent 是否会依据失败枚举补齐前置并再次主动调用复核，仍需后续运行时验收。F7.G1 视觉验收保持未通过。
