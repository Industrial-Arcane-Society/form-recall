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
- 首次尝试 `npm.cmd run test:agent-webview` 时，harness 在启动应用前因找不到 `tauri-driver` 与匹配 WebView2 Runtime 的 `msedgedriver` 退出；该次只有验收前置条件缺失证据。
- 后续将驱动安装在此隔离 worktree 的 `temp/acceptance/f0-webview-drivers-20261004`：`tauri-driver 2.1.0` 使用独立 `CARGO_HOME`/`--root` 编译安装；从 Microsoft 官方版本目录下载与已安装 WebView2 Runtime `154.0.4258.53` 匹配的 EdgeDriver。归档 SHA-256 为 `16ACCCCA3DD71DA28348A5A6852B9FA681D1F177F758339EABACFBD3CAC8D72B`；`msedgedriver --version` 报告 `154.0.4258.53`。
- 不带覆盖配置的首次构建失败于 Tauri build script：主 `tauri.conf.json` 的 bundle resource map 要求生成物 `target/package-resources/RUNTIME-INFORMATION.txt`，而干净 worktree 中没有该打包目录。没有运行会下载/准备 Python 与 CATIA bootstrap payload 的 `build_desktop_resources.mjs`。为保持 smoke-only 构建无打包依赖，第二次 harness 命令仅在子进程环境设置 `TAURI_CONFIG={"bundle":{"resources":[]}}`，清空该 smoke 构建继承的发布资源映射；没有修改配置文件或 refs。
- 带该临时覆盖运行 `npm.cmd run test:agent-webview`，退出码 0：Vue production build、专用 `ai-modeler-webview-smoke.exe` 构建成功；隐藏 Windows WebView2 session 四场景 `pure-qa`、`cad-tool-failure`、`accepted-current`、`stale-finish` 全部 `PASS`。验证到 production Vue 首屏 mount 与 WebView JavaScript → Rust Tauri Channel → production Vue handler 的事件和状态更新；日志明确显示未使用 DSH/provider/CATIA。

当前隐藏 WebView2 smoke 路径可以启动并通过生产 Vue 首屏/Channel 行为验收，未发现该路径上的可复现启动缺陷。仍未验证的是普通 `npm run dev`/Tauri dev 进程的实际启动、可见窗口绘制与屏幕像素/布局；该任务按“不打开用户软件”的约束没有运行这些路径。smoke binary 使用 fixture-only handler，不能代表真实后端或 CATIA/provider 集成。此前配置级和 Vite HTTP 检查仍只证明配置与前端服务，不替代可见窗口验收。

## 未合入 F0 分支行为对照

- `e0007e9873874616899db672ce5c94978a0832f3`（dev resource decouple）：`tauri.dev.conf.json` 在 `origin/main` 中与该提交一致，仍以空的 dev-only bundle resource map 覆盖安装包资源；当前 `tauriDevConfig.test.mjs` 保留此断言，并额外检查 Vite host 与 Tauri `devUrl` 对齐。主线的行为/测试更完整，不需要合入旧分支。
- `fbe8616eacdf1053cde68bf362643f42147dfe9c`（browser preview startup）：当前主线保留 `App.vue` 的浏览器预览早退路径和 `browserStartup.test.mjs`。在浏览器态 `startApplication()` 不触发任何 Tauri IPC，设置预览状态；提交给建模时也由 guard 早退。`node --test src/tauriDevConfig.test.mjs src/browserStartup.test.mjs` 合计 8/8 通过。App.vue 自该提交后有大量后续功能演进，但启动语义仍由当前测试锁定；无需合入旧分支。
