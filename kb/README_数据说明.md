# kb 数据说明（为什么有的进了包、有的没进）

协作包对 `kb/` 采取**"运行时依赖必带、爬取原件不带"**的口径。
判据不是体积，而是"队友克隆下来能不能直接跑起来 + 能不能复现我们的口径"。

## 1. 随包提供（运行时依赖，缺了程序会报错）

| 文件 | 谁在读 | 作用 |
|---|---|---|
| `auto_parts.json` | `ai_modeler/partlib.py` | 零件族定义与参数范围 |
| `bolt_std.json` | `ai_modeler/std_bolt.py` | 螺栓标准表（M 系列螺距/头径） |
| `bearing_std.json` | `ai_modeler/std_bearing.py` | 轴承标准表（内径/外径/宽度） |
| `pulley_std.json` | `ai_modeler/std_pulley.py` | 同步带轮齿形与标准轮 |
| `sprocket_std.json` | `ai_modeler/std_sprocket.py` | 链轮标准表 |
| `pin_real_std.json` | `ai_modeler/std_pin.py` | 实测销轴标准表 |
| `intent_corrections.json` | `ai_modeler/corrections.py` | 提示词纠错词表（自学习结果） |
| `real_features.json`(12.3MB) | `realholes.py` / `realsolid.py` | 实测特征库（孔位/型腔），A′ 阶段核心 |
| `real_brep_index.json` | `realsolid.py` | 实测 B-Rep 索引（文件本身不进包，见第 3 节） |
| `catia_catalogs.json`(1.3MB) | `catialib.py` | CATIA 目录号对照 |

## 2. 随包提供（口径与标定数据，体积都很小）

`real_dims.json`、`real_parts_scale.json`、`real_geometry_trust.json`、
`pin_std.json`、`basic_parts_cases.json`、`parts_param_catalog.json`、
`spring_series.json`、`prompt_cases_pulley.json`、
`bolt_std.md` / `bearing_std.md` / `pulley_std.md` / `sprocket_std.md`、
`papers_cn(.md)` / `papers_intl(.md)`、`bearing_pages.json` /
`pulley_pages.json` / `sprocket_pages.json`、`test_prompts*.txt`。

其中 `*_std.md` 是标准表的**来源留痕**（数值出自哪本手册哪一页），
审软著/写论文时需要，别删。

## 3. 明确不进包（爬取原件 / 缓存 / 大数据，可重建）

| 文件 | 体积 | 为什么不带 | 怎么重建 |
|---|---|---|---|
| `handbook_grid.jsonl` | 41.8 MB | 机械设计手册爬取原件，仓库带不动 | 需要原站访问权限，派生小表已带 |
| `handbook_target.jsonl` | 10.5 MB | 同上 | 同上 |
| `papers_cn_cache.json` | <0.1 MB | 爬取缓存 | 重跑爬取脚本 |
| `ai_render_index.json` | 0.2 MB | 渲染产物索引 | `python tools\render_ai_masks.py --all` |
| `kb/real_brep/*.npz` | ~120 MB | 实测 B-Rep 网格 | `python tools\build_real_brep.py --src <STEP目录> --tol 0.3` |
| `kb/real_brep_coarse/*.npz` | ~60 MB | 粗网格（快速渲染用） | 同上加 `--out kb\real_brep_coarse` |
| `零件真实图片*/`、`零件AI建模图片/`、`零件比对图/` | 数 GB | 现场照片与渲染图 | 由 `tools\reshoot_real_parts.py` 重新采集 |

**注意**：第 3 节里的 `real_brep*` 没有进包，所以**只克隆仓库跑不出实测 B-Rep 路径**，
这时程序会自动退回参数化建模（`ai_modeler/realsolid.py` 里 `available()` 返回 False）。
要跑满精度，需要按第 3 节的办法重建这两份网格。
