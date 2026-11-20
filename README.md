# FormRecall

FormRecall 是面向机械零件和工装夹具的 Windows 参数化 CAD 工作台。它把自然语言、参考图和尺寸输入转成可检查的 CADPlan，并用本地几何内核构建和预览零件。默认桌面使用 Go/MyGo，Rust 引擎负责模型、工程状态和 Agent 会话。

> **所有进入 `main` 的变更都必须通过 Pull Request。** 请从 `main` 新建工作分支，把提交推送到该分支，再向 `main` 发起 PR。代码、文档和小修复都遵循同一流程。工作分支统一使用 `类型/简短描述`（如 `docs/update-build-guide`）。允许的前缀和完整命名规则见[贡献指南](CONTRIBUTING.md#分支命名)。

## 当前能力

| 入口 | 用途 |
| --- | --- |
| Quick 建模 | 根据零件参数在本地生成模型，查看特征、几何检查和三维预览 |
| Agent 建模 | 通过自然语言和参考图生成或修改 CADPlan，由 DSH/Cordis 调用建模工具 |
| 旧版 Python | 保留夹具设计向导、历史研究实现和 CATIA 过渡能力 |

MyGo 管理自定义标题栏、工作台与系统对话框，原生 OpenGL 子窗口显示 GLB 模型。桌面通过 JSON Lines 调用常驻 `engine-host`，它与保留的 Vue/Tauri 兼容入口共同复用 `cad-engine`。Open CASCADE（OCCT）负责本地几何构建。CATIA 导出和复核需要实际的 Windows CATIA 环境。导出文件、几何检查和参考图相似度各有独立的验收依据。

## 开发启动

在 Windows x64 上准备 Git、PowerShell 7、Rust MSVC 工具链和 Visual Studio C++ Build Tools。仓库脚本提供固定 Go SDK，Node.js/npm 可用于根目录命令。首次构建需要获取尚未缓存的依赖。OCCT 运行时准备和打包参数见构建指南。[原生桌面构建](docs/05-operations/03-native-desktop-build.md)

```powershell
git clone https://github.com/Industrial-Arcane-Society/form-recall.git
Set-Location form-recall
./scripts/bootstrap-go.ps1
npm run build:native
npm run dev -- -SkipBuild -OcctManifest '<OCCT目录>/runtime-manifest.json'
```

根目录命令对应以下入口。

| 命令 | 入口 |
| --- | --- |
| `npm run dev` | 构建并启动 MyGo 原生桌面 |
| `npm run build:native` | 构建 Go 桌面和 Rust 引擎，默认 release |
| `npm run test:native` | Go 依赖核验、vet、测试，以及 Rust 核心和进程测试 |
| `npm run package:native` | 生成带 OCCT 与资源的便携目录及 ZIP，并核验包内文件 |
| `npm run dev:tauri` | 启动 Vue/Tauri 兼容桌面 |
| `npm run dev:web` | 启动 Vue 浏览器预览 |

当前根目录使用带 `:native` 后缀的构建和测试命令。全量 Rust 测试存在 23 项基线失败，完整结果和真实引擎回归范围见验证记录。[原生桌面验证记录](docs/05-operations/04-native-desktop-validation.md)

维护 Vue/Tauri 时先执行 `npm ci --prefix src/apps/desktop`，并准备 WebView2。旧版 Python 自检在 `src/legacy/` 下运行 `python -m ai_modeler --selftest`。

## 仓库导航

| 路径 | 内容 |
| --- | --- |
| [`src/apps/native-desktop/`](src/apps/native-desktop/) | 默认 MyGo 桌面、引擎客户端和原生 OpenGL 视口 |
| [`src/apps/engine-host/`](src/apps/engine-host/) | 常驻 Rust JSON Lines 进程入口 |
| [`src/core/cad-engine/`](src/core/cad-engine/) | 两种桌面共同使用的 Rust 建模、状态、Agent 与运行时核心 |
| [`src/apps/desktop/`](src/apps/desktop/) | Vue 界面与复用核心的 Tauri 兼容适配层 |
| [`src/native/`](src/native/) | 原生几何桥接与运行时构建 |
| [`src/legacy/`](src/legacy/) | Python 旧版应用、CATIA 适配与测试 |
| [`docs/`](docs/README.md) | 设计、研究、复现方法与迁移路线 |
| [`artifacts/`](artifacts/) | 已归档的几何实测与对照产物 |

本地临时模型、日志与历史资料位于 `temp/`。使用历史结果时，请核对对应的版本、输入与测试环境。

## 许可证

见[许可证全文](LICENSE)。
