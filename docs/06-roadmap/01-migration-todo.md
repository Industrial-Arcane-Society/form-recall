# Rust + DSH 重构总 Todo

更新时间：2026-10-02。每一项 feature 按边界实现、验收并单独提交。首个“目录重构”feature 完成后直接提交到 `main`；后续 feature 使用独立提交，不把未验收工作合并。

## 总目标

桌面程序启动整套建模流程：Rust/Tauri 管理应用、runtime 与 CAD 状态；DSH Agent 自主决定是否以及如何调用 DSH 原生 Cordis 工具；Rust 负责 CADPlan 与几何核心；Python 最终只保留 CATIA COM。保留快速模式，并允许快速模式与 Agent 创造模式切换。

## 当前进度

| ID | Feature | 状态 | 完成条件 / 提交边界 |
|---|---|---|---|
| F0 | 目录重构、文档重建和路径恢复 | **已完成** | 根目录结构、旧资料归档、文档索引、日志路径、开发启动、测试、Tauri build hook 和 GitHub Actions 已核验；本提交整体落到 `main` |
| F1 | Rust DSH ACP Host + 固定 Runtime Manifest | 未开始 | 无全局 Node 的安装包启动；文字/图片会话、思考/工具事件、会话恢复和单会话取消可复现；校验下载/升级/回滚 |
| F2 | DSH 原生 Cordis CAD 插件去 Python worker | 未开始 | 插件注册工具由 DSH 原生加载；CAD 工具命令直接进入 Rust；代码和实际运行均不经过 MCP 或 Python SDK 转译 |
| F3 | Rust CADPlan 与增量模型事务 | 未开始 | 计划版本、schema、校验、preview/build dispatch、回滚和来源信息由 Rust 拥有；Python fixtures 有 golden 对拍 |
| F4 | Rust/C++ thin OCCT FFI | 未开始 | Rust 几何请求建立有效 B-Rep、布尔、网格与拓扑报告；fixture 对拍有尺寸容差和失败样本 |
| F5 | Quick 模式与本地导出迁移 | 未开始 | Quick 与 Agent 共用稳定 CAD domain；AIMODEL/STL/视图导出有兼容性验收 |
| F6 | Python 缩减到 CATIA COM | 未开始 | Python 包只剩 CATIA Windows/COM adapter 和必要协议代码；其余业务逻辑和 Agent 代码不再由 Python 执行 |
| F7 | 多模态盲测与科学验收基线 | 未开始 | 图像/文字盲测、工具轨迹、几何量测、视觉差异关闭、真实 CATIA 证据分别归档并可复跑 |

## F0 验收记录

- [x] 迁移前工作树干净；记录 tracked 1,654 文件基线。
- [x] 把桌面端归入 `src/apps/desktop/`，Python 过渡工程归入 `src/legacy/`。
- [x] 把旧 `docs/`、`reports/`、`artifacts/` 保真归档到 `temp/`，没有删除历史文件。
- [x] 建立 `docs/README.md` 唯一根文档索引、编号目录、研究章程、架构基线、验证协议和前端设计规范。
- [x] 新增 `temp_path()`，Agent、selftest、CADPlan 和截图输出改到 `temp/`。
- [x] 改好遗留启动脚本、Tauri 资源 staging、开发根路径和 UI 手册路径。
- [x] 更新 Actions、贡献文档和内部路径引用，并在 CI 加入目录/运行路径回归测试。
- [x] Python selftest 36/36；Python pytest 277 passed、92 subtests passed；Vue build 通过；Rust tests 4 passed；资源 staging 通过；`git diff --check` 无错误。
- [x] 对比移动前后历史文件数量和 Git blob：迁移阶段索引保留 1,654 个原有 blob，内容多重集与迁移前 HEAD 一致；后续仅对列明的路径/测试适配文件作了修改；`.github/` 根工作流保留，本地缓存忽略规则正常。
- [x] 检查 docs 只有一个根级 Markdown；仓库根目录只留 `docs/`、`src/`、`temp/`、必要的 `.github/` 与仓库元数据/说明文件。
- [x] F0 整体验收完成，形成一个 `refactor: reorganize project into src docs and temp` 提交到 `main`。

旧协作包打包器仍含历史开发机绝对路径和旧包布局假设，不属于当前 Tauri 启动/打包链路；后续若继续使用该协作包工作流，需独立迁移并验收。

## 后续 feature 的最小验收卡

每项开工前写明输入、成功标准、失败样本和可复现命令；结束前记录代码变动、运行证据、风险和剩余项。Agent 相关 feature 必须留一次真实 DSH 请求的版本/会话/图像输入/工具事件记录；几何 feature 必须留可机器读取的 B-Rep 与尺寸报告；安装 feature 必须留无全局依赖的冷启动结果。

完成后由主 Agent 检查对照标准和未解决项，再以独立 commit 提交。静态测试通过不代表真实 DSH 多模态或 CATIA 验收通过。
