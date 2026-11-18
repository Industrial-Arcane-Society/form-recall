# 贡献与验证

本仓库正分阶段迁移。当前桌面入口在 `src/apps/desktop/`，过渡期 Python 入口在 `src/legacy/`。每个独立 feature 应有独立的变更说明、针对性验收和单独提交；不要把多个未验收 feature 混进一个提交。

## 本地命令

```powershell
# 现有 Python 后端
Set-Location src/legacy
python -m ai_modeler --selftest
python -m pytest tests

# Vue + Tauri 桌面端
Set-Location ../apps/desktop
npm ci
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
```

构建 Windows 安装包会准备嵌入式 Python 与依赖资源，可能联网下载资源。开发 UI 与离线前端类型检查使用 `npm run build`；安装包流程见 [`docs/05-operations/01-build-and-run.md`](docs/05-operations/01-build-and-run.md)。

## 研究与变更证据

- 代码事实、模型能力、几何结果和真实 CATIA 结果分别记录，不用静态检查代替运行验证。
- 图像估值要记录来源、像素测量和比例假设；不将估值写成用户标注尺寸。
- 几何有效性和参考图相似度分别验收；未测量的项目标为“待验证”。
- 新实验记录说明输入、环境、步骤、结果文件、失败情况与局限。Agent 日志写到 `temp/agent-logs/`，不要散落到源码包中。
- 历史文档和模型留在 `temp/`；只有复核过的现行结论进入 `docs/`。

当前迁移顺序与验收记录见 [`docs/06-roadmap/01-migration-todo.md`](docs/06-roadmap/01-migration-todo.md)。

## 旧 Python 实现的四关回归

改 `src/legacy/` 之前先跑一次自检，改完必须过这四关（**绿变红即回滚**）。命令都在 `src/legacy` 下执行：

| 关卡 | 命令 | 底线 |
|---|---|---|
| 自检 | `python -m ai_modeler --selftest` | 50/50 |
| 几何相似度 | `python tools\bench_detail_fidelity.py` | 不低于改动前（现 99.8%） |
| 真实几何 | `python tools\bench_real_geometry.py` | 不低于改动前（现 591/593） |
| CATIA 通道 | `python tools\check_catia_export_real.py` | 必须 523/523 |

单元测试：`python -m pytest tests -q`。当前有 4 项既有失败（`tests/test_agentloop.py` 两项、`tests/test_cadplan_sdk.py:111` 的 `KeyError: 'socket_head_bolt'` 两项），与建模主链路无关。
