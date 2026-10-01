# AI Modeler Desktop

Vue 3 / Three.js 工作区与 Tauri 2 / Rust 桌面壳。

## 开发

在仓库根目录运行：

```powershell
Set-Location src\apps\desktop
npm ci
npm run tauri:dev
```

当前桌面壳仍启动 `src/legacy/` 中的 Python JSONL bridge。Tauri 开发模式从 Cargo manifest 相邻的 checkout 路径定位 Python backend；安装包把过渡期 Python 源与 `docs/` 分别 stage 到 app resources。

## 构建和检查

```powershell
npm run build
cargo test --manifest-path src-tauri\Cargo.toml
```

Windows 安装包命令为 `npm run tauri:build`。它还会执行 `src/legacy/scripts/build_desktop_resources.mjs`，联网准备嵌入式 Python 与依赖安装资源。

## 状态边界

Vue 负责交互显示，Rust 负责 Tauri 命令和当前 Python 子进程生命周期；Agent、CADPlan 与通用几何目前仍在 Python。目标迁移路线见仓库 [`docs/README.md`](../../../docs/README.md)。
