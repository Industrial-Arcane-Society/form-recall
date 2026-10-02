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
| F1.2 固定 runtime manifest 与安装器 | **已完成** | app 私有 Node 与 DSH 包；校验下载、版本、升级和失败回滚 |
| F1.3 ACP session 与 Tauri 事件路由 | **已完成** | ACP 初始化、文字/图片、持续会话、恢复/取消/关闭；UI Agent 模式改走 Rust Host，Quick 模式仍走 Python；固定 DSH ACP 控制面已通过隔离验收 |
| F1.4 固定 runtime 真机 CLI 验收 | **已完成：链路通过，识图负样本已登记** | 固定 runtime CLI 已验收 ACP 图片、Cordis 工具、多轮恢复、取消及进程清理；图像观察准确率单独由 F7 评估 |

## F1.1 实施结果

- 新增 `src/apps/desktop/src-tauri/src/acp.rs`：ACP stdio JSON-RPC 进程客户端，不调用 shell，也不从 PATH 解析启动命令。
- stdout reader 将响应按数值 request id 分派，ACP notification 广播给订阅者；Agent 发起但客户端未声明能力的方法收到 JSON-RPC `-32601`，不静默执行。
- 错误响应、超时、管道关闭和有界退出有明确 Rust 错误；超时请求会从 pending 表删除，后续请求仍可使用同一连接。
- 加入由 Rust test binary 扮演的假 ACP 子进程测试，验证 `initialize`、`session/prompt`、`session/update`、RPC error、超时后续调用、`session/cancel` notification 和异常退出释放等待请求。

### F1.1 验证

- `cargo fmt --all`
- `cargo test --lib`：9 passed
- 未启动真实 DSH，未调用 LLM；ACP profile 的图像能力、实际 provider 和模型调用仍属于 F1.4。

## F1.2 实施结果

- 新增固定 Runtime manifest、npm `package.json` 和 lockfile；目标为 Windows x64，Node `v22.23.3` / npm `10.9.9`、DSH/ACP `0.2.0-rc.2`、ACP SDK `1.4.0`。
- Tauri 资源构建会把 `src/legacy/packaging/dsh-runtime/` 原样暂存到应用资源 `app/packaging/dsh-runtime/`；Rust Host 校验清单与锁文件 SHA-256 后，从官方 Node 归档下载并验证 SHA，再用私有 Node 执行锁定的 `npm ci`。
- 安装位于用户数据目录，与机器级 Node、DSH 和旧的用户 DSH profile 分离；新版本先安装到暂存目录，切换时保留前一版本，失败时恢复。Python 依赖已就绪的旧用户只补装 DSH Runtime，不重装 Python。
- 首次安装界面已区分 Python 镜像源与固定的官方 Node/npm 源，并明确说明 Agent Runtime 使用应用私有 Node。

### F1.2 验证

- `cargo fmt --all --check`、`cargo test --lib`：15 passed；包含安全路径、清单哈希、切换失败恢复、崩溃中断恢复和新版本升级切换测试。
- `npm run build`：通过；Vite 提示 CAD 视口 chunk 637 KB，属于已有体积提示。
- 执行 `node src/legacy/scripts/build_desktop_resources.mjs`：通过；暂存后的 `runtime-manifest.json`、`package.json`、`package-lock.json` 与源文件 SHA-256 完全一致。
- 在新建临时目录中独立下载并校验 Node 官方 Windows x64 归档，解出 Node/npm，用独立 config/cache 和私有 PATH 运行锁定 `npm ci`：538 packages installed；Node `v22.23.3`、npm `10.9.9`，DSH CLI `--version` 返回 `0.2.0-rc.2`，`dsh --profile acp --help` 可启动。该验证没有读取机器全局 DSH profile，也没有发起模型请求。
- F1.2 没有验证真实 ACP 会话或图像/模型 route；这些属于 F1.4。真实安装器升级故障通过 filesystem 单元测试覆盖，尚未在已安装 Tauri 包上完成端到端 UI 安装演练。

## F1.3 实施结果

- 新增 `src/apps/desktop/src-tauri/src/agent_host.rs`。Rust Host 从版本化 manifest 解析应用私有 Node/DSH 入口，生成不含密钥的 profile patch，密钥只通过 DSH 子进程环境传入；Agent 模式通过 ACP 控制 DSH，Quick 模式保留原 Python 快速建模通道。
- Agent profile 直接加载项目的 Cordis 原生插件，没有 MCP CAD 转译层。现阶段 CAD 插件仍通过一次性 JSONL worker 调用 Python 几何/CATIA 后端，这是向 F2 迁移前的明确过渡边界，不能视为已经移除 Python。
- Tauri 持续转发 thought/message/tool/usage 事件，前端展示工具状态，thought 在生成时展开并在本轮结束后折叠；模型预览通过文件哈希变化实时推送。图片采用 ACP image content block，原始用户图保留可校验哈希的会话资产清单。
- 应用分别保存 ACP `session_id`/工作区与 UI transcript；启动时尝试 `session/resume`，模型或连接配置变化时归档旧 UI history 并创建新会话。取消使用 `session/cancel`，新对话关闭旧 session，退出应用时有界关闭 DSH 进程。
- profile 中只对已明确核验的 DeepSeek `deepseek-flash` Responses route 和 SiliconFlow `moonshotai/kimi-k2.7-code` route 声明视觉能力；所选 ACP route 不公布图片能力时，会在发送图片前报错。

