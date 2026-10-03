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

### 真实 WebView 运行

本机安装的 WebView2 Runtime 为 `154.0.4258.53`。从微软官方 EdgeDriver CDN 下载了匹配版本的 x64 `msedgedriver.exe`，并在 worktree 的 `temp/acceptance/f7-webview-tools-20261004-045233-57ba6b1f/` 下构建/安装 `tauri-driver 2.1.0`。tauri-driver 使用任务专属 `CARGO_HOME` 与 `--root`；没有全局安装、改 PATH 或用户设置。

第一轮 `npm run test:agent-webview` 的前端构建和 smoke binary 构建通过，真实隐藏 WebView 的前两个场景通过：纯问答、CAD 工具失败。第三个 `accepted-current` 场景在 30 秒后超时。静态比对发现 fixture 的 terminal `current_model` 漏传 `acceptance_confirmed: true`：生产 acceptance predicate 要求 host 明确确认，即使 `status`、finish revision 和 geometry fingerprint 都有效，也不会仅凭 `status: accepted` 接受模型。

已修复 smoke fixture：accepted snapshot 显式设 `acceptance_confirmed: true`，candidate snapshot 设为 `false`，并为 accepted snapshot 增加 Rust 回归断言。按项目测试 wrapper 加载 Common-Controls v6 manifest 后，`webview_smoke` fixture 测试通过（5/5）。直接运行未嵌 manifest 的测试 exe 曾以 Windows `0xc0000139` 退出；这不是 WebView 或产品处理错误。

修复后唯一一次 CLI WebDriver 复跑中，四个场景全部通过：纯问答、CAD 工具失败、当前版本验收、过期 finish 保持候选。输出确认真实 `WebView2 JavaScript → Rust Tauri Channel → 生产 Vue handler` 路径收到事件并更新状态。WebDriver session 结束后未发现该验收目录下仍在运行的 smoke/driver 进程。

### 编译与 fixture 验证

静态、fixture 与真实 WebView 验证结果：

- `cargo check --bin ai-modeler-webview-smoke --features agent-webview-smoke,tauri/custom-protocol --offline`：通过。
- `cargo build --bin ai-modeler-webview-smoke --features agent-webview-smoke,tauri/custom-protocol --offline`：通过；只生成 executable，没有启动它。
- `pwsh -NoProfile -File src-tauri/scripts/test-lib.ps1 -Offline -Filter webview_smoke`：5/5 通过。
- `node --check scripts/test-agent-webview.mjs`：通过。
- 修复后的唯一一次 `npm run test:agent-webview`：真实隐藏 WebView 四场景全部通过。

没有运行 DSH、provider 或 CATIA，也没有截图、保存 WebDriver 会话内容或改动原始运行日志。后续 CLI WebView 运行仍只允许使用这个 smoke-only binary、隔离 WebDriver 和固定 fixture。

本次运行验证了 `WebView2 JavaScript → Tauri Channel callback → 生产 Vue handler` 四场景投递及状态处理。测试不检查窗口像素、布局、3D WebGL 绘制或用户可见渲染质量。真实视觉 QA 仍需单独验收。

现有 `npm run test:agent-channel-vue` 仍覆盖 MockRuntime Channel 序列化与 Vue 状态 handler 的快速回归；它与本 harness 是不同证据层，不能互相替代。
