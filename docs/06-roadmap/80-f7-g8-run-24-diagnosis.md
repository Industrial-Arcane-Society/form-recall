# F7/G8 Run24：review fingerprint 修复后的真实冷启动诊断

日期：2026-10-04

## 运行条件

- 固定代码 commit：`c851967bf0cc3d56d1a12b6640a83d66cfdc2656`。使用全新 detached worktree `C:\Users\lycohana\.codex\worktrees\f7-run24-coldstart`；运行前后 HEAD 固定且工作树干净。
- 使用项目默认原始图片和 runner 默认提示词 `请根据参考图片复刻模型，未标注尺寸按图像比例自行估算并记录来源，使用工具生成模型并复核。`；没有传 CADPlan、尺寸、孔数或额外 `userText`。原图 `temp/evidence-archive/artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png` 为 72,309 bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。Runner 报告 `unchanged=true`。
- 固定 Node `v22.23.3`、DSH `0.2.0-rc.2`，公开 provider descriptor 为 `https://api.deepseek.com/v1` / `deepseek-flash` / `low`。`DEEPSEEK_API_KEY` 环境变量存在；没有读取、记录或打印密钥值。
- 使用完整 1800 秒 runner deadline，仅启动一次，没有重试、GUI 或 CATIA。Wrapper 捕获的运行区间为 `2026-10-04T07:08:44.0639376Z` 至 `2026-10-04T07:17:01.2872565Z`，Stopwatch 耗时 `497,217 ms`（约 `497.2 s`），runner exit code `1`。
- 冷启动命令参数如下；命令没有最后一个可选 `userText` 参数。provider 文件仅含公开设置，不含密钥：

```powershell
$env:AIMODELER_ACCEPTANCE_DEADLINE_SECONDS = '1800'
$env:AIMODELER_OCCT_MANIFEST = 'C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\package-build-input-20261004\runtime-manifest.json'
& 'C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\f1-runtime-probe\node\node.exe' `
  'C:\Users\lycohana\.codex\worktrees\f7-run24-coldstart\src\apps\desktop\src-tauri\examples\native_agent_coldstart_acceptance.mjs' `
  'C:\Users\lycohana\.codex\worktrees\f7-run24-coldstart' `
  'C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\f1-runtime-probe' `
  'C:\Users\lycohana\AppData\Local\Temp\f7-g8-run24-20261004-150844-438225d3cee540eea60314bf5ce860cc\safe-output' `
  'C:\Users\lycohana\AppData\Local\Temp\f7-g8-run24-20261004-150844-438225d3cee540eea60314bf5ce860cc\provider-public.json' `
  'C:\Users\lycohana\.codex\worktrees\f7-run24-coldstart\temp\evidence-archive\artifacts\agent-quality-rebuild\references\user-nine-hole-recess-iso.png'
```

## 安全门禁结果

总体 `status=failed`、`transport_status=completed`、`model_status=candidate`、`failure_reason_count=11`、`evidence_error_count=0`；scratch 清理状态为 `removed`。构建状态为 `passed`，Cargo exit code `0`，耗时 `78,442 ms`。构建 stderr 字节数记录为 9,296；没有读取 stdout/stderr 正文。

Run24 全部 13 个 safe gate 状态与 allowlisted reason codes：

| Gate | 状态 | allowlisted reason codes |
|---|---|---|
| `run_scope` | passed | — |
| `dsh_session` | passed | — |
| `dsh_process` | passed | — |
| `trace_integrity` | passed | — |
| `original_image` | passed | — |
| `main_image_wire` | passed | — |
| `cordis_cad_tools` | failed | `acp_cad_tool_failed`; `cad_tool_rejected_unbound_or_unaudited`; `tool_call_rejected_or_failed` |
| `acp_cad_tool_results` | failed | `acp_cad_tool_result_failed` |
| `current_geometry` | passed | — |
| `artifacts` | passed | — |
| `holes` | passed | — |
| `visual` | passed | — |
| `finish` | failed | `finish_acceptance_flag_false`; `finish_domain_state_not_accepted`; `finish_geometry_review_acceptance_false`; `finish_geometry_review_decision_not_accept`; `finish_open_findings_nonzero`; `finish_summary_state_not_accepted`; `finish_unresolved_findings_present` |