### F1.3 验证

- `cargo fmt --all --check`：通过；`cargo test --lib`：19 passed。
- `npm run build`：通过；仍有 CAD viewport 637 KB chunk 体积提示。
- 私有 DSH `0.2.0-rc.2 --dump-config` 加载应用生成的 profile patch：退出码 0、stderr 为空，配置输出包含 AI Modeler provider 和三个 Cordis 插件。
- 在隔离临时 `DSH_HOME` 中通过私有 Node/DSH 真实运行 ACP：`initialize`、`session/new`、关闭进程、重新启动、`session/resume`、设置 `reasoning_effort=high`、`session/close` 均成功；ACP 公布 `image=true`。DSH stderr 为空，原生 Cordis 插件已注册 24 个工具（包括 CAD、知识检索、`web_search` 等），没有发送 `session/prompt`，没有使用 API 密钥发起模型请求。
- 上述为 F1.3 截止时的结论：通过 DSH ACP 控制面和 Cordis 插件装载验证，但尚未完成 live prompt、图片路由或 CAD 工具实建。F1.4 的后续验收结果见下文。

## F1.4 固定 runtime 真实运行验收

### CLI/runtime 与控制面

- 在隔离 worktree 的 `src/apps/desktop/src-tauri/target/f1-runtime-probe/` 准备 Node/DSH 运行目录。Node v22.23.3 官方归档 SHA-256 与 manifest 相符，package.json 与 package-lock.json SHA-256 也均相符。私有 npm 安装完成 538 个包；Node v22.23.3、npm 10.9.9、DSH/ACP 0.2.0-rc.2、ACP SDK 1.4.0。
- DSH `--profile acp --patch ... --dump-config` 返回 0、stderr 为空，合并配置内能看到 `ai-modeler-native-tools`、`ai-modeler-attachment-store` 与 `ai-modeler-live-stream` 三个 Cordis 插件。`DSH_HOME` 全程指向隔离目录，没有读取或修改全局 DSH profile。
- ACP `initialize` 返回 `promptCapabilities.image=true`。受控 API 请求将 reasoning effort 设为 off；session/prompt 返回 `end_turn`。只使用仓库中的 `src/legacy/examples/真实照片样本/50x50x5/KMS-M09-H06-50-20.png`，文件 SHA-256 为 `941e1f4b78255cbc2178a9e3daca3ffe54e6ac602a24f22b0a699fd113858851`，大小 17,849 bytes；没有把它描述成此前用户提供的参考图。
- 真实 Cordis 只读工具轨迹为 `cad_inspect_references`（完成）和 `cad_get_state`（完成）；没有调用写模型工具，CADPlan 保持 empty/revision 0。
- 第一次 live session 收到 `session/close` 后，另一 DSH 进程用同一 session id 与 cwd 调用 `session/resume` 成功；随后发出 `session/prompt` 并发送 `session/cancel`，ACP 返回 `stopReason=cancelled`。两个 DSH 进程均以 exit code 0 退出、stderr 为空。

### 图片路径与质量负样本

- Rust Host 的 `active-turn.json` 将 turn id、原图引用 id 与 SHA-256 绑定。DSH ACP 会把图片内联字节持久化为内容寻址附件，LLM stream 中的图片结构是 `image.attachment.attachmentId=sha256:…`，不是原始 base64。stream bridge 现按 DSH attachment id 与活动轮次 SHA 做精确匹配；Node 定向测试覆盖该实际结构。
- 真实运行记录显示 DSH LLM stream 有 1 张图，SHA 与本轮源图完全一致。Node provider 拦截器也在清理前观察到同 SHA、同字节数的 1 张图片；应用现行策略随后从主对话 provider payload 移除该图片，实际发往主模型的 request image count 是 0。视觉调用由 `cad_inspect_references` 的专用 Python observer 完成。因此本轮证明 ACP 附件及 Cordis 观察路径收到原图，也明确发现主 Agent 当前并不直接以图像 token 看图；F2–F6 迁移时必须决定并迁移该依赖，不能把工具观察说成 DSH 主模型直传视觉。
- 尽管原图 SHA 路径正确，图上实际有 6 个明显孔，`cad_inspect_references` 将两组各判成 4 个，主 Agent 最后复述为约 8 个。观察置信度为 0.8，但计数错误；这是 F7 图像理解精度负样本，不能算成模型通过或几何结果。
- Python observer 的结构化记录确认了实际调用模型/Responses route、reasoning off、输入 crop SHA 与源图 SHA 一致并收到 observation 响应；目前 Python 请求没有接入 Rust/Node provider-wire 原始请求记录，其 provider 线上图片 payload 的独立捕获仍是迁移/审计缺口。

### 实施与验证

- Rust 增加活动轮次图像哈希记录；Node stream bridge 根据 DSH 内容寻址附件 id 关联源 SHA，并分别记录清理前 provider 输入图片数与清理后实际发送图片数。
- `node --test src/legacy/tests/test_dsh_stream_bridge.mjs`：7/7 通过，覆盖原始图像字节、DSH attachment id、provider 清理及无文本落盘。
- `cargo fmt --all --check`：通过；`cargo test --lib agent_host::tests::active_turn_record_binds_user_image_hash_to_provider_observation`：1/1 通过。
- 前端下拉菜单修复另在 `main` 的提交 `b597a69`，不属于本 F1.4 提交。
