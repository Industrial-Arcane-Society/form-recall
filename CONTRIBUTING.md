# 协作约定（CONTRIBUTING）

## 一、跑起来

```powershell
python -m ai_modeler --selftest            # 自检（必须 35/35）
python -m ai_modeler                       # 打开界面
```

核心**不依赖任何第三方库**；可选装 OpenCASCADE：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\01_安装依赖.ps1
```

## 二、改之前先跑一遍判据（记下基线）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\04_验收基准.ps1
```

它会依次跑：几何相似度 → 真实几何 → CATIA 通道 → CATIA 孔位 → 自检，
把结果写进 `reports\` 与 `kb\*.json`。

## 三、改完必须过的四关（**绿变红即回滚**）

| 关卡 | 命令 | 底线 |
|---|---|---|
| 自检 | `python -m ai_modeler --selftest` | 35/35 |
| 几何相似度 | `python tools\bench_detail_fidelity.py` | 不低于改动前（现 99.8%） |
| 真实几何 | `python tools\bench_real_geometry.py` | 不低于改动前（现 591/593） |
| CATIA 通道 | `python tools\check_catia_export_real.py` | 必须 523/523 |

涉及图像口径的改动，再加跑：

```powershell
python tools\render_ai_masks.py --all
python tools\compare_ai_vs_photos.py --all --montage 20
```

## 四、代码约定

* 注释写**为什么**，尤其是"踩过的坑"（本项目大量注释都是实测教训，请保留风格）；
* 新增零件族：在 `ai_modeler/parts/realparts.py` 的 `families` 定义里加一条，并在 `BUILDERS` 登记；
* 新增判据/检查：放 `tools\`，并在 `README.md` 的判据表里登记；
* 不要把大数据提交进仓库（见 `README.md` 第三节）。

## 五、提交与分支

```
main           保持可发布
feat/xxx       新功能
fix/xxx        修复
```

提交信息格式：

```
feat(族名): 一句话说清改了什么
  判据：几何 99.8→99.9，图像 84.5→86.1，自检 35/35
```

## 六、提 PR 时请贴

* 判据前后对比（上面四关的数字）；
* 如果是**退化**：说明为什么必须这样改、影响多少件；
* 涉及几何的改动，贴 1~2 张 `reports\跑图对比\` 里的并排图。
