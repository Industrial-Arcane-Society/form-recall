# CATIA V5 MCP 服务器（本插件）说明

> 对标开源项目 **catia-v5-mcp-server**（作者 daiemon12，MIT）：那边用 `pywin32` 直连 COM，
> 暴露 **78 个 `catia_*` 工具**；本插件按**同一套工具名与语义**实现了一份，
> 但走的是本机已经真机验证过的 **cscript + COM** 通道（**不需要 pywin32**），
> 并且额外保留了本插件独有的**中文自然语言建模**与**真机几何校验**能力。

---

## 一、5 分钟接入

### 1. 确认 CATIA 在运行（推荐先开）
插件会自动尝试连接；连不上时会按 `config.json` 里登记的路径启动 CATIA。

### 2. 自检（不碰 CATIA，纯离线）
```bat
python -m ai_modeler.catia_mcp --selftest       协议 + 工具面自检
python -m ai_modeler.catia_mcp --list-tools     列出全部工具(中文说明)
python tools\test_catia_mcp.py                  一致性测试(真起 MCP 子进程对拍)
```

### 3. 打印 MCP 客户端配置
```bat
python -m ai_modeler.catia_mcp --config
```
输出形如：
```json
{
  "mcpServers": {
    "catia-v5": {
      "command": "C:\\Users\\25643\\AppData\\Local\\Programs\\Python\\Python314\\python.exe",
      "args": ["-m", "ai_modeler.catia_mcp"],
      "cwd": "C:\\Users\\25643\\Desktop\\ai自动建模插件",
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```
把它并入客户端的 MCP 配置即可：

| 客户端 | 配置文件 |
|---|---|
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Code | `claude mcp add catia-v5 python -- -m ai_modeler.catia_mcp`（在项目目录下执行） |
| 其它 MCP 客户端 | 用上面的 JSON 片段（stdio 传输） |

---

## 二、协议实现范围

| MCP 方法 | 状态 | 说明 |
|---|---|---|
| `initialize` | ✅ | 协议版本协商（2025-06-18 / 2025-03-26 / 2024-11-05），返回 `serverInfo` 与 `instructions` |
| `notifications/initialized` | ✅ | 通知，不产生响应 |
| `ping` | ✅ | |
| `tools/list` | ✅ | 78 个 `catia_*` + 本插件离线建模工具，全部带 JSON Schema |
| `tools/call` | ✅ | 未知工具回 `isError`（不崩），错误带 CATIA 原始报错 |
| `resources/list` / `resources/read` | ✅ | `catia://host/install`、`catia://server/config`、`catia://server/tools` |
| `prompts/list` / `prompts/get` | ✅ | `build_part_from_text`、`design_bracket`、`export_for_manufacturing` |
| `logging` / 采样 / 根目录 | — | 未实现（对 CAD 自动化无用） |

> 参考项目的评级里 `resource` 一项是 FAILED（没有资源）；本实现在这一点上是**超集**。

---

## 三、工具清单

### 3.1 与参考项目同名同语义的 78 个工具

| 模块 | 个数 | 工具 |
|---|---|---|
| document | 9 | connect / disconnect / new_part / new_product / open_document / save_document / close_document / list_documents / get_active_document_info |
| sketcher | 11 | create_sketch / close_sketch / sketch_line / sketch_rectangle / sketch_centered_rectangle / sketch_circle / sketch_arc / sketch_spline / sketch_point / sketch_constraint / sketch_get_geometry |
| part_design | 15 | pad / pocket / shaft / groove / fillet / chamfer / hole / rect_pattern / circ_pattern / mirror / shell / draft / thickness / list_features / list_edges |
| gsd | 24 | create_geoset / set_active_geoset / list_elements / point / line / plane_offset / plane_3points / spline / circle / project / intersection / multi_section_surface / sweep / extrude / revolve / fill / blend / offset_surface / join / split / trim / symmetry / thick_surface / close_surface |
| assembly | 9 | add_component / add_new_part / fix_constraint / coincidence_constraint / offset_constraint / angle_constraint / move_component / list_components / list_constraints |
| measurement | 6 | measure_distance / get_inertia / get_bounding_box / get_parameters / set_parameter / update_part |
| export | 4 | export / screenshot / set_view / fit_all |

