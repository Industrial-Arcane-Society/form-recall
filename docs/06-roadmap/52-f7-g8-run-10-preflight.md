# F7 Run10 预检记录

## 结论

Run10 没有进入模型请求阶段，不能用于判断模型质量，也没有验证 `tools/result` 安全诊断监听器。严格 runner 两次都在 Tauri/Cargo 资源准备阶段退出；没有 provider 请求、CAD 工具调用或严格门禁结果。Run10 不是一次模型冷启动。

## 输入与固定环境

- 隔离 worktree HEAD：`b77cc3e251654b7f8e061d2cbc887f8c61ab0ed3`。
- Node：`v22.23.3`；DSH：`0.2.0-rc.2`；provider key 只从 `DEEPSEEK_API_KEY` 环境变量读取。
- 默认任务文本 SHA-256：`eff320ddc00f6db8a190f269d939d3dbf1de4d0f925891538fd7859c70aac416`，与 Run09 相同。
- 原始参考图 SHA-256：`53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，72,309 bytes，与 Run09 相同。

## 构建阻断

1. 首次严格 runner 输出目录 `f7-g8-run10-20261003-047be6f7305249f7b83fef7f87e2d7cd` 在 `cargo build --offline` 以 exit 101 退出。没有 run metadata、门禁结果或诊断事件。
2. 隔离 worktree 缺少 `src/apps/desktop/dist` 和 `node_modules`。锁文件 SHA 与现有 checkout 相同。临时 junction 仅用于接入该锁定依赖，pinned Node 下 `vue-tsc -b` 与 Vite build 成功；junction 已删除核实，只保留隔离 worktree 的 `dist`。
3. 第二次 runner 输出目录 `f7-g8-run10-20261003-7715b097effa404cb63af520e9283732` 仍在构建前退出。`tauri-build` 的首个明确错误为缺少 `target/package-resources/RUNTIME-INFORMATION.txt`。
4. 唯一同提交可检查的 staging 包含 21 个文件，未发现会话、transcript、settings、provider config 或 credential 文件名。其 OCCT manifest SHA-256 为 `e95f7c445846029afd63a2e33dedd68a21dffe9e8853f2590f87f2cc99b2b653`，与当前所需 f4-occt manifest SHA-256 `39402baa4a84d3d8903e1ea97d8085e78ab047b75a568d4bb50617031a2f9649` 不同。没有复制这个 staging，也没有继续 runner。

## 证据与后续条件

两次失败输出都留在 Run10 隔离 worktree 的 `temp/acceptance/`。第二个目录保存脱敏预检报告与逐文件资源路径、大小、SHA-256 清单：

- `temp/acceptance/f7-g8-run10-20261003-7715b097effa404cb63af520e9283732/run10-preflight-report.md`
- `temp/acceptance/f7-g8-run10-20261003-7715b097effa404cb63af520e9283732/resource-provenance.json`

没有读取 raw transcript、assistant 正文/thought、工具参数、图片 base64 或凭据；没有启动 GUI/CATIA，也没有发送 provider 请求。`tool-result-diagnostics.jsonl` 为空，诊断监听器仍需真实 DSH 工具结果验收。

继续冷启动前，需要为当前 f4-occt manifest 找到来源可信、同版本且可验证的完整 runtime staging，或在独立变更中修复开发/验收构建准备流程；不能使用上述 hash 不匹配的资源包。
