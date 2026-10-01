# Rust + DSH 重构总 Todo

更新时间：2026-10-02。每一项 feature 按边界实现、验收并单独提交。首个“目录重构”feature 完成后直接提交到 `main`；后续 feature 使用独立提交，不把未验收工作合并。

**状态快照：**本记录核对 `main@0f182d3` 与 `feat/rust-dsh-acp-host@48446bb`。两者不能混作同一实现状态；以下每个功能均注明实际所在分支。

## 总目标

桌面程序启动整套建模流程：Rust/Tauri 管理应用、runtime 与 CAD 状态；DSH Agent 自主决定是否以及如何调用 DSH 原生 Cordis 工具；Rust 负责 CADPlan 与几何核心；Python 最终只保留 CATIA COM。保留快速模式，并允许快速模式与 Agent 创造模式切换。

## 当前进度

| ID | Feature | 状态 | 完成条件 / 提交边界 |
|---|---|---|---|
| F0 | 目录重构、文档重建和路径恢复 | **已完成** | 根目录结构、旧资料归档、文档索引、日志路径、开发启动、测试、Tauri build hook 和 GitHub Actions 已核验；本提交整体落到 `main` |
| F1 | Rust DSH ACP Host + 固定 Runtime Manifest | **部分完成：F1.1–F1.3 仅在 feature 分支；F1.4 未开始，未合入 main** | 已有 ACP 客户端、私有固定 runtime、Tauri/ACP 会话和 UI 事件接线；真实 provider 请求、原图上行及端到端桌面应用验收仍未完成。详见下方分支快照 |
| F2 | DSH 原生 Cordis CAD 插件去 Python worker | 未开始 | 插件注册工具由 DSH 原生加载；CAD 工具命令直接进入 Rust；代码和实际运行均不经过 MCP 或 Python SDK 转译 |
| F3 | Rust CADPlan 与增量模型事务 | 未开始 | 计划版本、schema、校验、preview/build dispatch、回滚和来源信息由 Rust 拥有；Python fixtures 有 golden 对拍 |
| F4 | Rust/C++ thin OCCT FFI | 未开始 | Rust 几何请求建立有效 B-Rep、布尔、网格与拓扑报告；fixture 对拍有尺寸容差和失败样本 |
| F5 | Quick 模式与本地导出迁移 | 未开始 | Quick 与 Agent 共用稳定 CAD domain；AIMODEL/STL/视图导出有兼容性验收 |
| F6 | Python 缩减到 CATIA COM | 未开始 | Python 包只剩 CATIA Windows/COM adapter 和必要协议代码；其余业务逻辑和 Agent 代码不再由 Python 执行 |
| F7 | 多模态盲测与科学验收基线 | 未开始 | 图像/文字盲测、工具轨迹、几何量测、视觉差异关闭、真实 CATIA 证据分别归档并可复跑 |

### 分支进展与验收边界

- `main@0f182d3` 包含 F0 目录重构提交 `3abd816` 和文档审计提交 `0f182d3`；文档审计没有改变产品代码，因此代码审计固定在 `main@3abd816`。F1 ACP Host/runtime 代码尚未进入 main。main 的产品链路仍是 Rust/Tauri → Python UI bridge/Agent 接入，见[当前系统审计](../00-research/02-current-system-audit.md)。
- F1 代码快照为 `56f3dc0`；当前 feature 分支 HEAD `48446bb` 还包含后续阶段记录和文档审计提交。三个独立功能提交是：`48bea66`（F1.1 ACP stdio client）、`46d737a`（F1.2 私有固定 DSH runtime）、`56f3dc0`（F1.3 Rust ACP Host 和 Tauri Agent 会话路由）；`f6212cc` 记录阶段验收结果。上述提交及 feature-only 阶段卡 `docs/06-roadmap/02-f1-acp-host.md` 均不属于 `main@0f182d3`。
- feature 阶段证据覆盖 ACP 控制面、session 新建/恢复/关闭、私有 Node/DSH 运行、Cordis 工具注册、Tauri 事件接线及构建/单测；记录明确写明未发送 `session/prompt`，未使用 API key 发起 provider 请求。因此不能据此宣称真实 LLM 对话、图片已经上行到模型、建模工具已端到端执行，或安装包界面验收已通过。
- 后续 F1 工作须补齐固定 runtime 下的受控真实 route/prompt 请求，检查图片传递和 thought/tool 事件，并对打包桌面端进行实际安装与会话验收；结果完成并单独提交前，F1 继续标为“部分完成”。

