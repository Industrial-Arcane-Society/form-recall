# F7 Run08 盲测冷启动验收诊断

## 运行范围与输入

Run08 使用当前 `feat/rust-dsh-acp-host` 工作树（HEAD `83699c19bfe054b1f8b69537c29141857449ead9`，本轮构建时工作树有 15 个 dirty paths）。没有修改生产 runner 或其他程序源码；未启动 GUI/CATIA，也没有给模型指定目标尺寸。

- 输出目录：`temp/acceptance/f7-g8-run08-coldstart-20261003-dfd7f3242eda440489eb3880ad1ee77d`
- Run ID：`native-agent-coldstart-63e7a2ed-0881-46df-a254-8377dfbdc141`
- 输入原图：`temp/evidence-archive/artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`，494×544、72,309 bytes；SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。请求前后 SHA 一致，主图 wire gate 通过。
- DSH/Node：`0.2.0-rc.2` / `v22.23.3`，使用 `src/apps/desktop/src-tauri/target/f1-runtime-probe` 固定运行时。
- 生产 system prompt：`src/legacy/prompts/dsh_agent/system.md`，SHA-256 `12d40e8c8e5d313782500c52998d4f01b1fa07e7bf15a00709d902cdfaca9ed3`。
- 当前 native tool schemas：`src/apps/desktop/src-tauri/src/native_tool_schemas.rs` SHA-256 `07f088546c0e10affbc21dcd2ffad094063732647706cf8dc5831d423087703e`；内嵌 schema JSON SHA-256 `52e5371c9ef8c60e299b9b40522a46f13099cf1ac10755d5769d70ddbae4131b`，含 25 个工具定义。以上哈希对应本次工作树内容，包含当时未提交的 prompt/schema 改动。
- 用户设置文件缺失：`AIMODELER_CONFIG_PATH`、`AIMODELER_DATA_DIR` 未设置；debug `src/legacy/config.json` 与 release `%LOCALAPPDATA%\com.aimodeler.desktop\config.json` 均不存在。按授权采用项目源码 `native_settings.rs` 的内置 DeepSeek 模板：provider `deepseek`、`https://api.deepseek.com/v1`、`deepseek-flash`、reasoning `low`。这不是用户当前桌面设置。临时非敏感 provider JSON SHA-256 `a1a18f4e84422f5d003d0b4315cf7fa96b1002d2fe11392c3c10b8ac78243909`；API key 仅从现有 `DEEPSEEK_API_KEY` 环境变量传给子进程，未写入配置或报告。来源另记于 `provider-config-provenance.json`。
- 本次默认用户提示未包含指定尺寸；text SHA-256 `eff320ddc00f6db8a190f269d939d3dbf1de4d0f925891538fd7859c70aac416`。
- 使用生产 runner 的临时脱敏副本 `temp/acceptance/run08-tools-20261003-b4e3604230af48d59a1d699a931ebbd4/native_agent_coldstart_acceptance.mjs`。与生产 runner 的差异仅是把 assistant 输出改为流式字符计数与 SHA-256，避免写入原始答复；thought 只计数。Agent 请求、prompt、schema、工具代理和门禁逻辑不变。profile 生成器的源码保证 key 不进入 profile 文件或摘要。DSH 在新 home 中生成的压缩 session transcript 与对应 session cache 文件已在不读取内容的情况下移除；保留了结构化门禁、工具审计、CADPlan、几何报告与图像。

## 执行结果

离线 Cargo example rebuild 通过（exit 0，约 10.1 秒）。DSH 进程正常结束（exit 0、`end_turn`）；传输完成，21 个 Responses API 请求和 21 个响应均被记录，原图 wire hash 匹配。模型提交并保存了 revision 11 的候选，但没有取得 host acceptance。整体 strict gate **failed**，不是传输失败。

21 次 DSH Cordis 工具调用中 14 次成功、7 次失败；另有一次 `probe-0` Rust 本地 `cad_get_state` postcheck，不计入 DSH Cordis 调用。门禁结果：

