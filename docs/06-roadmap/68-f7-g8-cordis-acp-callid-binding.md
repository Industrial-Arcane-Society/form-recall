# F7/G8 Cordis 与 ACP 调用身份绑定修复

## 范围与动机

本变更在 Cordis `defineTool.execute(args, exec)` 入口读取 `exec.callId`，把它作为临时相关性字段 `acpCallId` 送入本地 bridge；bridge 记录为 `acp_call_id`，供 coldstart strict gate 和 binding projection 做精确配对。Rust bridge 的 `toolCallId` 仍由 `randomUUID()` 独立生成，继续承担原有 native 取消、幂等与 Rust audit 关联职责。

此前 ACP `toolCallId` 与 native UUID 之间没有共同标识；同名同参并发只能尝试按短时间窗配对，无法排除交叠或同毫秒歧义。现在必须同时满足 ACP ID 唯一且精确相等、工具名/参数摘要相等、run/session/turn scope 相等、ACP 单次 start 与单一 terminal、成功 bridge 执行，以及 native UUID 对应成功 Rust audit。缺 ID、重复 ID、错配、跨 scope、错工具、参数摘要错配、ACP 额外事件或终态缺失均不能通过。

## 字段边界

`acpCallId` / `acp_call_id` 只用于当前验收过程的临时配对，不写入 safe acceptance projection 或 console。Safe projection 仍只输出 allowlist 内的阶段、工具名枚举、结果枚举和计数；投影测试把 ACP ID 和 native UUID 都作为 sentinel 检查不泄漏。ACP 更新原始事件和 bridge 记录属于隔离临时验收输入，不属于已发布安全摘要。

## 已核对的依赖事实与限制

仓库 runtime lockfile 固定 `@deepseek-ai/dsh-tools@0.2.0-rc.2`，锁定 tarball SHA-512 为 `vquUz8PjHWE4zaS3wH70IpYjL441p2UlUcG8TN2yJZN1RhDkuiHum4bWCsjEPyWLS/aB1QJvzdXC2EK/PRkbsQ==`。已只读检查该精确 npm tarball 的 `package/lib/types/index.d.ts`：`ToolExecutionInput.callId` 是必需 readonly 字段，`ToolRunContext extends ToolExecution`，故 Cordis `exec.callId` 是该 pin 的稳定调用身份。此次不安装该包或启动 DSH。

## 定向验收

允许的 Node 验收命令仅为：

```powershell
node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs
node --test src/legacy/tests/test_dsh_native_tools_rpc.mjs
```

固定 Node 版本 `v22.23.3`。本 feature 验收不执行 Cargo、DSH/provider、GUI 或 CATIA。生产路径 RPC fixture 调用真实 native Cordis `execute` handler，连接 fake bridge 并断言 ACP call ID 被原样传递；strict gate fixture 覆盖同名同参、重叠/同毫秒场景下以明确 ID 配对，以及缺失/错配 ID 拒绝。Safe projection sentinel 检查不得出现相关性字段值。

## 验收结论边界

Node fixture 证明代码路径和 fail-closed gate 逻辑，不证明 pinned DSH runtime 一定暴露 `exec.callId`，也不证明 provider、GUI、几何或 CATIA 行为。该字段是否由实际 pinned DSH 实例稳定提供，仍需在允许的新版本隔离验收中确认；不可使用 Run15/16 旧二进制产物代替。
