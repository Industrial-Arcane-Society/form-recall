# Agent 质量重构：统筹与验收清单

## 目标与边界

解决看图差、思考长且无用、多轮复核越改越错、模型转译不可靠四项问题。用户指定所有实现和正式 Agent CLI 验收由 gpt-6-luna / max 的 SubAgent 完成。主 Agent 只规划、协调、审核证据，不写实现代码。

基线：main 7c5f9bd 工作树干净；实现基于 feat/agentloop-multimodal-analysis a5fbaea，新分支 feat/agent-quality-rebuild。保留原快速模式、CADPlan/AIMODEL、手动 CATIA 通道。实际可用能力与未验证能力必须分别报告。

### 用户最新验收通道（覆盖下文旧的 UI 排期说明）

2026-10-01 用户明确：不要直接打开软件窗口，使用 CLI 验收。因此不再进行桌面鼠标、键盘或截图自动化。实际程序验收改为正式 DSH Agent CLI 调用；输入原图或自然语言，保留真实供应商请求、工具时间线、CADPlan/AIMODEL、网格、渲染预览及核验报告。直接 SDK 预置 CADPlan 仍仅是辅助测试。

当前历史 `--cli` 是无 LLM 的规则解析快速模式，`--cadplan` 是 SDK 操作，均不能冒充 DSH Agent 验收。正式 Agent CLI 使用 `run_dsh_agent`、原生 Cordis 工具、私有 JSONL worker、会话与审查链。原界面进度接线保留，但不宣称已做窗口视觉验收；有意义的动作/等待/工具进度通过真实 CLI 输出验收。同一会话禁止并发；不同隔离会话可独立测试。

## 分工

2026-10-01 用户最新将 subAgent 配置改为 `gpt-6-luna`、`max`。此前 medium Agent 已停止；当前接续工作由 `loop_max`、`input_max`、`geometry_max` 承担。目标范围和 CLI 验收要求不变。

| 工作线 | 负责人 | 独占文件范围 |
|---|---|---|
| 施工、复核、返修及候选管理 | loop_review | dsh_tool_domain.py、plan_state.py、system.md、对应测试 |
| 输入、会话、推理、真实调用诊断、界面进度 | input_reasoning | dsh_agent.py、llm.py、providers.py、流/附件桥、必要 app.py 接线、对应测试 |
| BREP 几何执行、特征语义、确定性核验 | geometry_backend | sdk/cadplan.py、core/model/docmodel.py、core/geometry、项目依赖与对应测试 |

跨文件接口须先协调。同一逻辑会话的真实验收串行；不同隔离CLI会话可并行，保留各自版本哈希及运行证据，不改全局模型配置。

## 完整 P0–P2 范围

