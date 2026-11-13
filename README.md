# AI 自动建模助手（协作包）

> 一句话：把**一句中文提示词**变成能在 CATIA 里用的真实零件模型。
>
> **面向汽车装备制造**：焊装夹具的结构件与标准件（托块、压臂、基板、气缸、销、
> 紧固件）已覆盖 19 个零件族；汽车零部件与模具方向的特征原语相同，族库尚待扩充。
> 数据全部来自 CATIA 实测 STEP，内建**判据体系**（几何 + 图像）与**闭环回归**，任何改动都会被量到。

| | |
|---|---|
| 语言 / 依赖 | Python 3.11+（实测 3.14.3 可用）；核心**纯标准库**，可选 `cadquery-ocp`（OpenCASCADE） |
| 界面 | tkinter 桌面程序（`python -m ai_modeler`） |
| CATIA 通道 | V5 宏 + cscript/COM 推送；也可导出 STEP 让 CATIA 导入 |
| 当前判据 | **几何相似度 99.8%（≥95% 498/502）**；**图像相似度 84.5%**（AI 渲染图 vs 真实照片） |
| 自检 | `python -m ai_modeler --selftest` → 47/47 |

---

## 一、目录罗列

> 参照常见仓库的罗列方式，但多加一列**说明**，让人一眼知道每个目录是干什么的。

| 目录 / 文件 | 说明 |
|---|---|
| `ai_modeler/` | **插件源码**：按 `agent/`、`sdk/`、`core/`、`parts/`、`catia_backend/`、`ui/`、`runtime/` 分层；包结构说明见 `ai_modeler/README.md` |
| `tools/` | **开发与数据工具**：几何/图像判据、渲染、比对、STEP 特征提取、建库和打包；分类索引见 `tools/README.md`。现有脚本路径作为兼容入口保留 |
| `catia_plugin/` | CATIA 侧宏（`AI自动建模助手.CATScript`）与安装脚本 |
| `datasets/训练提示词/` | 1828 条提示词（训练/测试集、负例、说明），可直接拿去做提示词↔模型训练 |
| `docs/architecture/` | AgentLoop、CADPlan SDK、CATIA MCP 和接口设计 |
| `docs/guides/` | CATIA 操作、安装和日常使用说明 |
| `docs/collaboration/` | Git 分支、协作包、上传与协作流程 |
| `docs/planning/` | 需求梳理、升级方案和项目流程资料 |
| `docs/历史报告/` | 原项目顶层散落的历史 Word/Markdown 报告（带轮复盘、阶段报告、升级对比、细节复现度报告等），按时间留痕 |
| `artifacts/` | 本地生成的模型、预览、AgentLoop 运行记录、自检输出和模型截图；不作为源码目录 |
| `.cache/` | 本机搜索缓存等可重建数据；已加入 Git 忽略 |
| `reports/` | 各类报告（Markdown + Word），含判据报告与故障复盘 |
| `reports/跑图对比/` | **最近几轮的跑图对比**：AI 建模图 vs 真实照片，并排拼图（121 张） |
| `examples/` | 样例数据：每族 1 组真实照片（含背景帧）+ 1 个 AI 渲染样本（含掩码） |
| `kb/` | 知识库：**运行时依赖的标准表/词表/实测特征**（螺栓、轴承、带轮、链轮、销、纠错词表、12 MB 实测特征库）。`kb/README_数据说明.md` 写清了每个文件谁在用、哪些不带、怎么重建 |
| `scripts/` | 一键脚本：装依赖 / 跑全流程 / 重建大数据 / 跑验收基准 / **打包协作包** / **初始化 Git 仓库** |
| `启动脚本/` | 双击即用的 Windows 脚本：启动程序、连接 CATIA、安装插件、装桌面快捷方式及 STEP 导出宏 |
| `.gitignore` / `.gitattributes` | 数据策略（哪些大文件不进库）与换行策略（`.ps1/.bat/.vbs` 固定 CRLF、源码固定 LF）。**改完这两个文件要跑 `tools\check_share_pack.py` 复核** |
| `README.md` | 本文件 |
| `CONTRIBUTING.md` | 协作约定：怎么改、怎么验、怎么提 |
| `LICENSE` | MIT |

---

## 二、快速上手（3 分钟）

