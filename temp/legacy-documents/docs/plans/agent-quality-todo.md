# Agent 质量重构 TODO

更新日期：2026-10-02。此文件是当前状态入口；历史与完整 P0–P2 范围见 [agent-quality-rebuild.md](agent-quality-rebuild.md)。

版本基线更新（2026-10-02）：目标 DSH npm runtime 为 **0.2.0-rc.2**。安装脚本将该 CLI 安装到 `%LOCALAPPDATA%/AI Modeler/dsh-cli`，应用通过公开 `DeepSeekHarness(dsh_bin=...dsh.cmd)` 使用；本机 `dsh --version` 实测输出 `0.2.0-rc.2`。PyPI 尚无配套 Python SDK/runtime-bin 0.2.0rc2 wheel，因此 Python JSON-RPC adapter 暂留 `deepseek-harness-sdk==0.1.5rc1`，没有冒充升级。公开启动路径已完成 RC2 initialize 和25项原生 Cordis 工具注册 smoke。真实 Agent CLI 也已调用在线模型并传入原图，但没有产出预览或候选：Agent重复11次无几何变化更新后被停止，故 DSH/SDK 组合的建模行为验收**未通过**，详见本节报告。

## 2026-10-02 DSH 原生插件迁移（进行中）

用户已明确调整架构方向：**DSH Agent 路径移除 MCP 本身**，不是只减少 MCP 转译层。目标是在 DSH profile 中用原生 Cordis 插件注册 AI Modeler 工具与 Agent hooks，DSH 继续负责模型调用和 Agent loop；Python 几何/CADPlan 后端改走应用内部接口。独立快速模式与 CATIA MCP 通道不在本次 Agent 路径迁移范围内。

- [x] 原生 Cordis 插件加载并直接注册 AI Modeler 工具；Agent 路径不加载 MCP client/server/protocol，也没有 MCP fallback。
- [x] CADPlan、知识库、建模、预览、图像证据、复核和保存操作通过私有 JSONL/stdin 通道调用协议中立 backend；预览/参考图作为 DSH 原生 attachment content 返回。
- [x] 针对 DSH `0.2.0-rc.2` 完成公开 `dsh_bin` 无模型 initialize / 25 项工具注册 smoke 和本地图像 attachment smoke；明确记录 Python SDK adapter 版本组合。
- [ ] 由独立 Agent CLI 使用原始参考图进行新冷启动验收。已实际尝试一次：Provider 收到原图，native Cordis 注册25项工具，但Agent在revision 1对同一 `base_block` 连续11次更新均未改变几何签名；没有预览或候选，约3分59秒后停止。此run失败，不能算建模验收通过。
- [x] 为重复 `cad_update_feature` 增加同几何签名与特征 ID 的无进展熔断：前两次记录 no-op，第三次记录 stop reason 并拒绝该重复更新；其他特征仍可新增，签名变化后允许再次编辑。`tests.test_dsh_review_workflow` 59项通过；和知识库测试合跑为72 passed、12 subtests passed。该修复尚未经过新的真实在线 Agent 冷启动。

进度：已把 DSH npm CLI 0.2.0-rc.2 安装到当前机器的 `%LOCALAPPDATA%/AI Modeler/dsh-cli`；本次复查版本仍输出 `0.2.0-rc.2`。应用实际 `_runtime_for()` 公开 `dsh_bin` 路径完成无模型 initialize，session overlay 注册25个 Cordis 工具。图像 smoke 以原始 PNG bytes 检查了 `ctx.attachments.saveImages` 和原生 image content。Python SDK adapter 保留 0.1.5rc1，原因是 PyPI 尚无 RC2 wheel；RC2 CLI initialize、注册和附件投影均完成无模型验证。迁移时暂时漏接的知识库视觉样例板已恢复为单次输入附件，原图仍通过引用工具按需读取；生成的临时样例板文件在编码进请求后立即清理。当前 `dsh_mcp_server.py` 已删除，旧 Agent workflow 测试改为直接覆盖 `dsh_tool_domain.py`；快速模式与 CATIA MCP 路径未改。

- 首次 RC2 冷启动在 provider wire 前失败：自定义附件服务仍按旧预算形状 `{maxPixels,maxBytes}` 读取，而 RC2 的 `ImageRequestTarget` 是 `{width,height,maxBytes}`。已修正 `dsh_attachment_store.mjs`，并以原图 494×544 / 72,309 bytes 验证 `saveImages -> readImageRequest` 成功；旧 pixel-budget 调用形状仍兼容。失败 run 见 [`rc2-cold-20261002-015634-6d08f70e`](../../artifacts/agent-quality-rebuild/rc2-cold-20261002-015634-6d08f70e/report.md)。修复后唯一一次真实 CLI 冷启动触达 SiliconFlow/Kimi，并使用原图；但Agent连续11次无进展更新同一 `base_block` 后被停止，没有preview/候选/几何核验。工具参数未归档，无法确认11次参数是否完全相同。报告 [`rc2-cold-20261002-020244-21fd2698`](../../artifacts/agent-quality-rebuild/rc2-cold-20261002-020244-21fd2698/report.md)，SHA-256 `9361905D12BE2F57A9D5F063FAED9544B7B8F5AFDE6B0045BA0EB2F96B2626F9`。程序级同几何签名空转熔断正在补齐；在其测试完成前，不继续同场景重跑。

定向回归记录：恢复知识库视觉样例板并增加无进展熔断后，`python -m pytest tests/test_dsh_review_workflow.py tests/test_knowledge_retrieval.py -q` 结果为 **72 passed, 12 subtests passed**。此前唯一失败是“本地视觉样例板条目”未进入 DSH 输入；现已修复，并调整测试验证原图按需读取、知识样例只发送一张临时图板。

历史说明：本文件下方仍保留 2026-10-01 的调查与测试记录。那些记录里的 `dsh_mcp_server.py` 路径、行号和 MCP 调用数字都是迁移前快照，模块已于 2026-10-02 删除；当前 CAD/几何业务入口位于 `ai_modeler/agent/dsh_tool_domain.py`，不应将历史路径当作当前代码入口。

## 总体状态