| 编号 | 优先级 | 要求 | 负责人 | 完成所需证据 |
|---|---|---|---|---|
| L01 | P0 | 删除逐特征强制视觉审查；区分未施工与建错 | loop_review | 真实 Agent 连续施工，未完成细节不触发错误返修 |
| L02 | P0 | 具体差异驱动返修，评分只辅助 | loop_review | 故意可见偏差被定位、修改、用新版证据关闭 |
| L03 | P0 | 取消全配对低分死锁；明确保留未验收候选 | loop_review | 视角不可比/歧义能保存候选并报告未解决项 |
| L04 | P0 | 可见孔实例与全局特征条目分开 | loop_review / geometry_backend | 多表面孔、阵列孔不会误用全局数验收 |
| I01 | P0 | 会话原图资产、来源及哈希持续保留 | input_reasoning | 图片建模后纯文字追问与修改仍可真实复核原图 |
| L05 | P1 | 稳定原图事实、冲突核查、不可观测性、差异账本 | loop_review | 冲突触发重新查看原图，不靠猜测改拓扑 |
| L06 | P1 | 最好候选、进展检测、往返修改检测、回退 | loop_review | 无进展保留最好有效候选，退化结果不替代有效结果 |
| L07 | P1 | 保持自主 web_search/来源读取及离线错误可恢复 | loop_review | 实际 Agent 按需查询标准参数；普通复刻不强制联网；网页不能覆盖用户约束 |
| I02 | P1 | 单原图和 ROI 查看；按需知识样例、明确目标与示例 | input_reasoning / loop_review | Agent 实际调用独立原图/ROI，无无关图污染 |
| I03 | P1 | 主建模与审查统一提供方能力、推理预算和档位 | input_reasoning / loop_review | 真实请求记录证明生效；不虚报模型上下文能力 |
| G01 | P1 | CADPlan 接入真正 OCCT BREP 执行 | geometry_backend | 软件内 Agent 建模报告真实后端与有效实体 |
| G02 | P1 | 当前 27 类特征能力映射；不静默跳过或近似 | geometry_backend | 逐类型映射表及执行验证，未支持显式状态；原 26 类清单不能遗漏新增 socket_head_bolt |
| G03 | P1 | 坐标、单位、axis/base/center、关系和布尔语义 | geometry_backend | 软件内文本及图片建模尺寸/位置证据 |
| G04 | P1 | 实体有效性、组件、体积、孔实例与贯穿检查 | geometry_backend | 原生实体核验报告，不用少数网格采样宣称整体有效 |
| G05 | P1 | 轮廓自交、回转内壁、追加回转及曲面离散风险 | geometry_backend | 对应合法/非法案例与软件 Agent 实际结果 |
| G06 | P1 | 本地几何、视觉验收、CATIA 结果独立 | geometry_backend / loop_review | 文件与界面明确未运行 CATIA 时不声称 CATPart 验证 |
| I04 | P2 | 真实 wire 请求、耗时、图片、重试、用量可观测 | input_reasoning | 真实调用级脱敏记录，未知用量明示未知 |
| I05 | P2 | 左侧有意义的动作、等待、工具和思考摘要进度 | input_reasoning | 真实软件屏幕及事件时间线，无长时间无提示 |
| V01 | 全部 | 快速模式、Agent 模式、连续对话兼容 | 全体 | 实际软件分别运行，人工预置几何不冒充 Agent 输出 |
| V02 | 全部 | 无历史独立冷启动建模验收 | 独立验收 subAgent（实现后排期） | 只给实际软件、新提示词/接口说明与原图；不提供旧 CADPlan、尺寸或正确构造步骤 |

## 真实软件验收场景

1. 单参考图：长方体、正面中央退台、3×3 九贯穿孔，大/小/大布局；无绝对尺寸也先完成一轮，记录比例估算。
2. 同一工件多参考视图：按视角复核，不把遮挡和投影差异当缺失。
3. 纯文字：明确尺寸的主体、孔、凹槽；核对输出尺寸与实体拓扑。
4. 连续对话：图片首轮后纯文字追问、局部修改；原图仍可用，无不必要重建。
5. 返修：可见孔位/比例偏差；明确问题和修改，最新几何证据关闭原问题。
6. 复核冲突、无进展、工具失败：候选可保留、错误明确、不会无尽重试或用低质量结果替代最好候选。
7. 普通施工与复杂识图/审查分别核验实际推理配置、调用耗时和用量。
8. 复杂几何及支持状态：回转、阵列、多个实体、合法与非法轮廓、未支持特征；保留能力边界。

每个场景保存用户输入、CLI命令与退出码、真实 DSH 工具/供应商请求时间线、CADPlan/AIMODEL、预览、实体核验和未解决项。当前验收走正式 Agent CLI；不打开桌面软件窗口，因此GUI截图不属于本轮证据。代码检查、单元测试或直接调用 SDK 只能提供辅助证据，不能替代内置 Agent CLI 的真实模型请求与工具调用。

