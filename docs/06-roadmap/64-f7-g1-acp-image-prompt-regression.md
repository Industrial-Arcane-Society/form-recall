# F7.G1 ACP 多图请求构造回归

日期：2026-10-04
范围：headless 单元回归；不启动 DSH、视觉 provider、桌面 GUI、WebView 或 CATIA。

## 改动

`agent_host.rs` 将主 Agent 的 ACP prompt 参数构造抽为私有纯函数 `session_prompt_params`，生产 `run_turn_inner` 继续将其结果传给 `AcpProcess::request("session/prompt", ...)`。文字仍排在 prompt 首位，图片按调用方提供的顺序输出为 `{type: image, mimeType, data}`。

新增的固定 fixture 回归包含 PNG、JPEG、WebP 三项。测试序列化并重新读取 prompt 参数，逐项检查图片顺序和 MIME；对 Base64 数据解码后核对原始字节，并将每项 SHA 与同序 `active_turn_record.reference_images` 条目比对。

运行命令：

```powershell
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter agent_host::tests::acp_session_prompt_preserves_multi_image_order_and_manifest_sha_bindings -Exact
```

结果：`1 passed, 0 failed`；291 项测试因定向过滤未运行。

## 验收边界

该回归证明生产代码使用的 ACP `session/prompt` 参数构造在 JSON 序列化边界保留了多图顺序、MIME、Base64 字节，以及与 reference manifest SHA 的对应关系。它使用固定二进制 fixture，不启动 ACP 子进程，也不验证真实 DSH/provider 收到或理解了图片；因此不能作为图像观察质量、模型理解或多模态自审通过的证据。

F7.G1 仍需真实受控 DSH 冷启动及视觉验收。Todo 状态应继续明确区分“本地 ACP 请求构造回归通过”和“真实视觉验收未通过/待完成”；F7.G8 也不因该测试而改变状态。
