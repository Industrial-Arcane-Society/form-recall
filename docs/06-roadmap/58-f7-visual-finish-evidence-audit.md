# F7/G8 visual coverage 与 host finish 证据审计

## 范围与结论

本记录基于 `feat/rust-dsh-acp-host` 的 `9fccf255339da0d0087753a0b8d6d13da7beca8f`，只读生产源码、Run13 脱敏诊断报告及仓库现有 fixtures。没有读取 Run13 原始日志、trace、ACP event ID、工具参数正文、模型回答/thought、provider response 或图像；没有启动 GUI、DSH、CATIA 或 provider。

Run13 脱敏报告确认原图工具结果和预览均成功返回 `image/png`，decode 和 attachment save 成功，`main_image_wire` 与 `cordis_cad_tools` 通过。它同时确认 `visual` gate 因 `visual_coverage_incomplete_or_conflicting`、`visual_gate_original_reference_binding_missing` 失败，`finish` gate 因 `host_finish_not_accepted` 失败。报告没有列出 host visual summary 和 finish summary 的底层字段值，因此不能从当前安全证据判断具体缺失了哪条 inventory、review、binding 或 finish 条件；三个 reason 是严格门禁结果，不等价于某个 Rust 函数抛出的错误。

**附件传输成功不能代替 host 证据生成。** `cad_get_reference_image` 与 `cad_preview_model` 只证明图像附件链路可用；`cad_inspect_references` 才建立源图 inventory，`cad_review_reference_similarity` 才产生与当前几何绑定的独立 review。保持门槛严格，先查并补齐真实证据，再运行 host finish 与同轮门禁。

## 生产数据路径

```text
DSH ACP tool call
  -> agent_host 启动 NativeToolBridge + NativeToolExecutor
  -> inspect/review: native_vision_domain 写入 host CAD state
  -> cad_get_state: native_state_summary 汇总当前引用证据
  -> native_review_gate aggregate 生成宿主整体 review
  -> cad_finish_model / finish_assessment 写入 finish_summary
  -> coldstart gate 对照同轮 source reference 与 host summary
```

ACP/tool bridge 的连线只说明调用如何到达 Rust；不提供视觉覆盖证明。`agent_host.rs:702–754` 初始化 executor、Cordis bridge 并将 bridge 配置交给 ACP 进程；`native_tool_bridge.rs:26–32, 367–375` 定义执行接口并把已验证调用交给 executor。`native_tool_executor.rs:170–178, 259–270, 443–482` 将 CAD 工具送到 `native_cad_tools::run`，将两个视觉工具分别送到 `native_vision_domain::inspect` / `review`。

### 原图与 visual coverage

- `native_cad_tools.rs:145–166`：`cad_get_reference_image` 从 manifest 读取宿主选定的原图，构造 Cordis image attachment，并返回 `state.clone()`；它不会创建 inventory 或独立 review。这与 Run13 的“图片附件成功”证据边界一致。
- `native_vision_domain.rs:130–273`：`cad_inspect_references` 根据宿主 reference manifest 获取原图、调用视觉 provider、规范化可见证据，并检查 evidence ID/描述。无效或空观察保留为 unknown/invalid；有效观察包含 reference ID、source SHA、全图 ROI/cache key、observation ID/version，并保存进状态。
- `native_vision_domain.rs:751–793`：库存有效性要求 reference ID/SHA、ROI/cache key 和有效标志匹配；证据列表非空，ID 必须带正确的 reference/ROI 前缀、描述非空且 ID 唯一。全图库存保存到 `reference_inventory`；ROI 库存保存到独立的 `reference_inventory_roi`。
- `native_vision_domain.rs:842–979`：`cad_review_reference_similarity` 要求当前 revision 的对应预览 generation/view、preview SHA 和 geometry fingerprint；对真实原图要求有效持久全图 source inventory，且至少有一条 required visible evidence。单视图分数不决定总体接受。
- `native_vision_domain.rs:1151–1245`：review 只有在施工阶段完整、完整当前几何处于范围内且不是 partial preview 时，才允许把要求证据列入 covered；模型提交的 covered ID 还需存在于 evidence、属于 required 集合且不为 out-of-scope。其余情况保留为部分/未验证。
- `native_state_summary.rs:585–656`：latest review 按当前 revision 和 user-target fingerprint 过滤；可比较性依赖 view 标记、几何签名、稳定 reference ID、索引一致性和全图 ROI。`native_state_summary.rs:665–724` 再检查源图库存绑定和有效性。
- `native_state_summary.rs:782–1069`：对每个原图汇总 required evidence 的支持、缺失、未知、冲突、unsupported coverage 和 finding。引用只有在 inventory 有效、required 列表非空、所有 required 项得到支持且无未解决/冲突/unsupported 项时才 `covered=true`；`all_references_covered` 要求列表非空且每一行 covered。
- `native_state_summary.rs:1061–1114`：summary 还要求 review 绑定当前 observation，并检查实际 evidence 中包含 required evidence ID。过期 observation 或缺项不能由另一个高分 review 抵消。

