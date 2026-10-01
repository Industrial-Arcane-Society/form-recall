# 开发工具索引

`tools/` 仍保留原有脚本路径，避免打断文档、启动脚本和团队命令。新工具优先使用带类别前缀的文件名。

| 前缀 / 名称 | 用途 | 示例 |
|---|---|---|
| `bench_*.py`、`evaluate.py` | 几何和图像基准评估 | `bench_detail_fidelity.py`, `bench_real_geometry.py` |
| `check_*.py`、`verify_*.py`、`audit_*.py` | 回归检查、静态审计和交付核验 | `check_catia_export_real.py`, `audit_catia_export.py` |
| `render_*.py`、`make_shots.py` | 生成模型预览和对比图 | `render_ai_parts.py`, `make_shots.py` |
| `build_*.py`、`extract_*.py`、`capture_*.py` | 从 CATIA、STEP 或文件构建数据 | `build_real_brep.py`, `capture_real_images.py` |
| `make_*.py`、`migrate_*.py`、`selflearn_*.py` | 生成样例、迁移数据和自学习 | `make_testlist.py`, `migrate_paramtable.py` |
| `live_*.py`、`connect_*.py`、`install_*.py` | 连接或检查本机 CATIA 与桌面环境 | `connect_catia.py`, `install_catia_plugin.py` |
| `check_share_pack.py`、`make_share_zip.py`、`push_to_github.py` | 协作包完整性和上传辅助 | 协作包打包与核验入口 |

常用一键流程在 [`scripts/`](../scripts/)；插件包的模块分层见 [`ai_modeler/README.md`](../ai_modeler/README.md)。