- **通过：** `run_scope`、`dsh`、`trace_integrity`、`original_image`、`main_image_wire`、`current_geometry`、`artifacts`。
- **失败：** `cordis_cad_tools`（失败工具调用）、`acp_cad_tool_results`（并非所有 CAD 调用完成）、`holes`（finish hole measurement 不完整）、`visual`（没有有效原图 inventory/独立覆盖）、`finish`（host 未接受）。

## 工具、CADPlan 与几何证据

成功的建模参数均来自模型对图像的估算，不是输入目标尺寸：

- `cad_create_model`：主体长方体中心 `[0,0,0]`，`length=100`、`width=60`、`height=120` mm，来源标为 `image_estimate`。
- `cad_add_face_recess`：正面中央凹槽，宽 20、高 112、深 30 mm，面中心 `[0,-4]`；槽底位于后退 30 mm 的面，顶部估留约 8 mm 横桥。
- 两次 `cad_add_hole_array`：左右外侧面各 3 孔，中心位置 `X=±30`、`Z=+38/0/−38`；槽底面后退 30 mm 另 3 孔，`X=0`、`Z=+38/0/−38`。9 个孔全部估为 Ø16 mm、沿 Y 贯穿。
- `cad_set_construction_milestone`：将主体、凹槽和上述 9 孔全部列为完成。
- `cad_preview_model(view="iso")`：revision 11 的预览 generation 5。`cad_finish_model` 成功保存候选，状态为 `candidate_holes_unverified`，`accepted=false`。

最终 `cadplan.json` SHA-256 为 `952e287819759d4b0132c680b0f7124b43dcd1ab5df2d8fbe7999532176cebf5`；`geometry-report.json` SHA-256 为 `2d24b8dbef7e6bff638569a08624ae606b46ef23aa631a4f9f06989f27c8b867`。OCCT 报告为 valid、revision 11、闭合实体 1 个，尺寸 100×60×120 mm、体积约 562,322.13 mm³。9 个物理孔实例均报告 `verified_open`，`hole_measurements_verified=true`；但证据明确是 sampled material intervals、不是直径计量，finish 的 `hole_measurement.complete=false`，所以严格 holes gate 仍失败。报告 `image_acceptance=not_performed`，CATIA 未执行。

## 失败归因与视觉观察

- 5 次 `cad_inspect_references` 均失败，返回 `truncated_response: Responses API 因 max_output_tokens 限制未完成输出`。尝试过原图、全图 ROI `[0,0,1,1]`、ROI `[0.05,0.08,0.45,0.85]`、ROI `[0.4,0.25,0.3,0.5]`，最后还请求了 `refresh_reason="reobserve"`；没有任何一次建立有效 source inventory。
- 两次 `cad_review_reference_similarity` 均失败。第一次用 `view="iso"`、`preview_generation=4`，因该 generation 不属于当前 revision 被拒；第二次改用 generation 5，仍因当前原图 SHA/ROI 没有持久 source inventory 被拒。因此 independent review pair count 为 0，原图 `covered=false`、`inventory_valid=false`，视觉证据未核验。
- 我手动对照了原图与 Run08 的 ISO、front、right、back 单视图（这不是独立 review）。模型 front 视图呈现 3×3 等径孔；原图的外列与槽底孔口有可见大小差异。模型 ISO 视图也未清楚呈现原图中可见的槽底三孔。故当前同径 Ø16 方案虽然本地 BREP 可用，仍有可见差异和未核验项。

严格失败原因：`cad_tool_rejected_unbound_or_unaudited`、`tool_call_rejected_or_failed`、`acp_cad_tool_failed`、`acp_cad_tool_result_completion_missing`、`holes_incomplete_or_unverified`、`visual_coverage_incomplete_or_conflicting`、`visual_gate_original_reference_binding_missing`、`host_finish_not_accepted`。

## 下一步

先修复 `cad_inspect_references` 的 Responses API 截断并取得持久、SHA/ROI 绑定的 source inventory；只用当前 revision 的 generation 5 与正确 reference binding 重做 ISO 单视图独立复核。再补齐严格 gate 所要求的 hole measurement completeness（当前仅有穿孔/材料区间采样证据），并依据新视觉证据处理孔径差异与槽底孔可见性。随后在全新输出目录重新执行完整 strict gate。当前 Run08 只证明本地几何构建和主图 wire 传输成功，不证明视觉匹配或 CATIA 结果。
