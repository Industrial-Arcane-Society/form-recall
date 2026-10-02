# F2.2：Rust 持有 Cordis 工具 schema

更新时间：2026-10-02。F2.2 将模型可见工具契约的来源从 Python 转移到 Rust；工具执行本身仍由 F2.1 的 Rust Host 桥接到过渡期 Python worker。

## 目标与实施

- Rust crate 内嵌版本控制的 25 项工具 schema，Host 会话启动时直接生成 DSH 使用的 `schemaVersion: 1` manifest。
- `agent_host` 不再启动 `dsh_tool_worker --schemas`，schema 读取不再依赖 Python 启动、包导入或应用 Python 环境。
- Node Cordis 插件继续通过原生 `ctx.tools.register` 和固定 DSH `defineTool()` 注册，不创建 MCP server。
- 内嵌内容从迁移时 `dsh_tool_backend.schemas()` 实际输出提取，逐工具包含原名称、description、`inputSchema`、必填字段、约束与嵌套结构。
- 固定 DSH schema 编译器只允许 `additionalProperties` 为布尔值，不接受 schema 对象。Cordis 参数投影将这种动态 map 转为 `additionalProperties: true`，并把 map-value JSON Schema 放进描述；Rust 中的原始完整 schema 不变，后端仍负责领域级参数校验。

## 验收证据

- 迁移对照脚本比较 Rust 内嵌 JSON 与当时的 Python `schemas()` 对象：**25 项逐字段完全相等**。
- Rust schema 单测验证 25 项、工具名唯一、description/schema 形状及 required 字段均有对应 property。
- 真实固定版本 DSH `0.2.0-rc.2` 随附的 `@deepseek-ai/dsh-tools` `defineTool()` 加载 Rust manifest 后，**25 项全部成功注册**。该项不启动 LLM、没有模型请求，也没有 CATIA 操作。
- `cargo test --lib`：24/24 通过；`cargo check --all-targets`：通过；`cargo fmt --check`：通过。
- `node --check` 与 `node --test src/legacy/tests/test_dsh_native_tools_rpc.mjs src/legacy/tests/test_dsh_stream_bridge.mjs`：13/13 通过。
- 桌面端 `npm run build`：通过；Vite 仍提示 `CadViewport` chunk 超过 500 kB，该提示不阻断构建。
- DSH `0.2.0-rc.2` 配套的真实 `@deepseek-ai/dsh-tools` `defineTool()` 手工加载验证：25/25 Cordis schema 成功注册；没有启动 LLM 或发起 API 请求。

## 未完成项

- Python 仍执行 CAD domain 和几何工具；只移除了 Python 对 schema 初始化的依赖。
- 真正由 Rust 拥有参数校验、CADPlan 与工具执行属于 F2.3/F2.5、F3 后续工作。
- Cordis 对象型 `additionalProperties` 的结构信息以工具描述传达给模型，当前 DSH 的工具参数编译层不会做该子 schema 的静态约束；服务端 domain validation 仍是执行前保障。
