# Agent CLI 启动状态审计（2026-10-01）

## 结论

**可观测事实：** PID 23464 是 `python -m ai_modeler agent ...`；其子进程 PID 41204 是 `deepseek-harness-sdk-runtime-win-x64.exe --profile sdk --patch ...runtime.patch.yml`，runtime 再派生 Python MCP server PID 27200。当前会话目录为 `C:\Users\lycohana\AppData\Local\AI Modeler\dsh\sessions\cold-nine-hole-20261001-202252-b8032d41`，CLI 交付目录为 `artifacts/cold-image-repro/cold-nine-hole-20261001-202252-b8032d41`。截至本次只读检查（本机时间约 20:30），父、runtime、MCP server 仍存在；会话 `agent-observability.jsonl` 在 20:30:14 继续增长，最新事件为 `dsh_request_start` + `provider_wire_request`，前一轮 `cad_update_feature` 工具已完成。CLI 输出目录仅有 0 字节 `stdout.json`、0 字节 `stderr.log` 和 invocation 记录，尚无结构化最终结果。

**判断：** 这不是仍停在 Python CLI 导入、DSH SDK runtime 启动或 runtime 握手阶段。日志证明 harness 已发起并收到多轮真实 SiliconFlow provider 请求，并已执行 MCP CAD 工具；截至检查时，它正在新的一轮 provider 请求中。若用户界面看起来“卡在 SDK 初始化”，那与这些后台证据不符，更可能是 CLI 将最终输出延后到完成时写入，或 stderr 重定向没有实时承载 DSH 进度。仅凭进程存在无法判断该次请求是否最终成功或是否会超时。

## 证据与调用顺序

- 进程命令行与 PID：`Get-CimInstance Win32_Process`；23464 创建于 20:22:52，41204 同秒创建，27200 于 20:22:55 创建。
- 当前记录：`C:\Users\lycohana\AppData\Local\AI Modeler\dsh\sessions\cold-nine-hole-20261001-202252-b8032d41\agent-observability.jsonl`；约 20:22:56 首次 `dsh_request_start` / `provider_wire_request`，随后出现响应头、首个流事件、usage/request complete、多个 `tool_start`/`tool_complete`。该文件最新时间戳 20:30:14。请求日志含敏感输入细节，本审计只核对事件类型、时序和 provider 主机，不复述载荷。
- CLI 输出：`artifacts/cold-image-repro/cold-nine-hole-20261001-202252-b8032d41/{invocation.txt,stdout.json,stderr.log}`。stdout/stderr 当前仍为空，不能据此证明没有进展。
- 源码顺序：`ai_modeler/cli/agent.py:376-405` 解析参数、校验图片/会话并加载配置；`392-396` 导入 DSH 适配器、读取全局配置；`428` 起进入 session lock / 主运行；`ai_modeler/agent/dsh_agent.py:829-891` 构造 `DeepSeekHarness`；`:1009-1011` 建立 harness 并准备请求 blocks；`:1144-1147` 调用 `harness.run(...)`。目标运行已跨过这些阶段，这是由子进程及后续 provider/MCP 事件佐证的事实。

## `--offline` 与成功命令对照

`ai_modeler/cli/agent.py:44-45` 将 `--offline` 定义为“仅关闭 Agent 网页搜索”。`:396-402` 只把本次传入的配置副本 `online.enabled` 设为 false；它没有关闭模型 provider 或 DSH runtime。`:392-403` 先加载 DSH 和配置，`llm_cfg` 仍用于请求。因此加 `--offline` 不能作为 SDK/provider 离线启动或超时恢复开关。

历史真实建模 CLI 成功完成到候选落盘的记录在 `artifacts/agent-quality-rebuild/cold-cli-fresh/acceptance-report.md:58`，命令包含 `python -m ai_modeler agent --text ... --image ... --session-id ... --output-dir ...`，**没有 `--offline`**。同目录报告记录它在约 31 分 41 秒后产出结果，退出码 4（候选未验收通过，非启动失败）。另有 `artifacts/agent-quality-rebuild/loop-review/source-contract-cli-20261001/observation.md:5` 使用 `--offline` 的 CLI 运行记录，但它是 source-contract 检查，不是此前完成真实建模候选的运行；不能替代上面的成功建模对照。

## 建议与边界

1. 当前父进程仍活跃、观测日志持续增长，低风险选择是先让该次运行继续，不启动第二个 CLI、不杀进程，也不加 `--offline`。
2. 若确认 PID 23464 已退出且有最终结果/明确错误，再按 `cold-cli-fresh/acceptance-report.md:58` 的正式 CLI 形式重试：使用新且唯一的 `--session-id`、新 `--output-dir`，其余输入沿用本轮原命令，并省略 `--offline`。这是对已有完成真实 CLI 命令的可追溯复用；它不能保证 provider 可用或恢复相同会话状态。
3. 本次检查没有触发 provider、没有打开 GUI、没有读取参考图文件或 CADPlan 内容；未对当前进程做写操作。限制：没有最终结果文件或进程退出码，无法判定当前请求最终成败；provider-wire 请求事件只证明请求已送出/响应过程被观测，不证明最终模型输出质量。
