# F7.G1 主 Agent 同次对照原图与当前单视图

日期：2026-10-05

## 修复范围

基于已推送 `main` HEAD `0e676d8`。生产代码此前已将 `cad_preview_model` 的当前单视图 PNG 编码为 Cordis tool result image block；但该 tool result 不带原图，原图只存在于此前 ACP turn / 独立 `cad_get_reference_image` 调用的上下文。因而本地契约并未保证主 Agent 在同一 tool result 中收到一对可直接核对的图片，也没有在这对图片旁绑定原图 SHA 与当前 preview generation。

## 变更

- `cad_preview_model` 新增可选 `reference_id`。Agent 直接对照时传入 Host 已登记的 reference ID；Host 从会话 `reference-images.json` 重新读取并验证其原始字节及 SHA-256。
- 成功结果按顺序携带原图原始字节、当前所请求方向的单视图。原图不经过 crop、重编码或覆盖。结构化 `comparison_attachments` 将 reference ID/source SHA 与 preview view/revision/generation/SHA 关联，并记录固定图片顺序。
- 不传 `reference_id` 时保留原有只返回当前单视图的行为。未知 ID、被篡改的原图路径内容或 SHA 不匹配均 fail closed。
- Cordis 实际渲染继续使用现有生产 attachment pipeline；图片对不是拼图或复合视图。

## 回归

定向 Rust 回归 `preview_comparison_attaches_original_bytes_before_single_view_with_sha_binding` 通过（1/1）：通过真实 Host reference manifest 选取原图，断言第一附件 Base64 解码前后逐字节等于原始 PNG，第二附件为当前单视图，并检查顺序、reference ID/SHA、view 和 generation；invented reference ID 被拒绝。Native tool schema 定向组 8/8 通过，其中检查了可选 `reference_id` 契约。代码路径重用 `native_vision_assets::reference_input`，该函数会在每次读取时核验 source SHA；测试没有调用真实 provider，也没有读取/改写任何验收原图。

追加验证 `node --test src/legacy/tests/test_dsh_native_tools_rpc.mjs`：6 passed、0 failed、1 skipped。新 Cordis fixture 从生产 Rust schema manifest 注册 `cad_preview_model`，桥接层返回绑定元数据与两张图，断言 production `attachments.saveImages` 和 Cordis `output.render` 均按原图、当前单视图顺序保留两个附件及原始字节。唯一 skip 是需安装 pinned DSH runtime 的独立 schema compiler 检查；本回归不依赖它，也不调用真实 provider。

## 验收边界

这项修复保证在模型按契约传入 `reference_id` 时，单次 `cad_preview_model` Cordis tool result 可包含绑定到来源 SHA 的原图与匹配当前 revision/generation 的单视图。它不证明模型会主动传 ID、不证明真实 DSH 会消费或正确理解两张图片，也不把已知 visual gate 的六项失败归因于此缺口。最初的契约测试没有发起 provider 请求或启动桌面 GUI；后续真实 DSH/provider 复验结果见下文。

## 2026-10-06 单图冷启动复验

在隔离 worktree `f7-g1-retest` 的代码 `1ba97cb` 上，使用仓库原图作为唯一用户输入启动项目 CLI runner；未启动桌面 GUI，未预置 CADPlan、尺寸或模型。DSH Runtime 校验为 Node `v22.23.3` + DSH `0.2.0-rc.2`，本地 offline Cargo build 与 native vision probe 均就绪。原图为 72,309 bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，运行前后不变。

### 已证实的链路

- 原图通过 reference inventory 进入主请求：DSH 请求和 provider wire telemetry 均记录 1 张输入图；真实 SiliconFlow `moonshotai/Kimi-K2.7-Code` `/v1/chat/completions` 返回 200 和流式响应。
- Agent 自行调用 `cad_create_model`、`cad_add_face_recess` 和孔阵列工具，几何 revision 从 0 增至 12，并调用 `cad_set_construction_milestone` 与 `cad_preview_model`。
- `cad_preview_model` 的 native bridge 与 Cordis 工具结果都含 1 个文本 block 和 2 个独立 image blocks。后续真实 provider wire 请求记录 `image_count=3`，即当前 turn 中的原图加 preview 返回的两张单视图。这证明本次 Host/Cordis 保留并把三张图片交给 provider 请求；不单独证明模型已正确理解这些视图。
- 本次 `cad_preview_model` 参数中存在 `reference_id`。因此确认了当前显式引用 ID 的原图+单视图链路，但**没有覆盖本修复专门增加的“省略 reference_id 时自动配对”分支**。
- 已生成 `visual-review/reference.png` 与 `visual-review/model-iso.png`，可在本机运行归档 `temp/acceptance/f7-g1-retest-20261006/` 查看。

### 未通过项及准确错误

- 最初两次 `cad_inspect_references` 分别被拒绝，Host 安全诊断为 `reference_id 不在当前会话原图清单中。` 和 `timeout: 视觉模型请求超时。`；之后同一工具调用成功。
- `cad_review_reference_similarity` 两次均被 Host 拒绝：`review_failed:preconditions_missing; create a current preview, inspect the selected reference, and register the current construction milestone, then retry.` 尽管运行此前已有成功的 reference inspect、construction milestone 与当前 preview；现有安全审计未保留足够调用参数以区分具体缺失前置条件，不能推断是哪一项不匹配。未产生可验收的独立视觉审查或基于审查结果的修正。
- Provider wire telemetry 有一次 `timeout`（150,013 ms）；后续请求仍继续。全程 900,008 ms 后，runner 的 `session/prompt` 以 `error_class=timeout` 结束（request sequence 4）。进度计数为 42 次有效 session updates、15 次 tool calls、10 次 completed、4 次 failed、4 次几何 revision advances。
- runner 汇总为 `status=failed`、`model_status=candidate`，严格门禁 36 项失败原因；DSH session、Cordis 工具、当前几何绑定、孔、visual 与 finish 均未通过。当前模型只是本地候选，不能称视觉复核通过，也没有 CATIA 验收。

本次结论：原图确实到达 provider，当前单视图也经 Cordis 保留并随原图一起到达 provider；但该事实没有转化为可完成的视觉复核，因为审查工具的前置条件连续失败，Agent 最终超时。自动配对分支由于 Agent 显式传了 `reference_id`，仍需另一次明确省略 ID 的专门复验。完整安全摘要在 `temp/acceptance/f7-g1-retest-20261006/summary.json`；同目录包含 `tool-result-diagnostics.jsonl` 与本轮两张图像证据。运行时 DSH scratch 按 runner 策略在结束后删除。
