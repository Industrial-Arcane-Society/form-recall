# F7 G8 Run20 冷启动诊断

日期：2026-10-04

## 运行边界

- 从集成快照 `2f4c57b5dcc607c189edaa76a828c40e9571e39d` 建立指定的独立 detached worktree `C:\Users\lycohana\.codex\worktrees\f7-run20\AI-_-`；运行前确认 clean，运行期间 HEAD 固定，运行结束时仍 clean。后续文档提交位于 `feat/f7-run20-coldstart-evidence`，不修改源代码，不 cherry-pick 或 push。
- 使用 runner 默认 prompt 与项目默认原图；未传额外用户文字、CADPlan、尺寸、孔数或特征答案。运行前与运行后原图均为 `72,309 bytes`、SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。摘要中的 `original_image` 为 `null`，故只能确认源文件前后未变，不能确认本次运行的原图绑定。
- 使用 pinned Node `v22.23.3` / DSH `0.2.0-rc.2`。Run20 worktree 未带 ignored `target/f1-runtime-probe` 与 OCCT manifest，因此复用项目另一 checkout 中已安装的相同固定版本 runtime 与 OCCT manifest；本轮 DSH home 由 runner 的私有 scratch 隔离创建。公开 keyless descriptor 为 `https://api.deepseek.com/v1`、`deepseek-flash`、`low`。只核验 `DEEPSEEK_API_KEY` 环境变量存在（`AIMODELER_LLM_API_KEY` 不存在），没有读取或输出密钥值。
- 冷启动命令使用 `$node` 指向上述 pinned Node，参数依次为 `native_agent_coldstart_acceptance.mjs`、Run20 worktree、pinned runtime、全新输出目录、keyless public descriptor、项目默认原图；未传最后一个可选 `userText` 参数。`AIMODELER_ACCEPTANCE_DEADLINE_SECONDS=1800`，使用 runner 完整默认 deadline。只启动一次，没有重试、GUI 或 CATIA。

实际命令的非秘密参数顺序：

```powershell
$env:AIMODELER_ACCEPTANCE_DEADLINE_SECONDS = '1800'
$runTemp = Join-Path $env:TEMP 'r20-20261004-0769796775504c13b8408d7075ff7abf'
$safeOutput = Join-Path $runTemp 'safe-output'
$providerDescriptor = Join-Path $runTemp 'provider-public.json'
& 'C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\f1-runtime-probe\node\node.exe' `
  'C:\Users\lycohana\.codex\worktrees\f7-run20\AI-_-\src\apps\desktop\src-tauri\examples\native_agent_coldstart_acceptance.mjs' `
  'C:\Users\lycohana\.codex\worktrees\f7-run20\AI-_-' `
  'C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\f1-runtime-probe' `
  $safeOutput $providerDescriptor `
  'C:\Users\lycohana\.codex\worktrees\f7-run20\AI-_-\temp\evidence-archive\artifacts\agent-quality-rebuild\references\user-nine-hole-recess-iso.png'
```

该参数顺序对应本次真实调用；临时 descriptor 只包含公开 provider 配置，没有写入密钥或额外用户 prompt。
- runner 开始时间 `2026-10-04T04:10:16.8876101Z`，结束时间 `2026-10-04T04:12:30.2009553Z`，耗时 `133.9 s`，exit code `1`。运行期间持续核验固定 HEAD；没有读取 stdout/stderr 正文、raw trace、完整 ACP 事件、thought/answer、工具参数或 payload。

## 可用的安全发布证据

输出目录为 `%TEMP%\r20-20261004-0769796775504c13b8408d7075ff7abf\safe-output\`。三个 allowlist artifacts 中只有 `summary.json` 存在，大小 `679 bytes`，SHA-256 `9548eb7345c45f18873a5a78f58ef5e4a5d503c38348e4a991645bf90ede9c70`；`tool-result-diagnostics.jsonl` 与 `build-status.json` 缺失，因此没有 ACP/Cordis projection、逐 gate 结果、gate reason codes 或 build status 可供报告，也没有可发布的 CAD 几何产物 SHA。

`summary.json` 发布的固定字段如下：

| 字段 | 值 |
|---|---|
| `status` / `strict_gates.status` | `failed` / `failed` |
| `status_scope` | `scope_unverified` |
| `transport_status` | `failed` |
| `model_status` | `none` |
| `strict_gates.failure_reason_count` | `1` |
| `strict_gates.evidence_error_count` | `0` |
| gate map | 空；未发布逐 gate 状态或原因码 |
| `failure_diagnostic.stage` | `offline_build` |
| `failure_diagnostic.code` | `uncaught_exception` |
| `runtime.node` / `runtime.dsh` | `unverified` / `unverified` |
| `original_image` | `null` |
| `scratch_cleanup_status` | `removed` |
| `scratch_recovery` | `complete`，所有计数 `0` |

`failure_diagnostic.stage` 是 runner 粗阶段，不是异常精确位置；`code` 只标记固定异常类别。它不能证明 Cargo 构建结果，也不能说明内部异常点。缺少 `build-status.json` 时构建状态未知；缺少 DSH/transport 成功证据时，不能确认 provider 请求是否发出。Run20 没有可据以判断的逐 gate 失败项或修复效果，具体根因保持 unknown。

## 结论与下一步

Run20 以非零状态结束，且仅发布了泛化安全失败摘要。虽然安全 stage 指向粗略的 `offline_build` 阶段，但这不能替代 build status 或错误原因。当前证据不构成完整真实 DSH 多模态运行，不证明 schema/合同修复或模型行为，也不能判定任一具体 gate 的结果。不能据此宣称 CADPlan、视觉质量或 CATIA 通过；F7 保持失败。

下一步在静态审查 Run20 runner 的 offline-build fatal path 和安全发布边界，确认缺失 build status 的契约与可改进的安全诊断；不要读取本次 stderr 或其它运行文件。若后续需要 provider 验收，应在得到单独授权后使用新 detached worktree、新输出目录再运行；不得重用 Run20 输出目录。Run20 scratch cleanup 已标记 `removed`，不应将安全摘要解释为 scratch 或产物验收。
