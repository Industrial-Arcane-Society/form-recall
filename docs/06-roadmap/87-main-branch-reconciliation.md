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

- F0：隐藏 WebView2 smoke 的 production Vue mount 与 Channel 四场景已通过；用户报告的空白窗口仍需普通 Tauri dev 可见窗口验收，隐藏 fixture smoke 和 Vite HTTP 200 不能替代此项。
- F6.7：MSI/NSIS 尚未安装验证；安装后无全局 Python/Node 的 DSH 首次启动、干净账户和真实 CATIA COM 导出/推送未完成。
- F7：真实多模态盲测尚未通过 strict gate；CLI 多模态传输及几何 mock 测试不能替代真实图像理解和复核验收。

这些验收项继续保留在 [迁移 Todo](./01-migration-todo.md)，本次只完成已具备证据的文档和分支核对。

## 2026-10-06 本地分支复核

- 核对基线：`main` / `origin/main` `a099b4ca3b0f1f372c9e9dbf3235a56ecf6a9235`，主工作区干净。
- `git branch --no-merged main` 列出 61 个本地分支 ref；`git cherry` 报告 21 行非等价 patch、20 个唯一提交 SHA。逐组核对 F4/F5/F6/F7 相关代码路径、测试、验收记录与 Todo 后，未发现需要补摘的有效 feature：对应构建器、Quick 解析、CATIA adapter、native tool bridge、coldstart gate/publication 与启动配置均已在 main 有当前实现或明确的验收限制。
- 不将 branch ref 未并入视为功能缺失。未合并 ref 中 `agentloop-refactor-v2` 的两项唯一改动仍属于旧 Python AgentLoop/prompt，且分支远落后于 main；与当前 Rust DSH ACP 主链冲突，不移植。
- worktree 清理按保守规则执行：F7.G9 已合入并推送后，其 clean worktree 仅剩可再生成的 ignored Cargo/Tauri schema 缓存，已用 Codex recoverable archive 归档；其他 worktree 当时仍有未提交改动、ignored 数据、独有提交或活跃验收，未归档。每周清理 heartbeat 保持启用，已删除重复的同类 heartbeat；分支 ref 不删除。

本次为只读源码/文档与 Git 元数据复核；没有运行真实 provider、读取历史 raw run、安装包或 ignored 数据。下一次清理必须按当时的活跃任务、提交可达性、tracked/untracked/ignored 状态重新核验，不能照本报告批量删 worktree。
