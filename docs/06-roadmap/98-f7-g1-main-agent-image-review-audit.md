# F7.G1 原图与单视图预览进入主 Agent 的源码审计

日期：2026-10-05

## 范围与结论

只审阅当前 checkout 的生产源码、已有测试、F7 Todo，以及已发布的安全运行摘要/诊断记录。没有读取 raw run traces、thought、provider answers、payload 或 scratch；没有联系 provider，也没有启动 GUI。

源码中没有证据支持“工具返回的原图/预览图在主 Agent review 前被确定性丢弃”这一生产缺陷。当前可追踪的本地路径如下：

1. 初始用户原图由 `agent_host.rs::session_prompt_params` 组成 ACP `session/prompt` image parts；`active_turn_record` 将 SHA 与 reference identity 绑定。现有 `acp_session_prompt_preserves_multi_image_order_and_manifest_sha_bindings` 测试核对顺序、MIME、Base64 原字节和对应 manifest SHA。
2. `cad_get_reference_image` / `cad_preview_model` 在 `native_cad_tools.rs` 从 manifest 或当前 preview generation 对应的视图读取 bytes，并经 `native_tool_executor.rs::tool_result` 放入 tool result 的 `images`。
3. `dsh_native_tools.mjs` 校验 media type/Base64/大小，调用 `ctx.attachments.saveImages`，并把返回的 references 放入 `imageAttachments`。工具的 `output.render` 将每个 reference 转为 `{type: 'image', attachment}` block，作为 DSH Cordis tool result content。
4. 独立视觉复核路径位于 `native_vision_domain.rs`：review 构造 provider 输入时按已验证 source manifest 读取原图，并按当前 revision/generation/fingerprint 读取预览图；现有 mock 回归逐字节核对这两张输入图。

## 可证边界 / 待补验证

- `src/legacy/tests/test_dsh_native_tools_rpc.mjs` 的 Cordis fixture 断言工具结果图被解码、保存，且 `imageAttachments` 被返回；它没有直接调用并断言 `output.render` 的最终 image content block。这里是测试覆盖缺口，源码实现本身可见且目前没有可证明的缺失赋值或错误映射，因此本次不改生产代码、不把测试缺口写成产品 bug。
- ACP helper 单测不是真实 DSH/provider 证明；Cordis fixture 也不证明 DSH 模型实际在下一轮多模态请求消费了该 content block。
- 已发布 safe summary/diagnostic 仅用于状态边界。Run31 `vision_review_projection` 的计数摘要、Run32 13-gate map 都不能证明真实视觉理解或 review 质量。Run32 `publication/uncaught_exception` 的具体根因仍未知。
- G9 的既有 schema/bridge/runtime gate 证据（包括 Run22 的 Cordis/ACP 身份与审计 gate）只证明工具契约/调用匹配层；不能推出视觉判断正确，也不解释 Run31/32 的视觉或 publication 结果。

## 最小下一步

若要关闭剩余的确定性测试盲点，后续可在 Cordis fixture 中显式调用 `registered[0].output.render(args, output)`，并断言第一块为文本、图片块数量/附件引用与 `imageAttachments` 顺序一致。若要完成 G1，仍需获准的严格冷启动及独立视觉验收；需要仅检查允许发布的 safe summary/projection，不检查 raw trace 或 provider 内容。运行时的 DSH/provider 是否真正消费并理解这些图像，静态代码和本地 fixture 均无法回答。

## 本次检查

本次不改 Rust/JS 行为，也不运行 provider 或 GUI。检查当前测试源码的静态语法与既有 Cordis plugin fixture：

```powershell
node --check src/legacy/ai_modeler/agent/dsh_native_tools.mjs
node --check src/legacy/tests/test_dsh_native_tools_rpc.mjs
node --test src/legacy/tests/test_dsh_native_tools_rpc.mjs
```

执行结果：两个 `node --check` 均通过；Cordis fixture 为 6 passed、0 failed、1 skipped（pinned DSH `defineTool` fixture 因本地未安装锁定 runtime 而按条件跳过）；`git diff --check` 通过。测试只覆盖本地 bridge/attachment fixture，不构成真实 DSH 或视觉验收。
