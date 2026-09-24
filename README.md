# AI 自动建模助手（协作包）

> 一句话：把**一句中文提示词**变成能在 CATIA 里用的真实零件模型 —— 数据全部来自 CATIA 实测 STEP，
> 内建**判据体系**（几何 + 图像）与**闭环回归**，任何改动都会被量到。

| | |
|---|---|
| 语言 / 依赖 | Python 3.11+（实测 3.14.3 可用）；核心**纯标准库**，可选 `cadquery-ocp`（OpenCASCADE） |
| 界面 | tkinter 桌面程序（`python -m ai_modeler`） |
| CATIA 通道 | V5 宏 + cscript/COM 推送；也可导出 STEP 让 CATIA 导入 |
| 当前判据 | **几何相似度 99.8%（≥95% 498/502）**；**图像相似度 84.5%**（AI 渲染图 vs 真实照片） |
| 自检 | `python -m ai_modeler --selftest` → 35/35 |

---

## 一、目录罗列

> 参照常见仓库的罗列方式，但多加一列**说明**，让人一眼知道每个目录是干什么的。

| 目录 / 文件 | 说明 |
|---|---|
| `ai_modeler/` | **插件源码**：自然语言解析、几何内核（BSP + 直写网格 + OCCT 后端）、零件库、CATIA 导出、界面、MCP 服务 |
| `tools/` | **工具与基准**：几何/图像判据、渲染、比对、STEP 特征提取、OpenCASCADE 建库、一键闭环脚本 |
| `catia_plugin/` | CATIA 侧宏（`AI自动建模助手.CATScript`）与安装脚本 |
| `datasets/训练提示词/` | 1828 条提示词（训练/测试集、负例、说明），可直接拿去做提示词↔模型训练 |
| `docs/` | 设计文档：升级方向、云端训练与工作流方案、全流程说明、CATIA 连接操作说明、**协作包更新教程** |
| `docs/历史报告/` | 原项目顶层散落的历史 Word/Markdown 报告（带轮复盘、阶段报告、升级对比、细节复现度报告等），按时间留痕 |
| `reports/` | 各类报告（Markdown + Word），含判据报告与故障复盘 |
| `reports/跑图对比/` | **最近几轮的跑图对比**：AI 建模图 vs 真实照片，并排拼图（121 张） |
| `examples/` | 样例数据：每族 1 组真实照片（含背景帧）+ 1 个 AI 渲染样本（含掩码） |
| `kb/` | 知识库：**运行时依赖的标准表/词表/实测特征**（螺栓、轴承、带轮、链轮、销、纠错词表、12 MB 实测特征库）。`kb/README_数据说明.md` 写清了每个文件谁在用、哪些不带、怎么重建 |
| `scripts/` | 一键脚本：装依赖 / 跑全流程 / 重建大数据 / 跑验收基准 / **打包协作包** / **初始化 Git 仓库** |
| `启动脚本/` | 双击即用的 Windows 脚本：启动程序、连接 CATIA、安装插件、装桌面快捷方式 |
| `.gitignore` / `.gitattributes` | 数据策略（哪些大文件不进库）与换行策略（`.ps1/.bat/.vbs` 固定 CRLF、源码固定 LF）。**改完这两个文件要跑 `tools\check_share_pack.py` 复核** |
| `README.md` | 本文件 |
| `CONTRIBUTING.md` | 协作约定：怎么改、怎么验、怎么提 |
| `LICENSE` | MIT |

---

## 二、快速上手（3 分钟）

```powershell
# 1) 只看插件（核心不需要任何第三方库）
python -m ai_modeler                 # 打开界面
python -m ai_modeler --selftest      # 自检（应 35/35）

# 2) 可选：装上 OpenCASCADE（多孔/复杂件更快更准）
powershell -ExecutionPolicy Bypass -File scripts\01_安装依赖.ps1

# 3) 跑一遍判据，看现在的水平
python tools\bench_detail_fidelity.py    # 几何相似度（对实测 STEP）
python tools\bench_real_geometry.py      # 真实几何命中
python tools\check_catia_export_real.py  # CATIA 通道
```

