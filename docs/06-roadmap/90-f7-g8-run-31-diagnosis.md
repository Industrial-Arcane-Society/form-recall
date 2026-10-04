# F7/G8 Run31：safe vision review projection 单次冷启动

日期：2026-10-04

## 运行条件

- fetch 后固定 main commit `93e2b1be893f158ad43300002e617266b814fb11`，tree `acc9b2f920b61931839f0591a63a13f585e7127d`；初始 worktree clean，原 Run29/Run30 分支保留，新文档分支为 `feat/f7-run31-coldstart`。
- 无 GUI、无 CATIA。仅运行一次 `native_agent_coldstart_acceptance.mjs`，仅传入四个必需位置参数，省略 image/userText 参数，使用默认原图与默认 prompt；没有 CADPlan、尺寸、孔数量或 userText seed。
- 无 provider preflight 只检查必需参数、runner/runtime/Node/DSH package descriptor、manifest、provider descriptor、默认原图路径存在性和 safe-output leaf 不存在；未读取这些描述文件的内容、API key、图片内容或 checksum。
- `AIMODELER_ACCEPTANCE_DEADLINE_SECONDS=1800`，指定已存在的 clean-target OCCT manifest。stdout/stderr 丢弃；没有重试。
- runner exit code：`1`。

## Safe allowlist 结果

只读取 `summary.json`、`build-status.json`、`tool-result-diagnostics.jsonl`。

| 字段 | 结果 |
|---|---|
| Safe summary status | `failed` |
| `status_scope` / transport / model | `scope_unverified` / `failed` / `none` |
| `failure_diagnostic` | `stage=publication`, `code=uncaught_exception` |
| strict gate status / failure count / evidence errors | `failed` / `1` / `0` |
| strict gate map | 空；13 项逐 gate 状态不可判定 |
| Build | `passed`，Cargo exit `0`，`30,915 ms`；stdout `0` bytes、stderr `196` bytes（未读正文） |

`tool-result-diagnostics.jsonl` 包含 `vision_review_projection`，schema=`vision_review_diagnostics_v1`，`review_count=2`。两条 review 的固定安全摘要字段相同：required `4`、mapped `4`、unmapped/missing/unsupported/out_of_scope/mismatch/unknown 均为 `0`；`view_comparable=true`、`coverage_class=estimated`、`stage_eligible=true`。这只确认 projection 可观测，不证明图像理解正确、视觉 coverage gate 通过或独立视觉验收通过。failure summary 未保留 gate map，不能将 review projection 等同于 `visual` gate。

| Safe 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `summary.json` | 681 | `0ab65539151912e019b12ab78fb49aeaddab4e7e06264c5e380211019564d67e` |
| `build-status.json` | 109 | `1fe74f3b5e3437c7779890a564fc92df24bbc5dbff3d7b56d5535a464d6b3583` |
| `tool-result-diagnostics.jsonl` | 8,676 | `8244ec6f6c68135947005b3a2708d9acacD4446edccf24ff0377f662fa360ace` |

safe-output：`C:\Users\lycohana\AppData\Local\Temp\f7-g8-run31-20261004-93e2b1be893f158ad43300002e617266b814fb11\safe-output`。

## 结论与边界

Run31 确认新版本 safe allowlist diagnostics 确实包含 `vision_review_projection`，并发布两条固定计数/枚举摘要。但 runner 以 exit `1` 结束，safe summary 是 publication-stage `uncaught_exception` failure、scope unverified、transport failed、model none，严格门禁没有逐项结果。不能据此称 `visual` gate 通过、不能评估主图 wire 与模型行为，也不能标记 G1 或 F7 完成。

投影字段不含图像、预览、review 正文或几何载荷；没有读取 raw provider/DSH logs、thought、scratch 或参数；没有启动 GUI/CATIA，也没有重跑。本轮不提供独立视觉或 CATIA 验收证据。
