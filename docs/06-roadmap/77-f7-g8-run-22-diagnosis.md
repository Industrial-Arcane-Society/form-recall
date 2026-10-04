# F7 G8 Run22 冷启动诊断

日期：2026-10-04

## 运行边界与静态预检

- 从集成 HEAD `d41f2c62baa54f2597ff1831ee108543543b76c5` 创建全新 detached worktree `C:\Users\lycohana\.codex\worktrees\f7-run22\AI-_-`。运行前确认干净、HEAD 匹配；运行后仍在同一 HEAD 且 clean。没有修改产品代码。
- 冷启动前 `src/apps/desktop/src-tauri/target` 不存在，因而 `target/package-resources/app` 不存在。运行后该目标也不存在；Cargo build 成功。
- 静态审查 `native_agent_coldstart_build.mjs`：`buildCargoEnvironment()` 克隆子进程环境、解析 `TAURI_CONFIG` JSON 对象，复制顶层配置及 `bundle`，只将 child config 的 `bundle.resources` 替换成 `[]`，其余字段保留；不会修改 parent environment。本次启动 shell 没有 `TAURI_CONFIG` override，所以 child overlay 仅提供 `bundle.resources=[]`，Tauri 源配置继续提供 `productName` 与 `build.devUrl`。仓库现有冷启动回归 fixture 显式提供这两个字段并断言其值在 override 后保留，同时断言 source environment 未变；本次仅静态阅读了该 fixture，没有运行测试。源配置值为 `productName=AI Modeler`、`build.devUrl=http://127.0.0.1:1420`。
- 使用项目默认原图和 prompt；没有传图片路径参数、可选 `userText`、CADPlan、尺寸、孔数或答案。运行前图像为 `72,309 bytes`，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。
- 复用另一 checkout 的固定运行时与 OCCT manifest：Node `v22.23.3`、DSH `0.2.0-rc.2`。只检查到 `DEEPSEEK_API_KEY` 环境变量存在，没有读取或输出值。临时 provider descriptor 只包含公开设置 `https://api.deepseek.com/v1`、`deepseek-flash`、`low`。
- 设置 `AIMODELER_ACCEPTANCE_DEADLINE_SECONDS=1800`。仅运行一次；无重试、GUI 或 CATIA。

## 运行结果与允许产物

runner 于 `2026-10-04T05:27:23.4653088Z` 开始，`2026-10-04T05:41:04.0992212Z` 结束，耗时 `820.6 s`；直接捕获的 runner exit code 为 `1`。构建前预检与构建后检查均确认 `target/package-resources/app` 不存在。

安全输出目录为 `C:\Users\lycohana\AppData\Local\Temp\f7-g8-run22-aefa5da45f3e40f589a179056ab5c35b\safe-output\`。仅读取 allowlist 中的三个文件：

| Artifact | 大小 | SHA-256 |
|---|---:|---|
| `summary.json` | 2,713 bytes | `e79ac2fc154cad132aac63abb7dd24c2500892fc954a9bdf54239fa47da5d8be` |
| `build-status.json` | 111 bytes | `1ffb8f9a501b90c29a0a4b768985fac385d61484876c468395a6ca05ffddb227` |
| `tool-result-diagnostics.jsonl` | 6,309 bytes | `9403d3dffbadb82b2c03a600a4561b4b1daa2af7cf7ed1452f3ff46151fb89db` |

`build-status.json` 记录 `status=passed`、`exit_code=0`、`elapsed_ms=198262`、`stdout_bytes=0`、`stderr_bytes=9292`。Cargo 成功，未读取构建 stdout/stderr 正文。summary 记录 Node/DSH 版本正确、`scratch_cleanup_status=removed`、scratch recovery 为 complete 且各计数为 0。

summary 的总体字段：`status=failed`、`status_scope=single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance`、`transport_status=failed`、`model_status=candidate`、strict `failure_reason_count=9`、`evidence_error_count=0`。

| Gate | 状态 | 固定 reason code |
|---|---|---|
| `run_scope` | passed | — |
| `dsh` | failed | `dsh_not_completed_or_nonzero_exit` |
| `trace_integrity` | passed | — |
| `original_image` | passed | — |
| `main_image_wire` | passed | — |
| `cordis_cad_tools` | passed | — |
| `acp_cad_tool_results` | passed | — |
| `current_geometry` | failed | `geometry_attestation_or_gate_binding_invalid`; `preview_geometry_finish_revision_or_fingerprint_stale` |
| `artifacts` | passed | — |
| `holes` | failed | `holes_incomplete_or_unverified` |
| `visual` | failed | `visual_coverage_incomplete_or_conflicting`; `visual_evidence_not_current_rust_summary`; `visual_gate_original_reference_binding_missing` |
| `finish` | failed | `finish_findings_construction_or_external_facts_incomplete`; `host_finish_not_accepted` |

`original_image` 发布 `bytes=72309`、上述 SHA 与 `unchanged=true`。`main_image_wire=passed` 表示 runner 的严格检查接受了本轮主模型请求的原图绑定以及同一请求的成功完整响应；这是 safe gate 级别的 provider 请求证据，不包含 request ID、payload 或响应正文。由此可确认这轮实际进入了 provider wire 请求路径，但不能从 summary 得出模型正确理解了原图。

`tool-result-diagnostics.jsonl` 只含两个固定 allowlist projection：

- ACP terminal projection：`missing_tool_call_id_event_count=0`、`same_terminal_event_pair_count=0`、`different_terminal_event_pair_count=8`、`terminal_update_hmac_unavailable_event_count=0`。所列工具组的终态均为 completed；部分组记录为 `repeated_same_status_distinct_updates`。这些计数本身没有参数或内容，不构成失败原因解释。
- Cordis binding projection：所有列出的调用在 `acp_to_bridge` 和 `bridge_to_audit` 两阶段均为 `matched`；每阶段合计 24 次匹配，无列出的拒绝、缺失或未绑定 outcome。对应严格 `cordis_cad_tools` 与 `acp_cad_tool_results` gate 均通过。

未读取 Run21 的 stderr/scratch，也未读取 Run22 的 raw provider logs、ACP 原始事件、工具参数、几何文件或密钥。没有启动 GUI/CATIA。

## 结论与待确认事实

Run22 验证了离线构建环境修复：在全新 worktree 的 release-only `target/package-resources/app` 不存在时，runner 子 Cargo 配置覆盖 `bundle.resources` 后 build 通过。真实单图 provider 运行随后提供了安全 wire gate 证据，Cordis/ACP 工具 gate 也通过；但 DSH gate 失败，且 geometry、holes、visual 和 finish gate 未通过，strict 整体失败。F7/G8 仍未通过。

safe reason codes 不能确定 DSH 未完成的具体异常点，也不能说明 geometry attestation/revision fingerprint 为何不一致、孔为何未验证、哪些 visual evidence 冲突或 finish 中哪些 finding/施工/外部事实未满足。上述根因仍 unknown；视觉质量及 CATIA 兼容性没有被本次 CLI gate 验收。下一步依据这些固定码和静态源代码检查调用/状态更新及证据绑定，不读 raw logs。若进行新的真实 provider run，需使用新的 detached worktree 与全新 output dir。
