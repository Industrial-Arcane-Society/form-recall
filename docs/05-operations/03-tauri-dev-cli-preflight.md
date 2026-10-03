# 根目录 Tauri dev：CLI 构建前置检查

## 实际入口与验证边界

当前根目录脚本为：

```text
npm run dev
  → npm --prefix src/apps/desktop run tauri:dev
  → tauri dev --config src-tauri/tauri.dev.conf.json
  → beforeDevCommand: npm run dev（子目录的 Vite）
  → Rust 构建
  → 执行 ai-modeler / Tauri 窗口
```

根 `npm run dev:web` 才只启动 Vite。根目录没有 `build` script，前端构建检查用 `npm --prefix src/apps/desktop run build`。

此前浏览器预览修复提交 `fbe8616` 属于独立分支 `feat/f7-dev-startup-fix`。它证明子目录 Vite/浏览器预览修复，不能称为根目录 `npm run dev` 的修复。当前 Tauri 资源配置修复分支 `feat/tauri-dev-resource-config` 从原 HEAD `3bd7782` 创建，不包含该浏览器提交，建议浏览器提交暂留原分支单独审阅。

本次没有执行实际 `tauri dev`、应用二进制、桌面窗口或 WebView，没有调用 provider 或读取真实配置/凭据。`tauri dev --help` 列出的 `--no-watch`、`--no-dev-server-wait` 和 `--no-dev-server` 都不表示“不运行应用”；没有据此绕过窗口禁令。只调用了带 `--help` 的 CLI、前端 build、定向配置检查与 Cargo check。

## 已复现的确定阻断

在上述基线的独立 worktree、无 `target/package-resources` 的状态下，以下命令退出 1：

```powershell
cargo check --manifest-path src/apps/desktop/src-tauri/Cargo.toml --lib --bins --offline --target-dir src/apps/desktop/src-tauri/target
```

失败发生在 `tauri_build::build()` 的 build script，关键错误为：

```text
resource path `target\package-resources\python` doesn't exist
```

base `tauri.conf.json` 无条件声明五项安装包资源。Tauri build helper 在 dev 构建时也复制这些 resources；`beforeDevCommand` 只启动 Vite，并不准备安装包资源。完整 staging 仅在 `beforeBuildCommand` 中，且涉及 OCCT runtime、CPython/pip 下载与生成目录替换。开发启动不应为编译 Rust 被迫运行完整 Windows 安装包 staging。

该错误是干净 target 的确定构建阻断。如果某个既有 checkout 已有完整 package-resources，则该阻断可能不出现；它不能直接解释已有窗口中的所有白屏/卡顿。历史已有 target 状态没有被删除、覆盖或用于“修复”。

## 最小修复

新增 dev-only fragment `src/apps/desktop/src-tauri/tauri.dev.conf.json`，仅将 `bundle.resources` 替换成空数组；`tauri:dev` 显式加载该 fragment。

发布 `tauri:build`、base 的 resources 五项映射、发布 staging hook 和窗口配置都保留。应用资源查找仍使用既有开发路径 `CARGO_MANIFEST_DIR/../../../legacy`；本次不复制 Python、OCCT、DSH 或安装包资源，也不创建占位文件。

本机已安装的 `tauri-build 2.7.1` 和 `tauri-codegen 2.7.1` 都使用 `TAURI_CONFIG` merge patch 读取 dev fragment；资源由 map 替换为 `[]`，不是给 map 增加空字段。

## 不开窗口的复跑命令与结果

在仓库根目录：

```powershell
# Help only; --help 返回，不执行 beforeDevCommand / 应用。
npm --prefix src/apps/desktop run tauri:dev -- --help

# 前端与 script/config 合约检查，不创建窗口。
npm --prefix src/apps/desktop run build
node --test src/apps/desktop/src/tauriDevConfig.test.mjs

# 与 CLI dev merge 相同的 fragment，只检查 Rust，不运行应用。
$devConfig = Get-Content -LiteralPath src/apps/desktop/src-tauri/tauri.dev.conf.json -Raw
$previousConfig = $env:TAURI_CONFIG
try {
  $env:TAURI_CONFIG = $devConfig
  cargo check --manifest-path src/apps/desktop/src-tauri/Cargo.toml --lib --bins --offline --target-dir src/apps/desktop/src-tauri/target
  $checkCode = $LASTEXITCODE
} finally {
  $env:TAURI_CONFIG = $previousConfig
}
if ($checkCode -ne 0) { throw 'Rust dev preflight failed.' }
```

基线 check 已复现资源缺失；dev fragment 下 `cargo check --lib --bins --offline` 成功，且 `target/package-resources` 仍不存在。前端 `vue-tsc -b && vite build` 成功（保留既有大 chunk 提示），定向 Node 配置检查 3/3 通过，`tauri:dev -- --help` 返回成功。没有把 check 或 help 结果当成已执行 Tauri 启动；实际 CLI config 路径解析、hook 执行和 app 运行仍属于下面的未验证边界。直接 Cargo check 不会自动加载 CLI `--config`，所以复跑须显式使用上述临时环境 merge。

## 尚未验证及后续最小边界

- CLI 真正执行 beforeDevCommand、等待 devUrl、Rust link、WebView2 创建和前端在桌面窗口中初始化尚未验证；Cargo check 不执行 binary，也不能替代 linker/runtime 证据。
- `devUrl=http://localhost:1420` 与 Vite `host=127.0.0.1` 保留当前配置；本次没有把它改成一个未经 WebView 实证的白屏“根因”。
- WebView 进程、JS/IPC console、真实 native initialize/settings/active-model 恢复均需运行中的窗口或另行认可的 WebView harness。纯 headless Chromium 不提供 native Tauri IPC。
- 若继续禁止启动窗口，可继续收集用户已运行根命令的启动前日志，定位编译错误、devUrl等待或进程退出；不能声称窗口首屏已通过。
- 未来只有在用户明确允许窗口验收后，才运行根 `npm run dev`，记录终端 build/startup 阶段、WebView console/DOM和关闭时状态。届时仍不需要调用模型 API即可验证首屏；真实模型属于独立授权范围。
