# F7.G8 Run09 冷启动诊断

## 验收范围与来源

Run ID：`native-agent-coldstart-d1880db5-6d5e-4f95-be1a-ed124bdfa2fb`。本次为单原图、单轮 CLI/Host 严格门禁冷启动，不含模型尺寸、预置 CADPlan 或 seed plan。DSH 进程退出码为 0，run 结束状态为 `end_turn`；严格验收结果为失败（runner exit code 1）。没有启动 GUI 或 CATIA。

| 项目 | 记录 |
|---|---|
| 原图 | `temp/evidence-archive/artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，72,309 bytes |
| 输入文本 | 仅记录脱敏文本 SHA-256：`eff320ddc00f6db8a190f269d939d3dbf1de4d0f925891538fd7859c70aac416` |
| 系统 prompt | SHA-256 `12d40e8c8e5d313782500c52998d4f01b1fa07e7bf15a00709d902cdfaca9ed3` |
| 工具 schema | 冷启动 schema 文件 SHA-256 `439ceee910df6d96c6c9ba2246b553c38ba5b3e3265d9022a90073d6737d4c0b` |
| 运行时 | 固定 Node `v22.23.3`；DSH `0.2.0-rc.2`；运行时源码 commit `915a252` |
| Provider | DeepSeek Responses，`deepseek-flash`，`https://api.deepseek.com/v1`；凭据只来自 `DEEPSEEK_API_KEY` 子进程环境，未写入报告或运行配置 |

桌面用户设置在预检时不可用，Run metadata 记载使用项目内置 `native_settings::provider_template(deepseek)`；但 `effective-profile.json` 的 `configuration_source` 却是 `desktop_production_profile`。此外，生成的 DSH profile 使用内部别名 `ai-modeler`。这些记录对配置来源的描述不一致，必须进一步核实；本报告不将该运行描述为使用了当前桌面用户配置。

运行使用生产冷启动 runner 的临时脱敏副本；Agent 请求、prompt/schema、工具代理与严格门禁保持原行为，落盘的 assistant 内容仅保留字符数与哈希，thought 仅保留字符数。本文不纳入 DSH session/transcript 数据、原始 assistant 内容或思考链。Run09 的视觉 cap 沿用 Run08 实际 wire cap `4096`，main DSH cap 为 `32768`；本次未以提高 cap 作为变量。

## 离线门禁重算

Run09 原有 `strict-gates.json` 由旧诊断逻辑产生。提交 `9dc3a12` 后，仅用保存的结构化 run/state/tool/provider 事件和新的严格 gate 脚本离线重算；没有启动模型或重放 DSH。重算窗口取已保存事件的最大时间戳加 1 ms。结果仍为失败，scope 为 `single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance`。

通过的 gates：run scope、DSH 退出、trace integrity、原图完整性、原图 wire、本地当前几何、产物和 holes。失败的 gates：

- `cordis_cad_tools`：`cad_tool_rejected_unbound_or_unaudited`、`tool_call_rejected_or_failed`、`acp_cad_tool_failed`。
- `acp_cad_tool_results`：新诊断码 `acp_cad_tool_result_failed`。
- `visual`：`visual_coverage_incomplete_or_conflicting`、`visual_gate_original_reference_binding_missing`。
- `finish`：`host_finish_not_accepted`。

保存的 bridge audit 共 20 次 CAD 调用，19 次 `success=true`；唯一失败为 `cad_review_reference_similarity`。ACP trace 中 20 个工具调用都有 terminal update：13 个 `completed`，7 个 `failed`，其中 failed 为 `cad_get_reference_image` 1 次、`cad_preview_model` 5 次、`cad_review_reference_similarity` 1 次。旧 gate 用“每个状态都必须为 `completed`”作为 completion 谓词，因此将这 7 个**已有 failed 终态**误报为 `acp_cad_tool_result_completion_missing`。`9dc3a12` 将有终态的 failed 与确实没有终态更新分别编码；Run09 新 gate 报 `acp_cad_tool_result_failed`，没有放宽成功条件。原图绑定仍未通过视觉门禁。