关键变化是 `visual` gate 本次通过且没有失败原因码；Run23 在修复前该 gate 曾报告覆盖不足、独立复核缺失和 reference mismatch。Run24 因此验证了 canonical user-target fingerprint 修复后，这次真实 DSH 冷启动能够生成并被 host 接受为当前 visual summary。`main_image_wire` 同样通过，说明主请求绑定了预期原图并成功返回。两者都不证明模型对图片的语义理解、形状相似度或尺寸准确性；本 runner 的范围不包含独立人工视觉评审或 CATIA 验收。

Run24 仍没有被 strict gate 接受。安全投影记录一次 `cad_finish_model` terminal `completed`，但 `finish` 的 7 条原因码表明 acceptance 状态仍为 candidate，并有 open/unresolved findings。ACP terminal projection 对 `cad_add_hole_array` 记录 3 个 completed、3 个 failed terminal。Cordis binding projection 对同一 allowlisted tool 汇总 `acp_to_bridge: matched=6`，`bridge_to_audit: matched=3, bridge_execution_rejected=3`；所有工具合计 ACP→bridge 34 个 matched、bridge→audit 31 个 matched/3 个 rejected。`bridge_execution_rejected` 是本次拒绝的公开 allowlisted bridge result code。摘要不含参数或逐调用关联细节，不能判断哪个输入/操作导致拒绝。缺失 call ID 事件数为 0。

后续对固定源码的只读复核发现，旧版 projection 在 `call.success !== true` 时会先输出 `bridge_execution_rejected`，可能短路同一调用的失败 native audit。此缺陷只说明 Run24 的公开原因码可能不够具体；Run24 历史产物无法据此重新分类，也不能判断具体孔参数或拒绝尺寸。修复提交 `4d0bd26` 在匹配到同工具、同调用 ID、运行时间范围内的失败 audit 时公开 `native_audit_rejected`；无匹配 audit 或 audit 成功时保留 `bridge_execution_rejected`。修复只通过合成回归验证，不能视为 Run24 根因证明；实现与边界见[诊断修复记录](./81-f7-g8-bridge-audit-rejection-diagnostic.md)。

## 允许读取的产物

唯一诊断目录：`C:\Users\lycohana\AppData\Local\Temp\f7-g8-run24-20261004-150844-438225d3cee540eea60314bf5ce860cc\safe-output`。仅读取 runner allowlist 的 `summary.json`、`build-status.json`、`tool-result-diagnostics.jsonl`；没有读取 raw provider/DSH logs、scratch、工具参数、模型 answer/thought、几何 payload 或预览图片。

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `summary.json` | 2,932 | `bb607a4781d5cb9a3fe94e24d676274f135023d2fbfc44394cb68008f7f69034` |
| `build-status.json` | 110 | `b62b6635641e72198e3dcaf62ff0635feac8418bafc8373ac2cb021ab0c117a5` |
| `tool-result-diagnostics.jsonl` | 8,461 | `8b4f928560ccf3d6d93396c49baef90e9677410380a71017a6ed1e229fe27c40` |

## 结论与下一步

Run24 的真实单图 DSH 冷启动支持了 review fingerprint 修复的目标：视觉 summary gate 从 Run23 的失败变为通过，且 Run24 在 `current_geometry`、`holes` 和原图 wire gate 上通过。严格验收仍失败，当前可由 allowlist 确认的问题集中在三次 `cad_add_hole_array` 被拒绝/失败，以及 finish 因未接受状态和 open/unresolved findings 而未完成验收；安全投影不含足够上下文来说明调用参数或 finding 内容。

下一步在包含 projection 修复的新固定 commit 上，以全新 output 执行真实冷启动，确认是否有 `native_audit_rejected` 记录，再按 hole-array 工具 producer → Rust bridge → ACP terminal 的安全契约继续追查失败工具结果如何进入 findings/finish。不可读取原始调用参数、raw provider logs 或几何负载，也不能因 visual gate 通过就把候选标记为 accepted。Run24 是修复前的历史记录，不能被新 projection 追溯分类。F7/G8 仍未完成。
