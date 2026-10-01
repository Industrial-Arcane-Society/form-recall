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

- Agent run、selftest 运行日志写入 `temp/agent-logs/`。
- 自动生成的 CADPlan 工程、预览和截图写入 `temp/outputs/`。
- 开发缓存位于 `temp/cache/`；安装版运行数据使用用户数据目录。
- 迁移前的模型、图片、报表和旧文档分别保存在 `temp/evidence-archive/` 与 `temp/legacy-documents/`。

历史文档中的运行命令可能对应旧目录布局。使用前先从本索引核对当前入口。