### 两条 visual gate

验收标签由 `src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.mjs:394–401` 生成，分别检查不同属性：

| reason | 生成条件（必须全部满足才能不报错） | 语义 |
|---|---|---|
| `visual_coverage_incomplete_or_conflicting` | finish summary 的 `reference_coverage=true`；`visual_evidence.all_references_covered=true`；`independent_review_pair_count > 0`；Rust `references.visual_coverage_complete=true`；manifest 有效；mismatch 数为 0 | 全部原图在当前 host 证据中完成 coverage，且存在独立可比较复核，没有冲突。 |
| `visual_gate_original_reference_binding_missing` | `original_reference_sources` 恰有一行；该行 ID、SHA 分别等于本轮输入 reference ID、SHA；该行 `covered=true` 且 `inventory_valid=true` | 将 coverage 明确绑定到本次不可变原图，防止错图、索引或旧库存误通过。 |

生产 host 侧 `native_review_gate.rs:353–427` 根据原图 manifest、`native_state_summary` 和每条引用行计算 `visual_coverage_complete`；它要求 manifest 合法，在存在原图时每行都 covered 且 inventory valid，并且 native visual summary 认定全覆盖。总体 review 的接受条件还要求几何通过、孔测量完整、全覆盖且 findings 清空（`native_review_gate.rs:62–115`）。

### `host_finish_not_accepted`

该标签在同一 coldstart gate 的 `native_agent_coldstart_gates.mjs:403–407` 生成。要不报错，必须同时满足：`domain.finished=true`、domain 和 finish summary 均为 `accepted`、finish summary 的 `accepted=true`、`overall_geometry_review_accepted=true`，并且 `review.issuer=native_review_gate`、`review.result.decision=accept`。它是多项合取的汇总标签，不能单独指出哪项失败。

host 的数据来源是 `native_cad_session.rs:1845–2039`。`finish_assessment` 校验当前 preview/revision/fingerprint 和用户来源 provenance，重算 aggregate，并要求已保存的整体 geometry review 与本次 host 重算结果一致；`accepted` 还要求：整体 geometry review 接受、本地几何检查通过、孔测量完整、reference coverage 完整、construction milestone 全部完成、required external facts 全部 verified、无 unresolved findings。`cad_finish_model` 通过 `native_cad_tools.rs:104–141` 调用 aggregate 和 finish assessment，返回真实 host `finish_summary`。候选文件可以被导出/保存不等于 strict acceptance 为 accepted。

## Run13 可知与不可知

**可知（来自 `56-f7-g8-run-13-diagnosis.md` 的脱敏字段）：**