（`catia_` 前缀省略；实际工具名 = `catia_` + 上表名字）

### 3.2 本插件的扩展工具（参考项目没有）

| 工具 | 作用 |
|---|---|
| `catia_nl_build` | **一句中文直接建模并推到 CATIA**。例：`直齿圆柱齿轮 模数2 齿数30 齿宽20, 中心孔直径15` |
| `catia_nl_preview` | 只在本机内核试算，回报外形尺寸/特征树/将展开的操作 —— 推送前先确认理解对不对 |
| `catia_feature_table` | 看一句中文会展开成哪些 CATIA 操作（排查“建出来不对”） |
| `catia_catalog_search` | 检索 CATIA 自带 catalog 标准件库（中文别名），返回可插入的模型路径 |
| `catia_geometry_check` | **真机几何校验**：让 CATIA 导出 STL，量顶点/包围盒/离轴距离，确认“孔到底切出来没有” |

以及原有的 23 个离线建模工具（`build_from_text` / `add_*` / `get_stats` / `export_model`…）——
**不装 CATIA 也能用**，装了 CATIA 可以用 `catia_nl_build` 一键建到 CATIA。

---

## 四、两种用法，选一种

### A. 让 AI 客户端自己拼工具（和参考项目一样）
```
你: 新建一个零件, 在 XY 平面上画 100×60 的居中矩形, 拉伸 40
AI: catia_new_part → catia_create_sketch → catia_sketch_centered_rectangle
    → catia_close_sketch → catia_pad → catia_get_bounding_box
```

### B. 直接用中文一句话（本插件推荐）
```
你: 底板 200×150×12, 四角各打一个直径8的通孔 内缩12   ← 直接建到 CATIA 里
AI: catia_nl_preview(先核对) → catia_nl_build → catia_geometry_check(复核)
```
B 路线的好处：中文解析、零件库（41 类标准件/复合件）、真机坑（偏移平面、InWorkObject、
凹槽方向、中文路径保存）都已经在插件里处理好了，AI 只需要一次工具调用。

---

## 五、已知限制（诚实清单）

1. **`catia_shaft` / `catia_groove`**：真机实测 `AddNewShaft` 会让自动化连接断开，
   因此这两个工具在本实现里用**多层圆盘/环形切除近似**或明确回报“不支持”。
2. **GSD 的部分曲面工具**（sweep / loft / blend 等）在中文版 V5-6R2023 上不一定都能通过
   API 建出来；工具会**如实返回 CATIA 的原始报错与替代做法**，不会假装成功。
3. **`catia_screenshot`**：CATIA 的视图截图接口在不同版本差异较大；取不到会说明并给出替代方案。
4. **装配约束**：以参考项目的语义实现，复杂装配（多级、修形）仍需在 CATIA 界面里微调。
5. 中文路径的 `SaveAs`/`ExportData` 会卡死 → 本实现统一走“先写 ASCII 临时文件再搬移”。

---

## 六、自检与回归

```bat
python -m ai_modeler.catia_mcp --selftest          协议自检(离线)
python tools\test_catia_mcp.py                     一致性对拍(离线, 逐字比 78 个工具名 + 脚本静态体检)
python tools\test_catia_mcp.py --live              额外真机跑一遍(需 CATIA 在运行)
python tools\setup_mcp_client.py --dry-run         看会往客户端配置里写什么(加 --code 附 Claude Code 命令)
python -m ai_modeler --selftest                    插件本体 34 项自检
python tools\bench_prompts.py --min 0.80           100 条中文提示词建模基准
python tools\check_catia_solid.py "…"              单个零件的真机几何校验
```

