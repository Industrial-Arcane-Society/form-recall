# AI Modeler Desktop

Vue 3 / Three.js 工作区与 Tauri 2 / Rust 桌面应用。

## 开发

在仓库根目录进入桌面包：

```powershell
Set-Location src\apps\desktop
npm ci
```

- `npm run dev` 启动 Vite 浏览器预览。浏览器预览没有 Tauri/Rust IPC，桌面命令会显示为不可用。
- `npm run tauri:dev` 启动完整 Tauri 桌面程序。
- `npm run build` 执行 Vue 类型检查并生成生产前端资源。

## 构建 Windows 安装包

```powershell
Set-Location src\apps\desktop
npm run tauri:build
```

Tauri 构建会先执行 `npm run build` 和 `src/legacy/scripts/build_desktop_resources.mjs`，再生成 MSI 与 NSIS 安装包。资源脚本校验并 staging OCCT、固定 DSH/Node 资源和按需 CATIA Python/pywin32 资源；缺少固定归档时需要网络下载。安装包输出在 `src-tauri\target\release\bundle\msi\` 与 `src-tauri\target\release\bundle\nsis\`。生成成功不代表已完成干净机器安装、DSH 冷启动或 CATIA 验收。

## Rust 测试

仓库脚本会为 Windows 测试宿主准备 Tauri 测试资源：

```powershell
# 从仓库内任意目录运行
Set-Location (git rev-parse --show-toplevel)
$env:AIMODELER_TEST_NODE = (Get-Command node.exe).Source
$env:AIMODELER_TEST_ACP_FIXTURE = Join-Path $PWD 'src\apps\desktop\src-tauri\tests\fixtures\fake-acp.mjs'
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline
```

## 运行边界

- Rust/Tauri 负责桌面命令、Agent Host、会话/CAD 状态、CADPlan 和本地 OCCT 几何。
- Rust Agent Host 通过应用固定 Node/DSH runtime 启动 DSH；CAD 工具由 DSH 原生 Cordis 插件注册，再路由到 Rust 执行器。
- Quick 模式的本地建模与导出走 Rust，不启动 Python。
- CATIA 导出/推送按用户请求启动应用私有 CPython/pywin32 adapter。CATIA COM 与几何结果仍需在实际 CATIA 环境验收。
- 旧 Python UI bridge 仍保留在 `src/legacy/` 供旧入口使用，但不在当前 Tauri 桌面 UI 的调用路径中。

架构和迁移进度见仓库 [`docs/README.md`](../../../docs/README.md) 与 [迁移 Todo](../../../docs/06-roadmap/01-migration-todo.md)。
