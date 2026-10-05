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

这项修复保证在模型按契约传入 `reference_id` 时，单次 `cad_preview_model` Cordis tool result 可包含绑定到来源 SHA 的原图与匹配当前 revision/generation 的单视图。它不证明模型会主动传 ID、不证明真实 DSH 会消费或正确理解两张图片，也不把已知 visual gate 的六项失败归因于此缺口。真实多模态冷启动仍需单独验收；本次没有发起 provider 请求或启动桌面 GUI。
