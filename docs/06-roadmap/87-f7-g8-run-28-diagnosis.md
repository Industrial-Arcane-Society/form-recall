# F7/G8 Run28：参数预检后的单次冷启动

日期：2026-10-04

## 目标与运行设置

- 固定 Run28 checkout：`C:\Users\lycohana\.codex\worktrees\f7-run28-coldstart\AI-_-`，HEAD `d11ad84af28f41180186657af87f4ba5f4007fdb`。
- 无 GUI、无 CATIA。使用 `native_agent_coldstart_acceptance.mjs`，默认原图与默认 prompt；未注入 CADPlan、尺寸、孔数或额外 userText。
- Runtime：`C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\f1-runtime-probe`。OCCT manifest：`C:\Users\lycohana\.codex\worktrees\f6-clean-target-build\AI-_-\src\apps\desktop\src-tauri\target\f4-occt\runtime-manifest.json`。
- Provider descriptor 沿用 Run26 报告中的 `provider-public.json`；没有读取 descriptor 内容或输出 API key。
- 唯一输出目录：`C:\Users\lycohana\AppData\Local\Temp\f7-g8-run28-20261004-f666acb5221547f3bf9f1a1e6c3fd84e\safe-output`。调用前该 leaf 不存在，只建立其父目录，之后由 runner 创建 leaf。
- `AIMODELER_ACCEPTANCE_DEADLINE_SECONDS=1800`；stdout/stderr 重定向丢弃。runner exit code `1`。summary 报告 runtime Node `v22.23.3`、DSH `0.2.0-rc.2`。

## Run27 输入失败调查与无 provider 预检

静态 runner 顺序显示 `input_validation` 在 `runtime_validation`、offline build、native probe 和 DSH launch 之前。Run27 命令中将图片路径写作未括号的 PowerShell 字符串拼接表达式，和 `process.argv.slice(2)` 的位置参数解析不匹配，足以解释最先读取图片路径时抛出异常；Run27 的安全产物没有更细异常内容，故此归因是本地命令形状分析，不是读取原始日志后的结论。

Run28 前用 pinned Node 执行 `node -e` 本地预检，没有导入/启动 runner、读取 provider descriptor 内容、启动 DSH/provider、创建 scratch 或调用网络。预检将 4 个必需位置参数作为独立参数传入，并省略可选图片与用户文本参数；runner 会因此选择内置默认路径和 prompt。预检输出的固定字段如下：

| 预检项 | 结果 |
|---|---|
| `argument_count` / required arguments | `4` / 全部存在 |
| 图片覆盖参数 / userText 覆盖参数 | 未提供 / 未提供 |
| 默认原图选择 | 是；`72,309` bytes，SHA-256 与固定值匹配 |
| 默认 prompt 分支 | 是 |
| CADPlan/尺寸/孔数 seed 参数数 | `0` |
| Runtime / manifest / descriptor 文件存在性 | 全部存在；descriptor 仅查存在性 |
| 输出 leaf 预先存在 | 否 |
| provider 启动 | 否 |

实际 Run28 的命令形状为：

```powershell
$env:AIMODELER_ACCEPTANCE_DEADLINE_SECONDS = '1800'
$env:AIMODELER_OCCT_MANIFEST = '<clean-target runtime-manifest.json>'
& '<pinned Node 22.23.3 node.exe>' `
  '<Run28 repo>\src\apps\desktop\src-tauri\examples\native_agent_coldstart_acceptance.mjs' `
  '<Run28 repo>' '<pinned runtime root>' '<fresh safe-output leaf>' '<Run26 provider-public.json>' *> $null
```

没有第五个 `imageArg` 或第六个 `userTextArg`，因此不依赖 PowerShell 内联拼接传递图片路径。

## Safe allowlist 结果

仅读取 `summary.json`、`build-status.json` 与 `tool-result-diagnostics.jsonl`。总体为 `status=failed`、`transport_status=completed`、`model_status=candidate`、`failure_reason_count=16`、`evidence_error_count=0`。

| Gate | 结果 | Safe reason codes |
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
| `visual` | failed | `original_reference_not_covered`; `visual_all_references_not_covered`; `visual_finish_coverage_incomplete`; `visual_gate_coverage_incomplete`; `visual_reference_mismatch_detected` |
| `finish` | failed | `finish_acceptance_flag_false`; `finish_domain_state_not_accepted`; `finish_geometry_review_acceptance_false`; `finish_geometry_review_decision_not_accept`; `finish_open_findings_nonzero`; `finish_summary_state_not_accepted`; `finish_unresolved_findings_present` |

Build status `passed`、Cargo exit `0`、`180,173 ms`；stdout 为 0 bytes，stderr 为 9,302 bytes。没有读取构建输出正文。

ACP/Cordis 安全投影显示 `cad_add_hole_array` 有 1 个 completed terminal 和 1 个 failed terminal，另有 1 个 `cad_set_construction_milestone` failed terminal；对应的 `bridge_to_audit` 各有 1 个 `native_audit_rejected`。其余投影列出的 CAD 工具身份绑定/审计匹配。该投影没有工具参数或失败正文，不能解释拒绝的具体内容。

Run28 visual/finish 子条件投影的固定字段显示：

- Visual：`manifest_valid`、`independent_review_present`、原图 inventory 行数、reference ID/SHA binding、inventory validity 与 summary 当前性均 true；但 `finish_coverage`、`all_references_covered`、`host_coverage_complete`、`mismatch_free`、`original_reference_covered` 均 false。
- Finish：host `finished`、review issuer、construction summary、external fact 计数/验证均 true；domain/summary accepted、accepted flag、overall geometry review accepted/decision、无 open finding、无 unresolved finding 均 false。
- `unclassified_failure=false`，没有发现投影无法映射的 gate reason。

## 结论与证据边界

Run28 证明修正后的 CLI 参数形状通过了本地 no-provider 预检，随后单次真实 runner 冷启动完成 DSH transport 并生成 strict gate 结果。整体仍是 candidate/strict failed：geometry 与 holes 通过，Cordis/ACP、visual、finish 失败。新增 projection 精确指出 host summary 的 failed predicates，但不提供 mismatch/finding/tool rejection 的正文，也不构成独立视觉质量或 CATIA 验收。没有读取 raw provider/DSH logs、scratch、工具参数、几何 payload 或任何图片/预览；没有启动 GUI/CATIA；没有重复 Run28。
