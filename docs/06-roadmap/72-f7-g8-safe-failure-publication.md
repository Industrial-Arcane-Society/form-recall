# F7 G8 fatal 失败安全发布验收

日期：2026-10-04

## 问题与代码路径

Run19 安全摘要只有 `summary.json`，其根因仍不可从历史白名单 artifact 确定。对 runner 代码路径的静态检查显示：`uncaughtException` 与 `unhandledRejection` handler 均不读取事件参数，而是进入 `failSafely()`；该流程等待受管子进程停止、清理 scratch、调用 `archiveFailure()` 写泛化失败摘要，然后 `process.exit(1)`。正常的 strict gate evaluation、ACP/Cordis projection 与 `build-status.json` 发布位于后续主路径，因此未捕获错误会在它们之前结束进程。旧 `archiveFailure()` 只写一个固定字段 summary，既没有诊断事件类型，也没有 runner 阶段。

源码只能说明这条控制流能产生“仅 summary”的 artifact 形态。Run19 的旧 summary 缺少 runtime、原图和 gate map，只表明这些字段在 fatal 路径没有被发布；不能据此确定异常发生的内部位置、是否执行了 provider 请求、Cargo 结果或 provider/模型故障原因。Run19 具体根因继续标记 unknown。

## 修复

- runner 维护 `failureStage`，只在固定阶段之间切换：`startup`、`config_validation`、`input_validation`、`runtime_validation`、`offline_build`、`native_probe`、`dsh_launch`、`dsh_session`、`strict_gate_evaluation`、`publication`。未知输入折叠为 `unknown`。
- fatal handler 仅传入固定 `uncaught_exception` 或 `unhandled_rejection` code；异常对象、stack、message、provider 响应或 stderr 均不传给发布器。
- failure publisher 在白名单 `summary.json` 内增加 `failure_diagnostic: { stage, code }`。只有摘要仍然只含固定状态、清理计数和 stage/code；fatal 发布不伪造不存在的 gate projection 或 build status。
- stage 是 runner 粗阶段，并不声称是异常精确位置；`code` 仅区别 Node 的 uncaught exception 与 unhandled rejection。这些标签用于缩小后续诊断范围，不用于推断根因。

## 合成验收

新增 Node synthetic tests 验证 fatal failure 发布。本次命令 `node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs` 共 **91/91 通过**；runner/publication `node --check` 与 `git diff --check` 均通过：

- 输出文件严格为 `summary.json`，状态为 failed，并且 stage/code 在 artifact 中可用。
- provider error、key、模型回答、prompt、工具参数、payload、stack 和额外传入的 exception 对象都不进入文件。
- 非白名单 stage/code 被压缩为 `unknown` / `uncaught_exception`；不可信 scratch 字段经既有 sanitizer 丢弃。
- failure 发布仍以 `wx` 创建，既有文件不会被覆盖。

仅运行本地合成测试；未启动真实 DSH/provider、GUI 或 CATIA，也没有重跑 Run19。F7/G8 与真实视觉/几何验收状态不变。
