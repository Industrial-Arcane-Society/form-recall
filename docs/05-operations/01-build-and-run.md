# 构建、启动与运行记录

## 当前目录入口

- Tauri/Vue 桌面端：`src/apps/desktop/`
- 过渡期 Python 源与 CATIA adapter：`src/legacy/`
- 研究与运维文档：`docs/`
- 运行日志、临时模型和迁移前证据：`temp/`

## 开发启动

```powershell
# 在仓库根目录运行
Set-Location .\src\apps\desktop
npm ci
npm run tauri:dev
```

`npm run dev` 只启动 Vite 浏览器预览，没有 Tauri/Rust IPC；需要验证桌面命令时使用 `npm run tauri:dev`。

旧 Python 过渡入口：

```powershell
Set-Location .\src\legacy
python -m ai_modeler --help
python -m ai_modeler --selftest
```

当前 Tauri 桌面端由 Rust/Tauri 管理会话、CADPlan、本地 OCCT 与 Quick 模式。Agent Host 使用应用固定 Node/DSH runtime，建模工具由 DSH 原生 Cordis 插件注册后路由到 Rust。Python 只保留旧入口及按需 CATIA COM adapter；CATIA 需要实际安装环境验收。

## 定向验证命令

```powershell
# Vue 类型检查与生产构建
Set-Location .\src\apps\desktop
npm ci
npm run build

# Rust 单测（Windows Tauri 测试宿主 wrapper）
Set-Location (git rev-parse --show-toplevel)
$env:AIMODELER_TEST_NODE = (Get-Command node.exe).Source
$env:AIMODELER_TEST_ACP_FIXTURE = Join-Path $PWD 'src\apps\desktop\src-tauri\tests\fixtures\fake-acp.mjs'
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline
```

## Windows 安装包

```powershell
Set-Location .\src\apps\desktop
npm run tauri:build
```

该命令先构建前端并运行 `src/legacy/scripts/build_desktop_resources.mjs`，再生成 MSI 和 NSIS。资源脚本校验 OCCT runtime，并 staging 固定 DSH/Node 资源、知识库/提示词及按需 CATIA CPython/pywin32 adapter 资源；固定归档未缓存时需要网络。默认运行的 Tauri 二进制配置在 `src/apps/desktop/src-tauri/Cargo.toml` 的 `default-run`。

安装包输出位于：

- `src/apps/desktop/src-tauri/target/release/bundle/msi/`
- `src/apps/desktop/src-tauri/target/release/bundle/nsis/`

成功编译只证明 bundle 产出，不等于已验证安装、干净机器冷启动、真实 DSH 多模态行为或 CATIA COM 几何结果。发布前应在目标 Windows 账户安装并记录完整启动结果。

## 输出与归档

- Agent run、selftest 运行日志写入 `temp/agent-logs/`。
- 自动生成的 CADPlan 工程、预览和截图写入 `temp/outputs/`。
- 开发缓存位于 `temp/cache/`；安装版运行数据使用用户数据目录。
- 迁移前的模型、图片、报表和旧文档分别保存在 `temp/evidence-archive/` 与 `temp/legacy-documents/`。

历史文档中的运行命令可能对应旧目录布局。使用前先从 [文档索引](../README.md) 核对当前入口。
