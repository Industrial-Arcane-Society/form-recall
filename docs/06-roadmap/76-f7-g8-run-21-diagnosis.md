# F7 G8 Run21 冷启动诊断

日期：2026-10-04

## 运行边界与预检

- 从集成基线 `8ca53d2f31984e8e441b18ad6a90594b7d379a4f` 创建全新 detached worktree `C:\Users\lycohana\.codex\worktrees\f7-run21\AI-_-`。运行前和构建失败后、文档修改前，HEAD 均为该 SHA，工作树 clean。未改产品代码。
- 按 Run20 与 runner 文档，使用项目默认原图和默认 prompt；没有传图片路径参数、可选 `userText`、CADPlan、尺寸、孔数或答案。运行前独立核对默认原图为 `72,309 bytes`，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。本轮安全 summary 的 `original_image` 为 `null`，因此不把这项预检当作运行期绑定或传输证据。
- 复用另一 checkout 的固定运行时与 OCCT manifest：Node `v22.23.3`、DSH `0.2.0-rc.2`；Runner 实际 Cargo rebuild 在 Run21 detached worktree 内执行。只检查到 `DEEPSEEK_API_KEY` 环境变量存在，没有读取或输出其值。临时 provider descriptor 只含公开设置 `https://api.deepseek.com/v1`、`deepseek-flash`、`low`，不含密钥。
- `AIMODELER_ACCEPTANCE_DEADLINE_SECONDS=1800`。只启动一次；未重试、未启动 GUI/CATIA。运行在离线构建失败处退出，未进入 native probe、DSH session 或 prompt，因此 1800 秒模型请求期限未开始计时。
- runner 安全输出位于 `C:\Users\lycohana\AppData\Local\Temp\f7-g8-run21-53da782cd44b4725b850e0315cb094f4\safe-output\`。审阅仅读取 allowlist 中的 `summary.json` 与 `build-status.json`；`tool-result-diagnostics.jsonl` 不存在。没有读取 scratch、stderr 正文、provider/raw logs、ACP 事件、工具参数或模型内容。

报告脚本在 runner 结束后因 PowerShell 元数据对象中布尔值拼写错误而未能输出其捕获的 `$LASTEXITCODE`。runner 的 fatal path 对该失败会以 exit 1 结束，这是按脚本控制流推断，未作为独立捕获的 exit code 报告。运行区间约 `58.3 s`，按临时根目录创建时间 `2026-10-04T04:55:32.9128772Z` 至安全 summary 最后写入时间 `2026-10-04T04:56:31.1637772Z` 估算；准确 Cargo build 计时由安全 build status 给出。

## 允许产物

| Artifact | 结果 |
|---|---|
| `summary.json` | 存在；679 bytes；SHA-256 `9548eb7345c45f18873a5a78f58ef5e4a5d503c38348e4a991645bf90ede9c70` |
| `build-status.json` | 存在；113 bytes；SHA-256 `633f3b1775d5fc660b8d0766bd7d7323bf2513c4988277034fff7894f95901f4` |
| `tool-result-diagnostics.jsonl` | 缺失；没有 ACP terminal-update 或 Cordis binding projection |

`build-status.json` 仅发布固定安全字段：`status=failed`、`exit_code=101`、`elapsed_ms=57794`、`stdout_bytes=0`、`stderr_bytes=10525`。没有读取 stderr 正文，因此 Cargo 失败的具体错误、根因和可修复项仍为 unknown。新增安全 build status 使 Run21 能确认 Cargo 命令非零退出；它不提供失败文本，也不能据字节数推断原因。

`summary.json` 的相关固定字段：

| 字段 | 值 |
|---|---|
| `status` / `strict_gates.status` | `failed` / `failed` |
| `status_scope` | `scope_unverified` |
| `transport_status` / `model_status` | `failed` / `none` |
| `strict_gates.failure_reason_count` / `evidence_error_count` | `1` / `0` |
| `strict_gates.gates` | 空 map；没有逐 gate 结果或 reason code |
| `failure_diagnostic.stage` / `code` | `offline_build` / `uncaught_exception` |
| `runtime.node` / `runtime.dsh` | `unverified` / `unverified` |
| `original_image` | `null` |
| `scratch_cleanup_status` | `removed` |

`offline_build` 是 runner 的粗阶段，不指出 Cargo 内部失败点。runner 在 build status 不是 `passed` 时立即进入 fatal path；native probe 与 DSH 进程在该代码路径之后。因此本次没有发起 provider 请求；没有任何 provider request/response 证据可审阅。此结论来自 runner 分支与 safe failure summary，不是模型/网络 gate 的通过结论。

## Gate 结论

| Gate | Run21 结论 | 证据边界 |
|---|---|---|
| `run_scope` | 未执行 / unknown | 没有 runtime session/turn manifest 或 gate map |
| `dsh` | 未执行 / unknown | 未启动 DSH |
| `trace_integrity` | 未执行 / unknown | 没有 ACP trace；projection 未发布 |
| `original_image` | 未执行 / unknown | 预检源图 SHA 匹配，但 summary 未发布运行期 image evidence |
| `main_image_wire` | 未执行 / unknown | 离线构建前失败；没有 provider request/response evidence |
| `cordis_cad_tools` | 未执行 / unknown | 没有 DSH/Cordis 调用或 projection |
| `acp_cad_tool_results` | 未执行 / unknown | 没有 ACP tool result 或 projection |
| `current_geometry` | 未执行 / unknown | 没有本轮构建状态、geometry postcheck 或候选几何 |
| `artifacts` | 未执行 / unknown | 没有本轮 CAD artifacts |
| `holes` | 未执行 / unknown | 没有建模或孔测量结果 |
| `visual` | 未执行 / unknown | 没有原图传输或视觉复核 evidence |
| `finish` | 未执行 / unknown | 没有 finish/review gate evidence |

## 结论与下一步

Run21 确认固定集成基线上的离线 Cargo rebuild 以 exit code `101` 失败，耗时 `57.794 s`，runner 未进入 provider 阶段。safe summary 与新 `build-status.json` 均按 allowlist 发布；运行输出未泄露 raw provider logs 或 Cargo stderr 正文。F7/G8 的真实多模态验收没有发生，所有 strict gate 均未执行；不能把失败归结为 Agent、schema、视觉理解、几何或 CATIA 问题，也不能宣称任何 gate 通过。

下一步静态审查当前 Rust/Cargo offline build 所需依赖、工具链和隔离 worktree 的可用缓存；不要为解释本次结果读取未列入 allowlist 的 Cargo stderr 或私有 scratch。若要重新进行 provider 验收，应先处理并记录有证据支持的构建原因，再使用新的 detached worktree 和全新 output dir 单次运行。Run21 输出不回填或复用；F7 保持失败。