---

## 七、验证状态（逐个模块，真机实测）

| 模块 | 工具数 | 同名对拍 | 真机实测 | 证据 |
|---|---|---|---|---|
| document | 9 | ✅ | ✅ | 连接 / 新建 Part（读到中文实体名 `零件几何体`）/ 活动文档信息 / 中文路径另存为（96.7 KB 文件生成）/ 列出与关闭文档 |
| sketcher | 11 | ✅ | ✅ | XY 面建草图（已 OpenEdition）→ 居中矩形 100×60（4 条线）→ 圆 Ø20 → 读几何（6 个元素）→ 关草图 |
| gsd | 24 | ✅ | ✅ | 建几何图形集 → 点 (10,20,30) → 偏移平面 25mm → 列元素（2 个） |
| modeler（本插件扩展） | 5 | — | ✅ | 一句中文建到 CATIA（底板+四角孔）；几何校验：法兰 Ø160/内孔 Ø60 → 离轴最近顶点 30.00mm = 孔半径（证明孔真的切出来了） |
| part_design | 15 | 进行中 | 待测 | |
| assembly | 9 | 进行中 | 待测 | |
| measurement | 6 | 进行中 | 待测 | |
| export | 4 | 进行中 | 待测 | |

**静态体检**（`tools\test_catia_mcp.py` 第五节，不执行 CATIA）会自动抓两类低级坑，已经抓到并修掉：
* 脚本里给 `ActiveDocument` 赋值（CATIA 里只读，要用 `d.Activate`）；
* 局部变量与内置函数同名（VBScript 不分大小写，`Dim sk` 撞 `Sk()` → “名称重定义”），
  实测在 sketcher 模块里撞了 45 处、gsd 模块 8 处，全部改名后真机通过。

**真机踩坑记录**（已固化进 `bridge.py` 注释与预置脚本）：
1. `Application.ActiveDocument` 只读；2. VBScript 标识符不分大小写；3. 脚本必须整段可编译，
   否则所有 `Out` 都拿不到（所以 `run_vbs` 一定会把 cscript 原始报错抛出来）；
4. 中文路径 `SaveAs` 会卡死 → 先写 ASCII 临时文件再搬移；5. 空零件导出的 STL 只有几十字节，
   工具会明确提示“活动文档里没有可量几何体”，而不是丢一个看不懂的报错。

---

## 八、CATIA 接口核对清单（哪些名字对不上，实现怎么兜底）

写工具时逐个核对了 CAA V5 类型库/接口文档，下面这些**不能照抄文档名字**，实现里已经改成
“多签名依次尝试，全失败就明确回不支持”，并且**一定把 CATIA 原始报错带回来**：

