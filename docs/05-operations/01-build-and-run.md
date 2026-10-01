# 构建、启动与运行记录

## 当前目录入口

- Tauri/Vue 桌面端：`src/apps/desktop/`
- 过渡期 Python 资源根：`src/legacy/`
- 新研究文档：`docs/`
- 运行日志、临时模型和迁移前证据：`temp/`

## 开发启动

```powershell
# 在仓库根目录执行：桌面前端和 Tauri
Set-Location .\src\apps\desktop
npm ci
npm run tauri:dev
```

Python 过渡入口：

```powershell
Set-Location C:\Users\lycohana\Desktop\code\AI-_-
Set-Location .\src\legacy
python -m ai_modeler --help
python -m ai_modeler --selftest
```

真实项目路径请以本地 checkout 为准。Windows 启动脚本位于 `src/legacy/启动脚本/`，其调用边界将随着 Agent/桌面 host 迁移而收缩。

## 定向验证命令

```powershell
# Python legacy test suite
Set-Location src\legacy
python -m pytest tests

# Vue typecheck / production build
Set-Location ..\apps\desktop
npm ci
npm run build

# Rust Tauri unit tests
cargo test --manifest-path src-tauri\Cargo.toml
```

完整 Windows 安装包构建会运行 `src/legacy/scripts/build_desktop_resources.mjs`，下载 CPython 和 pip bootstrap 资源并准备依赖清单；它比单独 `npm run build` 多出网络和资源 staging 步骤。发布安装包还须在干净 Windows 账户冷启动验证。

## 输出与归档

- 开发模式下，Python `temp_path()` 默认解析到仓库根目录 `temp/`；`AIMODELER_TEMP_DIR` 可显式覆盖该根目录。
- Agent 运行记录与 DSH 会话工作数据默认写入 `temp/agent-logs/`：旧 AgentLoop 使用 `agentloop/<运行 ID>/` 保存 `run.json`、预览和候选；当前 DSH 会话使用 `sessions/<会话 ID>/` 保存状态、用户轮次清单、原图副本、观测 JSONL 与会话专属 DSH home。selftest 日志写入 `selftest/<时间戳>/`。
- Python CLI 默认把持久会话数据写入 `temp/agent-logs/sessions/<session-id>/`，并把每轮结果、交付文件和该轮观测记录副本写入 `temp/agent-logs/agent-cli/<session-id>/turns/<turn-id>/`。`--output-dir` 会将这份 CLI 交付与运行记录副本写到指定目录；直接调用旧 AgentLoop API 时，显式传入 `output_dir` 也会覆盖运行目录。
- 自动生成的 CADPlan 工程、预览和普通 UI 截图默认写入 `temp/outputs/`（CADPlan SDK 为 `outputs/cadplan/`，桌面 UI 截图为 `outputs/model-shots/`）；联网查询缓存默认写入 `temp/cache/online.json`。
- 安装版通过 `AIMODELER_DATA_DIR` 把上述 Python `temp/` 路径映射到 Tauri 的 app-local-data 下的 `temp/` 子目录；运行数据不写进安装资源目录。
- 桌面端另有图片输入暂存与实时视图缓存：Rust 和 Python bridge 当前都使用 `%LOCALAPPDATA%/AI Modeler/ui-preview/`，输入暂存位于 `incoming/`，每个 UI 会话的 GLB 和 Agent 预览图位于各自会话目录。它是独立于 `temp/agent-logs/` 的 UI 缓存路径；当前代码没有看到它的会话结束或应用退出清理逻辑，因此这些输入副本与预览可能继续留在本机。不要把该目录误认为持久 Agent 日志或已归档交付物。
- Rust bridge 的诊断目前通过 stderr 输出；Tauri crate 未配置持久文件日志 sink，因此不能把终端诊断等同于已写入 `temp/agent-logs/` 的运行记录。
- 迁移前的模型、图片、报表和旧文档分别保存在 `temp/evidence-archive/` 与 `temp/legacy-documents/`。

历史文档中的运行命令可能对应旧目录布局。使用前先从本索引核对当前入口。