2026-10-01 用户重新提供的九孔退台等轴原图已原样保存为 `artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`，SHA256 `53ED87FADF8CCA869DA921627E6A13C845424C6906B3DE517D5CCA972DB94553`。初始建模提示不能预置历史尺寸或正确 CADPlan。当前仅有此张新原图，多图验收还可使用已有的真实其他工件参考图集。

## 完成规则

初始全部未完成。每项须有当前实现和覆盖其范围的证据，缺少/间接证据一律未完成。性能记录真实测量，不编造提速百分比或思考 token。实际供应商不提供的统计标未知。保留全范围目标，不因某条线难验收而缩小要求。除用户后续明确授权外，本轮不创建 PR、不自动推送。

### 2026-10-01 当前接续核对

- 用户最新配置为 GPT-6 Luna / medium；当前 input_mid、loop_mid、geometry_mid 均按此配置运行，取代旧 max 设置。
- root 读取最新几何日志：针对重复孔、合法扩孔、沉孔及部分预览的 11 项通过；SDK 与 BREP 合并 39 项通过。完整 27 类矩阵正在独立重跑，尚不能宣布全部特征可靠。
- 首次独立原图盲测的进程和执行句柄均已不存在，无最终退出码或交付结果。失败轨迹已归档 baseline-failed；不得宣称仍在运行或已通过。16 次请求均带原图，15 次完整请求的最大 reasoning 为 1565；主要失败是错误识图后的等价孔参数反复修改及缺少有效部分预览。
- 真实跨进程 CLI 续话曾报 session already exists；实现现将应用会话与 DSH 内部会话分离，实际纯问答成功读回旧模型。后续实际修改得到 110×60×20 mm，但 root 发现未修改的 width/height/center 来源证据被改写，已退回负责人修复，连续对话整体尚未验收。
- root 读取 MCP 孔汇总发现仍只接收 verified_open/verified_blind，与新合法沉孔及保留壁段状态不一致，已要求接口负责人对齐并补正反例。新版 source-only 识别及部分预览接口已加入，仍需实际模型调用和返修证据。
- 新完整矩阵 `feature-execution-matrix-20261001-134224.json` 已读取，共27类：21项记录为BREP passed、曲轴/叶轮/活塞/转子4项build_failed、涡旋盘timeout_unknown、shell unavailable/needs_review。涡旋盘原进程实际运行约243秒后退出1，PID已不存在；剩余类型通过独立续跑完成。汇总中的自交unknown清单与部分原始检查字段仍需核对，不能将BREP passed等同全面可靠或精确映射。
- 来源保护进一步复核发现 changed 参数恢复旧来源、无图纯文字误标image_estimate的风险，已改为拒绝无法核实的user声明；未改来源默认保留与显式可信来源纠正仍在完善。正式CLI将使用同一逻辑会话继续局部编辑与追问，并核实际历史注入，不以mock替代验收。
- root 读取真实原图独立识别 turn `e1ab86d936dd4f08964b78083a437f4f`：exit0/conversation，最终回复记录9个正面圆形开口及槽内后壁，未建模。原始工具观察、相对孔径关系和request预算仍需核对；此结果只证明观察调用，不证明图像复刻或视觉验收。
- root 读取真实120mm编辑 turn `c402f65775d84b2a8c85b3f420d10e56`：exit0/accepted，120×60×20 mm；宽高中心来源与此前110mm结果一致。此前110mm已经改坏的历史来源没有被悄悄修复；程序在该请求过程中升级了严格turn_id校验，因此该轮不能证明新校验行为。后续纯问答 turn `30600df009a64390bf166fb3173fadfd` exit0/conversation，准确复述上一轮要求并读取当前CAD，未修改；真实可见对话归档5轮。
- 原图source-only请求的实际原始观察已归档并由root读取：原图SHA一致、单图、608 prompt / 1759 completion / 912 reasoning、46.404秒、profile low、开启thinking但未发thinking_budget、输出上限1800。观察确认九开口，但未记同组相对孔径或平面关系；不能宣称看图质量/思考效率已解决。总费用统计仍须包括独立Python请求。
- 最新真实web CLI虽然调用了search/open，却返回不相关页面并只读取65字Bing跳转内容，不构成外部参数证据。原结果保留，改后搜索相关性/跳转处理和网页正文需再验，几何通过不能替代用户要求的外源事实核实。
- 全新独立 `cold_cli_fresh` 以forknone/Luna medium启动；只读新CLI说明、提示词和用户原图，用generic请求及唯一session `cold-fresh-20261001-d78fc15f`。PID53732实际活跃，原图独立识别工具已调用；尚无终态，不依据旧候选宣称通过。
- 使用额度中断后，root实查ordinaryUsageAllowed=true，恢复原Luna/medium工作线。cold原进程已自然结束，turn `f77cb0e084214297898ed5ea30bb5cda` exit4/candidate_not_verified，1899.6秒，111 tools、60 logical/63 wire、45 repairs、12 previews、344累计图像输入，末次请求12图；累计input5,401,650/output37,649/reasoning22,646，缓存明细5,243,520。root实际查看iso，模型仍是错误侧面孔的U结构，没有达成参考图复刻。
- root核验正式geometry-report：BREP valid/closed/单实体/自交clear，但圆柱切孔全部使用kind=cylinder，hole_instances空、hole_measurement_complete却true；已要求测孔语义不能由kind或改名绕过。原图对模型独立pair为0；2次review只是主Agent填visual_reviews自评match，未执行独立多模态比较。finish保留candidate正确，但最终声称参考匹配没有证据，须修复审查工具、计数及结果说明。
- 时间线明确外层MCP60秒早于内部source call150秒，旧请求未结束时Agent重试并排队；两次独立识图实际150秒失败，随后preview因无CADPlan失败。下一步是deadline契约、取消/去重、识图失败后透明候选、上下文图像清理及语义守卫，不能原样重复盲测或只追加提示词。
- 独立验收报告也曾将侧孔布局误判为总体相符；root实际查看front预览确认两侧前面无孔，仅中部3孔，要求验收Agent按原图与预览的面法线重新核对报告。9个开口总数不能替代正确面上的孔位证据。
- 几何负责人新增通用圆柱切除意图测孔与连杆解析近似构造，最新SDK/BREP46项仍有1个失败：通用重复孔guard误抢同轴pattern的no_op处理。已按几何delta保留pattern无效果诊断，等待新全量回归；不能沿用旧39/42项通过宣称最新全部通过。

