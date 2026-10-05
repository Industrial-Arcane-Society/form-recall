# F7/G8 Run35：main native probe 修复后的单次冷启动

日期：2026-10-05

## 运行条件与边界

- 基线：最新 `origin/main`，固定 HEAD `0e676d84c6bf1d5c9ae2825954576516c6d1bc50`；独立 worktree 分支 `feat/f7-g8-run35-native-diagnostic`，运行前 worktree clean。
- Runner：`native_agent_coldstart_acceptance.mjs`，只运行一次。使用 runner 默认参考图和默认 prompt，省略可选图片/文字参数；没有 CADPlan、尺寸或孔数 seed。原图 `72,309` bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。
- Runtime：Node `v22.23.3`、DSH `0.2.0-rc.2`。本地 provider 配置仅作为 runner 的现有配置文件路径传入，没有复制或输出凭据；联网设定未覆盖。OCCT manifest 通过环境变量指向当前主工作区已有 manifest，SHA-256 `e8c6547f25ce019f7d0d945c9ef84744de7c32dacbbdb8cd424d58ed5a2bdae2`。
- 超时：hard total `3,600` 秒，idle `1,800` 秒。Runner exit code `1`；prompt elapsed 为 `972,867 ms`。stdout/stderr 丢弃；build projection 只记录 stderr 字节数，不读取正文。
- 唯一读取的运行产物为 output tag `f7-g8-run35-20261005-3508824e827b454fa30e2b9b82b32ffe` 下的 `summary.json`、`build-status.json`、`tool-result-diagnostics.jsonl`。没有读取 raw provider log、scratch、工具参数或几何载荷；scratch cleanup 为 `removed`。

## Safe allowlist 结果

| 项目 | 结果 |
|---|---|
| Summary status / scope / transport / model | `failed` / `single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance` / `completed` / `candidate` |
| Native probe | `ready_success`；未在 native probe 阶段失败 |
| Offline build | passed，Cargo exit `0`，`102,853 ms`；stdout `0` bytes，stderr `9,295` bytes（正文未读） |
| Prompt progress | `972,867 ms`；124 个有效 session updates、46 个 tool calls、22 次 geometry revision advance |
| Strict gates | 13 项中 9 passed、4 failed；0 evidence errors |
| Scratch cleanup | `removed`；scratch recovery `complete` |

### Host strict gates

| 状态 | Gates |
|---|---|
| Passed（9） | `run_scope`、`dsh_session`、`dsh_process`、`trace_integrity`、`original_image`、`main_image_wire`、`current_geometry`、`artifacts`、`holes` |
| Failed（4） | `cordis_cad_tools`、`acp_cad_tool_results`、`visual`、`finish` |

`cordis_cad_tools` 的安全原因码为 `acp_cad_tool_failed`、`cad_tool_rejected_unbound_or_unaudited`、`tool_call_rejected_or_failed`；`acp_cad_tool_results` 为 `acp_cad_tool_result_failed`。ACP→bridge projection 中列出的 outcome 均 matched。bridge→audit projection 记录 `cad_add_feature` 1 次 `argument_validation` 拒绝、`cad_inspect_references` 1 次 `execution` 拒绝、`cad_preview_model` 1 次 `bridge_execution_rejected`，以及 `cad_review_reference_similarity` 5 次 `execution` 拒绝。摘要未公开拒绝正文，因此不从这些枚举推断具体调用原因。

`visual` gate 有 6 个失败检查：原图 inventory 无效、原图未被覆盖、所有引用未覆盖、finish coverage 不完整、visual gate coverage 不完整、缺少独立 review。安全 `vision_review_projection` 的 `review_count=0`。`finish` gate 有 5 项未接受检查：domain state、summary state、accepted flag、geometry review accepted、review decision accept。模型候选存在、current geometry 与 holes host gates passed，都不能替代独立视觉判断或 finish acceptance。

## 结论与验收边界

Run35 证明固定 main HEAD 上的 native probe 能到达 `ready_success`，并有一次完整 single-image/single-turn host run；原图和 main-image wire gates passed。Run34 的 `native_probe / uncaught_exception` 阶段已不再复现。当前 G8 仍失败：Cordis/ACP、visual、finish gates 未通过。

模型状态为 `candidate`，但三份 allowlist 产物中没有可供人眼比较的模型预览图或其他单视图 artifact；模型图像/模型文件处于不允许读取的 scratch/几何载荷范围，且 scratch 已清理。因此本记录不冒称完成原图对比、不评为视觉通过，也不依据 host geometry/holes gates 声称几何质量或 CATIA finish 已验收。CATIA 未运行。若后续要做独立单视图验收，需在明确允许的 artifact 流程中单独提供可审阅预览图。

## Safe 文件指纹

| Safe file | Bytes | SHA-256 |
|---|---:|---|
| `summary.json` | 4,980 | `0c297d965fb9d51a504757f20c846cdab4c426abc1b1141de149d5aff3b08511` |
| `build-status.json` | 111 | `e512dcf23b31175e2e44a49dc700908ef8105029704dcfc48f10bea288f4a9d8` |
| `tool-result-diagnostics.jsonl` | 9,373 | `e9c8f9e20fc7ef79a0396d68e10e38267265baa6604260b56fed28805011fc53` |