**尚未完成。文字基础建模已跑通；原图复刻、独立多模态纠错与可靠收敛还没有通过。**

冷启动图像复刻已因无有效候选而停止：Luna / medium 子 Agent 于约20:40按用户纠正只停止本run的CLI PID 23464、DSH runtime PID 41204 和MCP PID 27200；三者已不在进程表，未重启。它并非启动卡住：最终封存日志为39条provider wire / 38个DSH请求完成 / 53次MCP工具调用；116,707 input 与21,620 output tokens，39条provider请求全部携带原始图，图像parts分布为1图8次、2图15次、3图4次、4图9次、5图3次，合计101 parts。最终revision 32 / 6 features、`finished=false`、无output paths；revision 29仅有未接受的partial BREP（100×50×80 mm主体、凹槽、3个X轴横向通孔），blind feature `back_blind_1` 的 `cut_no_material_change` 重复4次。CLI在未完成的reasoning请求中被停止，没有AIMODEL/STL/可用PNG或最终报告；`total_tokens`原字段事件和为1,634,007，与input+output不一致，不作为可信总token数。完整报告 [`report.md`](../artifacts/cold-image-repro/cold-nine-hole-20261001-202252-b8032d41/report.md)，SHA-256 `87F7C550EE280FA3F905B690977826ACCA1908DE3BEB7E33828F671E4B7F9F96`。此轮不能算冷启动复刻成功或视觉审查通过。

修复状态：image-replay修复通过Python 61/61、Node 5/5、MCP stdio smoke（24 tools）；stall/partial-candidate修复通过4个定向测试及 `git diff --check`。这些是局部回归，不代表四项目标完成。真实 CLI run `coldimg20261001mid2`（turn `327db86d02094e299596a6608458c3e3`）生成了本地几何通过的候选，但终态为 `candidate_not_verified` / exit 4。BRep 是有效闭合单实体，9个孔核验为 `verified_open`；视觉独立复核认定候选凹腔过宽，九孔全在同一凹腔底面，未保留原图三条前后不同的面带布局和大/小孔径层级。18个主 Agent Kimi wire均为0图；另有3个 Python vision/source-inventory wire，各1/2/2图，共5个图像part。首个单图 source-inventory wire（原图 SHA 与reference一致）返回HTTP 200，但会话 `cad-state.json` 中 `reference_inventory` 的 `summary`、`visible_evidence`、`uncertain`、`inferred` 均为空，状态 `model_observation_unverified`；主Agent仍继续建模并复用该空缓存。结果、日志和独立复核分别见 [turn目录](../artifacts/agent-quality-rebuild/cold-acceptance-20261001-mid2/turns/327db86d02094e299596a6608458c3e3/)、[独立视觉复核](../artifacts/agent-quality-rebuild/cold-acceptance-20261001-mid2-independent-review.md)。

Inventory门禁补丁已落在 `ai_modeler/agent/dsh_mcp_server.py`，只信任当前 reference ID/SHA、全图ROI key、`observation_valid=true` 与非空可见事实；类别unknown / `no_match` 不参与该门禁，ROI观察有独立缓存键，raw响应值不进schema诊断。新增6项inventory专项测试通过；`tests.test_dsh_review_workflow` 共49/49通过。随后真实冷图 CLI `coldimg20261001inventoryfix` 的原图观察、SHA与建模准入均通过，但最终未得到可用候选即被监督端停止：36 provider requests / 35完整响应 / 1个中断在途请求，1个source图像请求；停机revision 14，无有效preview、无独立review pair、无AIMODEL/STL/最终结果。CLI progress counter 到`+717s`，这是相对事件进度而非已验证墙钟总耗时。停机state、调用审计、输入清单和报告见 [本次run归档](../../artifacts/agent-quality-rebuild/cold-acceptance-20261001-inventory-fix/run-report.md)。以上本地测试和inventory准入证据不代表候选模型验收通过。

计时审计待补：先前将该 turn 从 `turn_start` 到最后一条 wire 事件算作约 `568.4秒`，现降级为**未核证旧估值**。日志核对发现 Python 侧时间戳以秒记录，Node bridge 的部分时间戳以毫秒记录；当前 `turn_start` 与 wire event 不能直接混算。此前列出的 per-call 时长（含 iso/front 复核、source inventory、主 Agent wire）同样未核证，现撤下，不用于效率结论或前后对比。待按事件来源识别时间单位、统一转换到同一基准，并依据一致的请求开始/结束事件重算每次请求与整轮 elapsed。本次未查阅供应商官方文档；`enable_thinking`、`thinking_budget`、`reasoning_effort` 的供应商语义与限制待官方文档审计。