## 执行记录（辅助结果不代表软件验收完成）

- `loop_review` 第一批工具回归：`tests/test_dsh_review_workflow.py`，8 passed，证据 `artifacts/agent-quality-rebuild/loop-review/tests-dsh-first-batch-rerun.txt`。测试包括取消逐特征视觉门禁、未复核候选保存、评分只辅助、最新 partial 不被旧 match 遮盖、不适配视角、阵列与盲孔、原图 transport hash。此处使用工具级测试，不是真实视觉模型验收。
- 原增量运行时套件初次运行 54 passed / 5 failed，新旧几何报告兼容问题正在处理；不得忽略失败或把未核验改成通过。
- OCCT 主体、退台、九孔辅助核验已生成有效闭合单实体及九个解析孔径测量，追加回转和自交轮廓拒绝也有记录。主 Agent 读报告发现 5 mm pocket 实际切深 5.5 mm，要求修复并增加尺寸/体积断言后重跑；原辅助报告不能作为该尺寸要求通过证据。
- 图片资产/profile smoke 已运行；DSH 首次初始化被未知模型能力声明拒绝。输入 Agent 正在核实精确 route 的能力及框架必需的运行设置，再重跑启动。
- 真实桌面窗口控制已确认可枚举应用；真实内置 Agent 图片/文字建模验收尚未开始。

### 后续核对

