# F7/G8 Run29：safe error_class projection 后单次冷启动

日期：2026-10-04

## 运行条件与预检

- Worktree：`C:\Users\lycohana\.codex\worktrees\f7-run29-coldstart\AI-_-`，启动时 HEAD `e69076c4885c43ad3171807cc6f72b354d484bb4`，初始工作区 clean；在此创建 `feat/f7-run29-coldstart`。
- 无 GUI、无 CATIA。使用 `native_agent_coldstart_acceptance.mjs`，Run28 同样的四个必需位置参数；省略可选原图及 userText 参数，使用默认原图和默认 prompt。不提供 CADPlan、尺寸、孔数或其他 seed。
- Run28 使用的 pinned runtime 与 OCCT manifest 路径均通过存在性检查；Node `v22.23.3`。DSH 版本由安全摘要报告为 `0.2.0-rc.2`。
- Run26 所记 `provider-public.json` 路径存在。仅检查 descriptor 文件存在性，没有单独读取其内容或输出 API key；runner 按既有流程读取配置以启动 provider。
- 默认原图文件存在，72,309 bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。safe-output leaf `C:\Users\lycohana\AppData\Local\Temp\f7-g8-run29-20261004-8ce3118a7b0242058ea850daa7ac4811\safe-output` 调用前不存在。
- 参数预检确认 argument_count=4、required arguments present、无图片/text 覆盖、默认 prompt 分支、seed 参数数=0。预检中 manifest 环境变量的存在性字段因在设置该变量前读取而显示 false；同一运行前的独立 `Test-Path` 已确认 manifest 文件存在，runner 调用前设置了正确的 `AIMODELER_OCCT_MANIFEST`。该预检字段不作为 manifest 缺失证据。
- 设置 `AIMODELER_ACCEPTANCE_DEADLINE_SECONDS=1800`，stdout/stderr 丢弃；只进行一次 runner 调用，无重试。PowerShell 捕获 runner exit code `1`。safe summary 同时记录 `status=passed` 和 strict gates passed；此 exit code 与摘要状态不一致，保留为未解释的执行边界，不推断原因。

## Safe allowlist 结果

仅读取 `summary.json`、`build-status.json`、`tool-result-diagnostics.jsonl`。摘要报告 `status=passed`、`transport_status=completed`、`model_status=accepted_by_strict_cli_gate`、`failure_reason_count=0`、`evidence_error_count=0`、scratch cleanup `removed`。12 个 gate 全部 passed：run_scope、dsh_session、dsh_process、trace_integrity、original_image、main_image_wire、cordis_cad_tools、acp_cad_tool_results、current_geometry、artifacts、holes、visual、finish。

新增 visual/finish 子条件 projection 的所有检查均为 true。Visual 包括 manifest、independent review、reference ID/SHA binding、original coverage、inventory validity、summary freshness、coverage completeness 与 mismatch-free。Finish 包括 domain/summary accepted、geometry review accepted、construction complete、无 open/unresolved findings，以及外部事实计数和验证状态。

ACP/Cordis safe projection 中列出的 tool call terminal 均为 completed；所列 ACP→bridge 与 bridge→audit outcome 均 matched。此次没有 `native_audit_rejected`，因此运行没有实际触发新的 rejected-audit `error_class` 投影分支，不能宣称真实拒绝诊断获得验证。该投影不包含参数、自由文本、几何载荷或图片。

Build status：passed，Cargo exit `0`，elapsed `77,396 ms`，stdout `0` bytes，stderr `9,302` bytes；未读取构建输出正文。

| Safe 文件 | SHA-256 |
|---|---|
| `summary.json` | `6cd6aebc0b7adbaef92bdb33e0ba92e3d957920759e4f49597350c85cb78766e` |
| `build-status.json` | `694b32e70e005ff13179c09954f7ba81331e26133a11ce977673a6af92656291` |
| `tool-result-diagnostics.jsonl` | `a320384cce87bf4dfb824b8869f902a87fc19e2c15c4475393db9e659a5dc0e3` |

## 结论与证据边界

Run29 的单次真实 DSH/CLI 冷启动安全摘要报告 strict gates 全部通过，包含视觉覆盖与 finish host predicates；这是相较 Run28 strict failed 的可归因运行结果变化。它不能证明独立视觉质量、几何准确性、CATIA 可用性或用户可见桌面体验。运行命令返回 exit code `1` 与摘要 passed 状态的冲突仍未解释，需作为复核项保留。没有读取 raw provider/DSH logs、scratch、工具参数、几何载荷、图片或预览，也没有启动 GUI/CATIA。不得只凭本次 host projection 将整体 F7 标记完成。

针对新增 rejected-audit `error_class` projection 的定向 Node 测试通过：`failed bridge call reports a matching timed native audit rejection without leaking call data`（1/1）。该合成测试不是本次真实运行证据。
