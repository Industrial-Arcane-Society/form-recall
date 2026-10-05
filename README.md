# AI Modeler

面向 CATIA 的自然语言与参考图建模工具。当前仓库正在把桌面端和 Agent 迁到 Rust、Vue 与 DSH 原生插件；旧 Python 实现保留在 `src/legacy/`，用于迁移期间维持现有建模和 CATIA 能力。

## 开发入口

- 桌面应用：先在仓库根目录运行 `npm ci --prefix src/apps/desktop`，之后运行 `npm run dev` 启动 Tauri 桌面开发版。根脚本会检查本地 Tauri CLI 和平台原生绑定；缺失或安装不完整时会打印修复命令。可用 `npm run dev -- --help` 向 Tauri CLI 传递参数。也可进入 `src/apps/desktop/` 运行 `npm run tauri:dev`。
- 仅启动无桌面窗口的 Vite 前端服务：从仓库根目录运行 `npm run dev:web`，再访问终端显示的本地地址。
- 现有 Python CLI：进入 `src/legacy/`，运行 `python -m ai_modeler --selftest`；输入 `python -m ai_modeler --help` 查看选项。
- 当前迁移状态、研究记录、设计规范和复现方法：从 [`docs/README.md`](docs/README.md) 进入。
- 根目录开发启动检查的 Node 回归：`npm run test:dev-cli`（不启动桌面窗口）。

`temp/` 保存迁移前的文档和实验产物归档；新的 Agent 运行日志与本地生成记录也写入这里。该目录中的历史结果不是当前版本的自动验收结论。

根目录保留 `.github/` 供 GitHub Actions 发现工作流，保留 `.gitignore`、`.gitattributes`、许可证和仓库入口文件。活动源码、桌面应用与过渡期 Python 代码均归入 `src/`。

## 目录

```text
docs/                 当前研究文档索引与规范
src/apps/desktop/     Vue + Tauri 桌面应用
src/legacy/           迁移中的旧 Python 应用、资源、脚本和测试
temp/                 旧文档/实验归档、Agent 日志与临时生成物
.github/              GitHub Actions（需要位于仓库根目录）
```

## 提交约定

每个独立 feature 完成并通过对应验收后单独提交。目录重构作为首个完整迁移 feature 提交到 `main`；后续功能保持各自清晰的提交边界。
