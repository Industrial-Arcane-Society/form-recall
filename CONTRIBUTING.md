# 贡献指南

**所有变更通过 Pull Request 合并到 `main`。禁止直接向 `main` 推送或强制推送。** 这项约定适用于所有贡献者、维护者和自动化，涵盖代码、文档、配置和紧急修复。

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

在 GitHub 创建 PR，选择 `main` 为目标分支、工作分支为来源。只有 fork 权限时，从 fork 的工作分支发起 PR，目标仍选择官方仓库的 `main`。每个 PR 聚焦一项独立变更。

## 审查和合并

- 在 PR 说明中列出改动目的、影响范围、验证命令和结果。注明未运行的检查及原因，并在适用时附上截图、模型或复现步骤。
- 请维护者审查。处理审查意见和冲突，确认适用的检查通过后，由维护者在 GitHub 的 PR 页面合并。
- 即使 GitHub 设置允许直接推送，也必须遵守 PR 流程。分支保护的目标是要求通过 PR、禁止强制推送与删除 `main`，并对维护者同样适用。

## 按改动范围验证

以下命令从仓库根目录执行。Windows 桌面端依赖、Rust 测试宿主、原生运行时和打包细节见[构建与运行](docs/05-operations/01-build-and-run.md)。

| 改动范围 | 验证 |
| --- | --- |
| 文档 | 检查相对链接、命令、路径和对当前版本能力的描述 |
| 根目录启动脚本 | `npm run test:dev-cli` |
| Vue/Tauri 界面 | `npm ci --prefix src/apps/desktop`，然后 `npm --prefix src/apps/desktop run build`。涉及桌面交互时再通过 `npm run dev` 验证 |
| Rust、CADPlan 或 OCCT | 使用构建指南中的 Windows Rust 测试命令。改动几何时，使用相应运行时复现并核对尺寸、孔位或拓扑 |
| Python 旧版 | 按下文执行旧版回归 |

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

## 研究与验收记录

- 记录可复现的输入、环境、步骤、输出路径、失败情况和适用范围。
- 分别报告几何有效性、参考图相似度和实机 CATIA 结果。估算尺寸应注明来源、测量基准和比例假设。
- 临时输出和运行日志放在 `temp/`，按需归档可复现证据。提交前检查日志、模型和截图是否含凭据或未经授权的企业资料。

项目计划和验收记录可从[文档索引](docs/README.md)查阅。