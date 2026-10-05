# F7/G8 Run34：native probe 前退出

## 运行边界

- 基线：固定 `main` `148f1d3c35c31a87c5ef3f5eda4593e256e2bf8c`，使用独立隔离 worktree 和全新输出目录。
- 输入：省略可选图片与文字参数，使用 runner 默认参考图和默认 prompt；图像 `72,309` bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。未传 CADPlan、尺寸或孔数 seed。
- 环境预检：当前配置选择 `api.siliconflow.cn` / `moonshotai/Kimi-K2.7-Code`，联网开关开启且凭据已配置；凭据值未读取或输出。Pinned Node `v22.23.3`，DSH `0.2.0-rc.2`。
- Runner：调用 `native_agent_coldstart_acceptance.mjs` 的四个必需位置参数；stdout/stderr 丢弃；hard total 与 idle timeout 均为 1,800 秒；只执行一次，没有重试。

## 结果

- Runner 在约 1 分钟后以 exit code `1` 结束。
- 唯一安全摘要 `summary.json` 为 2,254 bytes，SHA-256 `486efc42495f811a313a13fcd6d8f63f76d1e981125542f9189e006c0cca2e01`。它只记录 `failure_diagnostic.stage=native_probe`、`phase=not_started`、`code=uncaught_exception`、runtime `unverified`，`prompt_progress=null`。
- 失败发生在 DSH/session/prompt 开始之前；没有发出原图 provider 请求，因此本次没有模型回复、CAD 工具结果、几何产物或视觉复核。
- Safe projection 的 scope 为 `scope_unverified`，13 个 strict gates 全为 `unknown`；failure reason count 为 1，evidence error count 为 0。逐 gate 原因不可用，不把这些 unknown 说成 gate 失败或通过。
- `build-status.json` 与 `tool-result-diagnostics.jsonl` 均缺失；scratch cleanup 状态为 `removed`。允许的安全输出目录 tag：`f7-g8-run34-20261005-596f244d00464794833a01fefca39521`；projection 没有发布内部 run ID。

## 结论与后续

Run34 只说明这版 runner 在 native probe 阶段异常退出，不能评价 DSH 是否启动，也不能评价模型的图像分析、建模或复核能力。下次冷启动前需修复 native probe 的可定位诊断或通过非敏感的启动自检锁定失败点，再使用新输出目录进行单次复测；不能因为 Run34 未到 provider 阶段而重复归因到模型质量。F7/G1 与 F7/G8 仍未通过。