ACP update 使用 ACP session ID，bridge rows 使用 `native-read-probe` tool session ID；各自与 run context 中对应的 ACP/tool session 字段一致。结构化 runner 配对没有报告 ACP/Cordis identity 不匹配。现有证据不表明 review 失败源自 session 误用；失败仍由 bridge success=false 及 ACP failed 状态记录。

## 几何与视觉证据

Run09 当前输出目录中的结构化几何报告 SHA-256 为 `8b1d470788addc728cff4d7e02609f13bd5f7a66bd7ef5dfb594efad1b6ed38a`，CADPlan SHA-256 为 `0ffaaa3b2c5bff2dad0d5a346eeb62d8c296e2728651a5d7ac305c228a86d09c`，单视图等距预览 SHA-256 为 `a5567b3164cbeace633b60e16ef3b9249e807c3a6cbe967704c7f9160e74c8c9`。

- 几何报告记录主体尺寸 `60×40×100 mm`、bounds `[-30,-20,-50,30,20,50] mm`、BREP `valid=true`、`closed_solid_count=1`。
- CADPlan 有 9 个孔实例；几何报告的 9 个实例均为 `Ø8 mm`、`through=true`。`hole_measurement_complete=true`、`hole_measurements_verified=true`，新 gate 的 holes 项通过。此项只证明当前几何与测量门禁，不证明其符合原图。
- `front.png` 与 `back.png` 单视图均显示 3×3 等尺寸孔；原图等轴视图可见大/小/大孔径组。新模型未保留该孔径比例，整体比例亦与原图不符。等距预览呈现有凹入的中央槽，但当前视觉证据不足以接受原图中的后退背壁与孔组对应关系。
- 几何报告 `image_acceptance=not_performed`、`catia_executed=false`。上述比较是对原图和新生成单视图的人工观察，不是独立模型图像验收或 CATIA 验证；visual gate 仍失败。

## reasoning 与来源抽取

全局配置仍为 `low`。本次三个原生视觉请求的结构化请求遥测均记录 `configured=low`、`requested=none`、`expected=none`、`reasoning_expected_status=known_protocol_payload_value`，`max_output_tokens=4096`；Responses usage 分别记录 `finish_reason=stop`、输出 token `1633 / 1306 / 968`、`reasoning_tokens=0`。这些字段证明请求路径记录了任务级 `none` 预期，且没有出现 Run08 中反复 `4096 reasoning_tokens` 与 `length` 截断的相同 usage 结果；它们不构成服务端对 reasoning 配置的确认。

原图 reference inventory 记录 `observation_status=observed`、`observation_valid=true`、1 个有效 observation，含 3 条 visible evidence、2 条 surface relations 和 4 条 uncertainty；`verification_status=unknown`。因此可以确认结构化源观察结果写入状态，但不能把它描述成已验证的尺寸事实。Run08 的对照记录为 5 次 `source_inventory` 请求均发送 `low`、4096 cap，usage `reasoning_tokens=4096` 且 `finish_reason=length`；本次 `none` 减少了这类 reasoning 用量，但尚未实现视觉几何一致性。

## 结论与后续

Run09 证明在固定 4096 vision cap 下，源观察请求使用 `requested/expected=none` 时，结构化 reference inventory 能被记录，且本次对应 usage 记录为 0 reasoning tokens；模型仍建立了与原图孔径分组不符的 9×Ø8 mm 方案，reference verification 未完成，视觉/finish 严格门禁失败。继续前应先核实 Run metadata 与 effective profile 的配置来源矛盾，并检查失败工具的 ACP/bridge 结果契约；禁止用放宽门禁或将 geometry pass 等同于视觉验收来判定通过。Run08/09 输出保留在 `temp/acceptance`，不纳入本报告提交。
