# F7/G8 Run25：诊断修复版 runner 未产出门禁结果

日期：2026-10-04

## 运行条件

- 固定代码 commit：`5168dbc65bb8df63e4c3edd027154bb6a30e2da9`。使用新 detached worktree `C:\Users\lycohana\.codex\worktrees\f7-run25-coldstart`，运行前 HEAD 与指定 SHA 一致、工作树干净。
- 使用项目默认原图和 runner 默认提示词：`请根据参考图片复刻模型，未标注尺寸按图像比例自行估算并记录来源，使用工具生成模型并复核。`。命令仅传 runner 的必需参数，没有传 CADPlan、尺寸、孔数或额外 `userText`。
- 默认原图路径：`temp/evidence-archive/artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`；运行前 SHA-256 为 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，与 Run24 相同。只校验了路径及 SHA，没有查看图像内容。
- 设置 acceptance deadline 为 1800 秒，并使用现有 runtime probe、manifest 与 Run24 的公开 provider descriptor；`DEEPSEEK_API_KEY` 只检查了是否存在，未读取或打印值。Runner stdout/stderr 均重定向到 `$null`。
- 只启动一次，没有重试。开始时间 `2026-10-04T07:52:57.9316747Z`，结束时间 `2026-10-04T07:52:58.1718046Z`，耗时 `0.240` 秒，runner exit code `1`。

## 冷启动调用

```powershell
$env:AIMODELER_ACCEPTANCE_DEADLINE_SECONDS = '1800'
$env:AIMODELER_OCCT_MANIFEST = 'C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\package-build-input-20261004\runtime-manifest.json'
& 'C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\f1-runtime-probe\node\node.exe' `
  'C:\Users\lycohana\.codex\worktrees\f7-run25-coldstart\src\apps\desktop\src-tauri\examples\native_agent_coldstart_acceptance.mjs' `
  'C:\Users\lycohana\.codex\worktrees\f7-run25-coldstart' `
  'C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\f1-runtime-probe' `
  'C:\Users\lycohana\AppData\Local\Temp\f7-g8-run25-20261004-155257-6dfd97920034\safe-output' `
  'C:\Users\lycohana\AppData\Local\Temp\f7-g8-run24-20261004-150844-438225d3cee540eea60314bf5ce860cc\provider-public.json' `
  'C:\Users\lycohana\.codex\worktrees\f7-run25-coldstart\temp\evidence-archive\artifacts\agent-quality-rebuild\references\user-nine-hole-recess-iso.png' *> $null
```

## 门禁结果与 allowlist 产物

Runner 在 `0.240` 秒后返回 exit code `1`。新输出目录为：

`C:\Users\lycohana\AppData\Local\Temp\f7-g8-run25-20261004-155257-6dfd97920034\safe-output`

该目录内三个唯一允许读取的诊断文件均不存在：

| Allowlist 文件 | 结果 | SHA-256 |
|---|---|---|
| `summary.json` | 不存在 | 无 |
| `build-status.json` | 不存在 | 无 |
| `tool-result-diagnostics.jsonl` | 不存在 | 无 |

因此本轮**没有生成整体 strict acceptance gate 结果**，也没有可报告的 pass/fail gate 或 allowlisted failure reason code。`runner_exit_code=1` 是唯一观察到的执行结果，不等同于某个 strict gate 的失败码。尤其无法确认是否出现 `native_audit_rejected`、`bridge_execution_rejected` 或任一 native audit 缺失结果；不能由短耗时推断 DSH 是否启动或具体失败阶段。

## 证据边界与结论

本轮未能验证 native audit 拒绝诊断修复，因为允许输出文件没有生成。没有读取 raw provider/DSH logs、scratch、工具参数、模型输出或几何数据；stdout/stderr 在执行时已丢弃。没有重试，遵守本轮一次运行限制。任何真实门禁状态、拒绝类别、具体参数或几何原因均未知。

## 下一步

需要先由主 Agent 根据可用的安全启动路径解决 runner 快速 exit 1 的阻塞，再决定是否另开一次独立冷启动；本报告不归因这次退出，也不把 Run25 标记为真实模型质量或 native audit 诊断验收通过/失败。
