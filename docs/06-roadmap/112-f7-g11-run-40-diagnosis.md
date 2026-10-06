# Run40：G11 RPC 诊断字段与建模结果复核

日期：2026-10-06

## 运行范围

- 使用原始 U 形凹槽工件参考图，以 source HEAD `abce7d2` 执行一次真实 DSH CLI 冷启动；Node `v22.23.3`、DSH `0.2.0-rc.2`。参考图 494×544、72,309 bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，运行前后字节未变。
- 未注入 CADPlan、尺寸或孔数 seed；未启动可见桌面窗口或 CATIA。只读取 runner allowlist 的 `summary.json`、`build-status.json`、`tool-result-diagnostics.jsonl` 与视觉核验包，不读取 raw provider 日志、scratch、prompt 或几何载荷。
- 安全产物保留在 ignored 本地目录 `temp/acceptance/f7-g11-run40-20261006/`，没有提交图像或运行载荷。

## 运行结果

- Build 通过（exit 0，39,432 ms）；DSH transport 完成，model status 为 `candidate`，但 runner exit code 为 `1`，因此本轮未通过验收。
- 13 个工具调用全部 terminal-completed，几何 revision 前进 3 次；prompt elapsed 为 1,224,331 ms（约 20.4 分钟）。
- 13 个 strict host gates 中 10 项通过、3 项失败。`holes` 失败于测量证据/完成状态缺失；`visual` 失败于原图及全部视图覆盖不完整；`finish` 失败于 review 未接受。总计 13 个 failure reason，evidence error count 为 0。
- 独立比较 allowlist 中的参考图和 ISO 预览：候选主体具有大致 U 形凹槽轮廓，但 ISO 中只看见左右两块外侧面各三孔；参考图凹槽内的中间壁还有一列三孔，候选未呈现该列。该观察与 `holes`/`visual` gate 失败相符；不能据此给尺寸判通过。

## G11 诊断字段边界

本次 summary 的 `dsh_diagnostic` 为 `stage=unknown`、`method=unknown`、`error_class=none`、`request_sequence=null`、`rpc_error_code=unknown`。工具诊断中没有 JSON-RPC failure row，因此本轮没有触发 RPC 错误分类与序号关联分支。这个结果只能说明安全字段可在无 RPC 失败的成功传输中保持无值状态，不能证明错误发生时能正确归类或保留 request sequence。

下一次针对 G11 的验证需要在隔离 harness 中制造单一可控的 JSON-RPC 数字错误码，并核对固定分类桶、请求序号及敏感字段不外泄。不要为了触发诊断而重复长时间真实建模冷启动。

## 安全产物身份

| 文件 | SHA-256 |
|---|---|
| `summary.json` | `a8f6622ab97df22ace024f37430be5d881722be98c0af7a15ac86ccc6576d7dd` |
| `build-status.json` | `c1e19eb6d2faf00e9a75b4d507ef18c51f3f63f3366c768678133c01af188ca6` |
| `tool-result-diagnostics.jsonl` | `bdc7d0cb26dc0fa87cbcb73d646b0146a3fc743efb10c7845deb1f156a139826` |
| reference PNG | `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553` |
| ISO preview PNG | `e0a4e04e0a83e20ebb343ca0ba0b2fd0c72b004f61c0f6b82e2969226e702a22` |

结论：Run40 证明原图传输、DSH transport、工具终态和候选预览导出链路本轮可运行；它没有通过孔、视觉与 finish 验收，也没有覆盖 G11 的 RPC 错误分支。F7.G8 与 F7.G11 均保持未完成。