```powershell
# 1) 打开界面；纯规则建模核心不要求第三方依赖
python -m ai_modeler

# 2) 安装多模态 Agent SDK 和可选加速依赖
powershell -ExecutionPolicy Bypass -File scripts/01_安装依赖.ps1

# 3) 配置「工具 → AI 大模型设置」后，使用持续 Agent 对话与参考图建模
```

使用本地规则解析和网格内核不需要 Agent SDK；需要图片识别、LLM 工具调用和联网搜索时，运行步骤 2 安装 `deepseek-harness-sdk==0.1.5rc1`。

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
| `kb/real_brep*/`（实测 B-Rep 网格，518 件） | ~120 MB | 先让 CATIA 导出 STEP（`启动脚本/导出真实几何_双击运行.vbs`），再 `python tools\build_real_brep.py --src <STEP 目录>` |
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
| 自检 | `python -m ai_modeler --selftest` | 47/47 | 内核/解析/MCP/CATIA/界面/工作流 |

**规矩**：任何一次改动都必须跑全量回归，**出现绿变红立即回滚**。

> **改完记得查一次打包**：`powershell -File scripts\05_打包协作包.ps1`
> （它先跑校验四项、再打包回读抽查。队友实测踩过两个坑——
> ① `.gitignore` 里写 `catia_*.py`，把源码 `ai_modeler/catia_export.py` 一起忽略了，git 静默不报错；
> ② 四个一键脚本里三个 `.ps1` 缺 UTF-8 BOM，Windows PowerShell 5.1 直接语法报错、双击一闪而过。）

---

## 面向 LLM 的结构化建模接口

多模态模型可按 prompts/CADPlan-LLM.md 与 schemas/cadplan-v1.schema.json
生成 CADPlan，再调用 Python SDK 与 CLI；缺少必需参数或数值来源时，接口会返回待确认项。
详细字段、CATIA 特征支持状态、严格模式和输出文件见 docs/architecture/CADPlan-SDK.md。

常用命令：python -m ai_modeler --cadplan validate .\plan.json、
python -m ai_modeler --cadplan preview .\plan.json、
python -m ai_modeler --cadplan build .\plan.json --catpart .\out\part.CATPart。

预览会返回可打开的正视 / 右视 / 俯视 PNG；建模结果还会包含三视图和
等轴测四视图。LLM 应先查看预览，建模后再查看输出图片，发现偏差就修订
CADPlan 并重建。预览图来自本地网格内核，不能替代 CATIA B-Rep 检查。

### 桌面 AgentLoop

运行 `python -m ai_modeler` 打开对话界面。输入框支持 **Ctrl+V 直接粘贴剪贴板图片**，也可以选择多张参考图。Agent 由 DeepSeek Harness Python SDK 驱动，按需要调用 CADPlan MCP 工具、网页搜索和多模态视觉复核；安装 SDK 请运行 `scripts/01_安装依赖.ps1`。MCP 工具不开放 shell。模型预览实时显示在右侧；本地候选不会自动同步到 CATIA。

详细说明见 [docs/architecture/AgentLoop.md](docs/architecture/AgentLoop.md)。

### 夹具单元协同设计（五步 Workflow）

工具栏「▣ 夹具单元协同设计」或「工具」菜单打开。它是与对话区**并存**的第二条入口：
对话区解决「一句话出一个零件」，向导解决「一个有输入约束、要人工复核、结论必须可追溯的
单元设计任务」。向导自己不实现建模逻辑——定完参数后把提示词交回对话区，落到 CATIA 的
路径始终只有一条。

| 阶段 | 做什么 | 产物 |
|---|---|---|
| 1 工程输入 | 接收工件数模、PRP 定位夹紧点、项目规则 | 统一坐标系、P01/P02 点位、资料清单 |
| 2 单元规划 | 围绕 PRP 规划基板/托块/压臂/气缸，区分自制件/外购件/标准件 | 单元结构方案 |
| 3 参数建模 | 参数化立板、托块、压臂、连接座及安装孔/铰接孔/减重槽 | 部件参数表 |
| 4 关联装配 | 配置气缸、定位销、铰轴、紧固件，建立固定/转动/连接关系 | 装配关系 + 机构行程 |
| 5 校核输出 | 汇总装配范围、名义间隙、机构姿态、参数版本 | 单元记录 JSON + 复核摘要 |

**四条红线**（`ai_modeler/workflow/` 里写死的，别为了好看绕过去）：