**试几条提示词**（都已在库、可复现）：

| 提示词 | 说明 |
|---|---|
| `内六角螺栓 M5x16` | 标准件（ISO 4762 参数化，含弹垫+平垫） |
| `EGT型材 EGT001 SW80x088` | TUNKERS 型材（实测 B-Rep 复刻，含内部型腔） |
| `支座 KMS-M05-010-100-D` | 厂内标准件（实测外轮廓 + 实测孔位） |
| `定位销 KMS-M01-P097-A20` | 台阶销（段长按实测归一化） |
| `法兰 外径 160 厚 20 内径 60 均布 6 个直径 12 的螺栓孔 分度圆 120` | 参数化生成（不依赖库） |

---

## 三、数据策略（**请先读这一节**）

为了让仓库能正常上传（GitHub 单文件上限 100 MB、仓库建议 <1 GB），
**可重建的大数据不进版本库**：

| 未包含 | 体积 | 怎么重建 |
|---|---|---|
| `_wheels/`（OCP 的离线轮子） | ~513 MB | `python tools\fetch_ocp_wheel.py` 自动下载 |
| `kb/real_brep*/`（实测 B-Rep 网格，518 件） | ~120 MB | 先让 CATIA 导出 STEP（`导出真实几何_双击运行.vbs`），再 `python tools\build_real_brep.py --src <STEP 目录>` |
| `零件真实图片/`（518 张整窗截图） | ~11 MB | 用 `tools\capture_real_images.py`（需 CATIA 会话） |
| `零件真实图片_带背景/`（518×2 双帧） | ~17 MB | `python tools\reshoot_real_parts.py`（需 CATIA 会话） |
| `零件AI建模图片/`（447 渲染图 + 掩码） | ~3 MB | `python tools\render_ai_masks.py --all` |
| `零件比对图/`（全部） | ~22 MB | `python tools\compare_ai_vs_photos.py --all --montage 40` |
| `kb/handbook_grid.jsonl`、`kb/handbook_target.jsonl` | 41.8 + 10.5 MB | 机械设计手册爬取原件；**派生的小表已随包提供**（`bolt_std.json` 等），原件需原站权限重新爬 |
| `kb/papers_cn_cache.json`、`kb/ai_render_index.json` | <0.4 MB | 爬取缓存 / 渲染索引，重跑对应脚本自动生成 |

> `kb/` 里**运行时真正要读的文件全部随包**（缺一个程序就报错），清单与判据见
> `kb/README_数据说明.md`；`tools\check_share_pack.py` 会强制校验这一点。

> 本包已放入：121 张对比图、每族 1 组真实照片与渲染样本、全部判据 JSON —— 足以复现结论。

---

## 四、判据体系（改任何东西都要过这几关）

| 判据 | 脚本 | 现在 | 含义 |
|---|---|---|---|
| 细节复现度（几何） | `tools\bench_detail_fidelity.py` | **99.8%**，≥95% 498/502 | 真实 STEP 里的每个圆柱面（直径/轴线/位置）在模型里有没有；F1 计分，**编造的孔也扣分** |
| 真实几何命中 | `tools\bench_real_geometry.py` | 591/593 | 建出来的**网格**外形 vs 实测外形 |
| CATIA 通道 | `tools\check_catia_export_real.py` | 523/523 | 特征表能被 CATIA 安全执行（无零深特征等） |
| CATIA 孔位几何 | `tools\check_catia_hole_positions.py --all` | 267/267（74601 孔） | 导出的孔位**反算回零件坐标系**与实测逐个比对 |
| 局部布尔验收 | `tools\check_bsp_local.py` | 10/10 | 自研布尔 vs 独立估计值（体积 + 封闭性） |
| **图像相似度** | `tools\compare_ai_vs_photos.py --all` | **84.5%** | AI 渲染图 vs 真实照片：0.5·(1px 容差 IoU) + 0.5·(1−归一化对称 chamfer) |
| 打包完整性 | `tools\check_share_pack.py` | 意外漏掉 0 / 关键文件缺失 0 / .gitignore 误伤 0 / 脚本编码不对 0 | 逐文件比对原项目与协作包，防"改完 .gitignore 忘了文件被静默忽略" |
| 打包规范 | `tools\make_share_zip.py` | 599 文件 / 27.3 MB，条目全是正斜杠 | 用 Python `zipfile` 打包（`Compress-Archive` 会写反斜杠，队友在 macOS/Linux 解压会丢目录），并回读抽查关键文件 |
| 脚本编码 | `tools\fix_script_encoding.py` | 5 个 `.ps1` 带 BOM、7 个 `.bat/.vbs` 是 GBK | `.ps1` 缺 BOM 会让 Windows PowerShell 5.1 直接解析失败（队友双击一闪而过）；`.bat/.vbs` 非 GBK 则中文路径读坏 |
| 自检 | `python -m ai_modeler --selftest` | 35/35 | 内核/解析/MCP/CATIA/界面 |

