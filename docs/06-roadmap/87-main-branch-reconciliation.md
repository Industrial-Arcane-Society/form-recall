# Main 分支合并核对（2026-10-04）

## 核对范围

从集成工作树 `f7-main-merge` 对照当时的 `origin/main`（`d11ad84af28f41180186657af87f4ba5f4007fdb`）与本地分支 tip。此操作保留主工作区 `feat/f6-clean-checkout-package-inputs` 的未跟踪文件和现有改动，没有切换、清理或合并到该工作区。

核对时有 47 个本地分支 tip 不可从 `origin/main` 到达。逐类检查后，未发现需要整支合并的缺失代码：F4/F5/F6 的构建器、Quick 解析、原生联网查询和 CATIA adapter 已在 main 对应模块、测试及 Todo 记录中；多数 F7/F0 分支为 patch-equivalent，或其较新的实现与诊断已在 main。旧 `feat/agentloop-refactor-v2` 修改的是 Python AgentLoop/prompt，与当前 Rust DSH ACP 桌面链路不同，不应作为当前主线代码合并。

## 本次补入的独有内容

- F0 CLI 启动复核记录由 `8fd17f1` 合入为 `7f262d4`：Vite、配置和构建 CLI 检查通过；可见 Tauri/WebView2 窗口仍未验证。
- MSI/NSIS 抽取后 smoke 记录由 `dbfe5fd` 合入为 `3436287`：包内容抽取和 System32-only 包内 smoke 通过；没有安装 MSI/NSIS，也没有验证安装后的 DSH/CATIA。
- F6 汇总 Todo 随后更新为 351/351 Rust 测试与当前打包证据，提交 `1d3e7b8`。

上述是按内容审查后挑选的记录，不是把本地所有分支 tip 强行合并。分支仍显示为未合入不等于它们的有效实现不在 main；重复、过时或无法推进验收的分支保持原样。

## 仍未完成的验收

- F0：用户报告的空白桌面窗口仍需可见 Tauri/WebView2 级验收；Vite HTTP 200 不能替代此项。
- F6.7：MSI/NSIS 尚未安装验证；安装后无全局 Python/Node 的 DSH 首次启动、干净账户和真实 CATIA COM 导出/推送未完成。
- F7：真实多模态盲测尚未通过 strict gate；CLI 多模态传输及几何 mock 测试不能替代真实图像理解和复核验收。

这些验收项继续保留在 [迁移 Todo](./01-migration-todo.md)，本次只完成已具备证据的文档和分支核对。