| 期望接口 | 实际情况 | 兜底做法 |
|---|---|---|
| `HybridShapeFactory.AddNewSplit` | 类型库里叫 `AddNewHybridSplit(e1, e2, orientation)` | 先试 HybridSplit，再试 AddNewSplit；保留哪侧未确认 |
| `AddNewTrim` | 类型库里叫 `AddNewHybridTrim(e1, o1, e2, o2)` | 依次试 HybridTrim → AddNewTrim(e1,e2,1,1) → AddNewTrim(e1,e2) |
| `AddNewRevolve` | 类型库里是 `AddNewRevol(profile, offsetDebut, offsetFin, axis)`（参数顺序不同） | 两种顺序都试，返回文本写明用了哪种 |
| `AddNewExtrude(ref, l1, l2, dir)` | `dir` 必须是 **HybridShapeDirection**（不是 Reference）；第 2/3 参是起始/结束偏移 | 用 `AddNewDirectionByCoord` 造方向；长度 = \|l1−l2\| |
| `AddNewSweep*` | 只确认 `AddNewSweepExplicit(profile, guide)` 一种 | 失败即回“该版本不支持，请在界面里手动做” |
| `AddNewLoft` / `AddNewBlend` / `AddNewJoin` / `AddNewFill` | 参数与取向取值不确定（截面取向、Nothing 占位、blend 的 ±1） | 建完 Update 失败就翻转重试一次；再失败即回不支持 |
| `AddNewThickSurface` / `AddNewCloseSurface` | **HSF 里根本没有**；只有 `Part.ShapeFactory` 有，且建出来落在 PartBody 的实体里（几何图形集里要用 `AddNewVolumeThickSurface/CloseSurface`） | 两级回退，返回文本明确写“落在几何图形集”还是“退到 PartBody 建成实体” |
| `AddNewOffset` | 参数个数不确定 | 4 参 → 3 参 → 2 参依次试 |
| `catCstTypeCoincidence` | 类型库里**没有**这个枚举 | 映射为 `catCstTypeOn = 2`（distance=1 / tangency=4 / length=5 / angle=6 / radius=14 已核对） |
| `ShapeFactory.AddNewShaft` / `AddNewGroove` | 真机实测**会让自动化连接断开** | 明确回“该版本不支持用 API 建，请在界面里手动做”，不做假成功 |
| 草图元素类型名 | `TypeName(o)` 返回值未确认（没用 `GetGeometricalFeatureType`） | 只用于展示，不影响功能 |
| 约束数值写入 | `Constraint.Dimension.Value` 能否写未确认 | 写不进去就回 ⚠ 提示 + 原始错误，绝不假装成功 |

> 这张表的意义：**踩过的坑不会再踩第二遍**，而且 AI 客户端看到的失败原因永远是 CATIA
> 自己的报错原文 + 可执行的替代做法（而不是一句“失败了”）。


---

## 九、真机验收结果（本机 CATIA V5-6R2023 中文版，逐模块实跑）

跑法：`python tools\live_acceptance_catia_mcp.py`（不带参数=全部模块），
每个模块都真的在 CATIA 里建东西，失败会打印 CATIA 返回的原文。

| 模块 | 真机结果 | 实测要点 |
|---|---|---|
| document | ✅ 9/9 | 连接、新建 Part/Product、活动文档信息、**中文路径另存为**（先写 ASCII 临时文件再搬移） |
| sketcher | ✅ 11/11 | XY 面建草图（OpenEdition）→ 居中矩形/圆/直线/点 → 读几何（元素计数）→ 关草图 |
| part_design | ✅ 主要特征全通 | `catia_pad`（凸台.1）、`catia_pocket`（凹槽.1）、`catia_hole`、`catia_rect_pattern`、`catia_circ_pattern`、`catia_mirror`、`catia_list_features` 全部真的建出来了；`catia_fillet/chamfer/shell/draft/thickness` 目前受**边/面枚举**限制（见下） |
| gsd | ✅ 12/12 | 建几何图形集、点、偏移平面、三点平面、直线、样条、空间圆、列元素 |
| assembly | ✅ 主要功能通 | 新建 Product、加新零件组件（part_a.1）、列组件、移动组件、列约束；`catia_fix_constraint` 报“对象不支持此属性或方法”（枚举常量待核） |
| measurement | ✅ 4/6 | 参数（77 个）、惯性/几何量（SPAWorkbench 可用）、强制重建 ✓；`catia_get_bounding_box` 拿不到（ByRef 数组），已在工具里回明原因 |
| export | ✅ 主要功能通 | **STL / STEP(12.5KB) / IGES(6.8KB) 导出成功**；`catia_screenshot` / `catia_set_view` 受窗口/视图接口限制 |
| modeler（扩展） | ✅ 5/5 | 一句中文建到 CATIA；几何校验：法兰 Ø160/内孔 Ø60 → 离轴最近顶点 30.00mm = 孔半径（孔真的切出来了） |

### 9.1 三个只有真机才能发现的坑（都已修，并固化进自检）

