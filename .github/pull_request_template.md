<!--
PR 模板：写法参照 lycohana/BiliSum（概要 / 原因 / 改动 / 验证 四段 + 勾选式验证），
但末尾加了本项目特有的两样东西：
  · **判据对比表**：任何改动都要给出改动前后的数字（本项目的判据是硬门禁）；
  · **回滚点**：一句话说清"变红了怎么退回去"。
-->

## 概要

<!-- 一句话说清这个 PR 做了什么、为什么值得合。 -->

## 原因

<!-- 为什么要改？贴证据：现场反馈、判据数字、日志片段。
     本项目大量改动都来自"实测踩坑"，把坑写出来，后来人不用再踩一遍。 -->

## 改动

<!-- 按模块列。涉及几何/判据的，指出改了哪个文件的哪一段逻辑。 -->

-
-

## 判据对比（**本项目的硬门禁，必须填**）

| 判据 | 改动前 | 改动后 | 变化 |
|---|---|---|---|
| 自检 `python -m ai_modeler --selftest` | 35/35 |  |  |
| 几何相似度 `python tools\bench_detail_fidelity.py` | 99.8% / 498-502 |  |  |
| 真实几何 `python tools\bench_real_geometry.py` | 591/593 |  |  |
| CATIA 通道 `python tools\check_catia_export_real.py` | 523/523 |  |  |
| CATIA 孔位 `python tools\check_catia_hole_positions.py --all` | 267/267 |  |  |
| 自研布尔 `python tools\check_bsp_local.py` | 10/10 |  |  |
| 图像相似度 `python tools\compare_ai_vs_photos.py --all` | 84.5% |  |  |

> **出现"绿变红"必须回滚或在本节说明为什么必须这样改、影响多少件。**

## 验证

- [ ] `python -m ai_modeler --selftest` —— 通过（35/35）
- [ ] `python tools\bench_detail_fidelity.py` —— 不低于改动前
- [ ] `python tools\bench_real_geometry.py` —— 不低于改动前
- [ ] `python tools\check_catia_export_real.py` —— 523/523
- [ ] 涉及几何的改动：附 1~2 张 `reports\跑图对比\` 里的并排图
- [ ] 涉及 CATIA 的改动：在真机推过一次（贴日志片段）

## 回滚点

<!-- 一句话：如果线上/主分支出问题，怎么退回去（例：把 docmodel.py 第 X 行换回 csg_subtract）。 -->

## 关联

<!-- 关联 issue / 讨论 / 现场截图 -->
