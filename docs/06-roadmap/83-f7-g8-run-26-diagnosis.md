# F7/G8 Run26：native audit 拒绝诊断修复后的冷启动

日期：2026-10-04

## 运行条件

- 固定代码 commit：`a10795885cbf4a402f7d447d98035fec6981ce69`。使用新 detached worktree `C:\Users\lycohana\.codex\worktrees\f7-run26-coldstart`，运行时 HEAD 固定为该 SHA。
- 采用项目默认原图和 runner 默认提示词“请根据参考图片复刻模型，未标注尺寸按图像比例自行估算并记录来源，使用工具生成模型并复核。”未提供 CADPlan、尺寸、孔数或额外 `userText`。
- 原图路径 `temp/evidence-archive/artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`；运行前 SHA-256 与 summary 均为 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，summary 记录 `unchanged=true`。
- 复用 Run24 的公开 provider descriptor 路径；仅检查 `DEEPSEEK_API_KEY` 环境变量存在性，未读取或记录密钥。Runner 使用固定 Node `v22.23.3`、DSH `0.2.0-rc.2`。stdout/stderr 重定向到 `$null`。
- 新 safe-output 父目录由 launcher 创建，`safe-output` leaf 在调用前确认不存在，由 runner 自行创建。只运行一次。
- 开始 `2026-10-04T08:15:43.9558306Z`，结束 `2026-10-04T08:24:00.6614571Z`，耗时 `496.706` 秒；runner exit code `1`。

## 调用参数

```powershell
$env:AIMODELER_ACCEPTANCE_DEADLINE_SECONDS = '1800'
$env:AIMODELER_OCCT_MANIFEST = 'C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\package-build-input-20261004\runtime-manifest.json'
& 'C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\f1-runtime-probe\node\node.exe' `
  'C:\Users\lycohana\.codex\worktrees\f7-run26-coldstart\src\apps\desktop\src-tauri\examples\native_agent_coldstart_acceptance.mjs' `
  'C:\Users\lycohana\.codex\worktrees\f7-run26-coldstart' `
  'C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\f1-runtime-probe' `
  'C:\Users\lycohana\AppData\Local\Temp\f7-g8-run26-20261004-161543-8e23851dd5f1\safe-output' `
  'C:\Users\lycohana\AppData\Local\Temp\f7-g8-run24-20261004-150844-438225d3cee540eea60314bf5ce860cc\provider-public.json' `
  'C:\Users\lycohana\.codex\worktrees\f7-run26-coldstart\temp\evidence-archive\artifacts\agent-quality-rebuild\references\user-nine-hole-recess-iso.png' *> $null
```

## 严格门禁结果

总体 `status=failed`、`transport_status=completed`、`model_status=candidate`、`failure_reason_count=12`、`evidence_error_count=0`。运行范围为单图单轮 CLI host gate，不代表独立视觉验收或 CATIA 验收。

| Gate | 状态 | allowlisted reason codes |
|---|---|---|
| `run_scope` | passed | — |
| `dsh_session` | passed | — |
| `dsh_process` | passed | — |
| `trace_integrity` | passed | — |
| `original_image` | passed | — |
| `main_image_wire` | passed | — |
| `cordis_cad_tools` | passed | — |
| `acp_cad_tool_results` | passed | — |
| `current_geometry` | passed | — |
| `artifacts` | passed | — |
| `holes` | passed | — |
| `visual` | failed | `original_reference_not_covered`; `visual_all_references_not_covered`; `visual_finish_coverage_incomplete`; `visual_gate_coverage_incomplete`; `visual_reference_mismatch_detected` |
| `finish` | failed | `finish_acceptance_flag_false`; `finish_domain_state_not_accepted`; `finish_geometry_review_acceptance_false`; `finish_geometry_review_decision_not_accept`; `finish_open_findings_nonzero`; `finish_summary_state_not_accepted`; `finish_unresolved_findings_present` |

安全 Cordis/ACP projection 对 `cad_add_hole_array` 记录 2 个 ACP terminal `completed`、2 次 `acp_to_bridge: matched`、2 次 `bridge_to_audit: matched`；该工具没有 `bridge_execution_rejected`、`native_audit_rejected` 或 audit missing 结果。因此本轮确认此工具的两次调用完成了 ACP、桥接和 native audit 匹配，但没有触发失败 audit 诊断分支。`dsh_diagnostic` 为 `stage=unknown`、`method=unknown`、`error_class=none`。

## 安全产物与哈希

安全输出目录：`C:\Users\lycohana\AppData\Local\Temp\f7-g8-run26-20261004-161543-8e23851dd5f1\safe-output`。只读取 runner allowlist 的三份文件，未读取目录内其他内容：

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `summary.json` | 2,988 | `81230408ba8598a9b02f7c9794f46dd51f335657776dbc4fbabd924f306e3f9d` |
| `build-status.json` | 110 | `366263a5197066ff389ac3c521cbfb9c4171ef96d3f7c117a7348e793fe481ed` |
| `tool-result-diagnostics.jsonl` | 7,297 | `d890516c7771ad45ad31f5678429625e83d4d07b7ac7a983766219333f95319f` |

安全 build status：`passed`、Cargo exit `0`、`80,038 ms`；stdout `0` bytes、stderr `9,296` bytes。没有读取构建输出正文。

## 结论与证据边界

本轮冷启动通过 DSH session/process、原图传输、Cordis/ACP 工具链、current geometry、holes 等门禁；整体仍因 visual 和 finish 两组门禁失败而未被 strict 接受。Run26 没有 `cad_add_hole_array` audit 拒绝，所以既不能验证 `native_audit_rejected` 分支，也没有观察到桥接拒绝。五条 visual reason codes 是 host 门禁结果，不构成本报告对图片质量或模型几何的独立评价。

没有读取 raw provider/DSH logs、scratch、工具参数、模型 answer/thought、几何 payload 或预览图。Run25 的输出目录冲突已避开；Run26 结果不解释 Run25 内部调用失败的具体阶段。
