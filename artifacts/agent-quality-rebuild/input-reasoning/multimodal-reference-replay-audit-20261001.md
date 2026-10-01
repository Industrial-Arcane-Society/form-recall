# 多模态原图与 Preview 重复上行审计

只读审计；未改源码、未启动模型/provider。核对 CLI/桌面 DSH 路径、provider wire 日志及当前实现。

## 流向与重复点

`python -m ai_modeler agent --image ...` → `cli/agent.py` 校验路径并调用 `dsh_agent.run_dsh_agent`；CLI 跨进程保存的 `conversation-history.json` 只有可见文字，reference 另存为 `sha256:<原图哈希>` 的不可变 asset/manifest。`dsh_agent.py` 将 prompt、文字历史、知识和 reference ID 放入 `blocks`，随后又把当前原图作为 image block 加入一次，再由 `harness.run(blocks, session_id=...)` 交给 DSH；MCP 工具通过 stdio 调 `dsh_mcp_server.py`。这是原图反复出现在首轮 provider 请求的来源：应用只在 `blocks` 构造处加入一次，DSH 在内部迭代中从含图会话上下文重新组装后续请求。`dsh_stream_bridge.mjs` 的 `llm/stream` 边界和 Node `fetch` hook 分别记录 DSH 消息与实际 provider body。

两类独立视觉调用走 Python `llm.chat_completion`，不共享 DSH 对话：`cad_inspect_references` 仅向观察器发送原图并保存结构化 inventory；`cad_review_reference_similarity` 按 `reference_id + view + preview_generation` 重新构造**一张原图与一张同视角预览**的 pair。相反，`cad_get_reference_image` 和 `cad_preview_model` 会把图片作为 MCP tool result 返回给 DSH，图片因此进入其后续上下文。

桥接层不是原图的注入源，但会让重复持续：`pruneStalePreviewImages` 只删有 provenance 且早于最新 generation 的 tool preview；用户原图被保护，最新 generation preview 被保留。它没有“当前请求不需要视觉”或“同一 preview 已发送过”的门控。`agent_runtime.py` 的 `_compact_main_reference_images` 属于另一条旧 AgentLoop；CLI 与桌面当前从 `dsh_agent.run_dsh_agent` 进入，改旧 compaction 不会修复这些 wire 事实。

## Wire 证据

- 首轮 `921c175f327b40fa96b9bbe9156a4298`：23 个 `provider_wire_request` 都映射到原图 SHA `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`；5 个请求还带 preview。日志拆分为 21 个 DSH `node_global_fetch` 请求（全部带原图）和 2 个 Python 视觉调用：source inventory 1 张原图、独立 review 1 组原图+ISO preview。响应耗时分别为 78,990.3 ms 与 140,455.9 ms，合计 219,446.2 ms。证据：`input-reasoning/cli-image-prune-20261001/turns/921c175f327b40fa96b9bbe9156a4298/agent-observability.jsonl`。
- 几何 turn `35bf6e27cf95420eb491e6dbdcd36824`：32 个 provider 请求中，30/31/32 重复发送同一 top preview SHA `6b6c63e6fad3ba956feb84f47b0a47389bf4a137e2c7ee6a923b215577cad640`；均为 revision 7、generation 3、top，geometry signature 一致。第 31/32 个请求前分别执行 `cad_get_state`、`cad_finish_model`；最后纯 stop 请求仍含图。桥接日志报告 `latest_generation=3` 并保留该 preview。该 turn 没有原图 image part，但资产仍由稳定 ID 留在 session manifest，可供工具按需读取。证据：`geometry-backend/geometry-local-material-cli-20261001/turns/35bf6e27cf95420eb491e6dbdcd36824/agent-observability.jsonl`。

## 最小改造与风险

1. **`ai_modeler/agent/dsh_agent.py`**：初始 DSH `blocks` 只放 reference ID/元数据，不再放原图 bytes；新 reference 在建模前必须完成 `cad_inspect_references` source-only 结构化读取并存档，缺失/失败时保留 unknown 并阻断依赖图像的验收。继续保留原始 content-addressed assets、manifest 和 SHA 校验。
2. **`ai_modeler/agent/dsh_mcp_server.py`**：预览仍落盘并保留 revision/view/hash，但 `cad_preview_model` 返回 DSH 的结果改为文字/元数据；原图复看通过 reference ID/ROI 调用独立观察器并返回结构化结果，不把 raw image tool result 塞回 DSH。对 preview 建 `(geometry_signature, view, render_options)` 缓存；对独立复核按 `(reference_id, source_sha256, geometry_signature/revision, view, preview_sha256, user_target_fingerprint, scope)` 幂等，只有输入确实变化才发新的单视图 pair。已有独立复核路径可复用。
3. **`ai_modeler/agent/dsh_stream_bridge.mjs`**：增加 DSH wire 防线，所有主对话/tool/stop payload 的 mapped `user_reference` 与 `tool_preview` 图像在发出前清除，并记录清除数；未解析或不能安全改写时 fail closed/显式报错。Python 独立视觉请求走 urllib，不经过此 hook，仍可按需发送原图或单视图 pair。

风险：不把图像直接放进 DSH 后，必须强制首轮结构化读图完成后才能执行依赖视觉的动作；review 结果需明确带证据和状态，否则模型可能只根据摘要推断。复核缓存必须纳入用户目标/范围和 preview 内容哈希，避免 geometry 同 revision、不同像素时误用旧结论。当前 reference manifest 已保留跨文本 turn 的原图访问能力，无需把二进制重新塞进 CLI 历史。无需改 `cli/agent.py` 的文字历史；`agent_runtime.py` 不在此 DSH 调用路径内。

## 冷启动 CLI 验收门槛

从项目根目录用新 session ID 分别启动两个独立 CLI 进程；第二次不传 `--image`：

```powershell
$img = ".\artifacts\agent-quality-rebuild\references\user-nine-hole-recess-iso.png"
python -m ai_modeler agent --session-id image-context-cold-20261001 --image $img --text "根据附图建立主体候选；按图记录可见证据，完成后按需要做一次单视图复核。" --output-dir .\artifacts\agent-quality-rebuild\input-reasoning\acceptance-image-context
python -m ai_modeler agent --session-id image-context-cold-20261001 --text "只做一次小的文本指定尺寸修改；如需复核，按 reference_id sha256:53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553 重新取图并选择匹配视图。" --output-dir .\artifacts\agent-quality-rebuild\input-reasoning\acceptance-image-context
```

验收 `agent-observability.jsonl` 与 reference/CAD 状态：① 两进程全部 DSH `node_global_fetch` 请求 `image_count=0`；② 新 reference 只触发一次结构化 source inventory，恰好 1 个原图 part，SHA/稳定 ID 与 manifest 相同，inventory 持久化；③ 明确要求的独立 review 每个唯一 pair 恰好 2 个 image part（1 原图 + 1 当前 view preview），重复相同输入不新增 provider request；④ 几何 signature、view、render options 不变时复用预览，不增加 preview generation；⑤ 第二进程没有 `--image` 仍能从 manifest 按 ID 取回原图，且源文件 SHA 校验不变；⑥ `cad_get_state`/finish/纯文本 stop 的 provider wire 都为 0 image parts。本次只列验收指标，没有运行。
