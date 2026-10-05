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

命令先构建生产 Vue bundle，再构建 `ai-modeler-webview-smoke`，最后启动 `tauri-driver` 建立 WebDriver session。当前五个 fixture 场景依次验证：

1. 纯问答完成且没有模型；
2. CAD 工具失败后保持候选模型；
3. finish revision 与 geometry fingerprint 都匹配时显示已验收；
4. stale finish 保持候选，不显示已验收。
5. 通过生产 Quick parser 与 Rust/OCCT builder 生成确定性长方体、提交隔离 active model、发送其真实 `model.changed` 文档，再由生产 `read_asset` IPC 和 `CadViewport` 的 `GLTFLoader` 装入 WebGL 场景。该场景需要本机 OCCT 7.9.3 runtime manifest；没有时必须报环境限制，不能用合成 GLB 代替。

应用二进制位置是 `src/apps/desktop/src-tauri/target/debug/ai-modeler-webview-smoke.exe`。构建与启动由同一个 npm 命令完成；若只需编译、不运行 WebView，可执行：

```powershell
npm run build
cargo build --manifest-path src-tauri/Cargo.toml --bin ai-modeler-webview-smoke --features agent-webview-smoke,tauri/custom-protocol --offline
```

## WebDriver 前置条件

- Windows WebView2 Runtime；Microsoft Edge WebDriver (`msedgedriver.exe`) 版本需匹配本机 WebView2 Runtime。
- `tauri-driver` 与 `msedgedriver.exe` 必须可在 `PATH` 中找到。也可用 `AIMODELER_TAURI_DRIVER` 和 `AIMODELER_EDGE_DRIVER` 指定完整路径。
- 生产 Quick/OCCT 场景需要 OCCT 7.9.3 manifest；标准位置是 `src-tauri/target/f4-occt/runtime-manifest.json` 或 `src-tauri/target/package-resources/occt-runtime/runtime-manifest.json`，也可设置 `AIMODELER_OCCT_MANIFEST`。
- 已安装 Node.js/npm、Rust/Cargo；项目 Rust 依赖在离线缓存内时构建可使用 `--offline`，npm build 仍需已安装项目依赖。

