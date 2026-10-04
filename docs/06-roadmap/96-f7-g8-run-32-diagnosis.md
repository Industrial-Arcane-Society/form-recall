# F7/G8 Run32：publication gate-map fallback 单次冷启动

日期：2026-10-05

## 运行条件

- 固定 feature worktree：`feat/f7-safe-publication-gate-fallback`，HEAD `8f422c04cd6d6e8be2ca0c698f62bfe287a32ff5`；运行前 tracked worktree clean。未改 main。
- 只运行一次 `native_agent_coldstart_acceptance.mjs`；仅传入四个必需位置参数，省略可选图片与文本参数，使用原始默认图片及默认 prompt；没有 CADPlan、尺寸、孔数量或 userText seed。
- 默认原图为 72,309 bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，与 runner 固定校验相符。
- 使用隔离安装目录 `target/f6-offline-runtime-audit-20261005/data-current-main/dsh-runtime/current`：Node `v22.23.3`，DSH `0.2.0-rc.2`。provider 配置文件存在且 key 环境变量非空；不读取配置正文或密钥值。
- 设置 OCCT manifest：当前主工作区 `src/apps/desktop/src-tauri/target/release/occt-runtime/runtime-manifest.json`。manifest SHA-256 `7b5551146e4ecf0789a30f0eda57bae626fc9566a46b63e597d4ebe64f8f6e73`；OCCT `7.9.3`，ABI `1`、bridge ABI `1`、upstream tag `V7_9_3` / commit `a016080bf6738d6aeae020badee4e888ad1540a5`。19 个 manifest 引用文件全部存在且 SHA 匹配。此运行所用 native OCCT/Cargo 源码在固定基线 `08885a0` 与当前 main 间无变化。
- `native_read_probe` 与 `native_agent_profile` 先在该固定 feature worktree 按 runner 相同的 offline Cargo 参数新构建；runner 又在 provider 前执行其强制重建。Cargo build projection：passed，exit `0`，3,469 ms，stdout 0 bytes、stderr 72 bytes（未读 stderr 正文）。
- fresh safe-output：`C:\Users\lycohana\AppData\Local\Temp\f7-g8-run32-20261005-8f422c04\safe-output`。Runner deadline 设置为 1,800 秒。外层命令 wrapper 未记录精确进程 start/end 差值；观测耗时约 15 分钟，不能视为精确 wall time。
- CLI stdout/stderr 丢弃。runner exit code：`1`。没有重试。

## Safe allowlist 结果

只读取 `summary.json`、`build-status.json`、`tool-result-diagnostics.jsonl`。

| 字段 | 结果 |
|---|---|
| Summary status | `failed` |
| scope / transport / model | `scope_unverified` / `failed` / `none` |
| failure diagnostic | `stage=publication`，`code=uncaught_exception` |
| strict summary | `failed`，failure reason count `13`，evidence error count `0` |
| gate map | 13 项齐全：9 `passed`、4 `failed`；unknown `0` |
| Build | `passed`，Cargo exit `0`，3,469 ms；stdout `0` bytes、stderr `72` bytes（未读正文） |
| Projection rows | 三类 allowlist projection 均有记录；vision schema `vision_review_diagnostics_v1`，review count `2` |

### 固定 gate 摘要

| Gate | Status | Reason count | Safe reason codes |
|---|---|---:|---|
| `run_scope` | passed | 0 | — |
| `dsh_session` | passed | 0 | — |
| `dsh_process` | passed | 0 | — |
| `trace_integrity` | passed | 0 | — |
| `original_image` | passed | 0 | — |
| `main_image_wire` | passed | 0 | — |
| `cordis_cad_tools` | failed | 3 | `acp_cad_tool_failed`; `cad_tool_rejected_unbound_or_unaudited`; `tool_call_rejected_or_failed` |
| `acp_cad_tool_results` | failed | 1 | `acp_cad_tool_result_failed` |
| `current_geometry` | passed | 0 | — |
| `artifacts` | passed | 0 | — |
| `holes` | passed | 0 | — |
| `visual` | failed | 4 | `original_reference_not_covered`; `visual_all_references_not_covered`; `visual_finish_coverage_incomplete`; `visual_gate_coverage_incomplete` |
| `finish` | failed | 5 | `finish_acceptance_flag_false`; `finish_domain_state_not_accepted`; `finish_geometry_review_acceptance_false`; `finish_geometry_review_decision_not_accept`; `finish_summary_state_not_accepted` |

| Safe file | Bytes | SHA-256 |
|---|---:|---|
| `summary.json` | 2,804 | `716952c98ad0f6dad4e7f83dc3c9a0908bc32cd5c2d85d5082251e6c0a434149` |
| `build-status.json` | 107 | `801a3a85262b6f65cf154772be4e6217cc902f0d6eff0daa2ab25056ef261055` |
| `tool-result-diagnostics.jsonl` | 10,032 | `e9a51bbb59d77811089ba503152ee1df01e84d882d6b6069902ceebe50001a10` |

## 结论与边界

Run32 证明 G8 的 fatal publication fallback 能保留 strict evaluation 已生成的固定 gate map：相较 Run31 的空 map，本次 13 项状态和 allowlisted reason code 均可读。Run32 仍 exit `1`，summary 的 publication-stage `uncaught_exception` 根因未知；不根据该枚举猜测异常位置，也不从 gate 投影重建工具调用细节。Cordis/ACP、visual 与 finish gates 失败；此 projection 是 host gate 状态，不等于独立视觉质量或 CATIA 验收。F7 与 G1 保持未通过。

除上述三份 safe allowlist artifact 外，没有读取 raw trace、provider thought、answer、参数/ID、payload 或 scratch；没有访问 stdout/stderr 正文、没有 GUI/CATIA，也没有第二次运行。
