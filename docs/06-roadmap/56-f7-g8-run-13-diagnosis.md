# F7 G8 Run13 冷启动诊断

## 运行边界

- 独立 worktree 从图片返回修复与 ACP identity gate 修复后的代码运行；输出路径启动前不存在，strict runner 只调用一次，退出码为 `1`。
- Node `v22.23.3`、DSH `0.2.0-rc.2`；staging manifest 与固定 OCCT manifest SHA-256 匹配。输入为原始参考图，SHA-256 为 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。
- Provider 来自隔离进程配置：DeepSeek `deepseek-flash`，configured effort `low`；不代表桌面用户的 provider 配置。
- 只读取运行元数据、strict gate 名称与 reason、diagnostics 白名单投影。没有读取原始 trace、ACP event ID、工具参数、错误正文、模型回答/thought、provider response 或图像数据；没有启动 GUI/CATIA，也没有重试。

## 门禁结果

| Gate | 结果 | 原因 |
|---|---|---|
| `run_scope` | 通过 | — |
| `dsh` | 通过 | — |
| `trace_integrity` | 通过 | — |
| `original_image` | 通过 | — |
| `main_image_wire` | 通过 | — |
| `cordis_cad_tools` | 通过 | — |
| `acp_cad_tool_results` | 失败 | `acp_cad_tool_result_terminal_update_duplicated` |
| `current_geometry` | 通过 | — |
| `artifacts` | 通过 | — |
| `holes` | 通过 | — |
| `visual` | 失败 | `visual_coverage_incomplete_or_conflicting`、`visual_gate_original_reference_binding_missing` |
| `finish` | 失败 | `host_finish_not_accepted` |

总体 strict acceptance 未通过。

## 证据解释

- 缺少 ACP `tool_call_id` 的独立门禁没有出现，说明 Run12 修复的 `undefined` 聚合路径没有复现。
- 重复终态门禁仍失败。当前实现只按非空调用 ID 聚合，因此白名单证据支持“至少一个非空 ID 组出现多于一次 terminal update”；它无法说明是协议允许的多阶段更新、DSH 重放还是 host 的重复投递。不能据此直接放宽 gate。
- 38 条安全诊断中 `isError=true` 为 0；`error_present`、`error_info_present`、`error_name_present`、`error_code_present` 均无 true 记录。Cordis 工具错误字段没有提供失败归因。
- 原图工具结果与预览均为 `image/png`；decode 与 attachment save 成功。该证据证明图片附件链路可用，不证明视觉审查通过。
- `current_geometry`、`artifacts`、`holes` gates 通过；本轮未读取具体几何和孔测量，尺寸、孔参数和外观质量仍为 unknown。

## 后续

ACP v1 将 session/update 定义为按 toolCallId 更新当前状态，不提供通知序号或 at-most-once 保证；当前 runner 也未保留可区分重复、冲突终态的安全摘要。先依据协议和生产代码确定合法更新语义，并补充区分同值更新、冲突终态和采集重放的测试。保持严格门禁，不读取本轮原始事件细节。后续严格冷启动只增加经过审查的脱敏分类计数，不输出调用 ID、参数或正文。视觉 coverage/original-reference binding、host finish 与真实新几何版本复核仍待解决。
