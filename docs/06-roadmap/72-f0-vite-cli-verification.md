# F0 Vite CLI 启动实测

## 环境与命令

- 工作区：`C:\Users\lycohana\.codex\worktrees\f0-vite-dev-url\AI-_-`
- 分支：`fix/f0-vite-dev-url`
- 桌面包目录：`src/apps/desktop`
- `package.json` 中 `npm run dev` 的实际脚本是 `vite`。
- 启动命令：`npm.cmd run dev`

## 观测结果

- Vite `8.3.2` 于 `1103 ms` ready，输出本地地址 `http://127.0.0.1:1420/`。
- 监听地址为 `127.0.0.1:1420`，与 `vite.config.ts` 的 host/port/strictPort 和 Tauri devUrl 相符。
- `GET /` 返回 `200 text/html`，包含页面 title 与 `/src/main.ts` 入口。
- `GET /src/main.ts` 返回 `200 text/javascript`，包含转换后的 Vue imports 与 mount 代码。
- Ctrl-C 停止服务后检查 TCP listener，端口 `1420` 已释放；worktree 状态保持干净。

## 证据边界

本次确认 Vite CLI 能启动、地址绑定符合配置且首页和入口模块可由 HTTP 读取。没有启动 Tauri 窗口、浏览器或 WebView2，因此不能据此证明可见 UI 绘制、交互、原生 IPC 或安装后的运行状态，也不能声称用户报告的“完全没有界面”已经复现或修复。

## 2026-10-04 无界面浏览器复核

- 工作区：`C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-`，分支 `feat/f7-p0-completion`。
- 在 `src/apps/desktop` 运行 `npm run dev -- --host 127.0.0.1 --strictPort`，Vite `8.3.2` 于 `592 ms` ready，监听 `127.0.0.1:1420`。
- 使用 Playwright CLI + 本机 Chrome 的无界面会话打开 `http://127.0.0.1:1420/`。标题为 `AI Modeler · 参数化建模工作台`；DOM snapshot 包含 `.app-frame`、建模对话、输入区、模型视口与右侧面板，不是空白 DOM。
- 浏览器 Console：0 errors、0 warnings；请求列表无失败项（28 个静态资源请求默认未展开）。预览模式下建模/文件操作按钮显示 disabled，页面明确显示“浏览器预览”。
- 关闭浏览器并 Ctrl-C 停止 Vite 后，TCP 1420 无监听。PowerShell 对 Ctrl-C session 返回 exit code 1；端口释放已单独核验。

该结果验证的是桌面包目录 `npm run dev`（Vite-only）与其浏览器预览。仓库根目录 `npm run dev` 会启动 Tauri；本次没有启动可见 Tauri 窗口或 WebView2，故用户报告的 Tauri 空白窗口仍不能据此判定已解决。Tauri `devUrl` 与 Vite IPv4 host 的配置修复及既有隐藏 WebView harness 证据见总 Todo 与 [WebView harness](./62-f7-webview-driver-harness.md)。

## 2026-10-04 指定 main 基线 CLI 复核

- 基线：`d11ad84af28f41180186657af87f4ba5f4007fdb`，隔离 worktree `f0-startup-cli-audit`。
- `node --test src/tauriDevConfig.test.mjs`：4/4 通过。它锁定根目录脚本分流、开发配置覆盖资源映射、`devUrl`/Vite 地址一致和发布资源配置；这是配置级回归，不会启动 Tauri。
- `npm.cmd run build`：通过；仅有既有 `CadViewport` 大 chunk 提示。
- 在仓库根目录运行 `npm.cmd run dev:web -- -- --host 127.0.0.1 --strictPort`：Vite 8.3.2 于 559 ms 就绪，`GET /` 与 `GET /src/main.ts` 均返回 200；Ctrl-C 后监听端口释放。该结果验证根目录 `dev:web` 到 Vite 的实际转发。
- `npm.cmd run tauri:dev -- --help` 显示 Tauri dev 命令读取 `devUrl` 并执行 `beforeDevCommand`；帮助调用没有启动应用。根目录 `npm run dev` 配置为调用该 Tauri dev 脚本，它会打开桌面窗口。按本次“不打开可见软件”的约束，没有运行实际 Tauri dev，因此没有复现或排除用户报告的空白桌面窗口。
- 尝试 `npm.cmd run test:agent-webview`，harness 在启动应用前因找不到 `tauri-driver` 与匹配 WebView2 Runtime 的 `msedgedriver` 退出。没有 WebView2 session，也没有应用故障日志；这属于验收前置条件缺失，不能记作应用测试失败或通过。

本次没有发现可由 CLI 证据复现的启动缺陷。当前未验证层是 Tauri dev 进程启动、WebView2 创建后的可见窗口绘制/渲染，以及原生 IPC。最强的下一步 CLI 验收是准备匹配版本的 `tauri-driver` 与 `msedgedriver` 后运行 `npm run test:agent-webview`；该 harness 可无可见交互地验证生产 Vue 首屏 mount 和四类 Channel 事件处理，但仍不证明用户屏幕上的窗口像素与布局质量。

## 未合入 F0 分支行为对照

- `e0007e9873874616899db672ce5c94978a0832f3`（dev resource decouple）：`tauri.dev.conf.json` 在 `origin/main` 中与该提交一致，仍以空的 dev-only bundle resource map 覆盖安装包资源；当前 `tauriDevConfig.test.mjs` 保留此断言，并额外检查 Vite host 与 Tauri `devUrl` 对齐。主线的行为/测试更完整，不需要合入旧分支。
- `fbe8616eacdf1053cde68bf362643f42147dfe9c`（browser preview startup）：当前主线保留 `App.vue` 的浏览器预览早退路径和 `browserStartup.test.mjs`。在浏览器态 `startApplication()` 不触发任何 Tauri IPC，设置预览状态；提交给建模时也由 guard 早退。`node --test src/tauriDevConfig.test.mjs src/browserStartup.test.mjs` 合计 8/8 通过。App.vue 自该提交后有大量后续功能演进，但启动语义仍由当前测试锁定；无需合入旧分支。
