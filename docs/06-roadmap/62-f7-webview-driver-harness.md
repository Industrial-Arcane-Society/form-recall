# F7.G2/G3：隐藏 Tauri WebView 的 CLI WebDriver 验收 harness

## 目标与隔离

新增 `ai-modeler-webview-smoke` 独立 Tauri binary，让 WebDriver 驱动 Windows 上真实 Tauri WebView2 中的生产 Vue bundle。应用通过隔离的 smoke-only `agent_send` handler 将确定性事件送入 Tauri `Channel<Value>`，生产 `App.vue` 的 Channel callback 负责更新模型与对话状态。

验收 binary 使用单独的 Cargo feature、产品标识和 Tauri 配置。窗口在创建前设为 `visible: false`、`skipTaskbar: true`，setup 后再次隐藏；它不注册 DSH AgentHost、联网搜索、OCCT 或 CATIA 命令，也不加载用户的生产设置目录。默认 `ai-modeler` binary 与现有 MockRuntime 测试保持原样。

测试仅发送固定 fixture prompt 和合成事件，不访问参考图、provider、真实 DSH 或 CATIA。runner 只输出场景通过/失败，不保存截图或会话内容。

## CLI 用法

在 Windows、`src/apps/desktop` 下运行：

```powershell
npm run test:agent-webview
```

命令先构建生产 Vue bundle，再构建 `ai-modeler-webview-smoke`，最后启动 `tauri-driver` 建立 WebDriver session。四个 fixture 场景依次验证：

1. 纯问答完成且没有模型；
2. CAD 工具失败后保持候选模型；
3. finish revision 与 geometry fingerprint 都匹配时显示已验收；
4. stale finish 保持候选，不显示已验收。

应用二进制位置是 `src/apps/desktop/src-tauri/target/debug/ai-modeler-webview-smoke.exe`。构建与启动由同一个 npm 命令完成；若只需编译、不运行 WebView，可执行：

```powershell
npm run build
cargo build --manifest-path src-tauri/Cargo.toml --bin ai-modeler-webview-smoke --features agent-webview-smoke,tauri/custom-protocol --offline
```

## WebDriver 前置条件

- Windows WebView2 Runtime；Microsoft Edge WebDriver (`msedgedriver.exe`) 版本需匹配本机 WebView2 Runtime。
- `tauri-driver` 与 `msedgedriver.exe` 必须可在 `PATH` 中找到。也可用 `AIMODELER_TAURI_DRIVER` 和 `AIMODELER_EDGE_DRIVER` 指定完整路径。
- 已安装 Node.js/npm、Rust/Cargo；项目 Rust 依赖在离线缓存内时构建可使用 `--offline`，npm build 仍需已安装项目依赖。

Tauri 官方列出 Windows `tauri-driver` → Microsoft Edge Driver 路线；Microsoft 文档说明 EdgeDriver 可启动并自动连接单实例 WebView2。参见 [Tauri WebDriver 指南](https://v2.tauri.app/develop/tests/webdriver/) 与 [Microsoft WebView2 WebDriver 指南](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/webdriver)。这说明该路径受支持，不代表本项目当前隐藏窗口配置已经实机稳定。

缺少驱动时 runner 会在构建或启动应用前退出。测试期间 WebDriver 创建的 session 在 `finally` 中删除，随后回收它启动的 `tauri-driver` 进程树。

## 当前验证与边界

本次只编译和运行了无窗口的静态/fixture 验证：

- `cargo check --bin ai-modeler-webview-smoke --features agent-webview-smoke,tauri/custom-protocol --offline`：通过。
- `cargo build --bin ai-modeler-webview-smoke --features agent-webview-smoke,tauri/custom-protocol --offline`：通过；只生成 executable，没有启动它。
- smoke fixture Rust 单测：5/5 通过。
- `node --check scripts/test-agent-webview.mjs`：通过。

没有启动 Tauri、WebView2、WebDriver、DSH、provider、CATIA。当前环境找不到 `tauri-driver.exe` 或 `msedgedriver.exe`，所以隐藏 WebView 是否能被此 WebDriver 版本稳定启动和附着仍未知；必须在具备匹配驱动且获准启动隐藏 WebView 的 Windows 环境中首次运行后才能确认。

这项测试证明目标应是实际 `WebView2 JavaScript → Tauri Channel callback → 生产 Vue handler`，不检查窗口像素、布局、3D WebGL 绘制或用户可见渲染质量。隐藏窗口的 WebDriver 可交互性也未经实机验证。真实视觉 QA 仍需单独的可见桌面验收。

现有 `npm run test:agent-channel-vue` 仍覆盖 MockRuntime Channel 序列化与 Vue 状态 handler 的快速回归；它与本 harness 是不同证据层，不能互相替代。
