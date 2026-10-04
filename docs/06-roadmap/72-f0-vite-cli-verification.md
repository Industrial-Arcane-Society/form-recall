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