用量与请求字段事实：21个 provider response body 合计 input 437,441、output 13,808、reasoning 9,564 tokens；各字段分别累加，**不引用或采信矛盾的 `total_tokens`**。18次主 Agent wire 全部0图；按日志顺序，input 从首条请求的6,708增至末条的73,414 tokens（约10.9倍）。请求记录含 `enable_thinking=true`；主 Agent 请求带 `thinking_budget=2048`，单图 inventory 带1024，两条 pair review 带2048；run profile记录low。这些是 wire/profile 中的实际字段，不证明供应商如何解释、执行或限制这些字段。五次 `cad_inspect_references` 都发生在首个 `cad_create_model` 前，后两次显式 `reused_cached_inventory=true`；工具响应正文/长度和 schemas 的 token 分项没有保存在日志中，所以 input 增长已证实，但不能仅凭本日志把增长归因给某一 schema 或某次工具返回。复核源码指出 inventory 读取顶层字段并以空值 fallback 写回；ROI 查询只按 reference id+source SHA 复用缓存，不区分 ROI，见 [`dsh_mcp_server.py`](../../ai_modeler/agent/dsh_mcp_server.py#L2714) 对应审查逻辑。`cad-state.json` 中首条 iso review 的 pair note 认为可比较且有5条差异证据，但整体 `view_comparable=false` 令证据被丢弃；原始5条内容未保存，不能重建或分类。provider 响应正文未归档，因此不能认定“同一 provider 响应”是空观察的根因。低图像 parts 数不等于推理短或有效；当前只有一轮 usage 记录，尚无修复前后同场景对照、每次长思考是否推动几何进展的归因，也没有真实Agent触发新熔断的证据。更早的 run 已被人工停止，不算自动熔断证据；其旧有约17分钟时长也未复核。

### P0–P2 四项问题验收矩阵

负责人沿用当前分工：`loop_max`（施工、审查、返修、候选账本），`input_max`（输入资产、上下文、推理与进度），`geometry_max`（CADPlan/OCCT转译与核验）；root负责协调验收。`[x]` 只表示本行写明的有限场景已有证据，不代表整个问题关闭。

| 核心问题 | 对应范围 / 负责人 | 当前证据与状态 | 下一项验收 |
|---|---|---|---|
| 看图差 | P0 `I01/L02/V02`，P1 `L05/I02`；input_max + loop_max + 独立复核 | **未通过。** `coldimg20261001mid2` 的 image-only inventory 字段全空，Agent仍继续建模；独立复核指出凹腔宽度、孔所在面带、孔径层级差异，程序复核却报 `not_comparable` / `unverified`。后续 `coldimg20261001inventoryfix` 的全图inventory非空且SHA匹配，建模准入通过；但特征在revision 3–14间多次删除/重建，revision 10 iso preview报 `geometry_build_failed`，报告未保留失败特征与底层错误，revision 14仍无有效preview、partial candidate或独立review pair。因此该run只验证inventory准入，不验证图像到几何正确性，也不能据现存证据归因几何后端。 | 先修复几何失败诊断、特征安全与最后有效前缀/partial candidate保留，再用不带尺寸/CADPlan的新冷启动复刻；保存成功预览和可比较的原图—候选pair，逐条记录源图事实与差异，让Agent按finding修正并用新图关闭差异。 |
| 思考长且无用 | P1 `I03/L06`，P2 `I04/I05`；input_max + loop_max | **效率目标未通过。** 真实 usage aggregate 为 input 437,441 / output 13,808 / reasoning 9,564 tokens；18条主wire全0图，另3次vision请求共5个图像part。整轮 elapsed 未核证；旧估值568.4秒及逐请求时长因 Python 秒 / Node 毫秒时间戳混用而撤下，须统一时间基准后重算。记录了请求字段与 progress timeline，但尚不能说明哪些长推理有用、重复上下文从何处增长或修复是否降低延迟；供应商官方文档语义待审。新增提示词复验 `inputguard-20261001-8d47e1`：首次整图 inventory 有效且同 key 未重复，约 76.2s 返回；模型到首次几何动作约 90.8s，随后连续4次 no-op，未生成有效 preview 或候选，最终 exit code 1。只验证 inventory 调用纪律改善，不验证模型完成、图像到几何正确性或效率目标达成；详见 [run report](../../artifacts/agent-quality-rebuild/input-reasoning/cli-image-inventory-guard-20261001-8d47e1/run-report.md)。 | 只读分解逐次input增长、每次 `cad_inspect_references` 目的/返回量与后续动作；先按事件来源统一时间单位，再重算逐次与整轮elapsed，形成可重复的同场景前后比较。用真实无进展/往返修改情景证明保存最佳候选并自动停止；避免以固定低调用数取代有效性。 |
| 越审越错 | P0 `L01/L02/L03`，P1 `L05/L06`；loop_max | **未通过。** 当前Agent审查把独立复核能比较且能指出的显著差异归为不可比，`open_multiview_findings=0`，没有可追踪问题账本或真实返修闭环。候选有保存为未验收状态的证据，但未证明低分死锁、finding保留、无进展回退门禁完整工作。 | 用这份现成错误候选做差异驱动返修：只把具体可见差异交给施工，保留原图/旧候选/问题ID，验证新版图关闭原finding；另以不兼容视角、低分、无进展和往返修改验证不会误判或无限循环。 |
| 模型转译不可靠 | P0 `L04`，P1 `G01–G06`；geometry_max | **真实案例失败。** 本轮BREP拓扑有效且9个孔均被测通，但几何是大面积宽凹腔和同一凹腔底面九等径孔；原图是窄中央凹面、两侧凸面上的分带孔和孔径层级。有效实体/through检查不能替代从图像到CAD语义的正确转译。 | 先做只读转译审查（`revolve segments=0`、槽面/孔轴、贯穿结果、参数解析通过与几何成功分离），再由geometry_max针对具体缺陷改修；以真实文本与图像Agent场景检查参数、拓扑、面归属和可见图差分。 |

#### P0–P2 全目标逐项跟踪

| ID | 优先级 | 目标 / 负责人 | 状态与证据边界 |
|---|---:|---|---|
| L01 | P0 | 取消逐特征强制视觉审查；区分未施工和建错 / loop_max | [ ] 有工具级回归记录；缺真实Agent连续施工证明未完成特征不会触发错误返修。 |
| L02 | P0 | 具体差异驱动返修，评分只辅助 / loop_max | [ ] 本轮独立报告指出具体可见差异；程序却报not_comparable且无finding。缺Agent按finding改动并以新版证据关闭。 |
| L03 | P0 | 解除全配对低分死锁并保留未验收候选 / loop_max | [ ] `candidate_not_verified`候选已保存是有限证据；`open_multiview_findings=0`，且无低分/歧义/未解决finding落账证明。 |
| L04 | P0 | 可见孔实例与全局特征条目分开 / loop_max + geometry_max | [ ] 本轮9孔计数通过但九孔全在一个面；缺多面/阵列孔按面归属的真实验收。 |
| I01 | P0 | 会话原图、来源、哈希持续保留 / input_max | [ ] 早前同会话文字续话证明manifest保留原图SHA；本轮也核对出SHA匹配，但source inventory为空且文本后按需取原图复核未通过。 |
| L05 | P1 | 稳定原图事实、冲突核查、不可观测性、差异账本 / loop_max | [ ] 本轮标注`model_observation_unverified`后仍继续建模；没有证明空观察会阻断/降级，或冲突会触发重新看图而非猜测改拓扑。 |
| L06 | P1 | 最好候选、进展/往返检测、回退 / loop_max | [ ] 同几何签名/特征的第三次 no-op `cad_update_feature` 现有代码级熔断与单测；RC2真实run中模型11次更新无变化，但停止由监督端执行，未看到补丁在LLM循环里触发。保留最好有效候选与端到端证据仍缺。 |
| L07 | P1 | 保留按需web_search/来源读取及离线错误恢复 / loop_max | [ ] 既有CLI曾返回无关结果和跳转页短正文；没有真实Agent读取相关可用来源的通过证据。 |
| I02 | P1 | 单原图、ROI查看、按需知识示例且无无关图污染 / input_max + loop_max | [ ] 本轮原图进了独立vision请求；未证明ROI调用、知识示例目标明确及无无关图污染。 |
| I03 | P1 | 主建模和审查的provider能力、推理预算、档位一致 / input_max + loop_max | [ ] 本轮 wire 记录了`low`与2048等请求字段，aggregate reasoning为9,564；供应商官方文档语义待审，不能从字段推导硬总预算。缺效率/有用性对照，elapsed 与逐请求时长待统一时间基准后重算。 |
| G01 | P1 | CADPlan执行真实OCCT BREP / geometry_max | [x] **限当前候选**：真实Agent run产出有效闭合单实体及报告；这只证明执行后端接通，不证明参考图转译正确。 |
| G02 | P1 | 当前27类特征映射，禁止静默跳过/冒充精确 / geometry_max | [ ] 既有表记载10 exact、16 approximate、1 unavailable，且若干复杂特征/自交检查仍需核验；未以逐类型有效Agent执行完成全范围验收。 |
| G03 | P1 | 单位、坐标、axis/base/center、关系与布尔语义 / geometry_max | [ ] 明确尺寸文本场景有真实通过证据；本轮图像场景几何位置/面带与图像不符，不能扩称图像坐标关系可靠。 |
| G04 | P1 | 实体有效性、组件、体积、孔实例、贯穿检查 / geometry_max | [x] **限当前候选**：单实体有效闭合，记录体积与9个`verified_open`孔；并不证明孔面分布吻合参考图或所有特征受支持。 |
| G05 | P1 | 自交轮廓、回转内壁、追加回转和曲面离散风险 / geometry_max | [ ] 有局部案例/矩阵证据但未完成对应合法/非法案例与真实Agent结果的全范围核对。 |
| G06 | P1 | 本地几何、视觉验收、CATIA结果互相独立 / geometry_max + loop_max | [x] **限当前run报告**明确几何passed、视觉未验证、CATIA未运行；绝不把BREP结果算作视觉或CATPart通过。 |
| I04 | P2 | 脱敏真实wire、耗时、图片、重试、usage可观测 / input_max | [x] **限当前run**：21/21 wire有provider response usage；按input/output/reasoning分别汇总。矛盾`total_tokens`不采信，也不由本例证明所有usage字段永远一致。 |
| I05 | P2 | 动作、等待、工具、思考摘要进度 / input_max | [ ] 本轮有18个MCP工具和stream/progress timeline；仍需按当前CLI验收通道确认长请求期间用户能持续看到有效阶段进展。 |
| V01 | 全部 | 快速模式、Agent模式、连续对话兼容 / 全体 | [ ] 有限文本Agent与续话场景通过；本轮图像失败，快速模式/全部模式兼容尚未同一冻结版本完成验收。 |
| V02 | 全部 | 无历史冷启动，只给原图、提示/接口说明 / 独立验收 | [ ] `coldimg20261001mid2`为真实冷启动但视觉结构错误；本轮验收失败，须修复后重跑，不能把候选存在当通过。 |

#### 场景证据覆盖

| 场景 | 已有证据 | 仍缺的证据 |
|---|---|---|
| 原图建模 | 本轮真实原图SHA、1次source inventory请求、iso/front候选与独立图像复核都已归档。 | inventory结果为空却继续建模；当前候选与源图凹面/面带/孔径关系不符。 |
| 纯文字建模 | 独立正式Agent CLI已有100×60×20 mm长方体真实通过；文本特征、单位与几何检查有有限证据。 | 更多特征语义、关系来源和失败恢复仍须按矩阵验收。 |
| 持续对话 | 既有同会话文字追问、局部尺寸修改和原图manifest保留证据；本轮冷启动没有额外续话。 | 文本follow-up后按需取回原图，生成新image-reference pair并真实关闭视觉finding仍未通过；不能把纯文本无图请求说成看过原图。 |
| 翻译几何差分 | 本轮有效BREP/孔贯通与明显图像几何差异同时存在，证明“几何检查passed”不等于“翻译正确”。 | 特征解析成功到实际几何的差分、轴向/面归属/槽拓扑、回转分段与贯穿等需逐项只读审查后修复验收。 |
| 资源与效率 | 21个 response body 的 usage aggregate 可保留；18次主 wire 全0图，3次 vision 合计5图。整轮 elapsed 和逐请求时长未核证；旧估值568.4秒已撤作计时结论，须统一 Python 秒 / Node 毫秒基准后重算。 | input增长、长思考效用、重复 `cad_inspect_references` 五次的用途/返回体积/缓存重用原因尚待审计；缺同场景优化前后对照。供应商官方文档尚未检查，待审 `enable_thinking` / `thinking_budget` / `reasoning_effort` 的语义。 |
| 无进展/往返熔断 | 旧run已停止且无进程，保留了失败记录；本轮没有长时间建模返修循环。 | 旧run为人工停止；没有自动熔断能停止无进展/循环、保留最佳候选、同时不误停有进展任务的端到端证据。 |

首次集成CLI尝试曾因子Agent误把“子Agent用Luna/medium”套用到被测应用provider、并将主wire的0 image count误判为失败，于16秒内自行中断；无建模结果、未长占provider。现已澄清：应用继续用项目当前provider配置，主DSH wire应为0 image parts，source inventory独立vision wire应恰好1张原图。新验收由子Agent按session `agent-observability.jsonl`与CAD state观察，不看stdout空文件推断卡死；同一语义blocker达到停滞门禁仍循环，或provider事件连续5分钟无进展时，仅停止该run并保留证据，不设全局小工具调用上限。

状态定义：`[x]` 仅表示该条写明的范围有证据；`[ ]` 表示仍需完成或验收。单测通过不能替代真实 Agent 验收。

## 1. 已完成的有限范围

- [x] 正式 DSH Agent CLI 入口，可输入文字/图片、保存每轮结果；不是旧的无 LLM 快速解析器。
- [x] 真实 Agent 能从文字建立 100×60×20 mm 长方体，并在后续问答中读回当前模型。
- [x] 真实 Agent 能调用增量工具添加特征：最新文字场景实际创建主体、圆柱通孔、圆柱盲孔、回转凸台，并生成本地预览与候选文件。
- [x] 接入本地 OCCT BREP 执行与实体核验；SDK/BREP 最近一次辅助回归 53 项通过。证据：`artifacts/agent-quality-rebuild/geometry-backend/final-sdk-brep-rerun-20261001-1745.txt`。
- [x] 完成当前 27 类特征的一次辅助执行矩阵：24 项 BREP 检查通过，3 项需进一步核验/不支持；17 项明确为近似映射。不能解读为 27 类全部精确支持。
- [x] 独立原图冷启动已实际运行并归档失败结果，未以人工建模替代 Agent 输出。
- [x] SubAgent 已按用户最新要求切换为 GPT-6 Luna / max；验收采用 CLI，不做桌面操作。

## 2. 正在修复：当前优先队列

- [ ] **有效观察仍被首个建模门禁阻断（unknown_shape_gate，P0）**
  - **截图报告**：用户截图中，Agent 称 `cad_inspect_references` 多次返回 `observation_valid=true` 且 SHA 匹配，列出了 `top_face` 孔位与 `side_surface` 主体等可见结构；随后 `cad_create_model` 仍反复报告前置校验失败。截图状态为 CADPlan empty / revision 0、知识检索 `no_match`，并称识图输出了一个类别结论。以上是截图内容转述，不等于独立验证了原图结构或识图结论。
  - **截图运行对应关系**：`截图对应session未定位`。本机真实 DSH 日志候选 `coldimg20261001mid` 确有 empty / revision 0 / `no_match`，但其 `agent-observability.jsonl` 只有 15 条 provider/stream 事件、没有 tool 事件，也没有可核对的 `observation_valid` 或来源 SHA 记录；不能证明它就是截图运行。候选 `coldimg20261001mid2` 记录了 5 次 `cad_inspect_references`、1 次 `cad_create_model` 和 `no_match`，但最终已有 CADPlan revision 3，与截图状态不符。日志只提取了这些白名单状态与工具字段，没有输出提示词、图片数据或凭据。
  - **此前 dirty MCP 代码链（补丁前调查快照，不证明截图运行版本）**：该快照中 `ai_modeler/agent/dsh_mcp_server.py` 的 `_inspect_reference`（约 3012–3048 行）根据可见证据是否非空写 `observation_valid`；成功时 `verification_status="model_observation_unverified"`，完全失败时才为 `"unknown"`，并把结果追加至 `reference_inventory_history`。成功处理分支（约 3405–3430 行）记录、保存并返回结果，但当时未将其写入 `state.reference_inventory[reference_id]`。创建前门禁（约 3239–3257 行）从该索引读取记录，要求 SHA 与 manifest 一致且整体 `verification_status` 不为 `unknown`。这是补丁前静态写读失配候选，不能据此认定为历史截图运行的触发根因。
  - **此前门禁实际读取条件（补丁前快照）**：该快照中的 MCP 门禁不检查知识检索是否命中，也不要求落入固定结构类别表；它检查逐原图索引是否存在、SHA 是否匹配、inventory 的整体 `verification_status` 是否恰为 `unknown`。有效清单中的单个特征/表面仍为 `unknown`，按该快照中的状态赋值规则本身不等同于整份观察失败。SHA 或 reference ID 不匹配也可能触发阻断，但截图运行缺少能证明该分支的安全日志。另有 `agent_runtime.py` 的旧式 `reference_reading` schema 门禁；在确认实际运行入口前，不把那条并行实现认作本次触发路径。
  - **补丁与专项回归（2026-10-01 记录）**：当时的补丁更新了 `ai_modeler/agent/dsh_mcp_server.py` 和 `tests/test_dsh_review_workflow.py`；6 个 inventory 专项 case 与 4 个几何保护/有效前缀 case 通过，目标测试模块 53/53 通过，`git diff --check` 通过。这些是单测/差异检查证据，不等于真实产品验收，也不能确认旧截图的历史触发根因。`dsh_mcp_server.py` 后于 2026-10-02 架构迁移中删除。
  - **当前真实 CLI 结果（已停止，未完成）**：`coldimg20261001inventoryfix` 使用唯一原图 `artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`（SHA-256 `53ed87fadf8cca869da9216276e13c845424c6906b3de517d5cca972db94553`）。首次state核验原图SHA匹配，`roi_norm=null`、`observation_valid=true`，清单记录左/右外侧面各3孔及退入中面3孔，`verification_status=model_observation_unverified`；这是inventory门禁通过证据，不代表模型正确。CADPlan依次创建 `base_block` rev1、`center_slot` rev2，删除主体rev3、删除slot后空计划rev4，再创建主体/slot rev5–6、重复`center_slot` rev7，并于rev8–10添加孔。rev10的iso `cad_preview_model` 返回 `geometry_build_failed`，但响应中的`failed_feature`和`error`均为null、prefix feature IDs为空，且没有`last_preview` / `partial_candidate`可供诊断。之后仍有多次删除/改动，最终revision 14共34 actions，仅余`base_block`、`center_slot`、`hole_left_outer_top_v2`；没有有效preview、image review pair、AIMODEL、STL或最终结果。归档计36 provider requests / 35个完成 / 1个中断在途；监督端Ctrl+C后CLI exit 1，Python/Node/DSH子进程核对为0。progress counter到`+717s`时停止，但这是CLI相对事件进度，**不是已验证的墙钟总耗时**。没有CATIA运行。完整state、630条observability事件、manifest与停止报告见 [run归档](../../artifacts/agent-quality-rebuild/cold-acceptance-20261001-inventory-fix/run-report.md)；原始stdout/stderr未分别持久化，`observed-cli-progress.md`是监督端可见事件整理。由于未保存原始tool args及底层错误，不能把本次失败归因给某个几何后端；报告表明server未保留失败特征/错误细节，也未保存可诊断的有效前缀/partial candidate。旧截图对应session仍未定位。
  - **本轮 P0 修复后的真实 CLI 冷启动（失败，未完成）**：session `coldimg20261001p0guard`，原图 SHA-256 `53ed87fadf8cca869da9216276e13c845424c6906b3de517d5cca972db94553`，中性任务与模型 `moonshotai/Kimi-K2.7-Code` / low；完整state、observability、manifest及报告见 [本轮归档](../../artifacts/agent-quality-rebuild/cold-acceptance-20261001-p0-guards/run-report.md)。inventory SHA匹配、`observation_valid=true`、4条观察，但右前竖面孔数报告4个而原图可见3个，未人工纠正或反馈。Agent创建唯一 `main-block` 后尝试删除，服务端 sole-body guard拒绝；随后仅反复修改同一主体，几何revision到4，历史signature回退被拒绝，未建凹槽/孔、无preview、partial candidate、独立review或导出。监督端依据多次无效更新/保护拒绝且无可用候选，于同run CLI tracker `+300.9s` 发送Ctrl+C，CLI exit1，目标Python/Node/DSH进程均0。日志16 provider wire request / 15完整response，15个DSH request start / 14 complete；仅inventory request带1图，其余15次0图。15个provider response body独立求和input 255,696、output 9,030、reasoning 6,139 tokens；矛盾`total_tokens`不采信。`+300.9s`是CLI stdout同run相对秒，不是跨run墙钟测量。此轮只实测验证了主体删除保护；重复特征守卫和preview失败前缀回退未触发，没有可验收几何候选。
  - **P0 server修复状态（仍待真实端到端验收）**：当前代码实现唯一 `operation=add` 主体删除保护、feature几何语义重复拦截，以及preview失败时经本地BREP逐前缀定位/保存 `accepted=false` partial candidate 和修复提示；若无可验证前缀则不伪造候选或底层错误。局部目标测试53/53通过，但本轮CLI只覆盖主体删除保护；重复特征和preview失败前缀分支仍需真实run验证。历史截图session与实际几何构建失败根因仍未确认。
  - **版本与计时边界**：补丁前调查快照的 `dsh_mcp_server.py` mtime 为 2026-10-01 21:42、diff 为 +3171/-329 行，HEAD 中不存在 `image_dependent_tools` / `reference_inventory` 这段门禁；以上是补丁前证据，不代表当前版本。旧截图历史 session 仍未定位；`+717s`仅为CLI相对事件进度，不作为真实整轮elapsed。Python秒与Node毫秒事件须按来源统一时间基准后重算，不使用旧`568.4秒`估值。
  - **修复要求与验收**：知识库 `no_match` 只表示没有匹配模板，不能阻止创造模式。对 `observation_valid=true` 且 SHA / reference ID 有效的清单，即使局部结构类别或未观察到的背面仍为 `unknown`，也应从有证据支持的可见轮廓、表面和孔等结构建立首轮可验证候选，并把未知事实保留为未知，不能补造。分别验收：①未收录的自定义结构在 `no_match` 下首轮 `cad_create_model` 成功；②可见面/孔/轮廓已给出而背面未知时可先建候选且不声称背面已核实；③真正无效或完全无可见证据的观察只做有限自动重识别，仍无效则明确报错并保持零几何写入；④错误 SHA、过期/错误 reference ID 仍被明确拒绝并给出可恢复原因；⑤复现截图对应 session 后，用安全字段核对 observation、SHA、reference ID、门禁结果与 revision 变化。历史根因与最终修复验收均未完成。
- [x] **切削孔几何核验（geometry_max）**：局部材料区间判定与fail-open回退已修；定向SDK/BREP **61/61**通过。正式Agent CLI `35bf6e27cf95420eb491e6dbdcd36824` exit0：100×60×20 mm底板上，BREP测得 Ø10通孔 `verified_open`、Ø8深6盲孔 `verified_blind`，含凸台的包围盒100×60×28 mm，单个封闭实体、无自交/未核验切削意图。结果只证明文本驱动的本地几何核验，没有视觉pair/CATIA核验。
- [ ] **其余特征支持范围（geometry_max）**：15,000面矩阵 `feature-execution-matrix-20261001-185447.json` 中27类有26项已应用特征自交检查clear；shell特征没有应用，仍needs_review。2,500面对照 `feature-execution-matrix-20261001-185529.json` 中 socket_head_bolt/bevel_gear自交unknown且均needs_review。支持标签exact 10项、approximate 16项、unavailable 1项。复杂紧固件最高约33秒单项构建；面数阈值不提供时长保证。
- [ ] **多视图清单（geometry_max）**：真实CLI的最终 `preview.png` 确实是top视图（`preview_image_view_set=top`），iso/front也有单独文件；但 `geometry-report.json.view_previews` 仅登记iso/front，`output_paths`把top作为通用`preview`，没有独立的`preview_top`/`views/top.png`字段。属于视图产物索引不一致，需按真实文件核对后修复。
- [ ] **多轮来源保护（loop_max + input_max）**：只改长度时，宽、高、位置来源必须保持。真实编辑虽然得到 120×60×20 mm，但整体 `cad_create_model` 重建曾重写其他来源，验收失败。最新真实几何CLI又复现新问题：明确的 user 数值缺少可信 `turn_id` 时，`cad_create_model` / `cad_add_feature`被反复拒绝；Agent最终删除主体并进入best-candidate恢复。`cad_get_state`其实返回authoritative user turns，loop正修复错误后引导模型读取本轮turn_id并禁止删主体绕过。
- [x] **来源接口契约（source_contract_fix，限定文本标量）**：真实CLI同一几何场景通过。Agent 曾尝试将从用户直径10换算出的半径5错误标为 `user`，MCP按来源契约拒绝；Agent读取权威用户轮次后将半径改为真实 `derived`，继续完成，未删除主体或重新建模。最终审计确认底板长100/宽60/厚20、盲孔深6、回转角360的直接参数都保留精确当前 `turn_id=389d8bc080dc446198ddb44e3e57be17` 与原文引句；总 action 12项、1次有因拒绝、0次替换。CADPlan/BREP结果为100×60×28 mm、闭合单实体、Ø10通孔 `verified_open`、Ø8×深6盲孔 `verified_blind`。几何文本场景耗时226.875秒、13次provider请求、4,503 reasoning tokens；较旧同场景979.922秒/32请求/19,443 reasoning分别减少约77%/59%/77%。39项定向MCP workflow tests、stdio smoke、不可比review mock gate通过。边界：CADPlan没有为转换前的直径、外径和凸台高度建立一对一来源标量字段；运行审计证明了转换参数及来源，但不能声称原始每个复合尺寸都被单独存档。未证明图像估值或图像审查。冻结SHA：server `D4071B0AF89DD988EE6C705FADE5B0EE2B11894D1D43EB9C80F07D79DFE01124`，system prompt `E591FD2155773004649CE18E8CF07135974D49E193AB1606B9EBD78063E9B86D`，test `5850CD584307BF8A76050E4A0A0BCCC0AB6170B87608658ED52128B64985BF04`。观察报告：[`observation.md`](../artifacts/agent-quality-rebuild/loop-review/source-contract-cli-20261001/observation.md)，Result SHA `ff1b9af66cac63f5556fe3654a6f137661d7df1389c391662289a28d7f196e30`。真实运行目录：`artifacts/agent-quality-rebuild/loop-review/source-contract-cli-20261001/output/turns/389d8bc080dc446198ddb44e3e57be17/`。
- [ ] **独立视觉复核与finding可执行（loop_max）**：独立报告已完成，基于原图、`views/iso.png`、`views/front.png`逐张目视且未读取CADPlan/geometry report/Agent自述。报告认为两个pair均可在限定可见特征上比较，明确指出宽凹腔与窄中央凹入结构不同、孔所在面带不同、孔径层级缺失；详见 [cold mid2 独立复核](../artifacts/agent-quality-rebuild/cold-acceptance-20261001-mid2-independent-review.md)。程序仍记录两轮 `not_comparable`、`coverage=unverified`、`score=null`、无normalized evidence，`open_multiview_findings=0`，并未把这些差异作为finding交给真实Agent修复。只证明候选被标为未验收，**没有视觉验收通过或修复闭环**。另有代码审阅证据：`_inspect_reference`约2714–2798只取观察对象顶层字段、空字段仍可按success缓存；ROI约3155–3164未纳入cache key；iso pair的strict boolean判定约2557、false时清空证据约2617–2622。`cad-state.json`的`reference_similarity_history[0]`保留 `pair_note`及`discarded_noncomparable_evidence_count=5`，但5条原始证据正文未留存，无法进一步分类。代码/报告证据不证明provider响应原文或触发该结果的唯一根因。
- [x] **旧预览图片清理（input_max）**：精确按source kind/generation删除旧预览并保留原图/未知图；Node bridge **4/4**、Python **37项**通过，日志 `artifacts/agent-quality-rebuild/input-reasoning/stream-bridge-node-tests-20261001.txt`、`input-cli-llm-regressions-20261001.txt`。第三轮真实CLI的provider wire证实generation 3→4后 `removed_stale_tool_previews=1`、payload只留gen4；参考原图SHA `53ed87...94553`仍在同一会话reference asset与manifest，原始图像请求SHA一致。此处验收了清理和本地留存；纯文本follow-up没重传原图，按需再次取用原图进行独立复核仍待验收。
- [x] **重复图像根因审计（input_max）**：报告 `artifacts/agent-quality-rebuild/input-reasoning/multimodal-reference-replay-audit-20261001.md`（SHA `9F9F39DC02E969AEAEF5E8B48E995E4133F4E0995EB4BEB3343341CEEFD0D1F9`）确认应用把原图bytes加入DSH初始blocks一次，但DSH将其带入后续会话请求重放；原始reference会保留是为后续按需复核。另一类重复来自 `cad_preview_model` 的图片工具结果进入DSH history，当前代预览仍被保留，因此`get_state`/`finish`/stop请求也带同一张预览图。审计未改代码。待实施的边界是主对话/tool/stop不传图像bytes、视觉检查走独立有哈希与证据的观察请求、相同输入对复用审查；需要两进程真实CLI证明首次读图与跨turn按reference ID独立取图。
- [ ] **推理与等待可观测（input_max）**：stream bridge改为状态转换+5秒采样、CLI watcher 15秒心跳；Python 37项、Node bridge 4/4、Node语法检查通过。真实CLI几何轮 `35bf6e27cf95420eb491e6dbdcd36824` 耗时979.9秒，32个provider请求/35个工具、19,443 reasoning tokens；wire等待972.6秒，reasoning阶段922.8秒，仅一次 `cad_validate_model`。首轮图像建模686秒，其中2次vision review约220秒、23次请求重复发送原图。日志采样已减少记录量；无效思考、视觉复核成本与过多模型往返仍未解决。
- [x] **图数未知值语义（input_max）**：真实 unknown/null/NaN/Inf/bool/非整数 usage/image计数不会被伪报为0或触发 `int(inf)`；dsh_agent定向19/19及扩展Python37项通过，证据日志SHA `99D6B3A3CD71D16F42625B9ED03492920B0DC65917CDE4CD50D1CAC581FACB3F`、`5BF388C66DE690BDBF07330B6DEAE3FD12FAAA020879A874BAF46BCD81AD28D1`。

### 当前真实输入续话验收（三轮完成，图像复核仍未通过）

- 首轮真实 DSH CLI turn `921c175f327b40fa96b9bbe9156a4298` 已完成：耗时约 11分26秒，23次 provider wire、累计 provider reasoning 12,533 tokens、28张次图像、19,364条旧版逐delta stream activity。每个被检查的原图 wire均匹配同一用户图SHA；预览生成后payload中同时出现原图与当前iso preview。Provider每请求有 `enable_thinking=true` 和 2048 budget，但实际累计reasoning远高于单次默认值；预算不是跨全Agent运行的总上限。
- Agent 实际建出90×36×80 mm主体+36×72×24 mm凹槽，revision4本地几何通过，保存AIMODEL/STL/ISO；exit4 / `candidate_not_verified`。独立多模态pair确实发送，但模型返回 `view_comparable=false`，却同时填82分及局部“verified/match”；程序最终守住了全局覆盖门槛（0个comparable pair、最终unverified）。这暴露局部review summary矛盾，已要求loop修复。
- 上述长运行暴露 `agent-observability.jsonl`在一次turn里膨胀到19,364条逐delta活动事件；input已调整桥接事件采样，并正在补测试。**旧运行不使用新采样代码。**
- 第二个同session纯文字turn `d1fae560acbc4fa58ac7a217455c1ea9` 已完成：总高度80→85 mm、center重新派生为42.5，长度/宽度/凹槽尺寸及来源保留，几何检查passed，exit4因参考视觉仍未验收。6次Provider请求/6 wire、907 reasoning tokens、仅30个stream_activity样本；首四次文本请求无图、后两次仅当前generation2预览。原图仍留在reference-images与user-turns清单（SHA一致），但纯文本请求没有自动带原图。用户只做尺寸修改时不必强制重新上传；用户要求视觉对照时Agent须能按保留reference id主动调取原图。
- 第三个同session纯文字turn `b52046c710fa4db0a237da981a06e720` 已完成：Agent 按请求对主体高度连续两次 +1 mm，最终 85→87 mm、中心 Z=43.5 mm，凹槽与其他尺寸保持。7次真实provider请求、4张实际图像部分、1,435 reasoning tokens；约73秒完成，stream observability压到108条。本轮真实 wire 看到 preview gen3 后再出 gen4，后续payload移除gen3、仅保留gen4；本地原图仍在同会话reference资产与manifest。CLI以exit4 / `candidate_not_verified`结束，所以这轮只通过增量编辑和预览清理验证，没有视觉复核结论。
- 预览清理已有真实多版本wire证据，不再待第三轮；仍要证明下一次视觉复核能从同一session的reference manifest按需读出SHA正确的原图，并与当前候选的匹配单视图形成可比pair。

provider wire 的累计耗时拆分：原图首轮686.0秒/23次请求/20工具/12,533 reasoning tokens；文本turn2 63.49秒/6请求/907 reasoning；文本turn3 72.30秒/7请求/1,435 reasoning。长几何turn 979.9秒/32请求/35工具/19,443 reasoning，wire等待972.6秒，reasoning阶段922.8秒。2048是每request预算，不是整轮总上限；不要用过低总调用次数替代消除失败重试和重复上下文。

详细证据见 [`runtime-cost-audit-20261001.md`](../artifacts/agent-quality-rebuild/input-reasoning/runtime-cost-audit-20261001.md)，SHA-256 `D0BAD010C1C248D776F79094E1C2DADE9B7D11A7ABAB8AE69B76C2BDD24FE932`。其中已将可证事实和无法从无payload日志推断的部分分开。

## 3. 核心验收：仍未通过

- [ ] **先让审查发现已有错误模型的问题**：使用归档候选与原图，独立检查应指出两侧正面缺孔、孔打在侧面；暂不提示正确构造或尺寸。
- [ ] **真实返修闭环**：审查差异关联具体特征，修改后使用新版视图关闭差异；旧图、自评分、改名不能绕过问题账本。
- [ ] **全新独立冷启动复刻**：只提供原图、通用请求和新接口说明，由 Agent 自行估量比例并建模；正确表达中央后退、三列九孔、大—小—大孔径布局。
- [ ] **连续对话完整验收**：图片首轮后文字追问/局部修改，保留原图、未改特征和来源，不无故重建。
- [ ] **多参考图与纯文字覆盖**：除当前单图，还需多视图合并、冲突处理、文字约束优先等真实场景。
- [ ] **联网检索可靠性**：修复无关搜索结果/跳转空正文；真实 Agent 搜索并读取可用来源。用户明确要求的外部事实未核实，不能整体宣称已完成。
- [ ] **最好候选与停止条件**：实际证明无进展/往返修改时保留有效候选，避免长时间重复无效操作。
- [ ] **快速模式兼容回归**：与 Agent 路由分离，并记录实际 CLI 结果。

## 4. 当前明确失败的证据

1. 原图独立冷启动：约 31 分 40 秒、111 次工具调用、45 次返修，最终未验收。模型两侧前面没有孔，错误地打在侧面；BREP 有效不能证明外形正确。
2. 该轮独立原图/模型多模态 pair 成功数为 0；主 Agent 的自评不能算独立视觉复核。
3. 该轮累计输入图片 344 张次、最后一次请求 12 张，说明旧预览积累仍需真实修复证据。
4. 最新长度编辑：几何尺寸正确，但未改参数来源被重建覆盖，不能算连续对话完全通过。
5. 最新增量文字场景 turn `2bd01a33c48c41c18664cd93c20ea682`：100×60×28 mm 闭合单实体；Ø10 通孔已测开；Ø8 / 深6盲孔因全局高度判断bug未核验，CLI `candidate_geometry_unverified` / exit4。原始结果在 `artifacts/agent-quality-rebuild/geometry-backend/geometry-cut-intent-cli-20261001/turns/2bd01a33c48c41c18664cd93c20ea682/result.json`。
6. Loop 初始44项回归曾有1项失败；补齐快捷凹槽/孔阵列等来源校验路径后，最新扩展回归54/54通过且MCP stdio smoke通过。仍待真实多轮Agent验证。

## 5. 执行顺序与交付边界

1. 三条 Luna / max 工作线并行修复各自问题；共享 MCP/来源接口协调，真实运行时冻结相关文件。
2. 先通过短文字编辑和已有错误模型的独立复核，避免直接重复半小时盲跑。
3. 再启动全新独立原图复刻与返修验收。
4. 补联网、多图、持续对话与快速模式场景；依据证据逐条勾选完整范围。

交付：本地可编辑 AIMODEL、STL、单视图预览、几何/审查报告与真实调用记录。不把未运行的 CATIA/CATPart 核验算作本轮成果。不提交、推送或创建 PR。