Tauri 官方列出 Windows `tauri-driver` → Microsoft Edge Driver 路线；Microsoft 文档说明 EdgeDriver 可启动并自动连接单实例 WebView2。参见 [Tauri WebDriver 指南](https://v2.tauri.app/develop/tests/webdriver/) 与 [Microsoft WebView2 WebDriver 指南](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/webdriver)。这说明该路径受支持，不代表本项目当前隐藏窗口配置已经实机稳定。

缺少驱动时 runner 会在构建或启动应用前退出。测试期间 WebDriver 创建的 session 在 `finally` 中删除，随后回收它启动的 `tauri-driver` 进程树。

## 当前验证与边界

### 真实 WebView 运行

本机安装的 WebView2 Runtime 为 `154.0.4258.53`。从微软官方 EdgeDriver CDN 下载了匹配版本的 x64 `msedgedriver.exe`，并在 worktree 的 `temp/acceptance/f7-webview-tools-20261004-045233-57ba6b1f/` 下构建/安装 `tauri-driver 2.1.0`。tauri-driver 使用任务专属 `CARGO_HOME` 与 `--root`；没有全局安装、改 PATH 或用户设置。

第一轮 `npm run test:agent-webview` 的前端构建和 smoke binary 构建通过，真实隐藏 WebView 的前两个场景通过：纯问答、CAD 工具失败。第三个 `accepted-current` 场景在 30 秒后超时。静态比对发现 fixture 的 terminal `current_model` 漏传 `acceptance_confirmed: true`：生产 acceptance predicate 要求 host 明确确认，即使 `status`、finish revision 和 geometry fingerprint 都有效，也不会仅凭 `status: accepted` 接受模型。

已修复 smoke fixture：accepted snapshot 显式设 `acceptance_confirmed: true`，candidate snapshot 设为 `false`，并为 accepted snapshot 增加 Rust 回归断言。按项目测试 wrapper 加载 Common-Controls v6 manifest 后，`webview_smoke` fixture 测试通过（5/5）。直接运行未嵌 manifest 的测试 exe 曾以 Windows `0xc0000139` 退出；这不是 WebView 或产品处理错误。

此前修复后的 CLI WebDriver 复跑记录中，四个场景全部通过：纯问答、CAD 工具失败、当前版本验收、过期 finish 保持候选。输出确认真实 `WebView2 JavaScript → Rust Tauri Channel → 生产 Vue handler` 路径收到事件并更新状态。WebDriver session 结束后未发现该验收目录下仍在运行的 smoke/driver 进程。

### 2026-10-04 隐藏 WebView CLI 复跑

在目标 worktree 的 `src/apps/desktop` 再次运行 `npm run test:agent-webview`，命令退出码为 0。Vue production build 与 smoke binary 构建通过；Vite 提示 `CadViewport` chunk 大于 500 kB（minified），属于非阻断 warning。四个场景 `pure-qa`、`cad-tool-failure`、`accepted-current`、`stale-finish` 全部输出 `PASS`。runner 等待生产 Vue 应用首屏 mount 成功，并通过真实 Windows WebView2 验证 `WebView2 JavaScript → Rust Tauri Channel → 生产 Vue handler` 事件投递及状态更新；未见启动或 IPC 错误。

本次使用的 smoke 配置将窗口设为 `visible: false`、`skipTaskbar: true`，Rust setup 后再次调用 `window.hide()`。结束后进程检查未发现 `tauri-driver.exe`、`msedgedriver.exe` 或 `ai-modeler-webview-smoke.exe` 残留；CLI 运行结束后的状态检查未发现工作树改动。本次仍未运行真实 DSH、provider 或 CATIA。该 CLI 验收只证明隐藏 WebView 的首屏挂载与 Channel→Vue 事件路径及 fixture 状态处理，不检查像素、布局或 3D WebGL 绘制质量。

### 编译与 fixture 验证

静态、fixture 与真实 WebView 验证结果：

- `cargo check --bin ai-modeler-webview-smoke --features agent-webview-smoke,tauri/custom-protocol --offline`：通过。
- `cargo build --bin ai-modeler-webview-smoke --features agent-webview-smoke,tauri/custom-protocol --offline`：通过；只生成 executable，没有启动它。
- `pwsh -NoProfile -File src-tauri/scripts/test-lib.ps1 -Offline -Filter webview_smoke`：5/5 通过。
- `node --check scripts/test-agent-webview.mjs`：通过。
- 此前记录的修复后 `npm run test:agent-webview` 复跑：真实隐藏 WebView 四场景全部通过；本次 2026-10-04 复跑另见上文。

没有运行 DSH、provider 或 CATIA，也没有截图、保存 WebDriver 会话内容或改动原始运行日志。后续 CLI WebView 运行仍只允许使用这个 smoke-only binary、隔离 WebDriver 和固定 fixture。

本次运行验证了 `WebView2 JavaScript → Tauri Channel callback → 生产 Vue handler` 四场景投递及状态处理。测试不检查窗口像素、布局、3D WebGL 绘制或用户可见渲染质量。真实视觉 QA 仍需单独验收。

现有 `npm run test:agent-channel-vue` 仍覆盖 MockRuntime Channel 序列化与 Vue 状态 handler 的快速回归；它与本 harness 是不同证据层，不能互相替代。

### 2026-10-05：此前的合成 GLB smoke 基线

先前 `glb-preview` 场景只在 smoke app data 生成单三角 GLB，用来验证生产 `read_asset`、`GLTFLoader.parse` 和 WebGL canvas 路径。该旧结果没有经过 Quick parser、OCCT、活动模型提交，不算生产几何端到端验收；本次把该场景替换为下面的生产 Quick 场景。

### 2026-10-05：生产 Quick/OCCT GLB 到隐藏 WebView

`production-quick-preview` 调用与生产 `quick_build` 共用的 `build_quick_active_model`（经 `headless_quick_smoke`），使用固定明确尺寸的长方体提示词，通过 Quick parser、CADPlan 校验、Rust/C ABI OCCT 建模、生产 GLB/AIMODEL/STL/OBJ 写出及活动模型 generation commit。随后 fixture transport 只发送该生产返回文档为 `model.changed`。`read_asset` command 委托生产 `lib.rs::read_asset`；Vue 生产 `CadViewport` 继续执行 `GLTFLoader.parse → replaceMesh → fitView`，runner 检查生产模型非 fixture、特征存在、状态为“三维预览已更新”且 canvas/WebGL context 有效。生产 runtime manifest 缺失时明确 fail closed，不回退手工 mesh。

本次目标 worktree 使用本地 npm cache 执行 `npm.cmd ci --offline --no-audit --no-fund`；`npm.cmd run build` 通过（只有既有 CadViewport >500kB warning）。`agent-webview-smoke` feature 定向 Rust 测试 7/7 通过，包含实际 OCCT Quick→active model→GLB 单测；smoke binary 离线 Cargo build 成功。使用 WebView2 `154.0.4258.53` 的匹配 EdgeDriver、隔离 `tauri-driver` 与 OCCT 7.9.3 manifest 运行 `npm.cmd run test:agent-webview`，五个场景全部 PASS。生产模型场景须在 revision fixture 前运行，避免合成更高 revision 触发 Vue stale-revision 防护。精确命令和边界见[生产几何隐藏 WebView 验收](./105-f7-g2-production-mesh-webview.md)。