- 输入原图和预览图片结果通过了附件返回、decode/save 检查；`main_image_wire` 与 `cordis_cad_tools` 通过。
- `current_geometry`、`artifacts`、`holes` 通过；`visual` 和 `finish` 未通过。
- `visual` 的两个 reason 分别对应覆盖完整性与本轮原图绑定检查；`host_finish_not_accepted` 表示 host finish 条件未满足。

**未知（该脱敏报告未给出，且本审计遵守不读原始运行数据的边界）：**

- Run13 是否调用了 `cad_inspect_references`；其 full-image inventory 是否有效、有多少 required evidence、是否与 manifest 的 reference ID/SHA/ROI 一致。
- 是否运行了当前 revision/generation 的 `cad_review_reference_similarity`；复核是否可比较、是否覆盖所有 required evidence、是否有 mismatch/unsupported/unresolved finding。
- 哪个 `host_finish_not_accepted` 子条件为 false，包括当前整体 geometry review、construction milestone、外部事实或 finish summary 的具体 accepted 状态。
- 因而，不能断言这三条 reason 有单一根因，也不能将 finish 失败归因于 visual failure。

## 最小正确修复流程

1. 先读取同一 run 的脱敏 `cad_get_state`/finish summary projection，查看每个原图行的 `inventory_valid`、`covered`、required/missing/unsupported/mismatch 计数，以及 host finish 的各项布尔摘要；不要用 `cad_get_reference_image` 成功或 similarity score 代替这些值。
2. 若全图 inventory 缺失或绑定无效，对原图调用 `cad_inspect_references`，让宿主校验并保存非空、有效、唯一且绑定本轮 reference ID/SHA 的可见证据库存。遇到明确观察冲突时按工具契约刷新观察并保留旧版本，不伪造或覆盖证据。
3. 若 inventory 有效但 coverage 不完整，先确保当前完整 CADPlan revision 有有效预览，并登记已完成施工范围；再以本轮原图和当前 `preview_generation` 调用 `cad_review_reference_similarity`。复核结果需覆盖 inventory 的 required evidence，处于完整当前几何范围，不得依赖 partial/out-of-scope evidence；修复真实冲突/finding 后再 review。
4. 用 Rust `cad_get_state` 确认原图行身份与 SHA 正确、inventory valid、covered、`all_references_covered=true`、无 mismatch，且 latest geometry/review 与当前 geometry fingerprint/observation 对齐。
5. 仅在当前 geometry review 被宿主重新计算并接受、本地 geometry/holes 检查齐备、construction milestone 完成、外部事实 verified 且 findings 清空后调用 `cad_finish_model`；随后用同一 run 的 postcheck 对照 host finish projection。任何条件不足时保留 candidate/失败，不修改验收门槛。

以上是由生产代码推导的最小工作流，不是 Run13 已证实漏调某工具。新的严格冷启动仍需真实运行验证 provider 视觉质量和用户请求下的实际 tool choice。

## Headless 测试覆盖与缺口

现有相关测试：

- `native_vision_domain.rs` 中 `repeated_source_evidence_id_cannot_make_inventory_reviewable`、`out_of_scope_review_is_not_promoted_and_feature_ids_are_scope_limited`、`explicit_conflict_refresh_versions_inventory_and_invalidates_old_review_and_finding_evidence`、`review_uses_exact_images_and_reports_specific_mismatch_without_accepting_score`。
- `native_state_summary.rs` 中 `review_versions_inventory_hash_roi_and_ledger_are_gated`、`missing_roi_or_sha_never_validates_inventory`、`review_source_hash_and_roi_must_match_current_full_image`。
- `native_review_gate.rs:642–678` 中 `a_high_similarity_score_without_all_reference_evidence_cannot_accept`。
- `native_cad_session.rs` 中 `image_task_with_high_per_view_coverage_still_needs_current_aggregate_acceptance`、`stale_overall_geometry_review_cannot_unlock_image_finish`、`current_overall_geometry_acceptance_unlocks_image_finish`、`aggregate_with_open_findings_can_finish_as_an_unaccepted_candidate`。
- `native_agent_coldstart_gates.test.mjs:58–64, 231–244` 有同轮完整 evidence 正例，以及 `host accepted false`、visual coverage false、缺 mandatory 字段等负例。

