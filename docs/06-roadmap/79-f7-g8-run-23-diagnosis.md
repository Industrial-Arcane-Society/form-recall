# F7/G8 Run23：safe subcondition cold-start diagnosis

日期：2026-10-04

## 运行条件

- 固定代码：`f718cfc34db9e30d523a52153c7fd93d487b9bfe`，全新 detached worktree `C:\Users\lycohana\.codex\worktrees\f7-run23-coldstart\AI-_-`；运行后 worktree clean。
- 使用默认单张原图与默认 prompt；没有传入 CADPlan、尺寸、孔数或额外 user text。原图 72,309 bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，运行前后未变化。
- Node `v22.23.3`、DSH `0.2.0-rc.2`，使用与 Run22 相同的公开 model/provider descriptor。运行 659.8 秒，runner exit code `1`；Cargo build passed，耗时 88,186 ms。
- 唯一安全输出目录为 `C:\Users\lycohana\AppData\Local\Temp\f7-g8-run23-20261004-141802-b5a5f3f45faf4c6e92fe7c17a6c23505\safe-output`，其中只有 `summary.json`、`build-status.json`、`tool-result-diagnostics.jsonl`。scratch 状态为 `removed`，evidence error count 为 0。
- 本次运行固定在新增 subcondition diagnostics commit；它早于 G5 finding rebind commit `848f521`。因此 Run23 验收新安全诊断对真实 DSH run 的效果，不作为 G5 新代码的真实 provider 验收；G5 的 Rust 单测独立通过。

## 安全门禁结果

总体 `status=failed`、`transport_status=completed`、`model_status=candidate`，严格失败原因数 13。通过：`run_scope`、`dsh_session`、`dsh_process`、`trace_integrity`、`original_image`、`main_image_wire`、`cordis_cad_tools`、`acp_cad_tool_results`、`current_geometry`、`artifacts`、`holes`。失败仅为 `visual` 与 `finish`：

- Visual（6）：`original_reference_not_covered`、`visual_all_references_not_covered`、`visual_finish_coverage_incomplete`、`visual_gate_coverage_incomplete`、`visual_independent_review_missing`、`visual_reference_mismatch_detected`。
- Finish（7）：`finish_acceptance_flag_false`、`finish_domain_state_not_accepted`、`finish_geometry_review_acceptance_false`、`finish_geometry_review_decision_not_accept`、`finish_open_findings_nonzero`、`finish_summary_state_not_accepted`、`finish_unresolved_findings_present`。

DSH diagnostic enum 为 `stage=unknown`、`method=unknown`、`error_class=none`；但 DSH session/process gates 均 passed，故 Run23 不再有 Run22 的 DSH 未完成/进程退出门禁失败。Current geometry 和 holes 均 passed。

图像 wire gate passed，只能证明主模型请求绑定了预期原图且同一请求得到成功响应，不能证明模型正确理解或复刻了图片。该单图 CLI host gate 也不是独立视觉评审或 CATIA 验收。

## 工具调用摘要

ACP 与 Cordis bridge/audit 绑定均为 39/39 matched，无 missing call ID 或拒绝。Allowlist 工具各自的完成数为：`cad_create_model` 1、`cad_add_face_recess` 1、`cad_add_hole_array` 3、`cad_update_feature` 11、`cad_preview_model` 4、`cad_get_reference_image` 5、`cad_inspect_references` 2、`cad_review_reference_similarity` 4、`cad_set_construction_milestone` 4、`cad_get_feature_spec` 2、`cad_get_state` 1、`cad_finish_model` 1。

`cad_preview_model` 有 3 次额外相同参数指纹调用。投影不暴露参数、视角、geometry revision 或调用顺序，因此这只证明同工具参数重复，不能判断它们对应相同视图或没有几何进展。四次 similarity tool calls 也不能单独证明独立复核覆盖充分；visual gates 明确显示覆盖缺失、独立 review 缺失和 mismatch。

## 结论与下一步

Run23 证明新增细分诊断能把 Run22 的聚合失败拆开：真实 run 已完成，原图传输、工具路由与调用身份、当前几何、孔测量均通过；视觉证据/覆盖和 finish findings 未通过。投影记录了一次已完成、ACP/Cordis 身份均匹配的 `cad_finish_model` 调用，所以“Agent 没走到 finish”不再成立为本次解释。仍不能从安全摘要看出哪张图/哪个视图或哪个具体 geometry finding 不匹配。

下一步应沿 `visual` reason-code producer→consumer 追查：四次独立 review 调用为何未生成被 host 接受的完整当前 visual summary，及未解决 finding 如何阻止 finish；以源码和安全结构化字段定位，不能看 raw provider logs、工具参数、预览/几何文件，也不能把 candidate 标成 accepted。若修改视觉工具契约、提示词或 review 归并，再用新固定 commit 和全新目录执行下一次单图冷启动。

## 证据清单

本报告直接依据 runner allowlist 的三份输出；未读取 raw DSH/provider logs、scratch、图片 payload、工具参数或几何/预览文件，未打开 GUI/CATIA。

| 文件 | SHA-256 |
|---|---|
| `summary.json` | `7b02211488ed5f6c739ae8a767d7764751cbf28f94e798d1c560fce0f88c1d0c` |
| `build-status.json` | `03fc89fa18b0fba8667b081a2a1ed6991e7f6032e7ea8b37f6be4bae93cddc2a` |
| `tool-result-diagnostics.jsonl` | `319af5eabcfafab90fb091f349af96f046c1f4aecb951ca8a3c908fce89eb83b` |
