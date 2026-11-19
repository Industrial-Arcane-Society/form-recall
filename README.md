# FormRecall

FormRecall 是面向机械零件和工装夹具的 Windows 参数化 CAD 工作台。它把自然语言、参考图和尺寸输入转成可检查的 CADPlan，并用本地几何内核构建和预览零件。应用窗口与部分命令仍使用 AI Modeler 名称。

> **所有进入 `main` 的变更都必须通过 Pull Request。** 请从 `main` 新建工作分支，把提交推送到该分支，再向 `main` 发起 PR。代码、文档和小修复都遵循同一流程。详见[贡献指南](CONTRIBUTING.md)。

## 当前能力

| 入口 | 用途 |
| --- | --- |
| Quick 建模 | 根据零件参数在本地生成模型，查看特征、几何检查和三维预览 |
| Agent 建模 | 通过自然语言和参考图生成或修改 CADPlan，由 DSH/Cordis 调用建模工具 |
| 旧版 Python | 保留夹具设计向导、历史研究实现和 CATIA 过渡能力 |

Rust/Tauri 负责原生桌面命令，Vue 提供界面，Open CASCADE（OCCT）负责本地几何构建。CATIA 导出和复核需要实际的 Windows CATIA 环境。导出文件、几何检查和参考图相似度各有独立的验收依据。

## 开发启动

在 Windows 上准备 Git、Node.js/npm、Rust MSVC 工具链、Visual Studio C++ 构建工具和 WebView2。桌面运行时和打包要求见[构建与运行](docs/05-operations/01-build-and-run.md)。

```powershell
git clone https://github.com/Industrial-Arcane-Society/form-recall.git
Set-Location form-recall
npm ci --prefix src/apps/desktop
npm run dev
```

只查看前端时，从仓库根目录运行 `npm run dev:web`。浏览器预览只提供前端，验证 Tauri 命令和本地建模请使用桌面版。旧版自检在 `src/legacy/` 下运行 `python -m ai_modeler --selftest`。

## 仓库导航

| 路径 | 内容 |
| --- | --- |
| [`src/apps/desktop/`](src/apps/desktop/) | Vue/Tauri 应用与 Rust 建模核心 |
| [`src/native/`](src/native/) | 原生几何桥接与运行时构建 |
| [`src/legacy/`](src/legacy/) | Python 旧版应用、CATIA 适配与测试 |
| [`docs/`](docs/README.md) | 设计、研究、复现方法与迁移路线 |
| [`artifacts/`](artifacts/) | 已归档的几何实测与对照产物 |

本地临时模型、日志与历史资料位于 `temp/`。使用历史结果时，请核对对应的版本、输入与测试环境。

## 许可证

见[许可证全文](LICENSE)。