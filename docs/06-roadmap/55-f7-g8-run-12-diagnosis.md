# F7 G8 Run12 冷启动诊断

## 运行与来源

- 隔离 worktree HEAD：`e9b9f8e4836cbc37da75917c6b293fa622dc0ebe`；Rust `mediaType` 修复 `fb045fd` 位于该 HEAD 历史中。
- 冷启动 runner：`native_agent_coldstart_acceptance.mjs --tool-result-diagnostics`，stdout/stderr 定向到私有隔离日志；没有读取日志内容。
- 启动前 runner 与 gates `node --check` 均 exit 0；无位置参数 usage 分支按预期 exit 1，且不创建输出/不读 provider JSON/不触发请求。
- 正式输出路径在启动前经 `Test-Path` 确认不存在，未预建；由 runner 自己创建。本次唯一完整 runner 调用 exit 1，metadata status 为 failed。
- Provider 来源为项目内置 DeepSeek 模板及当前进程 `DEEPSEEK_API_KEY`：`deepseek` / `deepseek-flash` / configured effort `low`。这不代表桌面用户设置；provider JSON 不含 key。
- Node `v22.23.3`；DSH `0.2.0-rc.2`。
- staging `source_manifest_sha256` 与当前 manifest 一致：`39402baa4a84d3d8903e1ea97d8085e78ab047b75a568d4bb50617031a2f9649`。
- 原图 SHA-256：`53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`（72,309 bytes）。用户文本 SHA-256：`eff320ddc00f6db8a190f269d939d3dbf1de4d0f925891538fd7859c70aac416`。
- 脱敏诊断共 103 条结构化事件；未读取 summary、transcript、tool trace、provider response、thought、工具参数或图像数据。

## 严格门禁

| Gate | 结果 | 失败原因（如有） |
|---|---|---|
| `run_scope` | passed | — |
| `dsh` | passed | — |
| `trace_integrity` | passed | — |
| `original_image` | passed | — |
| `main_image_wire` | passed | — |
| `cordis_cad_tools` | failed | `cad_tool_rejected_unbound_or_unaudited`; `cordis_tool_arguments_or_identity_missing_or_unmatched`; `native_calls_not_individually_observed_or_duplicate`; `tool_call_rejected_or_failed`; `acp_cad_tool_failed` |
| `acp_cad_tool_results` | failed | `acp_cad_tool_result_failed`; `acp_cad_tool_result_terminal_update_duplicated` |
| `current_geometry` | passed | — |
| `artifacts` | passed | — |
| `holes` | failed | `holes_incomplete_or_unverified` |
| `visual` | failed | `visual_coverage_incomplete_or_conflicting`; `visual_gate_original_reference_binding_missing` |
| `finish` | failed | `host_finish_not_accepted`; `finish_findings_construction_or_external_facts_incomplete` |
| **Overall** | **failed** | strict acceptance not satisfied |

## 图片结果与工具错误摘要

- `cad_get_reference_image`：10 次 Cordis result 均 `isError=false`；10 个 image block 类型均为 `image/png`；10 次 `native_image_decode` 均 `isError=false`，`attachment_save` 均 `isError=false`。解码字节数依次为 72,309、7,755、17,189、25,625、15,282、9,467、6,056、10,686、2,797、4,262。
- `cad_preview_model`：2 个 Cordis result 均 `isError=false`、image block 类型均为 `image/png`；2 次 native decode 与 `attachment_save` 均 `isError=false`，解码字节数为 33,284 和 30,031。
- 因此本次确认 Rust 图片字段修复消除了 Run10 的图片 decode/attachment 阻断；这只证明 image payload 被识别并保存，不等于视觉验收通过，`visual` gate 仍因覆盖/原图绑定失败。
- 其他失败工具事件仅记白名单投影：`cad_update_feature` 有 1 条 Cordis `isError=true`（error type/code=`other`）；`cad_add_feature` 有 1 条 Cordis `isError=true`（type/code=`other`）；`cad_add_hole_array` 有 1 条 Cordis `isError=true`（安全诊断未提供 type/code）。门禁同时报告 holes incomplete/unverified。未读取错误文本或参数，无法再细分根因。
- `cad_review_reference_similarity` 的 2 条 Cordis result 均 `isError=false`。

## 结论与限制

Run12 实测确认原图与预览图片均成功通过 decode、attachment save 以及 Cordis result，`mediaType` 修复的目标链路有效。整体验收仍失败：Cordis/ACP 工具门禁、holes、visual 与 finish 未通过。`current_geometry` 和 `artifacts` 门禁通过，但本报告未读取几何明细文件，故不进一步声称具体尺寸/孔数量/几何质量。未运行 GUI/CATIA；当前 `geometry` gate 通过不替代 CATIA 实测，`visual` gate 失败也不支持宣称图像验收通过。

## Run12 安全 gate 源码审计补充

在只读取本报告与 strict gate 名称/reasons 的约束下，审查了 ACP result 收集、Rust bridge 配对和 Cordis 安全错误摘要的静态源码。当前验收器用 `tool_call_id` 聚合 ACP 事件；缺少该字段时可能共用 `undefined` key，因此多个终态存在被误报为同一调用重复的边界风险。但 Run12 的允许证据没有调用 ID 是否存在、session/run/turn 关联或每条参数摘要候选匹配数，不能断定该边界是本次 `terminal_update_duplicated` 的实际根因。

Cordis 报告的 `other` 或缺 error type/code 只说明安全白名单没有得到可分类标识；现有证据不能区分 DSH 参数校验、工具结果校验、输出 finalization 或 Rust executor 失败。没有读取错误正文或参数，也不据此扩充 allowlist。

后续离线回归要证明缺 ID 只导致 identity-missing，不额外形成 duplicate；同一有效 ID 的双 terminal 仍必须失败。安全遥测只增加 error/error.info/name/code 存在性布尔值和匹配计数，不写入原始 ID、参数、内容或错误正文。当前问题定位和修复仍在进行中。

## 后续修正：缺 ACP ID 不再误并调用

提交 `3cb635b` 将没有 `tool_call_id` 的 ACP result 事件单独报告为 `acp_cad_tool_call_id_missing`，不再以共同 `undefined` 键归并；同一个有效 ID 的多个 terminal update 仍会触发 duplicate gate。Cordis安全诊断增加 `error_present`、`error_info_present`、`error_name_present`、`error_code_present` 四项存在性布尔值，不保存错误正文。

离线 Node fixtures **55/55 通过**，覆盖缺 ID 的独立拒绝、有效 ID 重复仍被拒绝以及安全字段存在性。此修正确认了门禁的缺失 ID 边界，但不能反向证明 Run12 的 ACP 事件实际缺 ID；需要新冷启动确认生产事件关联。