- 使用额度中断后，已核对当前账户 ordinaryUsageAllowed=true，恢复同一批 gpt-6-luna max Agent，没有换模型或接手实现。
- 凹槽尺寸辅助核验修复后重跑：depth=5 mm；去除体积约13500 mm³；主体加九孔最终体积263000.8871935025 mm³，与解析公式在epsilon误差内一致。旧“5.5 mm但passed”结果不再作为证据。
- `tests/test_occt_brep_backend.py` 新增9项通过，原SDK套件仍有6 failures、2 errors，需要分别修复实际阵列错误和更新确实改变的报告语义；原失败日志保留于 geometry-backend/unittest-brep-sdk.txt。
- 图片manifest/profile与DSH初始化smoke已落盘 input-reasoning/implementation-smoke.json。初始化 none/low 均通过（mock凭据，无模型请求）；原图复制哈希一致，纯文字追问资产不变。此结果不替代真实供应商请求和UI验收。
- 首轮真实UI锁分配给 input_reasoning：初始化后先跑自然语言100×60×20 mm长方体，保存窗口截图及真实调用记录；通过后再排原图复刻。当前未收到真实软件建模通过证据。
- 首轮GUI请求发出后，Computer Use收到物理Esc停止；随后用户选择改用CLI，UI操作停止。input_reasoning负责CLI入口与真实文本/图片Agent验收，其他Agent继续回归与接口修复。旧窗口请求不再作为当前活任务或完成证据。
- loop工具回归后扩到13 passed；增量运行时59 passed +2 subtests；native BREP套件扩到12项通过，CADPlan SDK16项通过。当前仍需CLI真实Agent、原图、连续对话及返修证据，不因辅助套件通过而完成目标。
- 正式 Agent CLI 已加入 `agent` / `--agent` 分发、会话锁、每轮交付归档和独立退出码，负责人报告 CLI 套件 9 passed。首条真实文字请求使用隔离会话 `aq-text-box-20261001`，用户输入为“创建一个100×60×20毫米长方体”。当前权威结果为 `state=error`、exit 3、无模型：4 次实际供应商请求均在约 5 秒后发生传输 TypeError。失败及 wire 记录保留在 `input-reasoning/cli-text-box/turns/5035c0d036c1455d852adc38e80a0e2d/`；不能算真实建模通过。
- 上述实际 wire 请求是 Kimi-K2.7-Code 的 `/v1/chat/completions`，`enable_thinking=true`、`thinking_budget=2048`、未观察到 `max_tokens`。传出预算不证明供应商硬性执行；需核实档位来源、真实用量与耗时。接入负责人正在定位传输原因和请求参数接线。
- 当前 27 类几何执行矩阵已完成：19 项成功执行或显式跳过、8 项失败（socket_head_bolt、spoked_wheel、vbelt_pulley、crankshaft、impeller、piston、scroll_disc、rotor）。shell 是显式不可执行项。这个合并统计不代表 19 项全部通过几何验收；近似、自交未检查及多实体仍须按各项核验报告判断。源网格边界诊断与 BREP 缝合/Fuse 失败已分开保存，继续修复常用配方及通用转译问题。
- 正式 CLI 新文本会话 `aq-text-diagnostic-20261001` 已有真实通过证据：turn `9aeb31f903f94672b6d4cf0d50f0b10d`，exit 0、5 次 MCP 工具、6 次供应商 HTTP 200，OCCT 单实体 100×60×20 mm / 120000 mm³。provider response body 汇总 input=43187、output=1338、total=44525、reasoning=528；首工具约11.2秒。旧连接失败未在此轮重现，根因仍未知，不能将一次可通视为已修。此场景仅证明这一条文字建模，不替代图像与复核验收。
- 独立 medium 冷启动验收只收到原图、新 CLI/提示词说明及“按参考图复刻，尺寸未标注请自行估量”，没有历史尺寸或 CADPlan。会话 `cold-acceptance-mid-20261001-7f3c91d2` 首工具约67.6秒；当前保存轨迹显示 rev23、无成功预览，多次同一 right_hole_top 切除无材料变化。左右孔同为 X 轴、相同 Y/Z、贯穿，仅改变沿轴点坐标或 X/-X 不会产生新孔；几何签名变化未识别这种物理等价，程序也未提供可见的有效部分预览。这是明确未完成项，需通用重复孔诊断、几何等价进展检测及失败部分预览。当前进程句柄正在由验收 Agent 核对最终状态，不根据旧 state 文件宣称仍活着。
- 2026-10-01 loop_review 接续回归：`python -m unittest tests.test_agent_runtime_incremental tests.test_dsh_review_workflow -v`，77 passed（runtime 60、workflow 17，约39秒）；完整日志为 `artifacts/agent-quality-rebuild/loop-review/runtime-workflow-77tests-20261001.txt`。先单独重跑发现 false-positive 原图重检用例遗漏了两次状态持久化；在测试中按真实 MCP handler 的保存边界补上 `_save` 后，该用例及合并回归通过。此结果是实现/工具链单测，未验证真实模型视觉纠错或 CLI 建模。

