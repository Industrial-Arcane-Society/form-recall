# F7/G8 Run33：含 G4 milestone scope 契约的单次 CLI 冷启动

日期：2026-10-05

## 运行条件

- 基线为 `origin/main` / `HEAD 8a907da1cd98844ebf5da49eaf3ec003ba53252d`；该历史包含 G4 修复提交 `3de0afe`。运行前 worktree clean。
- 只从该 worktree 执行一次 `native_agent_coldstart_acceptance.mjs`。CLI 仅传四个必需参数：Run33 repo、pinned DSH runtime、fresh output、provider JSON；省略可选原图和文本参数，没有 CADPlan、尺寸或孔数 seed。
- 无网络、无 provider 的 preflight 通过：CLI `node --check` 成功；Node `v22.23.3`、DSH `0.2.0-rc.2`；provider host 为 `api.siliconflow.cn`、model 为 `moonshotai/Kimi-K2.7-Code`，联网允许且凭据存在性已确认，未打印或记录凭据。
- pinned runtime、provider config 与 OCCT manifest 是主工作区已有的本地运行资源；Run33 使用 worktree 中的 CLI 代码，并未改写或复制这些资源。运行指定 OCCT manifest SHA-256 为 `f4ad33ac7af579440045c8465b9b6e7e4ca0193eeae4a3d4f33782a9e2c4e617`。
- 默认原图为 `72,309` bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，与 Run32 原图一致。默认 prompt 与 Run32 一致：`请根据参考图片复刻模型，未标注尺寸按图像比例自行估算并记录来源，使用工具生成模型并复核。`
- fresh safe-output：`C:\Users\lycohana\AppData\Local\Temp\f7-g8-run33-20261005-8a907da-1343a450`。模型响应 deadline 为 `1,080` 秒；单次 CLI 总耗时 `1,167.9` 秒，exit `1`。没有重试。CLI stdout/stderr 丢弃；safe build projection 只保留 stderr 字节数，没有读取其正文。
- 未启动 GUI/CATIA，未读取 raw provider logs、scratch、几何载荷或模型生成文件；只读取下列 safe allowlist artifacts。

## Safe allowlist 结果

只读取 `summary.json`、`build-status.json`、`tool-result-diagnostics.jsonl`。

| 字段 | 结果 |
|---|---|
| Summary status / transport / model | `failed` / `failed` / `none` |
| Scope | `single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance` |
| DSH diagnostic | `stage=session_rpc`、`method=session/prompt`、`error_class=timeout` |
| Strict summary | `failed`；48 个 failure reasons；0 个 evidence errors |
| Build | `passed`；Cargo exit `0`；82,964 ms；stdout 0 bytes、stderr 9,302 bytes（未读正文） |
| Scratch cleanup | `removed`；recovery status `complete` |

### 13 项 host strict gates

| 状态 | Gates |
|---|---|
| Passed（5） | `run_scope`、`dsh_process`、`trace_integrity`、`original_image`、`main_image_wire` |
| Failed（8） | `dsh_session`、`cordis_cad_tools`、`acp_cad_tool_results`、`current_geometry`、`artifacts`、`holes`、`visual`、`finish` |

`dsh_session` 失败码为 `dsh_session_not_completed`、`dsh_session_rpc_failed`；visual 与 finish 的 host gate 分别有 7、13 个 failed checks。`main_image_wire` 通过只表示 host wire gate 满足，不表示模型理解了图像。Run33 的 `vision_review_projection` 为 `review_count=0`，没有独立视觉 review 结果。

## G4 milestone 工具结果

`acp_terminal_update_projection` 记录的工具只有：

- `cad_get_feature_spec`：3 次开始、3 次 completed。
- `cad_get_state`：1 次开始、1 次 completed。
- `cad_inspect_references`：7 次开始、5 次 completed、2 次 failed。

