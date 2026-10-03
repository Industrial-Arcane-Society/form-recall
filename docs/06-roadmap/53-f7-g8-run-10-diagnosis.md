# F7 Run10 诊断报告

## 结论

严格冷启动已到达模型/工具循环并退出，runner exit code 1，G8 strict gate **failed**。工具结果诊断实际记录到图像 payload 在 Rust bridge 返回 `isError=false` 后被 DSH native tool 层拒绝，定位代码为 `IMAGE_RESULT_INVALID`。本次没有成功附加原图/预览 image block；没有 decoded byte 数值，也没有 `attachment_save` 事件。

## 输入与运行环境

- Checkout：隔离 worktree，HEAD `b77cc3e251654b7f8e061d2cbc887f8c61ab0ed3`。
- Runtime：Node `v22.23.3`、DSH `0.2.0-rc.2`；官方资源构建与 `cargo build --offline` 均通过。
- Provider：使用项目内置 DeepSeek template（Responses route `https://api.deepseek.com/v1`，`deepseek-flash`，configured effort `low`）；key 仅来自进程环境，未保存/记录。该配置不代表缺失的桌面用户设置。
- Profile：main/vision output cap `32768`；system prompt SHA-256 `14b672e9eb4b587766dc08c551b3a854daa6033f3dbc23985b48445efbd2dad9`。Run09 保存的 prompt SHA 是 `12d40e8c8e5d313782500c52998d4f01b1fa07e7bf15a00709d902cdfaca9ed3`，Run09 vision cap 为 4096；因此 Run10 与 Run09 不是完全同 profile 的单变量比较。Run10 使用当前 b77 production profile，未改 prompt/schema。
- Task：沿用 runner 内置默认任务；其 SHA-256 与 Run09 相同：`eff320ddc00f6db8a190f269d939d3dbf1de4d0f925891538fd7859c70aac416`。
- 原始 ISO 图片：SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，72,309 bytes；原图门禁通过。
- 唯一最终运行目录为本目录。此前两个 build 失败目录保留，未复用。

## Strict gates

通过：`run_scope`、`dsh`、`trace_integrity`、`original_image`、`main_image_wire`、`current_geometry`、`artifacts`、`holes`。

失败：`cordis_cad_tools`、`acp_cad_tool_results`、`visual`、`finish`。失败理由包括 CAD tool 未绑定/未审计或被拒绝、ACP tool failed/终态 update duplicated、visual coverage incomplete/conflicting、original-reference binding missing、host finish not accepted。整体仍失败。

`current_geometry`、`artifacts` 和 `holes` 的 gate 为 passed；本报告没有读取大体积几何状态文件，因此不报告几何尺寸、预览像素或孔布局细节。没有运行 CATIA。

## 安全诊断投影

诊断 JSONL 有 42 条记录，字段均在白名单内（未发现未知字段）。汇总所有 `isError=true` 的事件：

| 工具 | 阶段 | 结果 | 允许列表错误标识 |
|---|---|---|---|
| `cad_get_reference_image` | `native_image_validation` | error | `TypeError` / `IMAGE_RESULT_INVALID` |
| `cad_get_reference_image` | `cordis_tools_result` | error | 未提供允许列表错误 type/code |
| `cad_preview_model` | `native_image_validation` | error | `TypeError` / `IMAGE_RESULT_INVALID` |
| `cad_preview_model` | `cordis_tools_result` | error | 未提供允许列表错误 type/code |
| `cad_update_feature` | `cordis_tools_result` | error，两次 | type/code 均被降级为 `other` |

`cad_get_reference_image` 和 `cad_preview_model` 的 native bridge 结果各为 `isError=false`，但只返回了 `image` 类型块，媒体类型摘要为 `other`；验证阶段各产生 `IMAGE_RESULT_INVALID`，随后 Cordis `tools/result` 为 `isError=true`。两次都没有 `native_image_decode` 或 `attachment_save` 阶段，因此无媒体类型白名单命中、无 decoded byte 数值、无成功附件保存证据。脱敏诊断不能区分无效项究竟来自媒体类型、base64 格式或其他图片验证条件。

`cad_update_feature` 的两次失败只有 Cordis result 层记录，type/code 超出允许列表后均为 `other`；没有查看 arguments 或原始错误消息，原因无法进一步归因。

其余诊断中的 CAD result 为 `isError=false`；孔门禁仍通过。失败更新调用的身份/参数绑定门禁仍失败，故不能将结构几何通过解释为所有 CAD ACP 调用都成功。

## G1 与验证边界

当前 headless coldstart 的 `run_scope`、DSH、trace、原图和主图 wire gates 通过；它没有启动 Tauri WebView，因此真实 G1 WebView IPC 仍未验证。G8 strict 结果失败。没有 CATIA、GUI、原始 transcript/tool trace/provider response、工具参数、图片 base64 或模型正文被用于本报告；诊断仅使用允许字段及其计数/类型/字节数。summary schema 中有 `answer` 字段名，但本报告不访问或复述该字段值。