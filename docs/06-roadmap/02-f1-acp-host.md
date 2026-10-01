# F1：Rust DSH ACP Host

更新时间：2026-10-02。每个子 feature 完成并单独提交。DSH 管理 Agent 循环；Rust 管理 desktop 进程、ACP 会话和应用状态；CAD 工具由随 ACP profile 装载的 DSH 原生 Cordis 插件提供。

## 版本依据与边界

- 目标 DSH 版本：`0.2.0-rc.2`；对应 `@deepseek-ai/dsh-acp` 和 `@deepseek-ai/dsh-acp-app` 也固定到 `0.2.0-rc.2`，ACP SDK 为 `1.4.0`。
- 该版本随附 `dsh --profile acp`，profile 首次启动会从内置模板初始化。应用 patch 可覆盖 ACP route 并加载项目自己的 Cordis 插件。
- ACP v1 stdio 帧为**每条 JSON-RPC 消息一行，以 LF 分隔**，stdout 不能混入普通日志。Rust Host 直接实现该 stdio 客户端，不接入 MCP CAD 转译层。
- 图片通过 ACP image content block 发送，`data` 是 Base64。DSH 是否接受图片取决于初始化时可用的 provider/model route 和附件服务；真正 route 能力必须用固定 runtime 核验。
- ACP 更新可传递 thought/message 和通用工具调用生命周期，但不提供 DSH 私有 UI 卡片。CAD 预览仍需由原生工具结果进入 Rust CAD 状态，再由 Tauri 推送。

权威协议来源：[ACP v1 概览](https://agentclientprotocol.com/protocol/overview)、[ACP v1 stdio transport](https://agentclientprotocol.com/protocol/transports)、[DSH ACP package](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/acp/acp/README.md)、[DSH ACP bundle](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/bundle/acp-app/README.md)。

## 子 Feature 状态

| 子 Feature | 状态 | 验收边界 |
|---|---|---|
| F1.1 ACP stdio process/JSON-RPC peer | **已完成** | 显式子进程、LF JSON-RPC 行帧、request id 响应匹配、通知订阅、RPC error、请求超时、关闭 stdin 后有界等待/回收；不接入 Tauri，不宣称真实 DSH 已运行 |
| F1.2 固定 runtime manifest 与安装器 | 未开始 | app 私有 Node 与 DSH 包；校验下载、版本、升级和失败回滚 |
| F1.3 ACP session 与 Tauri 事件路由 | 未开始 | ACP 初始化、文字/图片、持续会话、恢复/取消/关闭；UI Agent 模式改走 Rust Host，Quick 模式仍走 Python |
| F1.4 固定 runtime 真机 CLI 验收 | 未开始 | 无模型 API key 的 ACP 控制面测试与受控的真实路由请求分别记录；图片能力、Cordis 插件、thought/tool 更新和会话清理有结果 |

## F1.1 实施结果

- 新增 `src/apps/desktop/src-tauri/src/acp.rs`：ACP stdio JSON-RPC 进程客户端，不调用 shell，也不从 PATH 解析启动命令。
- stdout reader 将响应按数值 request id 分派，ACP notification 广播给订阅者；Agent 发起但客户端未声明能力的方法收到 JSON-RPC `-32601`，不静默执行。
- 错误响应、超时、管道关闭和有界退出有明确 Rust 错误；超时请求会从 pending 表删除，后续请求仍可使用同一连接。
- 加入由 Rust test binary 扮演的假 ACP 子进程测试，验证 `initialize`、`session/prompt`、`session/update`、RPC error、超时后续调用、`session/cancel` notification 和异常退出释放等待请求。

### F1.1 验证

- `cargo fmt --all`
- `cargo test --lib`：9 passed
- 未启动真实 DSH，未调用 LLM；ACP profile 的图像能力、实际 provider 和模型调用仍属于 F1.4。

## 待实施设计

- Rust Host 从版本化 manifest 解析应用私有 Node、DSH 入口和包哈希；启动 `node <dsh-bin> --profile acp --patch <app-profile-patch>`，使用独立 app data 下的 `DSH_HOME`。
- agent profile patch 复用当前被认可的 provider/system prompt 与项目 Cordis 插件，但将 Python 参数解析/几何 worker 拆到后续 F2/F3；F1 期间如有兼容 worker，只能作为显式过渡，不能称为 Python 已移除。
- Tauri 必须把 ACP `thought`、`message`、`tool_call`、`tool_call_update` 和 `usage_update` 变为持续 UI 事件；必须识别 unsupported capability，不把缺失的图片/恢复能力伪装成成功。
- ACP `session/resume` 不回放旧的 UI transcript；应用自己的聊天历史与 ACP session id/cwd 要独立保存。首次迁移采用新的空用户配置和 session store，不自动搬运旧 Python Agent 配置/会话。
- 单轮 cancel 使用 ACP `session/cancel`，不得杀掉整个 DSH 进程；关闭桌面时再关闭各 ACP session 并结束 runtime。
