# F2.1：原生 Cordis → Rust 本机工具桥

更新时间：2026-10-02。该 feature 只建立 DSH 插件到 Rust Host 的工具执行边界，不代表 CAD 业务逻辑已从 Python 迁出。

## 目标与边界

- DSH 内继续由 Cordis 原生 `ctx.tools.register` 注册建模工具；不引入 MCP 工具转译。
- Node 插件只从 Rust Host 提供的版本化 schema 清单注册工具，并通过本机 JSON-RPC 调用 Rust Host。
- Rust Host 校验会话、随机 token、工具名、参数对象、截止时间和调用 ID；执行与取消由 Rust 管理。
- F2.1 过渡期内，Rust Host 仍通过私有 Python JSONL worker 执行原有 CAD 工具。Rust→Python 是内部兼容执行器；Cordis 插件不再启动 Python，也没有 Python fallback。

## 实现

- Rust 为每个 DSH 会话绑定随机 `127.0.0.1` 端口和 256-bit token，管理 Cordis schema 清单与 bridge 生命周期。
- JSON-RPC 提供 `tools/list`、`tools/call`、`tools/cancel`、`bridge/ping`；请求行、响应行、连接数、worker 输出和超时均有上限。
- 工具调用使用 session/token 校验、绝对 Unix 毫秒 deadline、活动调用去重、已完成结果缓存与调用 ID 参数指纹；同 ID 不同参数会拒绝。
- Cordis 取消使用独立连接发送相同 `toolCallId`；Rust 把取消转交给当前兼容 worker。
- 图片结果校验 MIME、Base64 与单图大小，再保存为 DSH 原生附件。
- Native plugin 本身不再引用 Python executable、项目根目录或 `dsh_tool_worker`。

## 验收记录

- `cargo test --lib`：23/23 通过；覆盖 Cordis 工具清单、会话/token 鉴权、相同调用重试、ID 冲突、过期 deadline 后可重试、取消路由、ACP 与 Agent Host 原有用例。
- Node `--check`：通过。
- `node --test src/legacy/tests/test_dsh_native_tools_rpc.mjs src/legacy/tests/test_dsh_stream_bridge.mjs`：13/13 通过；覆盖原生 Cordis 注册、Rust manifest、调用/取消协议、图片附件、畸形/过大响应和无 Python fallback。
- `cargo check --all-targets`：通过。
- `npm run build`（`src/apps/desktop`）：通过；Vite 有单个大 chunk 提示，不影响构建成功。
- F2.1 没有进行真实 LLM prompt 的 DSH/CAD 工具调用；目前协议两端分别用 Rust 与 Node 测试覆盖。这项限制保留在 F2 的后续真实运行验收中。

## 后续拆分

1. F2.2：让 Rust 成为工具 schema/参数契约的唯一来源，移除 Python schema worker 启动。
2. F2.3–F2.4：迁移只读状态、知识/Web、图像观察及复核。
3. F2.5：将 CAD 写工具接入 Rust CADPlan 与后续 OCCT 核心，删除 Python 工具 worker。

在 F2.5 及必要的视觉路径迁出前，总体 F2 仍为进行中。