`cordis_binding_projection` 同样只出现以上三个工具。`cad_get_feature_spec` 与 `cad_get_state` 的 ACP→bridge、bridge→audit 均 matched；`cad_inspect_references` 的 7 次 ACP→bridge 均 matched，bridge→audit 为 5 次 matched、2 次 `native_audit_rejected`（安全错误类别 `argument_validation`）。

本轮没有 `cad_set_construction_milestone` 调用。因此，Run33 没有对 `completed_feature_ids`、`expected_feature_ids` 是否随真实调用提交提供证据；运行时字段和值均不可判定。这不是 G4 通过，也不能把 `cad_inspect_references` 的两个参数验证拒绝归因到 milestone 修复。

## 结论与边界

这次运行停在 `session/prompt` timeout，最终 host 状态为 failed、model 为 none；ACP/Cordis 投影没有建模或 milestone 工具调用，独立 review 计数为 0。几何、工件、孔、visual、finish gates 均失败。本次没有生成可供视觉验收的模型候选，不能判断模型与原图的相似度，也不能宣称 G4 的 milestone scope 传递已实测通过。

projection 只保留固定字段和计数；timeout 的底层原因及两个 `cad_inspect_references` 调用被拒绝的具体参数仍未知。Run33 证明的是一次带默认原图的 DSH CLI host run 在该 baseline 上超时，不能外推成视觉能力结论，也不是 CATIA 验收。

## 后续 runner 时限审计与本地修复（未重跑 provider）

对 Run33 使用的 baseline runner 做静态代码核对，并只依据本文件上方记录的 safe allowlist 结果：当时 `session/prompt` 使用单个 `AIMODELER_ACCEPTANCE_DEADLINE_SECONDS=1080` 请求定时器。定时器既不区分无进展与总时限，也不监听有效 ACP 更新或 CAD 几何 revision 来重置无进展时限；Run33 的模型请求在 1080 秒超时与总 CLI 耗时 1167.9 秒相符（其差值包含后续清理/发布等 runner 工作，但 safe summary 不足以精确拆分）。这能确认旧 runner 的 timeout 策略，**不能**证明当次 provider 停止输出、ACP 消费器卡住，或模型思考过久；本审计未读取 raw logs、scratch、thought、图像/几何载荷，也没有发出 provider 请求。

当前冷启动 runner 已改为与生产 host watchdog 一致的双时限策略：总时限默认 3600 秒、无有效进展时限默认 600 秒。`AIMODELER_ACCEPTANCE_DEADLINE_SECONDS` 兼容为总时限；新的 `AIMODELER_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS` 优先，`AIMODELER_ACCEPTANCE_IDLE_TIMEOUT_SECONDS` 单独配置。仅当前 session 的非空且非重复内容片段、具有调用 ID 的工具调用/更新，以及严格递增几何 revision 可刷新 idle；噪声、其他 session、usage/config 类更新不刷新；hard total 不刷新。到时后先发送 `session/cancel` notification，再以有界 `session/close` 收尾，并沿用受管子进程有界终止。

回归以本地 fake ACP fixture 覆盖 total 不重置、有效 thought 持续更新、错误 session/空片段/未知/usage 噪声、工具终态、几何 revision 和安全摘要字段清洗；不调用 provider。该修复验证的是 runner 时限/进展状态机，Run33 的模型行为、G4 真实运行时及 F7/G8/视觉验收仍需新的独立验收，不能由合成 fixture 替代。

## Safe 文件指纹

| Safe file | Bytes | SHA-256 |
|---|---:|---|
| `summary.json` | 6,094 | `1a638ffb4fe4c580dbf5c39ccc1e3a1f80e15dec63aae3688d561f47174738a2` |
| `build-status.json` | 110 | `83751a98e096181eb69b0dcb873d4f7e66fc549dbf60f09387dfe5f9fe1859db` |
| `tool-result-diagnostics.jsonl` | 2,546 | `b91653dd8e4f3e6caa6f5455119fd839de6700a945396935f1ff146ad5f6e4c7` |