1. 前置不满足不进下一步——`can_enter()` 返回具体缺项；
2. P01 固定点偏移锁死为 0，任何阶段都改不动；
3. 参数一改，下游检查全部作废，`revision` 递增；
4. **有未通过的检查就不能进复核**（门禁规则）——默认案例压臂间距 d=3<5，
   所以第 5 阶段会被拦下并列出「检查未通过：压臂最小间距…」，改到 L=75 才放行。

**检查结论四分法**：`pass / fail / not_run / not_applicable` 分开记，不合并成一句「通过」。
其中完整运动轨迹恒为 `not_run`、制造与强度恒为 `not_applicable`——**没有的能力不许报绿灯**。

默认案例（UN200 气动夹紧单元）故意让压臂间距检查失败：`d = L − 62 + 4s`，L=65 时 d=3 < 5，
要接受修正（L→75，d=13）才能推进。这条路径在自检和单测里都锁着。

导出的单元记录带四个事实字段（`simulated / cad_connected / model_api_called /
real_cad_files_created`），人工勾选改不动它们。规则条目的 `confirmed=false` 表示
「示例约定」，不是企业标准。

接口细节见 [docs/planning/夹具工作流_接口契约.md](docs/planning/夹具工作流_接口契约.md)。

### 元增量学习（核心学习机制）

程序原来每轮都靠大模型从零理解提示词，**没有任何积累**：同一个「内六角螺钉」来了十次，十次都从头想。
`ai_modeler/meta/` 补的就是这一层。

**它不是外挂功能，是提示词理解链路的前置层**：`_agent_generate` 在请求模型之前先查元记忆，
把「这族以前怎么做、典型包络多少、相似案例长什么样」拼进本轮输入；拿到结果后再回写。

| 环节 | 做法 | 为什么 |
|---|---|---|
| 表示 | 字符 2–4 gram TF-IDF + 型号数字 + 族线索位，426 维 | 中文不依赖分词；可解释、可审计 |
| 判决 | **原型 + exemplar kNN 混合**（w=0.6 偏 kNN） | 单纯原型会把 M5x8~M5x50 平均成一个点，丢细节；单纯 kNN 不随学习收敛 |
| 增量 | 原型 EMA（α=1/(n+1)）+ 每族 ≤32 条 exemplar | 学得越多越稳，新样本冲不垮旧原型的骨架 |
| 门槛 | 同族累计 ≥3 条 verified 才建原型 | 宁可行先说「不知道」，也不让噪声定义一族 |
| 写入 | 只有 `validation.state == 'valid'` 才算 verified | **把错的学进去比不学更糟**——候选和失败只进候选池 |

**评测（可复现，`python -m pytest tests/test_meta_memory.py`）**

| 口径 | n_test | 混合判决 | 1-NN 基线 | 说明 |
|---|---|---|---|---|
| 全量 | 363 | 0.9972 | 0.9972 | 余下 1 条错在「手动夹紧气缸 vs 夹紧气缸」，1-NN 也错同一条 |
| 带类别词组 | 216 | 0.9954 | 0.9907 | 提示词自带族名，本来就容易 |
| **纯裸型号** | **92** | **1.0000** | **0.9891** | 去掉族名线索后的真实难度，**+1.09 pp** |

> ⚠️ 别把「全量 99.7%」当成模型多强。1828 条数据里 520 条提示词直接写着「内六角螺栓」，
> 那是泄漏不是能力。真实难度看**裸型号**那一行，所以指标一律按 variant 做消融。

增量方面：契约版 forgetting = 0.00196（8.1 s）；困难版（原型全建好后按序灌 10/50/200 条/族的流，
走 EMA + exemplar 淘汰）**max_drop = 0.0000**，没有一族掉分。

**为什么不用 torch**：用户双击启动的是系统 Python，不装 CUDA 也能用。元学习模块一旦依赖 torch，
程序就装不动了；而原型网络本来就不需要深度网络——1828 条数据也训不出来。少样本、可增量、可解释，
这三条比「用了深度学习」重要。

用法：菜单「工具 → 元增量学习面板」，首次打开会在后台把 1453 条历史灌进记忆（约 1 秒），
面板里能看族记忆、样本数、原型是否建立，并一键跑评测。记忆存在 `kb/meta_memory.json`（不进版本库）。

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
python -m ai_modeler --selftest      # 应 47/47，再动手
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

详见 `docs/guides/用队友的更新_操作手册.md`。

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
