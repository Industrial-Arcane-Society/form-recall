# RC2 原生 Cordis 插件冷启动验收

## 结果

**失败，未完成建模验收。** 唯一一次真实 CLI 冷启动运行在 7.721 秒后以退出码 3 结束。runtime 已启动并成功注册原生 Cordis 插件的 25 个工具；首个 DSH 图像请求随后以 `模型图片请求预算无效。（UNKNOWN）` 失败。该 turn 没有 provider wire request、MCP/CAD 工具调用、CADPlan、模型、网格或预览。没有重试。

本次结果是有真实图片输入的 production CLI/runtime 启动失败证据，**不代表模型 smoke 或模型验收通过**。

## 输入、命令、版本

- 用户提示词（原样）：`请根据参考图建立模型`
- 单张原图：`artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`；SHA-256 `53ed87fadf8cca869da9216276e13c845424c6906b3de517d5cca972db94553`；494×544，72,309 bytes。与此前原图 hash 一致。
- 完整命令：

```powershell
python -u -m ai_modeler agent --text "请根据参考图建立模型" --image "artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png" --session-id "rc2-cold-20261002-015634-6d08f70e" --output-dir "C:\Users\lycohana\Desktop\code\AI-_-\artifacts\agent-quality-rebuild\rc2-cold-20261002-015634-6d08f70e"
```

- 时间：开始 `2026-10-02 01:56:34.130 +08:00`；结束 `2026-10-02 01:56:41.851 +08:00`；耗时 7.721 秒；退出码 `3`。
- session ID `rc2-cold-20261002-015634-6d08f70e`；turn ID `e6ecde87201c4c7b8fb0729b425edf35`；DSH session ID `rc2-cold-20261002-015634-6d08f70e-dsh-6b2237b8e39d`。
- 实际 CLI shim：`%LOCALAPPDATA%\AI Modeler\dsh-cli\node_modules\.bin\dsh.cmd`，`--version` 为 `0.2.0-rc.2`。Python distributions `deepseek-harness-sdk` 与 `deepseek-harness-runtime-bin` 均为 `0.1.5rc1`。
- 生产入口：实际运行 `python -m ai_modeler agent`，由应用的 `run_dsh_agent()` 调用生产 `_runtime_for()`；runtime patch 使用 `%LOCALAPPDATA%\AI Modeler\dsh\sessions\rc2-cold-20261002-015634-6d08f70e\runtime.patch.yml`。
- 模型设置：provider profile `ai-modeler`，模型 `moonshotai/Kimi-K2.7-Code`，reasoning `low`；API key 只核实存在，没有读取或记录密钥值。

## Native Cordis 注册与协议路径

本机 session 的 `native-tool-registration.json` 在 turn 结束后存在，`phase=registered`，列出 25 个 `registeredToolNames`。完整 marker 已复制到该 turn 证据目录。日志在 run 输出和 session 日志中没有 `MCP client`、`MCP server`、`MCP protocol` 或 `Model Context Protocol` 路径字样；此次真实运行没有进入工具调用阶段。

工具数：注册 25；实际调用 0。Provider wire request 数：0 条已观测；usage：null；CAD/网格工具验证：没有发生。

## 图片与错误上下文

- 用户 turn manifest 记录 1 张 source reference：SHA-256 与原图一致，494×544。
- CLI result 记录 `knowledge_search.status=no_match`，同时记录 `knowledge_image_parts=1`（一张本地视觉样例 contact sheet；结果列出 20 个候选示例），`image_input_mode=individual`。
- DSH stream boundary 记录一次逻辑请求、`request_image_count=1`、`request_image_hash_count=0`；首个内容事件是 finish，28 ms 后 request complete，usage unknown，`provider_wire_observed=false`。因 provider wire 未产生，不能确认真实出站 image parts 清单。
- CLI 报错：`DSH 模型请求失败：模型图片请求预算无效。（UNKNOWN）`。因此 provider 没有完成任何建模响应。

## 模型与交付检查

CLI 结构化结果为 `state=error`，`plan=null`，`validation=null`，`local_geometry_checks=null`，`tool_calls=0`，`preview_calls=0`，`output_paths={}`。没有 AIMODEL、STL、BREP、网格或 preview 文件，无法报告 CAD/mesh 有效性、包围盒或孔布局。没有进行人工建模、人工尺寸指定、CADPlan 构造、模型后处理或 GUI/CATIA 操作。

## 保留文件

运行目录保留 CLI invocation、stdout/stderr、turn `result.json`、`agent-observability.jsonl`、active turn、conversation/user-turn manifests、runtime capacity policy、注册 marker、原始输入图副本及进程检查。stdout 的结构化 JSON 说明错误与产物缺失；stderr 留有完整启动进度。SHA-256 清单见 `SHA256SUMS.txt`。
