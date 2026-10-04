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

- `src/legacy/tests/test_dsh_native_tools_rpc.mjs` 的 Cordis fixture 现直接执行由生产 `apply()` 注册的 `output.render(args, output)`，并精确断言返回顺序为文本 block 加 `{ type: 'image', attachment: <saved attachment reference> }`。fixture 的 PNG bytes 经实际 bridge response、base64 decode、`saveImages` 与 render 回调贯通，因此锁定了本机 tool-result 到 Cordis 多模态 render content 的生产传输契约；没有发现需要改生产逻辑的接线缺陷。
- ACP helper 单测不是真实 DSH/provider 证明；Cordis `output.render` fixture 证明本地生产回调输出 image content block，不证明 DSH 模型实际在下一轮多模态请求消费或正确理解了该 content block。
- 已发布 safe summary/diagnostic 仅用于状态边界。Run31 `vision_review_projection` 的计数摘要、Run32 13-gate map 都不能证明真实视觉理解或 review 质量。Run32 `publication/uncaught_exception` 的具体根因仍未知。
- G9 的既有 schema/bridge/runtime gate 证据（包括 Run22 的 Cordis/ACP 身份与审计 gate）只证明工具契约/调用匹配层；不能推出视觉判断正确，也不解释 Run31/32 的视觉或 publication 结果。

## 最小下一步

本次回归关闭了确定性本地 render-block 测试盲点：调用生产注册的 `output.render` 并校验最终 content array 包含保存后的图片附件引用。若要完成 G1，仍需独立视觉质量/模型理解验收；真实 DSH/provider 是否在后续请求消费该图像以及是否理解正确，本地传输契约测试无法回答。

## 本次检查

本次只增加 Cordis fixture 对生产 `output.render` 图片 block 的断言，不改 Rust/JS 生产行为，也不运行 provider 或 GUI。定向检查命令：

```powershell
node --check src/legacy/ai_modeler/agent/dsh_native_tools.mjs
node --check src/legacy/tests/test_dsh_native_tools_rpc.mjs
node --test src/legacy/tests/test_dsh_native_tools_rpc.mjs
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter agent_host::tests::acp_session_prompt_preserves_multi_image_order_and_manifest_sha_bindings -Exact
git diff --check
```

执行结果：两个 `node --check` 均通过；Cordis/tool registration fixture 6 passed、0 failed、1 skipped（pinned DSH `defineTool` 测试因本地未安装锁定 runtime 按条件跳过）；Rust ACP helper 回归 1 passed、356 filtered；`git diff --check` 通过。Node fixture 使用测试版 `defineTool` stub，不启动 DSH/provider，只证明本地 bridge/attachment/render 生产回调的输出契约；Rust fixture只证明生产 ACP prompt helper 的图片顺序/MIME/字节与 manifest 绑定。两项均不是 provider 视觉理解验收。