## F0 验收记录

- [x] 迁移前工作树干净；记录 tracked 1,654 文件基线。
- [x] 把桌面端归入 `src/apps/desktop/`，Python 过渡工程归入 `src/legacy/`。
- [x] 把旧 `docs/`、`reports/`、`artifacts/` 保真归档到 `temp/`，没有删除历史文件。
- [x] 建立 `docs/README.md` 唯一根文档索引、编号目录、研究章程、架构基线、验证协议和前端设计规范。
- [x] 研究文件定位明确：章程预先定义研究假设、可观察量和反驳条件；验证协议规范数据、指标、对照及失败记录；实验模板用于逐次运行留档。代码事实、运行证据、几何/视觉与 CATIA 证据分层，避免把构建或单测冒充建模质量结果。
- [x] 前端设计规范建立语义色彩/间距 token、共享组件、状态/交互规则以及视觉、键盘和无障碍验收矩阵；将已观察代码事实与规范目标分开，并明确记录硬编码色值、DPI、焦点管理等仍待统一/验收项。
- [x] 对照 [BiliSum 当前仓库结构](https://github.com/lycohana/BiliSum)：其根目录按职责设有 `apps/`、`assets/`、`docs/`、`packages/`、`packaging/`、`scripts/`、`tests/`，并保留 `.github/`。本项目采纳按职责分层的做法；结合本项目边界将桌面应用归入 `src/apps/desktop/`、过渡代码归入 `src/legacy/`，历史资料放 `temp/`，并保留 GitHub 工作流所需 `.github/`。
- [x] 新增 `temp_path()`，Agent、selftest、CADPlan 和截图输出改到 `temp/`。
- [x] 改好遗留启动脚本、Tauri 资源 staging、开发根路径和 UI 手册路径。
- [x] 更新 Actions、贡献文档和内部路径引用，并在 CI 加入目录/运行路径回归测试。
- [x] Python selftest 36/36；Python pytest 277 passed、92 subtests passed；Vue build 通过；Rust tests 4 passed；资源 staging 通过；`git diff --check` 无错误。
- [x] 对比移动前后历史文件数量和 Git blob：迁移阶段索引保留 1,654 个原有 blob，内容多重集与迁移前 HEAD 一致；后续仅对列明的路径/测试适配文件作了修改；`.github/` 根工作流保留，本地缓存忽略规则正常。
- [x] 检查 docs 只有一个根级 Markdown；仓库根目录只留 `docs/`、`src/`、`temp/`、必要的 `.github/` 与仓库元数据/说明文件。
- [x] 在 `main@0f182d3` 复核目录：功能目录只有 `docs/`、`src/`、`temp/`；另有 GitHub 自动化目录 `.github/` 和仓库元数据/入口说明文件。`docs/` 根仅有 `README.md`；其 7 个子目录均为 `NN-kebab-case`；活动子文档采用 `NN-kebab-case.md`。旧文档库归档在 `temp/legacy-documents/`，历史报告/产物在 `temp/evidence-archive/` 等目录，Agent 运行日志在 `temp/agent-logs/`。`src/` 中仍保留运行时实际消费的知识库、提示词、数据和随源码说明，不把它们误称为已归档旧文档。
- [x] 文档索引审计见 [`03-docs-index-audit.md`](03-docs-index-audit.md)：审计工作树共 12 个 Markdown 文件（含 feature-only F1 阶段卡），检查 26 个本地链接目标，缺失 0；排除该 F1 卡后，main 子目录活动文档索引覆盖 9/9。该审计仅证明文档结构/链接可达，不代表产品功能验收。
- [x] F0 整体验收完成，形成一个 `refactor: reorganize project into src docs and temp` 提交到 `main`。

旧协作包打包器仍含历史开发机绝对路径和旧包布局假设，不属于当前 Tauri 启动/打包链路；后续若继续使用该协作包工作流，需独立迁移并验收。

## 后续 feature 的最小验收卡

每项开工前写明输入、成功标准、失败样本和可复现命令；结束前记录代码变动、运行证据、风险和剩余项。Agent 相关 feature 必须留一次真实 DSH 请求的版本/会话/图像输入/工具事件记录；几何 feature 必须留可机器读取的 B-Rep 与尺寸报告；安装 feature 必须留无全局依赖的冷启动结果。

完成后由主 Agent 检查对照标准和未解决项，再以独立 commit 提交。静态测试通过不代表真实 DSH 多模态或 CATIA 验收通过。
