# Tauri dev 与 Vite 启动核查（2026-10-04）

## 当前命令入口

仓库根目录没有 package.json。桌面前端包位于 src/apps/desktop，且它的脚本定义如下：

~~~text
npm run dev       → Vite 浏览器预览
npm run tauri:dev → Tauri 桌面开发启动
npm run build     → vue-tsc -b && vite build
~~~

因此，从仓库根目录运行 npm run dev 并不是本仓库定义的命令。从 src/apps/desktop 运行 npm run dev 才是 Vite；它不会启动 Rust 宿主，也不连接本机建模后端。桌面程序使用 npm run tauri:dev。

main 的 src/apps/desktop/package.json 将 tauri:dev 定义为 tauri dev。当前 feat/rust-dsh-acp-host 集成分支已改为 tauri dev --config src-tauri/tauri.dev.conf.json；该 dev-only 配置覆盖 bundle.resources 为空数组，绕过开发编译对安装包资源 staging 的要求，发布资源清单不变。

## 2026-10-04 headless 验证

本次没有打开 Tauri 窗口，也没有运行桌面二进制。

### main 基线 b597a69

在 src/apps/desktop 启动 npm run dev -- --host 127.0.0.1 --port 5173 --strictPort，Vite 约 1.11 秒就绪。使用 Playwright CLI 无头打开并快照，得到完整的建模工作区 DOM；页面标题为 AI Modeler · 参数化建模工作台，状态显示“后端未连接”。因此，本次没有在 Vite 浏览器预览中复现“完全没有界面”，但基线仍按桌面流程初始化，预览状态不够准确。Console 有一个 favicon.ico 404。

### feat/rust-dsh-acp-host 集成分支

在相同包目录启动 npm run dev -- --host 127.0.0.1 --port 5174 --strictPort，Vite 约 1.03 秒就绪。Playwright CLI 快照显示完整工作区、明确的“浏览器预览已就绪”状态和被禁用的本机操作；Console 为 0 error、0 warning。该结果验证的是 headless Vite 页面渲染，不是桌面 WebView、Rust IPC 或 DSH Agent 运行。

在 main 与集成分支中，npm exec --offline tauri -- --version 均输出 tauri-cli 2.12.1；npm run tauri:dev -- --help 均以 exit code 0 返回。当前本地依赖安装状态下没有复现“tauri is not recognized”。这不能证明用户机器缺失依赖时也可运行。

集成分支执行 npm run build 成功；Vite 提示 CadViewport chunk 超过 500 kB，这是打包体积提示，不是构建失败。

## 旧资源构建阻断与当前修复

此前在干净 target 上，Tauri build script 因 base 配置无条件声明的 target/package-resources/python 不存在而失败。开发构建不应被迫下载和 staging 发布运行时资源。集成分支的 tauri.dev.conf.json 将开发构建的 bundle.resources 覆盖为空数组；其 npm 级配置测试、Rust offline 检查和前端 build 已通过，发布 tauri:build 的资源映射保持原样。

## 仍未验证

- tauri:dev 的实际 beforeDevCommand、Rust link、devUrl 等待、WebView2 创建、Tauri IPC 初始化和窗口首屏均未执行或验证。
- “npm run dev 完全无界面”的原报告无法由本次 main Vite headless 验证复现；如果原命令实际在桌面环境里运行，请保留它的工作目录、命令行、终端输出与进程退出码，才能区分 Vite 浏览器预览与 Tauri 桌面启动故障。
- 后续桌面验收需明确允许启动 Tauri 窗口。当前记录不把 CLI help、Vue build 或 headless Chromium 页面当作桌面启动通过。