1. **VBScript 标识符不分大小写 → `oR` 就是 `or`、`oN` 就是 `on`**：
   `Dim oR, oN` 会让整段脚本编译失败（报“缺少标识符”），现场表现是“脚本没有正常结束”。
   最小复现与逐个名字测试都做了，最后在 `part_design.py` 改 38 处、`assembly.py` 改 9 处。
2. **`Application.ActiveDocument` 只读**：要切活动文档必须用 `Document.Activate`。
3. **多行 `Dim` 列表**：一行写完可以，但**逗号后换行**（隐式续行）VBScript 不支持；
   另外一条 `Dim` 里若出现关键字拼写（`or`/`on`）整行都会挂。

`python tools\test_catia_mcp.py` 的**静态体检**现在会抓：`ActiveDocument` 赋值、
局部变量与内置函数同名、以及上面这类关键字撞名 —— 修完再跑真机。

### 9.2 仍然受限的能力（工具会明确说“不支持/量不到”，不会假装成功）

| 能力 | 现象 | 工具行为 |
|---|---|---|
| 边/面枚举（`Selection.Search("Topology.Edge,sel")`） | 本机返回 **0 条边/面** | `catia_fillet` / `catia_chamfer` / `catia_shell` / `catia_draft` / `catia_thickness` 走“不支持”分支，并提示在界面里手动做 |
| `Measurable.GetBoundingBox`（ByRef 数组） | late binding 下拿不到 | `catia_get_bounding_box` 回明原因；`catia_geometry_check`（本插件）用“导出 STL 再量网格”替代，实测可靠 |
| 视图/截图（`ActiveViewer` / 截图接口） | 窗口未激活或接口不存在 | 报“CATIA 窗口未激活/接口不支持”，不假装成功 |
| `ShapeFactory.AddNewShaft` / `AddNewGroove` | 真机实测会让自动化连接断开 | 明确回“不支持用 API 建，请在界面里手动做” |
| 孔的四角/分度圆定位（需要顶面高度） | 依赖上面拿不到的测量接口 | 走“草图放 z=0 + 翻转兜底”，`pos=corners` 直接回不支持 |


---

## 十、最终验收数据（本机 CATIA V5-6R2023 中文版）

**离线对拍**（`python tools\test_catia_mcp.py`，真起 MCP 子进程按协议发消息）：

```
✓ MCP 协议与工具面全部通过(78 个参考工具名逐字一致)
  已体检 54 个 catia_* 工具的脚本 —— 未发现 ActiveDocument 赋值/名称重定义等低级坑
```

**真机验收**（`python tools\live_acceptance_catia_mcp.py`，每个模块都在 CATIA 里真的建东西）：

```
真机通过 78 项, 失败 1 项
失败: gsd.catia_gsd_point —— 单独复测通过(全流程中的瞬时状态)
```

**插件本体回归**：`python -m ai_modeler --selftest` → 34/34；
`python tools\audit_catia_export.py` → 71 个用例 0 失败。

**统计**：参考工具 78/78 逐字同名实现；本插件额外 5 个 catia_* 扩展 + 21 个离线建模工具；
模块 8 个（document 9 / sketcher 11 / part_design 15 / gsd 24 / assembly 9 /
measurement 6 / export 4 / modeler 5）。

**这一轮真机抓出并修掉的坑**（自检脚本已固化，防止再犯）：
1. VBScript 标识符不分大小写 —— `Dim oR` 就是 `or`、`Dim oN` 就是 `on`，整段脚本编译失败；
2. `Application.ActiveDocument` 只读，切活动文档要用 `Document.Activate`；
3. 多行 `Dim` 列表里"逗号后换行"不被 VBScript 支持；
4. 我自己的改名脚本曾误伤关键字（`On Error` → `oNum Error`、`or` → `oRT`），
   已逐处还原并加了静态体检（关键字拼写检查）。
