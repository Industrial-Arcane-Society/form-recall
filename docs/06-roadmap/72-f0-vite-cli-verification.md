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
