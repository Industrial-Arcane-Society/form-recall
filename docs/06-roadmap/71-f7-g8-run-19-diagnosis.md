# F7 G8 Run19 冷启动尝试诊断

日期：2026-10-04

## 运行边界

- 在独立 detached worktree `C:\r19` 上使用固定 HEAD `542ca3fc3ca172bcf7d6af6feabf68a91d2d3e3e`；runner 结束时 HEAD 未变、worktree clean。文档后续提交位于独立分支 `feat/run19-coldstart-docs`，不改源代码，不 cherry-pick 或 push。
- 启动前核验项目默认原图为 `72,309 bytes`，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。使用默认 prompt，未传入额外用户文字、CADPlan、尺寸或孔数。预检验证了原图；但运行摘要没有发布原图 SHA，因此不能确认本次运行消费的原图身份。
- 预检确认 pinned Node `v22.23.3`、DSH `0.2.0-rc.2` 及 OCCT manifest 存在。keyless 公开 descriptor 配置为 `https://api.deepseek.com/v1`、`deepseek-flash`、`low`；只确认 `DEEPSEEK_API_KEY` 环境变量存在，未读取或输出其值。由于没有完整 DSH/transport 证据，不能确认 provider 请求是否发出或该设置是否实际生效。
- 只启动一次，未重试，未启动 GUI/CATIA。开始时间 `2026-10-04T03:27:51.6364043Z`，结束时间 `2026-10-04T03:29:56.6104124Z`，耗时 `125.6 s`，runner exit code `1`。运行输出正文、stderr 正文及任何 raw trace/模型内容均未读取。
- 摘要报告 scratch cleanup 为 `removed`；stale-scratch recovery 为 `complete`，所有回收计数均为 `0`。临时 keyless descriptor 与未读取的 stdout/stderr 文件已删除。

## 可用的安全发布证据

输出目录为 `%TEMP%\aimodeler-f7-g8-run19-20261004-27021e2501bb40f6a1afedbbbea1ed46\safe-output\`。三个白名单文件中只有 `summary.json` 存在；`tool-result-diagnostics.jsonl` 与 `build-status.json` 缺失。summary 大小 `585 bytes`，SHA-256 `15abe928472e9df2409c2fea96ebe3219ba4253cb6c85465a067939e53f41abe`。ACP/Cordis projection 和构建状态均没有可报告的文件或 SHA；也没有从白名单发布 CAD 几何产物 SHA。

`summary.json` 的固定安全字段为：

| 字段 | 值 |
|---|---|
| `status` / `strict_gates.status` | `failed` / `failed` |
| `status_scope` | `scope_unverified` |
| `transport_status` | `failed` |
| `model_status` | `none` |
| `strict_gates.failure_reason_count` | `1` |
| `strict_gates.evidence_error_count` | `0` |
| `runtime.node` / `runtime.dsh` | `unverified` / `unverified` |
| `original_image` | `null` |
| `scratch_cleanup_status` | `removed` |
| `scratch_recovery` | `complete`，所有计数 `0` |

摘要没有发布 gate map、gate reason counts/codes 或原图摘要；唯一的 failure reason 也没有可用固定 reason code。`build-status.json` 缺失，因此 Cargo 构建结果与构建耗时未知。transport 失败及 model status `none` 不足以证明请求是否曾送达 provider；本轮不能确认真实模型请求或 schema 修复的运行效果。具体失败根因未知，不从未读取的 stderr 或其它运行数据作归因。

## 源码发布路径审查

源码中 `archiveFailure` 仅由 `failSafely()` 的 uncaught exception / unhandled rejection 路径调用，并只发布泛化 summary，不写 diagnostics 或 build status；这与本轮只存在 summary 的形状相符。但它不能指出内部抛错点，也不能据此确认具体构建、运行或 provider 路径失败。运行根因保持 unknown。

## 结论与下一步

Run19 是一次失败且安全发布不完整的 runner 尝试，不能作为完整真实多模态 DSH 冷启动验收，也不能评估逐 gate 结果、ACP/Cordis 绑定、schema 修复或视觉/几何质量。预检原图 SHA 只证明启动前文件符合基线，摘要未绑定该哈希。不得把它表述为通过或据此放宽 F7 门禁；F7 保持失败。

后续先静态检查 runner 的启动与安全发布路径，查明本次为何进入泛化失败发布路径；具体抛错原因在现有安全证据中不可知。未知原因保持 unknown。在获得授权并保证新鲜隔离输出后，才考虑另一次单独验收；不得把 Run19 当作可重跑输出目录。
