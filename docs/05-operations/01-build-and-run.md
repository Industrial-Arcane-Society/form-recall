# 构建、启动与运行记录

## 当前目录入口

- Tauri/Vue 桌面端：`src/apps/desktop/`
- 过渡期 Python 源与 CATIA adapter：`src/legacy/`
- 研究与运维文档：`docs/`
- 运行日志、临时模型和迁移前证据：`temp/`

## 开发启动

```powershell
# 在仓库根目录首次安装桌面端依赖（或依赖版本更新后）
npm ci --prefix src/apps/desktop

# 启动 Tauri 桌面应用
npm run dev

# 仅启动浏览器预览
npm run dev:web
```

从 `src/apps/desktop/` 目录运行时，`npm run dev` 只启动 Vite 浏览器预览，没有 Tauri/Rust IPC；需要验证桌面命令时使用 `npm run tauri:dev`。根目录脚本会将 Tauri 命令转发到该桌面包，因此依赖必须先安装在 `src/apps/desktop/node_modules/`；根目录没有独立的 npm lockfile。

旧 Python 过渡入口：

```powershell
Set-Location .\src\legacy
python -m ai_modeler --help
python -m ai_modeler --selftest
```

当前 Tauri 桌面端由 Rust/Tauri 管理会话、CADPlan、本地 OCCT 与 Quick 模式。Agent Host 使用应用固定 Node/DSH runtime，建模工具由 DSH 原生 Cordis 插件注册后路由到 Rust。Python 只保留旧入口及按需 CATIA COM adapter；CATIA 需要实际安装环境验收。

## 目录核对与已验收案例

Agent 提交带型号的描述时，宿主先做本地目录核对。使用 `cad_search_knowledge` 可查看命中的具体型号和来源。带标识符的未命中请求不会得到近邻型号的目录几何。可继续按用户明确的尺寸、原图或独立核实的标准建模。通用配方可供参考，目录状态与标准配方的适用条件分别核对。

左侧模型验收状态下的「已验收案例」提供本地案例库。

1. 用一条纯文字请求在空白零件中完成 Agent 建模，等宿主显示当前模型已验收。
2. 打开「已验收案例」，选择「保存当前模型为案例」。保存绑定该版本的几何指纹和验收状态。
3. 选择某条案例的「新建并填入描述」，确认新建零件，再发送填入的原描述。打开「新建时参考已验收案例」时，Agent 会在首轮引用相同描述的历史特征顺序，建模日志会记录命中。
4. 案例可单条移除。关闭参考开关可让下一次新建对照采用常规流程。已经发送过的会话消息随当前会话保留。

案例库写入应用本地数据目录的 `reviewed-cases/reviewed-cases.json`，最多保留最近保存的 64 个描述。条目包含用户主动保存的文字、几何指纹和特征种类与顺序。回用时传给模型的是特征顺序，本轮尺寸及验收仍须走当前的证据链。包含图片、历史轮次或待复核候选的项目暂不进入案例库。[离线研究与复现](../../artifacts/meta-incremental-research/README.md)

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

- 开发模式下，Python `temp_path()` 默认解析到仓库根目录 `temp/`；`AIMODELER_TEMP_DIR` 可显式覆盖该根目录。
- Agent 运行记录与 DSH 会话工作数据默认写入 `temp/agent-logs/`：旧 AgentLoop 使用 `agentloop/<运行 ID>/` 保存 `run.json`、预览和候选；当前 DSH 会话使用 `sessions/<会话 ID>/` 保存状态、用户轮次清单、原图副本、观测 JSONL 与会话专属 DSH home。selftest 日志写入 `selftest/<时间戳>/`。
- Python CLI 默认把持久会话数据写入 `temp/agent-logs/sessions/<session-id>/`，并把每轮结果、交付文件和该轮观测记录副本写入 `temp/agent-logs/agent-cli/<session-id>/turns/<turn-id>/`。`--output-dir` 会将这份 CLI 交付与运行记录副本写到指定目录；直接调用旧 AgentLoop API 时，显式传入 `output_dir` 也会覆盖运行目录。
- 自动生成的 CADPlan 工程、预览和普通 UI 截图默认写入 `temp/outputs/`（CADPlan SDK 为 `outputs/cadplan/`，桌面 UI 截图为 `outputs/model-shots/`）；联网查询缓存默认写入 `temp/cache/online.json`。
- 安装版通过 `AIMODELER_DATA_DIR` 把上述 Python `temp/` 路径映射到 Tauri 的 app-local-data 下的 `temp/` 子目录；运行数据不写进安装资源目录。
- 桌面端另有图片输入暂存与实时视图缓存：Rust 和 Python bridge 当前都使用 `%LOCALAPPDATA%/AI Modeler/ui-preview/`，输入暂存位于 `incoming/`，每个 UI 会话的 GLB 和 Agent 预览图位于各自会话目录。它是独立于 `temp/agent-logs/` 的 UI 缓存路径；当前代码没有看到它的会话结束或应用退出清理逻辑，因此这些输入副本与预览可能继续留在本机。不要把该目录误认为持久 Agent 日志或已归档交付物。
- Rust bridge 的诊断目前通过 stderr 输出；Tauri crate 未配置持久文件日志 sink，因此不能把终端诊断等同于已写入 `temp/agent-logs/` 的运行记录。
- 迁移前的模型、图片、报表和旧文档分别保存在 `temp/evidence-archive/` 与 `temp/legacy-documents/`。

历史文档中的运行命令可能对应旧目录布局。使用前先从 [文档索引](../README.md) 核对当前入口。