### Loop/MCP follow-up — 2026-10-01

The connected online/MCP/runtime/SDK regression command `python -m unittest tests.test_online_search tests.test_dsh_review_workflow tests.test_agent_runtime_incremental tests.test_cadplan_sdk -v` passed 106 tests in 29.646 seconds. Full transcript: `artifacts/agent-quality-rebuild/loop-review/full-regressions-source-web-partial-20261001.txt`. It includes mocked online fallback/parser diagnostics and new tool-level cases; this is not live provider/search acceptance.

MCP now has source-only `cad_inspect_references` bound to original image SHA/optional ROI. Its output is stored as an unverified and revisable model observation, with usage and request profile. Geometry failure images are returned as partial diagnostics while keeping failure status; partials do not enter accepted/best state. A validated applied prefix may be exported with the requested full plan and unapplied features retained in its report. Hole coverage understands active counterbore/retained-segment statuses and superseded history. Repeated same-feature geometry failure allows a first repair attempt, then requests feature-related ROI reinspection or partial visual diagnosis; text-only sessions are exempt.

Source provenance updates preserve untouched parameter sources. A changed value cannot claim `user` provenance without an exact trusted user-turn quote; source-only corrections require the explicit `provenance_corrections` field and are action-logged. Live CLI validation of the source-only VLM request and ordinary search-plus-page-open remains pending, as does a separate full cold-start image rebuild.

### Live source/web observations — 2026-10-01

A clean isolated CLI source-only request verified `cad_inspect_references` over a real image: the program passed one original image with exact matching transport SHA and saved structured, unverified source inventory. Full raw content and provider wire/usage are archived in `artifacts/agent-quality-rebuild/loop-review/cli-source-inventory-20261001/turns/e1ab86d936dd4f08964b78083a437f4f/source-inventory-evidence/`. This first prompt version identified 3+3+3 visible openings but omitted relative sizes and wall-plane relationships; new generic schema wording remains to be verified live. Provider completion usage was 1,759 against max_tokens 1,800 with 912 reasoning tokens over 46.4 seconds; no finish_reason was captured, while successful JSON extraction establishes only syntactic completeness.

The real online CLI call `aq-web-open-20261001` demonstrated a substantive retrieval problem: Bing parsing returned irrelevant ChatGPT/知乎/Translator entries for an AA6061-T6 density query. The tool then opened a Bing tracking redirect whose extracted body was 65 characters. The Agent still finished the box geometry as accepted without density support. Treat web facts as unverified for that run. Redirect decoding/relevance filtering are in the current checkout, but need post-cold-run tests and live CLI verification. An explicitly required web fact must be retained as unresolved when the search is irrelevant or page extraction yields no usable content.
