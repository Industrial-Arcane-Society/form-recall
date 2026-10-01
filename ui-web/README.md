# AI Modeler 桌面界面

这个目录包含 Tauri 2 / Rust 桌面壳和 Vue 3 / Three.js 工作区。建模、Agent、参数化文档、导出、MCP 与 CATIA 能力仍由仓库中的 Python 包提供。

## 开发

需要 Node.js、Rust 工具链、Windows WebView2，以及已安装项目 Python 依赖的环境。

```powershell
cd ui-web
npm ci
npm run tauri:dev
```

`tauri:dev` 会启动 Vite 和 Tauri。Tauri 进程启动 `python -m ai_modeler --ui-bridge`，以 UTF-8 JSONL 交换请求、响应和 Agent 事件。

## 构建

```powershell
cd ui-web
npm run build
npm run tauri:build -- --no-bundle
```

Tauri EXE 会输出到 `src-tauri/target/release/ai-modeler.exe`。需要 Windows 安装包时，运行 `npm run tauri:build`。

构建之后，仓库根目录的 `python -m ai_modeler` 和桌面快捷方式会转发到这个 EXE，并把当前 Python 解释器路径传给后端。旧 Tk 界面继续通过 `python -m ai_modeler --legacy-ui` 启动；未构建 Tauri EXE 时，无参数启动也会退回旧界面。

## 桌面桥接

- Rust 通过 Tauri `invoke` 和 `Channel` 连接 Python JSONL 桥接层。
- Python 负责模型计算、Agent 会话、工程文件、导出、设置、MCP 和 CATIA 操作。
- Python 为每个模型版本写入带特征颜色的 GLB 与元数据；Rust 只允许读取用户数据目录内的 `AI Modeler/ui-preview` 资源。
- 原生对话框负责选择图片、打开工程和保存导出文件。剪贴板图片通过受限的 Rust 命令写入同一预览资源目录。
- Three.js 视口分离为延迟加载模块，包含标准视角、轨道控制、网格、坐标轴和特征边线。

## 本地检查

```powershell
npm run build
cargo check --manifest-path src-tauri/Cargo.toml
```

Python 桥接契约和模型资源测试位于仓库根目录 `tests/test_ui_bridge.py`。
