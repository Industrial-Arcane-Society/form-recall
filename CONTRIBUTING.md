# 贡献指南

**所有变更通过 Pull Request 合并到 `main`。禁止直接向 `main` 推送或强制推送。** 这项约定适用于所有贡献者、维护者和自动化，涵盖代码、文档、配置和紧急修复。

## 提交 Issue

先在 [Issues](https://github.com/Industrial-Arcane-Society/form-recall/issues) 查找是否已有同一问题。有记录时在原 Issue 补充版本、复现条件或验证结果；新事项从 [Issue 模板](https://github.com/Industrial-Arcane-Society/form-recall/issues/new/choose) 选择缺陷报告、功能提案或团队任务。

- 缺陷报告写明版本、环境、复现步骤及实际与预期结果。
- 功能提案写明使用场景、交付范围和可验证的验收条件。
- 团队任务写明交付物、验收方法和依赖。涉及多人或跨模块时，在 Issue 中同步负责人、依赖和进度。
- 日志、截图和模型仅附上复现所需的脱敏内容。凭据及受限资料通过团队约定的私下渠道处理。

## 分支命名

使用 `类型/简短描述`，描述只用小写英文字母、数字和单连字符。分支前缀按改动用途选择，与使用的开发工具无关。

| 用途 | 前缀 |
| --- | --- |
| 功能与修复 | `feat/`、`fix/`、`hotfix/` |
| 文档、重构与测试 | `docs/`、`refactor/`、`test/` |
| 构建、CI、维护与发布 | `build/`、`ci/`、`chore/`、`release/` |

例如 `feat/plate-layout`、`fix/occt-preview`、`docs/update-build-guide`。工作分支须匹配以下表达式。

```regex
^(feat|fix|docs|refactor|test|build|ci|chore|release|hotfix)/[a-z0-9]+(-[a-z0-9]+)*$
```

## 从工作分支发起 PR

在仓库根目录从远端最新的 `main` 创建工作分支。以下示例假设 `origin` 指向 FormRecall 官方仓库，你拥有工作分支的推送权限。

```powershell
git fetch origin
git switch -c docs/update-build-guide origin/main
# 完成修改后，暂存本次涉及的文件
git add docs/05-operations/01-build-and-run.md
git diff --cached --check
git commit -m "docs: update the build guide"
git push -u origin docs/update-build-guide
```

在 GitHub 创建 PR，选择 `main` 为目标分支、工作分支为来源。只有 fork 权限时，从 fork 的工作分支发起 PR，目标仍选择官方仓库的 `main`。每个 PR 聚焦一项独立变更，标题用一句中文说明结果，例如「修复便携版启动后模型视口空白」。还在开发或缺少关键验证时，可以先开 Draft PR。

## 审查和合并

- 按 [PR 模板](.github/pull_request_template.md) 填写背景、影响、验证结果和回退方法。完成同仓库 Issue 时用 `Closes #编号`，仅关联时用 `Refs #编号`。注明未运行的检查及原因，并在适用时附上截图、模型或复现步骤。
- 请相关模块维护者审查。审查人重点核对改动边界、兼容性、验收证据与回退方法。作者处理审查意见和冲突，确认适用的检查通过后，由维护者在 GitHub 的 PR 页面合并。
- `release/` 分支用于发布流程。发布 PR 合并会触发发布，由维护者按发版安排单独操作。
- 即使 GitHub 设置允许直接推送，也必须遵守 PR 流程。分支保护的目标是要求通过 PR、禁止强制推送与删除 `main`，并对维护者同样适用。

## 按改动范围验证

以下命令从仓库根目录执行。默认桌面使用 MyGo，构建与运行参数见原生桌面指南。Vue/Tauri 保留独立兼容入口。[原生桌面构建](docs/05-operations/03-native-desktop-build.md)

| 改动范围 | 验证 |
| --- | --- |
| 文档 | 检查相对链接、命令、路径和对当前版本能力的描述 |
| MyGo 工作台、进程客户端或原生视口 | `npm run test:native -- -SkipRust`，然后 `npm run build:native`。需要实际窗口时使用 `npm run dev` |
| MyGo 与 Rust 跨进程接口 | 运行 `TestHostWorkbenchIntegration`，按验证记录显式设置真实引擎、资源和 OCCT 路径 |
| 原生构建、运行与打包脚本 | `npm run build:native`，使用独立数据目录启动，并执行 `npm run package:native` 核验目录、ZIP 和 SHA-256 |
| Tauri 开发启动脚本 | `npm run test:dev-cli` |
| Vue/Tauri 界面 | `npm ci --prefix src/apps/desktop`，然后 `npm --prefix src/apps/desktop run build`。涉及桌面交互时通过 `npm run dev:tauri` 验证 |
| Rust、CADPlan 或 OCCT | `npm run test:native -- -SkipGo`，并记录完整结果。改动几何时，使用已校验 OCCT 复现并核对尺寸、孔位或拓扑 |
| Python 旧版 | 按下文执行旧版回归 |

`test-native.ps1` 遇到失败会停止后续步骤，核心库失败时应独立运行 host 测试并分别记录结果。`-RustFilter` 只筛选核心库测试。2026-11-20 的迁移验证中，核心库保留了基线 `cf69659` 的同名 23 项失败，63 项忽略测试未在该全量运行中执行。定向通过需要同时报告筛选范围和全量基线。[原生桌面验证记录](docs/05-operations/04-native-desktop-validation.md)

### Python 旧版回归

修改 `src/legacy/` 时，改动前后在该目录运行适用的同一组检查，并记录基线和结果。涉及 CATIA 通道时，需要可用的真实 CATIA 环境。

| 检查 | 命令 |
| --- | --- |
| 自检 | `python -m ai_modeler --selftest` |
| 单元测试 | `python -m pytest tests -q` |
| 几何相似度 | `python tools/bench_detail_fidelity.py` |
| 真实几何 | `python tools/bench_real_geometry.py` |
| CATIA 导出 | `python tools/check_catia_export_real.py` |

自检应全部通过，几何相似度与真实几何结果应至少维持改动前基线，CATIA 通道验收目标为 523/523。对已有失败给出基线与本次复测证据，并在 PR 中明确说明新增失败。

## 模块修改边界

`src/apps/native-desktop` 管理窗口、工作台、视口与引擎客户端。`src/apps/engine-host` 管理 JSON Lines 协议和进程生命周期。CADPlan、工程持久化、几何构建、Agent、配置和运行时验证由 `src/core/cad-engine` 统一实现。`src/apps/desktop/src-tauri` 的兼容命令调用同一核心。

新增核心行为时，同时检查原生 RPC 与适用的 Tauri 命令入口。流式事件和取消请求应可并发处理，模型写入继续遵循 generation 与几何指纹约束。跨进程测试使用独立临时数据目录，模型验收状态以核心生成的当前版本投影为准。[架构与模块边界](docs/01-architecture/01-target-architecture.md)

## 研究与验收记录

- 记录可复现的输入、环境、步骤、输出路径、失败情况和适用范围。
- 分别报告几何有效性、参考图相似度和实机 CATIA 结果。估算尺寸应注明来源、测量基准和比例假设。
- 临时输出和运行日志放在 `temp/`，按需归档可复现证据。提交前检查日志、模型和截图是否含凭据或未经授权的企业资料。

项目计划和验收记录可从[文档索引](docs/README.md)查阅。
