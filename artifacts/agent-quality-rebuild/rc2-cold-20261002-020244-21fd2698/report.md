# RC2 原生 Cordis 修复后冷启动验收报告

## 结论

**失败：实际 provider 已触达，但 Agent 未完成模型。** 修复后的真实 CLI 能启动 DSH 0.2.0-rc.2，native Cordis 插件注册 marker 显示 25 个工具；原始图像至少一次以精确 source hash 出现在 SiliconFlow provider wire payload。Agent 检查了参考图并创建 `base_block`，随后对同一特征连续 11 次调用 `cad_update_feature`，全部 `no_op=true`，几何签名未改变。为避免继续空转，停止本次 session 的 CLI/runtime/node 进程树。没有 preview、模型候选、BREP/网格校验或导出，不能作为成功建模验收。

## 输入与运行

- Prompt（原样）：`请根据参考图建立模型`
- 输入：`artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`；494×544，72,309 bytes；SHA-256 `53ed87fadf8cca869da9216276e13c845424c6906b3de517d5cca972db94553`，与原始图 hash 相同。
- 完整命令：

```powershell
python -u -m ai_modeler agent --text "请根据参考图建立模型" --image "artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png" --session-id "rc2-cold-20261002-020244-21fd2698" --output-dir "C:\Users\lycohana\Desktop\code\AI-_-\artifacts\agent-quality-rebuild\rc2-cold-20261002-020244-21fd2698"
```

- 启动：`2026-10-02 02:02:44.744 +08:00`。停止：`2026-10-02 02:06:43.962 +08:00`。总计约 3 分 59.218 秒。CLI 因外部停止记录 `EXIT_CODE=-1`，不是正常 CLI 结果码。
- CLI session：`rc2-cold-20261002-020244-21fd2698`；turn：`d807f446ad3f41c2858e216ff2bd27cd`。
- Provider/model：`api.siliconflow.cn` / `moonshotai/Kimi-K2.7-Code`；DSH reasoning `low`，wire 中 16 条记录 `reasoning_effort=enabled`，另 1 条缺该字段。
- CLI shim 为 `%LOCALAPPDATA%\AI Modeler\dsh-cli\node_modules\.bin\dsh.cmd`，版本 `0.2.0-rc.2`；Python SDK/runtime bin 均为 `0.1.5rc1`。真实 app CLI 调用 production `_runtime_for()`。

## Runtime/native tool 证据

- session marker `native-tool-registration.json` 为 `phase=registered`，包含 25 个 native Cordis 工具名；完整 marker 已复制到 `evidence/`。
- Provider 请求日志记录 17 个 wire request、16 个 response complete；停止时最后一个 provider wire request 未完成。DSH stream 记录 16 个 request start、15 个 complete。实际有 provider activity，但完成的工具操作没有生成有效几何增量。
- 工具调用共 16 个 start、16 个 complete：`cad_inspect_references` 2、`cad_get_state` 1、`cad_create_model` 1、`cad_get_feature_spec` 1、`cad_update_feature` 11。没有 `cad_preview_model`、`cad_review_reference_similarity` 或导出工具调用。
- Image wire 计数：17 个 provider payload 中 1 个含 1 张图，16 个不含图；唯一图像 payload 的 `source_kind=user_reference`、72,309 bytes、source SHA 与原图完全相同，mapping 为 `exact-transport-hash-match`。DSH boundary 则记录每次 request_image_count=1、hash_count=0；不能把 boundary 数字等同于 wire 中实际图像数。
- Usage event 有 15 条；各 event 中 `input_tokens` 累计 55,297、`output_tokens` 累计 3,577。raw `total_tokens` 字段累计 416,762，与 input/output 分项之和不一致，报告保留原字段和分项，不将其解释为可靠总 token 数。
- 检查本次 run/session 文本日志，未发现 MCP client/server/protocol 路径字样；run 中使用的是 native Cordis 注册记录。

## Agent 自主 CAD 状态

- 参考图观察工具记录 `observation_valid=true`、`verification_status=model_observation_unverified`；没有生成视图供多模态 review，视图复核流程未完成。
- revision 1 仅含 `base_block`（box/add，名称 `主体长方体`），参数为 120×60×160，center `[0,30,80]`。本次 state 中该 feature 的 `sources` 为空；没有人工输入尺寸。
- 11 次 `cad_update_feature` 均记录 `no_op=true`、`unchanged_geometry_signature=9a6fcc47600ab0eb47eda6e4e869bbcc33f989baa13a87df3e2bf53f56de1656`，`no_progress_by_signature` 对该签名计数 11。revision 一直为 1，只有这一个 feature。
- 可用 `cad-state.actions` 与 observability/stderr 记录每次动作及 no-op 结果，但本次持久化日志没有保留各次 `cad_update_feature` 的 arguments 对象；因此无法确认 11 次调用的参数是否逐项完全一致。stderr 提示更新未改变特征、已记录相同 geometry hash 的无进展调用，并要求不要重放相同参数、应核对 finding 和原图。
- 最终状态 `finished=false`、无 `finish_state`、无 output path；没有 AIMODEL、STL、preview PNG、accepted candidate、BREP 或 mesh validation 结果。包围盒、孔布局均不可核验。

## 停止与产物

停止对象仅为本 run 的 CLI 及其 DSH runtime/node 子进程。进程快照在 `evidence/process-tree-before-stop.json`、`evidence/process-root.json`。stdout 0 bytes，stderr 11,532 bytes。完整 CLI/turn logs、CAD state、actions、25-tool registration marker、输入图副本与 runtime 元数据均在 `evidence/` 和 run 根目录；SHA-256 清单见 `SHA256SUMS.txt`。