**规矩**：任何一次改动都必须跑全量回归，**出现绿变红立即回滚**。

> **改完记得查一次打包**：`powershell -File scripts\05_打包协作包.ps1`
> （它先跑校验四项、再打包回读抽查。队友实测踩过两个坑——
> ① `.gitignore` 里写 `catia_*.py`，把源码 `ai_modeler/catia_export.py` 一起忽略了，git 静默不报错；
> ② 四个一键脚本里三个 `.ps1` 缺 UTF-8 BOM，Windows PowerShell 5.1 直接语法报错、双击一闪而过。）

---

## 五、协作方式

* 分支：`main` 保持可发布；功能走 `feat/xxx`，修复走 `fix/xxx`；
* 提交信息：`feat(范围): 说明` / `fix(范围): 说明`（中文可以，写清"改了什么 + 判据变化"）；
* **每个 PR 必须贴判据结果**（`几何相似度` / `图像相似度` / 自检），退化必须说明原因；
* 大文件不要提交：看 `.gitignore` 与第三节的数据策略。

详见 `CONTRIBUTING.md`。

### 拿到队友的更新

**情况一：你就在本仓库目录里工作**（克隆下来直接用）→ 普通 git 就够：

```powershell
git pull
python -m ai_modeler --selftest      # 应 35/35，再动手
```

**情况二："仓库 + 另一个带大数据的工作目录"**（本项目作者的用法：
仓库里只有代码，`kb/real_brep*`/`_wheels`/照片都在另一个非 git 的工作目录里）→ 用同步工具，
它会拉取 → 把改动按你原来的目录布局落到工作目录 → 跑自检 → 不对劲还能回滚：

```powershell
python tools\git_sync.py check              # 只读：队友改了什么
python tools\git_sync.py pull               # 拉取 + 落到工作目录 + 自检
python tools\git_sync.py pull --gates full  # 顺带跑几何判据
python tools\git_sync.py rollback           # 门禁没过 → 退回更新前
```

详见 `docs/用队友的更新_操作手册.md`。

### 把自己的改动推上去

```powershell
python tools\push_to_github.py status   # 只读：看本地比远端多了什么
python tools\push_to_github.py push     # 提交 + 推送（用 GitHub Desktop 已登录的令牌，不用手输密码）
python tools\push_to_github.py verify   # 从 GitHub 侧核对
```

**第一次上手？** 按 `docs\更新协作包到私密仓库.md` 走一遍（含实测踩到的 5 个坑）：

```powershell
# 1) 校验 + 打包
powershell -ExecutionPolicy Bypass -File scripts\05_打包协作包.ps1
# 2) 只有"从 zip 解出来的包"需要：补上 .git（zip 里故意不含 .git）
powershell -ExecutionPolicy Bypass -File scripts\06_初始化Git仓库.ps1
# 3) GitHub Desktop → Add local repository → Commit to main → Publish（勾 Keep this code private）
```

不跑第 2 步的话，GitHub Desktop 会报 `... isn't a Git repository` —— 这不是包坏了，是还没初始化。
