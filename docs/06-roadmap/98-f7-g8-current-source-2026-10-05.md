# F7.G8：`27d4657` 当前源码多模态 CLI 复测

日期：2026-10-05
源码基线：`27d46570149df6254a6eac3f23487cf17555b58d`（当时 `main` 与 `origin/main`）

## 本轮输入与边界

- 使用 pinned Node `v22.23.3` 与 DSH `0.2.0-rc.2`，并从该基线源码重新构建 Release CLI runner。Release build exit `0`，耗时 `4,408 ms`；stdout 为空，stderr 为 72 bytes，未读取 stderr 正文。
- 使用 CLI 默认原图和 prompt，只有四个必需位置参数；没有传图片或文本 override，没有提供 CADPlan、尺寸或孔数 seed。原图为 `72,309` bytes，运行前后保持不变。
- 使用现有本机 SiliconFlow profile。安全投影记录所选 host `api.siliconflow.cn`、model `moonshotai/Kimi-K2.7-Code`、online enabled。执行者曾仅把 profile 的 `api_key` 字段用于非空布尔检查；密钥值没有打印、写入报告或作为参数传递，runner 直接读取原始 profile 文件。
- 单次 runner 用时 `1,431.3 s`，exit `1`，transport=`completed`，model=`candidate`，strict failure reason count=`13`，evidence errors=`0`，scratch cleanup=`removed`。
- scope 为 `single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance`。本轮没有启动 GUI 或 CATIA，也不构成 CATIA 几何验收。

## Strict gate map

| 结果 | Gates |
|---|---|
| Passed | `run_scope`、`dsh_session`、`dsh_process`、`trace_integrity`、`original_image`、`main_image_wire`、`current_geometry`、`artifacts`、`holes` |
| Failed | `cordis_cad_tools`、`acp_cad_tool_results`、`visual`、`finish` |

安全失败原因码：

- `cordis_cad_tools`：`acp_cad_tool_failed`、`cad_tool_rejected_unbound_or_unaudited`、`tool_call_rejected_or_failed`。
- `acp_cad_tool_results`：`acp_cad_tool_result_failed`。
- `visual`：`original_reference_not_covered`、`visual_all_references_not_covered`、`visual_finish_coverage_incomplete`、`visual_gate_coverage_incomplete`。
- `finish`：`finish_acceptance_flag_false`、`finish_domain_state_not_accepted`、`finish_geometry_review_acceptance_false`、`finish_geometry_review_decision_not_accept`、`finish_summary_state_not_accepted`。

## 失败定位能说明什么

- Cordis 投影中多数 ACP→bridge 和 bridge→audit 计数匹配。例外为 `cad_inspect_references` 一次 `native_audit_rejected`，以及 `cad_set_construction_milestone` 一次 `bridge_execution_rejected`。ACP 投影分别记录这两个调用一次 failed terminal update；`missing_call_id_events=0`。安全摘要没有参数或详细异常，所以无法进一步断言拒绝的具体输入或生产代码根因。
- 视觉投影记录 4 次 review。每次 `required=8`、`mapped=0`、`unmapped=1`、`missing=8`、coverage=`partial`。独立 review、manifest、mismatch、inventory 和原图绑定检查通过，但实际视图未映射到所需参考项。因此视觉 gate 失败不能被解释为模型与原图“相似”或“不相似”，也不能作为修模方向。
- Finish 的 5 个 acceptance 子条件失败；host finished、construction complete、无 open/unresolved findings、external facts verified 等检查通过。Candidate 不能据此升级为已接受模型。
- safe summary 没有 `failure_diagnostic`。正常发布路径写完 safe artifacts 后将 `failurePhase` 设为 `runner_completion`；最终 summary 为 failed，runner exit `1` 与 summary 一致。本轮未复现 Run32 的 publication fatal，也没有证据表明这是崩溃。

## 结论与下一步

本轮确认 runner 可以用当前源码启动 DSH、发送原图并生成 candidate 与 safe artifacts；但工具链审计接受、ACP 工具结果、参考视图映射和 finish acceptance 仍未通过。没有充分证据把问题归咎于模型提示词、CADPlan 转译器或几何内核中的某一个；本轮也未改代码。F7/G1 仍保持未通过。

下一步应先为 `cad_inspect_references` / `cad_set_construction_milestone` 的 audit/bridge 拒绝补充可安全检查的定位证据，并修复 visual review 到参考图/模型单视图的映射，使 `mapped` 不再为 0；之后再用同一默认输入冷启动复测，不能把当前 gate 结果当作独立视觉审查结论。

## 安全输出指纹

本轮只读取 allowlist safe-output，不读取 raw logs、scratch 或模型载荷。安全文件 SHA-256：

- `summary.json`: `928bc7fe119f107f58126190adf89914aa34b60782dc7b69e55ec3c08e24e67c`
- `build-status.json`: `93eaab029e669cf60891bb06f15cba833df82fbcee5d18e1f966c528b7a212f6`
- `tool-result-diagnostics.jsonl`: `c654518242df26bbfb3108f46574b15383bd2e56801bb43792bd44d6652a084f`
