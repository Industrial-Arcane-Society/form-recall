# 浏览器开发预览启动与验收

## 两个入口

- `src/apps/desktop` 下 `npm run dev` 启动 Vite 浏览器预览。浏览器没有 Tauri IPC，因此页面只预览工作区、主题、视口与面板，不调用本机设置、运行时检测、Agent、Quick、工程或导出服务。
- 桌面开发入口为 `npm run tauri:dev`。它提供 Rust/Tauri 本地命令；真正的建模和文件操作在桌面程序中进行。本次修复没有启动该入口，也没有执行真实 provider 请求。

浏览器页显示“浏览器预览”及本地功能提示，本地服务控件禁用。DSH/OCCT/CATIA/Python 未在浏览器中检测，不能把它们显示为一直“检测中”或误报未安装。纯浏览器的 Enter、Ctrl+S/O/N 不发起本地操作。主题、网格、视图与面板仍可预览。

## 当前复现与根因

2026-10-03，从 `feat/rust-dsh-acp-host` 已提交 HEAD `3bd778267f67a77f333dc0b47c00a695bcd7dfaa` 创建干净的独立 worktree，在 `feat/f7-dev-startup-fix` 中检查。依赖复制到独立目录；所有 Vite cache、build 与 Playwright 产物写在该独立目录。共享工作树的 dirty 文件没有用于修复。

基线 `npm run dev -- --port 1437` 的 Vite ready 为 511 ms。首页 HTTP TTFB 37 ms，App.vue 19 ms；headless 页面已完整挂载。因此本次没有复现此前的 HTTP/module pending 白屏或约 100 秒延迟。当前 HEAD 已有 `server.watch.ignored: ['**/src-tauri/target/**']`，本次没有修改该配置。

当前可重复复现的启动错误：`startApplication()` 的 `!desktop` 分支仍调用 `initialize()`，而后者直接 `invoke('native_resource_status')`。纯浏览器没有 `window.__TAURI_INTERNALS__`，因此初始化被 `Cannot read properties of undefined (reading 'invoke')` 拒绝。页面显示“桌面核心未就绪”，footer 的本地 runtime 状态一直停在“检测中”。另外 index.html 未引用已存在的 favicon.svg，浏览器请求 favicon.ico 返回 404。

修复使浏览器分支仅初始化本地视图默认设置与预览状态，提前返回；桌面分支继续走 native resource/runtime/initialize。发送按钮和 submit handler 都检查 desktop，防止浏览器误入 DSH 安装或 Quick IPC。index.html 引用已有 favicon.svg。

## 定向检查

在 `src/apps/desktop` 下：

```powershell
node --test src/browserStartup.test.mjs src/nativeBootstrap.test.mjs src/agentStatus.test.mjs src/settingsBridge.test.mjs
npm run build
npm run dev -- --port 1437
```

Node 检查 14/14 通过。新增 4 项启动检查在独立 VM 中执行 App.vue 的生产启动分支/submit guard：浏览器零 native 调用；桌面仍经过资源、运行时与初始化；桌面失败仍进入依赖错误流程；浏览器提交提前返回。只去掉 TypeScript 的调用泛型，不改控制流；另有现有 native bootstrap、settings 和验收状态检查。

Vue 类型检查及生产 build 通过。保留既有 CadViewport 大于 500 KB 的 chunk 提示；该提示没有阻止构建。

HTTP 检查（不请求模型 API）：

```powershell
foreach ($path in @('/','/@vite/client','/src/main.ts','/src/App.vue','/src/style.css','/favicon.svg')) {
  curl.exe --silent --output NUL --max-time 15 --write-out "$path http=%{http_code} ttfb=%{time_starttransfer} total=%{time_total}\n" ('http://127.0.0.1:1437' + $path)
}
```

修复后一次缓存已热的检查：全部 HTTP 200；首页 TTFB 3.5 ms、App.vue 5.7 ms、favicon 2.4 ms。这些是本机单次延迟记录，不代表 benchmark 或证明历史白屏根因已全部覆盖。

## Headless 复跑

在独立 worktree 根目录启动隔离 browser session（默认 headless，不加 `--headed`）：

```powershell
npx --yes --package @playwright/cli playwright-cli -s=dev-preview open http://127.0.0.1:1437/
npx --yes --package @playwright/cli playwright-cli -s=dev-preview snapshot
npx --yes --package @playwright/cli playwright-cli -s=dev-preview console
```

运行前给页面安装 pageerror、console error、requestfailed 与 HTTP >=400 监听，再 goto 并等待“浏览器预览已就绪”。检查以下结果：

1. 页面标题、完整三栏 DOM 与 `canvas.three-canvas` 存在；加载时间设置合理上限，不能靠一直等待放行。
2. 输入文字后 Agent 与 Quick 的发送按钮都禁用；Enter 不进入依赖安装、不生成 busy turn、不调用 IPC。
3. 点击主题、参考网格、日志等纯浏览器控件可用。
4. 页面不出现 `undefined` 或“检测中”，无 pageerror、console error、失败请求和 HTTP 错误。
5. 截图写入独立目录 `output/playwright/`；最后只关闭该 named session 与本轮 Vite 进程。

本次 headless 记录：一次 goto 至就绪 DOM 为 127 ms，pageerror/console error/requestfailed/HTTP 错误均 0，WebGL canvas 1，Agent/Quick 发送禁用。截图保存在独立 worktree `output/playwright/browser-dev-ready.png`，没有加入 Git。

最后停止本轮 1437 server 后，用不带参数的 `npm run dev` 重启默认 1420：Vite ready 427 ms；首页 TTFB 19 ms、App.vue 3.8 ms；新 headless session 的 DOMContentLoaded 150.4 ms、app root 1、canvas 1、console errors/warnings 0，页面没有未解决的启动状态。HTTP 模块全部 200。这是新进程验收，未删除依赖缓存，也未创建或修改真实 Rust target 输出。

```powershell
npx --yes --package @playwright/cli playwright-cli -s=dev-preview close
```

这些证据证明浏览器预览启动与错误边界。真实桌面初始化、本机 OCCT/DSH、AgentHost→Tauri IPC→Vue、真实模型理解和 CATIA 不在本次验收范围。
