# F7.G7 阶段模型路由与 Agent 运行熔断

## 范围

F7.G7 为感知、建模和视觉复核分别配置模型供应商与推理强度，并为 DSH ACP turn 增加按时间、有效进展和重复无效写操作触发的有界停止。它不限制正常建模的总工具次数或修复轮数。

阶段设置保存在桌面应用全局 LLM 配置中的 `llm.agent_stage_overrides`。它们通过 `settings.save/get` 往返保留，不写入 `.aimodel` 工程。每个阶段空供应商或空 reasoning 值继承当前活动供应商档案及其默认值。界面通过选择一个供应商档案来选择该档案的默认模型；未提供自由文本阶段模型编辑入口。

## 模型路由与 reasoning 证据

- ACP 主会话使用 `modeling` 阶段 provider。
- `cad_inspect_references` 使用 `perception` 阶段的原生视觉 provider。
- `cad_review_reference_similarity` 使用 `review` 阶段的原生视觉 provider；未配置独立审查档案时继承当前全局活动 provider。
- 旧版扁平 provider 配置仍作为单 provider 档案处理。
- 模型和 reasoning 配置在启动 turn 前统一校验；未知阶段、档案或不支持的 reasoning 值以可操作错误返回。
- 建模阶段只在 ACP 的 `session/set_config_option` 回执明确包含当前 reasoning 值时记作 `acknowledged`。路由不支持、未公布选项、设置调用失败或回执缺少有效值时保留 `effective = null` 并发送告警。
- 感知和审查请求的 telemetry 记录 provider/stage 标识、请求的 reasoning 和发送前预计写入 payload 的字段；记录标记为 `pre_send_expected`，不代表服务端已接受或应用。未知 Chat Completions 路由不发送通用 `reasoning_effort`，已知 DeepSeek Responses 路由只发送该模型明确支持的 effort 值，已知 toggle 路由保留 `enable_thinking`/预算字段。不记录 API Key。

## 运行边界

| 条件 | 默认值 | 行为 |
|---|---:|---|
| 一轮总时限 | 60 分钟 | 整轮达到时限即发送 ACP `session/cancel`。 |
| 有效进展空闲时限 | 10 分钟 | 无可见文本/思考增量、工具状态变化或几何 revision 增长时触发取消。120 ms 轮询、重复状态和 usage 不算进展。 |
| 取消宽限 | 15 秒 | 给 DSH 和工具桥收尾；仍挂起时 shutdown ACP 与工具桥，释放无限等待的 pending request。后续请求由 Host 检测死进程并重建 session。 |
| 相同写操作无进展 | 5 次 | 只累计写工具的相同脱敏参数与不变 revision，或相同错误；只读工具不累计。revision 增长后清除计数。 |

用户取消、总时限、空闲时限、重复写操作无进展和 Host/ACP 错误拥有不同终态原因。Agent turn 由 Host wrapper 单点发出一个 `agent.turn_complete`；RAII guard 在成功、错误、超时和取消路径清除 active 与 cancel 状态。ACP 请求目前不支持单轮请求级 timeout，所以宽限期结束后需要回收当前 runtime 进程；这会要求下一轮恢复/重建 session。

## 验证记录

- `cargo check --manifest-path src/apps/desktop/src-tauri/Cargo.toml --lib --offline`：通过。
- `cargo test --lib --offline agent_run_guard -- --nocapture`：2 项通过，覆盖独立总/空闲时钟和重复写入熔断策略。
- `cargo test --lib --offline stalled_prompt_cancel_then_shutdown_releases_unbounded_request -- --nocapture`：1 项通过；fake ACP 在取消通知后仍挂起，ACP shutdown 后 pending request 在 1 秒内被唤醒。此测试只证明底层请求回收机制，不调用完整 Tauri `run_turn` watchdog。
- `cargo test --lib --offline agent_host::tests -- --nocapture`：最终 15 项通过，覆盖阶段配置解析、reasoning 回执、active 清理、终态分类与取消结果映射。
- 另有 `agent_model_config` 5 项通过，覆盖 UI 生成的空、空格和制表符 provider ID 继承默认档案；`native_vision_provider` 13 项通过，覆盖未知路由的 `none`/`high` 不注入通用 effort 字段、已知 toggle payload 与预发送 telemetry 语义。ACP reasoning 回执解析和视觉 provider 路由定向测试通过。
- `python -m pytest src/legacy/tests/test_ui_bridge.py -k settings`，设置 `PYTHONPATH=src/legacy`：1 项通过，6 项跳过筛选；覆盖三阶段设置经现有 provider normalization 与 flat-field mirror 后完整往返。
- 在 `src/apps/desktop/` 执行 `npm run build`：通过。Vite 对 `CadViewport` chunk 大于 500 kB 发出提示。
- 未运行真实 DSH/LLM 多模态会话；未打开 Tauri GUI；构建/fixture 证据不代表生产运行视觉效果已验收。
