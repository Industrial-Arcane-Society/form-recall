# F7/G8 Run30：late-fatal 状态一致性修复后的单次冷启动

日期：2026-10-04

## 运行条件

- 代码固定在 fetch 得到的 main commit `d9c38289d89ec897d91759669aae2dd33670a0a3`，tree `f060b62098e59089a251a94759683a69bf5348de`；使用专用 worktree 的新分支 `feat/f7-run30-coldstart`。原 `feat/f7-run29-coldstart` 分支保留。
- 无 GUI、无 CATIA。仅运行一次 `native_agent_coldstart_acceptance.mjs`，传入四个必需位置参数；省略 image/userText 参数，因此选择 runner 默认原图与默认 prompt；无 CADPlan、尺寸、孔数量或 userText seed。
- 无 provider 的本地 preflight 只检查参数数量、必需文件存在性和 safe-output leaf 不存在；没有读取 descriptor 内容、API key、原图内容或 checksum。Node、DSH package descriptor、OCCT manifest、Run26 provider descriptor、runner 与默认原图路径均存在。safe-output 父目录由 launcher 创建，leaf 由 runner 创建。
- `AIMODELER_ACCEPTANCE_DEADLINE_SECONDS=1800`，设置 `AIMODELER_OCCT_MANIFEST` 到已存在的 clean-target manifest。stdout/stderr 丢弃；没有重试。
- runner 进程退出码：`1`。

## Safe allowlist 结果

只读取 `summary.json`、`build-status.json`、`tool-result-diagnostics.jsonl`。

| 字段 | 结果 |
|---|---|
| Safe summary status | `failed` |
| `transport_status` / `model_status` | `failed` / `none` |
| `failure_diagnostic` | `stage=publication`, `code=uncaught_exception` |
| `strict_gates.status` | `failed` |
| `failure_reason_count` / `evidence_error_count` | `1` / `0` |
| `scratch_cleanup_status` / `status_scope` | `not_created` / `scope_unverified` |
| runtime / original image summary | `unverified` / `null` |
| Build | `passed`，Cargo exit `0`，`1,180 ms`；stdout `0` bytes、stderr `72` bytes（未读取正文） |

Failure summary 的 `strict_gates.gates` 是空对象。因此 13 个 strict gate 的单项状态全部**不可判定**；failure reason count 1 是泛化 fatal 计数，不能代替逐 gate 数据。也没有通过 safe summary 证明原图 wire、视觉门禁、finish 或 geometry gate 的结果。这里仅记录摘要实际发布的状态。

ACP/Cordis allowlist projection 记录到一次 `cad_add_hole_array` failed terminal；其余列出的 CAD terminal 为 completed。ACP→bridge outcome 均 matched；bridge→audit 中该工具有 3 次 matched、1 次 `native_audit_rejected`。该安全投影没有报告足以归因的工具错误正文；不阅读任何原始 trace、参数或几何数据，也不从工具事件推断 strict gate 是否通过。

| Safe 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `summary.json` | 681 | `0ab65539151912e019b12ab78fb49aeaddab4e7e06264c5e380211019564d67e` |
| `build-status.json` | 107 | `6f27e1a62b59c05b73c96840713506b6fcc36209051a9b475b146199afca9d0c` |
| `tool-result-diagnostics.jsonl` | 7,975 | `cbad5ac90699be290cd7f1881ba7aeb1b61387ca0c343f3ec0ab812c280670` |

safe-output：`C:\Users\lycohana\AppData\Local\Temp\f7-g8-run30-20261004-d9c38289d89ec897d91759669aae2dd33670a0a3\safe-output`。

## 结论与边界

Run30 实际 runner exit `1` 与 safe summary `failed` 一致。摘要报告 fatal 发生阶段为 `publication`、固定 code 为 `uncaught_exception`，与 late-fatal handler 路径相符；但安全字段无法区分原子替换分支与 publication 阶段首次发布 failure summary，因此不能证明原子替换分支实际执行。当前 failure summary 不保留此前 strict summary，故不能判定 fatal 之前初始 summary 是 passed 还是 failed，也不能据此声称 Run29 的历史 exit=1 原因已确认。异常正文与真正触发源未知。

本次运行不是 strict gate 验收通过：13 项逐 gate 状态不可用，summary scope unverified，transport failed，model none。安全 Cordis 投影不能替代门禁摘要，更不能证明独立视觉质量、几何正确性、CATIA 或桌面体验。没有读取 raw provider/DSH logs、scratch、工具参数、几何载荷、图片/预览；没有启动 GUI/CATIA；未重跑。