Headless predicate 覆盖进展（本次新增）：`native_agent_coldstart_gates.test.mjs` 新增 table-driven 翻转矩阵，分别覆盖 `visual_coverage_incomplete_or_conflicting` 的 6 个合取项、`visual_gate_original_reference_binding_missing` 的 6 个合取项、`host_finish_not_accepted` 的 7 个合取项。每个 fixture 先确认完整合成基线 gate 通过，再单独破坏一项并确认总结果失败及目标 reason 出现；生产门禁未改动、没有放宽条件。该覆盖仅证明当前 Node gate evaluator 对这些字段的严格判定，不证明字段在真实运行中由正确视觉分析生成，也不代表真实视觉/finish/F7 通过。

仍待实现的 headless 增补：

1. 用 mock vision provider 驱动 native executor 完成 inspect → current preview review → `cad_get_state`，断言 Cordis 工具执行只在有效全图 inventory 和当前可比较 review 存在后形成 required evidence coverage；同时校验 source ID/SHA 错绑、旧 observation、ROI inventory、空/重复 evidence、partial stage、mismatch 和 unsupported coverage 均 fail closed。
2. 增加“视觉全覆盖但 host finish 因其他条件未接受”与“host finish 汇总接受但原图行错绑”的反例，确保原因相互独立。
3. 覆盖 finish summary 每个合取项被单独置 false 的 Rust `finish_assessment` 测试，并断言候选导出状态不能误变成 accepted。

本次之后，mock provider → Rust `NativeToolExecutor` → `cad_get_state` 的最小组合路径已有 headless 集成覆盖：正例依序通过 `cad_inspect_references`、对预先提交且与 revision/fingerprint 绑定的当前预览执行 `cad_review_reference_similarity`、再执行 `cad_get_state`；断言原图 ID/SHA、ROI 派生 evidence ID、review revision/generation/fingerprint 和最终 coverage 均从会话状态对齐。负例覆盖 executor 路由上的错误 reference ID 与过期 preview generation，确认调用在绑定/当前性校验失败后不升级证据。该测试使用本地 mock HTTP vision provider，且预览是 fixture 中已提交的当前 host preview；它不证明 CAD 预览生成器、外部 provider 或真实视觉判断质量。

集成测试还捕获并修复了一个证据字段互操作缺口：review 已用 host 校验的 geometry fingerprint 记录当前签名，但遗漏了 state summary 用于精确比较的 `geometry_signature` 字段。review 现在把同一个已校验当前值写入该字段；没有变更比较 predicate 或验收门槛。若没有此字段，真实经过 executor 的 review 会在 get_state 被标为 noncomparable，required coverage 不会升级。

其余 headless 增补仍待实现：两个跨 gate 独立性反例、Rust `finish_assessment` 每个合取项的翻转测试，以及 executor 集成层的 source SHA 错绑、旧 observation、ROI-only inventory、空/重复 evidence、partial stage、mismatch/unsupported coverage 负例。相邻的 `native_vision_domain` 和 `native_state_summary` 既有单元测试仍覆盖其中若干领域校验，但不能替代完整的 executor 集成负例。

**证据边界：**以上只证明本地 headless mock provider 路径在合成 fixture 下保留严格绑定与当前性检查。它不代表真实 DSH/provider 视觉质量、不代表 Run13/Run15 已通过，也不改变 `F7.G1` 或 `F7.G8` 验收状态；真实 provider 行为仍须依据独立的脱敏运行证据评估。

## 维护状态

`F7.G1` 的视觉覆盖与原图 binding 仍未通过；`F7.G8` 的 Run13 严格冷启动仍未通过。本次新增的测试记录仅收敛 gate evaluator 的合成字段判定，不改变生产实现或 F7 验收状态。
