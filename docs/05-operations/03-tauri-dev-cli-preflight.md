# Tauri dev 与 Vite 启动核查（2026-10-04）

## 当前命令入口

当前合并后的仓库根目录有 `package.json`：

~~~text
仓库根目录 npm run dev      → Tauri 桌面开发启动
仓库根目录 npm run dev:web  → Vite 浏览器预览
桌面目录 npm run dev         → Vite 浏览器预览
桌面目录 npm run tauri:dev   → Tauri 桌面开发启动
桌面目录 npm run build       → vue-tsc -b && vite build
~~~

根目录 `npm run dev` 依次调用 `src/apps/desktop` 的 `tauri:dev`，再运行 `tauri dev --config src-tauri/tauri.dev.conf.json`；根目录 `npm run dev:web` 才只启动 Vite。Vite 固定绑定 `127.0.0.1:1420`（`strictPort: true`），Tauri `devUrl` 已对齐为 `http://127.0.0.1:1420`。此前 `localhost` 在本机 Node DNS 查询中优先解析到 `::1`，而 Vite 只监听 IPv4；该不一致现已由配置回归锁定并修正，但本次没有实际启动 Tauri/WebView，不能确认它就是用户所报故障的唯一原因。

`tauri.dev.conf.json` 覆盖开发构建的 `bundle.resources` 为空数组，避免开发编译被安装包资源 staging 阻塞；发布资源清单保持原样。

## 2026-10-04 headless 验证

本次没有打开 Tauri 窗口，也没有运行桌面二进制。

### 合并前 main 基线 b597a69（历史）

在 src/apps/desktop 启动 npm run dev -- --host 127.0.0.1 --port 5173 --strictPort，Vite 约 1.11 秒就绪。使用 Playwright CLI 无头打开并快照，得到完整的建模工作区 DOM；页面标题为 AI Modeler · 参数化建模工作台，状态显示“后端未连接”。因此，本次没有在 Vite 浏览器预览中复现“完全没有界面”，但基线仍按桌面流程初始化，预览状态不够准确。Console 有一个 favicon.ico 404。

### feat/rust-dsh-acp-host 集成分支

在相同包目录启动 npm run dev -- --host 127.0.0.1 --port 5174 --strictPort，Vite 约 1.03 秒就绪。Playwright CLI 快照显示完整工作区、明确的“浏览器预览已就绪”状态和被禁用的本机操作；Console 为 0 error、0 warning。该结果验证的是 headless Vite 页面渲染，不是桌面 WebView、Rust IPC 或 DSH Agent 运行。

在合并前 main 与集成分支中，`npm exec --offline tauri -- --version` 均输出 tauri-cli 2.12.1；`npm run tauri:dev -- --help` 均以 exit code 0 返回。当前本地依赖安装状态下没有复现“tauri is not recognized”。这不能证明用户机器缺失依赖时也可运行。

集成分支执行 npm run build 成功；Vite 提示 CadViewport chunk 超过 500 kB，这是打包体积提示，不是构建失败。

## 旧资源构建阻断与当前修复

此前在干净 target 上，Tauri build script 因 base 配置无条件声明的 target/package-resources/python 不存在而失败。开发构建不应被迫下载和 staging 发布运行时资源。集成分支的 tauri.dev.conf.json 将开发构建的 bundle.resources 覆盖为空数组；其 npm 级配置测试、Rust offline 检查和前端 build 已通过，发布 tauri:build 的资源映射保持原样。

## 2026-10-04 合并后 CLI 复核

- 仓库根目录运行 `npm run dev:web`，Vite 574 ms 就绪并监听 `127.0.0.1:1420`；对 `http://127.0.0.1:1420/` 与 `http://localhost:1420/` 的请求均返回 HTTP 200。桌面包目录直接运行 Vite 并请求 `/`、`/src/main.ts` 和 `/src/App.vue` 也均返回 HTTP 200。
- `npm run build` 通过；合并树的前端 Node 回归为 113/113；Rust 库测试通过 302/302。Rust 集成运行使用打包流程已校验的 OCCT runtime manifest。
- `npm run tauri:build` 成功生成 MSI 与 NSIS 安装包。此项验证生产编译和打包，不等价于启动安装后的桌面窗口。
- 子 Agent 运行 `npm run tauri:dev -- --help` 返回 0；没有启动 Tauri GUI。实际 `npm run dev` 的 `beforeDevCommand`、Tauri 等待 `devUrl`、WebView2 创建和首屏仍未验证。

## 仍未验证

- 变更后的配置契约已通过 Node CLI 回归；Vite/Tauri 的实际 dev 进程、`beforeDevCommand`、Rust link、`devUrl` 导航、WebView2 创建、Tauri IPC 初始化和桌面首屏仍未执行或验证。
- “npm run dev 完全无界面”的原报告仍未由本次 headless 配置检查复现。此前隐藏 WebView CLI harness 验证的是 smoke production bundle 的首屏 mount 与 Channel→Vue handler 投递，不使用 live Vite dev server，因此不构成 `npm run dev` 实测。排查时请保留工作目录、完整命令、终端输出与退出码。
- 后续桌面验收需明确允许启动 Tauri 窗口。当前记录不把 CLI help、Vue build 或 headless Chromium 页面当作桌面启动通过。

## 2026-10-04 devUrl/Vite 地址对齐

- 源码契约：`vite.config.ts` 绑定 `127.0.0.1:1420` 且启用 `strictPort`；Tauri `build.devUrl` 改为同一 IPv4 地址。配置 Node 回归会从 Vite 配置读取 host/port 并断言与 devUrl 一致。
- `csp` 保持 `null`，default capability 只按窗口名 `main` 授权，没有按主机名列出的 allowlist，因此没有需要同步改写的 CSP/allowlist。开发配置也没有覆盖 devUrl。
- Headless CLI：在 `src/apps/desktop` 执行 `node --test src/tauriDevConfig.test.mjs`，4/4 通过；同一新增断言在修改前以 `localhost` 对 `127.0.0.1` 失败。`npm ci --no-audit --no-fund` 后 `npm run build` 通过（Vue typecheck + Vite production build）；Vite 提示 `CadViewport` chunk 为 637.32 kB，超过 500 kB 建议阈值。`git diff --check` 通过。没有运行 `tauri dev`。
- 未测：可见窗口、真实 Tauri dev 启动、WebView2 首屏/IPC。本修正降低了 localhost 地址族错配风险，但用户报告的根因尚未由运行时确认。
